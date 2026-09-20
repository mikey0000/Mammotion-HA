"""Regression test: bluetooth discovery must schedule at most one entry reload.

Bug: ``async_step_bluetooth`` / ``async_step_bluetooth_confirm`` call
``check_and_update_bluetooth_device``, which calls ``async_schedule_reload`` when
it learns a new BLE address, and then call ``_abort_if_unique_id_configured``
with ``updates=``.  That helper defaults to ``reload_on_update=True``, so a
change to CONF_BLE_DEVICES scheduled a *second* reload of an already-loaded
entry.

Each reload runs ``async_setup_entry`` again, which builds a fresh
MammotionClient and logs in.  Two live clients connect to the broker with the
same client_id, the broker rejects both with "Not authorized", and pymammotion
reads that as a credential failure and gives up on cloud MQTT for the rest of
the process.

The equivalent check in ``tests/`` parsed ``config_flow.py`` for the
``reload_on_update=False`` keyword, because the flow could not be imported under
the stub harness.  Here the flow runs against a real loaded entry and the
reloads are counted.
"""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_BLE_DEVICES,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_MOWER_MAC = "AA:BB:CC:DD:EE:FF"
_ACCOUNT = "owner@example.com"


def _loaded_cloud_entry(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    """Add a Wi-Fi-only entry that Home Assistant has already set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            CONF_HAS_CLOUD_ACCOUNT: True,
            **data,
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _register_mower(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, entry: MockConfigEntry
) -> dr.DeviceEntry:
    """Give the entry a mower device with no BLE connection recorded yet."""
    return device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, _MOWER)}
    )


@pytest.mark.usefixtures("enable_bluetooth")
async def test_learning_a_ble_address_reloads_the_entry_once(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Writing CONF_BLE_DEVICES must not trigger a reload of its own."""
    entry = _loaded_cloud_entry(hass)
    _register_mower(hass, device_registry, entry)
    service_info = inject_advertisement(hass, _MOWER, _MOWER_MAC)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=service_info,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC.lower()}
    schedule_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.usefixtures("enable_bluetooth")
async def test_confirming_a_pending_discovery_card_reloads_the_entry_once(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """The confirm step repeats the merge, so it carries the same guard.

    A discovery card can sit unanswered while the mower is added another way;
    answering it then runs the merge for the first time against a loaded entry.
    """
    service_info = inject_advertisement(hass, _MOWER, _MOWER_MAC)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info,
    )
    assert result["step_id"] == "bluetooth_confirm"

    entry = _loaded_cloud_entry(hass)
    _register_mower(hass, device_registry, entry)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    schedule_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.usefixtures("enable_bluetooth")
async def test_an_entry_that_already_knows_the_address_is_not_reloaded_at_all(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Re-advertising a mower nothing has changed about must not restart its client."""
    entry = _loaded_cloud_entry(hass, ble_devices={_MOWER: _MOWER_MAC.lower()})
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _MOWER)},
        connections={(dr.CONNECTION_BLUETOOTH, _MOWER_MAC.lower())},
    )
    service_info = inject_advertisement(hass, _MOWER, _MOWER_MAC)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=service_info,
        )

    assert result["type"] is FlowResultType.ABORT
    schedule_reload.assert_not_called()


@pytest.mark.usefixtures("enable_bluetooth")
async def test_the_merge_tolerates_an_entry_with_no_ble_devices_key(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """``**entry.data.get(CONF_BLE_DEVICES, None)`` raised TypeError for a Wi-Fi-only entry.

    That aborted the discovery flow outright, so the mower could never gain its
    BLE address.
    """
    entry = _loaded_cloud_entry(hass)
    assert CONF_BLE_DEVICES not in entry.data
    _register_mower(hass, device_registry, entry)
    service_info = inject_advertisement(hass, _MOWER, _MOWER_MAC)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info,
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_BLE_DEVICES] == {_MOWER: _MOWER_MAC.lower()}
