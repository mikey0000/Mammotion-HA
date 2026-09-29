"""Remote-drive events reach the user: a notification, a bus event and the sensor's attributes.

The coordinator relays every session event to the mower's notifier, which publishes it
the way it publishes the device's own events.  The notifier and coordinator are the
shipped ones; the events are delivered through the handler the coordinator subscribed
to the library's session with.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from pymammotion.device.remote_drive import RemoteDriveEvent, RemoteDriveEventKind
from pytest_homeassistant_custom_component.common import async_capture_events
from remote_drive_support import LUBA3, RemoteDriveRig, make_remote_drive_rig

from custom_components.mammotion.const import DOMAIN, EVENT_REMOTE_DRIVE
from custom_components.mammotion.notifications import (
    QUIET_REMOTE_DRIVE_KINDS,
    MowerNotifier,
)
from custom_components.mammotion.sensor import (
    REMOTE_DRIVE_SENSOR,
    MammotionRemoteDriveSensorEntity,
)

_EN = json.loads(
    (
        Path(__file__).parent.parent
        / "custom_components"
        / "mammotion"
        / "translations"
        / "en.json"
    ).read_text()
)
_FAULT_ID = f"{DOMAIN}_{LUBA3}_remote_drive"
_NOTICE_ID = f"{DOMAIN}_{LUBA3}_remote_drive_safety"


def _exception_text(key: str, **placeholders: str) -> str:
    return _EN["exceptions"][key]["message"].format(**placeholders)


@pytest.fixture
def notifications() -> Any:
    """Intercept the persistent_notification helpers the notifier calls."""
    with (
        patch.object(persistent_notification, "async_create", autospec=True) as create,
        patch.object(
            persistent_notification, "async_dismiss", autospec=True
        ) as dismiss,
    ):
        yield SimpleNamespace(create=create, dismiss=dismiss)


@pytest.fixture
async def started(
    hass: HomeAssistant, notifications: Any
) -> AsyncIterator[RemoteDriveRig]:
    """Start a session with the mower's notifier listening; stop both afterwards."""
    rig = await make_remote_drive_rig(hass=hass)
    stop = MowerNotifier(hass, rig.coordinator).async_start()
    await rig.coordinator.async_start_remote_drive()
    await hass.async_block_till_done()
    yield rig
    stop()
    await rig.coordinator.async_shutdown()


async def _deliver(hass: HomeAssistant, rig: RemoteDriveRig, event: Any) -> None:
    """Deliver *event* through the handler the coordinator subscribed with."""
    handler = rig.coordinator.manager.subscribe_remote_drive.call_args.args[1]
    await handler(event)
    await hass.async_block_till_done()


def _shown(notifications: Any, notification_id: str) -> list[Any]:
    return [
        call
        for call in notifications.create.call_args_list
        if call.kwargs["notification_id"] == notification_id
    ]


@pytest.mark.parametrize(
    "kind",
    [kind for kind in RemoteDriveEventKind if kind not in QUIET_REMOTE_DRIVE_KINDS],
    ids=lambda kind: kind.value,
)
async def test_a_fault_raises_a_translated_notification(
    hass: HomeAssistant,
    notifications: Any,
    started: RemoteDriveRig,
    kind: RemoteDriveEventKind,
) -> None:
    """Each fault that needs the user's attention says what happened, in HA's language."""
    await _deliver(hass, started, RemoteDriveEvent(kind, "42"))

    [shown] = _shown(notifications, _FAULT_ID)
    assert shown.args[1] == _exception_text(f"remote_drive_{kind.value}", detail="42")
    assert shown.kwargs["title"] == _exception_text(
        "notification_title_remote_drive", device_name=LUBA3
    )


async def test_a_send_failure_names_the_error(
    hass: HomeAssistant, notifications: Any, started: RemoteDriveRig
) -> None:
    """SEND_FAILED carries the exception rather than a detail."""
    await _deliver(
        hass,
        started,
        RemoteDriveEvent(
            RemoteDriveEventKind.SEND_FAILED, error=RuntimeError("rejected")
        ),
    )

    [shown] = _shown(notifications, _FAULT_ID)
    assert shown.args[1] == _exception_text(
        "remote_drive_send_failed", detail="rejected"
    )


@pytest.mark.parametrize(
    "kind",
    [RemoteDriveEventKind.IDLE_TIMEOUT, RemoteDriveEventKind.LATENCY_HIGH],
    ids=lambda kind: kind.value,
)
async def test_an_idle_exit_or_a_latency_warning_is_quiet(
    hass: HomeAssistant,
    notifications: Any,
    started: RemoteDriveRig,
    kind: RemoteDriveEventKind,
) -> None:
    """Letting go ends the session by design; neither needs a notification."""
    await _deliver(hass, started, RemoteDriveEvent(kind, "600"))

    assert _shown(notifications, _FAULT_ID) == []


@pytest.mark.parametrize("kind", list(RemoteDriveEventKind), ids=lambda k: k.value)
async def test_every_event_is_fired_on_the_bus_and_kept_on_the_sensor(
    hass: HomeAssistant, started: RemoteDriveRig, kind: RemoteDriveEventKind
) -> None:
    """Automations get every kind, quiet ones included."""
    events = async_capture_events(hass, EVENT_REMOTE_DRIVE)
    sensor = MammotionRemoteDriveSensorEntity(started.coordinator, REMOTE_DRIVE_SENSOR)

    await _deliver(hass, started, RemoteDriveEvent(kind, "7"))

    [event] = events
    assert event.data["device_name"] == LUBA3
    assert event.data["kind"] == kind.value
    assert event.data["detail"] == "7"
    assert sensor.extra_state_attributes == {
        "last_event": kind.value,
        "last_event_detail": "7",
    }


async def test_the_safety_notice_is_shown_until_confirmed(
    hass: HomeAssistant, notifications: Any, started: RemoteDriveRig
) -> None:
    """It stands in for the app's safety dialog: the area is clear, stay in sight."""
    [shown] = _shown(notifications, _NOTICE_ID)
    assert shown.args[1] == _exception_text("remote_drive_safety_notice")

    await started.coordinator.async_confirm_remote_drive()
    await hass.async_block_till_done()

    notifications.dismiss.assert_any_call(hass, _NOTICE_ID)
