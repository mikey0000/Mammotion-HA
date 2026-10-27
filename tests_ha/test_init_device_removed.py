"""A device the library reports gone from the account leaves Home Assistant.

pymammotion fires ``MammotionClient.on_device_removed`` when the cloud rejects a
device as unbound (Mammotion 50101, Aliyun 29004) and the account's device list
no longer has it, or when a restore's fresh list drops it.
"""

from unittest.mock import MagicMock, patch

import pytest
from device_removed_support import make_removal_client, patched_setup
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion.config import async_get_store
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"
_GONE = "Luba-VS563L6H"
_KEPT = "Luba-VAME9R5S"


async def _setup(hass: HomeAssistant, client: MagicMock) -> MockConfigEntry:
    """Set a cloud entry up with the login, the platforms and the device bring-up held back."""
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
