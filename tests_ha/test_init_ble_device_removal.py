"""Deleting a BLE-configured mower from its device page.

The account does not list a mower set up over Bluetooth, so the only sign that
it is still around is its advertisement.
"""

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import async_remove_config_entry_device
from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)
from custom_components.mammotion.models import MammotionDevices
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_HEARD_MAC = "aa:bb:cc:dd:ee:01"
#: Never advertised by any test, so the bluetooth manager does not know it.
_UNHEARD_MAC = "0a:0b:0c:0d:0e:0f"


def _loaded_ble_entry(hass: HomeAssistant, address: str) -> MockConfigEntry:
    """Add a loaded entry that has ``_MOWER`` configured over BLE at ``address``.

    The handler reads the account's devices off ``runtime_data`` only to refuse
    the ones the cloud lists, and a BLE mower is never among them.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="owner@example.com",
        data={
            CONF_ACCOUNTNAME: "owner@example.com",
            CONF_HAS_CLOUD_ACCOUNT: True,
            CONF_BLE_DEVICES: {_MOWER: address},
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = MammotionDevices(mowers=[], RTK=[], spino=[])
    return entry


@pytest.mark.usefixtures("enable_bluetooth")
async def test_removing_a_ble_mower_that_is_out_of_range_forgets_it(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """A BLE mower no adapter hears any more leaves the entry data and the store."""
    entry = _loaded_ble_entry(hass, _UNHEARD_MAC)
    store = async_get_store(hass, entry)
    store.async_update_device_data(_MOWER, {"name": _MOWER})
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )

    with patch.object(
        hass.config_entries, "async_schedule_reload", autospec=True
    ) as reload:
        assert await async_remove_config_entry_device(hass, entry, device) is True

    assert entry.data[CONF_BLE_DEVICES] == {}
    assert _MOWER not in store.device_data
    reload.assert_called_once_with(entry.entry_id)


@pytest.mark.usefixtures("enable_bluetooth")
async def test_removing_a_ble_mower_still_in_range_is_refused(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """A BLE mower that is still advertising would only be rediscovered; it stays."""
    entry = _loaded_ble_entry(hass, _HEARD_MAC)
    store = async_get_store(hass, entry)
    store.async_update_device_data(_MOWER, {"name": _MOWER})
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )
    inject_advertisement(hass, _MOWER, _HEARD_MAC.upper())

    assert await async_remove_config_entry_device(hass, entry, device) is False

    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _HEARD_MAC}
    assert _MOWER in store.device_data
