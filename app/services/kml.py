from __future__ import annotations

import io
import json
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Package `lxml` missing.*")
    from fastkml import kml
from shapely import force_2d
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.services.geo import (
    geometry_from_geojson,
    geometry_to_geojson,
    union_polygons,
)


class ParseError(ValueError):
    """A user-facing error while parsing an uploaded geometry file."""


@dataclass
class ParsedGeometry:
    geometry: dict[str, Any]
    feature_count: int


def _walk_kml_features(feature: Any) -> Iterable[Any]:
    children = getattr(feature, "features", None)
    if children:
        for child in children:
            yield from _walk_kml_features(child)
    geometry = getattr(feature, "geometry", None)
    if geometry is not None:
        yield geometry


def _kml_geometries(data: bytes) -> tuple[list[BaseGeometry], int]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError("KML должен быть в кодировке UTF-8") from exc

    try:
        document = kml.KML.from_string(text)
    except Exception as exc:  # noqa: BLE001 - fastkml exception hierarchy is broad
        raise ParseError(f"Не удалось разобрать KML: {exc}") from exc

    geometries: list[BaseGeometry] = []
    feature_count = 0
    for feature in _walk_kml_features(document):
        try:
            raw = shape(feature.__geo_interface__)
        except Exception:
            continue
        raw = force_2d(raw)
        if raw.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection"):
            continue
        try:
            parsed = geometry_from_geojson(raw.__geo_interface__)
        except ValueError:
            continue
        geometries.append(parsed)
        feature_count += 1

    if not geometries:
        raise ParseError("В KML не найдено ни одного полигона")
    return geometries, feature_count


def _geojson_geometries(payload: Any) -> tuple[list[BaseGeometry], int]:
    if not isinstance(payload, dict):
        raise ParseError("GeoJSON должен быть объектом")

    if payload.get("type") == "FeatureCollection":
        items = payload.get("features") or []
    elif payload.get("type") == "Feature":
        items = [payload]
    elif payload.get("type") in ("Polygon", "MultiPolygon"):
        items = [{"type": "Feature", "properties": {}, "geometry": payload}]
    else:
        raise ParseError("Ожидается Polygon, MultiPolygon, Feature или FeatureCollection")

    geometries: list[BaseGeometry] = []
    feature_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        geometry = item.get("geometry") if item.get("type") == "Feature" else item
        if not geometry:
            continue
        try:
            parsed = geometry_from_geojson(geometry)
        except ValueError:
            continue
        geometries.append(parsed)
        feature_count += 1

    if not geometries:
        raise ParseError("В GeoJSON не найдено ни одного полигона")
    return geometries, feature_count


def parse_upload(
    filename: str | None,
    data: bytes,
    *,
    max_bytes: int = 10 * 1024 * 1024,
) -> ParsedGeometry:
    if len(data) > max_bytes:
        raise ParseError(
            f"Файл слишком большой: {len(data) / 1024 / 1024:.1f} МБ "
            f"(лимит {max_bytes / 1024 / 1024:.0f} МБ)"
        )
    if not data:
        raise ParseError("Файл пустой")

    suffix = Path(filename or "").suffix.lower()
    if suffix == ".kmz":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
                if not names:
                    raise ParseError("В KMZ не найден KML-файл")
                data = archive.read(names[0])
        except zipfile.BadZipFile as exc:
            raise ParseError("Некорректный KMZ-архив") from exc
        geometries, feature_count = _kml_geometries(data)
    elif suffix == ".kml":
        geometries, feature_count = _kml_geometries(data)
    elif suffix in (".geojson", ".json"):
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParseError(f"Не удалось разобрать JSON: {exc}") from exc
        geometries, feature_count = _geojson_geometries(payload)
    else:
        raise ParseError("Поддерживаются KML, KMZ, GeoJSON и JSON")

    try:
        merged = union_polygons(geometries)
    except ValueError as exc:
        raise ParseError(str(exc)) from exc
    return ParsedGeometry(
        geometry=geometry_to_geojson(merged),
        feature_count=feature_count,
    )
