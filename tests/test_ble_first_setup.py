"""BLE is registered per device before any cloud login, and survives the cloud dying.

Loads the real ``__init__.py`` against the stubs from conftest.py (same technique
as test_init_ble_fallback.py) and exercises the setup helpers directly.
"""

import asyncio
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
    handle.wait_until_connected = AsyncMock(return_value=True)
    assert await _await_device_connection(mammotion, "Luba-1", prefer_ble=True) is True
    handle.wait_until_connected.assert_awaited_once()

    # The library returns False on its 60 s timeout; that must reach the caller.
    handle.wait_until_connected = AsyncMock(return_value=False)
    assert await _await_device_connection(mammotion, "Luba-1", prefer_ble=True) is False


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


def _setup_body() -> str:
    source = _init_path.read_text()
    start = source.index("async def async_setup_entry(")
    return source[start : source.index("\nasync def ", start + 1)]


def test_setup_blocks_on_login_only_and_defers_devices() -> None:
    """Setup forwards platforms first and hands every device round-trip to a background task."""
    body = _setup_body()
    assert "async_config_entry_first_refresh" not in body
    assert "_await_device_connection(" not in body
    assert body.index("async_forward_entry_setups(") < body.index("entry.async_create_background_task(")
    assert "bring_up_task = entry.async_create_background_task(" in body
    assert "blocked for %.1fs" in body


def test_setup_restores_before_building_the_other_coordinators() -> None:
    """The other coordinators copy the device record in their constructors, so restore must come first."""
    body = _setup_body()
    restore = body.index("await report_coordinator.async_restore_data()")
    for cls in (
        "MammotionMaintenanceUpdateCoordinator(",
        "MammotionDeviceVersionUpdateCoordinator(",
        "MammotionMapUpdateCoordinator(",
        "MammotionDeviceErrorUpdateCoordinator(",
    ):
        assert body.index(cls) > restore, cls
    assert body.index("MammotionReportUpdateCoordinator(") < restore


def test_unload_cancels_the_bring_up_task_before_tearing_handles_down() -> None:
    """Home Assistant cancels entry tasks only after async_unload_entry, so unload must do it itself."""
    source = _init_path.read_text()
    start = source.index("async def async_unload_entry(")
    body = source[start : source.index("\nasync def ", start + 1)]
    assert body.index("bring_up_task") < body.index("async_unload_platforms(")
    assert "task.cancel()" in body


def _mower(name: str) -> MagicMock:
    mower = MagicMock()
    mower.name = name
    for attr in (
        "version_coordinator",
        "reporting_coordinator",
        "maintenance_coordinator",
        "error_coordinator",
        "map_coordinator",
    ):
        getattr(mower, attr).async_bring_up = AsyncMock()
    mower.map_coordinator.async_request_refresh = AsyncMock()
    mower.reporting_coordinator.bluetooth_enabled = True
    mower.reporting_coordinator.cloud_enabled = True
    return mower


def _handle() -> MagicMock:
    handle = MagicMock()
    handle.disconnect_transport = AsyncMock()
    handle.remove_transport = AsyncMock()
    return handle


