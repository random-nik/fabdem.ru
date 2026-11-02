from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import APP_DIR, Settings
from app.models import AreaRequest, ParseResponse
from app.services.areas import AreaError, resolve_area
from app.services.catalog import TileCatalog, load_catalog
from app.services.jobs import JobManager
from app.services.kml import ParseError, parse_upload
from app.services.source import RemoteZipSource, TileSource


def _parse_bbox_query(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError("bbox должен содержать четыре числа: west,south,east,north")
    return tuple(float(part) for part in parts)  # type: ignore[return-value]


def create_app(
    settings: Settings | None = None,
    catalog: TileCatalog | None = None,
    source: TileSource | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.catalog is None:
            app.state.catalog = load_catalog(settings)
        if app.state.source is None:
            app.state.source = RemoteZipSource(settings)
        app.state.jobs = JobManager(settings, app.state.catalog, app.state.source)
        try:
            yield
        finally:
            if app.state.jobs is not None:
                app.state.jobs.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.source = source
    app.state.jobs = None

    templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    static_dir = APP_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "app_name": settings.app_name,
                "app_version": settings.app_version,
                "max_tiles": settings.max_tiles,
                "max_upload_mb": settings.max_upload_bytes // (1024 * 1024),
                "license_name": settings.license_name,
                "license_url": settings.license_url,
                "dataset_url": settings.dataset_url,
                "doi": settings.doi,
            },
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(
            path=APP_DIR / "static" / "favicon.svg",
            media_type="image/svg+xml",
        )

    @app.get("/api/health")
    async def health(request: Request):
        current_catalog = request.app.state.catalog
        return {
            "status": "ok",
            "version": settings.app_version,
            "tiles": len(current_catalog) if current_catalog is not None else 0,
        }

    @app.get("/api/tiles.geojson")
    async def tiles_geojson(
        request: Request,
        bbox: str | None = Query(default=None, description="west,south,east,north"),
    ):
        current_catalog = request.app.state.catalog
        if current_catalog is None:
            raise HTTPException(status_code=503, detail="Индекс плиток ещё не загружен")
        try:
            bounds = _parse_bbox_query(bbox) if bbox else None
            return current_catalog.to_geojson(bounds)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/estimate")
    async def estimate(request: Request, payload: AreaRequest) -> dict[str, Any]:
        current_catalog = request.app.state.catalog
        if current_catalog is None:
            raise HTTPException(status_code=503, detail="Индекс плиток ещё не загружен")
        try:
            return resolve_area(
                payload,
                current_catalog,
                settings,
                enforce_limits=False,
            ).as_dict()
        except AreaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs")
    async def create_job(request: Request, payload: AreaRequest) -> dict[str, Any]:
        current_catalog = request.app.state.catalog
        jobs = request.app.state.jobs
        if current_catalog is None or jobs is None:
            raise HTTPException(status_code=503, detail="Сервис ещё не готов")
        try:
            # Validate synchronously so obvious errors are returned as 400
            # instead of being discovered later in the worker thread.
            resolve_area(payload, current_catalog, settings)
        except AreaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        job = jobs.create(payload)
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    async def job_status(request: Request, job_id: str) -> dict[str, Any]:
        jobs = request.app.state.jobs
        if jobs is None:
            raise HTTPException(status_code=503, detail="Сервис ещё не готов")
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return job.to_dict()

    @app.get("/api/jobs/{job_id}/download")
    async def job_download(request: Request, job_id: str):
        jobs = request.app.state.jobs
        if jobs is None:
            raise HTTPException(status_code=503, detail="Сервис ещё не готов")
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        if job.status == "error":
            raise HTTPException(status_code=409, detail=job.error or "Задача завершилась с ошибкой")
        if job.status != "done" or job.output_path is None or not job.output_path.exists():
            raise HTTPException(status_code=409, detail="Файл ещё не готов")
        media_type = (
            "application/zip"
            if job.filename.lower().endswith(".zip")
            else "image/tiff"
        )
        return FileResponse(
            path=Path(job.output_path),
            media_type=media_type,
            filename=job.filename,
        )

    @app.post("/api/parse-file", response_model=ParseResponse)
    async def parse_file(request: Request, file: UploadFile = File(...)) -> ParseResponse:
        data = await file.read(settings.max_upload_bytes + 1)
        try:
            parsed = parse_upload(
                file.filename,
                data,
                max_bytes=settings.max_upload_bytes,
            )
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ParseResponse(
            geometry=parsed.geometry,
            feature_count=parsed.feature_count,
        )

    return app


app = create_app()
