"""Calibrated satellite cloud-column retrieval.

This module deliberately keeps the retrieval contract separate from the
display-image nowcast code.  JPEG/PNG display values are not radiances and
cannot create scoring features.  A scoring column therefore requires a
calibrated frame and a matching numerical-model vertical prior.
"""

from __future__ import annotations

import math
import hashlib
import json
import logging
import multiprocessing
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import warnings
import xml.etree.ElementTree as ET
import uuid
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np
import requests

from firesky.geometry import haversine_km
from firesky.bounded_lock import acquire_file_lock
from firesky.version import NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION
from firesky.models import GeoPoint


SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION = NOWCAST_CLOUD_COLUMN_SCHEMA_VERSION
SATELLITE_CALIBRATED_FRAME_SCHEMA_VERSION = 1
SATELLITE_CLOUD_COLUMN_ALGORITHM = "ahi-calibrated-cloud-column-v4"
HIMAWARI_PROJECTION_IDENTITY = "ahi-geos9-himawari9-full-disk-5500x5500-v1"
HSD_COMPACT_CACHE_ALGORITHM = "ahi-hsd-calibrated-frame-v1"
HSD_COMPACT_CACHE_SCHEMA_VERSION = 2
HSD_DEFAULT_DOWNLOAD_WORKERS = 6
HSD_MAX_DOWNLOAD_WORKERS = 6
HSD_RANGE_CHUNKS_PER_OBJECT = 4
HSD_RANGE_MULTIPART_THRESHOLD_BYTES = 256 * 1024
HSD_MAX_RANGE_WORKERS = 12
HSD_CACHE_LOCK_TIMEOUT_SECONDS = 300.0
HSD_FOREGROUND_WAIT_TIMEOUT_SECONDS = 300.0
HIMAWARI_HSD_PROJECTION_IDENTITY = HIMAWARI_PROJECTION_IDENTITY
DEFAULT_HSD_CACHE_DIR = Path(".cache/himawari-hsd")
DEFAULT_HSD_COMPACT_CACHE_DIR = DEFAULT_HSD_CACHE_DIR / "compact"
# A larger block materially reduces NOAA Range round trips on a cold cache.
# Existing 1 MiB manifests are still resumed with their original geometry;
# changing the block size must never discard a partially downloaded scan.

_HSD_RANGE_EXECUTOR: Optional[ThreadPoolExecutor] = None
_HSD_RANGE_EXECUTOR_LOCK = threading.Lock()
_HSD_UPSTREAM_EXECUTOR: Optional[ThreadPoolExecutor] = None
_HSD_UPSTREAM_EXECUTOR_LOCK = threading.Lock()
# Foreground leaders run in their requesting thread, so this semaphore is the
# only foreground remote slot. The separate background executor adds one more
# bounded slot without ever queueing behind a foreground request.
_HSD_FOREGROUND_SLOT = threading.BoundedSemaphore(1)
# In-flight foreground leaders only; callers wait on these futures, so this is
# not a remote-work queue and entries disappear when each leader returns.
_HSD_UPSTREAM_FUTURES: dict[tuple[str, datetime, float, float, float], Future[Any]] = {}
# This dictionary intentionally has at most one entry: submission is skipped
# while that future is running or waiting, so the executor cannot accumulate a
# background FIFO behind a slow scan.
_HSD_BACKGROUND_FUTURES: dict[tuple[str, datetime, float, float, float], Future[Any]] = {}
_HSD_UPSTREAM_FUTURE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


_OBSOLETE_COMPACT_CACHE_MARKERS = (
    "cache schema is obsolete",
    "cache algorithm is obsolete",
    "cloud-column schema is obsolete",
    "calibrated frame schema is obsolete",
    "compact HSD frame schema is obsolete",
)


def _discard_obsolete_compact_cache(path: Path, error: BaseException) -> bool:
    """Delete an obsolete compact entry so the next read can refill it.

    Compact caches are derived artifacts, not a compatibility surface.  An old
    schema must never be interpreted as a cloud observation, but it also must
    not poison a live request before the remote warmer gets a chance to fetch
    the current frame.
    """
    if not any(marker in str(error) for marker in _OBSOLETE_COMPACT_CACHE_MARKERS):
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        _LOGGER.warning("无法删除过时的卫星 compact 缓存 %s", path, exc_info=True)
    else:
        _LOGGER.info("已删除过时的卫星 compact 缓存 %s", path)
    return True


def _hsd_range_executor() -> ThreadPoolExecutor:
    """Return the process-wide bounded executor for HSD byte ranges."""
    global _HSD_RANGE_EXECUTOR
    with _HSD_RANGE_EXECUTOR_LOCK:
        if _HSD_RANGE_EXECUTOR is None:
            _HSD_RANGE_EXECUTOR = ThreadPoolExecutor(
                max_workers=HSD_MAX_RANGE_WORKERS,
                thread_name_prefix="himawari-hsd-range",
            )
        return _HSD_RANGE_EXECUTOR


def _hsd_upstream_executor() -> ThreadPoolExecutor:
    """Return the process-wide single-slot executor for background HSD work."""
    global _HSD_UPSTREAM_EXECUTOR
    with _HSD_UPSTREAM_EXECUTOR_LOCK:
        if _HSD_UPSTREAM_EXECUTOR is None:
            _HSD_UPSTREAM_EXECUTOR = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="himawari-hsd-upstream",
            )
        return _HSD_UPSTREAM_EXECUTOR


def _hsd_request_key(
    cache_root: str | Path,
    reference_utc: datetime,
    point: Any,
    radius_km: float,
) -> tuple[str, datetime, float, float, float]:
    """Build the bounded key used for foreground/background HSD single flight."""
    _require_utc(reference_utc, "Himawari HSD request reference time")
    bucket = reference_utc.replace(
        minute=(reference_utc.minute // 10) * 10,
        second=0,
        microsecond=0,
    )
    return (
        str(Path(cache_root).resolve()),
        bucket,
        float(point.lat),
        float(point.lon),
        float(radius_km),
    )


def _finish_hsd_background_future(
    key: tuple[str, datetime, float, float, float],
    future: Future[Any],
) -> None:
    try:
        future.result()
    except Exception:
        _LOGGER.warning("HSD background frame fetch failed for %s", key, exc_info=True)
    finally:
        with _HSD_UPSTREAM_FUTURE_LOCK:
            if _HSD_BACKGROUND_FUTURES.get(key) is future:
                _HSD_BACKGROUND_FUTURES.pop(key, None)


def _submit_hsd_background(
    key: tuple[str, datetime, float, float, float],
    work: Callable[[], Any],
) -> bool:
    """Try to submit one background task; never queue behind another task."""
    with _HSD_UPSTREAM_FUTURE_LOCK:
        active = next(iter(_HSD_BACKGROUND_FUTURES.items()), None)
        if active is not None:
            active_key, active_future = active
            if not active_future.done():
                return False
            if _HSD_BACKGROUND_FUTURES.get(active_key) is active_future:
                _HSD_BACKGROUND_FUTURES.pop(active_key, None)
        future = _hsd_upstream_executor().submit(work)
        _HSD_BACKGROUND_FUTURES[key] = future
    future.add_done_callback(
        lambda completed: _finish_hsd_background_future(key, completed)
    )
    return True


def _run_hsd_foreground(
    key: tuple[str, datetime, float, float, float],
    work: Callable[[], Any],
) -> Any:
    """Run one foreground leader synchronously while sharing its result."""
    with _HSD_UPSTREAM_FUTURE_LOCK:
        future = _HSD_UPSTREAM_FUTURES.get(key)
        if future is None:
            future = Future()
            _HSD_UPSTREAM_FUTURES[key] = future
            leader = True
        else:
            leader = False
    if not leader:
        return future.result(timeout=HSD_FOREGROUND_WAIT_TIMEOUT_SECONDS)

    try:
        if not _HSD_FOREGROUND_SLOT.acquire(
            timeout=HSD_FOREGROUND_WAIT_TIMEOUT_SECONDS
        ):
            raise TimeoutError("等待 HSD 前台下载槽超时。")
        try:
            result = work()
        finally:
            _HSD_FOREGROUND_SLOT.release()
    except BaseException as exc:
        future.set_exception(exc)
        raise
    else:
        future.set_result(result)
        return result
    finally:
        with _HSD_UPSTREAM_FUTURE_LOCK:
            if _HSD_UPSTREAM_FUTURES.get(key) is future:
                _HSD_UPSTREAM_FUTURES.pop(key, None)



def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


LOW_CLOUD_STATES = frozenset({"detected", "clear", "unknown"})
RETRIEVAL_QUALITIES = frozenset({"high", "medium", "low", "unknown"})
RETRIEVAL_QUALITIES_ORDER = {"unknown": 0, "low": 1, "medium": 2, "high": 3}


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must use UTC offset +00:00")


@dataclass(frozen=True)
class CalibratedSatelliteFrame:
    """A geolocated, calibrated AHI observation.

    ``bands`` contains calibrated brightness temperature (K) for thermal
    channels and either reflectance or radiance for visible/NIR channels.  A
    display image is intentionally not accepted by this type.
    """

    valid_time_utc: datetime
    latitudes: np.ndarray
    longitudes: np.ndarray
    bands: Mapping[str, np.ndarray]
    units: Mapping[str, str]
    source: str
    quality: str = "unknown"
    schema_version: int = SATELLITE_CALIBRATED_FRAME_SCHEMA_VERSION
    range_bytes: int = 0
    # Number of remote cache blocks fetched while preparing this compact
    # frame. fsspec's mmap miss counter is not an HTTP request counter.
    range_blocks: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != SATELLITE_CALIBRATED_FRAME_SCHEMA_VERSION:
            raise ValueError("calibrated satellite frame schema is obsolete")
        _require_utc(self.valid_time_utc, "calibrated satellite frame time")
        if self.quality not in RETRIEVAL_QUALITIES:
            raise ValueError("calibrated satellite frame quality is invalid")
        if self.range_bytes < 0:
            raise ValueError("calibrated satellite range byte count is invalid")
        if self.range_blocks < 0:
            raise ValueError("calibrated satellite range block count is invalid")
        lats = np.asarray(self.latitudes)
        lons = np.asarray(self.longitudes)
        if lats.ndim not in {1, 2} or lons.ndim != lats.ndim or lats.size == 0 or lons.size == 0:
            raise ValueError("calibrated satellite frame coordinates must be non-empty 1-D or 2-D arrays")
        if np.any(~np.isfinite(lats)) or np.any(~np.isfinite(lons)):
            raise ValueError("calibrated satellite frame coordinates contain non-finite values")
        if lats.ndim == 1:
            shape = (lats.size, lons.size)
        elif lats.shape != lons.shape:
            raise ValueError("2-D calibrated satellite coordinates must have matching shapes")
        else:
            shape = lats.shape
        for channel, values in self.bands.items():
            try:
                array = np.asarray(values, dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"calibrated channel {channel} is not numeric") from exc
            if array.shape != shape:
                raise ValueError(f"calibrated channel {channel} shape does not match the grid")
            if channel not in self.units:
                raise ValueError(f"calibrated channel {channel} has no unit")
            # CF fill values are decoded to NaN and become per-pixel unknown.
            # Infinity is still malformed, but a missing pixel must not poison
            # an otherwise usable calibrated frame.
            if np.any(np.isinf(array)):
                raise ValueError(f"calibrated channel {channel} contains infinite values")


@dataclass(frozen=True)
class VerticalPriorLevel:
    pressure_hpa: int
    height_asl_m: float
    temperature_k: Optional[float]
    rh_percent: Optional[float]
    model_cloud_fraction: Optional[float]

    def __post_init__(self) -> None:
        if self.pressure_hpa <= 0 or not math.isfinite(self.height_asl_m):
            raise ValueError("vertical prior level is invalid")
        if self.temperature_k is not None and (
            not math.isfinite(self.temperature_k) or not 150.0 <= self.temperature_k <= 350.0
        ):
            raise ValueError("vertical prior temperature is invalid")
        if self.rh_percent is not None and (
            not math.isfinite(self.rh_percent) or not 0.0 <= self.rh_percent <= 100.0
        ):
            raise ValueError("vertical prior humidity is invalid")
        if self.model_cloud_fraction is not None and (
            not math.isfinite(self.model_cloud_fraction)
            or not 0.0 <= self.model_cloud_fraction <= 1.0
        ):
            raise ValueError("vertical prior cloud fraction is invalid")


@dataclass(frozen=True)
class VerticalPrior:
    """A same-cycle GFS column and terrain elevation at one location."""

    valid_time_utc: datetime
    cycle_time_utc: datetime
    forecast_hour: int
    source: str
    terrain_asl_m: float
    levels: tuple[VerticalPriorLevel, ...]
    identity: str
    target_lat: float
    target_lon: float

    def __post_init__(self) -> None:
        _require_utc(self.valid_time_utc, "vertical prior valid time")
        _require_utc(self.cycle_time_utc, "vertical prior cycle time")
        if not math.isfinite(self.terrain_asl_m) or not -500.0 <= self.terrain_asl_m <= 9000.0:
            raise ValueError("vertical prior terrain is invalid")
        if self.forecast_hour < 0:
            raise ValueError("vertical prior forecast hour is invalid")
        if not math.isfinite(self.target_lat) or not -90.0 <= self.target_lat <= 90.0:
            raise ValueError("vertical prior latitude is invalid")
        if not math.isfinite(self.target_lon) or not -180.0 <= self.target_lon <= 180.0:
            raise ValueError("vertical prior longitude is invalid")
        if not self.levels:
            raise ValueError("vertical prior has no levels")
        if not self.identity:
            raise ValueError("vertical prior identity is required")
        heights = [level.height_asl_m for level in self.levels]
        if heights != sorted(heights):
            raise ValueError("vertical prior levels must be ordered from low to high")
        pressures = [level.pressure_hpa for level in self.levels]
        if len(set(pressures)) != len(pressures) or any(
            left <= right for left, right in zip(pressures, pressures[1:])
        ):
            raise ValueError("vertical prior pressure must decrease with height")


@dataclass(frozen=True)
class SatelliteCloudColumn:
    """One retrieved layer, with explicit base uncertainty in ASL and AGL."""

    schema_version: int
    algorithm: str
    valid_time_utc: datetime
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    base_p10_asl_m: Optional[float]
    base_p50_asl_m: Optional[float]
    base_p90_asl_m: Optional[float]
    top_asl_m: Optional[float]
    terrain_asl_m: Optional[float]
    phase: str
    optical_depth: Optional[float]
    cloud_probability_by_height: Mapping[float, float]
    horizontal_cloud_probability: Optional[float]
    low_cloud_state: str
    retrieval_quality: str
    source: str
    vertical_prior_identity: str
    vertical_prior_source: str
    cloud_type: str = "unknown"
    base_source: str = "unknown"
    motion_dx_px_per_hour: float = 0.0
    motion_dy_px_per_hour: float = 0.0

    def __post_init__(self) -> None:
        if self.schema_version != SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION:
            raise ValueError("satellite cloud-column schema is obsolete")
        if self.algorithm != SATELLITE_CLOUD_COLUMN_ALGORITHM:
            raise ValueError("satellite cloud-column algorithm is obsolete")
        _require_utc(self.valid_time_utc, "cloud-column time")
        if not math.isfinite(self.lat) or not -90.0 <= self.lat <= 90.0:
            raise ValueError("cloud-column latitude is invalid")
        if not math.isfinite(self.lon) or not -180.0 <= self.lon <= 180.0:
            raise ValueError("cloud-column longitude is invalid")
        if not math.isfinite(self.distance_km) or self.distance_km < 0.0:
            raise ValueError("cloud-column distance is invalid")
        if not math.isfinite(self.bearing_deg) or not 0.0 <= self.bearing_deg < 360.0:
            raise ValueError("cloud-column bearing is invalid")
        if self.low_cloud_state not in LOW_CLOUD_STATES:
            raise ValueError("low_cloud_state is invalid")
        if self.retrieval_quality not in RETRIEVAL_QUALITIES:
            raise ValueError("retrieval_quality is invalid")
        if not self.source or not self.vertical_prior_identity or not self.vertical_prior_source:
            raise ValueError("cloud-column source and vertical prior provenance are required")
        if self.phase not in {"water", "ice", "mixed", "unknown"}:
            raise ValueError("cloud phase is invalid")
        if self.horizontal_cloud_probability is not None and (
            not math.isfinite(self.horizontal_cloud_probability)
            or not 0.0 <= self.horizontal_cloud_probability <= 1.0
        ):
            raise ValueError("horizontal cloud probability is invalid")
        values = (self.base_p10_asl_m, self.base_p50_asl_m, self.base_p90_asl_m, self.top_asl_m)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("cloud-column heights are invalid")
        if self.base_p10_asl_m is not None and self.base_p50_asl_m is not None:
            if self.base_p10_asl_m > self.base_p50_asl_m:
                raise ValueError("cloud base p10 must not exceed p50")
        if self.base_p50_asl_m is not None and self.base_p90_asl_m is not None:
            if self.base_p50_asl_m > self.base_p90_asl_m:
                raise ValueError("cloud base p50 must not exceed p90")
        if self.base_p90_asl_m is not None and self.top_asl_m is not None:
            if self.base_p90_asl_m > self.top_asl_m + 1e-6:
                raise ValueError("cloud base p90 must not exceed cloud top")
        if self.terrain_asl_m is not None and self.base_p10_asl_m is not None:
            if self.base_p10_asl_m + 1e-6 < self.terrain_asl_m:
                raise ValueError("cloud base cannot be below terrain")
        if self.terrain_asl_m is not None and (
            not math.isfinite(self.terrain_asl_m) or not -500.0 <= self.terrain_asl_m <= 9000.0
        ):
            raise ValueError("cloud-column terrain is invalid")
        for height, probability in self.cloud_probability_by_height.items():
            if not math.isfinite(float(height)) or not 0.0 <= float(probability) <= 1.0:
                raise ValueError("cloud probability profile is invalid")
            if self.terrain_asl_m is not None and float(height) < self.terrain_asl_m - 1e-6:
                raise ValueError("cloud probability is below terrain")
            if self.top_asl_m is not None and float(height) > self.top_asl_m + 500.0:
                raise ValueError("cloud probability is above cloud top")
        if self.optical_depth is not None and (
            not math.isfinite(self.optical_depth) or not 0.0 <= self.optical_depth <= 20.0
        ):
            raise ValueError("cloud optical depth is invalid")
        if not math.isfinite(self.motion_dx_px_per_hour) or not math.isfinite(self.motion_dy_px_per_hour):
            raise ValueError("cloud-column motion is invalid")

    @property
    def base_p10_agl_m(self) -> Optional[float]:
        return _agl(self.base_p10_asl_m, self.terrain_asl_m)

    @property
    def base_p50_agl_m(self) -> Optional[float]:
        return _agl(self.base_p50_asl_m, self.terrain_asl_m)

    @property
    def base_p90_agl_m(self) -> Optional[float]:
        return _agl(self.base_p90_asl_m, self.terrain_asl_m)

    @property
    def top_agl_m(self) -> Optional[float]:
        return _agl(self.top_asl_m, self.terrain_asl_m)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm": self.algorithm,
            "valid_time_utc": self.valid_time_utc.isoformat(),
            "lat": self.lat,
            "lon": self.lon,
            "distance_km": self.distance_km,
            "bearing_deg": self.bearing_deg,
            "base_p10_asl_m": self.base_p10_asl_m,
            "base_p50_asl_m": self.base_p50_asl_m,
            "base_p90_asl_m": self.base_p90_asl_m,
            "base_p10_agl_m": self.base_p10_agl_m,
            "base_p50_agl_m": self.base_p50_agl_m,
            "base_p90_agl_m": self.base_p90_agl_m,
            "top_asl_m": self.top_asl_m,
            "top_agl_m": self.top_agl_m,
            "terrain_asl_m": self.terrain_asl_m,
            "phase": self.phase,
            "cloud_type": self.cloud_type,
            "optical_depth": self.optical_depth,
            "cloud_probability_by_height": {
                str(round(float(height), 1)): round(float(probability), 5)
                for height, probability in self.cloud_probability_by_height.items()
            },
            "horizontal_cloud_probability": self.horizontal_cloud_probability,
            "low_cloud_state": self.low_cloud_state,
            "retrieval_quality": self.retrieval_quality,
            "source": self.source,
            "vertical_prior_identity": self.vertical_prior_identity,
            "vertical_prior_source": self.vertical_prior_source,
            "base_source": self.base_source,
            "motion_dx_px_per_hour": self.motion_dx_px_per_hour,
            "motion_dy_px_per_hour": self.motion_dy_px_per_hour,
        }


