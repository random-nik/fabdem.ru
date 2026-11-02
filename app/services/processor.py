from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.merge import merge as merge_rasters
from rasterio.warp import calculate_default_transform, reproject
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from app.config import Settings
from app.services.catalog import Tile
from app.services.geo import (
    NODATA,
    Bounds,
    area_km2,
    bbox_polygon,
    is_antimeridian,
    split_antimeridian,
)

ProgressCallback = Callable[[str, float], None]

CITATION = (
    "Hawker, L., Uhe, P., Paulo, L., Sosa, J., Savage, J., Sampson, C., & Neal, J. "
    "(2022). A 30m global map of elevation with forests and buildings removed. "
    "Environmental Research Letters. https://doi.org/10.1088/1748-9326/ac4d4f"
)
LICENCE_TEXT = (
    "FABDEM is licensed under CC BY-NC-SA 4.0. Non-commercial use only. "
    "FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 "
    "and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS "
    "by the European Union and ESA; all rights reserved."
)


@dataclass(frozen=True)
class ProcessOptions:
    crs: str = "EPSG:4326"
    mask: bool = True
    filename: str | None = None


@dataclass
class Estimate:
    tile_count: int
    area_km2: float
    pixels: int
    estimated_bytes: int
    within_limits: bool
    max_tiles: int
    warnings: list[str] = field(default_factory=list)


def _report(progress: ProgressCallback | None, stage: str, percent: float) -> None:
    if progress is not None:
        progress(stage, max(0.0, min(100.0, float(percent))))


def _output_size(bounds: Bounds) -> tuple[int, int]:
    """Approximate output size in pixels at FABDEM's 1 arc-second grid."""
    if is_antimeridian(bounds):
        width = sum(
            max(1, math.ceil((part[2] - part[0]) * 3600))
            for part in split_antimeridian(bounds)
        )
    else:
        width = max(1, math.ceil((bounds[2] - bounds[0]) * 3600))
    height = max(1, math.ceil((bounds[3] - bounds[1]) * 3600))
    return width, height


def estimate_area(
    bounds: Bounds,
    geometry: BaseGeometry | None,
    tiles: Sequence[Tile],
    settings: Settings,
) -> Estimate:
    if is_antimeridian(bounds):
        area = sum(area_km2(bbox_polygon(part)) for part in split_antimeridian(bounds))
    elif geometry is not None:
        area = area_km2(geometry)
    else:
        area = area_km2(bbox_polygon(bounds))

    width, height = _output_size(bounds)
    pixels = width * height
    # FABDEM is Float32; DEFLATE usually gives 25-40% of the raw size.
    estimated_bytes = max(1, int(pixels * 4 * 0.35))
    warnings: list[str] = []

    too_many_tiles = len(tiles) > settings.max_tiles
    too_large = estimated_bytes > settings.max_file_bytes
    if too_many_tiles:
        warnings.append(
            f"Область содержит {len(tiles)} плиток, лимит — {settings.max_tiles}. "
            "Уменьшите область или скачайте официальный архив 10°×10°."
        )
    if too_large:
        warnings.append(
            f"Оценочный размер файла ({estimated_bytes / 1024 / 1024:.0f} МБ) "
            f"превышает лимит {settings.max_file_bytes / 1024 / 1024:.0f} МБ."
        )
    if is_antimeridian(bounds):
        warnings.append(
            "Область пересекает 180-й меридиан. Результат будет собран из двух частей."
        )

    return Estimate(
        tile_count=len(tiles),
        area_km2=area,
        pixels=pixels,
        estimated_bytes=estimated_bytes,
        within_limits=not too_many_tiles and not too_large,
        max_tiles=settings.max_tiles,
        warnings=warnings,
    )


def _merge_tiles(tile_paths: Sequence[Path], bounds: Bounds) -> tuple[np.ndarray, object]:
    if not tile_paths:
        raise ValueError("Нет плиток для обработки")
    if is_antimeridian(bounds):
        raise ValueError("Область через 180-й меридиан нужно обрабатывать по частям")

    sources = [rasterio.open(path) for path in tile_paths]
    try:
        data, transform = merge_rasters(
            sources,
            bounds=bounds,
            nodata=NODATA,
            resampling=Resampling.nearest,
        )
    finally:
        for source in sources:
            source.close()
    if data.size == 0:
        raise ValueError("Не удалось собрать растровые данные для выбранной области")
    return data, transform


