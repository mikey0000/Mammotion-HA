"""Cloud remote drive: the app's token-leased driving session, run by the report coordinator.

pymammotion owns the session (``RemoteDriveSession``); the coordinator starts, confirms and
stops it for one mower, turns its refusals into translated errors, and relays its events
to the entities.  The session here is the library's own, on a real ``DeviceHandle``; only
the token endpoint, the cloud send and the clock are scripted.  The notifier and the
translations have their own modules.
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pymammotion.data.model.device import MowingDevice
from pymammotion.device.handle import DeviceHandle
from pymammotion.device.remote_drive import (
    RemoteDriveEventKind,
    RemoteDrivePhase,
    RemoteDriveSession,
)
from pymammotion.http.model.http import UnauthorizedExceptionError
from remote_drive_support import (
    LUBA3,
    OLD_FIRMWARE,
    SUPPORTING_FIRMWARE,
    RemoteDriveRig,
    add_entity_listener,
    make_active_rig,
    make_grant,
    make_mower_data,
    make_remote_drive_rig,
    make_runtime_data,
)
from user_command_support import make_cloud_handle, make_coordinator

from custom_components.mammotion import button as button_platform
from custom_components.mammotion import sensor as sensor_platform
from custom_components.mammotion import switch as switch_platform
from custom_components.mammotion.button import BUTTON_REMOTE_DRIVE
from custom_components.mammotion.const import CONF_HAS_CLOUD_ACCOUNT
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.sensor import (
    REMOTE_DRIVE_SENSOR,
    MammotionRemoteDriveSensorEntity,
)
from custom_components.mammotion.switch import (
    REMOTE_DRIVE_SWITCH,
    MammotionRemoteDriveSwitchEntity,
)


def _button(key: str) -> Any:
    return next(d for d in BUTTON_REMOTE_DRIVE if d.key == key)


async def _raised(coro: Any) -> HomeAssistantError:
    with pytest.raises(HomeAssistantError) as raised:
        await coro
    return raised.value


@pytest.fixture
async def hass_rig(hass: HomeAssistant) -> AsyncIterator[RemoteDriveRig]:
    """Build a rig whose coordinator is built by its own ``__init__``; shut it down after."""
    rig = await make_remote_drive_rig(hass=hass, firmware=OLD_FIRMWARE)
    yield rig
    await rig.coordinator.async_shutdown()


async def test_starting_requests_the_token_and_shows_the_safety_notice() -> None:
    """No video gate: HA never sees a frame, so there is nothing to report it with."""
    rig = await make_remote_drive_rig()

    await rig.coordinator.async_start_remote_drive()

    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.SAFETY_NOTICE
    rig.coordinator.manager.start_remote_drive.assert_awaited_once_with(LUBA3)
    assert rig.session.require_video is False


def _after(forward: Any, check: Any) -> Any:
    """Wrap the client's *forward* so *check* runs first."""

    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        check()
        return await forward(*args, **kwargs)

    return _wrapped


async def test_starting_subscribes_once_before_the_token_is_requested() -> None:
    """A refusal is emitted during start, so the subscription must already be there."""
    rig = await make_remote_drive_rig(tokens=[make_grant(), make_grant()])
    manager = rig.coordinator.manager
    manager.start_remote_drive.side_effect = _after(
        manager.start_remote_drive.side_effect,
        manager.subscribe_remote_drive.assert_called_once,
    )

    await rig.coordinator.async_start_remote_drive()
    await rig.coordinator.async_stop_remote_drive()
    await rig.coordinator.async_start_remote_drive()

    manager.subscribe_remote_drive.assert_called_once()


