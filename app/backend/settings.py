"""Runtime settings. Override with environment variables prefixed FLOWTWIN_ (e.g. FLOWTWIN_PORT=8443)
or a ``flowtwin.env`` file in the data folder."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    if os.environ.get("FLOWTWIN_DATA_DIR"):
        return Path(os.environ["FLOWTWIN_DATA_DIR"])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "FlowTwin"
    return Path.home() / ".flowtwin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLOWTWIN_", env_file=str(default_data_dir() / "flowtwin.env"),
                                      extra="ignore")

    data_dir: Path = default_data_dir()
    db_url: Optional[str] = None             # default: sqlite:///<data_dir>/flowtwin.sqlite
    host: str = "127.0.0.1"                  # set 0.0.0.0 to serve the intranet
    port: int = 8400
    tls_cert: Optional[Path] = None          # PEM certificate (enterprise or self-signed) -> HTTPS
    tls_key: Optional[Path] = None
    session_hours: int = 12
    site_code: str = "GGS-1"
    sample_dir: Optional[Path] = None         # synthetic demo GGS (default: data/sample in the source tree)

    scheduler_enabled: bool = True
    daily_run_time: str = "06:30"            # local time; validates the production day that ended at 06:00
    daily_run_days: int = 30                 # period of the scheduled twin run (ends on the latest gauged day)
    watch_enabled: bool = True               # drop-zone folder import
    watch_interval_s: int = 60
    backup_time: str = "02:00"
    backup_keep: int = 14
    workers: int = 2                         # background job threads

    @property
    def database_url(self) -> str:
        return self.db_url or f"sqlite:///{(self.data_dir / 'flowtwin.sqlite').as_posix()}"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def dropzone(self) -> Path:
        return self.data_dir / "dropzone"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def secure_cookies(self) -> bool:
        return self.tls_cert is not None

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.runs_dir, self.dropzone, self.backups_dir, self.data_dir / "logs"):
            d.mkdir(parents=True, exist_ok=True)


_active: Optional[Settings] = None


@lru_cache
def _from_env() -> Settings:
    return Settings()


def use_settings(s: Optional[Settings]) -> None:
    """Make ``s`` the settings every module sees (the app factory and tests call this)."""
    global _active
    _active = s


def get_settings() -> Settings:
    return _active or _from_env()
