"""A device removed from the Mammotion account, followed end to end through Home Assistant.

The entry is the anonymised config entry in ``fixtures/config_entries.json``,
set up for real with its platforms. The account then drops one of its mowers
the way pymammotion reports it (``on_device_removed`` after its own device
lists are pruned), and the test follows the reload and a restart: what the
device registry, the entity registry, ``runtime_data`` and the stored state on
disk hold afterwards.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from device_removed_support import make_removal_client, patched_setup
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pymammotion.aliyun.model.dev_by_account_response import Device
from pymammotion.data.model.device import MowingDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.config import MammotionConfigStore, async_get_store
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "config_entries.json").read_text()
)
_RECORD = _FIXTURE["data"]["mammotion_device_records"]["records"][0]
_GONE = _RECORD["device_name"]
_KEPT = "Luba-VA000099"
_GONE_MAC = "aa:bb:cc:dd:ee:01"
_CAPABILITY = MammotionConfigStore.capability_key(_RECORD["product_key"], "1")
_STORE_KEY = f"{DOMAIN}.{_FIXTURE['entry_id']}"


def _device(name: str, iot_id: str) -> Device:
    """Return the account's record of a mower, from the fixture's device record."""
    return Device(
        gmt_modified=0,
        node_type="DEVICE",
        device_name=name,
        product_name=name,
        status=_RECORD["status"],
        identity_id=_RECORD["identity_id"],
        net_type="NET_WIFI",
        category_key="LawnMower",
        product_key=_RECORD["product_key"],
        is_edge_gateway=False,
        category_name="",
        identity_alias=name,
        iot_id=iot_id,
        bind_time=_RECORD["bind_time"],
        owned=_RECORD["owned"],
        thing_type="DEVICE",
    )


def _entry(hass: HomeAssistant, ble_devices: dict[str, str]) -> MockConfigEntry:
    """Add the fixture's config entry, enabled, with ``ble_devices`` configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=_FIXTURE["entry_id"],
        unique_id=_FIXTURE["unique_id"],
        title=_FIXTURE["title"],
        version=_FIXTURE["version"],
        minor_version=_FIXTURE["minor_version"],
        source=_FIXTURE["source"],
        data={**_FIXTURE["data"], CONF_BLE_DEVICES: ble_devices},
        options=_FIXTURE["options"],
    )
    entry.add_to_hass(hass)
    return entry


def _seed_store(hass_storage: dict[str, Any]) -> None:
    """Store state for both mowers, as a previous run left it."""
    checked = datetime(2026, 10, 1, tzinfo=UTC).isoformat()
    hass_storage[_STORE_KEY] = {
        "version": 1,
        "minor_version": 5,
        "key": _STORE_KEY,
        "data": {
            "devices": {
                name: MowingDevice(name=name).to_dict() for name in (_GONE, _KEPT)
            },
            "transports": {_GONE: {"bluetooth_enabled": False}},
            "firmware_checks": {_GONE: checked, _KEPT: checked},
            "capabilities": {_CAPABILITY: {"schema": "shared by the model"}},
        },
    }


def _stored(hass_storage: dict[str, Any]) -> dict[str, Any]:
    return hass_storage[_STORE_KEY]["data"]


def _mower_names(entry: MockConfigEntry) -> list[str]:
    return [mower.name for mower in entry.runtime_data.mowers]


def _entities_of(hass: HomeAssistant, device_id: str) -> set[str]:
    entity_registry = er.async_get(hass)
    return {
        entity.entity_id
        for entity in er.async_entries_for_device(
            entity_registry, device_id, include_disabled_entities=True
        )
    }


@pytest.fixture
def client() -> Iterator[MagicMock]:
    """Return the account's client, listing both mowers; set-up and every reload get the same one."""
    client = make_removal_client(
        [
            _device(_GONE, _RECORD["iot_id"]),
            _device(_KEPT, "00000000000000000000000099"),
        ]
    )
    with patched_setup(client, logged_in=True):
        yield client


