"""The report coordinator must hear its mower's advertisements whatever case the MAC is stored in.

Home Assistant indexes address callbacks by the exact string and advertisements
always carry an upper-case address, while the config entry and the mower's own
network report both store it lower-case.  The subscription never fired, so a
mower whose only enabled transport is Bluetooth waited for the five-minute
report refresh to reconnect instead of connecting on the first advertisement.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _create_ble_only_device
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_MOWER_MAC = "AA:BB:CC:DD:EE:FF"


def _coordinator(
    hass: HomeAssistant, ble: MagicMock, *, stored_mac: str
) -> MammotionReportUpdateCoordinator:
    """Build the real report coordinator for a mower whose saved MAC is *stored_mac*."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_MOWER_MAC.lower(),
        data={CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC.lower()}},
    )
    entry.add_to_hass(hass)
    device = MowingDevice(name=_MOWER)
    device.mower_state.ble_mac = stored_mac
    handle = MagicMock()
    handle.get_transport = MagicMock(return_value=ble)
    manager = MagicMock()
    manager.get_device_by_name = MagicMock(return_value=device)
    manager.mower = MagicMock(return_value=handle)
    manager.update_ble_device = AsyncMock()
    return MammotionReportUpdateCoordinator(
        hass, entry, _create_ble_only_device(_MOWER), manager, unique_name=_MOWER
    )


@pytest.mark.usefixtures("enable_bluetooth")
@pytest.mark.parametrize("stored_mac", [_MOWER_MAC.lower(), _MOWER_MAC])
async def test_an_advertisement_reaches_the_coordinator_and_connects(
    hass: HomeAssistant, stored_mac: str
) -> None:
    """The first advertisement after startup is what brings a Bluetooth-only link up."""
    ble = MagicMock(is_connected=False)
    ble.connect = AsyncMock()
    coordinator = _coordinator(hass, ble, stored_mac=stored_mac)
    coordinator._async_start()

    service_info = inject_advertisement(hass, _MOWER, _MOWER_MAC, rssi=-55)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert coordinator.service_info is service_info
    coordinator.manager.update_ble_device.assert_awaited_once_with(
        _MOWER, service_info.device, -55
    )
    ble.connect.assert_awaited_once()
    coordinator._async_stop()
    coordinator.poll_debouncer.async_cancel()
