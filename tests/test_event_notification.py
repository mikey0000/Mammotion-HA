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

    def _handle_coordinator_update(self) -> None:
        self.__dict__["coordinator_updates"] = self.__dict__.get("coordinator_updates", 0) + 1


def _stub(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _load_event_platform() -> types.ModuleType:
    if "homeassistant.components.event" not in sys.modules:
        _stub("homeassistant.components.event", EventEntity=_FakeEventEntity)
    if "homeassistant.components.persistent_notification" not in sys.modules:
        _stub("homeassistant.components.persistent_notification", async_create=MagicMock(), async_dismiss=MagicMock())
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
    coordinator.config_entry.options = {"notify": ["warnings"]}
    coordinator.describe_error_code.side_effect = lambda code: (
        {"module": "nav", "level": "warning", "message": "Lost RTK", "solution": "Move"}
        if code == 2801
        else None
    )
    entity = event_module.MammotionNotificationEventEntity(coordinator)
    entity.coordinator = coordinator
    entity.hass = MagicMock()
    entity.entity_id = "event.luba_1_notification"
    entity.triggered = []
    entity.async_write_ha_state = MagicMock()
    _run(entity.async_added_to_hass())
    handler = coordinator.subscribe_notification.call_args.args[0]
    return entity, handler


def test_warning_code_notification_fires_with_decoded_payload(event_module: types.ModuleType) -> None:
    entity, handler = _added_entity(event_module)

    _run(handler(SimpleNamespace(identifier="device_warning_code_event", value={"data": '[{"c":-2801,"ct":1,"ft":1775843537000}]'})))

    assert len(entity.triggered) == 1
    event_type, attributes = entity.triggered[0]
    assert event_type == "device_warning_code_event"
    assert attributes["data"] == [{"c": -2801, "ct": 1, "ft": 1775843537000}]
    assert attributes["codes"] == [
        {
            "code": 2801,
            "count": 1,
            "time": "2026-04-10T17:52:17+00:00",
            "module": "nav",
            "level": "warning",
            "message": "Lost RTK",
            "solution": "Move",
        }
    ]
    entity.async_write_ha_state.assert_called_once()
    entity.hass.bus.async_fire.assert_called_once()
    name, payload = entity.hass.bus.async_fire.call_args.args
    assert name == "mammotion_notification"
    assert payload["entity_id"] == "event.luba_1_notification"
    assert payload["device_name"] == "Luba-1"
    assert payload["type"] == "device_warning_code_event"
    assert payload["codes"] == attributes["codes"]


def test_unknown_identifier_is_ignored(event_module: types.ModuleType) -> None:
    entity, handler = _added_entity(event_module)

    _run(handler(SimpleNamespace(identifier="device_protobuf_msg_event", value=None)))

    assert entity.triggered == []
    entity.async_write_ha_state.assert_not_called()
    entity.hass.bus.async_fire.assert_not_called()


def test_unsubscribe_is_registered_for_removal(event_module: types.ModuleType) -> None:
    coordinator = MagicMock()
    entity = event_module.MammotionNotificationEventEntity(coordinator)
    entity.coordinator = coordinator

    _run(entity.async_added_to_hass())

    assert entity.removers == [coordinator.subscribe_notification.return_value]


def test_attributes_keep_undecodable_data_as_is(event_module: types.ModuleType) -> None:
    assert event_module.notification_attributes({"data": "not json", "x": 1}) == {"data": "not json", "x": 1}
    assert event_module.notification_attributes(None) == {}


def test_notification_code_is_described_when_known(event_module: types.ModuleType) -> None:
    describe = lambda code: {"message": "Blade stuck"} if code == 1002 else None  # noqa: E731

    attributes = event_module.notification_attributes(
        {"data": '{"localTime":1725159492000,"code":"1002"}'}, describe
    )

    assert attributes["data"] == {"localTime": 1725159492000, "code": "1002"}
    assert attributes["codes"] == [{"code": 1002, "time": "2024-09-01T02:58:12+00:00", "message": "Blade stuck"}]


def test_unknown_code_is_listed_without_description(event_module: types.ModuleType) -> None:
    attributes = event_module.notification_attributes({"data": '[{"c":-9999,"ct":2,"ft":0}]'}, lambda code: None)

    assert attributes["codes"] == [{"code": 9999, "count": 2, "time": "1970-01-01T00:00:00+00:00"}]


def test_event_platform_is_registered() -> None:
    src = (Path(__file__).parent.parent / "custom_components" / "mammotion" / "__init__.py").read_text()
    assert "Platform.EVENT," in src


@pytest.fixture
def notifications(event_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> Any:
    create, dismiss = MagicMock(), MagicMock()
    monkeypatch.setattr(event_module.persistent_notification, "async_create", create)
    monkeypatch.setattr(event_module.persistent_notification, "async_dismiss", dismiss)
    return SimpleNamespace(create=create, dismiss=dismiss)


def _warning(code: int = -2801) -> SimpleNamespace:
    return SimpleNamespace(identifier="device_warning_code_event", value={"data": f'[{{"c":{code},"ct":1,"ft":0}}]'})


def test_enabled_category_raises_a_persistent_notification(event_module: types.ModuleType, notifications: Any) -> None:
    entity, handler = _added_entity(event_module)

    _run(handler(_warning()))

    notifications.create.assert_called_once()
    args, kwargs = notifications.create.call_args
    assert args[0] is entity.hass
    assert args[1] == "nav: Lost RTK Move"
    assert kwargs == {"title": "Luba-1: warnings", "notification_id": "mammotion_Luba-1_warnings"}


def test_disabled_category_raises_nothing(event_module: types.ModuleType, notifications: Any) -> None:
    entity, handler = _added_entity(event_module)
    entity.coordinator.config_entry.options = {"notify": ["notifications"]}

    _run(handler(_warning()))

    notifications.create.assert_not_called()
    assert len(entity.triggered) == 1  # the event entity still fires


def test_unknown_code_notification_falls_back_to_the_code(event_module: types.ModuleType, notifications: Any) -> None:
    entity, handler = _added_entity(event_module)

    _run(handler(_warning(-9999)))

    assert notifications.create.call_args.args[1] == "Code 9999"


def test_warning_notification_is_dismissed_once_errors_clear(event_module: types.ModuleType, notifications: Any) -> None:
    entity, handler = _added_entity(event_module)
    _run(handler(_warning()))
    entity.coordinator.data.errors.err_code_list = [2801]

    entity._handle_coordinator_update()
    notifications.dismiss.assert_not_called()

    entity.coordinator.data.errors.err_code_list = []
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()

    notifications.dismiss.assert_called_once_with(entity.hass, "mammotion_Luba-1_warnings")
    assert entity.coordinator_updates == 3


def test_options_flow_offers_notify_categories() -> None:
    src = (Path(__file__).parent.parent / "custom_components" / "mammotion" / "config_flow.py").read_text()
    assert "vol.Optional(CONF_NOTIFY, default=self.notify): SelectSelector(" in src
    assert "options=list(NOTIFY_CATEGORIES)" in src
    assert "translation_key=CONF_NOTIFY" in src
