"""Static Mammotion map renderer."""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageColor, ImageDraw

CANVAS_SIZE = (1024, 768)
BACKGROUND = (245, 245, 245, 255)
AREA_FILL = (59, 191, 97, 75)
AREA_STROKE = (59, 191, 97, 255)
OBSTACLE_FILL = (255, 149, 20, 85)
OBSTACLE_STROKE = (255, 149, 20, 230)
PATH_STROKE = (45, 45, 45, 240)
TRAIL_STROKE = (35, 119, 235, 170)
TRAIL_RECENT_STROKE = (35, 119, 235, 230)
VIRTUAL_STROKE = (220, 40, 40, 230)
MOWER_FILL = (35, 119, 235, 255)
MOWER_STROKE = (255, 255, 255, 255)
OSM_MAX_ZOOM = 19
OSM_TILE_SIZE = 256
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_USER_AGENT = "HomeAssistant-Mammotion-Map/1.0"


@dataclass(frozen=True)
class GeoBounds:
    """Geographic bounds in WGS84 lon/lat."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def width(self) -> float:
        return self.max_lon - self.min_lon

    @property
    def height(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.min_lon + self.max_lon) / 2,
            (self.min_lat + self.max_lat) / 2,
        )

    def expanded(self) -> GeoBounds:
        center_lat = self.center[1]
        pad_lat = max(self.height * 0.08, _meters_to_lat_degrees(3.0))
        pad_lon = max(self.width * 0.08, _meters_to_lon_degrees(3.0, center_lat))
        return GeoBounds(
            self.min_lon - pad_lon,
            self.min_lat - pad_lat,
            self.max_lon + pad_lon,
            self.max_lat + pad_lat,
        )


def placeholder_png() -> bytes:
    """Return a placeholder when map geometry is not available."""
    image = Image.new("RGBA", CANVAS_SIZE, BACKGROUND)
    draw = ImageDraw.Draw(image)
    text = "No mower map available yet"
    text_bounds = draw.textbbox((0, 0), text)
    draw.text(
        (
            (CANVAS_SIZE[0] - (text_bounds[2] - text_bounds[0])) / 2,
            (CANVAS_SIZE[1] - (text_bounds[3] - text_bounds[1])) / 2,
        ),
        text,
        fill=(120, 120, 120, 255),
    )
    return _encode(image)


def render_map_png(
    geojson: dict[str, Any] | None,
    tile_cache_dir: str | None = None,
    mower_location: Any | None = None,
    mower_trail: list[tuple[float, float]] | None = None,
) -> bytes:
    """Render a Mammotion GeoJSON map into a static PNG."""
    mower_point = _geo_location_point(mower_location)
    trail_points = _valid_geo_points(mower_trail or [])
    points = _geometry_points(geojson or {})
    points.extend(trail_points)
    if mower_point is not None:
        points.append(mower_point)
    if not points:
        return placeholder_png()

    bounds = _geo_bounds(points).expanded()
    center_lon, center_lat = bounds.center
    zoom = OSM_MAX_ZOOM
    min_pixel = _lonlat_to_pixel(bounds.min_lon, bounds.max_lat, zoom)
    max_pixel = _lonlat_to_pixel(bounds.max_lon, bounds.min_lat, zoom)
    pixel_width = max(max_pixel[0] - min_pixel[0], 1.0)
    pixel_height = max(max_pixel[1] - min_pixel[1], 1.0)
    scale = min(
        (CANVAS_SIZE[0] * 0.90) / pixel_width,
        (CANVAS_SIZE[1] * 0.90) / pixel_height,
    )
    scale = max(min(scale, 8.0), 0.2)

    center_pixel = _lonlat_to_pixel(center_lon, center_lat, zoom)
    source_width = CANVAS_SIZE[0] / scale
    source_height = CANVAS_SIZE[1] / scale
    source_min_x = center_pixel[0] - source_width / 2
    source_min_y = center_pixel[1] - source_height / 2
    source = _render_osm_source(
        zoom,
        source_min_x,
        source_min_y,
        source_width,
        source_height,
        tile_cache_dir,
    )
    image = source.resize(CANVAS_SIZE, Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(image, "RGBA")

    def project(coord: tuple[float, float]) -> tuple[float, float]:
        pixel_x, pixel_y = _lonlat_to_pixel(coord[0], coord[1], zoom)
        return (
            (pixel_x - source_min_x) * scale,
            (pixel_y - source_min_y) * scale,
        )

    for feature in (geojson or {}).get("features", []):
        _draw_geojson_feature(draw, feature, project)

    _draw_trail(draw, trail_points, project)

    if mower_point is not None:
        _draw_mower_marker(draw, project(mower_point))

    return _encode(image)


def _render_osm_source(
    zoom: int,
    source_min_x: float,
    source_min_y: float,
    source_width: float,
    source_height: float,
    tile_cache_dir: str | None,
) -> Image.Image:
    source = Image.new(
        "RGBA",
        (math.ceil(source_width), math.ceil(source_height)),
        BACKGROUND,
    )
    max_tile = (2**zoom) - 1
    min_tile_x = max(math.floor(source_min_x / OSM_TILE_SIZE), 0)
    max_tile_x = min(
        math.floor((source_min_x + source_width) / OSM_TILE_SIZE), max_tile
    )
    min_tile_y = max(math.floor(source_min_y / OSM_TILE_SIZE), 0)
    max_tile_y = min(
        math.floor((source_min_y + source_height) / OSM_TILE_SIZE), max_tile
    )

    for tile_x in range(min_tile_x, max_tile_x + 1):
        for tile_y in range(min_tile_y, max_tile_y + 1):
            tile = _load_osm_tile(zoom, tile_x, tile_y, tile_cache_dir)
            if tile is None:
                continue
            source.alpha_composite(
                tile.convert("RGBA"),
                (
                    round(tile_x * OSM_TILE_SIZE - source_min_x),
                    round(tile_y * OSM_TILE_SIZE - source_min_y),
                ),
            )
    return source


def _load_osm_tile(
    zoom: int, tile_x: int, tile_y: int, tile_cache_dir: str | None
) -> Image.Image | None:
    cache_path: Path | None = None
    if tile_cache_dir:
        cache_path = Path(tile_cache_dir) / str(zoom) / str(tile_x) / f"{tile_y}.png"
        if cache_path.exists():
            try:
                return Image.open(cache_path).copy()
            except OSError:
                cache_path.unlink(missing_ok=True)

    request = Request(
        OSM_TILE_URL.format(z=zoom, x=tile_x, y=tile_y),
        headers={"User-Agent": OSM_USER_AGENT},
    )
    try:
        with urlopen(request, timeout=5) as response:
            tile_bytes = response.read()
    except (HTTPError, OSError, TimeoutError, URLError):
        return None

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(tile_bytes)

    try:
        return Image.open(BytesIO(tile_bytes)).copy()
    except OSError:
        return None


def _draw_geojson_feature(
    draw: ImageDraw.ImageDraw, feature: dict[str, Any], project
) -> None:
    geometry = feature.get("geometry") or {}
    properties = feature.get("properties") or {}
    geometry_type = geometry.get("type")
    type_name = str(properties.get("type_name", "")).lower()
    name = properties.get("Name") or properties.get("title")
    stroke, fill = _feature_colours(type_name, properties)

    if geometry_type == "Polygon":
        for ring in geometry.get("coordinates", []):
            polygon = [project((float(coord[0]), float(coord[1]))) for coord in ring]
            if len(polygon) >= 3:
                draw.polygon(polygon, fill=fill, outline=stroke)
                draw.line(polygon + [polygon[0]], fill=stroke, width=3, joint="curve")
        if name and type_name == "area":
            _draw_label(draw, str(name), _centroid(_geometry_points(geometry)), project)
    elif geometry_type == "MultiPolygon":
        for polygon_coordinates in geometry.get("coordinates", []):
            for ring in polygon_coordinates:
                polygon = [
                    project((float(coord[0]), float(coord[1]))) for coord in ring
                ]
                if len(polygon) >= 3:
                    draw.polygon(polygon, fill=fill, outline=stroke)
                    draw.line(
                        polygon + [polygon[0]], fill=stroke, width=3, joint="curve"
                    )
    elif geometry_type in {"LineString", "MultiLineString"}:
        lines = (
            [geometry.get("coordinates", [])]
            if geometry_type == "LineString"
            else geometry.get("coordinates", [])
        )
        for coordinates in lines:
            line = [
                project((float(coord[0]), float(coord[1]))) for coord in coordinates
            ]
            if len(line) >= 2:
                draw.line(line, fill=stroke, width=4, joint="curve")
    elif geometry_type == "Point":
        coord = geometry.get("coordinates", [])
        if len(coord) < 2:
            return
        center = project((float(coord[0]), float(coord[1])))
        radius = 8 if type_name == "station" else 6
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            fill=fill,
            outline=stroke,
            width=2,
        )
        if name:
            _draw_text(draw, str(name), (center[0] + 10, center[1] - 6))


def _draw_mower_marker(draw: ImageDraw.ImageDraw, center: tuple[float, float]) -> None:
    radius = 12
    draw.ellipse(
        (
            center[0] - radius - 3,
            center[1] - radius - 3,
            center[0] + radius + 3,
            center[1] + radius + 3,
        ),
        fill=(0, 0, 0, 70),
    )
    draw.ellipse(
        (
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ),
        fill=MOWER_FILL,
        outline=MOWER_STROKE,
        width=4,
    )
    draw.ellipse(
        (center[0] - 4, center[1] - 4, center[0] + 4, center[1] + 4),
        fill=(255, 255, 255, 255),
    )


def _draw_trail(
    draw: ImageDraw.ImageDraw,
    trail_points: list[tuple[float, float]],
    project,
) -> None:
    if len(trail_points) < 2:
        return
    line = [project(point) for point in trail_points]
    draw.line(line, fill=TRAIL_STROKE, width=5, joint="curve")
    recent = line[-min(len(line), 40) :]
    if len(recent) >= 2:
        draw.line(recent, fill=TRAIL_RECENT_STROKE, width=7, joint="curve")


def _feature_colours(
    type_name: str, properties: dict[str, Any]
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    if type_name == "area":
        return AREA_STROKE, AREA_FILL
    if type_name in {"obstacle", "visual_obstacle_zone", "visual_safety_zone"}:
        return OBSTACLE_STROKE, OBSTACLE_FILL
    if type_name == "path":
        return PATH_STROKE, (45, 45, 45, 120)
    if type_name == "trail":
        return TRAIL_RECENT_STROKE, TRAIL_STROKE
    if type_name == "station":
        colour = _parse_colour(properties.get("color"), (90, 78, 181, 255))
        return colour, (*colour[:3], 180)
    if type_name == "virtual_wall":
        return VIRTUAL_STROKE, VIRTUAL_STROKE
    stroke = _parse_colour(properties.get("color"), PATH_STROKE)
    fill_colour = _parse_colour(properties.get("fillColor"), (*stroke[:3], 85))
    return stroke, (*fill_colour[:3], min(fill_colour[3], 110))


def _parse_colour(
    value: Any, fallback: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    if not value:
        return fallback
    try:
        return ImageColor.getcolor(str(value), "RGBA")
    except ValueError:
        return fallback


def _draw_label(
    draw: ImageDraw.ImageDraw,
    text: str,
    coord: tuple[float, float] | None,
    project,
) -> None:
    if coord is not None:
        _draw_text(draw, text, project(coord))


def _draw_text(draw: ImageDraw.ImageDraw, text: str, xy: tuple[float, float]) -> None:
    text_bounds = draw.textbbox((0, 0), text)
    text_width = text_bounds[2] - text_bounds[0]
    text_height = text_bounds[3] - text_bounds[1]
    x, y = xy
    padding = 4
    draw.rounded_rectangle(
        (
            x - padding,
            y - padding,
            x + text_width + padding,
            y + text_height + padding,
        ),
        radius=5,
        fill=(255, 255, 255, 210),
        outline=(60, 60, 60, 90),
    )
    draw.text((x, y), text, fill=(30, 30, 30, 255))


def _geometry_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, dict):
        return []
    geometry_type = value.get("type")
    if geometry_type == "FeatureCollection":
        return [
            point
            for feature in value.get("features", [])
            for point in _geometry_points(feature)
        ]
    if geometry_type == "Feature":
        return _geometry_points(value.get("geometry") or {})
    if geometry_type == "GeometryCollection":
        return [
            point
            for geometry in value.get("geometries", [])
            for point in _geometry_points(geometry)
        ]
    return _coordinate_points(value.get("coordinates", []))


def _coordinate_points(coordinates: Any) -> list[tuple[float, float]]:
    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and isinstance(coordinates[0], int | float)
        and isinstance(coordinates[1], int | float)
    ):
        lon = float(coordinates[0])
        lat = float(coordinates[1])
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            return [(lon, lat)]
        return []
    if isinstance(coordinates, list):
        return [point for item in coordinates for point in _coordinate_points(item)]
    return []


def _geo_bounds(points: list[tuple[float, float]]) -> GeoBounds:
    return GeoBounds(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _lonlat_to_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    sin_lat = math.sin(math.radians(lat))
    world_size = OSM_TILE_SIZE * (2**zoom)
    return (
        (lon + 180.0) / 360.0 * world_size,
        (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi))
        * world_size,
    )


def _meters_to_lat_degrees(meters: float) -> float:
    return meters / 111_111.0


def _meters_to_lon_degrees(meters: float, lat: float) -> float:
    return meters / (111_111.0 * max(math.cos(math.radians(lat)), 0.01))


def _geo_location_point(location: Any | None) -> tuple[float, float] | None:
    if location is None:
        return None
    latitude = getattr(location, "latitude", None)
    longitude = getattr(location, "longitude", None)
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180 and (
        latitude != 0 or longitude != 0
    ):
        return longitude, latitude
    return None


def _valid_geo_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    valid_points: list[tuple[float, float]] = []
    for point in points:
        try:
            lon = float(point[0])
            lat = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if -180 <= lon <= 180 and -90 <= lat <= 90 and (lat != 0 or lon != 0):
            valid_points.append((lon, lat))
    return valid_points


def _encode(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
