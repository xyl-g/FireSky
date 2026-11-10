"""Opt-in CAMS ADS data access. No Python network fallback on Rust failure."""
from datetime import datetime, timezone
import os
from pathlib import Path

from firesky.rust_grid import EngineOperationError, call_engine


def enabled():
    value = os.environ.get("FIRESKY_CAMS_DATA_BACKEND", "python").strip().lower()
    if value not in {"python", "rust"}:
        raise ValueError("FIRESKY_CAMS_DATA_BACKEND must be python or rust")
    return value == "rust"


def download(provider, area, targets, *, batch, exact=False, refresh=None):
    if batch and not targets:
        return {}
    if any(value.tzinfo is None or value.utcoffset() is None for value in targets):
        raise ValueError("CAMS target times must be timezone-aware")
    if refresh is not None and (refresh.tzinfo is None or refresh.utcoffset() is None):
        raise ValueError("CAMS refresh cycle must be timezone-aware")
    requested = {value.astimezone(timezone.utc): value for value in targets}
    params = dict(
        operation="batch" if batch else "download",
        cache_dir=str(provider.cache_dir), area=list(area),
        target_utcs=[value.astimezone(timezone.utc).isoformat() for value in targets],
        allow_download=True, require_exact_valid_time=exact,
        refresh_before_cycle=refresh.astimezone(timezone.utc).isoformat() if refresh else None,
        config=dict(timeout_seconds=max(120, provider.timeout_seconds),
                    request_attempts=max(3, provider.retry_max),
                    poll_max_seconds=max(5, provider.sleep_max_seconds)),
    )
    try:
        result = call_engine("cams_data", params, timeout_seconds=None)
    except EngineOperationError as exc:
        if str(exc) in {"CAMS download slot timed out", "CAMS cache lock timed out"}:
            raise TimeoutError("CAMS download/cache lock timed out") from exc
        raise
    if not isinstance(result, dict) or result.get("contract") != "rust-cams-data-v1":
        raise RuntimeError("Rust CAMS download response contract is invalid")
    rows = result.get("files")
    if not isinstance(rows, list):
        raise RuntimeError("Rust CAMS download response files are invalid")
    paths = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise RuntimeError("Rust CAMS download response path is invalid")
        target = datetime.fromisoformat(row["requested_valid_time"])
        path = Path(row["path"])
        if target not in requested or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Rust CAMS download returned a missing/unrequested file")
        paths[requested[target]] = path
    if batch:
        return paths
    if len(paths) != 1:
        raise RuntimeError("Rust CAMS single download did not return exactly one path")
    return next(iter(paths.values()))