@pytest.mark.parametrize(
    ("grant", "key", "detail"),
    [
        (make_grant(deviceResult=2, token=None), "remote_drive_token_unavailable", "2"),
        (
            make_grant(deviceResult=9, token=None, preemptUser="neighbour"),
            "remote_drive_occupied_by_other",
            "neighbour",
        ),
    ],
    ids=["unavailable", "occupied"],
)
async def test_a_refused_token_is_a_translated_error_and_leaves_the_session_idle(
    grant: Any, key: str, detail: str
) -> None:
    """The refusal's event says why; the error the user sees says the same."""
    rig = await make_remote_drive_rig(tokens=[grant])

    error = await _raised(rig.coordinator.async_start_remote_drive())

    assert error.translation_key == key
    assert error.translation_placeholders == {"detail": detail}
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE
    assert rig.coordinator.remote_drive_last_event.detail == detail


async def test_starting_without_a_usable_cloud_transport_needs_the_cloud() -> None:
    """Over BLE the mower drives with the ordinary movement commands instead."""
    rig = await make_remote_drive_rig(cloud_usable=False)

    error = await _raised(rig.coordinator.async_start_remote_drive())

    assert error.translation_key == "remote_drive_needs_cloud"
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


async def test_starting_a_running_session_again_is_refused() -> None:
    """A second start while one is live is refused, and the live one carries on."""
    rig = await make_remote_drive_rig()
    await rig.coordinator.async_start_remote_drive()

    error = await _raised(rig.coordinator.async_start_remote_drive())

    assert error.translation_key == "remote_drive_already_running"
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.SAFETY_NOTICE


async def test_a_rejected_login_takes_the_login_refresh_path() -> None:
    """The login is refreshed as for any user command, and the press is reported as failed."""
    rig = await make_remote_drive_rig(tokens=[UnauthorizedExceptionError("401")])
    rig.coordinator.account = "user@example.com"
    rig.coordinator.config_entry = SimpleNamespace(
        options={}, data={CONF_HAS_CLOUD_ACCOUNT: True}
    )
    rig.coordinator.manager.to_cache.return_value = {}

    error = await _raised(rig.coordinator.async_start_remote_drive())

    assert error.translation_key == "command_failed"
    rig.coordinator.manager.refresh_login.assert_awaited_once_with("user@example.com")
    assert rig.coordinator.update_failures == 1
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


async def test_a_network_failure_on_the_token_request_is_reported() -> None:
    """A dropped connection is reported, not raised as an untranslated traceback."""
    rig = await make_remote_drive_rig(tokens=[ConnectionError("down")])

    error = await _raised(rig.coordinator.async_start_remote_drive())

    assert error.translation_key == "command_failed"
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


async def test_an_unexpected_failure_on_the_token_request_propagates() -> None:
    """Only transient network errors are translated; a bug must not be dressed up as one."""
    rig = await make_remote_drive_rig(tokens=[ValueError("malformed grant")])

    with pytest.raises(ValueError, match="malformed grant"):
        await rig.coordinator.async_start_remote_drive()

    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


async def test_confirming_the_safety_notice_makes_the_session_active() -> None:
    """The confirm button stands in for the app's safety dialog."""
    rig = await make_remote_drive_rig()
    await rig.coordinator.async_start_remote_drive()

    await rig.coordinator.async_confirm_remote_drive()

    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.ACTIVE


async def test_confirming_with_no_safety_notice_up_is_refused() -> None:
    """Confirming outside the safety notice is a translated error."""
    rig = await make_remote_drive_rig()

    error = await _raised(rig.coordinator.async_confirm_remote_drive())

    assert error.translation_key == "remote_drive_not_awaiting_confirmation"


async def test_stopping_halts_the_mower_releases_the_token_and_is_idempotent() -> None:
    """The first stop sends a zero-speed frame and the exit notify; a second sends nothing."""
    rig = await make_active_rig()
    before = len(rig.sent.payloads)

    await rig.coordinator.async_stop_remote_drive()
    after_first = len(rig.sent.payloads)
    await rig.coordinator.async_stop_remote_drive()

    assert rig.sent.frames()[before:] == [("drive", 0, 0), ("exit",)]
    assert len(rig.sent.payloads) == after_first
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


