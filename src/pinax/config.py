# SPDX-License-Identifier: Apache-2.0
"""Runtime settings, read from PINAX_* environment variables or a .env file."""

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PINAX_", env_file=".env", extra="ignore")

    app_name: str = "Pinax"
    environment: Literal["demo", "dev", "prod"] = "demo"

    # Where things live. VAR_ROOT holds the catalog database and the snapshot indexes built from scans;
    # SCAN_ROOT is where `pinax scan` writes fs-inventory output (scans made elsewhere can live anywhere).
    var_root: Path = PROJECT_ROOT / "var"
    scan_root: Path = PROJECT_ROOT / "scans"
    demo_root: Path = PROJECT_ROOT / "demo"
    database_url: str = ""

    # fs-inventory. Defaults to bin/fs-inventory (scripts/install-fs-inventory.sh), then the PATH.
    fs_inventory: str = ""
    scan_threads: int = 32
    scan_extra_args: list[str] = []

    # Building snapshot indexes. DuckDB uses this much memory and these threads, spilling beside the index
    # beyond the memory limit. 0 threads means one per CPU; steps that read the scan use fewer if the memory
    # limit requires. More memory doesn't make the build faster (measured at 100 million files).
    build_memory_limit: str = "8GB"
    build_threads: int = 0
    # Snapshots kept per filesystem; older indexes are deleted, their folder history is kept.
    keep_snapshots: int = 6

    # Query side.
    query_memory_limit: str = "4GB"
    query_threads: int = 0
    search_max_rows: int = 500
    export_max_rows: int = 100_000

    # Reporting defaults. Data counts as cold when nothing in it has changed for this many days.
    cold_days: int = 730

    # Identity. "demo" uses a persona cookie; "header" trusts a username header (X-Remote-User by default) set
    # by a single sign-on proxy, and only on requests from TRUSTED_PROXIES (addresses or networks; a JSON list).
    auth_mode: Literal["demo", "header"] = "demo"
    auth_header: str = "X-Remote-User"
    trusted_proxies: list[str] = ["127.0.0.1", "::1"]

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.var_root / 'pinax.db'}"

    @property
    def snapshot_root(self) -> Path:
        return self.var_root / "snapshots"

    def fs_inventory_path(self) -> str:
        if self.fs_inventory:
            return self.fs_inventory
        local = PROJECT_ROOT / "bin" / "fs-inventory"
        if local.exists():
            return str(local)
        return shutil.which("fs-inventory") or "fs-inventory"

    def ensure_dirs(self) -> None:
        for p in (self.var_root, self.snapshot_root):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
