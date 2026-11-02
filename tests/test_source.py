from __future__ import annotations

import os
from pathlib import Path

import pytest
import rasterio

from app.config import Settings
from app.services.catalog import Tile
from app.services.source import LocalTileSource, RemoteZipSource, remote_vsi_path


def test_remote_vsi_path_normalizes_zip_name(settings: Settings, tile: Tile):
    path = remote_vsi_path(tile, settings.source_base_url)

    assert path.startswith("/vsizip//vsicurl/")
    assert "S40W060-S30W050_FABDEM_V1-2.zip" in path
    assert "S34W059_FABDEM_V1-2.tif" in path
    assert "-S-30" not in path


def test_local_source(tiles_dir: dict[str, Path]):
    source = LocalTileSource(tiles_dir["N055E037"].parent)
    tile = Tile(
        name="N055E037",
        file_name="N055E037_FABDEM_V1-2.tif",
        zipfile_name="N50E030-N60E040_FABDEM_V1-2.zip",
        west=37,
        south=55,
        east=38,
        north=56,
    )
    assert source.ensure_tile(tile) == tiles_dir["N055E037"]


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1",
    reason="set RUN_NETWORK_TESTS=1 to test the real FABDEM source",
)
def test_remote_source_small_tile(settings: Settings):
    tile = Tile(
        name="N005E001",
        file_name="N005E001_FABDEM_V1-2.tif",
        zipfile_name="N00E000-N10E010_FABDEM_V1-2.zip",
        west=1,
        south=5,
        east=2,
        north=6,
    )
    source = RemoteZipSource(settings)
    path = source.ensure_tile(tile)

    assert path.exists()
    with rasterio.open(path) as ds:
        assert ds.width == 3600
        assert ds.height == 3600
        assert ds.crs.to_epsg() == 4326
        assert ds.nodata == pytest.approx(-9999.0)
