"""Mammotion map image entities."""

from __future__ import annotations

import copy
import datetime
import json
import math
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from pymammotion.utility.constant import WorkMode

from . import MammotionConfigEntry
from .coordinator import MammotionMapUpdateCoordinator, MammotionReportUpdateCoordinator
from .entity import MammotionBaseEntity
from .geojson_utils import apply_geojson_offset
from .map_renderer import placeholder_png, render_map_png


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities,
) -> None:
    """Set up map image entities."""
    async_add_entities(
        MammotionMapImage(mower.map_coordinator, mower.reporting_coordinator, hass)
        for mower in entry.runtime_data.mowers
    )


class MammotionMapImage(MammotionBaseEntity, ImageEntity):
    """Static rendered mower map."""

    _attr_translation_key = "map"
    _attr_content_type = "image/png"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MammotionMapUpdateCoordinator,
        report_coordinator: MammotionReportUpdateCoordinator,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the map image."""
        MammotionBaseEntity.__init__(self, coordinator, "map")
        ImageEntity.__init__(self, hass)
        self._report_coordinator = report_coordinator
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        self._cached_png: bytes | None = None
        self._last_content_key: str | None = None

    async def async_added_to_hass(self) -> None:
        """Refresh rendered image when live mower telemetry changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._report_coordinator.async_add_listener(self._handle_report_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate image when map coordinator changes."""
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        super()._handle_coordinator_update()

    @callback
    def _handle_report_update(self) -> None:
        """Invalidate image when live mower position changes."""
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Return a rendered map image."""
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if mower is None:
            return placeholder_png()

        geojson = self._offset_geojson(mower)
        mower_location = self._offset_location(mower.location.device)
        mower_trail = (
            list(getattr(self._report_coordinator, "location_trail", []))
            if self._is_live_report_active(mower)
            else []
        )
        content_key = self._content_key(geojson, mower_location, mower_trail)
        if self._cached_png is not None and content_key == self._last_content_key:
            return self._cached_png

        tile_cache_dir = self.hass.config.path(".storage", "mammotion_osm_tiles")
        self._cached_png = await self.hass.async_add_executor_job(
            render_map_png,
            copy.deepcopy(geojson),
            tile_cache_dir,
            copy.deepcopy(mower_location),
            copy.deepcopy(mower_trail),
        )
        self._last_content_key = content_key
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        return self._cached_png

    def _offset_geojson(self, mower: Any) -> dict[str, Any] | None:
        geojson = self._merged_geojson(mower)
        if not geojson:
            return None
        return apply_geojson_offset(
            geojson,
            self._report_coordinator.map_offset_lat,
            self._report_coordinator.map_offset_lon,
        )

    @staticmethod
    def _merged_geojson(mower: Any) -> dict[str, Any] | None:
        base_geojson = MammotionMapImage._base_geojson(
            getattr(mower.map, "generated_geojson", None)
        )
        feature_collections = [base_geojson]
        if MammotionMapImage._is_live_report_active(mower):
            feature_collections.extend(
                (
                    MammotionMapImage._line_geojson(
                        getattr(mower.map, "generated_mow_progress_geojson", None)
                    ),
                    MammotionMapImage._line_geojson(
                        getattr(mower.map, "generated_dynamics_line_geojson", None)
                    ),
                )
            )
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
    def _is_live_report_active(mower: Any) -> bool:
        try:
            mode = int(mower.report_data.dev.sys_status or 0)
        except (TypeError, ValueError):
            return False
        return mode in {
            int(WorkMode.MODE_WORKING),
            int(WorkMode.MODE_RETURNING),
            int(WorkMode.MODE_PAUSE),
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
            "dump",
            "no_go_zone",
            "obstacle",
            "station",
            "virtual_wall",
            "visual_obstacle_zone",
            "visual_safety_zone",
        }

    def _offset_location(self, mower_location: Any) -> Any:
        if mower_location is None:
            return None
        location = copy.deepcopy(mower_location)
        latitude = getattr(location, "latitude", None)
        longitude = getattr(location, "longitude", None)
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            return location
        location.latitude = (
            latitude + self._report_coordinator.map_offset_lat / 111_111.0
        )
        cos_lat = math.cos(math.radians(location.latitude))
        if cos_lat != 0:
            location.longitude = longitude + self._report_coordinator.map_offset_lon / (
                111_111.0 * cos_lat
            )
        return location

    @staticmethod
    def _content_key(
        geojson: dict[str, Any] | None,
        mower_location: Any | None,
        mower_trail: list[tuple[float, float]],
    ) -> str:
        location_key = None
        if mower_location is not None:
            location_key = (
                round(float(getattr(mower_location, "latitude", 0.0) or 0.0), 7),
                round(float(getattr(mower_location, "longitude", 0.0) or 0.0), 7),
            )
        payload = {
            "geojson": geojson,
            "location": location_key,
            "trail": [
                (round(float(lon), 7), round(float(lat), 7))
                for lon, lat in mower_trail[-80:]
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
