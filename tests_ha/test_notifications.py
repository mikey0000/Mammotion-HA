"""The per-mower notifier: decoding, persistent notifications and the bus events.

It runs from entry setup rather than from the event entity, so every behaviour here
must hold with no entity involved at all.
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.mammotion import async_migrate_entry
from custom_components.mammotion.const import CONF_NOTIFY, DOMAIN
from custom_components.mammotion.notifications import (
    MAX_DATA_BYTES,
    MowerNotifier,
    notification_attributes,
    notification_message,
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
    coordinator.unique_name = "Luba-1"
    coordinator.config_entry.options = {} if notify is None else {CONF_NOTIFY: notify}
    coordinator.describe_error_code.side_effect = lambda code: (
        dict(_DESCRIBED) if code == 2801 else None
    )
    return coordinator


def _started(
    hass: HomeAssistant, notify: list[str] | None = ["warnings"]
) -> tuple[MowerNotifier, Any]:
    """Start a notifier and return it with the handler it subscribed."""
    notifier = MowerNotifier(hass, _coordinator(notify))
    notifier.async_start()
    return notifier, notifier.coordinator.subscribe_notification.call_args.args[0]


def _warning(code: int = -2801, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        identifier="device_warning_code_event",
        value={"data": f'[{{"c":{code},"ct":1,"ft":1775843537000}}]', **extra},
    )


@pytest.fixture
def notifications(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Intercept the real persistent_notification helpers the notifier calls."""
    create, dismiss = MagicMock(), MagicMock()
    monkeypatch.setattr(persistent_notification, "async_create", create)
    monkeypatch.setattr(persistent_notification, "async_dismiss", dismiss)
    return SimpleNamespace(create=create, dismiss=dismiss)


async def test_listeners_receive_the_decoded_and_described_payload(
    hass: HomeAssistant,
) -> None:
    """The entity, and anything else listening, gets the payload decoded and described."""
    notifier, handler = _started(hass)
    received: list[tuple[str, dict[str, Any]]] = []
    notifier.async_add_listener(
        lambda event_type, attrs: received.append((event_type, attrs))
    )

    await handler(_warning())

    assert received == [
        (
            "device_warning_code_event",
            {
                "data": [{"c": -2801, "ct": 1, "ft": 1775843537000}],
                "codes": [
                    {
                        "code": 2801,
                        "count": 1,
                        "time": "2026-04-10T17:52:17+00:00",
                        **_DESCRIBED,
                    }
                ],
            },
        )
    ]


async def test_a_removed_listener_is_not_called(hass: HomeAssistant) -> None:
    """A listener removed with its entity must not keep receiving events."""
    notifier, handler = _started(hass)
    received: list[str] = []
    remove = notifier.async_add_listener(
        lambda event_type, _attrs: received.append(event_type)
    )

    remove()
    await handler(_warning())

    assert received == []


async def test_an_unknown_identifier_publishes_nothing(
    hass: HomeAssistant, notifications: Any
) -> None:
    """A thing/event the integration does not know is dropped before any publishing."""
    _notifier, handler = _started(hass)
    fired = async_capture_events(hass, "mammotion_event")

    await handler(SimpleNamespace(identifier="device_protobuf_msg_event", value=None))
    await hass.async_block_till_done()

    assert fired == []
    notifications.create.assert_not_called()


async def test_the_bus_event_fires_with_the_device_and_type(
    hass: HomeAssistant,
) -> None:
    """Automations get one ``mammotion_event`` per notification, tied to the device."""
    _notifier, handler = _started(hass)
    fired = async_capture_events(hass, "mammotion_event")
    removed = async_capture_events(hass, "mammotion_notification")

    await handler(_warning())
    await hass.async_block_till_done()

    assert len(fired) == 1
    payload = fired[0].data
    assert payload["type"] == "device_warning_code_event"
    assert payload["device_name"] == "Luba-1"
    assert "device_id" in payload
    assert payload["codes"][0]["code"] == 2801
    assert removed == [], "the old event name is gone"


async def test_payload_keys_cannot_overwrite_the_event_keys(
    hass: HomeAssistant,
) -> None:
    """The device payload is merged in first and trimmed, so it cannot spoof the event's own keys."""
    _notifier, handler = _started(hass)
    fired = async_capture_events(hass, "mammotion_event")

    await handler(_warning(type="spoofed", device_name="spoofed", event_type="spoofed"))
    await hass.async_block_till_done()

    payload = fired[0].data
    assert payload["type"] == "device_warning_code_event"
    assert payload["device_name"] == "Luba-1"
    assert "event_type" not in payload, "raw payload keys must not reach the event"


async def test_an_enabled_category_raises_a_persistent_notification(
    hass: HomeAssistant, notifications: Any
) -> None:
    """A category the user opted into produces a Markdown notification, one per mower and category."""
    _notifier, handler = _started(hass)

    await handler(_warning())

    args, kwargs = notifications.create.call_args
    assert args == (hass, "**Lost RTK** (2801)\nMove")
    assert kwargs == {
        "title": "Luba-1: warnings",
        "notification_id": "mammotion_Luba-1_warnings",
    }


