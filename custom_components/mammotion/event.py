"""Mammotion event entities."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.state.device_state import DeviceNotification

from . import MammotionConfigEntry
from .const import (
    CONF_NOTIFY,
    DEFAULT_NOTIFY,
    DOMAIN,
    EVENT_NOTIFICATION,
    LOGGER,
    NOTIFY_CATEGORY_BY_EVENT,
    NOTIFY_WARNINGS,
)
from .coordinator import MammotionReportUpdateCoordinator
from .entity import MammotionBaseEntity

NOTIFICATION_EVENT_TYPES: list[str] = [
    "device_notification_event",
    "device_information_event",
    "device_warning_code_event",
    "device_warning_event",
    "device_biz_req_event",
    "device_log_progress_event",
    "device_config_req_event",
]

DescribeCode = Callable[[int], dict[str, str] | None]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one notification event entity per mower."""
    async_add_entities(
        MammotionNotificationEventEntity(mower.reporting_coordinator)
        for mower in entry.runtime_data.mowers
    )


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
    """Return event attributes: the payload with its JSON ``data`` decoded and codes described."""
    if not value:
        return {}
    attributes = dict(value)
    data = attributes.get("data")
    if isinstance(data, str):
        with contextlib.suppress(ValueError):
            data = json.loads(data)
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
    """Return a readable persistent-notification body: one line per described code."""
    lines = [
        entry.get("text") or f"Code {entry['code']}"
        for entry in attributes.get("codes", [])
    ]
    if lines:
        return "\n".join(lines)
    data = attributes.get("data")
    return json.dumps(data) if isinstance(data, (dict, list)) else str(data or "")


class MammotionNotificationEventEntity(MammotionBaseEntity, EventEntity):
    """Fires for every non-protobuf thing/event post the mower sends."""

    _attr_translation_key = "notification"
    _attr_event_types = NOTIFICATION_EVENT_TYPES

    def __init__(self, coordinator: MammotionReportUpdateCoordinator) -> None:
        """Initialize the notification event entity."""
        super().__init__(coordinator, "notification")
        self._warning_notified = False

    def _notification_id(self, category: str) -> str:
        return f"{DOMAIN}_{self.coordinator.device_name}_{category}"

    def _async_notify(self, identifier: str, attributes: dict[str, Any]) -> None:
        """Raise a persistent notification when the event's category is enabled in options."""
        category = NOTIFY_CATEGORY_BY_EVENT.get(identifier)
        enabled = self.coordinator.config_entry.options.get(CONF_NOTIFY, DEFAULT_NOTIFY)
        if category is None or category not in enabled:
            return
        title = f"{self.coordinator.device_name}: {category}"
        persistent_notification.async_create(
            self.hass,
            notification_message(attributes),
            title=title,
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
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Subscribe to device notifications for as long as the entity lives."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.subscribe_notification(self._async_handle_notification)
        )

    async def _async_handle_notification(
        self, notification: DeviceNotification
    ) -> None:
        if notification.identifier not in self.event_types:
            LOGGER.debug(
                "%s: ignoring unknown notification identifier %s",
                self.coordinator.device_name,
                notification.identifier,
            )
            return
        attributes = notification_attributes(
            notification.value, self.coordinator.describe_error_code
        )
        self._trigger_event(notification.identifier, attributes)
        self.async_write_ha_state()
        self._async_notify(notification.identifier, attributes)
        # Also a bus event, so automations can react without the entity.
        self.hass.bus.async_fire(
            EVENT_NOTIFICATION,
            {
                "entity_id": self.entity_id,
                "device_name": self.coordinator.device_name,
                "type": notification.identifier,
                **attributes,
            },
        )
