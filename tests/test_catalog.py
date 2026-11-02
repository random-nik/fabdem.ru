from __future__ import annotations

from app.services.catalog import Tile, TileCatalog, normalize_zip_name


def names(tiles: list[Tile]) -> set[str]:
    return {tile.name for tile in tiles}


def test_remote_file_name_is_corrected():
    tile = Tile(
        name="N005E001",
        file_name="N005E001_FABDEM_V1-2.tif",
        zipfile_name="N00E000-N10E010_FABDEM_V1-2.zip",
        west=1,
        south=5,
        east=2,
        north=6,
    )
    assert tile.remote_file_name == "N05E001_FABDEM_V1-2.tif"


def test_normalize_zip_name():
    assert (
        normalize_zip_name("S40W060-S-30W050_FABDEM_V1-2.zip")
        == "S40W060-S30W050_FABDEM_V1-2.zip"
    )
    assert (
        normalize_zip_name("N50E030-N60E040_FABDEM_V1-2.zip")
        == "N50E030-N60E040_FABDEM_V1-2.zip"
    )


def test_catalog_intersects_only_real_overlap(catalog: TileCatalog):
    tiles = catalog.intersecting((37, 55, 38, 56))
    assert names(tiles) == {"N055E037"}

    tiles = catalog.intersecting((37.1, 55.1, 37.9, 55.9))
    assert names(tiles) == {"N055E037"}

    tiles = catalog.intersecting((37, 55, 39, 56))
    assert names(tiles) == {"N055E037", "N055E038"}


def test_catalog_antimeridian(catalog: TileCatalog):
    tiles = catalog.intersecting((179, 66, -179, 67))
    assert names(tiles) == {"N066E179", "N066W180"}


def test_catalog_to_geojson_filter(catalog: TileCatalog):
    payload = catalog.to_geojson((37, 55, 39, 56))
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 2
    assert {f["properties"]["tile_name"] for f in payload["features"]} == {
        "N055E037",
        "N055E038",
    }


def test_catalog_get_and_archives(catalog: TileCatalog):
    tile = catalog.get("N055E037")
    assert tile is not None
    assert tile.bounds == (37.0, 55.0, 38.0, 56.0)
    assert catalog.get("missing") is None

    archives = catalog.archives([tile])
    assert len(archives) == 1
    assert archives[0]["name"].endswith(".zip")
    assert archives[0]["url"].startswith("https://")


def test_catalog_rejects_invalid_feature():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"tile_name": "bad"},
                "geometry": {"type": "Polygon", "coordinates": []},
            }
        ],
    }
    import pytest

    with pytest.raises(ValueError):
        TileCatalog.from_geojson(payload)