def _mask_geometry(data: np.ndarray, transform: object, geometry: BaseGeometry) -> np.ndarray:
    inside = geometry_mask(
        [mapping(geometry)],
        out_shape=(data.shape[1], data.shape[2]),
        transform=transform,
        invert=True,
        all_touched=False,
    )
    data = data.copy()
    data[0][~inside] = NODATA
    return data


def _target_crs(crs_value: str, bounds: Bounds) -> CRS:
    normalized = crs_value.strip().upper()
    if normalized in ("EPSG:4326", "WGS84", "WGS 84"):
        return CRS.from_epsg(4326)
    if normalized in ("EPSG:3857", "WEBMERCATOR", "WEB MERCATOR"):
        return CRS.from_epsg(3857)
    if normalized in ("UTM", "AUTO", "UTM-AUTO"):
        lon = (bounds[0] + bounds[2]) / 2
        lat = (bounds[1] + bounds[3]) / 2
        zone = int((lon + 180) / 6) + 1
        zone = max(1, min(60, zone))
        return CRS.from_dict({"proj": "utm", "zone": zone, "south": lat < 0})
    return CRS.from_user_input(crs_value)


def _reproject(
    data: np.ndarray,
    transform: object,
    bounds: Bounds,
    source_crs: CRS,
    target_crs: CRS,
) -> tuple[np.ndarray, object]:
    if target_crs.to_epsg() == source_crs.to_epsg():
        return data, transform

    height, width = data.shape[1], data.shape[2]
    dst_transform, dst_width, dst_height = calculate_default_transform(
        source_crs,
        target_crs,
        width,
        height,
        *bounds,
    )
    destination = np.full(
        (data.shape[0], dst_height, dst_width),
        NODATA,
        dtype=data.dtype,
    )
    reproject(
        source=data,
        destination=destination,
        src_transform=transform,
        src_crs=source_crs,
        src_nodata=NODATA,
        dst_transform=dst_transform,
        dst_crs=target_crs,
        dst_nodata=NODATA,
        resampling=Resampling.nearest,
    )
    return destination, dst_transform


def _write_geotiff(
    destination: Path,
    data: np.ndarray,
    transform: object,
    crs: CRS,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    height, width = data.shape[1], data.shape[2]
    tiled = width >= 512 and height >= 512
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": data.shape[0],
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": NODATA,
        "compress": "deflate",
        "predictor": 2,
        "tiled": tiled,
        "BIGTIFF": "IF_SAFER",
    }
    if tiled:
        profile.update(blockxsize=512, blockysize=512)

    with rasterio.open(destination, "w", **profile) as dst:
        dst.write(data.astype("float32", copy=False))
        dst.set_band_description(1, "Elevation (metres)")
        dst.update_tags(
            TITLE="FABDEM V1-2 clipped elevation",
            INSTITUTE="University of Bristol and Fathom",
            LICENCE=LICENCE_TEXT,
            SOURCE="https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn",
            FABDEM_VERSION="V1-2",
            CITATION=CITATION,
            AREA_OR_POINT="Point",
        )


def process_area(
    bounds: Bounds,
    geometry: BaseGeometry | None,
    tile_paths: Sequence[Path],
    output_path: Path,
    options: ProcessOptions,
    progress: ProgressCallback | None = None,
) -> Path:
    """Merge source tiles, crop to bounds, optionally mask and reproject."""
    _report(progress, "Склейка плиток", 20)
    data, transform = _merge_tiles(tile_paths, bounds)

    if options.mask and geometry is not None:
        _report(progress, "Маскирование по полигону", 55)
        data = _mask_geometry(data, transform, geometry)

    source_crs = CRS.from_epsg(4326)
    target_crs = _target_crs(options.crs, bounds)
    if target_crs.to_epsg() != source_crs.to_epsg():
        _report(progress, "Перепроецирование", 70)
        data, transform = _reproject(data, transform, bounds, source_crs, target_crs)

    _report(progress, "Запись GeoTIFF", 88)
    _write_geotiff(output_path, data, transform, target_crs)
    _report(progress, "Готово", 100)
    return output_path


def copy_tile(tile_path: Path, output_path: Path) -> Path:
    """Copy an original tile while preserving its georeferencing and tags."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(tile_path) as src:
        profile = src.profile.copy()
        profile.update(driver="GTiff")
        with rasterio.open(output_path, "w", **profile) as dst:
            dst.write(src.read())
            tags = src.tags()
            if tags:
                dst.update_tags(**tags)
            for band in src.indexes:
                description = src.descriptions[band - 1]
                if description:
                    dst.set_band_description(band, description)
    return output_path
