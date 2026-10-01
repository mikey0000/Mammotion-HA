"""The error coordinator's view of the mower's error log (``rw_id=5``).

Two defects: the *Last error time* sensor showed 1970 for an entry the firmware had
not stamped with real time yet (#916), and the log was only re-read on a few
``sys_status`` transitions, so a fault logged at any other moment stayed hidden for
hours (#917).
"""

import datetime
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any
from unittest.mock import create_autospec

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pymammotion.client import MammotionClient
from pymammotion.data.model.device import MowingDevice, PoolCleanerDevice
from pymammotion.data.model.pool_state import SpinoErrorEntry
from pymammotion.device.handle import DeviceHandle
from pymammotion.state.device_state import DeviceNotification
from pymammotion.transport.base import TransportRateLimitedError
from pymammotion.utility.constant import WorkMode
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import (
    MammotionDeviceErrorUpdateCoordinator,
    MammotionSpinoCoordinator,
)
from custom_components.mammotion.sensor import SENSOR_ERROR_TYPES

_NAME = "Yuka-MN1000001"
_ERROR_TIME = next(d for d in SENSOR_ERROR_TYPES if d.key == "error_1_time")
#: 2025-09-08 17:04:11 UTC, the rewritten stamp from the #916 report.
_REAL_EPOCH = 1757351051
#: Seconds since boot the same entry carried eight seconds earlier.
_UPTIME_STAMP = 378429
_WARNING = DeviceNotification(
    _NAME, "device_warning_code_event", {"data": '[{"c":-2800,"ct":1}]'}
)


_Factory = Callable[..., Awaitable[tuple[MammotionDeviceErrorUpdateCoordinator, Any]]]


@pytest.fixture
async def make_coordinator(hass: HomeAssistant) -> AsyncIterator[_Factory]:
    """Build error coordinators and shut them down, debounce timers included."""
    built: list[MammotionDeviceErrorUpdateCoordinator] = []

    async def _make(
        *, usable: bool = True, enabled: bool = True
    ) -> tuple[MammotionDeviceErrorUpdateCoordinator, Any]:
        coordinator, handle = await _coordinator(hass, usable=usable, enabled=enabled)
        built.append(coordinator)
        return coordinator, handle

    yield _make
    for coordinator in built:
        await coordinator.async_shutdown()


