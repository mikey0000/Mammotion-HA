"""Saving the options applies "prefer Bluetooth" live, by the same rule setup uses.

Setup prefers BLE only while the mower's Bluetooth switch is on, and always
when the entry has no Wi-Fi.  The options flow applied the checkbox as-is, so
saving options re-preferred a transport the user had switched off.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pymammotion.client import MammotionClient
from pymammotion.device.handle import DeviceHandle
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import (
    CONF_MOVEMENT_USE_WIFI,
    CONF_MOW_PATH_FETCH_ENABLED,
    CONF_PREFER_BLE,
    CONF_USE_WIFI,
    DOMAIN,
)
from custom_components.mammotion.models import MammotionDevices

_MOWER = "Luba-VS123456"


@pytest.mark.parametrize(
    ("use_wifi", "bluetooth_enabled", "prefer_ble", "expected"),
    [
        (True, True, True, True),
        (True, True, False, False),
        (True, False, True, False),
        (False, True, False, True),
        (False, False, False, False),
    ],
)
async def test_saving_options_applies_the_setup_rule(
    hass: HomeAssistant,
    use_wifi: bool,
    bluetooth_enabled: bool,
    prefer_ble: bool,
    expected: bool,
) -> None:
    """The Bluetooth switch wins over the checkbox, and a BLE-only entry always prefers BLE."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USE_WIFI: use_wifi})
    entry.add_to_hass(hass)
    mower = MagicMock()
    mower.name = _MOWER
    mower.reporting_coordinator.bluetooth_enabled = bluetooth_enabled
    mower.api.mower.return_value = create_autospec(DeviceHandle, instance=True)
    entry.runtime_data = MammotionDevices(mowers=[mower], RTK=[], spino=[])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PREFER_BLE: prefer_ble,
            CONF_MOVEMENT_USE_WIFI: False,
            CONF_MOW_PATH_FETCH_ENABLED: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    mower.api.set_prefer_ble.assert_called_once_with(_MOWER, prefer_ble=expected)


@pytest.mark.regression
async def test_unchecking_prefer_ble_disconnects_active_ble_link(
    hass: HomeAssistant,
) -> None:
    """Turning off "prefer Bluetooth" left an active BLE link connected (#931).

    DeviceHandle.active_transport selects a connected BLE link ahead of MQTT
    regardless of prefer_ble, so set_prefer_ble(False) alone kept BLE winning and
    holding the proxy slot until the transport was disconnected.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USE_WIFI: True})
    entry.add_to_hass(hass)
    handle = create_autospec(DeviceHandle, instance=True)
    api = create_autospec(MammotionClient, instance=True)
    api.mower.return_value = handle
    mower = SimpleNamespace(
        name=_MOWER,
        api=api,
        reporting_coordinator=SimpleNamespace(bluetooth_enabled=True),
    )
    entry.runtime_data = MammotionDevices(mowers=[mower], RTK=[], spino=[])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PREFER_BLE: False,
            CONF_MOVEMENT_USE_WIFI: False,
            CONF_MOW_PATH_FETCH_ENABLED: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    api.set_prefer_ble.assert_called_once_with(_MOWER, prefer_ble=False)
    handle.disconnect_transport.assert_awaited_once_with(TransportType.BLE)
