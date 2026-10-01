"""The Aliyun account lock held by the Mammotion app surfaces as a repair issue.

pymammotion reports bind_reply 2152 through ``MammotionClient.on_account_in_use_changed``
and keeps retrying on its own; the login is healthy, so no reauth flow starts.  The
user's only cue is the repair, which must follow the lock and go away on unload.
"""

from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pymammotion.client import MammotionClient
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.mammotion as mammotion_init
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"
_ISSUE_ID = f"account_in_use_{_ACCOUNT}"


def _client() -> MagicMock:
    client = create_autospec(MammotionClient, instance=True)
    client.on_account_in_use_changed = None
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


async def test_a_held_account_lock_raises_a_repair(hass: HomeAssistant) -> None:
    """The repair is the user's only cue, and it must not be mistaken for a dead login."""
    client = _client()
    entry = await _setup(hass, client)

    await client.on_account_in_use_changed(_ACCOUNT, True)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, _ISSUE_ID)
    assert issue is not None
    assert issue.translation_key == "account_in_use"
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_placeholders == {"account": _ACCOUNT}
    reauths = [
        f
        for f in hass.config_entries.flow.async_progress()
        if f["context"]["source"] == "reauth"
    ]
    assert reauths == [], "a held lock is not a reauth"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_a_released_account_lock_clears_the_repair(hass: HomeAssistant) -> None:
    """The transport reconnects on its own once the app signs out, so the repair must follow it."""
    client = _client()
    entry = await _setup(hass, client)
    await client.on_account_in_use_changed(_ACCOUNT, True)

    await client.on_account_in_use_changed(_ACCOUNT, False)

    assert ir.async_get(hass).async_get_issue(DOMAIN, _ISSUE_ID) is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_unloading_the_entry_clears_the_repair(hass: HomeAssistant) -> None:
    """An unloaded entry stops hearing the release, so its repair would otherwise linger."""
    client = _client()
    entry = await _setup(hass, client)
    await client.on_account_in_use_changed(_ACCOUNT, True)

    await hass.config_entries.async_unload(entry.entry_id)

    assert ir.async_get(hass).async_get_issue(DOMAIN, _ISSUE_ID) is None


def test_the_login_no_longer_handles_account_in_use() -> None:
    """The lock is reported by the transport after login, so a login branch for it can never run."""
    assert not hasattr(mammotion_init, "AccountInUseError")
