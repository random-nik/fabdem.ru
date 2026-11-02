from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import box
from shapely.strtree import STRtree

from app.config import OFFICIAL_BASE_URL, Settings
from app.services.geo import Bounds, tile_bounds_from_name, validate_bounds


def normalize_zip_name(name: str) -> str:
    """Fix a known typo in the official tile index for southern latitudes.

    The index contains names like ``S40W060-S-30W050_FABDEM_V1-2.zip`` while
    the actual archive is named ``S40W060-S30W050_FABDEM_V1-2.zip``.
    """
    return name.replace("N-", "N").replace("S-", "S")


@dataclass(frozen=True)
class Tile:
    name: str
    file_name: str
    zipfile_name: str
    west: float
    south: float
    east: float
    north: float

    @property
    def bounds(self) -> Bounds:
        return (self.west, self.south, self.east, self.north)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.west + self.east) / 2, (self.south + self.north) / 2)

    @property
    def zip_name_normalized(self) -> str:
        return normalize_zip_name(self.zipfile_name)

    @property
    def remote_file_name(self) -> str:
        """Name of the member inside the official ZIP.

        The public index is zero-padded to three latitude digits (``N005E001``),
        while the archive stores two digits (``N05E001``). Longitude keeps three
        digits. This is a known inconsistency in the published dataset.
        """
        if len(self.file_name) > 1 and self.file_name[1] == "0":
            return self.file_name[0] + self.file_name[2:]
        return self.file_name

    def archive_url(self, base_url: str = OFFICIAL_BASE_URL) -> str:
        return f"{base_url.rstrip('/')}/{self.zip_name_normalized}"


def _positive_overlap(a: Bounds, b: Bounds) -> bool:
    overlap_x = min(a[2], b[2]) - max(a[0], b[0])
    overlap_y = min(a[3], b[3]) - max(a[1], b[1])
    return overlap_x > 1e-10 and overlap_y > 1e-10


def _tile_from_feature(feature: dict[str, Any], index: int) -> Tile:
    if not isinstance(feature, dict):
        raise ValueError(f"Некорректная плитка #{index}: feature не является объектом")
    properties = feature.get("properties") or {}
    name = properties.get("tile_name")
    if not name:
        raise ValueError(f"Некорректная плитка #{index}: нет tile_name")
    try:
        west, south, east, north = tile_bounds_from_name(str(name))
    except ValueError as exc:
        raise ValueError(f"Некорректная плитка {name!r}: {exc}") from exc

    geometry = feature.get("geometry")
    if geometry and geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"Некорректная геометрия плитки {name!r}")

    file_name = properties.get("file_name") or f"{name}_FABDEM_V1-2.tif"
    zipfile_name = properties.get("zipfile_name")
    if not zipfile_name:
        raise ValueError(f"Некорректная плитка {name!r}: нет zipfile_name")

    return Tile(
        name=str(name),
        file_name=str(file_name),
        zipfile_name=str(zipfile_name),
        west=west,
        south=south,
        east=east,
        north=north,
    )


class TileCatalog:
    """In-memory index of the 1x1 degree FABDEM tiles."""

    def __init__(self, tiles: list[Tile], base_url: str = OFFICIAL_BASE_URL):
        if not tiles:
            raise ValueError("Индекс плиток пуст")
        names = [tile.name for tile in tiles]
        if len(names) != len(set(names)):
            raise ValueError("В индексе плиток есть дубликаты")
        self.tiles = tiles
        self.base_url = base_url.rstrip("/")
        self._by_name = {tile.name: tile for tile in tiles}
        self._geometries = [box(*tile.bounds) for tile in tiles]
        self._tree = STRtree(self._geometries)

    @classmethod
    def from_geojson(
        cls,
        payload: dict[str, Any],
        base_url: str = OFFICIAL_BASE_URL,
    ) -> "TileCatalog":
        if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
            raise ValueError("Ожидается GeoJSON FeatureCollection с плитками")
        features = payload.get("features") or []
        if not features:
            raise ValueError("GeoJSON не содержит плиток")
        tiles = [
            _tile_from_feature(feature, index)
            for index, feature in enumerate(features)
        ]
        return cls(tiles, base_url=base_url)

    def __len__(self) -> int:
        return len(self.tiles)

    def get(self, name: str) -> Tile | None:
        return self._by_name.get(name)

    def intersecting(self, bounds: Bounds | list[float] | tuple[float, ...]) -> list[Tile]:
        checked = validate_bounds(bounds)
        found: dict[str, Tile] = {}
        for part in (
            [checked]
            if checked[0] <= checked[2]
            else [(checked[0], checked[1], 180.0, checked[3]), (-180.0, checked[1], checked[2], checked[3])]
        ):
            query_box = box(*part)
            for index in self._tree.query(query_box):
                tile = self.tiles[int(index)]
                if tile.name in found:
                    continue
                if _positive_overlap(tile.bounds, part):
                    found[tile.name] = tile
        return sorted(found.values(), key=lambda item: item.name)

    def to_geojson(self, bounds: Bounds | list[float] | tuple[float, ...] | None = None) -> dict[str, Any]:
        tiles = self.tiles if bounds is None else self.intersecting(bounds)
        features = []
        for tile in tiles:
            west, south, east, north = tile.bounds
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "tile_name": tile.name,
                        "file_name": tile.file_name,
                        "zipfile_name": tile.zipfile_name,
                        "center": list(tile.center),
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [west, south],
                                [west, north],
                                [east, north],
                                [east, south],
                                [west, south],
                            ]
                        ],
                    },
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def archives(self, tiles: list[Tile]) -> list[dict[str, str]]:
        unique: dict[str, Tile] = {}
        for tile in tiles:
            unique.setdefault(tile.zip_name_normalized, tile)
        return [
            {
                "name": name,
                "url": tile.archive_url(self.base_url),
            }
            for name, tile in sorted(unique.items())
        ]


def load_catalog(settings: Settings) -> TileCatalog:
    """Load the tile index from disk, downloading it once if needed."""
    path = Path(settings.catalog_cache_path)
    if path.exists() and path.stat().st_size > 0:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = httpx.get(
                settings.catalog_url,
                timeout=120.0,
                follow_redirects=True,
                headers={"User-Agent": "fabdem-local-downloader/0.1"},
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - show a useful startup error
            raise RuntimeError(
                "Не удалось загрузить индекс плиток FABDEM. "
                "Проверьте интернет или укажите локальный файл через FABDEM_TILE_INDEX_URL."
            ) from exc
        temp_path = path.with_suffix(".part")
        temp_path.write_bytes(response.content)
        temp_path.replace(path)
        payload = response.json()

    try:
        return TileCatalog.from_geojson(payload, base_url=settings.source_base_url)
    except ValueError as exc:
        raise RuntimeError(f"Некорректный индекс плиток FABDEM: {exc}") from exc
