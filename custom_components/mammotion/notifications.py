"""Mower thing/event notifications, handled once per mower and independent of any entity.

The notifier decodes and describes each notification, then publishes it three ways:
to the event entity (if enabled), as a ``mammotion_event`` bus event, and as a
persistent notification when the user opted into that category.  None of them
depends on another, so disabling the entity no longer silences the other two.

It also watches the report's self-check code: while that names something stopping
the mower from starting, a persistent notification says what, until it clears.
Notification text comes from the integration's translations in HA's language.
"""

from __future__ import annotations

import contextlib
import json
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.translation import async_get_translations
from pymammotion.state.device_state import DeviceNotification

from .const import (
    CONF_NOTIFY,
    DEFAULT_NOTIFY,
    DOMAIN,
    EVENT_MAMMOTION,
    LOGGER,
    NOTIFY_CATEGORY_BY_EVENT,
    NOTIFY_SELF_CHECK,
    NOTIFY_WARNINGS,
    SELF_CHECK_NORMAL,
    SELF_CHECK_OTHER,
    SELF_CHECK_STATES,
)
from .coordinator import MammotionReportUpdateCoordinator

NOTIFICATION_EVENT_TYPES: list[str] = [
    "device_notification_event",
    "device_information_event",
    "device_warning_code_event",
    "device_warning_event",
    "device_biz_req_event",
    "device_log_progress_event",
    "device_config_req_event",
]

#: Decoded ``data`` larger than this is left out of the event: the recorder drops
#: state attributes past 16 KB, and a notification is not worth a whole row.
MAX_DATA_BYTES = 4096

DescribeCode = Callable[[int], dict[str, str] | None]
NotificationListener = Callable[[str, dict[str, Any]], None]

#: Error occurrences remembered for de-duplication: the mower's list holds ten, and
#: warning events repeat them, so this comfortably covers both.
MAX_SEEN_WARNINGS = 64
#: Seconds apart an event and a list entry for the same code can be and still be
#: one occurrence (the event's time is in ms, the list's in s).
WARNING_TIME_TOLERANCE = 2

#: English defaults, used only if the translations cannot be loaded.
_UNKNOWN_CODE = "Unknown error code {code}"
_TITLE = "{device_name}: {category}"


def _timestamp(epoch: Any, *, millis: bool | None = None) -> str | None:
    """Return an ISO timestamp for a device epoch, or None.

    ``millis`` says which unit the field uses; ``None`` guesses, since some firmware
    sends ``localTime`` in seconds and some in milliseconds, and a value below 10^11
    cannot be milliseconds after 1973.
    """
    try:
        value = int(epoch)
        if millis is None:
            millis = value >= 100_000_000_000
        if millis:
            value //= 1000
        return datetime.fromtimestamp(value, UTC).isoformat()
    except TypeError, ValueError, OSError, OverflowError:
        return None


def _epoch(timestamp: str | None) -> int | None:
    """Return the epoch seconds of an ISO timestamp from :func:`_timestamp`."""
    if timestamp is None:
        return None
    with contextlib.suppress(ValueError):
        return int(datetime.fromisoformat(timestamp).timestamp())
    return None


def _decoded_codes(data: Any) -> list[dict[str, Any]]:
    """Return the error codes carried by a notification payload.

    Warning-code events carry ``[{"c": -2801, "ct": 1, "ft": <ms>}]``; notification
    and information events carry ``{"code": "1002", "localTime": <ms>}``; a warning
    event's whole ``value`` is ``{"code": 1002}``, with no time.
    """
    entries: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "c" in item:
                with contextlib.suppress(TypeError, ValueError):
                    entries.append(
                        {
                            "code": abs(int(item["c"])),
                            "count": item.get("ct"),
                            "time": _timestamp(item.get("ft"), millis=True),
                        }
                    )
    elif isinstance(data, dict) and "code" in data:
        with contextlib.suppress(TypeError, ValueError):
            entries.append(
                {
                    "code": abs(int(data["code"])),
                    "time": _timestamp(data.get("localTime")),
                }
            )
    return entries


def notification_attributes(
    value: dict[str, Any] | None, describe: DescribeCode | None = None
) -> dict[str, Any]:
    """Return the event attributes: ``data`` decoded, plus ``codes`` when it carries any.

    Only these two keys are kept, so a device payload can never shadow a key Home
    Assistant or the bus event sets, and ``data`` is dropped when too large to record.
    A ``device_warning_event`` has no ``data``: its ``value`` is ``{"code": 1002}``.
    """
    if not value:
        return {}
    data = value.get("data")
    if isinstance(data, str):
        with contextlib.suppress(ValueError):
            data = json.loads(data)
    attributes: dict[str, Any] = {}
    if data is not None and len(json.dumps(data, default=str)) <= MAX_DATA_BYTES:
        attributes["data"] = data
    if codes := _decoded_codes(data if data is not None else value):
        if describe is not None:
            codes = [_described(entry, describe(entry["code"])) for entry in codes]
        attributes["codes"] = codes
    return attributes


