"""A BLE-only mower gets a Bluetooth switch but no cloud switch.

The stubbed version replaced every switch class with a ``MagicMock`` and read
the keys back off its call log, so it never built a switch.  Here ``switch.py``
runs against a real Home Assistant with its real entity classes and the real
``DeviceType`` checks, and the keys come off the entities it produced.
"""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pymammotion.data.model.device import MowingDevice

from custom_components.mammotion.switch import async_setup_entry

_BLE_ONLY = "Luba-VS111111"
_CLOUD = "Luba-VS222222"


def _mower(hass: HomeAssistant, name: str, iot_id: str) -> MagicMock:
    """Build a runtime mower record whose coordinator has no map yet."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data = MowingDevice()
    coordinator.device_name = name
    coordinator.unique_name = name
    coordinator.operation_settings.areas = []
    coordinator.manager.get_device_by_name.return_value = None
    mower = MagicMock()
    mower.device.device_name = name
    mower.device.iot_id = iot_id
    mower.reporting_coordinator = coordinator
    return mower


async def _switch_keys(hass: HomeAssistant, mower: MagicMock) -> set[str]:
    """Set up the switch platform for one mower and collect the keys it added."""
    entities: list[Any] = []
    entry = MagicMock()
    entry.runtime_data.mowers = [mower]
    entry.runtime_data.spino = []

    await async_setup_entry(hass, entry, entities.extend)

    return {entity.entity_description.key for entity in entities}


async def test_ble_only_mower_has_bluetooth_but_no_cloud_switch(
    hass: HomeAssistant,
) -> None:
    """Without an iot_id there is no cloud connection to switch off."""
    keys = await _switch_keys(hass, _mower(hass, _BLE_ONLY, ""))

    assert "bluetooth_enabled" in keys
    assert "cloud_enabled" not in keys


async def test_cloud_mower_has_both_connectivity_switches(
    hass: HomeAssistant,
) -> None:
    """A mower bound to an account can be moved between both transports."""
    keys = await _switch_keys(hass, _mower(hass, _CLOUD, "iot-123"))

    assert {"bluetooth_enabled", "cloud_enabled"} <= keys
