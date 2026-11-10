"""Explicit GFS data-access bridge; no Python numerical or network fallback."""
from datetime import timezone
import os
from pathlib import Path

from firesky.rust_grid import EngineOperationError, call_engine


def enabled():
    value = os.environ.get("FIRESKY_GFS_DATA_BACKEND", "python").strip().lower()
    if value not in {"python", "rust"}:
        raise ValueError("FIRESKY_GFS_DATA_BACKEND must be python or rust")
    return value == "rust"


def _cycle(value):
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _call(params, *, download=False):
    try:
        # Transfer timeout is applied to each blocking network operation in
        # Rust, as in urllib; a whole-subprocess deadline truncates large files.
        # A stale matching row can require a second 300-second index lock.
        result = call_engine("gfs_data", params, timeout_seconds=None if download else 670)
    except EngineOperationError as exc:
        if str(exc) == "GFS cache lock timed out":
            raise TimeoutError("等待 GFS 文件锁超时。") from exc
        raise
    if not isinstance(result, dict):
        raise RuntimeError("Rust GFS data response must be an object")
    return result


def build_url(client, cycle, hour, bbox, diagnostic=False):
    result = _call(dict(operation="url", cycle_utc=_cycle(cycle), forecast_hour=int(hour),
                        bbox=bbox, resolution=client.resolution,
                        include_diagnostic_fields=diagnostic))
    if not isinstance(result.get("url"), str):
        raise RuntimeError("Rust GFS URL response is invalid")
    return result["url"]


def _config(client):
    return dict(cache_dir=str(client.cache_dir), resolution=client.resolution,
                region=client.region, timeout_seconds=client.timeout_seconds,
                download_attempts=client.download_attempts)


def find(client, cycle, hour, bbox, url=None):
    result = _call(dict(operation="find", **_config(client), cycle_utc=_cycle(cycle),
                        forecast_hour=int(hour), bbox=bbox, url=url))
    if result.get("contract") != "rust-gfs-data-v1":
        raise RuntimeError("Rust GFS cache response identity is invalid")
    path = result.get("path")
    if path is None:
        return None
    if not isinstance(path, str) or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise RuntimeError("Rust GFS cache response path is invalid")
    return Path(path)


def download(client, url, cycle=None, hour=None, bbox=None):
    result = _call(dict(operation="download", **_config(client), url=url,
                        cycle_utc=_cycle(cycle), forecast_hour=hour, bbox=bbox), download=True)
    if result.get("contract") != "rust-gfs-data-v1":
        raise RuntimeError("Rust GFS download response identity is invalid")
    path = result.get("path")
    if not isinstance(path, str) or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise RuntimeError("Rust GFS download response path is invalid")
    return Path(path)
