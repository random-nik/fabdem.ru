from __future__ import annotations

import pytest
from shapely.geometry import box

from app.services.geo import (
    area_km2,
    bbox_polygon,
    geometry_from_geojson,
    is_antimeridian,
    split_antimeridian,
    tile_bounds_from_name,
    tile_name,
    validate_bounds,
)


def test_tile_name_positive_and_negative():
    assert tile_name(55.2, 37.1) == "N055E037"
    assert tile_name(-0.2, -0.2) == "S001W001"
    assert tile_name(0.0, 0.0) == "N000E000"
    assert tile_name(-34.5, -59.2) == "S035W060"


def test_tile_bounds_from_name():
    assert tile_bounds_from_name("N055E037") == (37.0, 55.0, 38.0, 56.0)
    assert tile_bounds_from_name("S034W059") == (-59.0, -34.0, -58.0, -33.0)
    assert tile_bounds_from_name("N000E000") == (0.0, 0.0, 1.0, 1.0)


def test_validate_bounds():
    assert validate_bounds([37, 55, 38, 56]) == (37.0, 55.0, 38.0, 56.0)
    assert validate_bounds([179, 65, -179, 67]) == (179.0, 65.0, -179.0, 67.0)
    with pytest.raises(ValueError):
        validate_bounds([37, 56, 38, 55])
    with pytest.raises(ValueError):
        validate_bounds([37, 55, 38])
    with pytest.raises(ValueError):
        validate_bounds([37, 55, 200, 56])


def test_antimeridian_helpers():
    assert is_antimeridian((179, 65, -179, 67)) is True
    assert is_antimeridian((37, 55, 38, 56)) is False
    assert split_antimeridian((179, 65, -179, 67)) == [
        (179.0, 65.0, 180.0, 67.0),
        (-180.0, 65.0, -179.0, 67.0),
    ]
    assert split_antimeridian((37, 55, 38, 56)) == [(37.0, 55.0, 38.0, 56.0)]


def test_geometry_from_geojson():
    geom = geometry_from_geojson(
        {
            "type": "Polygon",
            "coordinates": [
                [[37, 55], [38, 55], [38, 56], [37, 56], [37, 55]]
            ],
        }
    )
    assert geom.geom_type == "Polygon"
    assert geom.bounds == (37.0, 55.0, 38.0, 56.0)

    multi = geometry_from_geojson(
        {
            "type": "MultiPolygon",
            "coordinates": [
                [[[37, 55], [38, 55], [38, 56], [37, 56], [37, 55]]],
                [[[39, 55], [40, 55], [40, 56], [39, 56], [39, 55]]],
            ],
        }
    )
    assert multi.geom_type == "MultiPolygon"

    with pytest.raises(ValueError):
        geometry_from_geojson({"type": "Point", "coordinates": [37, 55]})


def test_area_km2():
    value = area_km2(bbox_polygon((37, 55, 38, 56)))
    assert 6_000 < value < 9_000
    assert area_km2(box(37, 55, 38, 56)) == pytest.approx(value, rel=1e-6)
