"""Mammotion map image entities."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
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
from .coordinator import MammotionReportUpdateCoordinator
from .entity import MammotionBaseEntity
from .geojson_utils import apply_coord, apply_geojson_offset

_IMAGE_RENDER_TIMEOUT = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MammotionConfigEntry,
    async_add_entities,
) -> None:
    """Set up map image entities."""
    async_add_entities(
        MammotionMapImage(
            mower.reporting_coordinator,
            hass,
        )
        for mower in entry.runtime_data.mowers
    )


class MammotionMapImage(MammotionBaseEntity, ImageEntity):
    """Static rendered mower map."""

    _RENDER_CACHE_SECONDS = 300.0
    _LOCATION_PRECISION = 5
    _PLACEHOLDER_CONTENT_KEY = "placeholder"

    _attr_translation_key = "map"
    _attr_content_type = "image/png"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: MammotionReportUpdateCoordinator,
        hass: HomeAssistant,
    ) -> None:
        """Initialize the map image."""
        MammotionBaseEntity.__init__(self, coordinator, "map")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        self._cached_png: bytes | None = None
        self._last_content_key: str | None = None
        self._notified_content_key: str | None = None
        self._last_render_time = 0.0
        self._geometry_sources: tuple[object | None, ...] | None = None
        self._geometry_offset: tuple[float, float] | None = None
        self._geometry_geojson: dict[str, Any] | None = None
        self._geometry_key = ""
        self._render_lock = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        """Refresh rendered image when the mower map changes."""
        await super().async_added_to_hass()
        if unsubscribe := self.coordinator.subscribe_map_updated(
            self._handle_map_update
        ):
            self.async_on_remove(unsubscribe)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate image when live mower telemetry changes."""
        content_key = self._current_content_key()
        if content_key != self._notified_content_key:
            self._notified_content_key = content_key
            self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        super()._handle_coordinator_update()

    @callback
    def _handle_map_update(self) -> None:
        """Invalidate image when static map data changes."""
        self._cached_png = None
        self._geometry_sources = None
        self._notified_content_key = None
        self._attr_image_last_updated = datetime.datetime.now(datetime.UTC)
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Return a rendered map image."""
        async with self._render_lock:
            # Re-read after waiting: another caller may have rendered this key,
            # or fresh telemetry may have replaced the requested image.
            payload = self._image_payload()
            if payload is None:
                self._notified_content_key = self._PLACEHOLDER_CONTENT_KEY
                return placeholder_png()

            geojson, mower_location, content_key = payload
            now = time.monotonic()
            if (
                self._cached_png is not None
                and content_key == self._last_content_key
                and now - self._last_render_time < self._RENDER_CACHE_SECONDS
            ):
                return self._cached_png

            tile_cache_dir = self.hass.config.path(
                ".storage", "mammotion_osm_tiles"
            )
            try:
                async with asyncio.timeout(_IMAGE_RENDER_TIMEOUT):
                    rendered = await render_map_png(
                        geojson,
                        tile_cache_dir,
                        mower_location,
                    )
            except TimeoutError:
                # Let the next coordinator tick announce the same content key
                # again so HA retries a render that exceeded its image deadline.
                self._notified_content_key = None
                return self._cached_png or placeholder_png()
            self._cached_png = rendered
            self._last_content_key = content_key
            self._notified_content_key = content_key
            self._last_render_time = now
            return self._cached_png

    def _current_content_key(self) -> str:
        """Return the key for the image Home Assistant should fetch."""
        payload = self._image_payload()
        if payload is None:
            return self._PLACEHOLDER_CONTENT_KEY
        return payload[2]

    def _image_payload(self) -> tuple[dict[str, Any] | None, Any | None, str] | None:
        """Return render inputs and their stable content key."""
        mower = self.coordinator.manager.get_device_by_name(
            self.coordinator.device_name
        )
        if mower is None or not self._has_renderable_map(mower):
            return None

        offset_lat = self.coordinator.map_offset_lat
        offset_lon = self.coordinator.map_offset_lon
        geojson, geometry_key = self._geometry_payload(mower, offset_lat, offset_lon)
        mower_location = self._offset_location(
            mower.location.device, offset_lat, offset_lon
        )
        content_key = self._content_key(
            geometry_key,
            mower_location,
        )
        return geojson, mower_location, content_key

    def _geometry_payload(
        self,
        mower: Any,
        offset_lat: float,
        offset_lon: float,
    ) -> tuple[dict[str, Any] | None, str]:
        """Return cached render geometry and a stable digest."""
        sources = self._geojson_sources(mower)
        offset = (offset_lat, offset_lon)
        if (
            self._geometry_sources is not None
            and len(sources) == len(self._geometry_sources)
            and all(
                current is cached
                for current, cached in zip(
                    sources[:-1], self._geometry_sources[:-1], strict=True
                )
            )
            and sources[-1] == self._geometry_sources[-1]
            and offset == self._geometry_offset
        ):
            return self._geometry_geojson, self._geometry_key

        geojson = self._merged_geojson(sources)
        if geojson is not None:
            geojson = apply_geojson_offset(geojson, offset_lat, offset_lon)
        encoded = json.dumps(
            geojson,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self._geometry_sources = sources
        self._geometry_offset = offset
        self._geometry_geojson = geojson
        self._geometry_key = hashlib.sha256(encoded).hexdigest()
        return geojson, self._geometry_key

    def _has_synced_map(self, mower: Any) -> bool:
        """Return whether the map cache is complete enough to render."""
        locations = mower.report_data.locations
        bol_hash = locations[0].bol_hash if locations else 0
        return self.coordinator.map_sync_status == "synced" and mower.map.is_map_synced(
            bol_hash
        )

    def _has_renderable_map(self, mower: Any) -> bool:
        """Keep the last complete generated map visible between synchronizations."""
        if self._has_synced_map(mower):
            return True
        return self._base_geojson(mower.map.generated_geojson) is not None

    def _geojson_sources(self, mower: Any) -> tuple[object | None, ...]:
        """Return the map objects whose identities define rendered geometry."""
        base_geojson = getattr(mower.map, "generated_geojson", None)
        mow_path_geojson = getattr(mower.map, "generated_mow_path_geojson", None)
        device_type = DeviceType.value_of_str(self.coordinator.device_name)
        firmware = mower.device_firmwares.main_controller
        dynamics_geojson = mower.map.generated_dynamics_line_geojson
        if device_type.is_support_dynamics_line(firmware) and self._has_line_geometry(
            dynamics_geojson
        ):
            progress_geojson = dynamics_geojson
        else:
            progress_geojson = mower.map.generated_mow_progress_geojson
        area_names = tuple(
            sorted(
                (
                    int(area.hash),
                    str(getattr(area, "name", "") or "").strip(),
                )
                for area in getattr(mower.map, "area_name", ())
                if str(getattr(area, "hash", "")).lstrip("-").isdigit()
            )
        )
        return base_geojson, mow_path_geojson, progress_geojson, area_names

    @staticmethod
    def _has_line_geometry(geojson: dict[str, Any] | None) -> bool:
        """Return whether a native progress payload contains a usable line."""
        return isinstance(geojson, dict) and any(
            (feature.get("geometry") or {}).get("type")
            in {"LineString", "MultiLineString"}
            for feature in geojson.get("features") or []
        )

    @staticmethod
    def _merged_geojson(
        sources: tuple[object | None, ...],
    ) -> dict[str, Any] | None:
        base_geojson = MammotionMapImage._base_geojson(
            sources[0] if isinstance(sources[0], dict) else None
        )
        mow_path_geojson = MammotionMapImage._line_geojson(
            sources[1] if isinstance(sources[1], dict) else None
        )
        area_names = dict(sources[3]) if len(sources) > 3 else {}
        feature_collections = [
            base_geojson,
            mow_path_geojson,
            MammotionMapImage._line_geojson(
                sources[2] if isinstance(sources[2], dict) else None
            ),
        ]
        features: list[dict[str, Any]] = []
        for geojson in feature_collections:
            if isinstance(geojson, dict):
                for feature in geojson.get("features") or []:
                    properties = feature.get("properties") or {}
                    try:
                        area_id = int(properties.get("hash"))
                    except (TypeError, ValueError):
                        area_id = 0
                    if area_id in area_names and area_names[area_id]:
                        feature = {
                            **feature,
                            "properties": {
                                **properties,
                                "Name": area_names[area_id],
                            },
                        }
                    features.append(feature)
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
        geometry_key: str,
        mower_location: Any | None,
    ) -> str:
        location_key = None
        if mower_location is not None:
            location_key = (
                round(
                    float(mower_location.longitude),
                    MammotionMapImage._LOCATION_PRECISION,
                ),
                round(
                    float(mower_location.latitude),
                    MammotionMapImage._LOCATION_PRECISION,
                ),
            )
        payload = {
            "geometry": geometry_key,
            "location": location_key,
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
