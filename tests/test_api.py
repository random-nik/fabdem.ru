from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.catalog import TileCatalog
from app.services.source import LocalTileSource
from tests.test_kml import KML_POLYGON


def make_client(
    settings: Settings,
    catalog: TileCatalog,
    tiles_dir: dict[str, Path],
) -> TestClient:
    app = create_app(
        settings=settings,
        catalog=catalog,
        source=LocalTileSource(tiles_dir["N055E037"].parent),
    )
    return TestClient(app)


def test_index_and_health(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "FABDEM" in response.text

        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_static_assets(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        for path in (
            "/static/app.js",
            "/static/style.css",
            "/static/vendor/leaflet.js",
            "/static/vendor/leaflet-geoman.js",
            "/favicon.ico",
        ):
            response = client.get(path)
            assert response.status_code == 200, path


def test_tiles_geojson_filtered(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.get("/api/tiles.geojson?bbox=37,55,39,56")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["features"]) == 2
        assert {f["properties"]["tile_name"] for f in payload["features"]} == {
            "N055E037",
            "N055E038",
        }


def test_estimate(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/estimate",
            json={"mode": "bbox", "bbox": [37, 55, 39, 56]},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["tile_count"] == 2
        assert payload["within_limits"] is True
        assert payload["area_km2"] > 0
        assert payload["estimated_bytes"] > 0


def test_estimate_over_limit_still_returns_archives(
    settings: Settings, catalog: TileCatalog, tiles_dir
):
    settings.max_tiles = 1
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/estimate",
            json={"mode": "bbox", "bbox": [37, 55, 39, 56]},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["within_limits"] is False
        assert payload["warnings"]
        assert payload["archives"]

        response = client.post(
            "/api/jobs",
            json={"mode": "bbox", "bbox": [37, 55, 39, 56]},
        )
        assert response.status_code == 400


def test_estimate_rejects_bad_bbox(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/estimate",
            json={"mode": "bbox", "bbox": [37, 56, 38, 55]},
        )
        assert response.status_code in (400, 422)


def test_job_download(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/jobs",
            json={"mode": "bbox", "bbox": [37, 55, 39, 56], "filename": "moscow"},
        )
        assert response.status_code == 200, response.text
        job = response.json()
        job_id = job["id"]

        status = None
        for _ in range(200):
            status_response = client.get(f"/api/jobs/{job_id}")
            assert status_response.status_code == 200
            status = status_response.json()
            if status["status"] in ("done", "error"):
                break
            time.sleep(0.02)

        assert status is not None
        assert status["status"] == "done", status
        assert status["download_url"] == f"/api/jobs/{job_id}/download"

        download = client.get(status["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("image/tiff")
        assert len(download.content) > 100


def test_tile_mode_job(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/jobs",
            json={"mode": "tile", "tile": "N055E037"},
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]

        for _ in range(200):
            status = client.get(f"/api/jobs/{job_id}").json()
            if status["status"] in ("done", "error"):
                break
            time.sleep(0.02)

        assert status["status"] == "done", status
        download = client.get(status["download_url"])
        assert download.status_code == 200
        assert download.content[:2] == b"II"


def test_antimeridian_job_returns_zip(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/jobs",
            json={"mode": "bbox", "bbox": [179, 66, -179, 67], "filename": "cross"},
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["id"]

        for _ in range(200):
            status = client.get(f"/api/jobs/{job_id}").json()
            if status["status"] in ("done", "error"):
                break
            time.sleep(0.02)

        assert status["status"] == "done", status
        download = client.get(status["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            names = set(archive.namelist())
        assert {"cross_part1.tif", "cross_part2.tif"} <= names
        assert "README.txt" in names


def test_parse_file(settings: Settings, catalog: TileCatalog, tiles_dir):
    with make_client(settings, catalog, tiles_dir) as client:
        response = client.post(
            "/api/parse-file",
            files={
                "file": (
                    "area.kml",
                    KML_POLYGON.encode("utf-8"),
                    "application/vnd.google-earth.kml+xml",
                )
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["feature_count"] == 1
        assert payload["geometry"]["type"] == "Polygon"
