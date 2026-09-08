"""BLE is registered per device before any cloud login, and survives the cloud dying.

Loads the real ``__init__.py`` against the stubs from conftest.py (same technique
as test_init_ble_fallback.py) and exercises the setup helpers directly.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_init_path = (
    Path(__file__).parent.parent / "custom_components" / "mammotion" / "__init__.py"
)
_spec = importlib.util.spec_from_file_location("custom_components.mammotion", _init_path)
_init_mod = importlib.util.module_from_spec(_spec)
_init_mod.__package__ = "custom_components.mammotion"
sys.modules["custom_components.mammotion"] = _init_mod
_spec.loader.exec_module(_init_mod)

_register_ble_devices = _init_mod._register_ble_devices
_await_device_connection = _init_mod._await_device_connection
_transport_base = sys.modules["pymammotion.transport.base"]


def _make_entry(ble_devices: dict[str, str]) -> MagicMock:
    entry = MagicMock()
    entry.data = {"ble_devices": ble_devices}
    return entry


@pytest.mark.anyio
async def test_register_ble_devices_registers_every_configured_mower_by_ble() -> None:
    """Every BLE mower becomes a per-device registration — in range or not — before login."""
    _init_mod.DEVICE_SUPPORT = ("Luba", "Yuka")
    bluetooth = sys.modules["homeassistant.components.bluetooth"]
    in_range = MagicMock(name="ble-device")
    bluetooth.async_ble_device_from_address = MagicMock(
        side_effect=lambda _hass, mac, _c: in_range if mac == "AA:BB:CC:DD:EE:FF" else None
    )
    bluetooth.async_register_callback = MagicMock(return_value=lambda: None)

    mammotion = MagicMock()
    mammotion.add_ble_only_device = AsyncMock()
    mowing_device = MagicMock()
    mammotion.get_device_by_name = MagicMock(return_value=mowing_device)
    entry = _make_entry({"Luba-1": "aa:bb:cc:dd:ee:ff", "Yuka-2": "11:22:33:44:55:66", "RTK-3": "de:ad:be:ef:00:00"})

    registered = await _register_ble_devices(MagicMock(), entry, mammotion)

    assert registered == {"Luba-1": "aa:bb:cc:dd:ee:ff", "Yuka-2": "11:22:33:44:55:66"}
    calls = {c.kwargs["device_name"]: c.kwargs for c in mammotion.add_ble_only_device.await_args_list}
    assert calls["Luba-1"]["ble_device"] is in_range and calls["Luba-1"]["ble_address"] is None
    assert calls["Yuka-2"]["ble_device"] is None and calls["Yuka-2"]["ble_address"] == "11:22:33:44:55:66"
    assert "RTK-3" not in calls  # RTK base stations are cloud-discovered only
    assert mowing_device.mower_state.ble_mac == "11:22:33:44:55:66"
    assert bluetooth.async_register_callback.call_count == 2  # reconnect callback for every BLE mower


@pytest.mark.anyio
async def test_await_device_connection_skips_waiting_without_a_usable_transport() -> None:
    """An out-of-range BLE-only mower must not block setup (nor raise ConfigEntryNotReady later)."""
    handle = MagicMock()
    handle.has_usable_transport = False
    handle.wait_until_connected = AsyncMock()
    mammotion = MagicMock()
    mammotion.mower = MagicMock(return_value=handle)

    assert await _await_device_connection(mammotion, "Luba-1", prefer_ble=True) is False
    handle.wait_until_connected.assert_not_awaited()

    handle.has_usable_transport = True
    handle.get_transport = MagicMock(return_value=None)
    assert await _await_device_connection(mammotion, "Luba-1", prefer_ble=True) is True
    handle.wait_until_connected.assert_awaited_once()


def test_setup_registers_ble_before_the_cloud_login() -> None:
    """Structural check: BLE registration precedes the login attempt in async_setup_entry."""
    source = _init_path.read_text()
    body = source[source.index("async def async_setup_entry(") :]
    assert body.index("await _register_ble_devices(") < body.index("await _async_attempt_login(")
    # A failed login no longer rewrites the entry as account-less.
    assert "CONF_HAS_CLOUD_ACCOUNT: False" not in source
    # The unrecoverable-auth callback keeps BLE mowers working.
    callback_body = body[body.index("async def _on_unrecoverable_auth_error(") : body.index("mammotion.on_unrecoverable_auth_error =")]
    assert "connect_ble" in callback_body
    assert "set_prefer_ble(value=True)" in callback_body
