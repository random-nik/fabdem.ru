from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

from pyproj import Geod
from shapely import force_2d, make_valid, union_all
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry

NODATA = -9999.0
WGS84 = "EPSG:4326"
GEOD = Geod(ellps="WGS84")

Bounds = tuple[float, float, float, float]
_TILE_RE = re.compile(r"^([NS])(\d{3})([EW])(\d{3})$")


def tile_name(lat: float, lon: float) -> str:
    """Return the FABDEM tile name for a coordinate (south-west corner)."""
    lat_floor = math.floor(float(lat))
    lon_floor = math.floor(float(lon))
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):03d}{ew}{abs(lon_floor):03d}"


def tile_bounds_from_name(name: str) -> Bounds:
    """Parse a FABDEM tile name into its nominal 1x1 degree bounds."""
    match = _TILE_RE.match(name)
    if not match:
        raise ValueError(f"Некорректное имя плитки: {name!r}")
    ns, lat_text, ew, lon_text = match.groups()
    lat = int(lat_text)
    lon = int(lon_text)
    if ns == "S":
        lat = -lat
    if ew == "W":
        lon = -lon
    return (float(lon), float(lat), float(lon + 1), float(lat + 1))


def validate_bounds(bounds: Sequence[float]) -> Bounds:
    """Validate a (west, south, east, north) bbox in EPSG:4326.

    A bbox with ``west > east`` is accepted and means a bbox crossing the
    antimeridian. It is split later, when needed.
    """
    try:
        values = tuple(float(value) for value in bounds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Границы должны состоять из четырёх чисел") from exc
    if len(values) != 4:
        raise ValueError("Границы должны состоять из четырёх чисел: west, south, east, north")
    west, south, east, north = values
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Границы содержат нечисловое значение")
    if not (-90 <= south < north <= 90):
        raise ValueError("Широта должна быть в диапазоне -90..90, south < north")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("Долгота должна быть в диапазоне -180..180")
    if west == east:
        raise ValueError("Западная и восточная границы не должны совпадать")
    return (west, south, east, north)


def is_antimeridian(bounds: Bounds) -> bool:
    return bounds[0] > bounds[2]


def split_antimeridian(bounds: Bounds) -> list[Bounds]:
    """Split a bbox crossing 180 degrees into two ordinary bboxes."""
    west, south, east, north = bounds
    if west <= east:
        return [bounds]
    return [
        (west, south, 180.0, north),
        (-180.0, south, east, north),
    ]


def bbox_polygon(bounds: Bounds) -> Polygon:
    west, south, east, north = bounds
    if west > east:
        raise ValueError("Прямоугольник пересекает 180-й меридиан; разделите его")
    return box(west, south, east, north)


def _polygonal_parts(geom: BaseGeometry) -> list[BaseGeometry]:
    if geom.is_empty:
        return []
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return [geom]
    if isinstance(geom, GeometryCollection):
        parts: list[BaseGeometry] = []
        for child in geom.geoms:
            parts.extend(_polygonal_parts(child))
        return parts
    return []


def geometry_from_geojson(value: Any) -> BaseGeometry:
    """Convert GeoJSON geometry/feature/collection to a valid polygonal geometry."""
    if not isinstance(value, dict):
        raise ValueError("Геометрия должна быть объектом GeoJSON")

    if value.get("type") == "FeatureCollection":
        geometries = []
        for feature in value.get("features", []):
            if not isinstance(feature, dict):
                continue
            geometry = feature.get("geometry")
            if geometry:
                geometries.append(geometry_from_geojson(geometry))
        if not geometries:
            raise ValueError("В GeoJSON нет полигонов")
        return union_all(geometries)

    if value.get("type") == "Feature":
        geometry = value.get("geometry")
        if not geometry:
            raise ValueError("У GeoJSON Feature нет geometry")
        return geometry_from_geojson(geometry)

    try:
        geom = shape(value)
    except Exception as exc:  # noqa: BLE001 - shapely raises several exception types
        raise ValueError(f"Не удалось прочитать GeoJSON: {exc}") from exc

    geom = force_2d(make_valid(geom))
    parts = _polygonal_parts(geom)
    if not parts:
        raise ValueError("Поддерживаются только полигоны (Polygon/MultiPolygon)")
    geom = union_all(parts)

    minx, miny, maxx, maxy = geom.bounds
    epsilon = 1e-9
    if minx < -180 - epsilon or maxx > 180 + epsilon or miny < -90 - epsilon or maxy > 90 + epsilon:
        raise ValueError(
            "Полигон выходит за диапазон долгот -180..180. "
            "Для областей через 180-й меридиан используйте прямоугольник."
        )
    if geom.is_empty or geom.area <= 0:
        raise ValueError("Полигон пустой или имеет нулевую площадь")
    return geom


def union_polygons(geometries: Iterable[BaseGeometry]) -> BaseGeometry:
    items = [geom for geom in geometries if geom is not None and not geom.is_empty]
    if not items:
        raise ValueError("Нет полигонов для объединения")
    result = union_all(items)
    parts = _polygonal_parts(result)
    if not parts:
        raise ValueError("После объединения не осталось полигонов")
    return union_all(parts)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def geometry_to_geojson(geom: BaseGeometry) -> dict[str, Any]:
    return _jsonable(mapping(geom))  # type: ignore[return-value]


def area_km2(geom: BaseGeometry) -> float:
    if geom.is_empty:
        return 0.0
    area, _ = GEOD.geometry_area_perimeter(geom)
    return abs(area) / 1_000_000.0


def geometry_is_polygonal(geom: BaseGeometry) -> bool:
    return geom.geom_type in ("Polygon", "MultiPolygon")
