"""Opt-in EC public/basic raw data access, with no SDK fallback."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

from firesky.rust_grid import EngineOperationError, call_engine


def enabled():
    value = os.environ.get("FIRESKY_ECMWF_DATA_BACKEND", "python").strip().lower()
    if value not in {"python", "rust"}:
        raise ValueError("FIRESKY_ECMWF_DATA_BACKEND must be python or rust")
    return value == "rust"


def _config(cache_dir, source, timeout=90.0):
    return dict(cache_dir=str(cache_dir), source=source, timeout_seconds=timeout)


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("EC cycle/evaluation times must be aware UTC")
    return value.isoformat()


def _call(params):
    result = call_engine("ecmwf_data", params, timeout_seconds=None)
    if not isinstance(result, dict) or result.get("contract") != "rust-ecmwf-data-v1":
        raise RuntimeError("Rust EC data response contract is invalid")
    return result


def download(adapter, cycle, hour):
    if adapter.resolution != "0p25":
        raise ValueError("Rust EC public/basic adapter requires 0p25")
    try:
        result = _call(dict(operation="download", config=_config(adapter.cache_dir, adapter.source),
                            cycle_utc=_utc(cycle), forecast_hour=hour, allow_download=True))
    except EngineOperationError as exc:
        if str(exc) == "EC cache lock timed out":
            raise TimeoutError("EC cache lock timed out") from exc
        raise
    rows = result.get("files")
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError("Rust EC download did not return exactly one path")
    row = rows[0]
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        raise RuntimeError("Rust EC download response path is invalid")
    path = Path(row["path"])
    expected = adapter.cache_path(adapter.cache_dir, cycle, hour)
    if row.get("forecast_hour") != hour or path.resolve() != expected.resolve() or not path.is_file():
        raise RuntimeError("Rust EC download returned a missing/unrequested file")
    return path


def probe(source, cycle, hour, timeout):
    result = _call(dict(operation="probe", config=_config(".cache/ecmwf", source, timeout),
                        cycle_utc=_utc(cycle), forecast_hour=hour))
    rows = result.get("files")
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get("forecast_hour") != hour or not isinstance(rows[0].get("available"), bool)):
        raise RuntimeError("Rust EC probe response is invalid")
    return rows[0]["available"]


def choose_cycle(reference, evaluations, *, cache_dir, available, source, timeout, max_cycles):
    values = [reference, *(evaluations), *([available] if available is not None else [])]
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
           for value in values):
        raise ValueError("EC cycle selection times must be timezone-aware")
    available = available or datetime.now(timezone.utc)
    result = _call(dict(operation="plan",
                        config=_config(cache_dir if cache_dir is not None else ".cache/ecmwf", source, timeout),
                        reference_utc=_utc(reference.astimezone(timezone.utc)),
                        now_utc=_utc(available.astimezone(timezone.utc)),
                        evaluation_utcs=[_utc(value.astimezone(timezone.utc)) for value in evaluations],
                        max_cycles=max_cycles, use_cache_for_cycle=cache_dir is not None))
    return datetime.fromisoformat(result["cycle_utc"].replace("Z", "+00:00"))