async def test_stopping_a_mower_that_never_drove_creates_no_session() -> None:
    """No session exists until the first start; stopping must not create one."""
    device = MowingDevice()
    handle = make_cloud_handle(LUBA3, device, reported_offline=False)
    await handle.stop_polling()
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, device, device_name=LUBA3, handle=handle
    )

    await coordinator.async_stop_remote_drive()

    coordinator.manager.stop_remote_drive.assert_not_awaited()
    assert coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


def test_an_unregistered_mower_reads_as_idle() -> None:
    """Before the client knows the mower there is no handle to ask."""
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator, MowingDevice(), device_name=LUBA3
    )
    coordinator.manager.mower.return_value = None

    assert coordinator.remote_drive_phase is RemoteDrivePhase.IDLE
    assert coordinator.remote_drive_fence_paused is False
    assert coordinator.supports_remote_drive() is False


async def test_shutting_the_coordinator_down_stops_a_live_session(
    hass: HomeAssistant,
) -> None:
    """Unloading the entry must not leave the mower holding a drive token."""
    rig = await make_active_rig(hass=hass)
    before = len(rig.sent.payloads)

    await rig.coordinator.async_shutdown()

    assert rig.session.phase is RemoteDrivePhase.IDLE
    assert rig.sent.frames()[before:] == [("drive", 0, 0), ("exit",)]


async def test_session_events_refresh_the_entities() -> None:
    """A session that ends by itself has to be reflected without a poll."""
    rig = await make_active_rig()
    updates: list[None] = []
    add_entity_listener(rig.coordinator, lambda: updates.append(None))

    await rig.clock.advance(10)  # the grant's idle timeout, with no input

    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE
    assert rig.coordinator.remote_drive_last_event.kind is (
        RemoteDriveEventKind.IDLE_TIMEOUT
    )
    assert updates


async def _fence_paused_rig() -> RemoteDriveRig:
    """Build an active session the mower has stopped at the fence."""
    rig = await make_active_rig()
    await rig.coordinator.async_remote_drive_nudge(250, 0)
    await rig.ack(0, fence_exceed_distance=6.0, localization_valid=True)
    assert rig.coordinator.remote_drive_last_event.kind is (
        RemoteDriveEventKind.APPROACH_FENCE
    )
    return rig


async def test_acknowledging_the_fence_warning_resumes_input() -> None:
    """The acknowledge button releases the pause the fence stop put on input."""
    rig = await _fence_paused_rig()
    assert rig.coordinator.remote_drive_fence_paused is True

    await _button("acknowledge_remote_drive_fence").press_fn(rig.coordinator)

    assert rig.coordinator.remote_drive_fence_paused is False
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.ACTIVE


async def _rig_in(state: str) -> RemoteDriveRig:
    match state:
        case "idle":
            return await make_remote_drive_rig()
        case "safety_notice":
            rig = await make_remote_drive_rig()
            await rig.coordinator.async_start_remote_drive()
            return rig
        case "active":
            return await make_active_rig()
        case "fence_paused":
            return await _fence_paused_rig()
    raise AssertionError(state)


@pytest.mark.parametrize(
    ("state", "switch_on", "confirm", "acknowledge"),
    [
        ("idle", False, False, False),
        ("safety_notice", True, True, False),
        ("active", True, False, False),
        ("fence_paused", True, False, True),
    ],
)
async def test_the_controls_follow_the_session(
    state: str, switch_on: bool, confirm: bool, acknowledge: bool
) -> None:
    """Confirm only on the safety notice, acknowledge only after a fence stop."""
    rig = await _rig_in(state)
    switch = MammotionRemoteDriveSwitchEntity(rig.coordinator, REMOTE_DRIVE_SWITCH)
    sensor = MammotionRemoteDriveSensorEntity(rig.coordinator, REMOTE_DRIVE_SENSOR)
    buttons = {
        d.key: button_platform.MammotionButtonSensorEntity(rig.coordinator, d)
        for d in BUTTON_REMOTE_DRIVE
    }

    assert switch.available is True
    assert switch.is_on is switch_on
    assert buttons["confirm_remote_drive"].available is confirm
    assert buttons["acknowledge_remote_drive_fence"].available is acknowledge
    assert sensor.native_value == rig.coordinator.remote_drive_phase.value


