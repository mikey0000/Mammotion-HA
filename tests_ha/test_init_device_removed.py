"""A device the library reports gone from the account leaves Home Assistant.

pymammotion fires ``MammotionClient.on_device_removed`` when the cloud rejects a
device as unbound (Mammotion 50101, Aliyun 29004) and the account's device list
no longer has it, or when a restore's fresh list drops it.
"""

from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pymammotion.client import MammotionClient
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.mammotion as mammotion_init
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"
_GONE = "Luba-VS563L6H"
_KEPT = "Luba-VAME9R5S"


def _client() -> MagicMock:
    client = create_autospec(MammotionClient, instance=True)
    client.on_device_removed = None
    client.to_cache.return_value = {}
    return client


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

    async def _attempt_login(*_args: Any, **_kwargs: Any) -> bool:
        return False

    async def _bring_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    with (
        patch.object(mammotion_init, "MammotionClient", return_value=client),
        patch.object(mammotion_init, "PLATFORMS", []),
        patch.object(mammotion_init, "_async_attempt_login", _attempt_login),
        patch.object(mammotion_init, "_async_bring_up_devices", _bring_up),
    ):
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
    client = _client()
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
    client = _client()
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
