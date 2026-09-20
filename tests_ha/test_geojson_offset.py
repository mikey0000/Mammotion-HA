"""Unit tests for apply_geojson_offset and helpers in geojson_utils.py."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from custom_components.mammotion.geojson_utils import (
    apply_coord,
    apply_geojson_offset,
    offset_geometry,
)

GEOJSON_PATH = Path(__file__).parent / "fixtures" / "mow_progress.geojson"

METERS_PER_DEGREE = 111_111.0


@pytest.fixture
def mow_progress_geojson() -> dict[str, Any]:
    """Load the synthetic mow-progress sample bundled with this repository."""
    return json.loads(GEOJSON_PATH.read_text())


# ---------------------------------------------------------------------------
# apply_coord
# ---------------------------------------------------------------------------


class TestApplyCoord:
    """Shifting a single coordinate pair by a metre offset."""

    def test_zero_offset_is_identity(self):
        """A zero offset leaves the coordinate exactly as it was."""
        c = [175.318, -38.002]
        assert apply_coord(c, c[1], 0.0, 0.0) == c

    def test_positive_lat_offset_moves_north(self):
        """A positive latitude offset raises the latitude."""
        c = [175.0, -38.0]
        result = apply_coord(c, c[1], 10.0, 0.0)
        assert result[1] > c[1]  # latitude increased (less negative = more north)

    def test_negative_lat_offset_moves_south(self):
        """A negative latitude offset lowers the latitude."""
        c = [175.0, -38.0]
        result = apply_coord(c, c[1], -10.0, 0.0)
        assert result[1] < c[1]

    def test_positive_lon_offset_moves_east(self):
        """A positive longitude offset raises the longitude."""
        c = [175.0, -38.0]
        result = apply_coord(c, c[1], 0.0, 10.0)
        assert result[0] > c[0]

    def test_lat_offset_magnitude(self):
        """A metre of latitude is the same distance wherever you are."""
        c = [175.0, 0.0]  # equator — simple case
        result = apply_coord(c, c[1], METERS_PER_DEGREE, 0.0)
        assert result[1] == pytest.approx(1.0, rel=1e-6)

    def test_lon_offset_magnitude_at_equator(self):
        """A metre of longitude matches a metre of latitude at the equator."""
        c = [0.0, 0.0]
        result = apply_coord(c, c[1], 0.0, METERS_PER_DEGREE)
        assert result[0] == pytest.approx(1.0, rel=1e-6)

    def test_lon_offset_cos_correction(self):
        """Longitude offset in metres must produce a larger degree shift at higher latitudes."""
        c_equator = [0.0, 0.0]
        c_60 = [0.0, 60.0]
        delta_equator = apply_coord(c_equator, c_equator[1], 0.0, 100.0)[0]
        delta_60 = apply_coord(c_60, c_60[1], 0.0, 100.0)[0]
        assert delta_60 > delta_equator

    def test_preserves_z_coordinate(self):
        """An altitude third element survives the shift untouched."""
        c = [175.0, -38.0, 42.0]
        result = apply_coord(c, c[1], 5.0, 5.0)
        assert result[2] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# offset_geometry
# ---------------------------------------------------------------------------


class TestOffsetGeometry:
    """Shifting each GeoJSON geometry type by a metre offset."""

    def test_none_returns_none(self):
        """A missing geometry is passed straight through."""
        assert offset_geometry(None, 10.0, 10.0) is None

    def test_empty_dict_returns_empty(self):
        """An empty geometry is passed straight through."""
        assert offset_geometry({}, 10.0, 10.0) == {}

    def test_unknown_type_returned_unchanged(self):
        """An unrecognised geometry type is left alone rather than mangled."""
        g = {"type": "Triangle", "coordinates": []}
        assert offset_geometry(g, 10.0, 10.0) == g

    def test_point(self):
        """A Point's single coordinate is shifted."""
        g = {"type": "Point", "coordinates": [175.0, -38.0]}
        result = offset_geometry(g, 10.0, 0.0)
        assert result["coordinates"][1] == pytest.approx(
            -38.0 + 10.0 / METERS_PER_DEGREE, rel=1e-9
        )
        assert result["coordinates"][0] == pytest.approx(175.0, rel=1e-9)

    def test_linestring(self):
        """Every vertex of a LineString is shifted."""
        g = {"type": "LineString", "coordinates": [[175.0, -38.0], [175.1, -38.1]]}
        result = offset_geometry(g, 0.0, 10.0)
        for orig, shifted in zip(g["coordinates"], result["coordinates"], strict=True):
            cos_lat = math.cos(math.radians(orig[1]))
            assert shifted[0] == pytest.approx(
                orig[0] + 10.0 / (METERS_PER_DEGREE * cos_lat), rel=1e-9
            )
            assert shifted[1] == pytest.approx(orig[1], rel=1e-9)

    def test_polygon(self):
        """Every ring of a Polygon is shifted."""
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]
        g = {"type": "Polygon", "coordinates": [ring]}
        result = offset_geometry(g, 10.0, 0.0)
        assert len(result["coordinates"]) == 1
        assert len(result["coordinates"][0]) == len(ring)
        for shifted in result["coordinates"][0]:
            assert shifted[1] > 0.0

    def test_multipolygon(self):
        """Every polygon of a MultiPolygon is shifted."""
        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]
        g = {"type": "MultiPolygon", "coordinates": [[ring]]}
        result = offset_geometry(g, 10.0, 10.0)
        assert result["type"] == "MultiPolygon"
        assert len(result["coordinates"][0][0]) == len(ring)

    def test_multilinestring(self):
        """Every line of a MultiLineString is shifted."""
        g = {
            "type": "MultiLineString",
            "coordinates": [[[0.0, 0.0], [1.0, 0.0]], [[2.0, 0.0], [3.0, 0.0]]],
        }
        result = offset_geometry(g, 10.0, 0.0)
        assert result["type"] == "MultiLineString"
        assert len(result["coordinates"]) == 2

    def test_geometry_collection(self):
        """Each member of a GeometryCollection is shifted in turn."""
        g = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [175.0, -38.0]},
                {"type": "LineString", "coordinates": [[175.0, -38.0], [175.1, -38.1]]},
            ],
        }
        result = offset_geometry(g, 10.0, 0.0)
        assert result["type"] == "GeometryCollection"
        assert len(result["geometries"]) == 2
        assert (
            result["geometries"][0]["coordinates"][1]
            != g["geometries"][0]["coordinates"][1]
        )

    def test_original_not_mutated(self):
        """The input geometry is copied, not shifted in place."""
        g = {"type": "Point", "coordinates": [175.0, -38.0]}
        original_coords = g["coordinates"][:]
        offset_geometry(g, 10.0, 10.0)
        assert g["coordinates"] == original_coords


