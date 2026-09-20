"""Enabling scheduled updates swallows a transport miss instead of raising into the event bus.

``set_scheduled_updates`` is called from the inbound MQTT status handler as well
as from the switch, so a BLE miss while resuming the poll loops (cooldown, stale
GATT cache) would otherwise escape into the state bus on a frame the user never
triggered.  Polling carries on over MQTT, so the position still stands.
"""

from unittest.mock import AsyncMock, MagicMock

from pymammotion.data.model.device import MowingDevice
from pymammotion.transport.base import TransportError

from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator


def _coordinator(handle: MagicMock) -> MammotionReportUpdateCoordinator:
    """Build the real coordinator; only the client, store and entry are stand-ins."""
    coordinator = MammotionReportUpdateCoordinator.__new__(
        MammotionReportUpdateCoordinator
    )
    device = MowingDevice()
    device.enabled = False
    coordinator.data = device
    coordinator.device_name = "Luba-VS1000001"
    coordinator.update_failures = 3
    coordinator.manager = MagicMock()
    coordinator.manager.get_device_by_name.return_value = device
    coordinator.manager.mower.return_value = handle
    coordinator.async_save_data = MagicMock()
    coordinator.async_flush_saved_data = AsyncMock()
    coordinator._async_ensure_startup_reads = AsyncMock()
    coordinator.hass = MagicMock()
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.async_create_background_task = MagicMock(
        side_effect=lambda _hass, coro, _name: coro.close()
    )
    return coordinator


async def test_a_transport_miss_does_not_fail_enabling_updates() -> None:
    """The position is stored before the poll restart, so only the restart missed."""
    handle = MagicMock()
    handle.resume_polling = AsyncMock(side_effect=TransportError("in cooldown"))
    coordinator = _coordinator(handle)

    assert await coordinator.set_scheduled_updates(True) is True

    assert coordinator.data.enabled is True
    coordinator.async_flush_saved_data.assert_awaited_once()
