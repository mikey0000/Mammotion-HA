"""Mammotion services."""

from __future__ import annotations

import dataclasses
from typing import Any, cast

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LOGGER
from .geojson_utils import apply_geojson_offset
from .models import MammotionMowerData

SERVICE_GET_GEOJSON = "get_geojson"
SERVICE_GET_MOW_PATH_GEOJSON = "get_mow_path_geojson"
SERVICE_GET_MOW_PROGRESS_GEOJSON = "get_mow_progress_geojson"
SERVICE_GET_MAP_DATA = "get_map_data"
SERVICE_SVG_ADD = "svg_add"
SERVICE_SVG_UPDATE = "svg_update"
SERVICE_SVG_DELETE = "svg_delete"

GEOJSON_SCHEMA = vol.Schema(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id}, extra=vol.ALLOW_EXTRA
)

_SVG_COMMON_FIELDS = {
    vol.Optional("svg_file_name", default="pattern.svg"): str,
    vol.Optional("scale", default=1.0): vol.Coerce(float),
    vol.Optional("rotate", default=0.0): vol.Coerce(float),
    vol.Optional("base_width_m", default=2.5): vol.Coerce(float),
    vol.Optional("base_height_m", default=2.5): vol.Coerce(float),
    vol.Optional("x_move"): vol.Coerce(float),
    vol.Optional("y_move"): vol.Coerce(float),
}

SVG_ADD_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("area_hash"): vol.Coerce(int),
        vol.Required("svg_data"): str,
        **_SVG_COMMON_FIELDS,
    },
    extra=vol.ALLOW_EXTRA,
)

SVG_UPDATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("device_hash"): vol.Coerce(int),
        vol.Required("area_hash"): vol.Coerce(int),
        vol.Required("svg_data"): str,
        **_SVG_COMMON_FIELDS,
    },
    extra=vol.ALLOW_EXTRA,
)

SVG_DELETE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required("device_hash"): vol.Coerce(int),
        vol.Required("area_hash"): vol.Coerce(int),
    },
    extra=vol.ALLOW_EXTRA,
)


_JS_MAX_SAFE_INT = (1 << 53) - 1