# ---------------------------------------------------------------------------
# apply_geojson_offset — top-level dispatch
# ---------------------------------------------------------------------------


class TestApplyGeojsonOffset:
    """Shifting a whole GeoJSON document, whatever shape it arrives in."""

    def test_zero_offsets_returns_same_object(self, mow_progress_geojson):
        """A zero offset short-circuits instead of rebuilding the document."""
        result = apply_geojson_offset(mow_progress_geojson, 0.0, 0.0)
        assert result is mow_progress_geojson

    def test_feature_collection_structure_preserved(self, mow_progress_geojson):
        """A FeatureCollection keeps its type and its feature count."""
        result = apply_geojson_offset(mow_progress_geojson, 10.0, 10.0)
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == len(mow_progress_geojson["features"])

    def test_feature_collection_coords_shifted(self, mow_progress_geojson):
        """Every feature in a collection is shifted."""
        result = apply_geojson_offset(mow_progress_geojson, 10.0, 0.0)
        for orig_feat, shifted_feat in zip(
            mow_progress_geojson["features"], result["features"], strict=True
        ):
            for o, s in zip(
                orig_feat["geometry"]["coordinates"],
                shifted_feat["geometry"]["coordinates"],
                strict=True,
            ):
                assert s[1] == pytest.approx(o[1] + 10.0 / METERS_PER_DEGREE, rel=1e-9)

    def test_properties_preserved(self, mow_progress_geojson):
        """Feature properties ride along untouched."""
        result = apply_geojson_offset(mow_progress_geojson, 10.0, 5.0)
        for orig, shifted in zip(
            mow_progress_geojson["features"], result["features"], strict=True
        ):
            assert shifted["properties"] == orig["properties"]

    def test_original_not_mutated(self, mow_progress_geojson):
        """The input document is copied, not shifted in place."""
        first_coord_before = mow_progress_geojson["features"][0]["geometry"][
            "coordinates"
        ][0][:]
        apply_geojson_offset(mow_progress_geojson, 13.8, -13.3)
        first_coord_after = mow_progress_geojson["features"][0]["geometry"][
            "coordinates"
        ][0]
        assert first_coord_after == first_coord_before

    def test_bare_feature(self):
        """A lone Feature is handled without a wrapping collection."""
        f = {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [175.0, -38.0]},
        }
        result = apply_geojson_offset(f, 10.0, 0.0)
        assert result["type"] == "Feature"
        assert result["geometry"]["coordinates"][1] == pytest.approx(
            -38.0 + 10.0 / METERS_PER_DEGREE, rel=1e-9
        )

    def test_bare_geometry(self):
        """A lone geometry is handled without a wrapping Feature."""
        g = {"type": "Point", "coordinates": [175.0, -38.0]}
        result = apply_geojson_offset(g, 10.0, 0.0)
        assert result["coordinates"][1] == pytest.approx(
            -38.0 + 10.0 / METERS_PER_DEGREE, rel=1e-9
        )

    def test_sample_all_features_shifted(self, mow_progress_geojson):
        """Every coordinate in each sample LineString must shift correctly."""
        lat_offset_m = -13.8
        lon_offset_m = -13.3
        result = apply_geojson_offset(mow_progress_geojson, lat_offset_m, lon_offset_m)
        for orig_feat, shifted_feat in zip(
            mow_progress_geojson["features"], result["features"], strict=True
        ):
            for o, s in zip(
                orig_feat["geometry"]["coordinates"],
                shifted_feat["geometry"]["coordinates"],
                strict=True,
            ):
                cos_lat = math.cos(math.radians(o[1]))
                assert s[1] == pytest.approx(
                    o[1] + lat_offset_m / METERS_PER_DEGREE, rel=1e-9
                )
                assert s[0] == pytest.approx(
                    o[0] + lon_offset_m / (METERS_PER_DEGREE * cos_lat), rel=1e-9
                )
