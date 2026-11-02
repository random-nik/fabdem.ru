from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.config import Settings
from app.services.catalog import Tile, TileCatalog
from app.services.geo import NODATA, tile_name


def write_test_tile(
    path: Path,
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    offset: int = 0,
    width: int = 10,
    height: int = 10,
) -> Path:
    """Create a small synthetic GeoTIFF with predictable values."""
    transform = from_origin(west, north, (east - west) / width, (north - south) / height)
    row = np.arange(width, dtype="float32") + offset
    data = np.tile(row, (height, 1))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=NODATA,
    ) as dst:
        dst.write(data, 1)
        dst.update_tags(TITLE="Synthetic test tile", INSTITUTE="tests")
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    result = Settings(base_dir=tmp_path)
    result.ensure_dirs()
    return result


@pytest.fixture
def catalog() -> TileCatalog:
    features = []
    for lat in (55, 66):
        for lon in (37, 38):
            name = tile_name(lat, lon)
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "tile_name": name,
                        "file_name": f"{name}_FABDEM_V1-2.tif",
                        "zipfile_name": "N50E030-N60E040_FABDEM_V1-2.zip",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [float(lon), float(lat)],
                                [float(lon), float(lat + 1)],
                                [float(lon + 1), float(lat + 1)],
                                [float(lon + 1), float(lat)],
                                [float(lon), float(lat)],
                            ]
                        ],
                    },
                }
            )

    # Tiles on both sides of the antimeridian.
    for lon in (179, -180):
        name = tile_name(66, lon)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "tile_name": name,
                    "file_name": f"{name}_FABDEM_V1-2.tif",
                    "zipfile_name": "N60E170-N70E180_FABDEM_V1-2.zip",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [float(lon), 66.0],
                            [float(lon), 67.0],
                            [float(lon + 1), 67.0],
                            [float(lon + 1), 66.0],
                            [float(lon), 66.0],
                        ]
                    ],
                },
            }
        )
    return TileCatalog.from_geojson({"type": "FeatureCollection", "features": features})


@pytest.fixture
def tiles_dir(tmp_path: Path) -> dict[str, Path]:
    directory = tmp_path / "source_tiles"
    directory.mkdir()
    paths = {
        "N055E037": write_test_tile(
            directory / "N055E037_FABDEM_V1-2.tif", 37, 55, 38, 56, offset=0
        ),
        "N055E038": write_test_tile(
            directory / "N055E038_FABDEM_V1-2.tif", 38, 55, 39, 56, offset=100
        ),
        "N066E179": write_test_tile(
            directory / "N066E179_FABDEM_V1-2.tif", 179, 66, 180, 67, offset=0
        ),
        "N066W180": write_test_tile(
            directory / "N066W180_FABDEM_V1-2.tif", -180, 66, -179, 67, offset=100
        ),
    }
    return paths


@pytest.fixture
def tile() -> Tile:
    return Tile(
        name="S034W059",
        file_name="S034W059_FABDEM_V1-2.tif",
        zipfile_name="S40W060-S-30W050_FABDEM_V1-2.zip",
        west=-59.0,
        south=-34.0,
        east=-58.0,
        north=-33.0,
    )
