"""A mower without a cloud ``iot_id`` (BLE-only, no account) gets no camera entity."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _stub(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _load_camera() -> types.ModuleType:
    """Load the real ``camera.py`` by path, stubbing the imports conftest does not."""
    if "websockets" not in sys.modules:
        _stub("websockets")
    if "homeassistant.helpers.entity_platform" not in sys.modules:
        _stub("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    if "custom_components.mammotion" not in sys.modules:
        _stub("custom_components.mammotion", MammotionConfigEntry=object)
    elif not hasattr(sys.modules["custom_components.mammotion"], "MammotionConfigEntry"):
        sys.modules["custom_components.mammotion"].MammotionConfigEntry = object
    if "custom_components.mammotion.agora_api" not in sys.modules:
        _stub("custom_components.mammotion.agora_api", AgoraResponse=MagicMock())
    if "custom_components.mammotion.agora_websocket" not in sys.modules:
        _stub(
            "custom_components.mammotion.agora_websocket",
            AgoraWebSocketHandler=MagicMock(),
        )

    path = Path(__file__).parent.parent / "custom_components" / "mammotion" / "camera.py"
    spec = importlib.util.spec_from_file_location("custom_components.mammotion.camera", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.mammotion.camera"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def camera_module() -> types.ModuleType:
    return _load_camera()


@pytest.fixture
def platform(camera_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Camera module with the entity class and service registration replaced by mocks."""
    monkeypatch.setattr(camera_module, "MammotionWebRTCCamera", MagicMock())
    monkeypatch.setattr(camera_module, "async_setup_platform_services", AsyncMock())
    monkeypatch.setattr(camera_module.DeviceType, "is_luba1", MagicMock(return_value=False))
    return camera_module


def _mower(name: str, iot_id: str) -> MagicMock:
    mower = MagicMock()
    mower.device.device_name = name
    mower.device.iot_id = iot_id
    mower.reporting_coordinator.async_check_stream_expiry = AsyncMock(return_value=(None, None))
    return mower


def _entry(*mowers: MagicMock) -> MagicMock:
    entry = MagicMock()
    entry.runtime_data.mowers = list(mowers)
    return entry


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def test_ble_only_entry_creates_no_camera(platform: types.ModuleType) -> None:
    add_entities = MagicMock()
    ble_mower = _mower("Luba-BLE", "")

    _run(platform.async_setup_entry(MagicMock(), _entry(ble_mower), add_entities))

    add_entities.assert_not_called()
    platform.async_setup_platform_services.assert_not_awaited()
    ble_mower.reporting_coordinator.async_check_stream_expiry.assert_not_awaited()


def test_only_cloud_mowers_get_a_camera(platform: types.ModuleType) -> None:
    add_entities = MagicMock()
    ble_mower = _mower("Luba-BLE", "")
    cloud_mower = _mower("Luba-Cloud", "iot-123")

    _run(platform.async_setup_entry(MagicMock(), _entry(ble_mower, cloud_mower), add_entities))

    add_entities.assert_called_once()
    assert len(add_entities.call_args.args[0]) == 1
    platform.MammotionWebRTCCamera.assert_called_once()
    assert platform.MammotionWebRTCCamera.call_args.args[0] is cloud_mower.reporting_coordinator
    platform.async_setup_platform_services.assert_awaited_once()
