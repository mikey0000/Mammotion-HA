"""Which path a manual movement takes: BLE, the legacy cloud command, the remote-drive session, or none.

One coordinator method, ``movement_path``, decides it for the nudge buttons' availability,
their presses and the camera ``move_*`` services:

* the "Send movement commands over Wi-Fi" option sends the legacy cloud command, as before;
* otherwise a usable BLE link wins;
* otherwise a mower that supports cloud remote drive (``DeviceHandle.supports_wifi_movement``:
  firmware 1.30.31.19 / 2.3.31.69, or function code 002.002 for a model outside those
  tables) moves through an ACTIVE remote-drive session, which cannot reverse;
* otherwise nothing moves it.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, create_autospec

import pytest
from homeassistant.exceptions import HomeAssistantError
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice
from pymammotion.data.model.function_codes import FunctionCodes
from pymammotion.device.handle import DeviceHandle
from pymammotion.device.remote_drive import RemoteDrivePhase, RemoteDriveSession
from pymammotion.messaging.command_queue import Priority
from pymammotion.transport.base import NoTransportAvailableError, TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from remote_drive_support import (
    LUBA3,
    RemoteDriveRig,
    make_active_rig,
    make_mower_data,
    make_runtime_data,
)

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.button import BUTTON_SENSORS
from custom_components.mammotion.camera import async_setup_platform_services
from custom_components.mammotion.const import CONF_MOVEMENT_USE_WIFI, DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionReportUpdateCoordinator,
    MovementPath,
    remote_drive_wire_speeds,
)

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"

_NEW = "2.3.31.69"
_OLD = "2.3.30.39"  # what the user's Luba 3 reports today
_UNKNOWN = ""

#: Not in either release-note family: the server's function list decides.
_YUKA_HS = "Yuka-HS6ABCDE"
_REMOTE_DRIVE = "002.002"

_NUDGES = {
    "emergency_nudge_forward": ("move_forward", "linear"),
    "emergency_nudge_left": ("move_left", "angular"),
    "emergency_nudge_right": ("move_right", "angular"),
    "emergency_nudge_back": ("move_back", "linear"),
}
_AHEAD = [key for key in _NUDGES if key != "emergency_nudge_back"]
#: Bounds the wait for the burst's first frame in the cancellation test.
_DRIVE_STARTED_TIMEOUT_S = 1
#: The session's minimum gap between two drive frames.
_FRAME_INTERVAL_S = 0.15


def _session(
    phase: RemoteDrivePhase, *, fence_paused: bool = False
) -> tuple[RemoteDrivePhase, bool]:
    """Describe a session for a parameter list; ``_handle`` builds a fresh one per test."""
    return phase, fence_paused


def _spec_session(phase: RemoteDrivePhase, fence_paused: bool) -> Any:
    session = create_autospec(RemoteDriveSession, instance=True)
    session.phase = phase
    session.fence_paused = fence_paused
    return session


class _Ble:
    """A BLE transport as the connect-before-move step sees it."""

    def __init__(self, *, usable: bool) -> None:
        self.is_usable = usable
        self.is_connected = False
        self.connect = AsyncMock(side_effect=self._connected)

    async def _connected(self) -> None:
        self.is_connected = True


def _handle(
    *,
    ble_usable: bool,
    cloud_usable: bool = True,
    reported_offline: bool = False,
    session: tuple[RemoteDrivePhase, bool] | None = None,
) -> MagicMock:
    """Build a handle whose ``active_transport`` keeps the real offline-flag contract."""
    handle = create_autospec(DeviceHandle, instance=True)
    ble = _Ble(usable=ble_usable)
    handle.get_transport.side_effect = lambda t: ble if t is TransportType.BLE else None

    def active_transport(
        *, prefer_ble: bool | None = None, user_initiated: bool = False
    ) -> object:
        if ble_usable:
            return ble
        if cloud_usable and (user_initiated or not reported_offline):
            return object()
        raise NoTransportAvailableError("nothing usable")

    handle.active_transport.side_effect = active_transport
    handle.remote_drive = _spec_session(*session) if session is not None else None
    return handle


def _coordinator(
    *,
    firmware: str = _NEW,
    option: bool = False,
    handle: MagicMock | None = None,
    device_name: str = LUBA3,
    function_codes: FunctionCodes | None = None,
) -> MammotionReportUpdateCoordinator:
    """Build the real coordinator class without HA, so the movement methods are the shipped ones.

    The transport side of the handle is faked; ``supports_wifi_movement`` is a real
    ``DeviceHandle``'s over ``coordinator.data``, so the rule is pymammotion's.  The
    client is spec'd, so a send is observed where it leaves the coordinator.
    """
    coordinator = object.__new__(MammotionReportUpdateCoordinator)
    coordinator.device_name = device_name
    coordinator.device = _create_ble_only_device(device_name)
    data = MowingDevice()
    data.device_firmwares.device_version = firmware
    if function_codes is not None:
        data.function_codes = function_codes
    coordinator.data = data
    coordinator.config_entry = SimpleNamespace(
        options={CONF_MOVEMENT_USE_WIFI: True} if option else {}
    )
    if handle is None:
        handle = _handle(ble_usable=False)
    handle.supports_wifi_movement.side_effect = DeviceHandle(
        "dev-1", device_name, data
    ).supports_wifi_movement
    data.mower_state.ble_mac = "aa:bb:cc:dd:ee:ff"
    manager = create_autospec(MammotionClient, instance=True)
    manager.mower.return_value = handle
    manager.get_device_by_name.return_value = data
    coordinator.manager = manager
    coordinator.update_failures = 0
    coordinator._bluetooth_enabled = True
    return coordinator


def _ble(coordinator: MammotionReportUpdateCoordinator) -> _Ble:
    return coordinator.manager.mower.return_value.get_transport(TransportType.BLE)


def _sent(coordinator: MammotionReportUpdateCoordinator, command: str, **kwargs: Any):
    """Assert *command* left for the client as a user command, and nothing else did."""
    coordinator.manager.send_command_with_args.assert_awaited_once_with(
        LUBA3,
        command,
        skip_if_saga_active=False,
        priority=Priority.USER,
        **kwargs,
    )


def _listing_remote_drive(firmware: str) -> FunctionCodes:
    """Return a server function list fetched for *firmware* that includes remote driving."""
    return FunctionCodes(
        product_key="pk", product_version=firmware, codes=[_REMOTE_DRIVE]
    )


def _nudge(key: str):
    """Return the nudge button description for *key*."""
    return next(entity for entity in BUTTON_SENSORS if entity.key == key)


_ACTIVE = _session(RemoteDrivePhase.ACTIVE)


@pytest.mark.parametrize(
    ("option", "ble_usable", "firmware", "session", "expected"),
    [
        # The option is the legacy escape hatch and wins over everything, BLE included.
        (True, True, _NEW, _ACTIVE, MovementPath.LEGACY_CLOUD),
        (True, False, _OLD, None, MovementPath.LEGACY_CLOUD),
        (True, False, _UNKNOWN, None, MovementPath.LEGACY_CLOUD),
        (False, True, _OLD, None, MovementPath.BLE),
        (False, True, _NEW, _ACTIVE, MovementPath.BLE),
        (False, False, _NEW, _ACTIVE, MovementPath.REMOTE_DRIVE),
        (
            False,
            False,
            _NEW,
            _session(RemoteDrivePhase.ACTIVE, fence_paused=True),
            MovementPath.FENCE_PAUSED,
        ),
        (False, False, _NEW, None, MovementPath.NONE),  # never started
        (False, False, _NEW, _session(RemoteDrivePhase.IDLE), MovementPath.NONE),
        (
            False,
            False,
            _NEW,
            _session(RemoteDrivePhase.SAFETY_NOTICE),
            MovementPath.NONE,
        ),
        (False, False, _NEW, _session(RemoteDrivePhase.EXITING), MovementPath.NONE),
        # Below the threshold a session is not trusted to move the mower.
        (False, False, _OLD, _ACTIVE, MovementPath.NONE),
        (False, False, _UNKNOWN, _ACTIVE, MovementPath.NONE),  # never guess
    ],
)
def test_movement_path(
    option: bool,
    ble_usable: bool,
    firmware: str,
    session: Any,
    expected: MovementPath,
) -> None:
    """The truth table of the one decision every movement caller uses."""
    handle = _handle(ble_usable=ble_usable, session=session)
    coordinator = _coordinator(firmware=firmware, option=option, handle=handle)

    assert coordinator.movement_path() is expected


@pytest.mark.parametrize(
    ("use_wifi", "expected"),
    [(True, MovementPath.LEGACY_CLOUD), (False, MovementPath.BLE)],
)
def test_an_explicit_use_wifi_wins(use_wifi: bool, expected: MovementPath) -> None:
    """A service call's ``use_wifi`` overrides the rule, as it always has."""
    coordinator = _coordinator(handle=_handle(ble_usable=False, session=_ACTIVE))

    assert coordinator.movement_path(use_wifi) is expected