async def _coordinator(
    hass: HomeAssistant, *, usable: bool, enabled: bool
) -> tuple[MammotionDeviceErrorUpdateCoordinator, Any]:
    """Return the real error coordinator, set up over a spec'd client and handle.

    The startup reads are marked done so only the triggers under test send.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-1",
        unique_id="aa:bb:cc:dd:ee:ff",
        data={CONF_BLE_DEVICES: {_NAME: "aa:bb:cc:dd:ee:ff"}},
    )
    entry.add_to_hass(hass)
    await async_get_store(hass, entry).async_load_device_data()
    device = MowingDevice()
    device.online = True
    device.enabled = enabled
    handle = create_autospec(DeviceHandle, instance=True)
    handle.has_usable_transport = usable
    manager = create_autospec(MammotionClient, instance=True)
    manager.get_device_by_name.return_value = device
    manager.mower.return_value = handle
    coordinator = MammotionDeviceErrorUpdateCoordinator(
        hass, entry, _create_ble_only_device(_NAME), manager, unique_name=_NAME
    )
    coordinator._startup_reads_done = True
    await coordinator._async_setup()
    return coordinator, handle


def _notification_handler(handle: Any) -> Any:
    return handle.subscribe_notification.call_args.args[0]


def _sys_status_handler(handle: Any) -> Any:
    return handle.watch_field.call_args.args[1]


def _error_log_reads(coordinator: MammotionDeviceErrorUpdateCoordinator) -> list[int]:
    """Return the ``context`` of every ``rw_id=5`` read handed to the client."""
    return [
        call.kwargs["context"]
        for call in coordinator.manager.send_command_and_wait.await_args_list
        if call.args[1] == "read_write_device" and call.kwargs.get("rw_id") == 5
    ]


async def _advance(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def _error_time_coordinator(
    times: list[int],
) -> MammotionDeviceErrorUpdateCoordinator:
    """Return a bare coordinator; ``get_error_time`` reads nothing but ``self.data``."""
    coordinator = MammotionDeviceErrorUpdateCoordinator.__new__(
        MammotionDeviceErrorUpdateCoordinator
    )
    coordinator.data = MowingDevice()
    coordinator.data.errors.err_code_list = [-2800] + [0] * (len(times) - 1)
    coordinator.data.errors.err_code_list_time = times
    return coordinator


@pytest.mark.regression
@pytest.mark.parametrize(
    "times",
    [[_UPTIME_STAMP, *[0] * 9], [0] * 10],
    ids=["uptime_stamp", "zero_padding"],
)
def test_the_error_time_is_unknown_until_the_entry_has_a_real_time(
    times: list[int],
) -> None:
    """A fresh entry carries the mower's uptime and an empty log zero padding.

    Both were handed to ``fromtimestamp`` and showed as a 1970 date, so anything
    comparing the fault time with "now" took a new fault for a decades-old one.
    """
    coordinator = _error_time_coordinator(times)

    assert coordinator.get_error_time(1) is None
    assert _ERROR_TIME.value_fn(coordinator, coordinator.data) is None


def test_the_error_time_is_the_entrys_utc_time() -> None:
    """Once rewritten, the slot holds real UTC epoch seconds."""
    coordinator = _error_time_coordinator([_REAL_EPOCH, *[0] * 9])

    assert _ERROR_TIME.value_fn(coordinator, coordinator.data) == datetime.datetime(
        2025, 9, 8, 17, 4, 11, tzinfo=datetime.UTC
    )


def test_an_unfetched_error_log_has_no_time() -> None:
    """Before the first read the lists are empty."""
    assert _error_time_coordinator([]).get_error_time(1) is None


def _spino_error_time_coordinator(timestamp: int | None) -> MammotionSpinoCoordinator:
    """Return a bare Spino coordinator; ``get_error_time`` reads only ``self.data``."""
    coordinator = MammotionSpinoCoordinator.__new__(MammotionSpinoCoordinator)
    coordinator.data = PoolCleanerDevice()
    if timestamp is not None:
        coordinator.data.pool_state.error_log = [
            SpinoErrorEntry(code=-1001, timestamp=timestamp)
        ]
    return coordinator


@pytest.mark.regression
@pytest.mark.parametrize("timestamp", [_UPTIME_STAMP, 0], ids=["uptime", "zero"])
def test_a_spino_error_without_a_real_time_has_none(timestamp: int) -> None:
    """The Spino path had no lower bound and showed the same 1970 dates."""
    assert _spino_error_time_coordinator(timestamp).get_error_time() is None


def test_a_spino_error_time_is_utc() -> None:
    """A real epoch is reported as is."""
    assert _spino_error_time_coordinator(_REAL_EPOCH).get_error_time() == (
        datetime.datetime(2025, 9, 8, 17, 4, 11, tzinfo=datetime.UTC)
    )


def test_an_empty_spino_error_log_has_no_time() -> None:
    """No entry, no time."""
    assert _spino_error_time_coordinator(None).get_error_time() is None


@pytest.mark.regression
async def test_a_warning_notification_reads_the_error_log(
    hass: HomeAssistant, make_coordinator: _Factory
) -> None:
    """A fault logged outside the watched transitions used to stay hidden for hours.

    The warning arrived and the coordinator only re-published the list it already
    had.  The read waits a few seconds so the firmware can replace the entry's
    uptime stamp with real time first.
    """
    coordinator, handle = await make_coordinator()

    await _notification_handler(handle)(_WARNING)
    await hass.async_block_till_done()
    assert _error_log_reads(coordinator) == []

    await _advance(hass, 11)
    assert _error_log_reads(coordinator) == [2, 3]


async def test_a_burst_of_warnings_reads_the_log_once(
    hass: HomeAssistant, make_coordinator: _Factory
) -> None:
    """A fault posts several events and usually a mode change; one read covers them."""
    coordinator, handle = await make_coordinator()
    handler = _notification_handler(handle)

    await handler(_WARNING)
    await handler(DeviceNotification(_NAME, "device_warning_event", {"data": "[]"}))
    await _sys_status_handler(handle)(WorkMode.MODE_PAUSE)
    await _advance(hass, 11)

    assert _error_log_reads(coordinator) == [2, 3]


async def test_other_notifications_do_not_read_the_log(
    hass: HomeAssistant, make_coordinator: _Factory
) -> None:
    """Only warnings say the log changed; the rest would spend sends for nothing."""
    coordinator, handle = await make_coordinator()

    await _notification_handler(handle)(
        DeviceNotification(_NAME, "device_information_event", {"data": "[]"})
    )
    await _advance(hass, 11)

    assert _error_log_reads(coordinator) == []


@pytest.mark.regression
@pytest.mark.parametrize(
    "mode",
    [
        WorkMode.MODE_READY,
        WorkMode.MODE_WORKING,
        WorkMode.MODE_RETURNING,
        WorkMode.MODE_LOCK,
        WorkMode.MODE_PAUSE,
    ],
)
async def test_entering_a_watched_mode_reads_the_error_log(
    hass: HomeAssistant, make_coordinator: _Factory, mode: WorkMode
) -> None:
    """A fault logged as the mower settles in ``MODE_READY`` was read only at the next job."""
    coordinator, handle = await make_coordinator()

    await _sys_status_handler(handle)(mode)
    await _advance(hass, 11)

    assert _error_log_reads(coordinator) == [2, 3]


async def test_an_unwatched_mode_does_not_read_the_log(
    hass: HomeAssistant, make_coordinator: _Factory
) -> None:
    """Charging and the like say nothing about the log."""
    coordinator, handle = await make_coordinator()

    await _sys_status_handler(handle)(WorkMode.MODE_UPDATING)
    await _advance(hass, 11)

    assert _error_log_reads(coordinator) == []


@pytest.mark.parametrize(
    ("usable", "enabled"),
    [(False, True), (True, False)],
    ids=["offline", "updates_off"],
)
async def test_no_read_is_sent_to_an_unreachable_or_switched_off_mower(
    hass: HomeAssistant, make_coordinator: _Factory, usable: bool, enabled: bool
) -> None:
    """A background read follows the offline gate and the updates switch."""
    coordinator, handle = await make_coordinator(usable=usable, enabled=enabled)

    await _notification_handler(handle)(_WARNING)
    await _advance(hass, 11)

    coordinator.manager.send_command_and_wait.assert_not_awaited()


async def test_a_failed_read_leaves_later_warnings_working(
    hass: HomeAssistant, make_coordinator: _Factory, caplog: pytest.LogCaptureFixture
) -> None:
    """A rate-limited read is dropped quietly and the next warning reads again."""
    coordinator, handle = await make_coordinator()
    handler = _notification_handler(handle)
    coordinator.manager.send_command_and_wait.side_effect = TransportRateLimitedError(
        "quota"
    )

    await handler(_WARNING)
    await _advance(hass, 11)
    coordinator.manager.send_command_and_wait.side_effect = None
    # Lets the debouncer's cooldown after the failed read run out.
    await _advance(hass, 11)
    await handler(_WARNING)
    await _advance(hass, 11)

    assert _error_log_reads(coordinator) == [2, 2, 3]
    # The debouncer logs anything the read let escape; nothing may reach it.
    assert "Unexpected exception" not in caplog.text


async def test_startup_reads_the_error_log(
    hass: HomeAssistant, make_coordinator: _Factory
) -> None:
    """Start-up reads both registers at once rather than waiting for a trigger."""
    coordinator, _handle = await make_coordinator()

    await coordinator._async_startup_reads()

    assert _error_log_reads(coordinator) == [2, 3]


async def test_shutdown_drops_a_pending_read(
    hass: HomeAssistant, make_coordinator: _Factory
) -> None:
    """A read scheduled just before unload must not fire into a torn-down entry."""
    coordinator, handle = await make_coordinator()

    await _notification_handler(handle)(_WARNING)
    await coordinator.async_shutdown()
    await _advance(hass, 11)

    assert _error_log_reads(coordinator) == []
