"""Mammotion event entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MammotionConfigEntry
from .coordinator import MammotionReportUpdateCoordinator
from .entity import MammotionBaseEntity
from .notifications import NOTIFICATION_EVENT_TYPES, MowerNotifier


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one notification event entity per mower."""
    async_add_entities(
        MammotionNotificationEventEntity(mower.reporting_coordinator, mower.notifier)
        for mower in entry.runtime_data.mowers
    )


class MammotionNotificationEventEntity(MammotionBaseEntity, EventEntity):
    """Fires for every non-protobuf thing/event post the mower sends."""

    _attr_translation_key = "notification"
    _attr_event_types = NOTIFICATION_EVENT_TYPES

    def __init__(
        self, coordinator: MammotionReportUpdateCoordinator, notifier: MowerNotifier
    ) -> None:
        """Initialize the notification event entity."""
        super().__init__(coordinator, "notification")
        self._notifier = notifier

    async def async_added_to_hass(self) -> None:
        """Listen to the mower's notifier for as long as the entity lives."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._notifier.async_add_listener(self._async_on_notification)
        )

    @callback
    def _async_on_notification(
        self, event_type: str, attributes: dict[str, Any]
    ) -> None:
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
