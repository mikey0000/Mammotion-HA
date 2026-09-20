"""Turning "Enable updates" off must not take the device offline (issue #889).

The switch used to call ``MammotionClient.set_scheduled_updates``, which
disconnects BLE and detaches the account's cloud transports from the handle.
That dropped both legs of ``MammotionBaseEntity.available``, so every entity of
the device — the switch itself included — went unavailable with no way back
short of reloading the config entry, and the position was never written to disk
so the reload brought it back on.

The equivalent file in ``tests/`` could only read ``coordinator.py`` and check
that the right tokens appeared.  Here the real coordinator methods run against
a real ``MowingDevice``, so a wrong value fails and not just a missing token.
"""

from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.entity import MammotionBaseEntity
from custom_components.mammotion.switch import (
    UPDATE_SWITCH_ENTITIES,
    MammotionUpdateSwitchEntity,
)

_DEVICE_NAME = "Luba-VS1000001"
_SWITCH = UPDATE_SWITCH_ENTITIES[0]


def _handle() -> MagicMock:
    """Stand in for the pymammotion device handle the coordinator drives."""
    handle = MagicMock()
    handle.stop_polling = AsyncMock()
    handle.resume_polling = AsyncMock()
    handle.restart_keep_alive = AsyncMock()
    handle.set_cloud_attached = MagicMock()
    handle.is_transport_connected = MagicMock(return_value=False)
    return handle


def _coordinator(
    *, enabled: bool = True, handle: MagicMock | None = None
) -> MammotionReportUpdateCoordinator:
    """Build the real coordinator; only the client, store and entry are stand-ins."""
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    device = MowingDevice()
    device.enabled = enabled
    coordinator.data = device
    coordinator.device_name = _DEVICE_NAME
    coordinator.update_failures = 0
    coordinator._startup_reads_done = False
    coordinator._bluetooth_enabled = True
    coordinator._on_stop = []
    coordinator._subscriptions = []
    coordinator.hass = MagicMock()
    coordinator.manager = MagicMock()
    coordinator.manager.get_device_by_name.return_value = device
    coordinator.manager.mower.return_value = handle if handle is not None else _handle()
    coordinator.async_save_data = MagicMock()
    coordinator.async_flush_saved_data = AsyncMock()
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_request_report_snapshot = AsyncMock()
    coordinator._async_startup_reads = AsyncMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coro, _name: coro.close()
    )
    return coordinator


def _capture_background_tasks(
    coordinator: MammotionReportUpdateCoordinator,
) -> list[Coroutine[Any, Any, None]]:
    """Hold on to whatever the coordinator hands to the event loop."""
    tasks: list[Coroutine[Any, Any, None]] = []
    coordinator.config_entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coro, _name: tasks.append(coro)
    )
    return tasks


def _switch_entity(
    coordinator: MammotionReportUpdateCoordinator,
) -> MammotionUpdateSwitchEntity:
    """Build the real switch on top of *coordinator*."""
    entity = MammotionUpdateSwitchEntity.__new__(MammotionUpdateSwitchEntity)
    entity.coordinator = coordinator
    entity.entity_description = _SWITCH
    return entity


async def test_the_switch_no_longer_touches_transports() -> None:
    """Transport state belongs to the Bluetooth and Cloud switches alone."""
    handle = _handle()
    coordinator = _coordinator(enabled=True, handle=handle)

    await coordinator.set_scheduled_updates(False)

    assert coordinator.data.enabled is False
    handle.stop_polling.assert_awaited_once()
    coordinator.manager.set_scheduled_updates.assert_not_called()
    handle.set_cloud_attached.assert_not_called()


async def test_the_position_is_flushed_to_disk_when_it_changes() -> None:
    """Polling stops on disable, so nothing else would ever save the snapshot."""
    coordinator = _coordinator(enabled=True)

    assert await coordinator.set_scheduled_updates(False) is True

    coordinator.async_save_data.assert_called_once_with(coordinator.data)
    coordinator.async_flush_saved_data.assert_awaited_once()