@dataclass(frozen=True)
class SatelliteCloudColumnSet:
    schema_version: int
    valid_time_utc: datetime
    source: str
    vertical_prior_identity: str
    retrieval_quality: str
    low_cloud_state: str
    columns: tuple[SatelliteCloudColumn, ...] = field(default_factory=tuple)
    motion_dx_px_per_hour: float = 0.0
    motion_dy_px_per_hour: float = 0.0

    def __post_init__(self) -> None:
        if self.schema_version != SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION:
            raise ValueError("satellite cloud-column set schema is obsolete")
        _require_utc(self.valid_time_utc, "cloud-column set time")
        if not self.source or not self.vertical_prior_identity:
            raise ValueError("cloud-column set provenance is required")
        if self.low_cloud_state not in LOW_CLOUD_STATES:
            raise ValueError("cloud-column set low_cloud_state is invalid")
        if self.retrieval_quality not in RETRIEVAL_QUALITIES:
            raise ValueError("cloud-column set retrieval quality is invalid")
        if self.columns:
            if any(column.valid_time_utc != self.valid_time_utc for column in self.columns):
                raise ValueError("cloud-column times do not match the set time")
            if any(column.vertical_prior_identity != self.vertical_prior_identity for column in self.columns):
                raise ValueError("cloud-column vertical prior identity does not match")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "valid_time_utc": self.valid_time_utc.isoformat(),
            "source": self.source,
            "vertical_prior_identity": self.vertical_prior_identity,
            "retrieval_quality": self.retrieval_quality,
            "low_cloud_state": self.low_cloud_state,
            "motion_dx_px_per_hour": self.motion_dx_px_per_hour,
            "motion_dy_px_per_hour": self.motion_dy_px_per_hour,
            "columns": [column.as_dict() for column in self.columns],
        }


@dataclass(frozen=True)
class _HsdObject:
    band: str
    segment: int
    key: str
    etag: str
    last_modified: str
    scan_time_utc: datetime
    size_bytes: int

    @property
    def end_time_utc(self) -> datetime:
        return self.scan_time_utc + timedelta(minutes=10)


