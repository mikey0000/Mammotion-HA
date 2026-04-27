"""Pure GeoJSON coordinate offset utilities."""

from __future__ import annotations

import math
from typing import Any

_METERS_PER_DEGREE = 111_111.0


def apply_coord(
    c: list[float], lat: float, offset_lat_m: float, offset_lon_m: float
) -> list[float]:
    """Shift a single [lon, lat] coordinate pair by metre offsets."""
    cos_lat = math.cos(math.radians(lat)) or 1.0
    return [
        c[0] + offset_lon_m / (_METERS_PER_DEGREE * cos_lat),
        c[1] + offset_lat_m / _METERS_PER_DEGREE,
        *c[2:],
    ]


def offset_geometry(
    geometry: dict[str, Any], offset_lat_m: float, offset_lon_m: float
) -> dict[str, Any]:
    """Return a new geometry dict with all coordinates shifted by metre offsets."""
    if not geometry:
        return geometry
    gtype = geometry.get("type")
    if gtype == "Point":
        c = geometry["coordinates"]
        return {
            **geometry,
            "coordinates": apply_coord(c, c[1], offset_lat_m, offset_lon_m),
        }
    if gtype == "LineString":
        return {
            **geometry,
            "coordinates": [
                apply_coord(c, c[1], offset_lat_m, offset_lon_m)
                for c in geometry["coordinates"]
            ],
        }
    if gtype == "Polygon":
        return {
            **geometry,
            "coordinates": [
                [apply_coord(c, c[1], offset_lat_m, offset_lon_m) for c in ring]
                for ring in geometry["coordinates"]
            ],
        }
    if gtype == "MultiPolygon":
        return {
            **geometry,
            "coordinates": [
                [
                    [apply_coord(c, c[1], offset_lat_m, offset_lon_m) for c in ring]
                    for ring in poly
                ]
                for poly in geometry["coordinates"]
            ],
        }
    if gtype == "MultiLineString":
        return {
            **geometry,
            "coordinates": [
                [apply_coord(c, c[1], offset_lat_m, offset_lon_m) for c in line]
                for line in geometry["coordinates"]
            ],
        }
    if gtype == "GeometryCollection":
        return {
            **geometry,
            "geometries": [
                offset_geometry(g, offset_lat_m, offset_lon_m)
                for g in geometry.get("geometries", [])
            ],
        }
    return geometry


def apply_geojson_offset(
    geojson: dict[str, Any], offset_lat_m: float, offset_lon_m: float
) -> dict[str, Any]:
    """Return geojson with all coordinates shifted by the given metre offsets.

    Handles FeatureCollection, Feature, and bare geometry types.
    Returns the original object unchanged when both offsets are zero.
    """
    if not offset_lat_m and not offset_lon_m:
        return geojson
    gtype = geojson.get("type")
    if gtype == "FeatureCollection":
        return {
            **geojson,
            "features": [
                {
                    **f,
                    "geometry": offset_geometry(
                        f["geometry"], offset_lat_m, offset_lon_m
                    ),
                }
                for f in geojson.get("features", [])
            ],
        }
    if gtype == "Feature":
        return {
            **geojson,
            "geometry": offset_geometry(
                geojson["geometry"], offset_lat_m, offset_lon_m
            ),
        }
    return offset_geometry(geojson, offset_lat_m, offset_lon_m)
