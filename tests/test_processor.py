from __future__ import annotations

from pathlib import Path

import pytest
import rasterio
from shapely.geometry import box

from app.config import Settings
from app.services.catalog import TileCatalog
from app.services.geo import NODATA
from app.services.processor import (
    ProcessOptions,
    copy_tile,
    estimate_area,
    process_area,
)


def test_estimate_area(catalog: TileCatalog, settings: Settings):
    tiles = catalog.intersecting((37, 55, 39, 56))
    estimate = estimate_area((37, 55, 39, 56), None, tiles, settings)

    assert estimate.tile_count == 2
    assert estimate.area_km2 > 0
    assert estimate.pixels > 0
    assert estimate.estimated_bytes > 0
    assert estimate.within_limits is True
    assert estimate.max_tiles == settings.max_tiles


def test_process_area_merges_and_crops(
    catalog: TileCatalog, tiles_dir: dict[str, Path], tmp_path: Path
):
    output = tmp_path / "merged.tif"
    bounds = (37, 55, 39, 56)
    tile_paths = [tiles_dir["N055E037"], tiles_dir["N055E038"]]

    process_area(
        bounds=bounds,
        geometry=None,
        tile_paths=tile_paths,
        output_path=output,
        options=ProcessOptions(crs="EPSG:4326"),
    )

    with rasterio.open(output) as ds:
        assert ds.width == 20
        assert ds.height == 10
        assert ds.crs.to_epsg() == 4326
        assert ds.nodata == pytest.approx(NODATA)
        data = ds.read(1)
        assert data[0, 0] == pytest.approx(0)
        assert data[0, 9] == pytest.approx(9)
        assert data[0, 10] == pytest.approx(100)
        assert data[0, 19] == pytest.approx(109)


def test_process_area_masks_outside_polygon(
    tiles_dir: dict[str, Path], tmp_path: Path
):
    output = tmp_path / "masked.tif"
    geometry = box(37, 55, 38, 56)
    process_area(
        bounds=(37, 55, 39, 56),
        geometry=geometry,
        tile_paths=[tiles_dir["N055E037"], tiles_dir["N055E038"]],
        output_path=output,
        options=ProcessOptions(crs="EPSG:4326", mask=True),
    )

    with rasterio.open(output) as ds:
        data = ds.read(1)
        assert data[0, 0] == pytest.approx(0)
        assert data[0, 9] == pytest.approx(9)
        assert data[0, 10] == pytest.approx(NODATA)
        assert data[0, 19] == pytest.approx(NODATA)


def test_process_area_reprojects(tiles_dir: dict[str, Path], tmp_path: Path):
    output = tmp_path / "reprojected.tif"
    process_area(
        bounds=(37, 55, 39, 56),
        geometry=None,
        tile_paths=[tiles_dir["N055E037"], tiles_dir["N055E038"]],
        output_path=output,
        options=ProcessOptions(crs="EPSG:3857"),
    )

    with rasterio.open(output) as ds:
        assert ds.crs.to_epsg() == 3857
        assert ds.width > 0
        assert ds.height > 0
        assert ds.nodata == pytest.approx(NODATA)


def test_process_area_reprojects_to_utm(tiles_dir: dict[str, Path], tmp_path: Path):
    output = tmp_path / "utm.tif"
    process_area(
        bounds=(37, 55, 39, 56),
        geometry=None,
        tile_paths=[tiles_dir["N055E037"], tiles_dir["N055E038"]],
        output_path=output,
        options=ProcessOptions(crs="utm"),
    )

    with rasterio.open(output) as ds:
        assert ds.crs.to_dict()["proj"] == "utm"
        assert ds.crs.to_dict()["zone"] == 37
        assert ds.width > 0
        assert ds.height > 0


def test_copy_tile(tiles_dir: dict[str, Path], tmp_path: Path):
    output = tmp_path / "tile.tif"
    copy_tile(tiles_dir["N055E037"], output)

    with rasterio.open(output) as ds:
        assert ds.width == 10
        assert ds.height == 10
        assert ds.tags().get("TITLE") == "Synthetic test tile"
        assert ds.read(1)[0, 0] == pytest.approx(0)