async def test_an_unchanged_position_is_not_written() -> None:
    """The inbound MQTT handlers re-assert True on every frame."""
    coordinator = _coordinator(enabled=True)

    assert await coordinator.set_scheduled_updates(True) is False

    coordinator.async_save_data.assert_not_called()
    coordinator.async_flush_saved_data.assert_not_awaited()


async def test_enabling_resumes_polling_rather_than_only_restarting_keepalive() -> None:
    """restart_keep_alive honours the stop this switch set, so it alone never returns."""
    handle = _handle()
    coordinator = _coordinator(enabled=False, handle=handle)

    await coordinator.set_scheduled_updates(True)

    handle.resume_polling.assert_awaited_once()
    handle.restart_keep_alive.assert_not_called()


async def test_a_restored_disabled_device_stays_quiet() -> None:
    """``enabled`` round-trips through the device snapshot, so setup must honour it."""
    handle = _handle()
    coordinator = _coordinator(enabled=False, handle=handle)

    await coordinator._async_setup()

    handle.stop_polling.assert_awaited_once()
    coordinator._async_startup_reads.assert_not_awaited()


async def test_setup_still_wires_the_watch_when_updates_are_off() -> None:
    """_async_setup runs once a session; an early return stranded it till a reload."""
    handle = _handle()
    coordinator = _coordinator(enabled=False, handle=handle)

    await coordinator._async_setup()

    handle.watch_field.assert_called_once()
    handle.subscribe_state_changed.assert_called_once()


async def test_re_enabling_runs_the_reads_setup_skipped() -> None:
    """Otherwise the settings entities sit on defaults until the entry reloads."""
    coordinator = _coordinator(enabled=False)
    scheduled = _capture_background_tasks(coordinator)

    await coordinator.set_scheduled_updates(True)
    await scheduled[0]

    coordinator._async_startup_reads.assert_awaited_once()


async def test_the_startup_reads_do_not_block_the_service_call() -> None:
    """They carry a 60s budget; awaiting them would hold switch.turn_on open."""
    coordinator = _coordinator(enabled=False)
    scheduled = _capture_background_tasks(coordinator)

    await coordinator.set_scheduled_updates(True)

    coordinator._async_startup_reads.assert_not_awaited()
    scheduled[0].close()


async def test_the_transition_comes_from_the_base_not_a_second_read() -> None:
    """The base decides whether anything changed; the override must not re-read it."""
    coordinator = _coordinator(enabled=True)
    scheduled = _capture_background_tasks(coordinator)

    assert await coordinator.set_scheduled_updates(True) is False

    assert scheduled == []


def test_the_switch_cannot_strand_itself() -> None:
    """It is the only way back from updates-off, so it overrides the base check."""
    coordinator = _coordinator(enabled=False)
    coordinator.is_online = MagicMock(return_value=False)
    entity = _switch_entity(coordinator)

    assert MammotionBaseEntity.available.fget(entity) is False
    assert entity.available is True


def test_the_switch_is_unavailable_only_without_state_to_act_on() -> None:
    """``data`` is None between coordinator construction and the first refresh."""
    coordinator = _coordinator()
    coordinator.data = None

    assert _switch_entity(coordinator).available is False


@pytest.mark.parametrize("enabled", [True, False])
def test_the_switch_shows_the_stored_position(enabled: bool) -> None:
    """The position lives in the device snapshot, so a restart restores it."""
    assert _switch_entity(_coordinator(enabled=enabled)).is_on is enabled


async def test_reconnecting_ble_respects_the_updates_switch() -> None:
    """A BLE reconnect restarts the device's stream, undoing the stop."""
    coordinator = _coordinator(enabled=False)

    await coordinator._async_reconnect_ble()

    coordinator.manager.mower.assert_not_called()


async def test_the_sys_status_watch_stays_quiet_while_off() -> None:
    """The watch is wired either way, so the callback is what has to check."""
    coordinator = _coordinator(enabled=False)

    await coordinator._on_sys_status_changed_refresh(13)

    coordinator.async_request_report_snapshot.assert_not_awaited()
