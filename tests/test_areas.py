from __future__ import annotations

import pytest

from app.config import Settings
from app.models import AreaRequest
from app.services.areas import AreaError, resolve_area
from app.services.catalog import TileCatalog


def test_resolve_bbox(catalog: TileCatalog, settings: Settings):
    area = resolve_area(
        AreaRequest(mode="bbox", bbox=[37, 55, 39, 56]),
        catalog,
        settings,
    )
    assert area.mode == "bbox"
    assert area.bounds == (37.0, 55.0, 39.0, 56.0)
    assert {tile.name for tile in area.tiles} == {"N055E037", "N055E038"}
    assert area.estimate.tile_count == 2
    assert area.tile is None


def test_resolve_tile(catalog: TileCatalog, settings: Settings):
    area = resolve_area(
        AreaRequest(mode="tile", tile="N055E037"),
        catalog,
        settings,
    )
    assert area.mode == "tile"
    assert area.tile is not None
    assert area.tile.name == "N055E037"
    assert area.bounds == (37.0, 55.0, 38.0, 56.0)
    assert len(area.tiles) == 1


def test_resolve_polygon(catalog: TileCatalog, settings: Settings):
    area = resolve_area(
        AreaRequest(
            mode="polygon",
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [[37.1, 55.1], [37.9, 55.1], [37.9, 55.9], [37.1, 55.9], [37.1, 55.1]]
                ],
            },
            mask=True,
        ),
        catalog,
        settings,
    )
    assert area.mode == "polygon"
    assert area.geometry is not None
    assert area.geometry.geom_type == "Polygon"
    assert area.estimate.tile_count == 1


def test_resolve_bbox_without_data(catalog: TileCatalog, settings: Settings):
    with pytest.raises(AreaError, match="данн|покрыт"):
        resolve_area(
            AreaRequest(mode="bbox", bbox=[0, 0, 1, 1]),
            catalog,
            settings,
        )


def test_resolve_bbox_over_limit(catalog: TileCatalog, settings: Settings):
    settings.max_tiles = 1
    with pytest.raises(AreaError, match="лимит|плит"):
        resolve_area(
            AreaRequest(mode="bbox", bbox=[37, 55, 39, 56]),
            catalog,
            settings,
        )


def test_resolve_rejects_bad_request(catalog: TileCatalog, settings: Settings):
    with pytest.raises(AreaError):
        resolve_area(AreaRequest(mode="bbox"), catalog, settings)

    with pytest.raises(AreaError):
        resolve_area(AreaRequest(mode="polygon"), catalog, settings)

    with pytest.raises(AreaError):
        resolve_area(AreaRequest(mode="tile", tile="missing"), catalog, settings)
