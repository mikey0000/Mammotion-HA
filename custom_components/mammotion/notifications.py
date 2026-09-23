"""Mower thing/event notifications, handled once per mower and independent of any entity.

The notifier decodes and describes each notification, then publishes it three ways:
to the event entity (if enabled), as a ``mammotion_event`` bus event, and as a
persistent notification when the user opted into that category.  None of them
depends on another, so disabling the entity no longer silences the other two.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from pymammotion.state.device_state import DeviceNotification

from .const import (
    CONF_NOTIFY,
    DEFAULT_NOTIFY,
    DOMAIN,
    EVENT_MAMMOTION,
    LOGGER,
    NOTIFY_CATEGORY_BY_EVENT,
    NOTIFY_WARNINGS,
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


def _decoded_codes(data: Any) -> list[dict[str, Any]]:
    """Return the error codes carried by a notification payload.

    Warning-code events carry ``[{"c": -2801, "ct": 1, "ft": <ms>}]``; notification
    and information events carry ``{"code": "1002", "localTime": <ms>}``.
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
    if codes := _decoded_codes(data):
        if describe is not None:
            codes = [_described(entry, describe(entry["code"])) for entry in codes]
        attributes["codes"] = codes
    return attributes


def _described(entry: dict[str, Any], info: dict[str, str] | None) -> dict[str, Any]:
    """Merge the coordinator's description (including its ``text``) into a code entry."""
    return {**entry, **info} if info else entry


def notification_message(attributes: dict[str, Any]) -> str:
    """Return a Markdown persistent-notification body: a paragraph per code."""
    if paragraphs := [_code_paragraph(entry) for entry in attributes.get("codes", [])]:
        return "\n\n".join(paragraphs)
    data = attributes.get("data")
    return json.dumps(data) if isinstance(data, (dict, list)) else str(data or "")


def _code_paragraph(entry: dict[str, Any]) -> str:
    if not (message := entry.get("message")):
        return f"Unknown error code {entry['code']}"
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
        self._warning_notified = False

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
        self._async_notify(notification.identifier, attributes)
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

    def _async_notify(self, identifier: str, attributes: dict[str, Any]) -> None:
        """Raise a persistent notification when the event's category is enabled in options."""
        category = NOTIFY_CATEGORY_BY_EVENT.get(identifier)
        if category is None or (entry := self.coordinator.config_entry) is None:
            return
        if category not in entry.options.get(CONF_NOTIFY, DEFAULT_NOTIFY):
            return
        persistent_notification.async_create(
            self.hass,
            notification_message(attributes),
            title=f"{self.coordinator.device_name}: {category}",
            notification_id=self._notification_id(category),
        )
        if category == NOTIFY_WARNINGS:
            self._warning_notified = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Dismiss the warning notification once the mower reports no active errors."""
        if self._warning_notified and not getattr(
            getattr(self.coordinator.data, "errors", None), "err_code_list", True
        ):
            persistent_notification.async_dismiss(
                self.hass, self._notification_id(NOTIFY_WARNINGS)
            )
            self._warning_notified = False
