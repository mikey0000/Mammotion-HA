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
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from aiohttp import ClientConnectorError
from aiohttp.client_reqrep import ConnectionKey
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.util import dt as dt_util
from pymammotion.aliyun.exceptions import CloudSetupError
from pymammotion.transport.base import LoginFailedError, ReLoginRequiredError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.mammotion import (
    CLOUD_LOGIN_RETRY_INTERVAL,
    _async_attempt_login,
    _CloudLogin,
)
from custom_components.mammotion.const import (
    CONF_ACCOUNTNAME,
    CONF_AEP_DATA,
    CONF_HAS_CLOUD_ACCOUNT,
)
from cloud_login_support import (
    ACCOUNT,
    CACHED_SESSION,
    login_issue,
    make_cloud_client,
    make_cloud_entry,
    reauth_flows,
)


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
    login = _CloudLogin(hass, entry, client, ACCOUNT, "password")
    logins.append(login)
    return await _async_attempt_login(login, ble_fallback=ble_fallback)


def _issue_key(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    return (
        None if (issue := login_issue(hass, entry)) is None else issue.translation_key
    )


def _dead_login_client() -> MagicMock:
    """Return a client whose restore finds the login dead (``reauth_required`` set)."""
    client = make_cloud_client(ReLoginRequiredError(ACCOUNT, "refresh token rejected"))
    client.reauth_required = "refresh token rejected"
    return client


async def test_a_dead_login_with_ble_continues_ble_only_and_asks_for_reauthentication(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """BLE mowers keep working while the user re-authenticates."""
    entry = make_cloud_entry(hass)
    client = _dead_login_client()

    assert await _attempt(hass, entry, client, logins) is False
    await hass.async_block_till_done()

    assert len(reauth_flows(hass)) == 1
    # A dead refresh token does not become valid by waiting, so it must not be re-spent.
    assert CONF_AEP_DATA not in entry.data
    # The account stays configured: dropping it would strand the cloud devices.
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True
    assert entry.data[CONF_ACCOUNTNAME] == ACCOUNT


@pytest.mark.regression
async def test_a_refused_cached_login_with_ble_asks_for_reauthentication(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """It fell back to BLE silently, leaving the user no prompt to fix the password."""
    entry = make_cloud_entry(hass)
    client = make_cloud_client(LoginFailedError(ACCOUNT, "wrong password"))

    assert await _attempt(hass, entry, client, logins) is False
    await hass.async_block_till_done()

    client.restore_credentials.assert_awaited_once()
    assert len(reauth_flows(hass)) == 1


@pytest.mark.regression
async def test_a_dead_login_is_not_followed_by_a_second_password_login(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """It answered a dead login with a second, password login before the reauth."""
    entry = make_cloud_entry(hass)
    client = _dead_login_client()

    await _attempt(hass, entry, client, logins)

    client.restore_credentials.assert_awaited_once()
    client.login_and_initiate_cloud.assert_not_awaited()


async def test_a_dead_login_without_ble_raises_auth_failed(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """With nothing to fall back on, setup fails into Home Assistant's reauth flow."""
    entry = make_cloud_entry(hass)
    client = _dead_login_client()

    with pytest.raises(ConfigEntryAuthFailed):
        await _attempt(hass, entry, client, logins, ble_fallback=False)


async def test_a_cloud_setup_refusal_continues_ble_only_and_is_not_retried(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """A deterministic refusal does not succeed by waiting."""
    entry = make_cloud_entry(hass)
    client = make_cloud_client(CloudSetupError("openId isEmpty,result_code=100002"))

    assert await _attempt(hass, entry, client, logins) is False

    assert _issue_key(hass, entry) == "cloud_login_failed"
    assert entry.data[CONF_AEP_DATA] == CACHED_SESSION


@pytest.mark.regression
async def test_a_refused_connection_at_boot_continues_ble_only(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """ClientConnectorError raised ConfigEntryNotReady ahead of the BLE fallback."""
    entry = make_cloud_entry(hass)
    key = ConnectionKey("api.example.com", 443, True, True, None, None, None, None)
    client = make_cloud_client(
        ClientConnectorError(key, OSError(111, "Connection refused"))
    )

    assert await _attempt(hass, entry, client, logins) is False

    assert _issue_key(hass, entry) == "cloud_login_retrying"
    assert entry.data[CONF_AEP_DATA] == CACHED_SESSION


@pytest.mark.regression
async def test_a_transport_scoped_auth_error_with_ble_keeps_the_cache_and_skips_the_password(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """Every AuthError wiped the cache, though a given-up transport leaves the login valid."""
    entry = make_cloud_entry(hass)
    client = make_cloud_client(
        ReLoginRequiredError(ACCOUNT, "Aliyun refreshToken rejected (2401)")
    )

    assert await _attempt(hass, entry, client, logins) is False
    await hass.async_block_till_done()

    client.login_and_initiate_cloud.assert_not_awaited()
    assert entry.data[CONF_AEP_DATA] == CACHED_SESSION
    assert reauth_flows(hass) == []


async def test_an_unexpected_fault_with_ble_is_shown_and_never_retried(
    hass: HomeAssistant, logins: list[_CloudLogin]
) -> None:
    """Only a network failure is worth retrying; a fault is left to a reload."""
    entry = make_cloud_entry(hass)
    client = make_cloud_client(ValueError("malformed login payload"))

    assert await _attempt(hass, entry, client, logins) is False
    async_fire_time_changed(
        hass, dt_util.utcnow() + CLOUD_LOGIN_RETRY_INTERVAL + timedelta(seconds=1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _issue_key(hass, entry) == "cloud_login_failed"
    client.restore_credentials.assert_awaited_once()