async def test_notifications_are_off_when_the_option_was_never_set(
    hass: HomeAssistant, notifications: Any
) -> None:
    """Opt-in: a new entry has no ``notify`` option and raises nothing."""
    _notifier, handler = _started(hass, notify=None)

    await handler(_warning())

    notifications.create.assert_not_called()


async def test_a_disabled_category_raises_nothing(
    hass: HomeAssistant, notifications: Any
) -> None:
    """A category the user left out stays silent."""
    _notifier, handler = _started(hass, notify=["notifications"])

    await handler(_warning())

    notifications.create.assert_not_called()


async def test_the_warning_notification_is_dismissed_once_errors_clear(
    hass: HomeAssistant, notifications: Any
) -> None:
    """The warning notification is withdrawn when the mower stops reporting errors."""
    notifier, handler = _started(hass)
    on_update = notifier.coordinator.async_add_listener.call_args.args[0]
    await handler(_warning())
    notifier.coordinator.data.errors.err_code_list = [2801]

    on_update()
    notifications.dismiss.assert_not_called()

    notifier.coordinator.data.errors.err_code_list = []
    on_update()
    on_update()

    notifications.dismiss.assert_called_once_with(hass, "mammotion_Luba-1_warnings")


async def test_stopping_the_notifier_unsubscribes_from_the_coordinator(
    hass: HomeAssistant,
) -> None:
    """Unloading the entry must leave no subscription behind on the coordinator."""
    notifier = MowerNotifier(hass, _coordinator())

    notifier.async_start()()

    notifier.coordinator.subscribe_notification.return_value.assert_called_once_with()
    notifier.coordinator.async_add_listener.return_value.assert_called_once_with()


def test_a_grouped_warning_gets_one_paragraph_per_code() -> None:
    """A mower reports several codes in one frame; each needs its own readable entry."""
    described = {
        1201: {"message": "The robot is stuck", "solution": "Clear the obstacle"},
        1004: {"message": "Tilt sensor activated", "solution": ""},
    }
    attributes = notification_attributes(
        {
            "data": '[{"c":-11134,"ct":1,"ft":0},{"c":-1201,"ct":1,"ft":0},{"c":-1004,"ct":1,"ft":0}]'
        },
        described.get,
    )

    assert notification_message(attributes) == (
        "Unknown error code 11134\n\n"
        "**The robot is stuck** (1201)\nClear the obstacle\n\n"
        "**Tilt sensor activated** (1004)"
    )


def test_attributes_keep_only_data_and_codes() -> None:
    """Raw payload keys other than ``data`` are not carried into the event."""
    attributes = notification_attributes({"data": "not json", "x": 1})

    assert attributes == {"data": "not json"}
    assert notification_attributes(None) == {}


def test_oversized_data_is_left_out_but_its_codes_are_kept() -> None:
    """The recorder drops attributes past 16 KB; a huge ``data`` must not take the codes with it."""
    codes = [
        {"c": -1, "ct": 1, "ft": 0, "pad": "x" * 100}
        for _ in range(MAX_DATA_BYTES // 100)
    ]

    attributes = notification_attributes({"data": json.dumps(codes)})

    assert "data" not in attributes
    assert len(attributes["codes"]) == len(codes)


def test_notification_code_is_described_when_known() -> None:
    """A known code carries its human description."""
    attributes = notification_attributes(
        {"data": '{"localTime":1725159492000,"code":"1002"}'},
        lambda code: {"message": "Blade stuck"} if code == 1002 else None,
    )

    assert attributes["codes"] == [
        {"code": 1002, "time": "2024-09-01T02:58:12+00:00", "message": "Blade stuck"}
    ]


def test_notification_local_time_in_seconds_is_decoded() -> None:
    """Some firmware sends localTime in seconds (1788929118), others milliseconds."""
    attributes = notification_attributes(
        {"data": '{"localTime":1788929118,"code":"1203"}'}
    )

    assert attributes["codes"][0]["time"] == "2026-09-09T04:45:18+00:00"


def test_an_unknown_code_is_listed_without_description() -> None:
    """An unknown code still reaches the user, named by its number."""
    attributes = notification_attributes(
        {"data": '[{"c":-9999,"ct":2,"ft":0}]'}, lambda code: None
    )

    assert attributes["codes"] == [
        {"code": 9999, "count": 2, "time": "1970-01-01T00:00:00+00:00"}
    ]
    assert notification_message(attributes) == "Unknown error code 9999"


def test_warning_frame_time_is_always_milliseconds() -> None:
    """``ft`` is milliseconds; a small value (unsynced clock) is not seconds."""
    attributes = notification_attributes({"data": '[{"c":-1,"ct":1,"ft":5400000}]'})

    assert attributes["codes"][0]["time"] == "1970-01-01T01:30:00+00:00"


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ["warnings"]),
        ({CONF_NOTIFY: []}, []),
        ({CONF_NOTIFY: ["notifications"]}, ["notifications"]),
    ],
    ids=["unset-keeps-warnings", "explicitly-off-stays-off", "user-choice-kept"],
)
async def test_migration_keeps_notifications_on_for_existing_entries(
    hass: HomeAssistant, options: dict[str, Any], expected: list[str]
) -> None:
    """Notifications became opt-in; an entry from before that must not go silent."""
    entry = MockConfigEntry(domain=DOMAIN, version=1, minor_version=2, options=options)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 3
    assert entry.options[CONF_NOTIFY] == expected
