"""No coordinator talks to a device whose updates switch is off.

The report coordinator was fixed for #889, but its three siblings —
maintenance, version and errors — each sent their one-off reads from
``_async_setup`` regardless, so a device set up with updates off was still
probed four ways on every Home Assistant start.

The reads now sit behind a shared hook.  ``_async_setup`` keeps doing its
wiring either way, because it runs once per session and an early return there
would strand the ``sys_status`` watches until a config-entry reload.

The version in ``tests/`` read the four classes out of ``coordinator.py`` and
looked for tokens.  Here each ``_async_setup`` runs against a real
``MowingDevice`` whose ``enabled`` flag is the thing under test, and each
startup-reads hook runs with only the send layer replaced.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.coordinator import (
    MammotionBaseUpdateCoordinator,
    MammotionDeviceErrorUpdateCoordinator,
    MammotionDeviceVersionUpdateCoordinator,
    MammotionMaintenanceUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)

_DEVICE_NAME = "Luba-VS1000001"

#: Sends that used to go out from _async_setup unconditionally.
_PROBES = {
    MammotionReportUpdateCoordinator: "send_todev_ble_sync",
    MammotionMaintenanceUpdateCoordinator: "get_maintenance",
    MammotionDeviceVersionUpdateCoordinator: "get_device_version_main",
    MammotionDeviceErrorUpdateCoordinator: "read_write_device",
}
_COORDINATORS = list(_PROBES)


def _coordinator(
    coordinator_class: type[MammotionBaseUpdateCoordinator], *, enabled: bool = True
) -> MammotionBaseUpdateCoordinator:
    """Build the real coordinator with only the send layer and the client replaced."""
    coordinator = coordinator_class.__new__(coordinator_class)
    device = MowingDevice()
    device.enabled = enabled
    # A populated table keeps the error coordinator off its cloud-lookup branch.
    device.errors.error_codes = {"1": "known"}
    coordinator.data = device
    coordinator.device = device
    coordinator.device_name = _DEVICE_NAME
    coordinator.update_failures = 0
    coordinator._startup_reads_done = False
    coordinator._bluetooth_enabled = False
    coordinator._on_stop = []
    coordinator._subscriptions = []
    coordinator._prev_sys_status = None
    coordinator.hass = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.manager.get_device_by_name.return_value = device
    coordinator.manager.mower.return_value = _handle()
    coordinator.manager.request_report_snapshot = AsyncMock()
    coordinator.async_send_command = AsyncMock()
    coordinator.async_send_and_wait = AsyncMock()
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_save_data = MagicMock()
    coordinator.is_online = MagicMock(return_value=False)
    return coordinator


def _handle() -> MagicMock:
    """Stand in for the pymammotion device handle the coordinator drives."""
    handle = MagicMock()
    handle.stop_polling = AsyncMock()
    handle.resume_polling = AsyncMock()
    return handle


def _sent_commands(coordinator: MammotionBaseUpdateCoordinator) -> list[Any]:
    """Return the command names both send helpers were asked for."""
    return [
        call.args[0]
        for call in (
            *coordinator.async_send_command.await_args_list,
            *coordinator.async_send_and_wait.await_args_list,
        )
    ]


@pytest.mark.parametrize("coordinator_class", _COORDINATORS)
async def test_setup_sends_nothing_while_updates_are_off(
    coordinator_class: type[MammotionBaseUpdateCoordinator],
) -> None:
    """A device restored with the switch off must not be probed four ways."""
    coordinator = _coordinator(coordinator_class, enabled=False)

    await coordinator._async_setup()

    assert _sent_commands(coordinator) == []


@pytest.mark.parametrize("coordinator_class", _COORDINATORS)
async def test_setup_still_runs_the_reads_when_updates_are_on(
    coordinator_class: type[MammotionBaseUpdateCoordinator],
) -> None:
    """The probes moved behind the hook rather than being dropped."""
    coordinator = _coordinator(coordinator_class, enabled=True)

    await coordinator._async_setup()

    assert _PROBES[coordinator_class] in _sent_commands(coordinator)


@pytest.mark.parametrize("coordinator_class", _COORDINATORS)
async def test_the_one_time_wiring_still_happens_when_disabled(
    coordinator_class: type[MammotionBaseUpdateCoordinator],
) -> None:
    """An early return here would strand it until a reload — the #889 trap."""
    coordinator = _coordinator(coordinator_class, enabled=False)
    handle = coordinator.manager.mower.return_value

    await coordinator._async_setup()

    handle.subscribe_state_changed.assert_called_once()
    handle.subscribe_device_status.assert_called_once()


@pytest.mark.parametrize("coordinator_class", _COORDINATORS)
async def test_the_gate_runs_the_reads_once_not_once_per_refresh(
    coordinator_class: type[MammotionBaseUpdateCoordinator],
) -> None:
    """On means exactly one run, however often the gate is reached."""
    coordinator = _coordinator(coordinator_class, enabled=True)

    await coordinator._async_ensure_startup_reads()
    sent_after_first = _sent_commands(coordinator)
    await coordinator._async_ensure_startup_reads()

    assert sent_after_first
    assert _sent_commands(coordinator) == sent_after_first


async def test_the_gate_stays_shut_for_an_unknown_device() -> None:
    """The client drops the record on logout; the reads have nowhere to go."""
    coordinator = _coordinator(MammotionMaintenanceUpdateCoordinator, enabled=True)
    coordinator.manager.get_device_by_name.return_value = None

    await coordinator._async_ensure_startup_reads()

    assert _sent_commands(coordinator) == []


async def test_a_sibling_catches_up_on_its_first_enabled_refresh() -> None:
    """The switch only reaches the report coordinator, so the others self-heal."""
    coordinator = _coordinator(MammotionMaintenanceUpdateCoordinator, enabled=False)

    await coordinator._async_update_data()
    assert _sent_commands(coordinator) == []

    coordinator.data.enabled = True
    await coordinator._async_update_data()

    assert _PROBES[MammotionMaintenanceUpdateCoordinator] in _sent_commands(coordinator)
