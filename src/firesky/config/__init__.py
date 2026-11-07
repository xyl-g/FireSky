"""YAML-based configuration for the firesky web service.

The web server no longer reads command-line arguments: it loads a single YAML
file at startup. The path is taken from the ``FIRESKY_CONFIG`` environment
variable, falling back to ``config/web_server.yaml`` next to this module.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "web_server.yaml"



@dataclass
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    workers: Optional[int] = None
    aerosol_source: str = "cams"
    report_dir: str = "reports/web"
    gfs_cache_dir: str = ".cache/gfs"
    ecmwf_cache_dir: str = ".cache/ecmwf"
    cams_cache_dir: str = ".cache/cams"
    map_cache_dir: str = ".cache/map-frames"
    regional_cache_dir: str = ".cache/regional-forecasts"
    point_cache_dir: str = ".cache/point-forecasts"
    satellite_cache_dir: str = ".cache/himawari-hsd"
    satellite_warmup_token: Optional[str] = None
    forecast_cache_seconds: int = 300
    regional_refresh_payload: Optional[dict[str, Any]] = None


@dataclass
class LoggingSettings:
    level: str = "INFO"
    dir: str = "logs"
    app_name: str = "app"
    when: str = "midnight"
    backup_count: int = 14
    console: bool = True


@dataclass
class Settings:
    server: ServerSettings = field(default_factory=ServerSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)


def _coerce_int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "" or value == "null":
        return None
    return int(value)


def _coerce_server(raw: dict[str, Any]) -> ServerSettings:
    return ServerSettings(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8765)),
        workers=_coerce_int_or_none(raw.get("workers")),
        aerosol_source=str(raw.get("aerosol_source", "cams")),
        report_dir=str(raw.get("report_dir", "reports/web")),
        gfs_cache_dir=str(raw.get("gfs_cache_dir", ".cache/gfs")),
        ecmwf_cache_dir=str(raw.get("ecmwf_cache_dir", ".cache/ecmwf")),
        cams_cache_dir=str(raw.get("cams_cache_dir", ".cache/cams")),
        map_cache_dir=str(raw.get("map_cache_dir", ".cache/map-frames")),
        regional_cache_dir=str(raw.get("regional_cache_dir", ".cache/regional-forecasts")),
        point_cache_dir=str(raw.get("point_cache_dir", ".cache/point-forecasts")),
        satellite_cache_dir=str(raw.get("satellite_cache_dir", ".cache/himawari-hsd")),
        satellite_warmup_token=(
            str(raw["satellite_warmup_token"])
            if raw.get("satellite_warmup_token") not in (None, "")
            else None
        ),
        forecast_cache_seconds=int(raw.get("forecast_cache_seconds", 300)),
        regional_refresh_payload=(
            dict(raw["regional_refresh_payload"])
            if isinstance(raw.get("regional_refresh_payload"), dict)
            else None
        ),
    )


def _coerce_logging(raw: dict[str, Any]) -> LoggingSettings:
    return LoggingSettings(
        level=str(raw.get("level", "INFO")),
        dir=str(raw.get("dir", "logs")),
        app_name=str(raw.get("app_name", "app")),
        when=str(raw.get("when", "midnight")),
        backup_count=int(raw.get("backup_count", 14)),
        console=bool(raw.get("console", True)),
    )


def load_settings(path: Optional[str | Path] = None) -> Settings:
    """Load settings from YAML.

    Explicit ``path`` wins; otherwise ``FIRESKY_CONFIG`` env var; otherwise the
    bundled ``web_server.yaml``. Missing files are tolerated (defaults apply).
    """
    if path:
        config_path = Path(path)
    else:
        configured = os.environ.get("FIRESKY_CONFIG", "").strip()
        candidates = [Path(configured)] if configured else []
        candidates.extend((
            Path.cwd() / "etc" / "python.yaml",
            Path(sys.executable).resolve().parent.parent / "etc" / "python.yaml",
            DEFAULT_CONFIG_PATH,
        ))
        config_path = next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])
    raw: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    server = _coerce_server(raw.get("server", {}) or {})
    logging_cfg = _coerce_logging(raw.get("logging", {}) or {})
    return Settings(server=server, logging=logging_cfg)


__all__ = [
    "Settings",
    "ServerSettings",
    "LoggingSettings",
    "load_settings",
    "DEFAULT_CONFIG_PATH",
]
