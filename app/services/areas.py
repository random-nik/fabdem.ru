from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.geometry.base import BaseGeometry

from app.config import Settings
from app.models import AreaRequest
from app.services.catalog import Tile, TileCatalog
from app.services.geo import (
    Bounds,
    geometry_from_geojson,
    validate_bounds,
)
from app.services.processor import Estimate, estimate_area


class AreaError(ValueError):
    """A user-facing problem with the requested area."""


@dataclass
class ResolvedArea:
    mode: str
    bounds: Bounds
    geometry: BaseGeometry | None
    tiles: list[Tile]
    estimate: Estimate
    tile: Tile | None
    archives: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "bounds": list(self.bounds),
            "tile": self.tile.name if self.tile else None,
            "tile_count": self.estimate.tile_count,
            "tiles": [
                {
                    "name": tile.name,
                    "bounds": list(tile.bounds),
                    "file_name": tile.file_name,
                }
                for tile in self.tiles
            ],
            "area_km2": round(self.estimate.area_km2, 3),
            "pixels": self.estimate.pixels,
            "estimated_bytes": self.estimate.estimated_bytes,
            "within_limits": self.estimate.within_limits,
            "max_tiles": self.estimate.max_tiles,
            "warnings": self.estimate.warnings,
            "archives": self.archives,
        }


def resolve_area(
    request: AreaRequest,
    catalog: TileCatalog,
    settings: Settings,
    *,
    enforce_limits: bool = True,
) -> ResolvedArea:
    tile: Tile | None = None
    geometry: BaseGeometry | None = None

    if request.mode == "tile":
        if not request.tile:
            raise AreaError("Укажите имя плитки, например N055E037")
        tile = catalog.get(request.tile)
        if tile is None:
            raise AreaError(f"Плитка {request.tile!r} не найдена в индексе FABDEM")
        bounds = tile.bounds
        tiles = [tile]

    elif request.mode == "bbox":
        if not request.bbox:
            raise AreaError("Укажите границы области: west, south, east, north")
        try:
            bounds = validate_bounds(request.bbox)
        except ValueError as exc:
            raise AreaError(str(exc)) from exc
        tiles = catalog.intersecting(bounds)

    elif request.mode == "polygon":
        if not request.geometry:
            raise AreaError("Передайте GeoJSON-полигон")
        try:
            geometry = geometry_from_geojson(request.geometry)
        except ValueError as exc:
            raise AreaError(str(exc)) from exc
        bounds = geometry.bounds  # type: ignore[assignment]
        tiles = catalog.intersecting(bounds)

    else:  # pragma: no cover - protected by Pydantic Literal
        raise AreaError(f"Неизвестный режим: {request.mode}")

    if not tiles:
        raise AreaError(
            "В выбранной области нет данных FABDEM. "
            "Возможно, это океан или территория вне покрытия."
        )

    estimate = estimate_area(bounds, geometry, tiles, settings)
    if enforce_limits and len(tiles) > settings.max_tiles:
        raise AreaError(
            f"Превышен лимит плиток: {len(tiles)} > {settings.max_tiles}. "
            "Уменьшите область или скачайте официальный архив 10°×10°."
        )
    if enforce_limits and estimate.estimated_bytes > settings.max_file_bytes:
        raise AreaError(
            f"Оценочный размер файла {estimate.estimated_bytes / 1024 / 1024:.0f} МБ "
            f"превышает лимит {settings.max_file_bytes / 1024 / 1024:.0f} МБ."
        )

    return ResolvedArea(
        mode=request.mode,
        bounds=bounds,
        geometry=geometry,
        tiles=tiles,
        estimate=estimate,
        tile=tile,
        archives=catalog.archives(tiles),
    )