async def _account_drops_the_mower(hass: HomeAssistant, client: MagicMock) -> None:
    """Prune the account's list and report the removal, as pymammotion does."""
    client.mammotion_device_list = [
        device for device in client.mammotion_device_list if device.device_name != _GONE
    ]
    await client.on_device_removed(_GONE, _RECORD["iot_id"])


@pytest.mark.regression
async def test_a_mower_removed_from_the_account_leaves_home_assistant_for_good(
    hass: HomeAssistant, hass_storage: dict[str, Any], client: MagicMock
) -> None:
    """Device, entities and stored state go, and neither a reload nor a restart brings them back.

    The stored state was never purged, so a restore replayed it; and a purge
    made while the mower's coordinator still polled was saved straight back.
    """
    _seed_store(hass_storage)
    entry = _entry(hass, {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device_registry = dr.async_get(hass)
    gone = device_registry.async_get_device_by_identifier(
        (DOMAIN, _GONE), entry.entry_id
    )
    assert gone is not None
    gone_entities = _entities_of(hass, gone.id)
    assert gone_entities, "the platforms should have created the mower's entities"
    gone_coordinator = entry.runtime_data.mowers[
        _mower_names(entry).index(_GONE)
    ].reporting_coordinator

    await _account_drops_the_mower(hass, client)
    # Until the reload unloads it, the mower's coordinator keeps polling and saving.
    gone_coordinator.async_save_data(MowingDevice(name=_GONE))
    assert _GONE not in async_get_store(hass, entry).device_data
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _mower_names(entry) == [_KEPT]
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, _GONE), entry.entry_id)
        is None
    )
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, _KEPT), entry.entry_id)
        is not None
    )
    entity_registry = er.async_get(hass)
    assert [e for e in gone_entities if entity_registry.async_get(e)] == []
    assert [e for e in gone_entities if hass.states.get(e)] == []
    stored = _stored(hass_storage)
    assert set(stored["devices"]) == {_KEPT}
    assert _GONE not in stored["transports"]
    assert set(stored["firmware_checks"]) == {_KEPT}
    assert _CAPABILITY in stored["capabilities"], "another mower of the model uses it"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _mower_names(entry) == [_KEPT]
    assert (
        device_registry.async_get_device_by_identifier((DOMAIN, _GONE), entry.entry_id)
        is None
    )
    assert set(_stored(hass_storage)["devices"]) == {_KEPT}
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.regression
@pytest.mark.usefixtures("enable_bluetooth")
async def test_a_mower_also_set_up_over_bluetooth_stays_as_bluetooth_only(
    hass: HomeAssistant, hass_storage: dict[str, Any], client: MagicMock
) -> None:
    """BLE control needs no account, so the same device and entities carry on without the cloud.

    The HA device was deleted and the reload re-created it from the BLE
    configuration, losing its entities' ids, names and areas.
    """
    _seed_store(hass_storage)
    entry = _entry(hass, {_GONE: _GONE_MAC})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device_registry = dr.async_get(hass)
    before = device_registry.async_get_device_by_identifier(
        (DOMAIN, _GONE), entry.entry_id
    )
    assert before is not None
    entities_before = _entities_of(hass, before.id)

    await _account_drops_the_mower(hass, client)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert sorted(_mower_names(entry)) == sorted([_GONE, _KEPT])
    gone = next(m for m in entry.runtime_data.mowers if m.name == _GONE)
    assert gone.device.net_type == "BLE", "rebuilt from the BLE configuration"
    after = device_registry.async_get_device_by_identifier(
        (DOMAIN, _GONE), entry.entry_id
    )
    assert after is not None
    assert after.id == before.id
    assert _entities_of(hass, after.id) == entities_before
    stored = _stored(hass_storage)
    assert _GONE in stored["devices"]
    assert stored["transports"][_GONE] == {"bluetooth_enabled": False}
    assert await hass.config_entries.async_unload(entry.entry_id)