class SegmentedHimawariHsdFrameProvider:
    """Fetch calibrated Himawari L1b HSD segments for one footprint.

    HSD is deliberately used only as calibrated B07/B13/B15 brightness
    temperatures. It does not provide the official HCAI mask, phase, cloud
    height, or optical depth fields, so those fields are left unknown by the
    cloud-column retrieval. Raw files are shared by scan/band/segment across
    all point requests; only the compact frame is point-specific.
    """

    BUCKET_URL = "https://noaa-himawari9.s3.amazonaws.com"
    PREFIX = "AHI-L1b-FLDK"
    BANDS = ("07", "13", "15")
    SEGMENT_ROWS = 550
    TOTAL_SEGMENTS = 10
    RESOLUTION = "20"
    _KEY_RE = re.compile(
        r"(?:^|/)HS_H(?P<sat>\d+)_(?P<date>\d{8})_(?P<time>\d{4})"
        r"_B(?P<band>\d{2})_FLDK_R(?P<resolution>\d+)_S(?P<segment>\d{2})10\.DAT\.bz2$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_HSD_CACHE_DIR,
        *,
        max_age_minutes: float = 30.0,
        lookback_minutes: int = 60,
        max_workers: int = HSD_DEFAULT_DOWNLOAD_WORKERS,
        retries: int = 3,
        request_timeout_seconds: float = 20.0,
        min_coverage: float = 0.95,
        max_outlier_fraction: float = 0.02,
    ) -> None:
        if (
            max_age_minutes <= 0.0
            or lookback_minutes < 0
            or max_workers < 1
            or max_workers > HSD_MAX_DOWNLOAD_WORKERS
            or retries < 0
            or request_timeout_seconds <= 0.0
            or not 0.0 < min_coverage <= 1.0
            or not 0.0 <= max_outlier_fraction <= 1.0
        ):
            raise ValueError("Himawari HSD provider limits are invalid")
        self.cache_dir = Path(cache_dir)
        self.max_age = timedelta(minutes=float(max_age_minutes))
        self.lookback_minutes = int(lookback_minutes)
        self.max_workers = int(max_workers)
        self.retries = int(retries)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.min_coverage = float(min_coverage)
        self.max_outlier_fraction = float(max_outlier_fraction)

    def fetch(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
    ) -> Optional[CalibratedSatelliteFrame]:
        frames = self.fetch_series(reference_utc, point, radius_km=radius_km, count=1)
        return frames[0] if frames else None

    def fetch_series(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
        count: int = 2,
        exclude_valid_times: Sequence[datetime] = (),
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        """Fetch the newest complete scans, newest first.

        A scan is complete only when every required B07/B13/B15 segment for the
        footprint is present and decodes to calibrated Kelvin arrays.  Returning
        the available complete prefix lets a current observation remain usable
        when the preceding scan has not been published yet; callers must gate
        motion/advection on receiving two frames.
        """
        _require_utc(reference_utc, "Himawari HSD reference time")
        if not math.isfinite(float(radius_km)) or float(radius_km) <= 0.0:
            raise ValueError("Himawari HSD radius must be positive")
        if isinstance(count, bool) or not 1 <= int(count) <= 4:
            raise ValueError("Himawari HSD scan count must be between one and four")
        excluded_valid_times = frozenset(exclude_valid_times)
        for valid_time in excluded_valid_times:
            _require_utc(valid_time, "Himawari HSD excluded frame time")
        rows, cols = self._region_indices(point, float(radius_km))
        segments = self._required_segments(rows)
        selected = self._select_scans(
            reference_utc,
            segments,
            count=int(count),
            exclude_valid_times=excluded_valid_times,
        )
        frames: list[CalibratedSatelliteFrame] = []
        failures: list[str] = []
        for scan_time, objects in selected:
            raw_dir = self.cache_dir / "raw" / scan_time.strftime("%Y%m%dT%H%MZ")
            try:
                paths = self._download_scan(objects, raw_dir)
                frame = self._decode_frame(
                    paths,
                    scan_time,
                    point,
                    radius_km=float(radius_km),
                    rows=rows,
                    cols=cols,
                    objects=objects,
                )
            except Exception as exc:
                # One broken scan (missing segment, upstream 5xx, decode error)
                # must not hide the other complete scans in the same window.
                # Older scans remain valid nowcast observations.
                _LOGGER.warning(
                    "Himawari HSD scan %s unavailable: %s",
                    scan_time.strftime("%Y-%m-%dT%H:%MZ"),
                    exc,
                )
                failures.append(f"{scan_time.strftime('%Y-%m-%dT%H:%MZ')}: {exc}")
                continue
            if frame is not None:
                frames.append(frame)
        if not frames and failures:
            raise RuntimeError(
                "Himawari HSD 扫描全部不可用：" + "; ".join(failures)
            )
        if frames:
            self._prune_raw_scans(keep=max(3, int(count)))
        return tuple(frames)

    def _prune_raw_scans(self, *, keep: int) -> None:
        if keep < 1:
            raise ValueError("Himawari HSD raw-cache retention must be positive")
        raw_root = self.cache_dir / "raw"
        if not raw_root.is_dir():
            return
        scan_dirs = sorted(
            (
                path
                for path in raw_root.iterdir()
                if path.is_dir() and re.fullmatch(r"\d{8}T\d{4}Z", path.name)
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        # Historical replay and live nowcast share this directory.  Retaining
        # only the globally newest scans lets a live request delete the event
        # day's raw scans before the retrospective cache can reuse them. Keep
        # the newest ``keep`` scans independently for each UTC calendar day;
        # the general cache-prune task remains responsible for age-based disk
        # cleanup.
        by_day: dict[str, list[Path]] = {}
        for path in scan_dirs:
            by_day.setdefault(path.name[:8], []).append(path)
        for paths in by_day.values():
            for path in paths[keep:]:
                shutil.rmtree(path)

    @classmethod
    def _required_segments(cls, rows: tuple[int, int]) -> tuple[int, ...]:
        first = rows[0] // cls.SEGMENT_ROWS + 1
        last = (rows[1] - 1) // cls.SEGMENT_ROWS + 1
        return tuple(range(max(1, first), min(cls.TOTAL_SEGMENTS, last) + 1))

    def _select_scan(
        self,
        reference_utc: datetime,
        segments: Sequence[int],
    ) -> Optional[tuple[datetime, dict[tuple[str, int], _HsdObject]]]:
        selected = self._select_scans(reference_utc, segments, count=1)
        return selected[0] if selected else None

    def _select_scans(
        self,
        reference_utc: datetime,
        segments: Sequence[int],
        *,
        count: int,
        exclude_valid_times: Sequence[datetime] = (),
    ) -> list[tuple[datetime, dict[tuple[str, int], _HsdObject]]]:
        if isinstance(count, bool) or not 1 <= int(count) <= 4:
            raise ValueError("Himawari HSD scan count must be between one and four")
        excluded_valid_times = frozenset(exclude_valid_times)
        for valid_time in excluded_valid_times:
            _require_utc(valid_time, "Himawari HSD excluded frame time")
        # Listing two adjacent hour prefixes is enough for the normal 10-minute
        # publication cadence and avoids one S3 request per candidate scan.
        hours = max(1, math.ceil(self.lookback_minutes / 60.0))
        listing: list[_HsdObject] = []
        base = reference_utc.replace(minute=0, second=0, microsecond=0)
        for offset in range(-hours, 1):
            # L1b objects are stored below the UTC hour prefix, with the
            # ten-minute scan in the filename/directory below it. Listing the
            # hour avoids accidentally querying only HH00.
            listing.extend(self._list_prefix(f"{self.PREFIX}/{(base + timedelta(hours=offset)):%Y/%m/%d/%H}"))
        grouped: dict[datetime, dict[tuple[str, int], list[_HsdObject]]] = {}
        for item in listing:
            grouped.setdefault(item.scan_time_utc, {}).setdefault((item.band, item.segment), []).append(item)
        required = {(band, segment) for band in self.BANDS for segment in segments}
        candidates: list[tuple[datetime, dict[tuple[str, int], _HsdObject]]] = []
        for scan_time, buckets in grouped.items():
            if scan_time + timedelta(minutes=5) in excluded_valid_times:
                continue
            if not required.issubset(buckets) or any(len(buckets[key]) != 1 for key in required):
                continue
            objects = {key: buckets[key][0] for key in required}
            end_time = scan_time + timedelta(minutes=10)
            age = reference_utc - end_time
            if end_time > reference_utc or age < timedelta(0) or age > self.max_age:
                continue
            candidates.append((scan_time, objects))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[: int(count)]

    def _list_prefix(self, prefix: str) -> list[_HsdObject]:
        """List every matching object below ``prefix`` with full pagination.

        One scan-hour now holds close to a thousand objects; without paging,
        a ListObjectsV2 response truncated at ``max-keys`` silently hides the
        newest scans and a complete scan appears to be missing.
        """
        result: list[_HsdObject] = []
        continuation_token: Optional[str] = None
        namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        while True:
            query: dict[str, str] = {
                "list-type": "2",
                "max-keys": "1000",
                "prefix": prefix,
            }
            if continuation_token:
                query["continuation-token"] = continuation_token
            request = urllib.request.Request(
                f"{self.BUCKET_URL}/?{urllib.parse.urlencode(query)}",
                headers={"User-Agent": "firesky/0.1 burning-sunset-engine"},
            )
            with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                root = ET.fromstring(response.read())
            for content in root.findall("s3:Contents", namespace):
                key = content.findtext("s3:Key", default="", namespaces=namespace)
                match = self._KEY_RE.search(key)
                if match is None or match.group("band") not in self.BANDS:
                    continue
                if match.group("resolution") != self.RESOLUTION:
                    continue
                segment = int(match.group("segment"))
                if not 1 <= segment <= self.TOTAL_SEGMENTS:
                    continue
                scan_time = datetime.strptime(
                    f"{match.group('date')}{match.group('time')}", "%Y%m%d%H%M"
                ).replace(tzinfo=timezone.utc)
                size = int(content.findtext("s3:Size", default="0", namespaces=namespace))
                if size <= 0:
                    continue
                result.append(
                    _HsdObject(
                        band=match.group("band"),
                        segment=segment,
                        key=key,
                        etag=content.findtext("s3:ETag", default="", namespaces=namespace).strip('"'),
                        last_modified=content.findtext("s3:LastModified", default="", namespaces=namespace),
                        scan_time_utc=scan_time,
                        size_bytes=size,
                    )
                )
            if root.findtext("s3:IsTruncated", default="false", namespaces=namespace).strip().lower() != "true":
                break
            continuation_token = root.findtext(
                "s3:NextContinuationToken", default="", namespaces=namespace
            ).strip()
            if not continuation_token:
                break
        return result

    @staticmethod
    def _region_indices(point: Any, radius_km: float) -> tuple[tuple[int, int], tuple[int, int]]:
        try:
            from pyresample.geometry import AreaDefinition
        except ImportError as exc:
            raise RuntimeError("Himawari HSD provider requires pyresample") from exc
        area = _himawari_area_definition(AreaDefinition)
        radius_lat = radius_km / 111.2
        radius_lon = radius_km / max(20.0, 111.2 * math.cos(math.radians(float(point.lat))))
        rows: list[int] = []
        cols: list[int] = []
        for lat in (float(point.lat) - radius_lat, float(point.lat) + radius_lat):
            for lon in (float(point.lon) - radius_lon, float(point.lon) + radius_lon):
                col, row = area.get_array_indices_from_lonlat(lon, lat)
                rows.append(int(row))
                cols.append(int(col))
        r0, r1 = max(0, min(rows) - 4), min(5500, max(rows) + 5)
        c0, c1 = max(0, min(cols) - 4), min(5500, max(cols) + 5)
        if r1 <= r0 or c1 <= c0:
            raise RuntimeError("Himawari HSD point is outside the full disk")
        return (r0, r1), (c0, c1)

    def _download_scan(
        self,
        objects: Mapping[tuple[str, int], _HsdObject],
        raw_dir: Path,
    ) -> dict[tuple[str, int], Path]:
        raw_dir.mkdir(parents=True, exist_ok=True)
        result: dict[tuple[str, int], Path] = {}
        worker_count = min(self.max_workers, len(objects))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="himawari-hsd") as executor:
            futures = {
                executor.submit(self._download_one, item, raw_dir): identity
                for identity, item in objects.items()
            }
            for future in futures:
                result[futures[future]] = future.result()
        return result

    def _download_one(self, item: _HsdObject, raw_dir: Path) -> Path:
        target = raw_dir / Path(item.key).name
        lock_path = target.with_name(target.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock:
            acquire_file_lock(
                lock,
                timeout_seconds=HSD_CACHE_LOCK_TIMEOUT_SECONDS,
                description="Himawari HSD 缓存锁",
            )
            return self._download_one_locked(item, raw_dir)

    def _download_one_locked(self, item: _HsdObject, raw_dir: Path) -> Path:
        target = raw_dir / Path(item.key).name
        metadata_path = target.with_name(target.name + ".json")
        identity = {
            "key": item.key,
            "etag": item.etag,
            "last_modified": item.last_modified,
            "size_bytes": item.size_bytes,
            "band": item.band,
            "segment": item.segment,
            "scan_time_utc": item.scan_time_utc.isoformat(),
            "projection_identity": HIMAWARI_HSD_PROJECTION_IDENTITY,
        }
        if target.exists() and metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text())
                if metadata.get("identity") == identity and target.stat().st_size == item.size_bytes:
                    digest = _sha256_file(target)
                    if digest == metadata.get("sha256") and _etag_matches_file(item.etag, target):
                        return target
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        partial = target.with_name(target.name + f".part-{os.getpid()}-{uuid.uuid4().hex}")
        ranges = self._range_spans(item.size_bytes)
        futures = []
        try:
            executor = _hsd_range_executor()
            futures = [
                executor.submit(self._download_range, item, start, end)
                for start, end in ranges
            ]
            chunks = [future.result() for future in futures]
            with partial.open("wb") as output:
                for chunk in chunks:
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if partial.stat().st_size != item.size_bytes:
                raise IOError("Himawari HSD segment size does not match S3 listing")
            digest = _sha256_file(partial)
            if not _etag_matches_file(item.etag, partial):
                raise IOError("Himawari HSD segment ETag does not match downloaded bytes")
            os.replace(partial, target)
            metadata = {"identity": identity, "sha256": digest}
            temporary = metadata_path.with_name(metadata_path.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
            temporary.write_text(json.dumps(metadata, separators=(",", ":")))
            os.replace(temporary, metadata_path)
            return target
        except Exception as exc:
            for future in futures:
                future.cancel()
            raise RuntimeError(f"failed to download Himawari HSD segment {target.name}: {exc}") from exc
        finally:
            partial.unlink(missing_ok=True)

    @staticmethod
    def _range_spans(size_bytes: int) -> tuple[tuple[int, int], ...]:
        if isinstance(size_bytes, bool) or int(size_bytes) <= 0:
            raise ValueError("Himawari HSD segment size is invalid")
        if int(size_bytes) <= HSD_RANGE_MULTIPART_THRESHOLD_BYTES:
            return ((0, int(size_bytes) - 1),)
        chunk_count = min(HSD_RANGE_CHUNKS_PER_OBJECT, int(size_bytes))
        base, remainder = divmod(int(size_bytes), chunk_count)
        spans: list[tuple[int, int]] = []
        start = 0
        for index in range(chunk_count):
            length = base + (1 if index < remainder else 0)
            end = start + length - 1
            spans.append((start, end))
            start = end + 1
        return tuple(spans)

    def _download_range(self, item: _HsdObject, start: int, end: int) -> bytes:
        expected_length = end - start + 1
        last_error: Optional[BaseException] = None
        for _attempt in range(self.retries + 1):
            try:
                headers = {
                    "User-Agent": "firesky/0.1 burning-sunset-engine",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "Range": f"bytes={start}-{end}",
                }
                url = f"{self.BUCKET_URL}/{urllib.parse.quote(item.key)}"
                with requests.get(
                    url,
                    headers=headers,
                    stream=True,
                    timeout=(min(5.0, self.request_timeout_seconds), self.request_timeout_seconds),
                ) as response:
                    response.raise_for_status()
                    if int(response.status_code) != 206:
                        raise IOError("Himawari HSD Range response must use HTTP 206")
                    response_headers = response.headers
                    content_range = str(response_headers.get("Content-Range", "")).strip()
                    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", content_range)
                    if match is None:
                        raise IOError("Himawari HSD Range response has invalid Content-Range")
                    actual_start, actual_end, total = (int(value) for value in match.groups())
                    if (actual_start, actual_end, total) != (start, end, item.size_bytes):
                        raise IOError("Himawari HSD Range Content-Range does not match request")
                    content_length = response_headers.get("Content-Length")
                    if content_length is not None and int(content_length) != expected_length:
                        raise IOError("Himawari HSD Range Content-Length does not match request")
                    payload = b"".join(
                        chunk
                        for chunk in response.iter_content(chunk_size=1024 * 1024)
                        if chunk
                    )
                    if len(payload) != expected_length:
                        raise IOError("Himawari HSD Range response has incomplete bytes")
                    return payload
            except Exception as exc:
                last_error = exc
        raise IOError(
            f"Himawari HSD Range {start}-{end} failed after {self.retries + 1} attempts: {last_error}"
        ) from last_error

    def _decode_frame(
        self,
        paths: Mapping[tuple[str, int], Path],
        scan_time: datetime,
        point: Any,
        *,
        radius_km: float,
        rows: tuple[int, int],
        cols: tuple[int, int],
        objects: Mapping[tuple[str, int], _HsdObject],
    ) -> Optional[CalibratedSatelliteFrame]:
        try:
            from satpy import Scene
        except ImportError as exc:
            raise RuntimeError("Himawari HSD provider requires Satpy") from exc
        expected = {(band, segment) for band in self.BANDS for segment in self._required_segments(rows)}
        if set(paths) != expected:
            return None
        filenames = [str(paths[key]) for key in sorted(paths)]
        scene = Scene(filenames=filenames, reader="ahi_hsd")
        try:
            scene.load([f"B{band}" for band in self.BANDS])
            slices = (slice(rows[0], rows[1]), slice(cols[0], cols[1]))
            bands: dict[str, np.ndarray] = {}
            coordinates: Optional[tuple[np.ndarray, np.ndarray]] = None
            valid_times: set[datetime] = set()
            coverage: list[float] = []
            for band in self.BANDS:
                dataset = scene[f"B{band}"]
                if str(dataset.attrs.get("units", "")).strip() != "K":
                    return None
                area = dataset.attrs.get("area")
                if area is None or tuple(area.shape) != (5500, 5500):
                    return None
                start = dataset.attrs.get("start_time")
                end = dataset.attrs.get("end_time")
                if not isinstance(start, datetime) or not isinstance(end, datetime):
                    return None
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                else:
                    start = start.astimezone(timezone.utc)
                valid_times.add(start)
                # Satpy's HSD calibration uses a logarithm for some invalid
                # fill values before the calibrated array reaches this
                # provider.  Those pixels are rejected by the finite/range
                # checks below; suppress only that expected warning so a
                # historical replay is not flooded with identical messages.
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r"invalid value encountered in log",
                        category=RuntimeWarning,
                    )
                    values = np.asarray(dataset.data[slices].compute(), dtype=np.float32)
                finite = np.isfinite(values)
                fraction = float(np.count_nonzero(finite) / max(1, values.size))
                coverage.append(fraction)
                # A small number of fill pixels can occur at the geostationary
                # disk edge. They remain NaN/unknown at sample time; only a
                # footprint with insufficient finite coverage is unavailable.
                if fraction < self.min_coverage or np.any(np.isinf(values)):
                    return None
                # Calibration fill values can land inside the footprint as a
                # handful of out-of-range pixels. Rejecting one whole ten-minute
                # scan for them wastes the observation; mask them to NaN (the
                # sample-time unknown state) and reject only when the band is
                # dominated by them.
                outliers = finite & ((values < 100.0) | (values > 400.0))
                if float(np.count_nonzero(outliers) / max(1, values.size)) > self.max_outlier_fraction:
                    return None
                if np.any(outliers):
                    values = np.where(outliers, np.float32(np.nan), values)
                bands[f"B{band}"] = values
                if coordinates is None:
                    longitude, latitude = area.get_lonlats(data_slice=slices, dtype=np.float32)
                    coordinates = (np.asarray(latitude, dtype=np.float32), np.asarray(longitude, dtype=np.float32))
            if valid_times != {scan_time} or coordinates is None:
                return None
            latitudes, longitudes = coordinates
            if np.any(~np.isfinite(latitudes)) or np.any(~np.isfinite(longitudes)):
                return None
            area = scene["B13"].attrs["area"]
            point_col, point_row = area.get_array_indices_from_lonlat(float(point.lon), float(point.lat))
            local_row, local_col = int(point_row) - rows[0], int(point_col) - cols[0]
            if not (0 <= local_row < latitudes.shape[0] and 0 <= local_col < latitudes.shape[1]):
                return None
        finally:
            del scene
        identity = "|".join(
            f"{key[0]}:{key[1]}:{objects[key].key}:{objects[key].etag}:{objects[key].size_bytes}"
            for key in sorted(objects)
        )
        source = (
            "noaa-himawari9-hsd-calibrated:"
            f"{hashlib.sha256((identity + '|' + HIMAWARI_HSD_PROJECTION_IDENTITY + '|' +
                              f'{rows[0]}:{rows[1]}:{cols[0]}:{cols[1]}').encode()).hexdigest()[:24]}"
        )
        mean_coverage = min(coverage) if coverage else 0.0
        return CalibratedSatelliteFrame(
            valid_time_utc=scan_time + timedelta(minutes=5),
            latitudes=latitudes,
            longitudes=longitudes,
            bands=bands,
            units={f"B{band}": "K" for band in self.BANDS},
            source=source,
            quality="medium" if mean_coverage >= 0.99 else "low",
            range_bytes=sum(item.size_bytes for item in objects.values()),
            range_blocks=len(objects),
        )