def _stringify_large_ints(obj: Any) -> Any:
    """Recursively convert integers beyond JS Number.MAX_SAFE_INTEGER to strings.

    JavaScript's JSON.parse silently loses precision on integers > 2**53-1.
    Converting them to strings before sending over the WebSocket preserves the
    full hash value; Python's vol.Coerce(int) can convert them back on ingress.
    """
    if isinstance(obj, dict):
        return {k: _stringify_large_ints(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stringify_large_ints(v) for v in obj]
    if (
        isinstance(obj, int)
        and not isinstance(obj, bool)
        and abs(obj) > _JS_MAX_SAFE_INT
    ):
        return str(obj)
    return obj


def _get_mower_by_entity_id(
    hass: HomeAssistant, entity_id: str
) -> MammotionMowerData | None:
    """Find the MammotionMowerData for the given entity_id across all config entries."""
    from . import MammotionConfigEntry  # noqa: PLC0415

    entity_reg = er.async_get(hass)
    entity_entry = entity_reg.async_get(entity_id)
    if entity_entry is None:
        LOGGER.error("Could not find entity %s", entity_id)
        return None

    entries: list[MammotionConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    for entry in entries:
        if not entry.runtime_data:
            continue
        mower = next(
            (
                m
                for m in entry.runtime_data.mowers
                if entity_entry.unique_id.startswith(
                    m.reporting_coordinator.unique_name
                )
            ),
            None,
        )
        if mower is not None:
            return mower
    return None


@callback
def async_setup_services(hass: HomeAssistant) -> None:  # noqa: C901
    """Register Mammotion services."""

    async def handle_get_geojson(call: ServiceCall) -> dict[str, Any]:
        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        coordinator = mower.reporting_coordinator
        if coordinator.is_online():
            await coordinator.async_start_report_stream(duration_ms=300_000)
        return apply_geojson_offset(
            coordinator.data.map.generated_geojson,
            coordinator.map_offset_lat,
            coordinator.map_offset_lon,
        )

    async def handle_get_mow_path_geojson(call: ServiceCall) -> dict[str, Any]:
        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        coordinator = mower.reporting_coordinator
        return apply_geojson_offset(
            coordinator.data.map.generated_mow_path_geojson,
            coordinator.map_offset_lat,
            coordinator.map_offset_lon,
        )

    async def handle_get_mow_progress_geojson(call: ServiceCall) -> dict[str, Any]:
        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        coordinator = mower.reporting_coordinator
        return apply_geojson_offset(
            coordinator.data.map.generated_mow_progress_geojson,
            coordinator.map_offset_lat,
            coordinator.map_offset_lon,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_GEOJSON,
        handle_get_geojson,
        schema=GEOJSON_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MOW_PATH_GEOJSON,
        handle_get_mow_path_geojson,
        schema=GEOJSON_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MOW_PROGRESS_GEOJSON,
        handle_get_mow_progress_geojson,
        schema=GEOJSON_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def handle_get_map_data(call: ServiceCall) -> dict[str, Any]:
        from pymammotion.data.model.device import MowingDevice  # noqa: PLC0415

        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        device_data = cast(MowingDevice, mower.reporting_coordinator.data)
        map_dict = dataclasses.asdict(device_data.map)
        return _stringify_large_ints(
            {
                "area": map_dict.get("area", {}),
                "svg": map_dict.get("svg", {}),
                "area_name": map_dict.get("area_name", []),
            }
        )

    async def handle_svg_add(call: ServiceCall) -> dict[str, Any]:
        from pymammotion.data.model.device import MowingDevice  # noqa: PLC0415
        from pymammotion.utility.svg import build_svg_for_area  # noqa: PLC0415

        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        coordinator = mower.reporting_coordinator
        device_data = cast(MowingDevice, coordinator.data)
        area_hash: int = call.data["area_hash"]
        frame_list = device_data.map.area.get(area_hash)
        boundary = []
        if frame_list:
            for frame in sorted(
                frame_list.data, key=lambda f: getattr(f, "current_frame", 0)
            ):
                boundary.extend(getattr(frame, "data_couple", []))
        msg = build_svg_for_area(
            area_hash=area_hash,
            boundary=boundary,
            svg_file_data=call.data["svg_data"],
            svg_file_name=call.data["svg_file_name"],
            scale=call.data["scale"],
            rotate=call.data["rotate"],
            base_width_m=call.data["base_width_m"],
            base_height_m=call.data["base_height_m"],
        )
        if "x_move" in call.data:
            msg.svg_message.x_move = call.data["x_move"]
        if "y_move" in call.data:
            msg.svg_message.y_move = call.data["y_move"]
        result = await coordinator.send_svg_command(msg)
        return {"device_hash": str(result)}

    async def handle_svg_update(call: ServiceCall) -> dict[str, Any]:
        from pymammotion.data.model.device import MowingDevice  # noqa: PLC0415
        from pymammotion.utility.svg import build_svg_update  # noqa: PLC0415

        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        coordinator = mower.reporting_coordinator
        device_data = cast(MowingDevice, coordinator.data)
        area_hash: int = call.data["area_hash"]
        frame_list = device_data.map.area.get(area_hash)
        boundary = []
        if frame_list:
            for frame in sorted(
                frame_list.data, key=lambda f: getattr(f, "current_frame", 0)
            ):
                boundary.extend(getattr(frame, "data_couple", []))
        msg = build_svg_update(
            device_hash=call.data["device_hash"],
            area_hash=area_hash,
            boundary=boundary,
            svg_file_data=call.data["svg_data"],
            svg_file_name=call.data["svg_file_name"],
            scale=call.data["scale"],
            rotate=call.data["rotate"],
            base_width_m=call.data["base_width_m"],
            base_height_m=call.data["base_height_m"],
        )
        if "x_move" in call.data:
            msg.svg_message.x_move = call.data["x_move"]
        if "y_move" in call.data:
            msg.svg_message.y_move = call.data["y_move"]
        result = await coordinator.send_svg_command(msg)
        return {"device_hash": str(result)}

    async def handle_svg_delete(call: ServiceCall) -> dict[str, Any]:
        from pymammotion.utility.svg import build_svg_delete  # noqa: PLC0415

        mower = _get_mower_by_entity_id(hass, call.data[ATTR_ENTITY_ID])
        if mower is None:
            LOGGER.error("Could not find entity %s", call.data[ATTR_ENTITY_ID])
            return {}
        msg = build_svg_delete(
            device_hash=call.data["device_hash"],
            area_hash=call.data["area_hash"],
        )
        await mower.reporting_coordinator.send_svg_command(msg)
        return {}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_MAP_DATA,
        handle_get_map_data,
        schema=GEOJSON_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SVG_ADD,
        handle_svg_add,
        schema=SVG_ADD_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SVG_UPDATE,
        handle_svg_update,
        schema=SVG_UPDATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SVG_DELETE,
        handle_svg_delete,
        schema=SVG_DELETE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