def _described(entry: dict[str, Any], info: dict[str, str] | None) -> dict[str, Any]:
    """Merge the coordinator's description (including its ``text``) into a code entry."""
    return {**entry, **info} if info else entry


def notification_message(
    attributes: dict[str, Any], unknown_code: str = _UNKNOWN_CODE
) -> str:
    """Return a Markdown persistent-notification body: a paragraph per code.

    *unknown_code* is the (translated) text for a code the table does not describe,
    with a ``{code}`` placeholder.
    """
    if paragraphs := [
        _code_paragraph(entry, unknown_code) for entry in attributes.get("codes", [])
    ]:
        return "\n\n".join(paragraphs)
    data = attributes.get("data")
    return json.dumps(data) if isinstance(data, (dict, list)) else str(data or "")


def _code_paragraph(entry: dict[str, Any], unknown_code: str) -> str:
    if not (message := entry.get("message")):
        return unknown_code.format(code=entry["code"])
    heading = f"**{message}** ({entry['code']})"
    return f"{heading}\n{solution}" if (solution := entry.get("solution")) else heading


class MowerNotifier:
    """Receives one mower's notifications and publishes them."""

    def __init__(
        self, hass: HomeAssistant, coordinator: MammotionReportUpdateCoordinator
    ) -> None:
        """Initialize the notifier; nothing is subscribed until ``async_start``."""
        self.hass = hass
        self.coordinator = coordinator
        self._listeners: list[NotificationListener] = []
        #: ``(code, epoch seconds)`` of every error occurrence already notified or
        #: already in the mower's list when the notifier started.
        self._seen_warnings: deque[tuple[int, int | None]] = deque(
            maxlen=MAX_SEEN_WARNINGS
        )
        #: Newest entry time in the mower's error list, or None before the first report.
        #: The list is a rolling history that never empties and may be re-sent whole,
        #: so only an entry newer than this is a new fault.
        self._latest_error_epoch: int | None = None
        #: The self-check state the notification currently shows, if one is up.
        self._self_check_shown: str | None = None

    @callback
    def async_add_listener(self, listener: NotificationListener) -> CALLBACK_TYPE:
        """Call *listener* with ``(event_type, attributes)`` for every notification."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Subscribe to the mower; the returned callable undoes it."""
        unsubscribers = [
            self.coordinator.subscribe_notification(self._async_handle_notification),
            self.coordinator.async_add_listener(self._handle_coordinator_update),
        ]

        @callback
        def _stop() -> None:
            for unsubscribe in unsubscribers:
                unsubscribe()

        return _stop

    def _notification_id(self, category: str) -> str:
        return f"{DOMAIN}_{self.coordinator.device_name}_{category}"

    async def _async_handle_notification(
        self, notification: DeviceNotification
    ) -> None:
        if notification.identifier not in NOTIFICATION_EVENT_TYPES:
            LOGGER.debug(
                "%s: ignoring unknown notification identifier %s",
                self.coordinator.device_name,
                notification.identifier,
            )
            return
        attributes = notification_attributes(
            notification.value, self.coordinator.describe_error_code
        )
        for listener in list(self._listeners):
            listener(notification.identifier, attributes)
        await self._async_notify(notification.identifier, attributes)
        self._async_fire(notification.identifier, attributes)

    def _async_fire(self, identifier: str, attributes: dict[str, Any]) -> None:
        payload = {
            **attributes,
            "device_id": self._device_id(),
            "device_name": self.coordinator.device_name,
            "type": identifier,
        }
        self.hass.bus.async_fire(EVENT_MAMMOTION, payload)

    def _device_id(self) -> str | None:
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.coordinator.unique_name)}
        )
        return device.id if device else None

    def _category_enabled(self, category: str) -> bool:
        if (entry := self.coordinator.config_entry) is None:
            return False
        return category in entry.options.get(CONF_NOTIFY, DEFAULT_NOTIFY)

    async def _async_translations(self) -> dict[str, str]:
        """Return the integration's exception and entity strings in HA's language."""
        language = self.hass.config.language
        return {
            **await async_get_translations(self.hass, language, "exceptions", {DOMAIN}),
            **await async_get_translations(self.hass, language, "entity", {DOMAIN}),
        }

    @staticmethod
    def _text(
        translations: dict[str, str], key: str, default: str, **placeholders: Any
    ) -> str:
        """Return the ``exceptions.<key>`` message with *placeholders* filled in."""
        template = translations.get(
            f"component.{DOMAIN}.exceptions.{key}.message", default
        )
        try:
            return template.format(**placeholders)
        except KeyError, IndexError, ValueError:
            return template

    def _is_new_warning(self, code: int, epoch: int | None) -> bool:
        """Return True, and remember it, for an error occurrence not seen before.

        An occurrence is the code plus when it happened.  The mower's error list is a
        rolling history of the last ten — it never empties — and warning events repeat
        what it holds, so the code alone cannot tell a new fault from an old one.
        """
        for seen_code, seen_epoch in self._seen_warnings:
            if seen_code != code:
                continue
            # Without a time an occurrence can only be matched to another without
            # one; matching it to timed history would hide every recurrence of a
            # common code.
            if epoch is None or seen_epoch is None:
                if epoch is seen_epoch:
                    return False
                continue
            if abs(seen_epoch - epoch) <= WARNING_TIME_TOLERANCE:
                return False
        self._seen_warnings.append((code, epoch))
        return True

    async def _async_notify(self, identifier: str, attributes: dict[str, Any]) -> None:
        """Raise a persistent notification when the event's category is enabled in options.

        A warning event only raises one for the occurrences it carries that neither an
        earlier event nor the error list has already produced.
        """
        category = NOTIFY_CATEGORY_BY_EVENT.get(identifier)
        if category is None or not self._category_enabled(category):
            return
        if category == NOTIFY_WARNINGS and (codes := attributes.get("codes")):
            new = [
                entry
                for entry in codes
                if self._is_new_warning(entry["code"], _epoch(entry.get("time")))
            ]
            if not new:
                return
            attributes = {**attributes, "codes": new}
        await self._async_show(category, attributes)

    async def _async_show(self, category: str, attributes: dict[str, Any]) -> None:
        translations = await self._async_translations()
        persistent_notification.async_create(
            self.hass,
            notification_message(
                attributes,
                translations.get(
                    f"component.{DOMAIN}.exceptions.notification_unknown_code.message",
                    _UNKNOWN_CODE,
                ),
            ),
            title=self._text(
                translations,
                f"notification_title_{category}",
                _TITLE,
                device_name=self.coordinator.device_name,
                category=category,
            ),
            notification_id=self._notification_id(category),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Track the self-check code and new entries in the mower's error list."""
        self._update_self_check()
        self._update_warnings()

    @callback
    def _update_warnings(self) -> None:
        """Notify about error-list entries newer than any seen, unless an event already did.

        The first report's list is history and only sets the high-water mark.  This
        is also the only warning source over Bluetooth, where no cloud events arrive.
        """
        errors = getattr(self.coordinator.data, "errors", None)
        # An empty list means it has not been fetched yet.  Once it has, it is always
        # ten slots, zero-padded — all zeros on a mower that has never had a fault.
        if errors is None or not errors.err_code_list:
            return
        pairs = [
            (abs(int(code)), int(epoch))
            for code, epoch in zip(
                errors.err_code_list, errors.err_code_list_time, strict=False
            )
            if code
        ]
        latest = max((epoch for _, epoch in pairs), default=0)
        if self._latest_error_epoch is None:
            self._latest_error_epoch = latest
            self._seen_warnings.extend(pairs)
            return
        newer = [pair for pair in pairs if pair[1] > self._latest_error_epoch]
        self._latest_error_epoch = max(latest, self._latest_error_epoch)
        new = [pair for pair in newer if self._is_new_warning(*pair)]
        if not new or not self._category_enabled(NOTIFY_WARNINGS):
            return
        entries = [
            _described(
                {"code": code, "time": _timestamp(epoch, millis=False)},
                self.coordinator.describe_error_code(code),
            )
            for code, epoch in new
        ]
        self.hass.async_create_task(
            self._async_show(NOTIFY_WARNINGS, {"codes": entries}),
            f"{self.coordinator.device_name} warning notification",
        )

    @callback
    def _update_self_check(self) -> None:
        """Show what blocks the mower while the self-check code says so; clear it after."""
        report = getattr(self.coordinator.data, "report_data", None)
        if report is None:
            return
        code = report.dev.self_check_status
        state = SELF_CHECK_STATES.get(code, SELF_CHECK_OTHER)
        if state == SELF_CHECK_NORMAL or not self._category_enabled(NOTIFY_SELF_CHECK):
            if self._self_check_shown is not None:
                self._self_check_shown = None
                persistent_notification.async_dismiss(
                    self.hass, self._notification_id(NOTIFY_SELF_CHECK)
                )
            return
        if state == self._self_check_shown:
            return
        self._self_check_shown = state
        self.hass.async_create_task(
            self._async_show_self_check(state, code),
            f"{self.coordinator.device_name} self-check notification",
        )

    async def _async_show_self_check(self, state: str, code: int) -> None:
        translations = await self._async_translations()
        if self._self_check_shown != state:
            return  # it cleared or changed while the translations loaded
        state_name = translations.get(
            f"component.{DOMAIN}.entity.sensor.self_check.state.{state}", state
        )
        persistent_notification.async_create(
            self.hass,
            self._text(translations, f"self_check_{state}", state_name, code=code),
            title=self._text(
                translations,
                "notification_title_self_check",
                _TITLE,
                device_name=self.coordinator.device_name,
                category=state_name,
                state=state_name,
            ),
            notification_id=self._notification_id(NOTIFY_SELF_CHECK),
        )