def test_movement_path_without_a_handle() -> None:
    """No handle means the mower is not registered with the client yet."""
    coordinator = _coordinator()
    coordinator.manager.mower.return_value = None

    assert coordinator.movement_path() is MovementPath.NONE


def test_movement_path_follows_firmware_that_arrives_later() -> None:
    """Read on every call, so a mower set up before its version was known picks it up."""
    coordinator = _coordinator(
        firmware=_UNKNOWN, handle=_handle(ble_usable=False, session=_ACTIVE)
    )
    assert coordinator.movement_path() is MovementPath.NONE

    coordinator.data.device_firmwares.device_version = _NEW

    assert coordinator.movement_path() is MovementPath.REMOTE_DRIVE


@pytest.mark.parametrize(
    ("function_codes", "expected"),
    [
        (_listing_remote_drive(_NEW), MovementPath.REMOTE_DRIVE),
        (
            FunctionCodes(product_key="pk", product_version=_NEW, codes=[]),
            MovementPath.NONE,
        ),
        (None, MovementPath.NONE),  # nothing fetched yet
        (_listing_remote_drive(_OLD), MovementPath.NONE),  # fetched before an OTA
    ],
    ids=["listed", "not_listed", "not_fetched", "stale_firmware"],
)
def test_a_model_outside_the_tables_asks_the_server(
    function_codes: FunctionCodes | None, expected: MovementPath
) -> None:
    """A Yuka HS has no release-note threshold, so function code 002.002 decides."""
    coordinator = _coordinator(
        device_name=_YUKA_HS,
        firmware=_NEW,
        function_codes=function_codes,
        handle=_handle(ble_usable=False, session=_ACTIVE),
    )

    assert coordinator.movement_path() is expected


