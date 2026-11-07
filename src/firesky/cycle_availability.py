"""Query actual cycle availability from upstream APIs (NOMADS, ECMWF opendata, CDS, DWD).

This is the SINGLE source of truth for "latest available cycle" across the
original codebase this engine was ported from. Time-based heuristics (choose_gfs_cycle, choose_ecmwf_cycle,
_pick_cams_cycle_utc) are only used as the *starting point* for probing — the
final answer is always verified against the real API.

All queries share an in-memory TTL cache (default 5 minutes) to avoid
hammering upstream APIs on every status poll.
"""
from __future__ import annotations

import threading
import time
import urllib.request
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

T = TypeVar("T")


_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes
_LOGGER = logging.getLogger(__name__)
_LAST_SOURCES: dict[str, str] = {}

# A published GFS directory can appear before all forecast files are indexed.
# Probe one small, standard GRIB2 file instead of treating the directory itself
# as proof that the cycle is usable.  f006 is early enough to be present for
# every operational cycle and exercises the same product family used by the
# downloader.
_GFS_PROBE_FORECAST_HOUR = 6


def _cached_get(key: str, compute: Callable[[], T]) -> T:
    """Thread-safe TTL cache wrapper. Key must encode the input parameters."""
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and now - entry[0] < _CACHE_TTL_SECONDS:
            return entry[1]  # type: ignore[return-value]
    value = compute()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value  # type: ignore[return-value]


def clear_cycle_availability_cache() -> None:
    """Flush the in-memory cache. Call after a manual download to force a refresh."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _LAST_SOURCES.clear()


def get_cycle_source(source: str) -> str | None:
    """Return the probe provenance recorded by the latest query for source."""
    with _CACHE_LOCK:
        return _LAST_SOURCES.get(source)


def _record_source(source: str, provenance: str) -> None:
    with _CACHE_LOCK:
        _LAST_SOURCES[source] = provenance


def _http_head_ok(url: str, timeout: float = 6.0) -> bool:
    """HEAD request that returns True for 2xx, False for anything else."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "firesky/0.1 burning-sunset-engine"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _LOGGER.info("cycle probe url=%s status=%s", url, resp.status)
            return 200 <= resp.status < 300
    except Exception as exc:
        _LOGGER.warning("cycle probe failed url=%s error=%s", url, exc)
        return False


def _gfs_cycle_probe_url(cycle: datetime) -> str:
    """Return a concrete operational GFS file used for availability probing."""
    cycle = cycle.astimezone(timezone.utc)
    return (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
        f"gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/"
        f"gfs.t{cycle:%H}z.pgrb2.0p25.f{_GFS_PROBE_FORECAST_HOUR:03d}"
    )


def query_latest_gfs_cycle(
    now_utc: datetime,
    timeout: float = 6.0,
    *,
    record_provenance: bool = True,
) -> datetime:
    """Probe NOMADS for the latest published GFS cycle.

    Strategy: start from the time-based heuristic (choose_gfs_cycle, 4h lag),
    then walk back through (06, 12, 18, 00) one cycle at a time, HTTP-HEADing
    a concrete early forecast file for each. Return the first cycle whose
    product file responds 200; directory existence alone is not sufficient.
    If all probes fail (network outage), fall back to the bare heuristic so the
    UI doesn't break — but the download will then fail loudly at the API call.

    ``record_provenance=False`` keeps request-scoped callers (nowcast cycle
    resolution) from overwriting the scheduler-owned probe provenance.
    """
    from firesky.gfs import choose_gfs_cycle

    def compute() -> datetime:
        candidate = choose_gfs_cycle(now_utc)
        _LOGGER.info("GFS cycle probe start now=%s heuristic=%s", now_utc.isoformat(), candidate.isoformat())
        for _ in range(4):  # 4 six-hour steps back = one full day
            url = _gfs_cycle_probe_url(candidate)
            if _http_head_ok(url, timeout=timeout):
                _LOGGER.info("GFS cycle selected=%s", candidate.isoformat())
                if record_provenance:
                    _record_source("gfs", "provider")
                return candidate
            candidate = candidate - timedelta(hours=6)
        fallback = choose_gfs_cycle(now_utc)
        if record_provenance:
            _record_source("gfs", "heuristic")
        return fallback

    return _cached_get(f"gfs:{now_utc:%Y%m%d%H}", compute)


