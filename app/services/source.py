from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Protocol

import httpx
import rasterio
from remotezip import RemoteZip

from app.config import Settings
from app.services.catalog import Tile, normalize_zip_name


class SourceError(RuntimeError):
    """Raised when a source tile cannot be obtained."""


class TileSource(Protocol):
    def ensure_tile(self, tile: Tile) -> Path:
        ...


def remote_vsi_path(tile: Tile, base_url: str) -> str:
    """Return a GDAL virtual path for a tile inside the official 10x10 ZIP."""
    return (
        f"/vsizip//vsicurl/{base_url.rstrip('/')}/"
        f"{normalize_zip_name(tile.zipfile_name)}/{tile.remote_file_name}"
    )


class LocalTileSource:
    """A source backed by already prepared tiles (used by tests)."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def ensure_tile(self, tile: Tile) -> Path:
        path = self.directory / tile.file_name
        if not path.exists():
            raise SourceError(f"Локальная плитка не найдена: {path}")
        return path


class RemoteZipSource:
    """Read 1x1 degree tiles directly from the official 10x10 ZIPs.

    GDAL's ``/vsizip//vsicurl/`` driver performs HTTP Range requests, so only
    the needed member and ZIP metadata are transferred, not the whole archive.
    The extracted tile is cached as a compressed local GeoTIFF.
    """

    _locks_guard = threading.Lock()
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.tile_cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _lock_for(cls, name: str) -> threading.Lock:
        with cls._locks_guard:
            lock = cls._locks.get(name)
            if lock is None:
                lock = threading.Lock()
                cls._locks[name] = lock
            return lock

    def ensure_tile(self, tile: Tile) -> Path:
        destination = self.settings.tile_cache_dir / tile.file_name
        if destination.exists() and destination.stat().st_size > 0:
            return destination

        lock = self._lock_for(tile.name)
        with lock:
            if destination.exists() and destination.stat().st_size > 0:
                return destination

            temp = destination.with_suffix(".part.tif")
            errors: list[str] = []
            try:
                if self.settings.tile_mirror_url:
                    try:
                        self._download_from_mirror(tile, temp)
                    except Exception as exc:  # noqa: BLE001 - try the official source next
                        errors.append(f"зеркало: {exc}")
                        self._download_from_remote_zip(tile, temp)
                else:
                    self._download_from_remote_zip(tile, temp)
                self._validate_tile(temp)
                os.replace(temp, destination)
            except Exception as first_exc:  # noqa: BLE001 - fall back to GDAL range access
                errors.append(str(first_exc))
                try:
                    remote = remote_vsi_path(tile, self.settings.source_base_url)
                    self._copy_remote_tile(remote, temp)
                    self._validate_tile(temp)
                    os.replace(temp, destination)
                except Exception as second_exc:  # noqa: BLE001
                    temp.unlink(missing_ok=True)
                    errors.append(str(second_exc))
                    raise SourceError(
                        f"Не удалось получить плитку {tile.name} из источника FABDEM: "
                        + "; ".join(errors)
                    ) from second_exc
            return destination

    def _download_from_mirror(self, tile: Tile, destination: Path) -> None:
        zip_name = tile.zip_name_normalized
        if zip_name.lower().endswith(".zip"):
            zip_name = zip_name[:-4]
        url = (
            f"{self.settings.tile_mirror_url}/tiles/{zip_name}/"
            f"{tile.remote_file_name}?download=true"
        )
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(120.0, connect=30.0),
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 256):
                    if chunk:
                        handle.write(chunk)

    def _download_from_remote_zip(self, tile: Tile, destination: Path) -> None:
        archive_url = tile.archive_url(self.settings.source_base_url)
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(120.0, connect=30.0),
        ) as client:
            head = client.head(archive_url)
            head.raise_for_status()
            accept_ranges = head.headers.get("accept-ranges", "").lower()
            if "bytes" not in accept_ranges:
                raise SourceError("источник не поддерживает HTTP Range")

        with RemoteZip(archive_url) as archive:
            data = archive.read(tile.remote_file_name)
        if not data:
            raise SourceError("пустой ответ для плитки")
        destination.write_bytes(data)

    @staticmethod
    def _validate_tile(path: Path) -> None:
        if not path.exists() or path.stat().st_size == 0:
            raise SourceError("получен пустой файл")
        with rasterio.open(path) as dataset:
            if dataset.width <= 0 or dataset.height <= 0:
                raise SourceError("получен некорректный GeoTIFF")

    def _copy_remote_tile(self, remote: str, destination: Path) -> None:
        gdal_options = {
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".zip,.tif",
            "VSI_CACHE": "TRUE",
            "GDAL_HTTP_TIMEOUT": "120",
            "GDAL_HTTP_MAX_RETRY": "3",
        }
        with rasterio.Env(**gdal_options):
            with rasterio.open(remote) as src:
                profile = src.profile.copy()
                profile.update(
                    driver="GTiff",
                    compress="deflate",
                    predictor=2,
                    tiled=True,
                    blockxsize=512,
                    blockysize=512,
                    BIGTIFF="IF_SAFER",
                )
                with rasterio.open(destination, "w", **profile) as dst:
                    dst.write(src.read())
                    tags = src.tags()
                    if tags:
                        dst.update_tags(**tags)
