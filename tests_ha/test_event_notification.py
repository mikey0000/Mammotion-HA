"""The notification event entity, against the real EventEntity.

The stubbed version hand-rolled an ``EventEntity`` stand-in because
``homeassistant.components.event`` was faked, so it asserted on a list the
fake appended to.  Here the real base class runs: ``_trigger_event`` validates
the type against ``event_types`` and the result is read back off the entity's
own ``state_attributes``, and the bus event is captured
from the real bus.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.mammotion.event import (
    MammotionNotificationEventEntity,
    notification_attributes,
)

_DESCRIBED = {
    "module": "nav",
    "level": "warning",
    "message": "Lost RTK",
    "solution": "Move",
    "text": "nav: Lost RTK, Move",
}


def _coordinator(notify: list[str] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.device_name = "Luba-1"
    coordinator.config_entry.options = {
        "notify": notify if notify is not None else ["warnings"]
    }
    coordinator.describe_error_code.side_effect = lambda code: (
        dict(_DESCRIBED) if code == 2801 else None
    )
    return coordinator


async def _added_entity(
    hass: HomeAssistant, notify: list[str] | None = None
) -> tuple[Any, Any]:
    """Build an entity attached to hass and return it with its subscribed handler."""
    coordinator = _coordinator(notify)
    entity = MammotionNotificationEventEntity(coordinator)
    entity.coordinator = coordinator
    entity.hass = hass
    entity.entity_id = "event.luba_1_notification"
    entity.async_write_ha_state = MagicMock()
    await entity.async_added_to_hass()
    return entity, coordinator.subscribe_notification.call_args.args[0]


def _warning(code: int = -2801) -> SimpleNamespace:
    return SimpleNamespace(
        identifier="device_warning_code_event",
        value={"data": f'[{{"c":{code},"ct":1,"ft":0}}]'},
    )


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Intercept the real persistent_notification helpers the platform calls."""
    create, dismiss = MagicMock(), MagicMock()
    monkeypatch.setattr(persistent_notification, "async_create", create)
    monkeypatch.setattr(persistent_notification, "async_dismiss", dismiss)
    return SimpleNamespace(create=create, dismiss=dismiss)


async def test_warning_code_notification_fires_with_decoded_payload(
    hass: HomeAssistant,
) -> None:
    """A known warning code reaches the event entity with its payload decoded."""
    entity, handler = await _added_entity(hass)
    fired = async_capture_events(hass, "mammotion_notification")

    await handler(
        SimpleNamespace(
            identifier="device_warning_code_event",
            value={"data": '[{"c":-2801,"ct":1,"ft":1775843537000}]'},
        )
    )
    await hass.async_block_till_done()

    attributes = entity.state_attributes
    assert attributes["event_type"] == "device_warning_code_event"
    assert attributes["data"] == [{"c": -2801, "ct": 1, "ft": 1775843537000}]
    assert attributes["codes"] == [
        {
            "code": 2801,
            "count": 1,
            "time": "2026-04-10T17:52:17+00:00",
            **_DESCRIBED,
        }
    ]
    entity.async_write_ha_state.assert_called_once()
    assert len(fired) == 1
    payload = fired[0].data
    assert payload["entity_id"] == "event.luba_1_notification"
    assert payload["device_name"] == "Luba-1"
    assert payload["type"] == "device_warning_code_event"
    assert payload["codes"] == attributes["codes"]


async def test_unknown_identifier_is_ignored(hass: HomeAssistant) -> None:
    """A notification for another device does not fire this entity."""
    entity, handler = await _added_entity(hass)
    fired = async_capture_events(hass, "mammotion_notification")

    await handler(SimpleNamespace(identifier="device_protobuf_msg_event", value=None))
    await hass.async_block_till_done()

    assert entity.state_attributes["event_type"] is None
    entity.async_write_ha_state.assert_not_called()
    assert fired == []


async def test_unsubscribe_is_registered_for_removal(hass: HomeAssistant) -> None:
    """The subscription must be torn down with the entity, not leaked."""
    coordinator = _coordinator()
    entity = MammotionNotificationEventEntity(coordinator)
    entity.coordinator = coordinator
    entity.hass = hass

    await entity.async_added_to_hass()

    assert coordinator.subscribe_notification.return_value in entity._on_remove


def test_attributes_keep_undecodable_data_as_is() -> None:
    """A payload that will not decode is surfaced verbatim rather than dropped."""
    assert notification_attributes({"data": "not json", "x": 1}) == {
        "data": "not json",
        "x": 1,
    }
    assert notification_attributes(None) == {}


