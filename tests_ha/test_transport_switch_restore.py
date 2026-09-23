"""The Bluetooth and cloud switches persist in the entry store and are applied before the first connect.

The equivalent tests in ``tests/`` drove a hand-written stand-in for
``homeassistant.helpers.storage.Store`` and read ``__init__.py`` /
``coordinator.py`` as text.  Here the real ``Store`` writes to the real storage
fixture, the coordinator is the real one, and the advertisement that must not
re-attach a detached transport is fed to the real bluetooth manager.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pymammotion.transport.base import TransportType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import (
    _async_bring_up_mower,
    _create_ble_only_device,
    _register_ble_reconnect_callback,
)
from custom_components.mammotion.config import (
    TRANSPORT_BLUETOOTH,
    TRANSPORT_CLOUD,
    MammotionConfigStore,
    async_get_store,
)
from custom_components.mammotion.const import CONF_BLE_DEVICES, DOMAIN
from custom_components.mammotion.coordinator import MammotionReportUpdateCoordinator
from custom_components.mammotion.models import MammotionMowerData
from tests_ha.ble_advertisements import inject_advertisement

_MOWER = "Luba-VS123456"
_MOWER_MAC = "AA:BB:CC:DD:EE:FF"
_ENTRY_ID = "entry-1"
_STORE_KEY = f"{DOMAIN}.{_ENTRY_ID}"


def _stored(data: dict[str, Any], *, minor_version: int) -> dict[str, Any]:
    """Return what Home Assistant would have written to disk for this store."""
    return {
        "version": 1,
        "minor_version": minor_version,
        "key": _STORE_KEY,
        "data": data,
    }


async def _loaded_store(hass: HomeAssistant) -> MammotionConfigStore:
    """Return a store that has read whatever is currently on disk."""
    store = MammotionConfigStore(hass, _ENTRY_ID)
    await store.async_load_device_data()
    return store


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add the BLE-only entry the store and coordinators belong to."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=_ENTRY_ID,
        unique_id=_MOWER_MAC.lower(),
        data={CONF_BLE_DEVICES: {_MOWER: _MOWER_MAC.lower()}},
    )
    entry.add_to_hass(hass)
    return entry


def _manager() -> MagicMock:
    """Return a MammotionClient stand-in with one reachable device handle."""
    handle = MagicMock()
    handle.device_name = _MOWER
    handle.remove_transport = AsyncMock()
    handle.disconnect_transport = AsyncMock()
    handle.restart_keep_alive = AsyncMock()
    manager = MagicMock()
    manager.mower = MagicMock(return_value=handle)
    manager.set_cloud_attached = AsyncMock()
    manager.add_ble_to_device = AsyncMock()
    return manager


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry, manager: MagicMock
) -> MammotionReportUpdateCoordinator:
    """Build the real report coordinator, which reads the switches in its constructor."""
    return MammotionReportUpdateCoordinator(
        hass, entry, _create_ble_only_device(_MOWER), manager, unique_name=_MOWER
    )


async def test_flat_legacy_store_is_migrated_into_sections(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Stores written before the switches existed hold the device map at the top level."""
    hass_storage[_STORE_KEY] = _stored({_MOWER: {"name": _MOWER}}, minor_version=1)

    store = await _loaded_store(hass)

    assert store.device_data == {_MOWER: {"name": _MOWER}}
    assert store.transport_settings == {}
    assert store.transport_enabled(_MOWER, TRANSPORT_BLUETOOTH) is True


