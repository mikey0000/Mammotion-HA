"""Device tracker platform for Mammotion mowers."""

from __future__ import annotations

import logging
import math
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pymammotion.data.model.enums import RTKStatus

from . import MammotionConfigEntry
from .const import ATTR_DIRECTION
from .coordinator import MammotionBaseUpdateCoordinator
from .entity import MammotionBaseEntity

_LOGGER = logging.getLogger(__name__)

#: Typical horizontal error in metres for each fix type; the mower reports no
#: per-fix figure. A coarse lookup also keeps ``gps_accuracy`` from changing on
#: every frame, which would write a state row each time.
_ACCURACY_BY_FIX: dict[RTKStatus, float] = {
    RTKStatus.FIX: 0.05,
    RTKStatus.FLOAT: 0.5,
}
#: Single-point fix, and the no-fix case, where 0 would claim an exact position.
_FALLBACK_ACCURACY = 3.0
#: A still mower's fix wanders by millimetres from frame to frame; holding the
#: published position until it has moved this far keeps that noise out of the
#: state without rounding the coordinates.
_MIN_MOVE_METRES = 0.01


def _distance_metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return the distance between two nearby (latitude, longitude) points."""
    north = (b[0] - a[0]) * 111_111.0
    east = (b[1] - a[1]) * 111_111.0 * math.cos(math.radians(a[0]))
    return math.hypot(north, east)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the RTK tracker from config entry."""
    mammotion_devices = entry.runtime_data.mowers

    for mower in mammotion_devices:
        async_add_entities([MammotionTracker(mower.reporting_coordinator)])


class MammotionTracker(MammotionBaseEntity, TrackerEntity, RestoreEntity):  # type: ignore[misc]
    """Mammotion device tracker."""

    _attr_translation_key = "device_tracker"

    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: MammotionBaseUpdateCoordinator[Any]) -> None:
        """Initialize the Tracker."""
        super().__init__(coordinator, f"{coordinator.device_name}_gps")
        self._position: tuple[float, float] | None = None

    async def async_added_to_hass(self) -> None:
        """Take the stored position before the first state is written."""
        await super().async_added_to_hass()
        self._update_position()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update the held position, then write the state."""
        self._update_position()
        super()._handle_coordinator_update()

    @callback
    def _update_position(self) -> None:
        """Hold the published position until the mower has moved _MIN_MOVE_METRES.

        Comparing with the published point rather than the previous frame lets
        slow movement add up, so the state stays within that distance of the
        mower's own reading.
        """
        device = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if device is None:
            self._position = None
            return
        lat = device.location.device.latitude
        lon = device.location.device.longitude
        if lat is None or lon is None:
            self._position = None
            return
        reading = (lat, lon)
        if (
            self._position is None
            or _distance_metres(self._position, reading) >= _MIN_MOVE_METRES
        ):
            self._position = reading

    @property
    def force_update(self) -> bool:
        """Write state only on a real change; TrackerEntity ignores _attr_force_update."""
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        device = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if device is None:
            return {}
        return {ATTR_DIRECTION: device.location.orientation}

    @property
    def location_accuracy(self) -> float:
        """Return the position's accuracy radius in metres, from the RTK fix type."""
        device = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if device is None:
            return _FALLBACK_ACCURACY
        fix = RTKStatus.from_value(device.report_data.rtk.status)
        return _ACCURACY_BY_FIX.get(fix, _FALLBACK_ACCURACY)

    @property
    def latitude(self) -> float | None:
        """Return the held latitude, adjusted by map offset."""
        if self._position is None:
            return None
        return self._position[0] + self.coordinator.map_offset_lat / 111_111.0

    @property
    def longitude(self) -> float | None:
        """Return the held longitude, adjusted by map offset."""
        if self._position is None:
            return None
        lon = self._position[1]
        lat = self.latitude
        if lat is None:
            return None
        cos_lat = math.cos(math.radians(lat))
        if cos_lat == 0:
            return lon
        return lon + self.coordinator.map_offset_lon / (111_111.0 * cos_lat)