def test_notification_code_is_described_when_known() -> None:
    """A known code carries its human description."""

    def describe(code: int) -> dict[str, str] | None:
        return {"message": "Blade stuck"} if code == 1002 else None

    attributes = notification_attributes(
        {"data": '{"localTime":1725159492000,"code":"1002"}'}, describe
    )

    assert attributes["data"] == {"localTime": 1725159492000, "code": "1002"}
    assert attributes["codes"] == [
        {"code": 1002, "time": "2024-09-01T02:58:12+00:00", "message": "Blade stuck"}
    ]


def test_notification_local_time_in_seconds_is_decoded() -> None:
    """Some firmware sends localTime in seconds (1788929118), others milliseconds."""
    described = {
        "module": "navigation",
        "message": "Recharge has failed",
        "solution": "Clear the route",
        "text": "navigation: Recharge has failed, Clear the route",
    }

    attributes = notification_attributes(
        {"data": '{"localTime":1788929118,"code":"1203"}'}, lambda code: described
    )

    assert attributes["codes"] == [
        {"code": 1203, "time": "2026-09-09T04:45:18+00:00", **described}
    ]


def test_unknown_code_is_listed_without_description() -> None:
    """An unknown code still reaches the user, minus the description."""
    attributes = notification_attributes(
        {"data": '[{"c":-9999,"ct":2,"ft":0}]'}, lambda code: None
    )

    assert attributes["codes"] == [
        {"code": 9999, "count": 2, "time": "1970-01-01T00:00:00+00:00"}
    ]


def test_warning_frame_time_is_always_milliseconds() -> None:
    """``ft`` is milliseconds; a small value (unsynced clock) is not seconds."""
    attributes = notification_attributes({"data": '[{"c":-1,"ct":1,"ft":5400000}]'})

    assert attributes["codes"][0]["time"] == "1970-01-01T01:30:00+00:00"


def test_event_platform_is_registered() -> None:
    """The event platform is in the integration's platform list."""
    src = (
        Path(__file__).parent.parent / "custom_components" / "mammotion" / "__init__.py"
    ).read_text()
    assert "Platform.EVENT," in src


async def test_enabled_category_raises_a_persistent_notification(
    hass: HomeAssistant, notifications: Any
) -> None:
    """A category the user enabled produces a persistent notification."""
    entity, handler = await _added_entity(hass)

    await handler(_warning())

    notifications.create.assert_called_once()
    args, kwargs = notifications.create.call_args
    assert args[0] is hass
    assert args[1] == "nav: Lost RTK, Move"
    assert kwargs == {
        "title": "Luba-1: warnings",
        "notification_id": "mammotion_Luba-1_warnings",
    }


async def test_disabled_category_raises_nothing(
    hass: HomeAssistant, notifications: Any
) -> None:
    """A category the user disabled stays silent."""
    entity, handler = await _added_entity(hass, notify=["notifications"])

    await handler(_warning())

    notifications.create.assert_not_called()
    # The event entity still fires; only the persistent notification is gated.
    assert entity.state_attributes["event_type"] == "device_warning_code_event"


async def test_unknown_code_notification_falls_back_to_the_code(
    hass: HomeAssistant, notifications: Any
) -> None:
    """With no description to show, the notification names the code itself."""
    _entity, handler = await _added_entity(hass)

    await handler(_warning(-9999))

    assert notifications.create.call_args.args[1] == "Code 9999"


async def test_warning_notification_is_dismissed_once_errors_clear(
    hass: HomeAssistant, notifications: Any
) -> None:
    """The notification is withdrawn when the device stops reporting the error."""
    entity, handler = await _added_entity(hass)
    await handler(_warning())
    entity.coordinator.data.errors.err_code_list = [2801]

    entity._handle_coordinator_update()
    notifications.dismiss.assert_not_called()

    entity.coordinator.data.errors.err_code_list = []
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()

    notifications.dismiss.assert_called_once_with(hass, "mammotion_Luba-1_warnings")


def test_options_flow_offers_notify_categories() -> None:
    """The options flow exposes every notification category."""
    src = (
        Path(__file__).parent.parent
        / "custom_components"
        / "mammotion"
        / "config_flow.py"
    ).read_text()
    assert "vol.Optional(CONF_NOTIFY, default=self.notify): SelectSelector(" in src
    assert "options=list(NOTIFY_CATEGORIES)" in src
    assert "translation_key=CONF_NOTIFY" in src
