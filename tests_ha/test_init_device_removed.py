"""A device the library reports gone from the account leaves Home Assistant.

pymammotion fires ``MammotionClient.on_device_removed`` when the cloud rejects a
device as unbound (Mammotion 50101, Aliyun 29004) and the account's device list
no longer has it, or when a restore's fresh list drops it.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from device_removed_support import make_removal_client, patched_setup
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import async_remove_config_entry_device
from custom_components.mammotion.config import MammotionConfigStore, async_get_store
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"
_GONE = "Luba-VS563L6H"
_KEPT = "Luba-VAME9R5S"


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add a cloud entry whose registry holds both mowers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=_ACCOUNT,
        data={
            CONF_ACCOUNTNAME: _ACCOUNT,
            "password": "hunter2",
            CONF_HAS_CLOUD_ACCOUNT: True,
        },
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    for name in (_GONE, _KEPT):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, name)}
        )
    return entry


async def _setup(hass: HomeAssistant, client: MagicMock) -> MockConfigEntry:
    """Set a cloud entry up with the login, the platforms and the device bring-up held back."""
    entry = _make_entry(hass)
    with patched_setup(client, logged_in=False, platforms=False):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.regression
async def test_a_device_gone_from_the_account_is_removed_and_the_entry_reloaded(
    hass: HomeAssistant,
) -> None:
    """The entry stayed loaded after the registry delete.

    Its coordinators kept polling a device the cloud rejects with 50101.
    """
    client = make_removal_client()
    entry = await _setup(hass, client)
    assert entry.state is ConfigEntryState.LOADED

    with patch.object(
        hass.config_entries, "async_schedule_reload", autospec=True
    ) as reload:
        await client.on_device_removed(_GONE, "VIfnsgIQCmHqn4IXXWkQ000000")

    registry = dr.async_get(hass)
    assert (
        registry.async_get_device_by_identifier((DOMAIN, _GONE), entry.entry_id) is None
    )
    assert (
        registry.async_get_device_by_identifier((DOMAIN, _KEPT), entry.entry_id)
        is not None
    )
    reload.assert_called_once_with(entry.entry_id)
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_device_gone_while_the_entry_is_not_loaded_is_removed_without_a_reload(
    hass: HomeAssistant,
) -> None:
    """A restore prunes during setup; reloading an entry mid-setup or unloaded would loop."""
    client = make_removal_client()
    entry = await _setup(hass, client)
    await hass.config_entries.async_unload(entry.entry_id)

    with patch.object(
        hass.config_entries, "async_schedule_reload", autospec=True
    ) as reload:
        await client.on_device_removed(_GONE, "VIfnsgIQCmHqn4IXXWkQ000000")

    registry = dr.async_get(hass)
    assert (
        registry.async_get_device_by_identifier((DOMAIN, _GONE), entry.entry_id) is None
    )
    reload.assert_not_called()


@pytest.mark.regression
async def test_the_stored_state_goes_even_when_the_ha_device_is_already_gone(
    hass: HomeAssistant,
) -> None:
    """The purge sat behind the registry lookup.

    A device the user had already deleted, or one dropped before it was ever
    registered, kept its stored state for a restore to replay.
    """
    client = make_removal_client()
    entry = await _setup(hass, client)
    store = async_get_store(hass, entry)
    store.async_update_device_data(_GONE, {"name": _GONE})
    registry = dr.async_get(hass)
    gone = registry.async_get_device_by_identifier((DOMAIN, _GONE), entry.entry_id)
    registry.async_remove_device(gone.id)

    with patch.object(hass.config_entries, "async_schedule_reload", autospec=True):
        await client.on_device_removed(_GONE, "VIfnsgIQCmHqn4IXXWkQ000000")

    assert _GONE not in store.device_data
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.regression
async def test_removing_a_gone_device_from_the_device_page_drops_its_stored_state(
    hass: HomeAssistant,
) -> None:
    """Only the cloud-removal callback purged the store.

    A device deleted from its device page kept its state and switch settings for
    a re-added mower to start from.
    """
    client = make_removal_client()
    entry = await _setup(hass, client)
    store = async_get_store(hass, entry)
    store.async_update_device_data(_GONE, {"name": _GONE})
    await store.async_set_transport_enabled(_GONE, "ble", False)
    gone = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, _GONE), entry.entry_id
    )

    assert await async_remove_config_entry_device(hass, entry, gone) is True

    assert _GONE not in store.device_data
    assert store.transport_enabled(_GONE, "ble") is True
    await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.regression
async def test_removing_a_device_of_an_unloaded_entry_drops_its_stored_state(
    hass: HomeAssistant,
) -> None:
    """An unloaded entry's removal returned before touching the store.

    With no store in memory, the removal must read the one on disk and rewrite it.
    """
    client = make_removal_client()
    entry = await _setup(hass, client)
    await async_get_store(hass, entry).async_set_transport_enabled(_GONE, "ble", False)
    await hass.config_entries.async_unload(entry.entry_id)
    gone = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, _GONE), entry.entry_id
    )
    before = MammotionConfigStore(hass, entry.entry_id)
    await before.async_load_device_data()
    assert _GONE in before.transport_settings

    assert await async_remove_config_entry_device(hass, entry, gone) is True

    on_disk = MammotionConfigStore(hass, entry.entry_id)
    await on_disk.async_load_device_data()
    assert _GONE not in on_disk.transport_settings


@pytest.mark.regression
async def test_a_device_removed_during_a_failed_setup_saves_again_on_the_retry(
    hass: HomeAssistant,
) -> None:
    """The store's removed set outlived the failed setup.

    A setup that fails is not unloaded, so its store was reused with the device
    still marked removed, and every save for it was dropped until HA restarted,
    even after the mower was bound to the account again.
    """
    client = make_removal_client()

    async def remove_then_fail(*_: Any, **__: Any) -> bool:
        await client.on_device_removed(_GONE, "VIfnsgIQCmHqn4IXXWkQ000000")
        raise ConfigEntryNotReady

    entry = _make_entry(hass)
    with patched_setup(
        client, logged_in=False, platforms=False, login=remove_then_fail
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY

    with patched_setup(client, logged_in=False, platforms=False):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    store = async_get_store(hass, entry)
    await store.async_set_transport_enabled(_GONE, "ble", False)

    assert store.transport_enabled(_GONE, "ble") is False
    await hass.config_entries.async_unload(entry.entry_id)
