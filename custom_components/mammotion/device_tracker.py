from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import MammotionConfigEntry
from .const import ATTR_DIRECTION
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the RTK tracker from config entry."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        async_add_entities([MammotionTracker(mower.reporting_coordinator)])


class MammotionTracker(MammotionBaseEntity, TrackerEntity, RestoreEntity):
    """Mammotion device tracker."""

    _attr_force_update = False
    _attr_translation_key = "device_tracker"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: MammotionBaseUpdateCoordinator) -> None:
        """Initialize the Tracker."""
        super().__init__(coordinator, f"{coordinator.device_name}_gps")

        self._attr_name = coordinator.device_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return {
            ATTR_DIRECTION: self.coordinator.manager.get_device_by_name(
                self.coordinator.device_name
            ).location.orientation
        }

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device, adjusted by map offset."""
        lat = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        ).location.device.latitude
        if lat is None:
            return None
        return lat + self.coordinator.map_offset_lat / 111_111.0

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device, adjusted by map offset."""
        lon = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        ).location.device.longitude
        if lon is None:
            return None
        lat = self.latitude
        if lat is None:
            return None
        cos_lat = math.cos(math.radians(lat))
        if cos_lat == 0:
            return lon
        return lon + self.coordinator.map_offset_lon / (111_111.0 * cos_lat)

    @property
    def battery_level(self) -> int | None:
        """Return the battery level of the device."""
        return self.coordinator.data.report_data.dev.battery_val