class HsdCompactCache:
    """Atomic compact cache for calibrated HSD frames."""

    def __init__(self, cache_dir: str | Path = DEFAULT_HSD_COMPACT_CACHE_DIR, *, max_age_minutes: float = 30.0) -> None:
        if max_age_minutes <= 0.0:
            raise ValueError("compact HSD cache age must be positive")
        self.cache_dir = Path(cache_dir)
        self.max_age = timedelta(minutes=float(max_age_minutes))

    def write(self, frame: CalibratedSatelliteFrame, point: Any, radius_km: float) -> Path:
        self._validate_frame(frame)
        self._validate_arrays(
            frame.bands,
            np.asarray(frame.latitudes),
            np.asarray(frame.longitudes),
        )
        if not math.isfinite(float(radius_km)) or float(radius_km) <= 0.0:
            raise ValueError("compact HSD cache radius is invalid")
        metadata = {
            "cache_schema_version": HSD_COMPACT_CACHE_SCHEMA_VERSION,
            "cache_algorithm": HSD_COMPACT_CACHE_ALGORITHM,
            "frame_schema_version": frame.schema_version,
            "valid_time_utc": frame.valid_time_utc.astimezone(timezone.utc).isoformat(),
            "ready_time_utc": (
                frame.valid_time_utc.astimezone(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
            "source": frame.source,
            "quality": frame.quality,
            "range_bytes": frame.range_bytes,
            "range_blocks": frame.range_blocks,
            "target_lat": round(float(point.lat), 5),
            "target_lon": round(float(point.lon), 5),
            "radius_km": round(float(radius_km), 3),
            "projection_identity": HIMAWARI_HSD_PROJECTION_IDENTITY,
            "projection_hash": hashlib.sha256(HIMAWARI_HSD_PROJECTION_IDENTITY.encode()).hexdigest(),
            "bands": sorted(frame.bands),
            "units": dict(frame.units),
        }
        path = self._path(frame.valid_time_utc, point, radius_km)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + f".tmp-{os.getpid()}-{uuid.uuid4().hex}")
        arrays: dict[str, Any] = {
            "metadata": np.asarray(json.dumps(metadata, separators=(",", ":"))),
            "latitudes": np.asarray(frame.latitudes, dtype=np.float32),
            "longitudes": np.asarray(frame.longitudes, dtype=np.float32),
        }
        arrays.update({f"band__{name}": np.asarray(values, dtype=np.float32) for name, values in frame.bands.items()})
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        self._prune(point, radius_km, keep=3)
        return path

    def _prune(self, point: Any, radius_km: float, *, keep: int) -> None:
        if keep < 1:
            raise ValueError("compact HSD cache retention must be positive")
        paths = sorted(self.cache_dir.glob(self._glob(point, radius_km)), reverse=True)
        # A current scan must not evict the only compact frames for a past
        # event. Retain the newest ``keep`` frames per UTC day, while the
        # normal cache-prune command handles long-term disk retention.
        by_day: dict[str, list[Path]] = {}
        for path in paths:
            match = re.match(r"^frame-(\d{8})T\d{6}\d{6}Z-", path.name)
            if match is None:
                continue
            by_day.setdefault(match.group(1), []).append(path)
        for day_paths in by_day.values():
            for path in day_paths[keep:]:
                path.unlink(missing_ok=True)

    def read(self, reference_utc: datetime, point: Any, radius_km: float) -> Optional[CalibratedSatelliteFrame]:
        _require_utc(reference_utc, "compact HSD cache reference time")
        for path in sorted(self.cache_dir.glob(self._glob(point, radius_km)), reverse=True):
            frame = self._read_path(path, reference_utc, point, radius_km)
            if frame is not None:
                return frame
        return None

    def read_series(
        self,
        reference_utc: datetime,
        point: Any,
        radius_km: float,
        *,
        count: int = 2,
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        """Read recent complete compact scans, newest first."""
        _require_utc(reference_utc, "compact HSD cache reference time")
        if isinstance(count, bool) or not 1 <= int(count) <= 4:
            raise ValueError("compact HSD scan count must be between one and four")
        frames: list[CalibratedSatelliteFrame] = []
        for path in sorted(self.cache_dir.glob(self._glob(point, radius_km)), reverse=True):
            frame = self._read_path(path, reference_utc, point, radius_km)
            if frame is not None:
                frames.append(frame)
                if len(frames) >= int(count):
                    break
        return tuple(frames)

    def _read_path(
        self,
        path: Path,
        reference_utc: datetime,
        point: Any,
        radius_km: float,
    ) -> Optional[CalibratedSatelliteFrame]:
        try:
            with np.load(path, allow_pickle=False) as payload:
                metadata = json.loads(str(payload["metadata"].item()))
                self._validate_metadata(metadata, point, radius_km)
                valid_time = datetime.fromisoformat(metadata["valid_time_utc"])
                _require_utc(valid_time, "compact HSD cache time")
                ready_time = datetime.fromisoformat(metadata["ready_time_utc"])
                _require_utc(ready_time, "compact HSD cache ready time")
                if (
                    ready_time > reference_utc
                    or reference_utc - valid_time > self.max_age
                    or ready_time != valid_time + timedelta(minutes=5)
                ):
                    return None
                latitudes = np.asarray(payload["latitudes"], dtype=np.float32)
                longitudes = np.asarray(payload["longitudes"], dtype=np.float32)
                bands = {
                    name.removeprefix("band__"): np.asarray(payload[name], dtype=np.float32)
                    for name in payload.files if name.startswith("band__")
                }
                self._validate_arrays(bands, latitudes, longitudes)
                return CalibratedSatelliteFrame(
                    valid_time_utc=valid_time,
                    latitudes=latitudes,
                    longitudes=longitudes,
                    bands=bands,
                    units=dict(metadata["units"]),
                    source=str(metadata["source"]),
                    quality=str(metadata["quality"]),
                    schema_version=int(metadata["frame_schema_version"]),
                    range_bytes=int(metadata["range_bytes"]),
                    range_blocks=int(metadata["range_blocks"]),
                )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # Derived cache files are disposable.  Remove an obsolete entry so
            # a live request can refill it with the current strict schema.
            if _discard_obsolete_compact_cache(path, exc):
                return None
            raise ValueError(f"invalid HSD compact cache: {path}") from exc

    @staticmethod
    def _validate_frame(frame: CalibratedSatelliteFrame) -> None:
        if frame.schema_version != SATELLITE_CALIBRATED_FRAME_SCHEMA_VERSION:
            raise ValueError("cannot cache obsolete calibrated HSD frame")
        if frame.quality not in {"medium", "low"}:
            raise ValueError("HSD frame quality must be medium or low")
        if set(frame.bands) != {f"B{band}" for band in SegmentedHimawariHsdFrameProvider.BANDS}:
            raise ValueError("HSD frame must contain exactly B07/B13/B15")
        if dict(frame.units) != {f"B{band}": "K" for band in SegmentedHimawariHsdFrameProvider.BANDS}:
            raise ValueError("HSD frame units must be Kelvin")

    @classmethod
    def _validate_metadata(cls, metadata: Mapping[str, Any], point: Any, radius_km: float) -> None:
        required = {
            "cache_schema_version", "cache_algorithm", "frame_schema_version", "valid_time_utc", "ready_time_utc",
            "source", "quality", "range_bytes", "range_blocks", "target_lat", "target_lon",
            "radius_km", "projection_identity", "projection_hash", "bands", "units",
        }
        if set(metadata) != required or int(metadata["cache_schema_version"]) != HSD_COMPACT_CACHE_SCHEMA_VERSION:
            raise ValueError("compact HSD cache schema is obsolete")
        if metadata["cache_algorithm"] != HSD_COMPACT_CACHE_ALGORITHM:
            raise ValueError("compact HSD cache algorithm is obsolete")
        if int(metadata["frame_schema_version"]) != SATELLITE_CALIBRATED_FRAME_SCHEMA_VERSION:
            raise ValueError("compact HSD frame schema is obsolete")
        try:
            valid_time = datetime.fromisoformat(str(metadata["valid_time_utc"]))
            ready_time = datetime.fromisoformat(str(metadata["ready_time_utc"]))
            _require_utc(valid_time, "compact HSD cache time")
            _require_utc(ready_time, "compact HSD cache ready time")
            if ready_time != valid_time + timedelta(minutes=5):
                raise ValueError("compact HSD cache ready time must be five minutes after valid time")
        except (TypeError, ValueError) as exc:
            raise ValueError("compact HSD cache time is invalid") from exc
        if metadata["quality"] not in {"medium", "low"}:
            raise ValueError("compact HSD quality is invalid")
        if metadata["projection_identity"] != HIMAWARI_HSD_PROJECTION_IDENTITY or metadata["projection_hash"] != hashlib.sha256(HIMAWARI_HSD_PROJECTION_IDENTITY.encode()).hexdigest():
            raise ValueError("compact HSD projection identity is invalid")
        expected_bands = sorted(f"B{band}" for band in SegmentedHimawariHsdFrameProvider.BANDS)
        if metadata["bands"] != expected_bands or metadata["units"] != {name: "K" for name in expected_bands}:
            raise ValueError("compact HSD band contract is invalid")
        if abs(float(metadata["target_lat"]) - float(point.lat)) > 1e-4 or abs(float(metadata["target_lon"]) - float(point.lon)) > 1e-4 or abs(float(metadata["radius_km"]) - float(radius_km)) > 1e-3:
            raise ValueError("compact HSD footprint identity is invalid")

    @staticmethod
    def _validate_arrays(bands: Mapping[str, np.ndarray], latitudes: np.ndarray, longitudes: np.ndarray) -> None:
        expected = {f"B{band}" for band in SegmentedHimawariHsdFrameProvider.BANDS}
        if set(bands) != expected or latitudes.ndim != 2 or longitudes.shape != latitudes.shape or np.any(~np.isfinite(latitudes)) or np.any(~np.isfinite(longitudes)):
            raise ValueError("compact HSD arrays are invalid")
        for name, values in bands.items():
            array = np.asarray(values)
            finite = np.isfinite(array)
            # Out-of-range fill pixels are masked to NaN at decode time, so the
            # persisted frame only ever carries NaN plus calibrated Kelvin.
            if array.shape != latitudes.shape or np.any(np.isinf(array)) or float(np.count_nonzero(finite) / max(1, array.size)) < 0.95 or np.any((array[finite] < 100.0) | (array[finite] > 400.0)):
                raise ValueError(f"compact HSD band {name} is invalid")

    @staticmethod
    def _glob(point: Any, radius_km: float) -> str:
        return f"frame-*-{float(point.lat):+.5f}-{float(point.lon):+.5f}-{float(radius_km):.3f}.npz"

    def _path(self, valid_time: datetime, point: Any, radius_km: float) -> Path:
        stamp = valid_time.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        return self.cache_dir / f"frame-{stamp}-{float(point.lat):+.5f}-{float(point.lon):+.5f}-{float(radius_km):.3f}.npz"


class HsdCacheWarmer:
    def __init__(self, remote: SegmentedHimawariHsdFrameProvider, compact_cache: HsdCompactCache) -> None:
        self.remote = remote
        self.compact_cache = compact_cache

    def warm(self, reference_utc: datetime, point: Any, radius_km: float) -> Optional[Path]:
        frames = self.remote.fetch_series(reference_utc, point, radius_km=radius_km, count=2)
        if not frames:
            return None
        paths = [self.compact_cache.write(frame, point, radius_km) for frame in frames]
        return paths[0]


class HsdCompactCacheProvider:
    def __init__(self, cache_dir: str | Path = DEFAULT_HSD_COMPACT_CACHE_DIR, *, max_age_minutes: float = 30.0) -> None:
        self.cache = HsdCompactCache(cache_dir, max_age_minutes=max_age_minutes)

    def fetch(self, reference_utc: datetime, point: Any, *, radius_km: float) -> Optional[CalibratedSatelliteFrame]:
        return self.cache.read(reference_utc, point, radius_km)

    def fetch_series(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
        count: int = 2,
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        return self.cache.read_series(reference_utc, point, radius_km, count=count)

    def fetch_cached_series(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
        count: int = 2,
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        """Read only the compact cache; never prepare a remote frame."""
        return self.fetch_series(
            reference_utc, point, radius_km=radius_km, count=count
        )


class AutoHsdCompactCacheProvider:
    """Read compact HSD frames, building one synchronously on a cold miss.

    The remote reader only downloads the B07/B13/B15 segments intersecting the
    requested footprint. A returned frame has passed the same compact-cache
    validation used on cache hits. A second frame is filled asynchronously so a
    single direct observation can start a nowcast without waiting for motion.
    """

    def __init__(
        self,
        cache_root: str | Path = DEFAULT_HSD_CACHE_DIR,
        *,
        max_age_minutes: float = 30.0,
        allow_foreground: bool = True,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.allow_foreground = bool(allow_foreground)
        self.compact_cache = HsdCompactCache(
            self.cache_root / "compact",
            max_age_minutes=max_age_minutes,
        )
        self.remote = SegmentedHimawariHsdFrameProvider(
            self.cache_root,
            max_age_minutes=max_age_minutes,
        )

    def fetch(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
    ) -> Optional[CalibratedSatelliteFrame]:
        cached = self.compact_cache.read(reference_utc, point, radius_km)
        if cached is not None:
            return cached
        if not self.allow_foreground:
            self._ensure_background_frames(
                _hsd_request_key(self.cache_root, reference_utc, point, radius_km),
                reference_utc,
                point,
                radius_km,
                (),
            )
            return None
        key = _hsd_request_key(self.cache_root, reference_utc, point, radius_km)

        def work() -> Optional[CalibratedSatelliteFrame]:
            frame = self.remote.fetch(reference_utc, point, radius_km=radius_km)
            if frame is not None:
                self.compact_cache.write(frame, point, radius_km)
            return frame

        return _run_hsd_foreground(key, work)

    def fetch_cached_series(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
        count: int = 2,
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        """Read HSD compact data without scheduling or performing network I/O."""
        return self.compact_cache.read_series(
            reference_utc, point, radius_km, count=count
        )

    def fetch_series(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
        count: int = 2,
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        if isinstance(count, bool) or not 1 <= int(count) <= 4:
            raise ValueError("compact HSD scan count must be between one and four")
        requested_count = int(count)
        cached = self.compact_cache.read_series(
            reference_utc,
            point,
            radius_km,
            count=requested_count,
        )
        # One observation is enough for the current forecast. Once two are
        # cached, preserve the existing ordering and never touch the network.
        if (requested_count <= 1 and cached) or len(cached) >= 2:
            return cached

        key = _hsd_request_key(self.cache_root, reference_utc, point, radius_km)
        if not self.allow_foreground:
            self._ensure_background_frames(
                key,
                reference_utc,
                point,
                radius_km,
                cached,
            )
            return cached
        if cached:
            self._ensure_background_frame(
                key,
                reference_utc,
                point,
                radius_km,
                cached,
            )
            return cached

        frame = _run_hsd_foreground(
            key,
            lambda: self._fetch_and_cache_one(
                reference_utc,
                point,
                radius_km,
            ),
        )
        if frame is None:
            return ()
        # The foreground future writes exactly one frame. Return that frame
        # directly so a fast background completion cannot turn this call into
        # a blocking two-frame request.
        if requested_count >= 2:
            self._ensure_background_frame(
                key,
                reference_utc,
                point,
                radius_km,
                (frame,),
            )
        return (frame,)

    def prepare_series(
        self,
        reference_utc: datetime,
        point: Any,
        *,
        radius_km: float,
        count: int = 2,
    ) -> tuple[CalibratedSatelliteFrame, ...]:
        """Prepare a complete compact series under the foreground gate."""
        if isinstance(count, bool) or not 1 <= int(count) <= 4:
            raise ValueError("compact HSD scan count must be between one and four")
        requested_count = int(count)
        cached = self.compact_cache.read_series(
            reference_utc, point, radius_km, count=requested_count
        )
        if len(cached) >= requested_count:
            return cached
        # Keep the full-series operation separate from fetch/fetch_series
        # single-flight results, whose return types are different.
        key = _hsd_request_key(self.cache_root, reference_utc, point, radius_km) + (
            "prepare",
            requested_count,
        )

        def work() -> tuple[CalibratedSatelliteFrame, ...]:
            current = self.compact_cache.read_series(
                reference_utc, point, radius_km, count=requested_count
            )
            exclude = [frame.valid_time_utc for frame in current]
            failure: Optional[str] = None
            while len(current) < requested_count:
                try:
                    frame = self._fetch_and_cache_one(
                        reference_utc,
                        point,
                        radius_km,
                        exclude_valid_times=tuple(exclude),
                    )
                except Exception as exc:
                    # fetch_series only raises when every candidate scan in the
                    # window failed, so retrying here cannot find another scan.
                    failure = str(exc)
                    _LOGGER.warning("HSD prepare scan failed: %s", exc)
                    break
                if frame is None:
                    break
                exclude.append(frame.valid_time_utc)
                current = self.compact_cache.read_series(
                    reference_utc, point, radius_km, count=requested_count
                )
            if not current and failure is not None:
                raise RuntimeError("HSD motion pair preparation failed: " + failure)
            return current

        return _run_hsd_foreground(key, work)

    def _fetch_and_cache_one(
        self,
        reference_utc: datetime,
        point: Any,
        radius_km: float,
        *,
        exclude_valid_times: Sequence[datetime] = (),
    ) -> Optional[CalibratedSatelliteFrame]:
        frames = self.remote.fetch_series(
            reference_utc,
            point,
            radius_km=radius_km,
            count=1,
            exclude_valid_times=tuple(exclude_valid_times),
        )
        if not frames:
            return None
        frame = max(frames, key=lambda item: item.valid_time_utc)
        self.compact_cache.write(frame, point, radius_km)
        return frame

    def _ensure_background_frame(
        self,
        key: tuple[str, datetime, float, float, float],
        reference_utc: datetime,
        point: Any,
        radius_km: float,
        known_frames: Sequence[CalibratedSatelliteFrame],
    ) -> None:
        self._ensure_background_frames(
            key, reference_utc, point, radius_km, known_frames
        )

    def _ensure_background_frames(
        self,
        key: tuple[str, datetime, float, float, float],
        reference_utc: datetime,
        point: GeoPoint,
        radius_km: float,
        known_frames: Sequence[CalibratedSatelliteFrame],
    ) -> None:
        known_times = tuple(frame.valid_time_utc for frame in known_frames)

        def work() -> Optional[CalibratedSatelliteFrame]:
            cached = self.compact_cache.read_series(
                reference_utc,
                point,
                radius_km,
                count=2,
            )
            if len(cached) >= 2:
                return None
            exclude = list(tuple(frame.valid_time_utc for frame in cached) or known_times)
            last: Optional[CalibratedSatelliteFrame] = None
            while len(cached) < 2:
                last = self._fetch_and_cache_one(
                    reference_utc,
                    point,
                    radius_km,
                    exclude_valid_times=tuple(exclude),
                )
                if last is None:
                    break
                exclude.append(last.valid_time_utc)
                cached = self.compact_cache.read_series(
                    reference_utc,
                    point,
                    radius_km,
                    count=2,
                )
            return last

        try:
            _submit_hsd_background(key, work)
        except Exception:
            _LOGGER.warning("HSD background frame task could not be scheduled for %s", key, exc_info=True)


@dataclass(frozen=True)
class HsdMotionEstimate:
    """Motion measured from two calibrated HSD footprints."""

    dx_px_per_hour: float
    dy_px_per_hour: float
    correlation: float
    overlap_ratio: float
    age_minutes: float
    quality: str
    previous_time_utc: datetime
    latest_time_utc: datetime

    def __post_init__(self) -> None:
        _require_utc(self.previous_time_utc, "HSD motion previous time")
        _require_utc(self.latest_time_utc, "HSD motion latest time")
        if self.latest_time_utc <= self.previous_time_utc:
            raise ValueError("HSD motion times must be increasing")
        values = (
            self.dx_px_per_hour,
            self.dy_px_per_hour,
            self.correlation,
            self.overlap_ratio,
            self.age_minutes,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("HSD motion diagnostics are invalid")
        if not 0.0 <= self.correlation <= 1.0 or not 0.0 <= self.overlap_ratio <= 1.0:
            raise ValueError("HSD motion quality is invalid")
        if self.age_minutes <= 0.0:
            raise ValueError("HSD motion age is invalid")
        if self.quality not in {"strong", "unknown"}:
            raise ValueError("HSD motion quality label is invalid")


@dataclass(frozen=True)
class HsdAdvectionPlan:
    """Conservative target-time projection derived from a scan pair."""

    target_time_utc: datetime
    source_times_utc: tuple[datetime, ...]
    horizon_hours: float
    motion_age_minutes: Optional[float]
    motion_quality: str
    scale: float
    applied: bool
    reason: str

    def __post_init__(self) -> None:
        _require_utc(self.target_time_utc, "HSD advection target time")
        if not self.source_times_utc:
            raise ValueError("HSD advection requires source times")
        if any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in self.source_times_utc):
            raise ValueError("HSD advection source times must be UTC")
        values = (self.horizon_hours, self.scale)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("HSD advection diagnostics are invalid")
        if self.horizon_hours < 0.0 or not 0.0 <= self.scale <= 1.0:
            raise ValueError("HSD advection horizon or scale is invalid")
        if self.motion_age_minutes is not None and (
            not math.isfinite(float(self.motion_age_minutes)) or self.motion_age_minutes <= 0.0
        ):
            raise ValueError("HSD motion age is invalid")
        if self.motion_quality not in {"observation", "strong", "decayed", "unknown", "diagnostic-only"}:
            raise ValueError("HSD advection quality is invalid")
        if self.applied and self.scale <= 0.0:
            raise ValueError("applied HSD advection must have a positive scale")


def estimate_hsd_motion(
    previous: CalibratedSatelliteFrame,
    latest: CalibratedSatelliteFrame,
    *,
    min_overlap: float = 0.85,
    min_correlation: float = 0.35,
) -> HsdMotionEstimate:
    """Estimate translation from calibrated HSD B13 or HCAI probability.

    The estimate is diagnostic only unless both frames share a grid and have
    enough finite overlap.  NaNs are excluded from the overlap gate and filled
    with the common finite mean only for the FFT correlation; they never become
    a clear cloud classification in the retrieval.
    """
    if previous.valid_time_utc >= latest.valid_time_utc:
        raise ValueError("HSD motion frames must be ordered oldest to newest")
    motion_band = "B13" if "B13" in previous.bands and "B13" in latest.bands else "CLOUD_PROBABILITY"
    previous_values = np.asarray(previous.bands.get(motion_band), dtype=np.float32)
    latest_values = np.asarray(latest.bands.get(motion_band), dtype=np.float32)
    if previous_values.shape != latest_values.shape or previous_values.ndim != 2:
        raise ValueError(f"satellite motion frames must have matching 2-D {motion_band} grids")
    if np.asarray(previous.latitudes).shape != np.asarray(latest.latitudes).shape:
        raise ValueError("HSD motion coordinate grids do not match")
    delta_seconds = (latest.valid_time_utc - previous.valid_time_utc).total_seconds()
    if delta_seconds <= 0.0 or delta_seconds > 2.0 * 3600.0:
        raise ValueError("HSD motion frame interval is outside the supported range")
    finite = np.isfinite(previous_values) & np.isfinite(latest_values)
    overlap = float(np.count_nonzero(finite) / max(1, finite.size))
    age_minutes = delta_seconds / 60.0
    if overlap < float(min_overlap):
        return HsdMotionEstimate(
            0.0, 0.0, 0.0, overlap, age_minutes, "unknown",
            previous.valid_time_utc, latest.valid_time_utc,
        )
    common = np.concatenate((previous_values[finite], latest_values[finite]))
    mean = float(np.mean(common))
    previous_work = np.where(finite, previous_values, mean).astype(np.float32)
    latest_work = np.where(finite, latest_values, mean).astype(np.float32)
    previous_work -= float(previous_work.mean())
    latest_work -= float(latest_work.mean())
    previous_std = float(previous_work.std())
    latest_std = float(latest_work.std())
    if previous_std < 1e-4 or latest_std < 1e-4:
        return HsdMotionEstimate(
            0.0, 0.0, 0.0, overlap, age_minutes, "unknown",
            previous.valid_time_utc, latest.valid_time_utc,
        )
    window = np.outer(np.hanning(previous_work.shape[0]), np.hanning(previous_work.shape[1]))
    previous_fft = np.fft.fft2(previous_work * window)
    latest_fft = np.fft.fft2(latest_work * window)
    cross_power = latest_fft * np.conj(previous_fft)
    cross_power /= np.maximum(np.abs(cross_power), 1e-9)
    correlation_map = np.abs(np.fft.ifft2(cross_power))
    peak_y, peak_x = np.unravel_index(np.argmax(correlation_map), correlation_map.shape)
    if peak_x > correlation_map.shape[1] // 2:
        peak_x -= correlation_map.shape[1]
    if peak_y > correlation_map.shape[0] // 2:
        peak_y -= correlation_map.shape[0]
    max_shift = 0.20 * min(previous_work.shape)
    if abs(float(peak_x)) > max_shift or abs(float(peak_y)) > max_shift:
        return HsdMotionEstimate(
            0.0, 0.0, 0.0, overlap, age_minutes, "unknown",
            previous.valid_time_utc, latest.valid_time_utc,
        )
    shifted = np.roll(previous_work, (int(peak_y), int(peak_x)), axis=(0, 1))
    correlation = float(np.corrcoef(shifted[finite].ravel(), latest_work[finite].ravel())[0, 1])
    correlation = max(0.0, min(1.0, correlation)) if math.isfinite(correlation) else 0.0
    hours = delta_seconds / 3600.0
    dx = float(peak_x) / hours
    dy = float(peak_y) / hours
    quality = "strong" if correlation >= float(min_correlation) else "unknown"
    if quality == "unknown":
        dx = dy = 0.0
    return HsdMotionEstimate(
        dx, dy, correlation, overlap, age_minutes, quality,
        previous.valid_time_utc, latest.valid_time_utc,
    )


def plan_hsd_advection(
    frames: Sequence[CalibratedSatelliteFrame],
    target_time_utc: datetime,
    motion: Optional[HsdMotionEstimate],
) -> HsdAdvectionPlan:
    """Select the allowed extrapolation regime without inventing clear pixels."""
    _require_utc(target_time_utc, "HSD advection target time")
    if not frames:
        raise ValueError("HSD advection requires at least one frame")
    latest = max(frames, key=lambda item: item.valid_time_utc)
    source_times = tuple(sorted((frame.valid_time_utc for frame in frames), reverse=True))
    horizon = max(0.0, (target_time_utc - latest.valid_time_utc).total_seconds() / 3600.0)
    if horizon <= 1e-9:
        return HsdAdvectionPlan(target_time_utc, source_times, 0.0, None, "observation", 0.0, False, "at_observation")
    # A "strong" correlation between two frames minutes apart says nothing
    # about a pair separated by hours: large-scale temperature gradients
    # self-correlate, and a stale displacement used to extrapolate at full
    # weight. Stale pairs fall back to the no-extrapolation regime.
    motion_usable = (
        motion is not None
        and motion.quality == "strong"
        and math.isfinite(motion.age_minutes)
        and motion.age_minutes <= 40.0
    )
    if motion is None or len(frames) < 2 or not motion_usable:
        stale = motion is not None and motion.quality == "strong"
        return HsdAdvectionPlan(
            target_time_utc,
            source_times,
            horizon,
            motion.age_minutes if motion is not None else None,
            "unknown",
            0.0,
            False,
            "stale_paired_motion" if stale else "paired_motion_unavailable",
        )
    if horizon <= 2.0:
        return HsdAdvectionPlan(target_time_utc, source_times, horizon, motion.age_minutes, "strong", 1.0, True, "strong_0_2h")
    if horizon <= 4.0:
        scale = max(0.0, min(1.0, (4.0 - horizon) / 2.0))
        return HsdAdvectionPlan(target_time_utc, source_times, horizon, motion.age_minutes, "decayed", scale, scale > 0.0, "decayed_2_4h")
    return HsdAdvectionPlan(target_time_utc, source_times, horizon, motion.age_minutes, "diagnostic-only", 0.0, False, "beyond_4h")


def _shift_calibrated_array(
    values: np.ndarray,
    dx: float,
    dy: float,
    *,
    categorical_values: Optional[frozenset[int]] = None,
) -> np.ndarray:
    """Translate an array without wrapping pixels.

    HSD radiances and HCAI continuous fields retain the original bilinear
    interpolation. HCAI flag fields use nearest-neighbor sampling so a
    fractional motion shift cannot manufacture a new category code.
    """
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2:
        raise ValueError("HSD advection arrays must be two-dimensional")
    height, width = array.shape
    y, x = np.indices(array.shape, dtype=np.float32)
    source_x = x - float(dx)
    source_y = y - float(dy)
    if categorical_values is not None:
        nearest_x = np.floor(source_x + 0.5).astype(np.int32)
        nearest_y = np.floor(source_y + 0.5).astype(np.int32)
        valid = (
            (source_x >= 0.0)
            & (source_x <= width - 1)
            & (source_y >= 0.0)
            & (source_y <= height - 1)
        )
        result = np.full(array.shape, np.nan, dtype=np.float32)
        safe_x = np.clip(nearest_x, 0, width - 1)
        safe_y = np.clip(nearest_y, 0, height - 1)
        selected = array[safe_y, safe_x]
        valid &= np.isfinite(selected)
        valid &= selected == np.floor(selected)
        valid &= np.isin(selected, tuple(categorical_values))
        result[valid] = selected[valid]
        return result

    x0 = np.floor(source_x).astype(np.int32)
    y0 = np.floor(source_y).astype(np.int32)
    x1 = x0 + 1
    y1 = y0 + 1
    valid = (x0 >= 0) & (x1 < width) & (y0 >= 0) & (y1 < height)
    result = np.full(array.shape, np.nan, dtype=np.float32)
    if not np.any(valid):
        return result
    wx = source_x - x0
    wy = source_y - y0
    # Clip only for safe indexing; ``valid`` below still controls writes, so
    # out-of-footprint pixels remain NaN rather than wrapping around.
    safe_x0 = np.clip(x0, 0, width - 1)
    safe_x1 = np.clip(x1, 0, width - 1)
    safe_y0 = np.clip(y0, 0, height - 1)
    safe_y1 = np.clip(y1, 0, height - 1)
    v00 = array[safe_y0, safe_x0]
    v01 = array[safe_y0, safe_x1]
    v10 = array[safe_y1, safe_x0]
    v11 = array[safe_y1, safe_x1]
    finite = np.isfinite(v00) & np.isfinite(v01) & np.isfinite(v10) & np.isfinite(v11)
    valid &= finite
    result[valid] = (
        (1.0 - wy[valid]) * (1.0 - wx[valid]) * v00[valid]
        + (1.0 - wy[valid]) * wx[valid] * v01[valid]
        + wy[valid] * (1.0 - wx[valid]) * v10[valid]
        + wy[valid] * wx[valid] * v11[valid]
    )
    return result


def advect_hsd_frame(
    frame: CalibratedSatelliteFrame,
    plan: HsdAdvectionPlan,
    motion: Optional[HsdMotionEstimate],
) -> CalibratedSatelliteFrame:
    """Project a calibrated frame only inside the plan's trusted horizon."""
    if not plan.applied or motion is None:
        return frame
    # Decay is a confidence/score constraint, not a slower physical wind. A
    # three-hour target must be translated by the full measured velocity for
    # three hours; blending with a GFS displacement would require an explicit
    # second motion source and is intentionally outside this contract.
    dx = motion.dx_px_per_hour * plan.horizon_hours
    dy = motion.dy_px_per_hour * plan.horizon_hours
    is_hcai = "hcai" in frame.source.lower()
    bands = {
        name: _shift_calibrated_array(
            values,
            dx,
            dy,
            categorical_values=HCAI_FLAG_VALUES.get(name) if is_hcai else None,
        )
        for name, values in frame.bands.items()
    }
    source = f"{frame.source}|advection={plan.reason}|horizon={plan.horizon_hours:.3f}h"
    return replace(frame, valid_time_utc=plan.target_time_utc, bands=bands, source=source)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _etag_matches_file(etag: str, path: Path) -> bool:
    # Single-part S3 objects expose an MD5 ETag. Multipart ETags are still
    # accepted after size/SHA256 sidecar validation because their suffix is not
    # a direct digest of the complete object.
    normalized = str(etag or "").strip('"')
    if not normalized or "-" in normalized or not re.fullmatch(r"[0-9a-fA-F]{32}", normalized):
        return True
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == normalized.lower()


def _himawari_area_definition(area_definition_type: Any) -> Any:
    """Official AHI full-disk geostationary projection used by HCAI grids.

    HCAI NetCDF files expose geospatial bounds but omit the CF projection
    variable.  These values are the instrument's fixed full-disk geometry;
    the returned lon/lat arrays are generated by inverse projection for the
    requested HDF5 pixel slice, never by a lat/lon rectangle assumption.
    """
    return area_definition_type(
        "Himawari_Area",
        "AHI Full Disk area",
        "geos9",
        {"proj": "geos", "lon_0": 140.7, "h": 35785863.0, "a": 6378137.0, "b": 6356752.3, "units": "m"},
        5500,
        5500,
        (-5499999.9012, -5499999.9012, 5499999.9012, 5499999.9012),
    )


class _RangeBudget:
    def __init__(self, max_bytes: Optional[int], deadline: Optional[float]) -> None:
        self.max_bytes = None if max_bytes is None else int(max_bytes)
        # HDF5 can issue many small logical reads for the same mmap block.
        # This is only an anti-abuse guard; the actual network budget is
        # enforced from the wrapped fsspec Range fetcher.
        self.logical_guard_bytes = (
            None
            if self.max_bytes is None
            else max(64 * 1024 * 1024, self.max_bytes * 64)
        )
        self.deadline = None if deadline is None else float(deadline)
        self.logical_bytes = 0
        self.logical_requests = 0
        self.remote_bytes = 0
        self.remote_blocks = 0
        self._lock = threading.Lock()

    @property
    def bytes_read(self) -> int:
        with self._lock:
            return self.remote_bytes

    @property
    def blocks(self) -> int:
        with self._lock:
            return self.remote_blocks

    def reserve(self, count: int, *, request: bool = True) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TimeoutError("Himawari HCAI aggregate range retrieval timed out")
        with self._lock:
            if count < 0 or (
                self.logical_guard_bytes is not None
                and self.logical_bytes + int(count) > self.logical_guard_bytes
            ):
                raise TimeoutError("Himawari HCAI logical read guard exceeded")
            self.logical_bytes += int(count)
            if request:
                self.logical_requests += 1

    def reserve_remote(self, expected: int, block_size: int) -> None:
        if expected < 0 or block_size <= 0:
            raise ValueError("invalid HCAI remote range span")
        with self._lock:
            if self.max_bytes is not None and self.remote_bytes + expected > self.max_bytes + block_size:
                raise TimeoutError("Himawari HCAI remote range bytes exceeded configured limit")

    def record_remote_fetch(self, count: int, block_size: int) -> None:
        if count < 0 or block_size <= 0:
            raise ValueError("invalid HCAI remote fetch statistics")
        with self._lock:
            if self.max_bytes is not None and self.remote_bytes + count > self.max_bytes + block_size:
                raise TimeoutError("Himawari HCAI remote range bytes exceeded configured limit")
            self.remote_bytes += int(count)
            self.remote_blocks += max(1, math.ceil(count / block_size)) if count else 0

    def merge_remote_stats(self, count: int, blocks: int) -> None:
        if count < 0 or blocks < 0:
            raise ValueError("invalid HCAI merged remote statistics")
        with self._lock:
            if self.max_bytes is not None and self.remote_bytes + count > self.max_bytes:
                raise TimeoutError("Himawari HCAI remote range bytes exceeded configured limit")
            self.remote_bytes += int(count)
            self.remote_blocks += int(blocks)

class _BoundedRemoteFile:
    def __init__(self, wrapped: Any, budget: _RangeBudget, deadline: Optional[float]) -> None:
        self.wrapped = wrapped
        self.budget = budget
        self.deadline = None if deadline is None else float(deadline)

    def _check(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TimeoutError("Himawari HCAI remote read timed out")

    def read(self, size: int = -1) -> bytes:
        self._check()
        if size < 0:
            raise ValueError("unbounded remote reads are forbidden")
        self.budget.reserve(int(size))
        data = self.wrapped.read(size)
        return data

    def readinto(self, buffer: Any) -> int:
        self._check()
        requested = len(buffer)
        self.budget.reserve(requested)
        count = self.wrapped.readinto(buffer)
        return int(count or 0)

    def seek(self, offset: int, whence: int = 0) -> int:
        self._check()
        return self.wrapped.seek(offset, whence)

    def tell(self) -> int:
        self._check()
        return self.wrapped.tell()

    def close(self) -> None:
        self.wrapped.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)


def _read_scaled_dataset(dataset: Any, slices: tuple[slice, slice]) -> np.ndarray:
    raw = np.asarray(dataset[slices], dtype=np.float64)
    attrs = dataset.attrs
    fill = attrs.get("_FillValue")
    if fill is not None:
        fill_value = _attr_float(fill)
        raw[np.isclose(raw, fill_value)] = np.nan
    valid_range = attrs.get("valid_range")
    if valid_range is not None:
        lower, upper = (_attr_float(value) for value in np.asarray(valid_range).reshape(-1)[:2])
        raw[(raw < lower) | (raw > upper)] = np.nan
    scale = _attr_float(attrs.get("scale_factor", 1.0))
    offset = _attr_float(attrs.get("add_offset", 0.0))
    raw = raw * scale + offset
    return raw


def _attr_float(value: Any) -> float:
    """Decode scalar HDF5 attributes without silently accepting arrays."""
    array = np.asarray(value).reshape(-1)
    if array.size != 1:
        raise ValueError("Himawari HCAI numeric attribute must be scalar")
    return float(array[0])


def _decode_attr(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    array = np.asarray(value)
    if array.size != 1:
        return str(value)
    item = array.reshape(-1)[0]
    if isinstance(item, bytes):
        return item.decode("utf-8", errors="strict")
    return str(item)


def _dataset_unit(dataset: Any, fallback: str) -> str:
    value = dataset.attrs.get("units")
    if value is None:
        return fallback
    decoded = _decode_attr(value).strip()
    return decoded or fallback


def _canonical_flag_meaning(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _agl(asl: Optional[float], terrain: Optional[float]) -> Optional[float]:
    if asl is None or terrain is None:
        return None
    return max(0.0, float(asl) - float(terrain))


def validate_cloud_column_payload(payload: Mapping[str, Any]) -> None:
    """Reject old or partial persisted payloads; no compatibility parsing."""

    if payload.get("schema_version") != SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION:
        raise ValueError("satellite cloud-column payload version is invalid")
    required = {
        "valid_time_utc",
        "source",
        "vertical_prior_identity",
        "retrieval_quality",
        "low_cloud_state",
        "columns",
    }
    if set(payload) != required | {
        "schema_version",
        "motion_dx_px_per_hour",
        "motion_dy_px_per_hour",
    }:
        raise ValueError("satellite cloud-column payload fields are invalid")
    if not isinstance(payload["columns"], list):
        raise ValueError("satellite cloud-column payload columns are invalid")
    if not isinstance(payload["vertical_prior_identity"], str) or not payload["vertical_prior_identity"]:
        raise ValueError("satellite cloud-column vertical prior identity is invalid")
    if payload["retrieval_quality"] not in RETRIEVAL_QUALITIES:
        raise ValueError("satellite cloud-column retrieval quality is invalid")
    if payload["low_cloud_state"] not in LOW_CLOUD_STATES:
        raise ValueError("satellite cloud-column low-cloud state is invalid")
    try:
        set_time = datetime.fromisoformat(str(payload["valid_time_utc"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("satellite cloud-column valid time is invalid") from exc
    if set_time.tzinfo is None or set_time.utcoffset() != timezone.utc.utcoffset(set_time):
        raise ValueError("satellite cloud-column valid time must be UTC")
    if not math.isfinite(float(payload["motion_dx_px_per_hour"])) or not math.isfinite(
        float(payload["motion_dy_px_per_hour"])
    ):
        raise ValueError("satellite cloud-column motion is invalid")

    column_fields = {
        "schema_version",
        "algorithm",
        "valid_time_utc",
        "lat",
        "lon",
        "distance_km",
        "bearing_deg",
        "base_p10_asl_m",
        "base_p50_asl_m",
        "base_p90_asl_m",
        "base_p10_agl_m",
        "base_p50_agl_m",
        "base_p90_agl_m",
        "top_asl_m",
        "top_agl_m",
        "terrain_asl_m",
        "phase",
        "cloud_type",
        "optical_depth",
        "cloud_probability_by_height",
        "horizontal_cloud_probability",
        "low_cloud_state",
        "retrieval_quality",
        "source",
        "vertical_prior_identity",
        "vertical_prior_source",
        "base_source",
        "motion_dx_px_per_hour",
        "motion_dy_px_per_hour",
    }
    reconstructed: list[SatelliteCloudColumn] = []
    for column in payload["columns"]:
        if not isinstance(column, Mapping) or set(column) != column_fields:
            raise ValueError("satellite cloud-column nested fields are invalid")
        if column["schema_version"] != SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION:
            raise ValueError("satellite cloud-column nested schema is obsolete")
        if column["algorithm"] != SATELLITE_CLOUD_COLUMN_ALGORITHM:
            raise ValueError("satellite cloud-column nested algorithm is obsolete")
        if column["valid_time_utc"] != payload["valid_time_utc"]:
            raise ValueError("satellite cloud-column nested time does not match set time")
        if not isinstance(column["cloud_probability_by_height"], Mapping):
            raise ValueError("satellite cloud-column probability profile is invalid")
        if not column["vertical_prior_identity"] or not column["vertical_prior_source"]:
            raise ValueError("satellite cloud-column prior provenance is invalid")
        if column["source"] != payload["source"]:
            raise ValueError("satellite cloud-column source does not match set source")
        try:
            column_time = datetime.fromisoformat(str(column["valid_time_utc"]))
            profile = {
                float(height): float(probability)
                for height, probability in column["cloud_probability_by_height"].items()
            }
            reconstructed_column = SatelliteCloudColumn(
                schema_version=int(column["schema_version"]),
                algorithm=str(column["algorithm"]),
                valid_time_utc=column_time,
                lat=float(column["lat"]),
                lon=float(column["lon"]),
                distance_km=float(column["distance_km"]),
                bearing_deg=float(column["bearing_deg"]),
                base_p10_asl_m=_optional_payload_float(column["base_p10_asl_m"]),
                base_p50_asl_m=_optional_payload_float(column["base_p50_asl_m"]),
                base_p90_asl_m=_optional_payload_float(column["base_p90_asl_m"]),
                top_asl_m=_optional_payload_float(column["top_asl_m"]),
                terrain_asl_m=_optional_payload_float(column["terrain_asl_m"]),
                phase=str(column["phase"]),
                optical_depth=_optional_payload_float(column["optical_depth"]),
                cloud_probability_by_height=profile,
                horizontal_cloud_probability=_optional_payload_float(
                    column["horizontal_cloud_probability"]
                ),
                low_cloud_state=str(column["low_cloud_state"]),
                retrieval_quality=str(column["retrieval_quality"]),
                source=str(column["source"]),
                vertical_prior_identity=str(column["vertical_prior_identity"]),
                vertical_prior_source=str(column["vertical_prior_source"]),
                cloud_type=str(column["cloud_type"]),
                base_source=str(column["base_source"]),
                motion_dx_px_per_hour=float(column["motion_dx_px_per_hour"]),
                motion_dy_px_per_hour=float(column["motion_dy_px_per_hour"]),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("satellite cloud-column physical fields are invalid") from exc
        if column_time.tzinfo is None or column_time.utcoffset() != timezone.utc.utcoffset(column_time):
            raise ValueError("satellite cloud-column nested time must be UTC")
        if column["vertical_prior_identity"] != payload["vertical_prior_identity"]:
            raise ValueError("satellite cloud-column prior identity does not match set")
        for actual, derived_key in (
            (column["base_p10_agl_m"], reconstructed_column.base_p10_agl_m),
            (column["base_p50_agl_m"], reconstructed_column.base_p50_agl_m),
            (column["base_p90_agl_m"], reconstructed_column.base_p90_agl_m),
            (column["top_agl_m"], reconstructed_column.top_agl_m),
        ):
            supplied = _optional_payload_float(actual)
            if (supplied is None) != (derived_key is None) or (
                supplied is not None
                and derived_key is not None
                and not math.isclose(supplied, derived_key, rel_tol=0.0, abs_tol=1e-5)
            ):
                raise ValueError("satellite cloud-column ASL/AGL fields are inconsistent")
        reconstructed.append(reconstructed_column)
    expected_quality = aggregate_retrieval_quality(item.retrieval_quality for item in reconstructed)
    if payload["retrieval_quality"] != expected_quality:
        raise ValueError("satellite cloud-column set quality does not match nested columns")
    expected_state = aggregate_local_low_cloud_state((payload,))
    if payload["low_cloud_state"] != expected_state:
        raise ValueError("satellite cloud-column set low-cloud state does not match nested columns")


def _optional_payload_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("payload numeric value is not finite")
    return number


def nearest_prior_level(prior: VerticalPrior, height_asl_m: float) -> VerticalPriorLevel:
    return min(prior.levels, key=lambda level: abs(level.height_asl_m - height_asl_m))


def retrieve_cloud_columns(
    frame: CalibratedSatelliteFrame,
    point: Any,
    distance_km: float,
    bearing_deg: float,
    prior: VerticalPrior,
    *,
    sample: Optional[Mapping[str, float]] = None,
    motion_dx_px_per_hour: float = 0.0,
    motion_dy_px_per_hour: float = 0.0,
) -> tuple[SatelliteCloudColumn, ...]:
    """Retrieve one or more cloud layers from calibrated AHI + GFS prior.

    The posterior is intentionally conservative: the model supplies candidate
    saturated layers and heights, while the calibrated satellite channels
    supply cloud presence/top/phase/material evidence.  Missing channels or a
    missing prior produce an unknown set, never a clear set.
    """

    if frame.valid_time_utc != prior.valid_time_utc:
        raise ValueError("satellite frame and vertical prior times do not match")
    if abs(float(prior.target_lat) - float(point.lat)) > 0.30 or abs(
        ((float(prior.target_lon) - float(point.lon) + 180.0) % 360.0) - 180.0
    ) > 0.30:
        raise ValueError("satellite frame point and vertical prior location do not match")
    values = dict(sample) if sample is not None else sample_calibrated_frame(frame, point.lat, point.lon)
    if _has_hcai_retrieval(values):
        return _retrieve_hcai_columns(
            frame,
            point,
            distance_km,
            bearing_deg,
            prior,
            values,
            motion_dx_px_per_hour,
            motion_dy_px_per_hour,
        )
    if not required_calibrated_channels(values):
        return ()
    b13 = values["B13"]
    b15 = values["B15"]
    b07 = values["B07"]
    surface_temperature = _surface_temperature(prior)
    if surface_temperature is None:
        return ()
    cloud_probability = _cloud_probability(b13, surface_temperature)
    if cloud_probability < 0.20:
        clear_evidence = _hsd_clear_evidence(frame, prior, b07, b13, b15)
        return (
            SatelliteCloudColumn(
                schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                algorithm=SATELLITE_CLOUD_COLUMN_ALGORITHM,
                valid_time_utc=frame.valid_time_utc,
                lat=float(point.lat),
                lon=float(point.lon),
                distance_km=float(distance_km),
                bearing_deg=float(bearing_deg),
                base_p10_asl_m=None,
                base_p50_asl_m=None,
                base_p90_asl_m=None,
                top_asl_m=None,
                terrain_asl_m=prior.terrain_asl_m,
                phase="unknown",
                optical_depth=None,
                cloud_probability_by_height={},
                horizontal_cloud_probability=0.0,
                # B13 versus model surface temperature is not a cloud mask.
                # An HSD frame can only claim clear after all three calibrated
                # channels agree and the same-cycle GFS low levels provide an
                # independent clear/unsaturated constraint.
                low_cloud_state="clear" if clear_evidence else "unknown",
                retrieval_quality=_frame_quality(frame) if clear_evidence else "unknown",
                source=frame.source,
                vertical_prior_identity=prior.identity,
                vertical_prior_source=prior.source,
                cloud_type="clear" if clear_evidence else "unknown",
                base_source="none",
                motion_dx_px_per_hour=motion_dx_px_per_hour,
                motion_dy_px_per_hour=motion_dy_px_per_hour,
            ),
        )

    top = _cloud_top(prior, b13)
    if top is None:
        return ()
    # B07/B13/B15 brightness temperatures do not provide a calibrated cloud
    # phase retrieval.  The channel differences are useful for presence and
    # texture diagnostics, but mapping them to water/ice/mixed would publish a
    # false categorical observation.  Keep phase unknown; only the HCAI branch
    # below may publish the official phase field.
    phase = "unknown"
    # L1 thermal retrievals do not uniquely provide optical thickness.  Keep
    # it unknown here; the NOAA HCAI path below supplies CldOptDpth when it is
    # valid instead of manufacturing a proxy from brightness temperature.
    optical_depth = None
    supports = _candidate_layers(prior, top, cloud_probability)
    if not supports:
        supports = (nearest_prior_level(prior, top),)
    layer_weights = _layer_weights(supports, prior, top)

    columns: list[SatelliteCloudColumn] = []
    for layer_index, level in enumerate(supports):
        layer_probability = cloud_probability * layer_weights[layer_index]
        base = _cloud_base_posterior(prior, level, top, layer_probability, phase)
        base_p10, base_p50, base_p90, base_source = base
        probabilities = _height_probabilities(prior, top, layer_probability, level)
        low_top = top - prior.terrain_asl_m <= 3500.0
        low_probability = max(
            (
                probability
                for height, probability in probabilities.items()
                if height <= min(top, level.height_asl_m + 1200.0)
            ),
            default=0.0,
        )
        low_state = (
            "detected"
            if low_top
            and low_probability >= 0.55 + 0.15 * clamp01((prior.terrain_asl_m - 1500.0) / 2000.0)
            and level.height_asl_m - prior.terrain_asl_m <= 3500.0
            else "clear"
            if cloud_probability < 0.30 and _frame_quality(frame) == "high"
            else "unknown"
        )
        # ``top`` is the nearest same-cycle GFS temperature level to B13.  It
        # is a cloud-top proxy, not a satellite height retrieval.  The base
        # posterior is likewise a GFS-supported proxy and remains explicitly
        # uncertain through p10/p50/p90; do not turn it into a deterministic
        # cloud type or cloud phase.
        cloud_type = "unknown"
        base_source = f"hsd-b13-top-proxy+{base_source}"
        columns.append(
            SatelliteCloudColumn(
                schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                algorithm=SATELLITE_CLOUD_COLUMN_ALGORITHM,
                valid_time_utc=frame.valid_time_utc,
                lat=float(point.lat),
                lon=float(point.lon),
                distance_km=float(distance_km),
                bearing_deg=float(bearing_deg),
                base_p10_asl_m=base_p10,
                base_p50_asl_m=base_p50,
                base_p90_asl_m=base_p90,
                top_asl_m=top,
                terrain_asl_m=prior.terrain_asl_m,
                phase=phase,
                optical_depth=optical_depth,
                cloud_probability_by_height=probabilities,
                horizontal_cloud_probability=cloud_probability,
                low_cloud_state=low_state,
                retrieval_quality=_column_quality(frame, prior, cloud_probability),
                source=frame.source,
                vertical_prior_identity=prior.identity,
                vertical_prior_source=prior.source,
                cloud_type=cloud_type,
                base_source=base_source,
                motion_dx_px_per_hour=motion_dx_px_per_hour,
                motion_dy_px_per_hour=motion_dy_px_per_hour,
            )
        )
    return _conserve_cloud_profiles(tuple(columns), cloud_probability)


def sample_calibrated_frame(frame: CalibratedSatelliteFrame, lat: float, lon: float) -> dict[str, float]:
    latitudes = np.asarray(frame.latitudes)
    longitudes = np.asarray(frame.longitudes)
    finite_lats = latitudes[np.isfinite(latitudes)]
    finite_lons = longitudes[np.isfinite(longitudes)]
    if finite_lats.size == 0 or finite_lons.size == 0:
        raise ValueError("calibrated satellite footprint has no finite coordinates")
    target_lat = float(lat)
    target_lon = float(lon)
    if not float(np.min(finite_lats)) - 1e-6 <= target_lat <= float(np.max(finite_lats)) + 1e-6:
        raise ValueError("calibrated satellite target is outside latitude footprint")
    lon_delta = ((finite_lons - target_lon + 180.0) % 360.0) - 180.0
    if float(np.min(np.abs(lon_delta))) > 1e-6:
        # A compact HCAI bbox is never wider than a hemisphere, so a target
        # outside its longitude span must not snap to the edge pixel.
        if not (
            float(np.min(finite_lons)) - 1e-6 <= target_lon <= float(np.max(finite_lons)) + 1e-6
        ):
            raise ValueError("calibrated satellite target is outside longitude footprint")
    if latitudes.ndim == 1:
        lat_index = int(np.argmin(np.abs(latitudes - target_lat)))
        lon_delta = np.abs(((longitudes - target_lon + 180.0) % 360.0) - 180.0)
        lon_index = int(np.argmin(lon_delta))
        index = (lat_index, lon_index)
    else:
        distance = (latitudes - target_lat) ** 2 + (
            ((longitudes - target_lon + 180.0) % 360.0) - 180.0
        ) ** 2
        index = tuple(int(value) for value in np.unravel_index(np.argmin(distance), distance.shape))
    nearest_lat = float(latitudes[index])
    nearest_lon = float(longitudes[index])
    nearest_km = haversine_km(
        GeoPoint(target_lat, target_lon),
        GeoPoint(nearest_lat, nearest_lon),
    )
    if not math.isfinite(nearest_km) or nearest_km > 25.0:
        raise ValueError("calibrated satellite target is outside pixel tolerance")
    return {
        channel: float(np.asarray(values)[index])
        for channel, values in frame.bands.items()
    }


def required_calibrated_channels(values: Mapping[str, float]) -> bool:
    return all(channel in values and math.isfinite(float(values[channel])) for channel in ("B07", "B13", "B15"))


def _has_hcai_retrieval(values: Mapping[str, float]) -> bool:
    return (
        ("CLOUD_MASK" in values and math.isfinite(float(values["CLOUD_MASK"])))
        or (
            "CLOUD_PROBABILITY" in values
            and math.isfinite(float(values["CLOUD_PROBABILITY"]))
            and 0.0 <= float(values["CLOUD_PROBABILITY"]) <= 1.0
        )
    )


def _retrieve_hcai_columns(
    frame: CalibratedSatelliteFrame,
    point: Any,
    distance_km: float,
    bearing_deg: float,
    prior: VerticalPrior,
    values: Mapping[str, float],
    motion_dx_px_per_hour: float,
    motion_dy_px_per_hour: float,
) -> tuple[SatelliteCloudColumn, ...]:
    """Use NOAA Enterprise HCAI values without inventing BT-derived optics."""

    mask_value = values.get("CLOUD_MASK")
    mask_code = (
        int(round(float(mask_value)))
        if mask_value is not None and math.isfinite(float(mask_value))
        else None
    )
    if mask_code is not None and mask_code not in {0, 1, 2, 3}:
        mask_code = None
    probability_value = _valid_optional(values.get("CLOUD_PROBABILITY"), 0.0, 1.0)
    if probability_value is None:
        if mask_code is None:
            return ()
        probability_value = {0: 0.0, 1: 0.25, 2: 0.75, 3: 1.0}[mask_code]
    cloud_probability = float(probability_value)
    mask_quality = _hcai_mask_quality(values.get("CLOUD_MASK_QF"), frame.quality)
    height_quality = _hcai_height_quality(values.get("CLOUD_HEIGHT_QF"), frame.quality)
    if mask_code == 0:
        quality = mask_quality
    elif mask_code is None:
        # A probability without a valid categorical mask cannot establish the
        # calibrated clear/cloud state; keep the sample unknown rather than
        # using the model/frame quality as a substitute.
        quality = "unknown"
    else:
        quality = _merge_qualities(mask_quality, height_quality)
    top_value = values.get("CLOUD_TOP_HEIGHT")
    top = float(top_value) if top_value is not None and math.isfinite(float(top_value)) else None
    if top is not None and (top < -300.0 or top > 20000.0):
        top = None
    if mask_code == 0 and cloud_probability < 0.30:
        state = "clear" if quality in {"high", "medium"} else "unknown"
        return (
            SatelliteCloudColumn(
                schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                algorithm=SATELLITE_CLOUD_COLUMN_ALGORITHM,
                valid_time_utc=frame.valid_time_utc,
                lat=float(point.lat),
                lon=float(point.lon),
                distance_km=float(distance_km),
                bearing_deg=float(bearing_deg),
                base_p10_asl_m=None,
                base_p50_asl_m=None,
                base_p90_asl_m=None,
                top_asl_m=None,
                terrain_asl_m=prior.terrain_asl_m,
                phase="unknown",
                optical_depth=None,
                cloud_probability_by_height={},
                horizontal_cloud_probability=0.0,
                low_cloud_state=state,
                retrieval_quality=quality,
                source=frame.source,
                vertical_prior_identity=prior.identity,
                vertical_prior_source=prior.source,
                cloud_type="clear" if state == "clear" else "unknown",
                base_source="none",
                motion_dx_px_per_hour=motion_dx_px_per_hour,
                motion_dy_px_per_hour=motion_dy_px_per_hour,
            ),
        )

    # A failed/missing cloud-top retrieval cannot be replaced by a model
    # pressure level.  The mask remains unknown rather than scoring a fake
    # layer at the last available height.
    if top is None or height_quality == "unknown":
        return ()
    if top < prior.terrain_asl_m - 100.0:
        return ()
    phase = _hcai_phase(values.get("CLOUD_PHASE"))
    cloud_type = _hcai_cloud_type(values.get("CLOUD_TYPE"), phase)
    optical_depth = _valid_optional(values.get("CLOUD_OPTICAL_DEPTH"), 0.0, 20.0)
    supports = _candidate_layers(prior, top, cloud_probability)
    if not supports:
        supports = (nearest_prior_level(prior, top),)
    layer_weights = _layer_weights(supports, prior, top)
    columns: list[SatelliteCloudColumn] = []
    for layer_index, level in enumerate(supports):
        layer_probability = cloud_probability * layer_weights[layer_index]
        base_p10, base_p50, base_p90, base_source = _cloud_base_posterior(
            prior, level, top, layer_probability, phase
        )
        probabilities = _height_probabilities(prior, top, layer_probability, level)
        # A geostationary top retrieval cannot prove that a lower layer is
        # absent.  For an upper cloud, explicitly report unknown low cloud.
        low_state = (
            "unknown"
            if quality == "unknown"
            else "unknown"
            if top - prior.terrain_asl_m > 3500.0
            else "detected"
            if (
                max(probabilities.values(), default=0.0) >= 0.55
                or (top - prior.terrain_asl_m <= 500.0 and cloud_probability >= 0.55)
            )
            else "unknown"
        )
        columns.append(
            SatelliteCloudColumn(
                schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                algorithm=SATELLITE_CLOUD_COLUMN_ALGORITHM,
                valid_time_utc=frame.valid_time_utc,
                lat=float(point.lat),
                lon=float(point.lon),
                distance_km=float(distance_km),
                bearing_deg=float(bearing_deg),
                base_p10_asl_m=base_p10,
                base_p50_asl_m=base_p50,
                base_p90_asl_m=base_p90,
                top_asl_m=top,
                terrain_asl_m=prior.terrain_asl_m,
                phase=phase,
                optical_depth=optical_depth,
                cloud_probability_by_height=probabilities,
                horizontal_cloud_probability=cloud_probability,
                low_cloud_state=low_state,
                retrieval_quality=quality,
                source=frame.source,
                vertical_prior_identity=prior.identity,
                vertical_prior_source=prior.source,
                cloud_type=cloud_type,
                base_source=base_source,
                motion_dx_px_per_hour=motion_dx_px_per_hour,
                motion_dy_px_per_hour=motion_dy_px_per_hour,
            )
        )
    return _conserve_cloud_profiles(tuple(columns), cloud_probability)


def _hcai_mask_quality(value: Optional[float], frame_quality: str) -> str:
    if value is None or not math.isfinite(float(value)):
        return "unknown"
    code = int(round(float(value)))
    if code == 0 and frame_quality in {"high", "medium"}:
        return frame_quality
    if code in {4, 5, 6} and frame_quality in {"high", "medium", "low"}:
        return "low"
    return "unknown"


def _hcai_height_quality(value: Optional[float], frame_quality: str) -> str:
    if value is None or not math.isfinite(float(value)):
        return "unknown"
    code = int(round(float(value)))
    if code == 0 and frame_quality in {"high", "medium"}:
        return frame_quality
    return "unknown"


def _merge_qualities(*qualities: str) -> str:
    order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    if not qualities:
        return "unknown"
    return min(qualities, key=lambda value: order.get(value, 0))


def aggregate_retrieval_quality(values: Sequence[str] | Any) -> str:
    """Aggregate spatial quality without letting one fill pixel poison a frame."""
    scores = {"low": 1.0, "medium": 2.0, "high": 3.0}
    statuses = tuple(values)
    usable = [scores[value] for value in statuses if value in scores]
    coverage = len(usable) / max(1, len(statuses))
    if not usable or coverage < 0.25:
        return "unknown"
    mean = sum(usable) / len(usable)
    if mean >= 2.5 and coverage >= 0.75:
        return "high"
    if mean >= 1.5 and coverage >= 0.50:
        return "medium"
    return "low"


def cloud_column_coverage(values: Sequence[Any] | Any) -> float:
    """Fraction of unique horizontal samples with usable calibrated quality.

    One HCAI pixel can produce several inferred pressure layers.  Coverage is
    a sampling-quality measure, so those layers must occupy one denominator
    slot rather than multiplying confidence.
    """
    statuses: dict[tuple[Any, ...], str] = {}
    for cloud_set in tuple(values):
        columns = cloud_set.columns if hasattr(cloud_set, "columns") else cloud_set.get("columns", ())
        if not columns:
            set_time = (
                cloud_set.valid_time_utc
                if hasattr(cloud_set, "valid_time_utc")
                else cloud_set.get("valid_time_utc")
            )
            set_source = cloud_set.source if hasattr(cloud_set, "source") else cloud_set.get("source")
            set_identity = (
                cloud_set.vertical_prior_identity
                if hasattr(cloud_set, "vertical_prior_identity")
                else cloud_set.get("vertical_prior_identity")
            )
            statuses[("empty-set", set_time, set_source, set_identity)] = "unknown"
            continue
        for column in columns:
            if hasattr(column, "retrieval_quality"):
                key = (
                    column.valid_time_utc,
                    round(float(column.lat), 5),
                    round(float(column.lon), 5),
                    round(float(column.distance_km), 2),
                    round(float(column.bearing_deg), 2),
                )
                quality = str(column.retrieval_quality)
            else:
                key = (
                    column["valid_time_utc"],
                    round(float(column["lat"]), 5),
                    round(float(column["lon"]), 5),
                    round(float(column["distance_km"]), 2),
                    round(float(column["bearing_deg"]), 2),
                )
                quality = str(column["retrieval_quality"])
            previous = statuses.get(key)
            if previous is None or RETRIEVAL_QUALITIES_ORDER.get(quality, 0) > RETRIEVAL_QUALITIES_ORDER.get(previous, 0):
                statuses[key] = quality
    if not statuses:
        return 0.0
    return sum(status != "unknown" for status in statuses.values()) / len(statuses)


def aggregate_low_cloud_state(values: Sequence[str] | Any) -> str:
    states = tuple(value for value in values if value in LOW_CLOUD_STATES)
    if not states:
        return "unknown"
    known = [state for state in states if state != "unknown"]
    if known:
        detected = sum(1 for state in known if state == "detected")
        # A single marginal frame used to lock the whole event as "detected":
        # the aggregate scans ±30 minutes and fog forms and dissipates on
        # 10-30 minute scales, so one far-from-event detection is not
        # representative. Escalate to "detected" only when a clear share of
        # the known columns agrees.
        if detected / len(known) >= 0.34:
            return "detected"
    if "clear" in states and "unknown" not in states:
        return "clear"
    return "unknown"


def aggregate_local_low_cloud_state(values: Sequence[Any] | Any, max_distance_km: float = 120.0) -> str:
    states: list[str] = []
    for cloud_set in tuple(values):
        columns = cloud_set.columns if hasattr(cloud_set, "columns") else cloud_set.get("columns", ())
        for column in columns:
            distance = float(column.distance_km if hasattr(column, "distance_km") else column["distance_km"])
            if distance <= float(max_distance_km):
                states.append(
                    str(column.low_cloud_state if hasattr(column, "low_cloud_state") else column["low_cloud_state"])
                )
    return aggregate_low_cloud_state(states)


def aggregate_vertical_prior_identity(values: Sequence[Any] | Any) -> str:
    identities = sorted(
        {
            str(value.vertical_prior_identity if hasattr(value, "vertical_prior_identity") else value["vertical_prior_identity"])
            for value in tuple(values)
            if (value.vertical_prior_identity if hasattr(value, "vertical_prior_identity") else value.get("vertical_prior_identity"))
        }
    )
    if not identities:
        return "unknown"
    if len(identities) == 1:
        return identities[0]
    digest = hashlib.sha256("|".join(identities).encode("utf-8")).hexdigest()[:24]
    return f"gfs-prior-set:{digest}"


def aggregate_vertical_prior_source(values: Sequence[Any] | Any) -> str:
    sources = sorted(
        {
            str(value.vertical_prior_source if hasattr(value, "vertical_prior_source") else value["vertical_prior_source"])
            for value in tuple(values)
            if (value.vertical_prior_source if hasattr(value, "vertical_prior_source") else value.get("vertical_prior_source"))
        }
    )
    if not sources:
        return "unknown"
    if len(sources) == 1:
        return sources[0]
    digest = hashlib.sha256("|".join(sources).encode("utf-8")).hexdigest()[:16]
    return f"mixed-prior:{digest}"


def _hcai_phase(value: Optional[float]) -> str:
    if value is None or not math.isfinite(float(value)):
        return "unknown"
    # NOAA Enterprise Cloud Type/Phase ATBD: 0 clear, 1 liquid water,
    # 2 supercooled water, 3 mixed, 4 ice, 5 uncertain.
    return {1: "water", 2: "water", 3: "mixed", 4: "ice"}.get(
        int(round(value)), "unknown"
    )


def _hcai_cloud_type(value: Optional[float], phase: str) -> str:
    if value is not None and math.isfinite(float(value)):
        # NOAA Enterprise Cloud Type ATBD output: 0 clear, 1 spare,
        # 2 liquid water, 3 supercooled liquid water, 4 mixed phase,
        # 5 optically thick ice, 6 optically thin ice, 7 multilayered ice,
        # 8 undetermined.
        names = {
            0: "clear",
            2: "warm-water",
            3: "supercooled-water",
            4: "mixed",
            5: "opaque-ice",
            6: "thin-ice",
            7: "multilayer-ice",
        }
        return names.get(int(round(value)), "unknown")
    return "ice" if phase == "ice" else "water" if phase == "water" else "unknown"


def _valid_optional(value: Optional[float], lower: float, upper: float) -> Optional[float]:
    if value is None or not math.isfinite(float(value)):
        return None
    value = float(value)
    return value if lower <= value <= upper else None


def _surface_temperature(prior: VerticalPrior) -> Optional[float]:
    candidates = [
        level
        for level in prior.levels
        if level.temperature_k is not None and level.height_asl_m >= prior.terrain_asl_m
    ]
    return min(candidates, key=lambda level: level.height_asl_m).temperature_k if candidates else None


def _cloud_probability(b13: float, surface_temperature: float) -> float:
    # This is only a cloud-presence likelihood. A warm low cloud may have
    # nearly the same B13 temperature as the model surface, so a low value is
    # not sufficient evidence for clear sky.
    return _sigmoid((surface_temperature - b13 - 0.8) / 1.5)


def _hsd_clear_evidence(
    frame: CalibratedSatelliteFrame,
    prior: VerticalPrior,
    b07: float,
    b13: float,
    b15: float,
) -> bool:
    """Return true only for a conservative HSD clear-sky intersection.

    HSD has calibrated radiances but no categorical cloud mask.  In particular,
    a warm low cloud can have nearly the same B13 temperature as the surface.
    Therefore the B13/surface comparison alone deliberately yields unknown;
    ``clear`` requires both multispectral consistency and a dry, cloud-free
    GFS low-level prior.
    """
    if not frame.source.startswith("noaa-himawari9-hsd-calibrated:"):
        return False
    low_levels = [
        level
        for level in prior.levels
        if level.height_asl_m >= prior.terrain_asl_m
        and level.height_asl_m - prior.terrain_asl_m <= 3500.0
    ]
    if not low_levels:
        return False
    if any(
        level.model_cloud_fraction is None
        or level.model_cloud_fraction > 0.05
        or level.rh_percent is not None
        and level.rh_percent >= 72.0
        for level in low_levels
    ):
        return False
    # These are only consistency gates, never a cloud optical proxy.  The
    # broad thresholds tolerate channel calibration differences while refusing
    # to call a single-channel warm-low-cloud case clear.
    return abs(float(b07) - float(b13)) <= 8.0 and abs(float(b13) - float(b15)) <= 3.0


def _cloud_top(prior: VerticalPrior, brightness_temperature: float) -> Optional[float]:
    candidates = [
        level
        for level in prior.levels
        if level.temperature_k is not None and level.height_asl_m >= prior.terrain_asl_m
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda level: abs(level.temperature_k - brightness_temperature)).height_asl_m


def _candidate_layers(prior: VerticalPrior, top: float, probability: float) -> tuple[VerticalPriorLevel, ...]:
    if probability < 0.35:
        return ()
    # A geostationary thermal top is an opaque-cloud top, not a cloud-base
    # retrieval.  Keep lower GFS support as an explicitly capped possibility
    # for multilayer scenes, but make sure an upper candidate is present so
    # that low-level humidity cannot become the only inferred layer.
    upper_only = top - prior.terrain_asl_m > 3500.0
    candidates = [
        level
        for level in prior.levels
        if level.height_asl_m <= top + 900.0
        and (level.model_cloud_fraction is not None and level.model_cloud_fraction >= 0.10
             or level.rh_percent is not None and level.rh_percent >= 78.0)
    ]
    if upper_only:
        nearest_top = nearest_prior_level(prior, top)
        if nearest_top not in candidates:
            candidates.append(nearest_top)
        candidates.sort(key=lambda level: level.height_asl_m)
    if not candidates:
        return ()
    # Preserve distinct separated model-supported layers.  Adjacent pressure
    # samples are one layer; gaps over 1.5 km start another posterior.
    selected: list[VerticalPriorLevel] = []
    for level in candidates:
        if not selected or level.height_asl_m - selected[-1].height_asl_m > 1500.0:
            selected.append(level)
    return tuple(selected[-3:])


def _layer_weights(
    levels: Sequence[VerticalPriorLevel],
    prior: VerticalPrior,
    top: float,
) -> tuple[float, ...]:
    """Split one satellite mask probability across model-supported layers."""
    raw: list[float] = []
    for level in levels:
        cloud = float(level.model_cloud_fraction or 0.0)
        humidity = max(0.0, min(1.0, ((level.rh_percent or 55.0) - 55.0) / 45.0))
        raw.append(max(0.05, cloud, humidity))
    if top - prior.terrain_asl_m > 3500.0:
        # Preserve a real multilayer low deck as an unknown possibility, but
        # cap its share at 20% until a retrieval independent of GFS confirms
        # it.  This keeps unknown distinct from clear without turning RH into
        # an HSD low-cloud observation.
        low_cutoff = top - 2500.0
        upper_total = sum(
            value for value, level in zip(raw, levels) if level.height_asl_m >= low_cutoff
        )
        low_total = sum(
            value for value, level in zip(raw, levels) if level.height_asl_m < low_cutoff
        )
        if upper_total > 0.0 and low_total > 0.0:
            low_target = upper_total * 0.25
            scale = min(1.0, low_target / low_total)
            raw = [
                value * scale if level.height_asl_m < low_cutoff else value
                for value, level in zip(raw, levels)
            ]
    total = sum(raw)
    return tuple(value / total for value in raw) if total > 0.0 else tuple(1.0 / len(levels) for _ in levels)


def _conserve_cloud_profiles(
    columns: tuple[SatelliteCloudColumn, ...],
    observed_probability: float,
) -> tuple[SatelliteCloudColumn, ...]:
    """Prevent a single-pixel mask from being counted once per inferred layer."""
    totals: dict[float, float] = {}
    for column in columns:
        for height, probability in column.cloud_probability_by_height.items():
            totals[float(height)] = totals.get(float(height), 0.0) + float(probability)
    factors = {
        height: min(1.0, max(0.0, observed_probability) / total)
        for height, total in totals.items()
        if total > max(0.0, observed_probability)
    }
    if not factors:
        return columns
    return tuple(
        replace(
            column,
            cloud_probability_by_height={
                height: probability * factors.get(float(height), 1.0)
                for height, probability in column.cloud_probability_by_height.items()
            },
        )
        for column in columns
    )


def _cloud_base_posterior(
    prior: VerticalPrior,
    level: VerticalPriorLevel,
    top: float,
    probability: float,
    phase: str,
) -> tuple[float, float, float, str]:
    terrain = prior.terrain_asl_m
    # GFS low-level saturation can narrow a low-top posterior, but it cannot
    # establish a low cloud below an independently retrieved mid/high top.
    # The top-height gate is the only HSD evidence available here; unknown
    # remains unknown and is never converted to clear.
    low_top = top - terrain <= 3500.0
    if low_top and level.height_asl_m - terrain <= 3500.0:
        spread = 700.0 if level.rh_percent is not None and level.rh_percent >= 88.0 else 1100.0
        p50 = max(terrain, level.height_asl_m - 250.0)
        source = "ahi-top-low+gfs-saturation-layer"
    elif not low_top and level.height_asl_m < top - 2500.0:
        # A lower layer below an independently inferred upper top is retained
        # only as an uncertain multilayer possibility.  Its probability is
        # capped by _layer_weights; its height proxy stays tied to the upper
        # top instead of publishing a false deterministic low base.
        spread = 2200.0
        p50 = max(terrain, top - 1600.0)
        source = "ahi-top+gfs-low-layer-uncertain"
    elif phase == "unknown":
        # HSD thermal channels do not retrieve phase. Keep the cloud-base
        # posterior deliberately wider than a phase-specific prior; this is a
        # height uncertainty proxy, not a hidden phase classifier.
        spread = 2200.0
        p50 = max(terrain, top - 1600.0)
        source = "ahi-top+gfs-cloud-layer-unknown-phase"
    elif phase == "ice" and probability < 0.65:
        spread = 2200.0
        p50 = max(terrain, top - 1800.0)
        source = "ahi-top+gfs-thin-cloud-prior"
    else:
        spread = 1800.0
        p50 = max(terrain, top - 1400.0)
        source = "ahi-top+gfs-cloud-layer"
    p10 = max(terrain, p50 - spread)
    p90 = min(top, p50 + spread)
    if p90 < p50:
        p50 = p90
    if p50 < p10:
        p10 = p50
    return p10, p50, p90, source


def _height_probabilities(
    prior: VerticalPrior,
    top: float,
    cloud_probability: float,
    layer: VerticalPriorLevel,
) -> dict[float, float]:
    result: dict[float, float] = {}
    upper_only = top - prior.terrain_asl_m > 3500.0
    low_cutoff = top - 2500.0
    width = 950.0 if not upper_only and layer.height_asl_m - prior.terrain_asl_m <= 3500.0 else 1800.0
    for level in prior.levels:
        if level.height_asl_m < prior.terrain_asl_m or level.height_asl_m > top + 500.0:
            continue
        vertical = math.exp(-0.5 * ((level.height_asl_m - layer.height_asl_m) / width) ** 2)
        # The 0.35 GFS floor encodes sea-level model trust. On high terrain
        # the model surface layer sits far from the real skin temperature and
        # the B13-surface sigmoid drifts, so scale the floor down instead of
        # manufacturing low-cloud probability on plateaus.
        gfs_floor = 0.35 - 0.20 * clamp01((prior.terrain_asl_m - 1500.0) / 2000.0)
        model = gfs_floor
        if level.model_cloud_fraction is not None:
            model = max(model, min(1.0, level.model_cloud_fraction))
        if level.rh_percent is not None:
            model = max(model, min(1.0, max(0.0, (level.rh_percent - 55.0) / 45.0)))
        value = max(0.0, min(1.0, cloud_probability * vertical * model))
        if upper_only and level.height_asl_m < low_cutoff:
            # A top-only retrieval cannot distinguish a lower deck from the
            # upper cloud. Keep that unresolved contribution bounded rather
            # than allowing the GFS humidity profile to dominate the score.
            value = min(value, 0.20)
        result[float(level.height_asl_m)] = value
    return result


def _cloud_type(level: VerticalPriorLevel, top: float, phase: str) -> str:
    agl = top - level.height_asl_m
    if agl > 4500.0:
        return "deep-ice" if phase == "ice" else "deep-mixed"
    if level.height_asl_m <= 3500.0:
        return "low-water" if phase == "water" else "low-mixed"
    return "high-ice" if phase == "ice" else "mid-mixed"


def _column_quality(frame: CalibratedSatelliteFrame, prior: VerticalPrior, probability: float) -> str:
    if frame.quality == "high" and probability >= 0.55:
        return "high"
    if frame.quality in {"high", "medium"} and probability >= 0.30:
        return "medium"
    return "low"


def _frame_quality(frame: CalibratedSatelliteFrame) -> str:
    return frame.quality if frame.quality in RETRIEVAL_QUALITIES else "unknown"


def _sigmoid(value: float) -> float:
    if value >= 35.0:
        return 1.0
    if value <= -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def vertical_prior_from_features(
    *,
    valid_time_utc: datetime,
    cycle_time_utc: datetime,
    forecast_hour: int,
    source: str,
    identity: str,
    terrain_asl_m: float,
    features: Sequence[Any],
    target_lat: float,
    target_lon: float,
) -> VerticalPrior:
    """Build one prior from the nearest GFS horizontal column."""

    candidates = [feature for feature in features if math.isfinite(float(feature.lat)) and math.isfinite(float(feature.lon))]
    if not candidates:
        raise ValueError("GFS vertical prior has no horizontal samples")
    nearest = min(
        candidates,
        key=lambda feature: (float(feature.lat) - target_lat) ** 2 + (float(feature.lon) - target_lon) ** 2,
    )
    same_column = [
        feature
        for feature in candidates
        if abs(float(feature.lat) - float(nearest.lat)) <= 1e-5
        and abs(float(feature.lon) - float(nearest.lon)) <= 1e-5
    ]
    levels = tuple(
        VerticalPriorLevel(
            pressure_hpa=int(feature.pressure_hpa),
            height_asl_m=float(feature.geopotential_height_m),
            temperature_k=feature.temperature_k,
            rh_percent=feature.rh_percent,
            model_cloud_fraction=(
                None
                if feature.cloud_fraction_percent is None
                else max(0.0, min(1.0, float(feature.cloud_fraction_percent) / 100.0))
            ),
        )
        for feature in sorted(same_column, key=lambda item: float(item.geopotential_height_m))
        if math.isfinite(float(feature.geopotential_height_m))
        and float(feature.geopotential_height_m) >= float(terrain_asl_m) - 100.0
    )
    if not levels:
        raise ValueError("GFS vertical prior has no levels above terrain")
    return VerticalPrior(
        valid_time_utc=valid_time_utc,
        cycle_time_utc=cycle_time_utc,
        forecast_hour=forecast_hour,
        source=source,
        terrain_asl_m=float(terrain_asl_m),
        levels=levels,
        identity=identity,
        target_lat=float(target_lat),
        target_lon=float(target_lon),
    )


def cloud_columns_to_features(
    columns: Sequence[SatelliteCloudColumn],
    prior: VerticalPrior,
    point: Any,
    *,
    footprint: tuple[float, float, float] | None = None,
) -> list[Any]:
    """Map posterior cloud probabilities to the actual GFS pressure levels.

    This adapter never invents a standard-atmosphere pressure.  Every output
    pressure is an existing level from the matching GFS prior.
    """

    from firesky.models import GridCellFeature

    if footprint is not None:
        if len(footprint) != 3:
            raise ValueError("satellite sample footprint must contain width, near and far")
        footprint_width, footprint_near, footprint_far = (
            float(value) for value in footprint
        )
        if (
            not math.isfinite(footprint_width)
            or not math.isfinite(footprint_near)
            or not math.isfinite(footprint_far)
            or footprint_width <= 0.0
            or footprint_near < 0.0
            or footprint_far < footprint_near
        ):
            raise ValueError("satellite sample footprint is invalid")
    else:
        footprint_width = footprint_near = footprint_far = None

    # Several posterior layers may cover the same model pressure level.  Merge
    # the conditional profile by a bounded union, but keep the authoritative
    # horizontal probability as a separate value.  The profile is P(height |
    # cloud), not another copy of the horizontal footprint probability.
    merged: dict[tuple[float, float, float, float, int], float] = {}
    level_by_key: dict[tuple[float, float, float, float, int], VerticalPriorLevel] = {}
    for column in columns:
        if column.low_cloud_state == "clear" or not column.cloud_probability_by_height:
            continue
        for level in prior.levels:
            probability = column.cloud_probability_by_height.get(float(level.height_asl_m))
            if probability is None or probability < 0.08:
                continue
            height = float(level.height_asl_m)
            if column.top_asl_m is not None and height > column.top_asl_m + 500.0:
                continue
            key = (
                round(float(column.lat), 5),
                round(float(column.lon), 5),
                round(float(column.distance_km), 2),
                round(float(column.bearing_deg), 2),
                int(level.pressure_hpa),
            )
            merged[key] = 1.0 - (1.0 - merged.get(key, 0.0)) * (1.0 - float(probability))
            level_by_key[key] = level
    horizontal_by_key: dict[tuple[float, float, float, float], float] = {}
    upper_low_cutoff_by_key: dict[tuple[float, float, float, float], float] = {}
    for column in columns:
        probability = column.horizontal_cloud_probability
        if probability is None:
            continue
        key = (
            round(float(column.lat), 5),
            round(float(column.lon), 5),
            round(float(column.distance_km), 2),
            round(float(column.bearing_deg), 2),
        )
        horizontal_by_key[key] = max(
            horizontal_by_key.get(key, 0.0), float(probability)
        )
        if (
            column.top_asl_m is not None
            and column.terrain_asl_m is not None
            and column.top_asl_m - column.terrain_asl_m > 3500.0
        ):
            upper_low_cutoff_by_key[key] = max(
                upper_low_cutoff_by_key.get(key, float("-inf")),
                float(column.top_asl_m) - 2500.0,
            )
    for key, probability in tuple(merged.items()):
        horizontal_key = key[:4]
        low_cutoff = upper_low_cutoff_by_key.get(horizontal_key)
        horizontal_probability = horizontal_by_key.get(horizontal_key)
        level = level_by_key[key]
        if (
            low_cutoff is not None
            and horizontal_probability is not None
            and level.height_asl_m < low_cutoff
        ):
            # Multiple inferred layers are unioned below. Keep the unresolved
            # lower-deck contribution capped after that union as well; two
            # weak priors must not combine into a deterministic low cloud.
            merged[key] = min(float(probability), 0.20 * float(horizontal_probability))
    features: list[GridCellFeature] = []
    for key, probability in merged.items():
        lat, lon, distance, bearing, pressure = key
        level = level_by_key[key]
        horizontal_key = key[:4]
        horizontal_probability = horizontal_by_key.get(horizontal_key)
        if horizontal_probability is None or horizontal_probability <= 0.0:
            continue
        # The merged profile is an unconditional vertical probability for
        # this footprint.  Store only the conditional profile in the feature;
        # horizontal occupancy is carried separately and must not be counted
        # again by the vertical scorer.
        conditional_probability = float(
            np.clip(float(probability) / max(float(horizontal_probability), 1e-9), 0.0, 1.0)
        )
        features.append(
            GridCellFeature(
                lat=lat,
                lon=lon,
                distance_km=distance,
                bearing_deg=bearing,
                pressure_hpa=pressure,
                geopotential_height_m=float(level.height_asl_m),
                rh_percent=float(level.rh_percent if level.rh_percent is not None else 70.0),
                temperature_k=level.temperature_k,
                omega_pa_s=None,
                u_ms=None,
                v_ms=None,
                cloud_fraction_percent=100.0 * conditional_probability,
                horizontal_cloud_probability=horizontal_probability,
                cloud_probability_given_cloud=conditional_probability,
                # HCAI optical depth remains in the cloud-column contract; it
                # is not converted into made-up kg/kg condensate.
                cloud_liquid_kg_kg=None,
                cloud_ice_kg_kg=None,
                surface_visibility_m=None,
                precip_rate_kg_m2_s=None,
                footprint_azimuth_width_deg=footprint_width,
                footprint_near_distance_km=footprint_near,
                footprint_far_distance_km=footprint_far,
            )
        )
    return features


__all__ = [
    "SATELLITE_CLOUD_COLUMN_ALGORITHM",
    "SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION",
    "SATELLITE_CALIBRATED_FRAME_SCHEMA_VERSION",
    "DEFAULT_HCAI_CACHE_DIR",
    "DEFAULT_HCAI_COMPACT_CACHE_DIR",
    "DEFAULT_HSD_CACHE_DIR",
    "DEFAULT_HSD_COMPACT_CACHE_DIR",
    "HIMAWARI_PROJECTION_IDENTITY",
    "HIMAWARI_HSD_PROJECTION_IDENTITY",
    "HSD_COMPACT_CACHE_ALGORITHM",
    "HSD_COMPACT_CACHE_SCHEMA_VERSION",
    "HSD_DEFAULT_DOWNLOAD_WORKERS",
    "HSD_MAX_DOWNLOAD_WORKERS",
    "CalibratedSatelliteFrame",
    "SegmentedHimawariHsdFrameProvider",
    "HsdCacheWarmer",
    "HsdCompactCache",
    "HsdCompactCacheProvider",
    "AutoHsdCompactCacheProvider",
    "HsdMotionEstimate",
    "HsdAdvectionPlan",
    "estimate_hsd_motion",
    "plan_hsd_advection",
    "advect_hsd_frame",
    "SatelliteCloudColumn",
    "SatelliteCloudColumnSet",
    "VerticalPrior",
    "VerticalPriorLevel",
    "cloud_columns_to_features",
    "aggregate_low_cloud_state",
    "aggregate_local_low_cloud_state",
    "aggregate_retrieval_quality",
    "cloud_column_coverage",
    "aggregate_vertical_prior_identity",
    "aggregate_vertical_prior_source",
    "retrieve_cloud_columns",
    "sample_calibrated_frame",
    "validate_cloud_column_payload",
    "vertical_prior_from_features",
]
