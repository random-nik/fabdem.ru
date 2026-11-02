from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AreaRequest(BaseModel):
    mode: Literal["bbox", "polygon", "tile"] = "bbox"
    bbox: list[float] | None = None
    geometry: dict[str, Any] | None = None
    tile: str | None = None
    mask: bool = True
    crs: str = "EPSG:4326"
    filename: str | None = Field(default=None, max_length=120)


class ParseResponse(BaseModel):
    geometry: dict[str, Any]
    feature_count: int
