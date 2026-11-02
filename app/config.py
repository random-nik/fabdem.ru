from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent

OFFICIAL_BASE_URL = "https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn"
TILE_INDEX_URL = f"{OFFICIAL_BASE_URL}/FABDEM_v1-2_tiles.geojson"


@dataclass
class Settings:
    """Runtime configuration.

    The defaults are deliberately local-first: all downloaded tiles and generated
    files stay inside the project's ``data`` directory.
    """

    base_dir: Path = PROJECT_DIR
    data_dir: Path | None = None
    cache_dir: Path | None = None
    tile_cache_dir: Path | None = None
    jobs_dir: Path | None = None
    catalog_cache_path: Path | None = None

    source_base_url: str = OFFICIAL_BASE_URL
    # Optional direct 1x1 tile mirror. Leave empty to use the official ZIPs.
    # Example: https://huggingface.co/buckets/links-ads/fabdem/resolve
    tile_mirror_url: str = ""
    catalog_url: str = TILE_INDEX_URL

    max_tiles: int = 9
    max_file_bytes: int = 500 * 1024 * 1024
    max_upload_bytes: int = 10 * 1024 * 1024
    max_vertices: int = 20_000
    job_ttl_hours: int = 24
    max_workers: int = 2

    app_name: str = "FABDEM Downloader"
    app_version: str = "0.1.0"
    dataset_url: str = "https://data.bris.ac.uk/data/dataset/s5hqmjcdj8yo2ibzi9b4ew3sn"
    doi: str = "10.5523/bris.s5hqmjcdj8yo2ibzi9b4ew3sn"
    license_url: str = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
    license_name: str = "CC BY-NC-SA 4.0"

    extra: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir).resolve()
        if self.data_dir is None:
            self.data_dir = self.base_dir / "data"
        self.data_dir = Path(self.data_dir)
        if self.cache_dir is None:
            self.cache_dir = self.data_dir / "cache"
        self.cache_dir = Path(self.cache_dir)
        if self.tile_cache_dir is None:
            self.tile_cache_dir = self.cache_dir / "tiles"
        self.tile_cache_dir = Path(self.tile_cache_dir)
        if self.jobs_dir is None:
            self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir = Path(self.jobs_dir)
        if self.catalog_cache_path is None:
            self.catalog_cache_path = self.data_dir / "FABDEM_v1-2_tiles.geojson"
        self.catalog_cache_path = Path(self.catalog_cache_path)
        self.source_base_url = self.source_base_url.rstrip("/")
        self.tile_mirror_url = self.tile_mirror_url.rstrip("/")

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.tile_cache_dir, self.jobs_dir):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_dir=Path(os.getenv("FABDEM_BASE_DIR", PROJECT_DIR)),
            source_base_url=os.getenv("FABDEM_SOURCE_BASE_URL", OFFICIAL_BASE_URL),
            tile_mirror_url=os.getenv("FABDEM_TILE_MIRROR_URL", ""),
            catalog_url=os.getenv("FABDEM_TILE_INDEX_URL", TILE_INDEX_URL),
            max_tiles=int(os.getenv("FABDEM_MAX_TILES", "9")),
            max_file_bytes=int(os.getenv("FABDEM_MAX_FILE_BYTES", str(500 * 1024 * 1024))),
            max_upload_bytes=int(os.getenv("FABDEM_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024))),
            max_workers=int(os.getenv("FABDEM_MAX_WORKERS", "2")),
        )