@pytest.mark.parametrize(
    ("path_setup", "ahead", "back"),
    [
        ({"ble_usable": True}, True, True),
        ({"option": True}, True, True),
        ({"session": _ACTIVE}, True, False),  # the session cannot reverse
        (
            {"session": _session(RemoteDrivePhase.ACTIVE, fence_paused=True)},
            False,
            False,
        ),
        ({"session": _session(RemoteDrivePhase.SAFETY_NOTICE)}, False, False),
        ({}, False, False),
    ],
    ids=["ble", "option", "session", "fence_paused", "unconfirmed", "none"],
)
def test_nudge_availability(
    path_setup: dict[str, Any], ahead: bool, back: bool
) -> None:
    """Forward, left and right follow the path; back also needs a path that can reverse."""
    handle = _handle(
        ble_usable=path_setup.get("ble_usable", False),
        session=path_setup.get("session"),
    )
    coordinator = _coordinator(option=path_setup.get("option", False), handle=handle)

    for key in _AHEAD:
        assert _nudge(key).available_fn(coordinator) is ahead, key
    assert _nudge("emergency_nudge_back").available_fn(coordinator) is back


@pytest.mark.parametrize("key", list(_NUDGES))
async def test_a_ble_press_connects_and_prefers_ble(key: str) -> None:
    """A usable BLE link wins over an active session."""
    command, axis = _NUDGES[key]
    coordinator = _coordinator(handle=_handle(ble_usable=True, session=_ACTIVE))

    await _nudge(key).press_fn(coordinator)

    _ble(coordinator).connect.assert_awaited_once()
    _sent(coordinator, command, prefer_ble=True, **{axis: 0.4})
    coordinator.manager.remote_drive.assert_not_awaited()


