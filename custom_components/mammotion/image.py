"""Mammotion map image entities."""

from __future__ import annotations

import datetime
import json
import time
from copy import copy
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from pymammotion.utility.device_type import DeviceType
from pymammotion.utility.map_renderer import placeholder_png, render_map_png

from . import MammotionConfigEntry
from .coordinator import (
    MammotionMapUpdateCoordinator,
    MammotionReportUpdateCoordinator,
)
from .entity import MammotionBaseEntity
from .geojson_utils import apply_coord, apply_geojson_offset


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities,
) -> None:
    """Set up map image entities."""
    async_add_entities(
        MammotionMapImage(
            mower.reporting_coordinator,
            mower.map_coordinator,
            hass,
        )
        for mower in entry.runtime_data.mowers
    )


class MammotionMapImage(MammotionBaseEntity, ImageEntity):
    """Static rendered mower map."""

    _RENDER_CACHE_SECONDS = 300.0

    _attr_translation_key = "map"
    _attr_content_type = "image/png"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        map_coordinator: MammotionMapUpdateCoordinator,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the map image."""
        MammotionBaseEntity.__init__(self, coordinator, "map")
        ImageEntity.__init__(self, hass)
        self._map_coordinator = map_coordinator
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        self._cached_png: bytes | None = None
        self._last_content_key: str | None = None
        self._last_render_time = 0.0

    async def async_added_to_hass(self) -> None:
        """Refresh rendered image when the mower map changes."""
        await super().async_added_to_hass()
        self.coordinator.subscribe_map_updated(self._handle_map_update)
        self.async_on_remove(
            self._map_coordinator.async_add_listener(self._handle_map_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate image when live mower telemetry changes."""
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        super()._handle_coordinator_update()

    @callback
    def _handle_map_update(self) -> None:
        """Invalidate image when static map data changes."""
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Return a rendered map image."""
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if mower is None:
            return placeholder_png()

        offset_lat = self.coordinator.map_offset_lat
        offset_lon = self.coordinator.map_offset_lon
        geojson = self._merged_geojson(mower)
        if geojson is not None:
            geojson = apply_geojson_offset(geojson, offset_lat, offset_lon)
        mower_location = self._offset_location(
            mower.location.device, offset_lat, offset_lon
        )
        mower_trail = self._offset_trail(
            list(getattr(self.coordinator, "location_trail", [])),
            offset_lat,
            offset_lon,
        )
        content_key = self._content_key(
            geojson,
            mower_location,
            mower_trail,
            offset_lat,
            offset_lon,
        )
        now = time.monotonic()
        if (
            self._cached_png is not None
            and content_key == self._last_content_key
            and now - self._last_render_time < self._RENDER_CACHE_SECONDS
        ):
            return self._cached_png

        tile_cache_dir = self.hass.config.path(".storage", "mammotion_osm_tiles")
        self._cached_png = await render_map_png(
            geojson,
            tile_cache_dir,
            mower_location,
            mower_trail,
        )
        self._last_content_key = content_key
        self._last_render_time = now
        return self._cached_png

    def _merged_geojson(self, mower: Any) -> dict[str, Any] | None:
        base_geojson = MammotionMapImage._base_geojson(
            getattr(mower.map, "generated_geojson", None)
        )
        device_type = DeviceType.value_of_str(self.coordinator.device_name)
        firmware = mower.device_firmwares.main_controller
        if device_type.is_support_dynamics_line(firmware):
            progress_geojson = mower.map.generated_dynamics_line_geojson
        else:
            progress_geojson = mower.map.generated_mow_progress_geojson
        feature_collections = [
            base_geojson,
            MammotionMapImage._line_geojson(progress_geojson),
        ]
        features: list[dict[str, Any]] = []
        for geojson in feature_collections:
            if isinstance(geojson, dict):
                features.extend(geojson.get("features") or [])
        if not features:
            return None
        return {
            "type": "FeatureCollection",
            "name": "Mammotion Map",
            "features": features,
        }

    @staticmethod
    def _base_geojson(geojson: dict[str, Any] | None) -> dict[str, Any] | None:
        """Keep persistent map geometry and drop stale route/progress overlays."""
        if not isinstance(geojson, dict):
            return None
        features = [
            feature
            for feature in geojson.get("features") or []
            if MammotionMapImage._is_base_map_feature(feature)
        ]
        if not features:
            return None
        return {"type": "FeatureCollection", "features": features}

    @staticmethod
    def _line_geojson(geojson: dict[str, Any] | None) -> dict[str, Any] | None:
        """Keep only line geometry from live task overlays."""
        if not isinstance(geojson, dict):
            return None
        features = [
            feature
            for feature in geojson.get("features") or []
            if (feature.get("geometry") or {}).get("type")
            in {"LineString", "MultiLineString"}
        ]
        if not features:
            return None
        return {"type": "FeatureCollection", "features": features}

    @staticmethod
    def _is_base_map_feature(feature: dict[str, Any]) -> bool:
        properties = feature.get("properties") or {}
        type_name = str(
            properties.get("type_name")
            or properties.get("type")
            or properties.get("Type")
            or ""
        ).lower()
        return type_name in {
            "area",
            "charging_station",
            "corridor_line",
            "corridor_point",
            "dump",
            "no_go_zone",
            "obstacle",
            "path",
            "station",
            "svg",
            "virtual_wall",
            "visual_obstacle_zone",
            "visual_safety_zone",
        }

    @staticmethod
    def _content_key(
        geojson: dict[str, Any] | None,
        mower_location: Any | None,
        mower_trail: list[tuple[float, float]],
        offset_lat: float,
        offset_lon: float,
    ) -> str:
        location_key = None
        if mower_location is not None:
            location_key = (
                round(float(getattr(mower_location, "longitude")), 7),
                round(float(getattr(mower_location, "latitude")), 7),
            )
        payload = {
            "geojson": geojson,
            "location": location_key,
            "trail": [
                (round(float(lon), 7), round(float(lat), 7))
                for lon, lat in mower_trail[-80:]
            ],
            "offset": (round(offset_lat, 3), round(offset_lon, 3)),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _offset_location(
        location: Any | None,
        offset_lat: float,
        offset_lon: float,
    ) -> Any | None:
        if location is None:
            return None
        latitude = getattr(location, "latitude", None)
        longitude = getattr(location, "longitude", None)
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except TypeError, ValueError:
            return None
        if latitude == 0.0 and longitude == 0.0:
            return None
        shifted = apply_coord([longitude, latitude], latitude, offset_lat, offset_lon)
        shifted_location = copy(location)
        shifted_location.longitude = shifted[0]
        shifted_location.latitude = shifted[1]
        return shifted_location

    @staticmethod
    def _offset_trail(
        trail: list[tuple[float, float]],
        offset_lat: float,
        offset_lon: float,
    ) -> list[tuple[float, float]]:
        shifted_trail: list[tuple[float, float]] = []
        for longitude, latitude in trail:
            shifted = apply_coord(
                [float(longitude), float(latitude)],
                float(latitude),
                offset_lat,
                offset_lon,
            )
            shifted_trail.append((shifted[0], shifted[1]))
        return shifted_trail