@pytest.mark.parametrize(
    ("phase", "on"),
    [
        (RemoteDrivePhase.IDLE, False),
        (RemoteDrivePhase.REQUESTING_TOKEN, True),
        (RemoteDrivePhase.SAFETY_NOTICE, True),
        (RemoteDrivePhase.ACTIVE, True),
        (RemoteDrivePhase.EXITING, False),  # the token is already being released
    ],
)
def test_the_switch_is_on_while_the_session_holds_the_token(
    phase: RemoteDrivePhase, on: bool
) -> None:
    """Every phase, including the two a real session only passes through."""
    session = create_autospec(RemoteDriveSession, instance=True)
    session.phase = phase
    handle = create_autospec(DeviceHandle, instance=True)
    handle.remote_drive = session
    coordinator = make_coordinator(
        MammotionReportUpdateCoordinator,
        MowingDevice(),
        device_name=LUBA3,
        handle=handle,
    )
    coordinator.unique_name = LUBA3

    switch = MammotionRemoteDriveSwitchEntity(coordinator, REMOTE_DRIVE_SWITCH)

    assert switch.is_on is on


async def test_the_switch_starts_and_stops_the_session() -> None:
    """On requests the token; off stops the mower and releases it."""
    rig = await make_remote_drive_rig()
    switch = MammotionRemoteDriveSwitchEntity(rig.coordinator, REMOTE_DRIVE_SWITCH)

    await switch.async_turn_on()
    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.SAFETY_NOTICE
    await switch.async_turn_off()

    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.IDLE


async def test_the_controls_ignore_the_clouds_offline_flag() -> None:
    """They are user actions: the advisory flag must not hide or block them."""
    rig = await make_remote_drive_rig()
    rig.handle.availability.mqtt_reported_offline = True
    switch = MammotionRemoteDriveSwitchEntity(rig.coordinator, REMOTE_DRIVE_SWITCH)

    assert switch.available is True
    await switch.async_turn_on()

    assert rig.coordinator.remote_drive_phase is RemoteDrivePhase.SAFETY_NOTICE


def test_the_sensor_lists_every_phase() -> None:
    """The ENUM options are the library's phases, so none renders as unknown."""
    assert REMOTE_DRIVE_SENSOR.options == [phase.value for phase in RemoteDrivePhase]


_REMOTE_DRIVE_KEYS = {
    "remote_drive",
    "remote_drive_state",
    "confirm_remote_drive",
    "acknowledge_remote_drive_fence",
}


class _Setup:
    """Run the button, switch and sensor platforms for one mower, recording what they add."""

    def __init__(self, rig: RemoteDriveRig) -> None:
        self.entry = rig.coordinator.config_entry
        self.entry.runtime_data = make_runtime_data(make_mower_data(rig.coordinator))
        self.added: list[str] = []

    def _add(self, entities: Any) -> None:
        self.added.extend(entity.entity_description.key for entity in entities)

    async def async_run(self, hass: HomeAssistant) -> None:
        for platform in (button_platform, switch_platform, sensor_platform):
            await platform.async_setup_entry(hass, self.entry, self._add)

    def remote_drive(self) -> list[str]:
        return sorted(key for key in self.added if key in _REMOTE_DRIVE_KEYS)


async def test_the_entities_appear_once_the_mower_supports_remote_drive(
    hass: HomeAssistant, hass_rig: RemoteDriveRig
) -> None:
    """The gate is re-read on every update, so firmware that arrives later still counts."""
    setup = _Setup(hass_rig)
    await setup.async_run(hass)
    assert setup.remote_drive() == []

    hass_rig.coordinator.data.device_firmwares.device_version = SUPPORTING_FIRMWARE
    hass_rig.coordinator.async_update_listeners()
    # A later update must not add them a second time.
    hass_rig.coordinator.async_update_listeners()

    assert setup.remote_drive() == sorted(_REMOTE_DRIVE_KEYS)