@pytest.mark.parametrize("key", list(_NUDGES))
async def test_an_option_press_sends_the_legacy_cloud_command_unchanged(
    key: str,
) -> None:
    """The option is the escape hatch: the legacy command, as before."""
    command, axis = _NUDGES[key]
    coordinator = _coordinator(firmware=_OLD, option=True)

    await _nudge(key).press_fn(coordinator)

    _ble(coordinator).connect.assert_not_awaited()
    _sent(coordinator, command, prefer_ble=False, **{axis: 0.4})


async def test_an_explicit_use_wifi_false_still_goes_to_ble() -> None:
    """A caller's explicit choice overrides the rule."""
    coordinator = _coordinator(handle=_handle(ble_usable=False, session=_ACTIVE))

    await coordinator.async_move_forward(0.5, use_wifi=False)

    _ble(coordinator).connect.assert_awaited_once()
    _sent(coordinator, "move_forward", prefer_ble=True, linear=0.5)
    coordinator.manager.remote_drive.assert_not_awaited()


@pytest.mark.parametrize(
    ("command", "speed", "expected"),
    [
        # What the legacy move_* commands send for the same speeds.
        ("move_forward", 0.4, (250, 0)),
        ("move_left", 0.4, (0, -112)),
        ("move_right", 0.4, (0, 112)),
        ("move_forward", 1.0, (850, 0)),
        ("move_right", 1.0, (0, 382)),
        ("move_forward", 0.1, (0, 0)),  # inside the joystick's dead zone
        ("move_back", 0.4, None),  # the session cannot reverse
    ],
)
def test_remote_drive_wire_speeds(
    command: str, speed: float, expected: tuple[int, int] | None
) -> None:
    """The session gets the same wire ints the legacy commands send for a speed."""
    assert remote_drive_wire_speeds(command, speed) == expected


async def _ack_first_drive_frame(rig: RemoteDriveRig) -> None:
    """Ack the burst's first frame so the session sends the queued release on the wire."""
    await rig.ack(0)
    await rig.clock.advance(_FRAME_INTERVAL_S)


@pytest.mark.parametrize(
    ("key", "wire"),
    [
        ("emergency_nudge_forward", (250, 0)),
        ("emergency_nudge_left", (0, -112)),
        ("emergency_nudge_right", (0, 112)),
    ],
)
async def test_a_session_press_is_a_burst_then_hands_off(
    key: str, wire: tuple[int, int]
) -> None:
    """Drive, then (0, 0): the release that also starts the session's idle countdown."""
    rig = await make_active_rig()

    await _nudge(key).press_fn(rig.coordinator)
    await _ack_first_drive_frame(rig)

    assert rig.coordinator.manager.remote_drive.await_args_list == [
        call(LUBA3, *wire),
        call(LUBA3, 0, 0),
    ]
    assert rig.sent.frames()[-2:] == [("drive", *wire), ("drive", 0, 0)]
    rig.coordinator.manager.send_command_with_args.assert_not_awaited()


async def test_a_cancelled_burst_still_hands_off() -> None:
    """The mower must not keep the last speed because the press was cancelled mid-burst."""
    rig = await make_active_rig()
    rig.coordinator.remote_drive_nudge_s = 60
    driving = asyncio.Event()
    forward = rig.coordinator.manager.remote_drive.side_effect

    async def _drive(*args: Any, **kwargs: Any) -> None:
        await forward(*args, **kwargs)
        driving.set()

    rig.coordinator.manager.remote_drive.side_effect = _drive
    press = asyncio.ensure_future(rig.coordinator.async_move_forward(0.4))
    await asyncio.wait_for(driving.wait(), _DRIVE_STARTED_TIMEOUT_S)
    press.cancel()
    with pytest.raises(asyncio.CancelledError):
        await press

    assert rig.coordinator.manager.remote_drive.await_args_list[-1] == call(LUBA3, 0, 0)


