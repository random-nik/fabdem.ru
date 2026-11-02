from __future__ import annotations

import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import AreaRequest
from app.services.areas import AreaError, ResolvedArea, resolve_area
from app.services.catalog import TileCatalog
from app.services.geo import is_antimeridian, split_antimeridian
from app.services.processor import LICENCE_TEXT, ProcessOptions, copy_tile, process_area
from app.services.source import TileSource


def _safe_filename(value: str | None, default: str, extension: str) -> str:
    name = (value or "").strip()
    if not name:
        name = default
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not name:
        name = default
    if name.lower().endswith(extension.lower()):
        return name
    return f"{name}{extension}"


@dataclass
class Job:
    id: str
    status: str = "queued"
    progress: int = 0
    message: str = "В очереди"
    created_at: float = field(default_factory=time.time)
    filename: str = "fabdem.tif"
    output_path: Path | None = None
    error: str | None = None
    request: AreaRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        size = None
        if self.output_path is not None and self.output_path.exists():
            size = self.output_path.stat().st_size
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at,
            "filename": self.filename,
            "size_bytes": size,
            "error": self.error,
            "download_url": (
                f"/api/jobs/{self.id}/download"
                if self.status == "done" and self.output_path is not None
                else None
            ),
        }


class JobManager:
    def __init__(
        self,
        settings: Settings,
        catalog: TileCatalog,
        source: TileSource,
    ):
        self.settings = settings
        self.catalog = catalog
        self.source = source
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._cleanup_old_outputs()
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.max_workers),
            thread_name_prefix="fabdem-job",
        )

    def _cleanup_old_outputs(self) -> None:
        cutoff = time.time() - max(1, self.settings.job_ttl_hours) * 3600
        try:
            for path in self.settings.jobs_dir.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    def create(self, request: AreaRequest) -> Job:
        job_id = uuid.uuid4().hex[:12]
        default_name = (
            request.tile + ".tif"
            if request.mode == "tile" and request.tile
            else f"fabdem_{job_id}.tif"
        )
        job = Job(
            id=job_id,
            filename=_safe_filename(request.filename, default_name, ".tif"),
            request=request,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _update(self, job: Job, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)

    def _run(self, job: Job) -> None:
        self._update(job, status="running", progress=2, message="Проверка области")
        try:
            request = job.request
            if request is None:
                raise AreaError("Внутренняя ошибка: пустой запрос")

            area = resolve_area(request, self.catalog, self.settings)
            if area.mode == "tile" and area.tile is not None:
                self._run_tile(job, request, area)
            elif is_antimeridian(area.bounds):
                self._run_antimeridian(job, request, area)
            else:
                self._run_area(job, request, area)
        except Exception as exc:  # noqa: BLE001 - surface a user-facing error
            self._update(
                job,
                status="error",
                progress=100,
                message="Ошибка",
                error=str(exc),
            )

    def _run_tile(self, job: Job, request: AreaRequest, area: ResolvedArea) -> None:
        assert area.tile is not None
        self._update(job, progress=35, message=f"Получение плитки {area.tile.name}")
        tile_path = self.source.ensure_tile(area.tile)
        output = self.settings.jobs_dir / f"{job.id}.tif"
        self._update(job, progress=80, message="Копирование плитки")
        copy_tile(tile_path, output)
        self._update(
            job,
            status="done",
            progress=100,
            message="Готово",
            output_path=output,
            filename=_safe_filename(request.filename, area.tile.file_name, ".tif"),
        )

    def _ensure_tiles(
        self,
        job: Job,
        tiles: list,
        start_percent: int = 5,
        end_percent: int = 55,
    ) -> list[Path]:
        if len(tiles) == 1:
            tile = tiles[0]
            self._update(
                job,
                progress=start_percent,
                message=f"Получение плитки {tile.name} (1/1)",
            )
            return [self.source.ensure_tile(tile)]

        paths: dict[str, Path] = {}
        total = len(tiles)
        workers = min(4, total)
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="fabdem-tile",
        ) as executor:
            futures = {
                executor.submit(self.source.ensure_tile, tile): tile
                for tile in tiles
            }
            for index, future in enumerate(as_completed(futures), start=1):
                tile = futures[future]
                paths[tile.name] = future.result()
                self._update(
                    job,
                    progress=start_percent
                    + int((end_percent - start_percent) * index / total),
                    message=f"Получение плитки {tile.name} ({index}/{total})",
                )
        return [paths[tile.name] for tile in tiles]

    def _run_area(self, job: Job, request: AreaRequest, area: ResolvedArea) -> None:
        tile_paths = self._ensure_tiles(job, area.tiles)

        output = self.settings.jobs_dir / f"{job.id}.tif"
        options = ProcessOptions(
            crs=request.crs,
            mask=bool(request.mask and area.geometry is not None),
        )

        def progress(stage: str, percent: float) -> None:
            self._update(
                job,
                progress=55 + int(percent * 0.45),
                message=stage,
            )

        process_area(
            bounds=area.bounds,
            geometry=area.geometry,
            tile_paths=tile_paths,
            output_path=output,
            options=options,
            progress=progress,
        )
        self._update(
            job,
            status="done",
            progress=100,
            message="Готово",
            output_path=output,
            filename=_safe_filename(request.filename, f"fabdem_{job.id}.tif", ".tif"),
        )

    def _run_antimeridian(
        self,
        job: Job,
        request: AreaRequest,
        area: ResolvedArea,
    ) -> None:
        """Produce a ZIP with two GeoTIFFs for a bbox crossing 180 degrees."""
        tile_paths = self._ensure_tiles(job, area.tiles, 5, 50)

        base = re.sub(r"\.(tif|tiff|zip|geotiff)$", "", job.filename, flags=re.IGNORECASE)
        archive_path = self.settings.jobs_dir / f"{job.id}.zip"
        parts = split_antimeridian(area.bounds)

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, part_bounds in enumerate(parts, start=1):
                self._update(
                    job,
                    progress=55 + int(20 * (index - 1)),
                    message=f"Обработка части {index}/{len(parts)}",
                )
                part_path = self.settings.jobs_dir / f"{job.id}_part{index}.tif"
                process_area(
                    bounds=part_bounds,
                    geometry=None,
                    tile_paths=tile_paths,
                    output_path=part_path,
                    options=ProcessOptions(crs=request.crs, mask=False),
                )
                archive.write(part_path, f"{base}_part{index}.tif")
                part_path.unlink(missing_ok=True)
            archive.writestr(
                "README.txt",
                "FABDEM V1-2, CC BY-NC-SA 4.0, non-commercial use.\n"
                "https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn\n"
                f"{LICENCE_TEXT}\n",
            )

        self._update(
            job,
            status="done",
            progress=100,
            message="Готово",
            output_path=archive_path,
            filename=_safe_filename(base, f"fabdem_{job.id}", ".zip"),
        )
