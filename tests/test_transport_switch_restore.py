"""The Bluetooth and cloud switches persist in the entry store and are applied before the first connect."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "mammotion"


class _FakeStore:
    """Stand-in for homeassistant.helpers.storage.Store with HA's migrate-on-load behaviour."""

    stored: dict[str, Any] | None = None

    def __init__(self, hass: Any, *, version: int, minor_version: int, key: str) -> None:
        self.hass = hass
        self.version = version
        self.minor_version = minor_version
        self.key = key
        self.saved: list[dict[str, Any]] = []
        self.delayed: list[Any] = []

    async def async_load(self) -> dict[str, Any] | None:
        if self.stored is None:
            return None
        if (self.stored["version"], self.stored["minor_version"]) == (self.version, self.minor_version):
            return self.stored["data"]
        return await self._async_migrate_func(  # type: ignore[attr-defined]
            self.stored["version"], self.stored["minor_version"], self.stored["data"]
        )

    async def async_save(self, data: dict[str, Any]) -> None:
        self.saved.append(data)

    def async_delay_save(self, data_func: Any, delay: float) -> None:
        self.delayed.append(data_func)

    async def async_remove(self) -> None:
        return None


def _load_config() -> types.ModuleType:
    """Load the real ``config.py`` under an isolated package so conftest's stub stays put."""
    pkg = "_test_mammotion_cfg_pkg"
    sys.modules[pkg] = types.ModuleType(pkg)
    const = types.ModuleType(f"{pkg}.const")
    const.DOMAIN = "mammotion"
    sys.modules[f"{pkg}.const"] = const
    if "pymammotion.http.model.http" not in sys.modules:
        http_mod = types.ModuleType("pymammotion.http.model.http")
        http_mod.ErrorInfo = object
        sys.modules["pymammotion.http.model.http"] = http_mod

    storage = sys.modules["homeassistant.helpers.storage"]
    original_store = storage.Store
    storage.Store = _FakeStore
    try:
        spec = importlib.util.spec_from_file_location(f"{pkg}.config", _ROOT / "config.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg
        sys.modules[f"{pkg}.config"] = module
        spec.loader.exec_module(module)
    finally:
        storage.Store = original_store
    return module


@pytest.fixture(scope="module")
def config_module() -> types.ModuleType:
    return _load_config()


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def _store(config_module: types.ModuleType, stored: dict[str, Any] | None) -> Any:
    _FakeStore.stored = stored
    store = config_module.MammotionConfigStore(MagicMock(), "entry-1")
    _run(store.async_load_device_data())
    return store


def test_flat_legacy_store_is_migrated_into_sections(config_module: types.ModuleType) -> None:
    store = _store(config_module, {"version": 1, "minor_version": 1, "data": {"Luba-1": {"name": "Luba-1"}}})

    assert store.device_data == {"Luba-1": {"name": "Luba-1"}}
    assert store.transport_settings == {}
    assert store.transport_enabled("Luba-1", config_module.TRANSPORT_BLUETOOTH) is True


def test_transport_switch_is_saved_immediately_and_read_back(config_module: types.ModuleType) -> None:
    store = _store(config_module, None)

    _run(store.async_set_transport_enabled("Luba-1", config_module.TRANSPORT_BLUETOOTH, False))

    assert store.saved == [{"devices": {}, "transports": {"Luba-1": {"bluetooth_enabled": False}}}]
    assert store.transport_enabled("Luba-1", config_module.TRANSPORT_BLUETOOTH) is False
    assert store.transport_enabled("Luba-1", config_module.TRANSPORT_CLOUD) is True

    reloaded = _store(config_module, {"version": 1, "minor_version": 2, "data": store.saved[0]})
    assert reloaded.transport_enabled("Luba-1", config_module.TRANSPORT_BLUETOOTH) is False


def test_removing_a_device_drops_its_transport_settings(config_module: types.ModuleType) -> None:
    store = _store(
        config_module,
        {"version": 1, "minor_version": 2, "data": {"devices": {}, "transports": {"Luba-1": {"cloud_enabled": False}}}},
    )

    _run(store.async_remove_device("Luba-1"))

    assert store.transport_settings == {}
    assert store.saved[-1] == {"devices": {}, "transports": {}}


def test_setup_applies_restored_switches_before_connecting() -> None:
    """The switches are applied in the per-mower bring-up, ahead of its first connect."""
    src = (_ROOT / "__init__.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_async_bring_up_mower")
    body = ast.get_source_segment(src, fn)
    assert "report_coordinator.bluetooth_enabled" in body
    assert "report_coordinator.cloud_enabled" in body
    assert body.index("mammotion.set_prefer_ble(device_name, prefer_ble=use_ble)") < body.index(
        "reachable = await _await_device_connection("
    )
    assert body.index("remove_transport(TransportType.BLE)") < body.index(
        "reachable = await _await_device_connection("
    )


def test_coordinator_reads_switches_from_store_and_persists_changes() -> None:
    src = (_ROOT / "coordinator.py").read_text()
    assert 'self._store.transport_enabled(\n            self.device_name, TRANSPORT_BLUETOOTH' in src
    assert 'self._store.transport_enabled(\n            self.device_name, TRANSPORT_CLOUD' in src
    assert src.count("await self._store.async_set_transport_enabled(") == 2


def test_switching_bluetooth_off_detaches_the_transport() -> None:
    src = (_ROOT / "coordinator.py").read_text()
    tree = ast.parse(src)
    fns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_set_bluetooth_enabled"
    ]
    base = next(ast.get_source_segment(src, f) for f in fns if "remove_transport" in ast.get_source_segment(src, f))
    assert "await handle.remove_transport(TransportType.BLE)" in base
    assert "disconnect_transport(TransportType.BLE)" not in base


def test_advertisements_do_not_reattach_a_detached_transport() -> None:
    src = (_ROOT / "__init__.py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_register_ble_reconnect_callback"
    )
    body = ast.get_source_segment(src, fn)
    assert body.index("transport_enabled(") < body.index("mammotion.add_ble_to_device(")