def query_latest_ecmwf_cycle(now_utc: datetime, timeout: float = 6.0) -> datetime:
    """Probe ECMWF opendata for the latest published cycle.

    Strategy: start from the time-based heuristic (choose_ecmwf_cycle, 7h lag),
    then walk back through (00, 12) one cycle at a time, HTTP-HEADing the
    .index file on each of the three ECMWF opendata sources (ecmwf, aws, google).
    Return the first cycle where any source confirms.
    """
    from firesky.ecmwf import ecmwf_index_available, OPEN_DATA_SOURCE_URLS, choose_ecmwf_cycle

    sources = list(OPEN_DATA_SOURCE_URLS.keys())

    def compute() -> datetime:
        candidate = choose_ecmwf_cycle(now_utc)
        _LOGGER.info("ECMWF cycle probe start now=%s heuristic=%s", now_utc.isoformat(), candidate.isoformat())
        for _ in range(2):  # 2 twelve-hour steps back = one full day
            for source in sources:
                if ecmwf_index_available(source, candidate, forecast_hour=0, timeout_seconds=int(timeout)):
                    _LOGGER.info("ECMWF cycle selected=%s source=%s", candidate.isoformat(), source)
                    _record_source("ecmwf", "provider")
                    return candidate
            candidate = candidate - timedelta(hours=12)
        fallback = choose_ecmwf_cycle(now_utc)
        _record_source("ecmwf", "heuristic")
        return fallback

    return _cached_get(f"ecmwf:{now_utc:%Y%m%d%H}", compute)




def query_latest_cams_cycle(now_utc: datetime, timeout: float = 12.0) -> datetime:
    """Probe CDS API for the latest published CAMS cycle.

    Strategy: call choose_best_cams_selection which queries CDS apply_constraints
    to find what (date, time, lead) tuples are actually published. The returned
    run_date/run_time is the latest cycle CDS has on tap.

    If CDS is unreachable (network outage, API key missing, etc.), fall back to
    a 10h publication-delay heuristic — CAMS 12Z run typically appears on CDS
    ~10h after start, verified empirically.
    """
    def compute() -> datetime:
        try:
            import requests as req_lib
            import cdsapi
            from firesky.aerosol import (
                CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS,
                choose_best_cams_selection,
            )
        except ImportError:
            result = _cams_heuristic(now_utc)
            _record_source("cams", "heuristic")
            return result

        try:
            session = req_lib.Session()
            session.trust_env = False
            client = cdsapi.Client(quiet=True, progress=False, timeout=int(timeout),
                                   retry_max=1, sleep_max=1, session=session)
            process = client.client.get_process("cams-global-atmospheric-composition-forecasts")
            base_query = {
                "variable": [
                    "total_aerosol_optical_depth_550nm",
                    "aerosol_extinction_coefficient_532nm",
                ],
                "pressure_level": [
                    str(level) for level in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
                ],
                "type": ["forecast"],
                "data_format": "grib",
            }
            constrained = process.apply_constraints(base_query)
            # mid_target = now + 36h (middle of the 0-72h lead range, mirrors
            # the download side's _handle_cams_download).
            target = now_utc.replace(minute=0, second=0, microsecond=0) + timedelta(hours=36)
            selection = choose_best_cams_selection(process, base_query, constrained, target)
            if selection is not None:
                run_date, run_time, _ = selection
                run_hour = int(run_time.split(":", 1)[0])
                result = datetime(run_date.year, run_date.month, run_date.day, run_hour, tzinfo=timezone.utc)
                _LOGGER.info("CAMS cycle selected=%s", result.isoformat())
                _record_source("cams", "provider")
                return result
        except Exception as exc:
            _LOGGER.warning("CAMS cycle probe failed error=%s", exc)
        result = _cams_heuristic(now_utc)
        _record_source("cams", "heuristic")
        _LOGGER.info("CAMS cycle fallback selected=%s", result.isoformat())
        return result

    return _cached_get(f"cams:{now_utc:%Y%m%d%H}", compute)


def _cams_heuristic(now_utc: datetime) -> datetime:
    """Fallback when CDS API is unreachable. 12h step + 10h publication delay."""
    step_h, offset_h = 12, 10
    utc_h = now_utc.hour - offset_h
    aligned = (utc_h // step_h) * step_h
    cycle = datetime(now_utc.year, now_utc.month, now_utc.day,
                     (aligned % 24 + 24) % 24, tzinfo=timezone.utc)
    if aligned < 0:
        cycle -= timedelta(days=1)
    return cycle