@pytest.mark.parametrize(
    ("setup", "key"),
    [
        ({"session": _ACTIVE}, "remote_drive_no_reverse"),
        (
            {"session": _session(RemoteDrivePhase.ACTIVE, fence_paused=True)},
            "remote_drive_fence_paused",
        ),
        ({}, "movement_unavailable"),
    ],
    ids=["reverse_in_session", "fence_paused", "no_path"],
)
async def test_a_move_that_cannot_happen_says_why(
    setup: dict[str, Any], key: str
) -> None:
    """Never a silent no-op, and never a zero-speed frame standing in for reverse."""
    handle = _handle(ble_usable=False, session=setup.get("session"))
    coordinator = _coordinator(handle=handle)

    with pytest.raises(HomeAssistantError) as raised:
        await coordinator.async_move_back(0.4)

    assert raised.value.translation_key == key
    coordinator.manager.remote_drive.assert_not_awaited()
    coordinator.manager.send_command_with_args.assert_not_awaited()


async def _call_move_service(hass, coordinator, service: str, data: dict) -> None:
    """Register the camera platform's services for one mower and call *service* on its camera."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = make_runtime_data(make_mower_data(coordinator))
    await async_setup_platform_services(hass, entry)
    # The services find the mower through the camera state's model_name.
    hass.states.async_set(
        "camera.mower", "idle", {"model_name": coordinator.device_name}
    )

    await hass.services.async_call(
        "mammotion", service, {"entity_id": "camera.mower", **data}, blocking=True
    )


async def test_camera_move_service_drives_the_session(hass) -> None:
    """No ``use_wifi`` in the call: an active session moves the mower."""
    rig = await make_active_rig()

    await _call_move_service(hass, rig.coordinator, "move_right", {"speed": 0.4})
    await _ack_first_drive_frame(rig)

    assert rig.sent.frames()[-2:] == [("drive", 0, 112), ("drive", 0, 0)]


@pytest.mark.parametrize(
    ("service", "session", "key"),
    [
        ("move_backward", _ACTIVE, "remote_drive_no_reverse"),
        ("move_forward", None, "movement_unavailable"),
    ],
)
async def test_camera_move_service_that_cannot_move_raises(
    hass, service: str, session: Any, key: str
) -> None:
    """Reverse in a session, or no path at all, is a translated error."""
    coordinator = _coordinator(handle=_handle(ble_usable=False, session=session))

    with pytest.raises(HomeAssistantError) as raised:
        await _call_move_service(hass, coordinator, service, {"speed": 0.5})

    assert raised.value.translation_key == key
    coordinator.manager.remote_drive.assert_not_awaited()
    coordinator.manager.send_command_with_args.assert_not_awaited()


async def test_camera_move_service_explicit_use_wifi_sends_the_legacy_command(
    hass,
) -> None:
    """``use_wifi`` in the call still selects the legacy command."""
    coordinator = _coordinator(handle=_handle(ble_usable=False, session=_ACTIVE))

    await _call_move_service(
        hass, coordinator, "move_forward", {"speed": 0.5, "use_wifi": True}
    )

    _sent(coordinator, "move_forward", prefer_ble=False, linear=0.5)


def _locale_files() -> list[Path]:
    """Return strings.json followed by every translation file."""
    return [_ROOT / "strings.json", *sorted((_ROOT / "translations").glob("*.json"))]


def _option_description(path: Path) -> str:
    return json.loads(path.read_text())["options"]["step"]["init"]["data_description"][
        CONF_MOVEMENT_USE_WIFI
    ]


def test_every_locale_explains_the_option_against_remote_drive() -> None:
    """Both thresholds, and the remote-drive switch it is the alternative to, by its own name."""
    for path in _locale_files():
        text = _option_description(path)
        switch_name = json.loads(path.read_text())["entity"]["switch"]["remote_drive"][
            "name"
        ]
        assert "1.30.31.19" in text, path.name
        assert "2.3.31.69" in text, path.name
        assert switch_name in text, path.name


def test_no_locale_copied_the_english_option_description() -> None:
    """A locale that copied the English text was never translated."""
    english = _option_description(_ROOT / "strings.json")

    copied = [
        path.stem
        for path in sorted((_ROOT / "translations").glob("*.json"))
        if path.stem != "en" and _option_description(path) == english
    ]

    assert copied == []
