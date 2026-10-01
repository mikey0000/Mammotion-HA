"""A cloud login that fails at setup leaves BLE mowers working.

A setup that raised on an auth failure despite configured BLE mowers was retried
by Home Assistant over and over, burning through 284+ Aliyun token refreshes in
40 minutes and invalidating the refresh token.  With a BLE fallback the entry loads
BLE-only; a dead login additionally asks for re-authentication, and nothing here
ever answers a rejection with a second password login.

The real exception hierarchy decides which branch runs, and the entry is a real one
whose data is read back after the failure.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest
from aiohttp import ClientConnectorError
from aiohttp.client_reqrep import ConnectionKey
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from pymammotion.aliyun.exceptions import CloudSetupError
from pymammotion.client import MammotionClient
from pymammotion.transport.base import LoginFailedError, ReLoginRequiredError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _async_attempt_login, _CloudLogin
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
    DOMAIN,
)

_ACCOUNT = "owner@example.com"


def _entry(hass: HomeAssistant, **data: object) -> MockConfigEntry:
    """Add a cloud entry that also has a BLE mower configured."""
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
    return entry


def _client(side_effect: Exception, *, reauth_required: str | None = None) -> MagicMock:
    """Return a client whose login methods raise *side_effect*."""
    client = create_autospec(MammotionClient, instance=True)
    client.restore_credentials.side_effect = side_effect
    client.login_and_initiate_cloud.side_effect = side_effect
    client.reauth_required = reauth_required
    return client


@pytest.fixture
def logins() -> Iterator[list[_CloudLogin]]:
    """Cancel every retry an attempt scheduled, so no timer outlives the test."""
    made: list[_CloudLogin] = []
    yield made
    for login in made:
        login.async_cancel()


async def _attempt(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    client: MagicMock,
    logins: list[_CloudLogin],
    *,
    ble_fallback: bool = True,
) -> bool:
    login = _CloudLogin(hass, entry, client, _ACCOUNT, "password")
    logins.append(login)
    return await _async_attempt_login(login, ble_fallback=ble_fallback)


def _reauth_flows(hass: HomeAssistant) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == "reauth"
    ]


def _issue_key(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"cloud_login_{entry.entry_id}")
    return None if issue is None else issue.translation_key


def _dead_login() -> ReLoginRequiredError:
    return ReLoginRequiredError(_ACCOUNT, "refresh token rejected")


async def test_a_dead_login_with_ble_continues_ble_only_and_asks_for_reauthentication(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """BLE mowers keep working while the user re-authenticates."""
    entry = _entry(hass, **{CONF_AEP_DATA: {"token": "dead"}})
    client = _client(_dead_login(), reauth_required="refresh token rejected")

    assert await _attempt(hass, entry, client, logins) is False
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1
    # A dead refresh token does not become valid by waiting, so it must not be re-spent.
    assert CONF_AEP_DATA not in entry.data
    # The account stays configured: dropping it would strand the cloud devices.
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True
    assert entry.data[CONF_ACCOUNTNAME] == _ACCOUNT


@pytest.mark.regression
async def test_a_rejected_password_with_ble_asks_for_reauthentication(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """It fell back to BLE silently, leaving the user no prompt to fix the password."""
    entry = _entry(hass)
    client = _client(LoginFailedError(_ACCOUNT, "wrong password"))

    assert await _attempt(hass, entry, client, logins) is False
    await hass.async_block_till_done()

    assert len(_reauth_flows(hass)) == 1


@pytest.mark.regression
async def test_a_dead_login_is_not_followed_by_a_second_password_login(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """``restore_credentials`` already made the one sanctioned fallback login."""
    entry = _entry(hass, **{CONF_AEP_DATA: {"token": "dead"}})
    client = _client(_dead_login(), reauth_required="refresh token rejected")

    await _attempt(hass, entry, client, logins)

    client.restore_credentials.assert_awaited_once()
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_a_dead_login_without_ble_raises_auth_failed(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """With nothing to fall back on, setup fails into Home Assistant's reauth flow."""
    entry = _entry(hass)
    client = _client(_dead_login(), reauth_required="refresh token rejected")

    with pytest.raises(ConfigEntryAuthFailed):
        await _attempt(hass, entry, client, logins, ble_fallback=False)


@pytest.mark.regression
@pytest.mark.parametrize("ble", [False, True], ids=["cloud_only", "ble"])
async def test_a_cloud_setup_failure_loads_and_retries_from_the_cache(
    hass: HomeAssistant, logins: list[_CloudLogin], ble: bool
) -> None:
    """Without BLE it raised ConfigEntryNotReady, so HA retried setup indefinitely."""
    entry = _entry(hass, **{CONF_AEP_DATA: {"token": "cached"}})
    client = _client(CloudSetupError("Error in getting mqtt credentials: refused"))

    assert await _attempt(hass, entry, client, logins, ble_fallback=ble) is False

    assert _issue_key(hass, entry) == "cloud_login_retrying"
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True


@pytest.mark.regression
async def test_a_refused_connection_at_boot_continues_ble_only(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """ClientConnectorError raised ConfigEntryNotReady ahead of the BLE fallback."""
    entry = _entry(hass, **{CONF_AEP_DATA: {"token": "cached"}})
    key = ConnectionKey("api.example.com", 443, True, True, None, None, None, None)
    client = _client(ClientConnectorError(key, OSError(111, "Connection refused")))

    assert await _attempt(hass, entry, client, logins) is False

    assert _issue_key(hass, entry) == "cloud_login_retrying"
    assert entry.data[CONF_AEP_DATA] == {"token": "cached"}
