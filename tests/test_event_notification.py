"""The notification event entity fires for known identifiers with the JSON payload decoded."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeEventEntity:
    """Minimal EventEntity stand-in that records triggered events.

    conftest's MammotionBaseEntity stub sits first in the MRO and neither calls
    ``super().__init__`` nor forwards ``async_write_ha_state``, so the recording
    attributes are created lazily and writes are counted by the test.
    """

    _attr_event_types: list[str]

    @property
    def event_types(self) -> list[str]:
        return self._attr_event_types

    def _trigger_event(self, event_type: str, event_attributes: dict[str, Any] | None = None) -> None:
        if event_type not in self.event_types:
            raise ValueError(event_type)
        self.__dict__.setdefault("triggered", []).append((event_type, event_attributes or {}))

    def async_on_remove(self, func: Any) -> None:
        self.__dict__.setdefault("removers", []).append(func)

    async def async_added_to_hass(self) -> None:
        return None


def _stub(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _load_event_platform() -> types.ModuleType:
    if "homeassistant.components.event" not in sys.modules:
        _stub("homeassistant.components.event", EventEntity=_FakeEventEntity)
    state_mod = sys.modules.get("pymammotion.state.device_state") or _stub("pymammotion.state.device_state")
    if not hasattr(state_mod, "DeviceNotification"):
        state_mod.DeviceNotification = object
    if "custom_components.mammotion" not in sys.modules:
        _stub("custom_components.mammotion", MammotionConfigEntry=object)
    elif not hasattr(sys.modules["custom_components.mammotion"], "MammotionConfigEntry"):
        sys.modules["custom_components.mammotion"].MammotionConfigEntry = object

    path = Path(__file__).parent.parent / "custom_components" / "mammotion" / "event.py"
    spec = importlib.util.spec_from_file_location("custom_components.mammotion.event", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.mammotion.event"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def event_module() -> types.ModuleType:
    return _load_event_platform()


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def _added_entity(event_module: types.ModuleType) -> tuple[Any, Any]:
    """Return an entity added to hass and the handler it subscribed with."""
    coordinator = MagicMock()
    coordinator.device_name = "Luba-1"
    entity = event_module.MammotionNotificationEventEntity(coordinator)
    entity.coordinator = coordinator
    entity.triggered = []
    entity.async_write_ha_state = MagicMock()
    _run(entity.async_added_to_hass())
    handler = coordinator.subscribe_notification.call_args.args[0]
    return entity, handler


def test_warning_code_notification_fires_with_decoded_payload(event_module: types.ModuleType) -> None:
    entity, handler = _added_entity(event_module)

    _run(handler(SimpleNamespace(identifier="device_warning_code_event", value={"data": '[{"c":-2801,"ct":1}]'})))

    assert entity.triggered == [("device_warning_code_event", {"data": [{"c": -2801, "ct": 1}]})]
    entity.async_write_ha_state.assert_called_once()


def test_unknown_identifier_is_ignored(event_module: types.ModuleType) -> None:
    entity, handler = _added_entity(event_module)

    _run(handler(SimpleNamespace(identifier="device_protobuf_msg_event", value=None)))

    assert entity.triggered == []
    entity.async_write_ha_state.assert_not_called()


def test_unsubscribe_is_registered_for_removal(event_module: types.ModuleType) -> None:
    coordinator = MagicMock()
    entity = event_module.MammotionNotificationEventEntity(coordinator)
    entity.coordinator = coordinator

    _run(entity.async_added_to_hass())

    assert entity.removers == [coordinator.subscribe_notification.return_value]


def test_attributes_keep_undecodable_data_as_is(event_module: types.ModuleType) -> None:
    assert event_module.notification_attributes({"data": "not json", "x": 1}) == {"data": "not json", "x": 1}
    assert event_module.notification_attributes(None) == {}


def test_event_platform_is_registered() -> None:
    src = (Path(__file__).parent.parent / "custom_components" / "mammotion" / "__init__.py").read_text()
    assert "Platform.EVENT," in src
