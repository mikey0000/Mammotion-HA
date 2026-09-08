"""Mammotion event entities."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pymammotion.state.device_state import DeviceNotification

from . import MammotionConfigEntry
from .const import LOGGER
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


def notification_attributes(value: dict[str, Any] | None) -> dict[str, Any]:
    """Return event attributes for a notification, decoding the JSON-string ``data`` payload."""
    if not value:
        return {}
    attributes = dict(value)
    data = attributes.get("data")
    if isinstance(data, str):
        with contextlib.suppress(ValueError):
            attributes["data"] = json.loads(data)
    return attributes


class MammotionNotificationEventEntity(MammotionBaseEntity, EventEntity):
    """Fires for every non-protobuf thing/event post the mower sends."""

    _attr_translation_key = "notification"
    _attr_event_types = NOTIFICATION_EVENT_TYPES

    def __init__(self, coordinator: MammotionReportUpdateCoordinator) -> None:
        """Initialize the notification event entity."""
        super().__init__(coordinator, "notification")

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
        self._trigger_event(
            notification.identifier, notification_attributes(notification.value)
        )
        self.async_write_ha_state()