@pytest.mark.anyio
async def test_bring_up_mower_waits_then_brings_up_every_coordinator_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reachable mower brings up all five coordinators in order and then requests the map."""
    mammotion = MagicMock()
    handle = _handle()
    mammotion.mower = MagicMock(return_value=handle)
    mower = _mower("Luba-1")
    order: list[str] = []
    mower.version_coordinator.async_bring_up.side_effect = lambda: order.append("version")
    mower.reporting_coordinator.async_bring_up.side_effect = lambda: order.append("report")
    mower.map_coordinator.async_bring_up.side_effect = lambda: order.append("map")
    monkeypatch.setattr(_init_mod, "_await_device_connection", AsyncMock(return_value=True))
    monkeypatch.setattr(_init_mod.asyncio, "sleep", AsyncMock())

    await _init_mod._async_bring_up_mower(mammotion, mower, use_wifi=True, prefer_ble=True)

    mammotion.set_prefer_ble.assert_called_once_with("Luba-1", prefer_ble=True)
    handle.remove_transport.assert_not_awaited()
    assert order == ["version", "report", "map"]
    mower.maintenance_coordinator.async_bring_up.assert_awaited_once()
    mower.error_coordinator.async_bring_up.assert_awaited_once()
    mower.map_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.anyio
async def test_bring_up_mower_with_bluetooth_off_detaches_and_skips_map_when_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bluetooth off detaches the transport; an unreachable mower still refreshes but requests no map."""
    mammotion = MagicMock()
    handle = _handle()
    mammotion.mower = MagicMock(return_value=handle)
    mower = _mower("Luba-1")
    mower.reporting_coordinator.bluetooth_enabled = False
    monkeypatch.setattr(_init_mod, "_await_device_connection", AsyncMock(return_value=False))

    await _init_mod._async_bring_up_mower(mammotion, mower, use_wifi=True, prefer_ble=True)

    mammotion.set_prefer_ble.assert_called_once_with("Luba-1", prefer_ble=False)
    handle.remove_transport.assert_awaited_once()
    mower.reporting_coordinator.async_bring_up.assert_awaited_once()
    mower.map_coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.anyio
async def test_bring_up_devices_isolates_one_failing_mower(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mower whose bring-up raises is logged and must not stop the others or propagate."""
    good, bad = _mower("Luba-good"), _mower("Luba-bad")
    rtk = MagicMock()
    rtk.name = "RTK-1"
    rtk.coordinator.async_bring_up = AsyncMock()
    devices = MagicMock()
    devices.mowers = [bad, good]
    devices.RTK = [rtk]
    devices.spino = []
    calls: list[str] = []

    async def fake_bring_up(_mammotion: MagicMock, mower: MagicMock, **_kwargs: bool) -> None:
        calls.append(mower.name)
        if mower is bad:
            raise _transport_base.TransportError("boom")

    monkeypatch.setattr(_init_mod, "_async_bring_up_mower", fake_bring_up)

    await _init_mod._async_bring_up_devices(MagicMock(), devices, use_wifi=True, prefer_ble=True)

    assert sorted(calls) == ["Luba-bad", "Luba-good"]
    rtk.coordinator.async_bring_up.assert_awaited_once()


@pytest.mark.anyio
async def test_guarded_logs_a_foreign_cancellation_but_propagates_our_own() -> None:
    """A cancellation raised inside a child is logged; cancelling the task itself still propagates."""
    async def cancelled_child() -> None:
        raise asyncio.CancelledError

    await _init_mod._async_guarded("Luba-1", cancelled_child())  # foreign: swallowed and logged

    async def slow_child() -> None:
        await asyncio.sleep(10)

    task = asyncio.ensure_future(_init_mod._async_guarded("Luba-1", slow_child()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_devices_of_an_unloaded_entry_can_be_removed() -> None:
    """A disabled entry has no runtime_data; its devices must still be removable from the UI."""
    entry = MagicMock(spec=["state"])
    entry.state = "not_loaded"
    device = MagicMock()
    device.identifiers = {("mammotion", "Luba-1")}

    assert await _init_mod.async_remove_config_entry_device(MagicMock(), entry, device) is True


@pytest.mark.anyio
async def test_devices_still_present_on_a_loaded_entry_are_protected() -> None:
    """Removing a mower the account still lists is refused; an orphaned device is allowed."""
    entry = MagicMock()
    entry.state = "loaded"
    mower = MagicMock()
    mower.unique_name = "Luba-1"
    entry.runtime_data.mowers = [mower]
    device = MagicMock()
    device.identifiers = {("mammotion", "Luba-1")}

    assert await _init_mod.async_remove_config_entry_device(MagicMock(), entry, device) is False
    device.identifiers = {("mammotion", "Luba-gone")}
    assert await _init_mod.async_remove_config_entry_device(MagicMock(), entry, device) is True