async def test_transport_switch_is_saved_immediately_and_read_back(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Toggles are rare and must survive a restart, so they skip the delayed write."""
    store = await _loaded_store(hass)

    await store.async_set_transport_enabled(_MOWER, TRANSPORT_BLUETOOTH, False)

    assert hass_storage[_STORE_KEY]["data"] == {
        "devices": {},
        "transports": {_MOWER: {"bluetooth_enabled": False}},
        "firmware_checks": {},
        "capabilities": {},
    }
    assert store.transport_enabled(_MOWER, TRANSPORT_BLUETOOTH) is False
    assert store.transport_enabled(_MOWER, TRANSPORT_CLOUD) is True

    reloaded = await _loaded_store(hass)
    assert reloaded.transport_enabled(_MOWER, TRANSPORT_BLUETOOTH) is False


async def test_removing_a_device_drops_its_transport_settings(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A device removed from the account must not leave its switches behind."""
    hass_storage[_STORE_KEY] = _stored(
        {"devices": {}, "transports": {_MOWER: {"cloud_enabled": False}}},
        minor_version=2,
    )
    store = await _loaded_store(hass)

    await store.async_remove_device(_MOWER)

    assert store.transport_settings == {}
    assert hass_storage[_STORE_KEY]["data"] == {
        "devices": {},
        "transports": {},
        "firmware_checks": {},
        "capabilities": {},
    }


async def test_the_coordinator_reads_its_switches_from_the_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Entities must show the stored position the moment they are created."""
    hass_storage[_STORE_KEY] = _stored(
        {
            "devices": {},
            "transports": {
                _MOWER: {"bluetooth_enabled": False, "cloud_enabled": False}
            },
        },
        minor_version=2,
    )
    entry = _entry(hass)
    await async_get_store(hass, entry).async_load_device_data()

    coordinator = _coordinator(hass, entry, _manager())

    assert coordinator.bluetooth_enabled is False
    assert coordinator.cloud_enabled is False


async def test_flipping_a_switch_persists_it(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The switch position is the user's, so it has to outlive the process."""
    entry = _entry(hass)
    await async_get_store(hass, entry).async_load_device_data()
    coordinator = _coordinator(hass, entry, _manager())

    await coordinator.async_set_bluetooth_enabled(False)
    await coordinator.async_set_cloud_enabled(False)

    assert hass_storage[_STORE_KEY]["data"]["transports"] == {
        _MOWER: {"bluetooth_enabled": False, "cloud_enabled": False}
    }
    assert (await _loaded_store(hass)).transport_enabled(
        _MOWER, TRANSPORT_BLUETOOTH
    ) is False


async def test_switching_bluetooth_off_detaches_the_transport(
    hass: HomeAssistant,
) -> None:
    """A merely disconnected BLE transport is still selectable and gets reconnected."""
    entry = _entry(hass)
    await async_get_store(hass, entry).async_load_device_data()
    manager = _manager()
    handle = manager.mower(_MOWER)
    coordinator = _coordinator(hass, entry, manager)

    await coordinator.async_set_bluetooth_enabled(False)

    handle.remove_transport.assert_awaited_once_with(TransportType.BLE)
    handle.disconnect_transport.assert_not_awaited()
    handle.set_prefer_ble.assert_called_once_with(value=False)


async def test_setup_applies_restored_switches_before_connecting(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The switches are applied in the per-mower bring-up, ahead of its first connect."""
    hass_storage[_STORE_KEY] = _stored(
        {
            "devices": {},
            "transports": {
                _MOWER: {"bluetooth_enabled": False, "cloud_enabled": False}
            },
        },
        minor_version=2,
    )
    entry = _entry(hass)
    await async_get_store(hass, entry).async_load_device_data()
    manager = _manager()
    handle = manager.mower(_MOWER)
    order: list[str] = []
    manager.set_prefer_ble.side_effect = lambda *_a, **_k: order.append("prefer_ble")
    handle.remove_transport.side_effect = lambda *_a: order.append("detach_ble")
    manager.set_cloud_attached.side_effect = lambda *_a, **_k: order.append(
        "detach_cloud"
    )

    mower = MammotionMowerData(
        name=_MOWER,
        unique_name=_MOWER,
        device=_create_ble_only_device(_MOWER),
        api=manager,
        maintenance_coordinator=MagicMock(async_bring_up=AsyncMock()),
        reporting_coordinator=_coordinator(hass, entry, manager),
        version_coordinator=MagicMock(async_bring_up=AsyncMock()),
        map_coordinator=MagicMock(
            async_bring_up=AsyncMock(), async_request_refresh=AsyncMock()
        ),
        error_coordinator=MagicMock(async_bring_up=AsyncMock()),
        notifier=MagicMock(),
    )

    async def _connect(*_args: Any, **_kwargs: Any) -> bool:
        order.append("connect")
        return False

    with patch("custom_components.mammotion._await_device_connection", _connect):
        await _async_bring_up_mower(manager, mower, use_wifi=True, prefer_ble=True)

    assert order == ["prefer_ble", "detach_cloud", "detach_ble", "connect"]
    manager.set_prefer_ble.assert_called_once_with(_MOWER, prefer_ble=False)
    handle.remove_transport.assert_awaited_once_with(TransportType.BLE)


@pytest.mark.usefixtures("enable_bluetooth")
async def test_advertisements_do_not_reattach_a_detached_transport(
    hass: HomeAssistant,
) -> None:
    """``add_ble_to_device`` would re-create the very transport the switch detached."""
    entry = _entry(hass)
    store = async_get_store(hass, entry)
    await store.async_load_device_data()
    await store.async_set_transport_enabled(_MOWER, TRANSPORT_BLUETOOTH, False)
    manager = _manager()
    _register_ble_reconnect_callback(hass, entry, manager, _MOWER, _MOWER_MAC)

    inject_advertisement(hass, _MOWER, _MOWER_MAC)
    await hass.async_block_till_done()

    manager.add_ble_to_device.assert_not_awaited()

    await store.async_set_transport_enabled(_MOWER, TRANSPORT_BLUETOOTH, True)
    inject_advertisement(hass, _MOWER, _MOWER_MAC, rssi=-55)
    await hass.async_block_till_done()

    manager.add_ble_to_device.assert_awaited_once()
    assert manager.add_ble_to_device.await_args.kwargs["rssi"] == -55
