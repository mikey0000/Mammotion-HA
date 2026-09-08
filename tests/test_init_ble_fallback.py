"""Regression tests for BLE fallback when Aliyun auth fails during setup.

Bug: ReLoginRequiredError raised in the _async_attempt_login retry block
propagated uncaught, crashing setup even when BLE addresses were available.
This caused HA to retry async_setup_entry repeatedly, burning through 284+
Aliyun token refreshes in 40 min and invalidating the refresh token entirely.

Fix: catch ReLoginRequiredError alongside LoginFailedError in the retry
handler and fall back to BLE-only mode when ble_fallback=True.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── load the real __init__.py under test ─────────────────────────────────
# Register under the real package name so relative imports (.const, .coordinator
# etc.) resolve to the stubs already in sys.modules from conftest.py.
_init_path = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "__init__.py"
)
_spec = importlib.util.spec_from_file_location("custom_components.mammotion", _init_path)
_init_mod = importlib.util.module_from_spec(_spec)
_init_mod.__package__ = "custom_components.mammotion"
sys.modules["custom_components.mammotion"] = _init_mod
_spec.loader.exec_module(_init_mod)

_async_attempt_login = _init_mod._async_attempt_login

# Pull the stub exception classes from sys.modules so isinstance checks match.
_transport_base = sys.modules["pymammotion.transport.base"]
_ReLoginRequiredError = _transport_base.ReLoginRequiredError
_LoginFailedError = _transport_base.LoginFailedError
_ha_exceptions = sys.modules["homeassistant.exceptions"]
_ConfigEntryAuthFailed = _ha_exceptions.ConfigEntryAuthFailed
_const = sys.modules["custom_components.mammotion.const"]
CONF_HAS_CLOUD_ACCOUNT = _const.CONF_HAS_CLOUD_ACCOUNT


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    return hass


def _make_entry(ble_devices: dict | None = None) -> MagicMock:
    entry = MagicMock()
    entry.data = {}
    if ble_devices:
        entry.data["ble_devices"] = ble_devices
    return entry


def _make_mammotion(side_effect: Exception) -> MagicMock:
    """Return a mock MammotionClient whose login methods raise side_effect."""
    m = MagicMock()
    m.restore_credentials = AsyncMock(side_effect=side_effect)
    m.login_and_initiate_cloud = AsyncMock(side_effect=side_effect)
    return m


# ── tests ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_relogin_required_ble_fallback_returns_false() -> None:
    """ReLoginRequiredError + ble_fallback=True must return False, not raise.

    This is the regression test for the loop described in the log:
    Aliyun refreshToken invalid (2401) during init with BLE available
    should silently drop cloud and succeed in BLE-only mode.
    """
    hass = _make_hass()
    entry = _make_entry()
    mammotion = _make_mammotion(_ReLoginRequiredError("Aliyun refreshToken rejected"))

    result = await _async_attempt_login(
        hass, entry, mammotion, "user@test.com", "password", ble_fallback=True
    )

    assert result is False
    # A failed login must not permanently downgrade the entry to BLE-only: the
    # cloud account stays configured and is retried on the next setup.
    for call in hass.config_entries.async_update_entry.call_args_list:
        assert call[1]["data"].get(CONF_HAS_CLOUD_ACCOUNT, True) is not False


@pytest.mark.anyio
async def test_relogin_required_no_ble_raises_auth_failed() -> None:
    """ReLoginRequiredError with no BLE must raise ConfigEntryAuthFailed."""
    hass = _make_hass()
    entry = _make_entry()
    mammotion = _make_mammotion(_ReLoginRequiredError("Aliyun refreshToken rejected"))

    with pytest.raises(_ConfigEntryAuthFailed):
        await _async_attempt_login(
            hass, entry, mammotion, "user@test.com", "password", ble_fallback=False
        )


@pytest.mark.anyio
async def test_login_failed_ble_fallback_still_returns_false() -> None:
    """LoginFailedError (wrong Mammotion password) with BLE still falls back."""
    hass = _make_hass()
    entry = _make_entry()
    mammotion = _make_mammotion(_LoginFailedError("wrong password"))

    result = await _async_attempt_login(
        hass, entry, mammotion, "user@test.com", "badpass", ble_fallback=True
    )

    assert result is False
