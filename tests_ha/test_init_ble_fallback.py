"""Regression tests for BLE fallback when Aliyun auth fails during setup.

Bug: ReLoginRequiredError raised in the _async_attempt_login retry block
propagated uncaught, crashing setup even when BLE addresses were available.
This caused HA to retry async_setup_entry repeatedly, burning through 284+
Aliyun token refreshes in 40 min and invalidating the refresh token entirely.

Fix: catch ReLoginRequiredError alongside LoginFailedError in the retry
handler and fall back to BLE-only mode when ble_fallback=True.

The equivalent tests in ``tests/`` ran against a stub whose
``EXPIRED_CREDENTIAL_EXCEPTIONS`` included bare ``Exception``, so they could not
tell the routing apart.  Here the real exception hierarchy decides which branch
runs, and the entry is a real one whose data is read back after the failure.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from pymammotion.transport.base import LoginFailedError, ReLoginRequiredError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mammotion import _async_attempt_login
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


def _client(side_effect: Exception) -> MagicMock:
    """Return a MammotionClient stand-in whose login methods raise *side_effect*."""
    client = MagicMock()
    client.restore_credentials = AsyncMock(side_effect=side_effect)
    client.login_and_initiate_cloud = AsyncMock(side_effect=side_effect)
    return client


async def test_relogin_required_ble_fallback_returns_false(
    hass: HomeAssistant,
) -> None:
    """ReLoginRequiredError + ble_fallback=True must return False, not raise.

    This is the regression test for the loop described in the log:
    Aliyun refreshToken invalid (2401) during init with BLE available
    should silently drop cloud and succeed in BLE-only mode.
    """
    entry = _entry(hass)
    client = _client(ReLoginRequiredError("Aliyun refreshToken rejected", "2401"))

    result = await _async_attempt_login(
        hass, entry, client, _ACCOUNT, "password", ble_fallback=True
    )

    assert result is False
    # A failed login must not permanently downgrade the entry to BLE-only: the
    # cloud account stays configured and is retried on the next setup.
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True
    assert entry.data[CONF_ACCOUNTNAME] == _ACCOUNT


async def test_relogin_required_no_ble_raises_auth_failed(
    hass: HomeAssistant,
) -> None:
    """ReLoginRequiredError with no BLE must raise ConfigEntryAuthFailed."""
    entry = _entry(hass)
    client = _client(ReLoginRequiredError("Aliyun refreshToken rejected", "2401"))

    with pytest.raises(ConfigEntryAuthFailed):
        await _async_attempt_login(
            hass, entry, client, _ACCOUNT, "password", ble_fallback=False
        )


async def test_login_failed_ble_fallback_still_returns_false(
    hass: HomeAssistant,
) -> None:
    """LoginFailedError (wrong Mammotion password) with BLE still falls back."""
    entry = _entry(hass)
    client = _client(LoginFailedError("wrong password", "1001"))

    result = await _async_attempt_login(
        hass, entry, client, _ACCOUNT, "badpass", ble_fallback=True
    )

    assert result is False


async def test_a_rejected_cache_is_cleared_before_the_retry(
    hass: HomeAssistant,
) -> None:
    """A dead refresh token does not become valid by waiting, so it must not be re-spent."""
    entry = _entry(hass, **{CONF_AEP_DATA: {"token": "dead"}})
    client = _client(ReLoginRequiredError("Aliyun refreshToken rejected", "2401"))

    result = await _async_attempt_login(
        hass, entry, client, _ACCOUNT, "password", ble_fallback=True
    )

    assert result is False
    client.restore_credentials.assert_awaited_once()
    client.login_and_initiate_cloud.assert_awaited_once()
    assert CONF_AEP_DATA not in entry.data
    assert entry.data[CONF_HAS_CLOUD_ACCOUNT] is True
