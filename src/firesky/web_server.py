from __future__ import annotations

import re
import json
import gzip
import hashlib
import hmac
import mimetypes
import multiprocessing as mp
import copy
import math
import os
import queue
import struct
import threading
import time
import tempfile
import urllib.parse
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from numbers import Real
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from types import SimpleNamespace
import numpy as np

from firesky import rust_astronomy
from firesky.aerosol import (
    _cams_backend_identity,
    CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS,
    CamsAdsAerosolProvider,
    cams_area_from_model_bbox,
    cams_profile_bracket_times,
    load_cams_optics_grid,
    parse_cams_cache_name,
    resolve_aerosol_provider,
)
from firesky.analysis_constants import source_domain_radius_km
from firesky.ecmwf import EcmwfOpenDataClient, choose_ecmwf_cycle
from firesky.ecmwf_ifs import (
    ECMWF_IFS_PRESSURE_LEVELS,
    EcmwfIfsAdapter,
    load_ecmwf_ifs_grid_coordinates,
)
from firesky.ecmwf_point import (
    _ecmwf_point_domain_geometry,
    choose_ecmwf_ifs_cycle,
    choose_ecmwf_ifs_cycle_for_event,
    ecmwf_point_evaluation_times,
    run_ecmwf_basic_point_forecast,
)
from firesky.ecmwf_point import _tensor_to_native_grid
from firesky.gfs import (
    EAST_ASIA_OPERATIONAL_BBOX,
    GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES,
    GFS_DIAGNOSTIC_PRESSURE_LEVELS,
    GFS_DIAGNOSTIC_REQUEST_SCHEMA_VERSION,
    GFS_DIAGNOSTIC_HYDROMETEOR_UNITS,
    GFS_OPERATIONAL_PADDING_KM,
    GfsInputUnavailableError,
    GfsNomadsClient,
    bbox_contains,
    canonical_subset_bbox,
    choose_gfs_cycle,
)
from firesky.forecast_characteristics import fire_cloud_quality_level
from firesky.forward_field_scoring import MAX_SCORE as FIRE_CLOUD_QUALITY_MAX_SCORE
from firesky.geometry import (
    bounding_box,
    haversine_km,
    initial_bearing_deg,
    solar_event_time_local,
    solar_snapshot,
)
from firesky.version import FORECAST_ALGORITHM_VERSION
from firesky.html_report import write_html_report
from firesky.location import fetch_point_metadata, fetch_timezone, search_location_fast
from firesky.map_frames import (
    REQUIRED_QUALITY_FLOAT_FIELDS,
    canonical_map_bearing_deg,
    read_map_frame,
    write_map_frame_payloads,
    write_map_frames,
)
from firesky.models import ForecastRequest, GeoPoint
from firesky.satellite_cloud_column import (
    AutoHsdCompactCacheProvider,
    HsdCacheWarmer,
    HsdCompactCache,
    RustHsdCompactCacheProvider,
    SegmentedHimawariHsdFrameProvider,
)
from firesky.satellite import (
    build_satellite_cross_section,
    JmaNowcastAnalyzer,
)
from firesky.national_batch import NATIONAL_CONFIDENCE_KIND
from firesky.national_batch import (
    NATIONAL_AEROSOL_EXTINCTION_UNITS,
    NATIONAL_AEROSOL_NORMALIZATION,
    NATIONAL_AEROSOL_PROFILE_MODE,
    NATIONAL_AEROSOL_SPATIAL_INTERPOLATION,
    NATIONAL_AEROSOL_TEMPORAL_INTERPOLATION,
    NATIONAL_BATCH_ALGORITHM,
    NATIONAL_ANALYSIS_RADIUS_KM,
    NATIONAL_INPUT_MANIFEST_FORMAT,
    LAYER_PROFILE_FIELDS,
    NationalBatchPrepared,
    _external_cache_subset_indices,
    _load_existing_external_derived_cache,
    _model_derived_surface_cloud_path,
    build_national_input_manifest,
    build_national_cross_section,
    build_national_frame_payloads,
    fire_cloud_quality_from_assessments,
    prepare_model_columns,
    prepare_external_model_columns,
    prepare_solar_event_timeline,
    prepare_solar_samples,
    run_native_national_batch,
    apply_decoded_model_surface_elevations,
    validate_prepared_model_surface_elevations,
)
from firesky.national_point_core import materialize_national_point_core
from firesky.national_frame_contract import (
    NATIONAL_FRAME_PRECISION_FIELDS,
    validate_current_national_frame_data,
    validate_current_national_frame_metadata,
)
from firesky.native_core import native_status
from firesky.regional_diagnostics import (
    RegionalDiagnosticBundleWriter,
    diagnostic_bundle_path,
    read_regional_diagnostic,
)
from firesky.service import (
    ForecastService,
    NOWCAST_CORE_WINDOW_MINUTES,
    evaluation_times_utc,
    nowcast_duration_is_qualified,
    nowcast_event_uses_satellite_scoring,
    required_model_hours,
    resolve_nowcast_gfs_cycle,
    resolve_reference_time,
    satellite_lookup_reference_time,
)
from firesky.storage import dated_directory, first_existing, storage_phenomenon
from firesky.time_contract import (
    parse_iso_date,
    parse_utc_iso_datetime,
    parse_zoned_iso_datetime,
)
from firesky.time_utils import CHINA_TIME_ZONE, china_today
from firesky.cache_tasks import (
    REGION_BBOXES,
    _coverage_points,
    _country_configs,
    _precomputed_solar_schedule,
    _cams_area_from_bbox,
    parse_forecast_hours,
    prefetch_cache,
    prune_cache,
    resolve_datasets_for_areas,
    daemon_task_results,
)
from firesky.cams_optics import (
    SPECTRAL_OPTICS_CACHE_PREFIX,
    SPECTRAL_OPTICS_SCHEMA_VERSION,
    build_spectral_optics_request,
    load_cams_spectral_optics,
    load_spectral_optics_cache,
    read_spectral_optics_cache_metadata,
    spectral_optics_cache_filename,
    spectral_optics_cache_identity,
    validate_spectral_optics_cache_metadata,
    write_spectral_optics_cache_metadata,
)


# The web UI is the SvelteKit frontend build shipped one level above the
# package root (firesky/frontend/build).
STATIC_DIR = Path(__file__).resolve().parents[2] / "frontend"
BUILD_DIR = STATIC_DIR / "build"
MAX_REQUEST_BYTES = 64 * 1024
CHINA_CITY_CATALOG_POINT_MODE = "china-city-catalog"
CHINA_CITY_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "china-city-catalog.json"
CHINA_PRODUCTION_POINT_COUNT = 36_685
CHINA_PRODUCTION_SPACING_DEG = 0.25
POINT_GRID_RESOLUTION_MODE = "nearest-regional-grid-v1"
ECMWF_FORECAST_CONTRACT_REASON = (
    "当前 ECMWF adapter 未提供同一评分合同所需的目标层云量、液水/冰水与完整地形输入，"
    "不能以 GFS 代理静默补齐。"
)


def _cams_diagnostic_enabled(payload: dict | None) -> bool:
    """Return whether the caller explicitly requested expanded CAMS diagnostics."""
    if not payload:
        return False
    product = str(payload.get("cams_product") or "").strip().lower()
    return bool(
        payload.get("cams_diagnostic")
        or product in {"spectral", "expanded-optics", "cams-spectral"}
    )


def _gfs_diagnostic_enabled(payload: dict | None) -> bool:
    """Return whether the caller explicitly requested expanded GFS diagnostics."""
    if not payload:
        return False
    product = str(payload.get("gfs_product") or "").strip().lower()
    return bool(
        payload.get("gfs_diagnostic")
        or product in {"diagnostic", "expanded", "gfs-diagnostic"}
    )


@lru_cache(maxsize=1)
def _china_city_catalog_points() -> tuple[dict[str, Any], ...]:
    """Load the 695 checked-in points used only for backend/offline Bot validation."""
    try:
        payload = json.loads(CHINA_CITY_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"中国城市目录不可用：{CHINA_CITY_CATALOG_PATH}"
        ) from exc
    rows = payload.get("cities") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 695:
        raise RuntimeError("中国城市目录必须包含 695 个城市。")
    points: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("中国城市目录包含无效记录。")
        name = str(row.get("short_name") or row.get("name") or "").strip()
        if not name or name in seen_names:
            raise RuntimeError("中国城市目录包含重复或空城市名。")
        seen_names.add(name)
        points.append({
            "name": name[:120],
            "lat": bounded_float(row.get("lat"), "目录纬度", -90.0, 90.0),
            "lon": bounded_float(row.get("lon"), "目录经度", -180.0, 180.0),
            "elevation_m": 0.0,
            "_elevation_source": "model-grid",
            "timezone_name": "Asia/Shanghai",
        })
    return tuple(points)

# Regions exposed on the compute / outlook pages. These are the large operational
# regions (NOT countries) so they line up with the data-assets prefetch regions.
# Bounds here are (west, south, east, north) to match the destructure in
# national_points_from_payload. Most large regions span several timezones, so
# their grid points use the real IANA timezone. China is the deliberate
# exception: the public product is a China-time view and all of its points must
# use the country's single legal civil timezone, including the western border.
REGION_LABELS: dict[str, str] = {
    "china": "中国",
    "east-asia": "东亚",
    "southeast-asia": "东南亚",
    "south-asia": "南亚",
    "europe": "欧洲",
    "north-america": "北美",
    "south-america": "南美",
    "oceania": "大洋洲",
    "africa-middle-east": "非洲及中东",
    "north-asia": "北亚",
}
REGION_POINT_PREFIX: dict[str, str] = {
    "china": "CN",
    "east-asia": "EA",
    "southeast-asia": "SE",
    "south-asia": "SA",
    "europe": "EU",
    "north-america": "NA",
    "south-america": "SA2",
    "oceania": "OC",
    "africa-middle-east": "AME",
    "north-asia": "NAS",
}

REGIONAL_FORECAST_AREAS: dict[str, dict[str, Any]] = {}
for _code, _label in REGION_LABELS.items():
    _w, _e, _n, _s = REGION_BBOXES[_code]  # REGION_BBOXES uses (west, east, north, south)
    REGIONAL_FORECAST_AREAS[_code] = {
        "label": _label,
        "bounds": (_w, _s, _e, _n),  # (west, south, east, north)
        "point_prefix": REGION_POINT_PREFIX[_code],
    }
    if _code == "china":
        REGIONAL_FORECAST_AREAS[_code]["timezone_name"] = "Asia/Shanghai"


# Identical requests share one computation. A failed or wedged producer must
# not leave every follower request waiting forever.
FORECAST_INFLIGHT_WAIT_TIMEOUT_SECONDS = 300.0
DATA_FETCH_MAX_RUNTIME_SECONDS = 1800.0
DATA_FETCH_STALL_TIMEOUT_SECONDS = 600.0
SATELLITE_WARMUP_REMOTE_GATE_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class WebServerConfig:
    workers: Optional[int] = None
    report_dir: Path = Path("reports/web")
    aerosol_source: str = "cams"
    gfs_cache_dir: Path = Path(".cache/gfs")
    ecmwf_cache_dir: Path = Path(".cache/ecmwf")
    cams_cache_dir: Path = Path(".cache/cams")
    map_cache_dir: Path = Path(".cache/map-frames")
    regional_cache_dir: Path = Path(".cache/regional-forecasts")
    point_cache_dir: Path = Path(".cache/point-forecasts")
    satellite_cache_dir: Path = Path(".cache/himawari-hsd")
    satellite_warmup_token: Optional[str] = None
    forecast_cache_seconds: int = 300
    # Explicit scheduler target/event.  None keeps cycle refresh read-only.
    regional_refresh_payload: Optional[dict[str, Any]] = None


def _cached_satellite_frame_provider(config: WebServerConfig):
    # HSD 独占：HCAI 整文件下载过慢已从实况链路移除。
    configured = os.environ.get("FIRESKY_HSD_BACKEND")
    if configured is None:
        # The desktop Rust backend owns the compact-frame validation and
        # retrieval path. Keep standalone web callers on the Python default,
        # while making the desktop selector apply consistently to nowcast
        # scoring without requiring a second undocumented environment flag.
        configured = (
            "rust"
            if os.environ.get("FIRESKY_DESKTOP_DATA_BACKEND", "python")
            .strip()
            .lower()
            == "rust"
            else "python"
        )
    backend = configured.strip().lower()
    if backend not in {"python", "rust"}:
        raise ValueError("FIRESKY_HSD_BACKEND must be python or rust")
    if backend == "rust":
        return RustHsdCompactCacheProvider(
            config.satellite_cache_dir,
            allow_foreground=False,
        )
    return AutoHsdCompactCacheProvider(
        config.satellite_cache_dir,
        allow_foreground=False,
    )


def _native_regional_backend_enabled() -> bool:
    """Select the native public regional executor without changing defaults."""
    configured = os.environ.get("FIRESKY_REGIONAL_DATA_BACKEND", "python")
    backend = configured.strip().lower()
    if backend not in {"python", "rust"}:
        raise ValueError("FIRESKY_REGIONAL_DATA_BACKEND must be python or rust")
    return backend == "rust"


class ForecastResponseCache:
    # Entries are full response deepcopies; the key space is request-derived
    # (coordinates/date/phenomenon/cycle), so an unbounded dict grows for the
    # process lifetime. Cap it LRU-style; 512 entries is far above any real
    # TTL (300 s) working set.
    MAX_ENTRIES = 512

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._inflight: dict[str, threading.Event] = {}

    def get_or_compute(self, key: str, ttl_seconds: int, compute) -> dict[str, Any]:
        if ttl_seconds <= 0:
            return compute()
        while True:
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None:
                    self._entries.move_to_end(key)
                    if cached[0] > time.monotonic():
                        return copy.deepcopy(cached[1])
                event = self._inflight.get(key)
                if event is None:
                    event = threading.Event()
                    self._inflight[key] = event
                    break
            if not event.wait(timeout=FORECAST_INFLIGHT_WAIT_TIMEOUT_SECONDS):
                raise TimeoutError("等待相同预测任务完成超时。")
        try:
            result = compute()
        except BaseException:
            with self._lock:
                self._inflight.pop(key, None)
                event.set()
            raise
        with self._lock:
            self._store(key, time.monotonic() + ttl_seconds, copy.deepcopy(result))
            self._inflight.pop(key, None)
            event.set()
        return result

    def put(self, key: str, ttl_seconds: int, result: dict[str, Any]) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            self._store(key, time.monotonic() + ttl_seconds, copy.deepcopy(result))

    def _store(self, key: str, expires_at: float, value: dict[str, Any]) -> None:
        self._entries[key] = (expires_at, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.MAX_ENTRIES:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


FORECAST_RESPONSE_CACHE = ForecastResponseCache()


class ForecastCancelled(Exception):
    """Raised by progress callbacks when a forecast is cancelled."""


def _utc_now_iso() -> str:
    """Wall-clock timestamp for API responses (monotonic 时间没有固定纪元，不能外发)."""
    return datetime.now(timezone.utc).isoformat()


# 后端真实状态 → 管理台契约词表。前端 types.ts 沿用旧 Go 网关的
# computing/succeeded 命名，统一在 API 边界映射，避免两侧词表漂移。
# 后端真实状态全集（列表过滤参数白名单）。
JOB_STATUSES = {"queued", "running", "done", "failed", "cancelled"}


class ForecastJob:
    def __init__(self, job_id: str, defer_result: bool = False, payload: Any = None) -> None:
        self.job_id = job_id
        self.defer_result = defer_result
        # 内部计时用单调时钟；对外序列化一律用 UTC ISO 字符串。
        self._created_mono = time.monotonic()
        self._completed_mono: Optional[float] = None
        self.created_at = _utc_now_iso()
        self.completed_at: Optional[str] = None
        self.started_at: Optional[str] = None
        self.status = "running"
        self.result: Optional[dict[str, Any]] = None
        self.error: Optional[BaseException] = None
        try:
            self.request_payload: Optional[str] = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            self.request_payload = None
        self.cache_key: Optional[str] = None
        self.algorithm_version = FORECAST_ALGORITHM_VERSION
        # 最近一次进度快照：进度除事件流外在锁内同步落到任务对象上，
        # 供列表/详情端点直接读取（SSE 断开时前端仍有进度可显示）。
        self.percent = 0
        self.stage = "queued"
        self.message = "预测任务已创建"
        self._lock = threading.RLock()
        self._cancel_requested = threading.Event()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()

    def emit(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload["elapsed_seconds"] = round(time.monotonic() - self._created_mono, 1)
        self._events.put(payload)

    def progress(self, event: dict[str, Any]) -> None:
        with self._lock:
            if self._cancel_requested.is_set():
                raise ForecastCancelled("计算已取消")
            if self._completed_mono is not None:
                return
            payload = {
                "event": "progress",
                "stage": str(event.get("stage") or "running"),
                "percent": max(0, min(99, int(event.get("percent") or 0))),
                "message": str(event.get("message") or "预测处理中"),
            }
            details = event.get("details")
            if isinstance(details, dict):
                payload["details"] = details
            self.percent = payload["percent"]
            self.stage = payload["stage"]
            self.message = payload["message"]
            self.emit(payload)

    def start(self) -> bool:
        with self._lock:
            if self._cancel_requested.is_set() or self._completed_mono is not None:
                return False
            self.status = "running"
            self.started_at = _utc_now_iso()
            return True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancel_requested.is_set()

    def done(self, result: dict[str, Any]) -> None:
        with self._lock:
            if self._cancel_requested.is_set() or self._completed_mono is not None:
                return
            self.status = "done"
            self._completed_mono = time.monotonic()
            self.completed_at = _utc_now_iso()
            self.percent = 100
            self.stage = "done"
            self.message = "预测完成"
            self.result = result
            event = {
                "event": "done",
                "stage": "done",
                "percent": 100,
                "message": "预测完成",
            }
            if self.defer_result:
                event["result_ref"] = self.job_id
            else:
                event["result"] = result
            self.emit(event)

    def failed(self, error: BaseException) -> None:
        with self._lock:
            if self._cancel_requested.is_set() or self._completed_mono is not None:
                return
            self.status = "failed"
            self._completed_mono = time.monotonic()
            self.completed_at = _utc_now_iso()
            self.percent = 100
            self.stage = "failed"
            self.error = error
            unavailable = isinstance(error, GfsInputUnavailableError)
            self.message = str(error)
            self.emit(
                {
                    "event": "failed",
                    "stage": "failed",
                    "percent": 100,
                    "message": str(error),
                    "status": "unavailable" if unavailable else "failed",
                    "error_type": type(error).__name__,
                }
            )

    def cancel(self) -> bool:
        with self._lock:
            if self._completed_mono is not None:
                return False
            if self._cancel_requested.is_set():
                return True
            self._cancel_requested.set()
            self.status = "cancelled"
            self._completed_mono = time.monotonic()
            self.completed_at = _utc_now_iso()
            self.percent = 100
            self.stage = "cancelled"
            self.message = "计算已取消"
            self.emit(
                {
                    "event": "cancelled",
                    "stage": "cancelled",
                    "percent": 100,
                    "message": "计算已取消",
                }
            )
            return True

    def next_event(self, timeout: float = 15.0) -> Optional[dict[str, Any]]:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None


class ForecastJobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, ForecastJob] = {}
        self._regional_dedupe: dict[str, str] = {}
        # Regional runs share the large GFS/CAMS/native batch resources. Keep
        # them in submission order behind one dedicated consumer; ordinary
        # point/nowcast jobs retain their existing independent execution path.
        self._regional_queue: queue.Queue[tuple[ForecastJob, Any, WebServerConfig, Any]] = queue.Queue()
        self._regional_worker = threading.Thread(
            target=self._run_regional_queue,
            name="firesky-regional-fifo",
            daemon=True,
        )
        self._regional_worker.start()

    def start(
        self,
        payload: Any,
        config: WebServerConfig,
        runner=None,
        *,
        defer_result: bool = False,
        regional: bool = False,
        dedupe_key: str | None = None,
    ) -> ForecastJob:
        self._cleanup()
        with self._lock:
            if regional and dedupe_key:
                existing_id = self._regional_dedupe.get(str(dedupe_key))
                existing = self._jobs.get(existing_id) if existing_id else None
                if existing is not None:
                    # A failed regional task is deliberately retained for the
                    # fingerprint's lifetime.  The next GFS cycle produces a
                    # new fingerprint; repeated polling cannot retry the same
                    # failed input in parallel or in a loop.
                    return existing
                self._regional_dedupe.pop(str(dedupe_key), None)
            job = ForecastJob(uuid.uuid4().hex, defer_result=defer_result, payload=payload)
            if regional:
                job.status = "queued"
            if dedupe_key:
                # 区域任务的指纹即缓存键；普通点任务无缓存键，保持 None。
                job.cache_key = str(dedupe_key)
            self._jobs[job.job_id] = job
            if regional and dedupe_key:
                self._regional_dedupe[str(dedupe_key)] = job.job_id
        if regional:
            # Regional scoring shares the large native/GFS/CAMS working set and
            # is intentionally FIFO. Surface the wait position immediately so
            # a queued job is distinguishable from a stalled worker.
            queue_ahead = self._regional_queue.qsize()
            self._regional_queue.put((job, payload, config, runner))
            job.progress({
                "stage": "queued",
                "percent": 1,
                "message": (
                    "区域计算排队中"
                    if queue_ahead == 0
                    else f"区域计算排队中，前方约 {queue_ahead} 个任务"
                ),
                "details": {
                    "queue_position": queue_ahead + 1,
                    "queue_ahead": queue_ahead,
                },
            })
        else:
            thread = threading.Thread(
                target=self._run,
                args=(job, payload, config, runner),
                daemon=True,
            )
            thread.start()
        return job

    def get(self, job_id: str) -> Optional[ForecastJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def find_regional_by_key(self, dedupe_key: str) -> Optional[ForecastJob]:
        self._cleanup()
        with self._lock:
            job_id = self._regional_dedupe.get(str(dedupe_key))
            return self._jobs.get(job_id) if job_id else None

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        return job.cancel() if job is not None else False

    def snapshot_list(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read-only view of in-memory jobs for the operations console.

        firesky has no durable job table (that lived in the old Go gateway), so
        the console lists whatever this process currently knows about.
        """
        with self._lock:
            jobs = list(self._jobs.values())
        if status:
            # 过滤参数用对外词表，先归一化回后端真实状态再过滤；
            # 未知状态严格过滤为空集，而不是静默返回全部任务。
            requested = status
            jobs = [job for job in jobs if requested in JOB_STATUSES and job.status == requested]
        rows = []
        for job in jobs:
            row = {
                "id": job.job_id,
                "status": job.status,
                "progress_percent": job.percent,
                "progress_stage": job.stage,
                "progress_message": job.message,
                "error_message": str(job.error) if job.error is not None else None,
                "request_payload": job.request_payload,
                "started_at": job.started_at,
                "cache_key": job.cache_key,
                "algorithm_version": job.algorithm_version,
                "created_at": job.created_at,
                "completed_at": job.completed_at,
            }
            rows.append(row)
        total = len(rows)
        window = rows[offset : offset + max(1, min(limit, 200))]
        return {"items": window, "total": total, "offset": offset, "limit": limit}

    def _run(self, job: ForecastJob, payload: Any, config: WebServerConfig, runner=None) -> None:
        try:
            if not job.start():
                return
            job.progress({"stage": "queued", "percent": 1, "message": "预测任务已创建"})
            task_runner = runner or run_forecast_payload
            result = task_runner(payload, config, progress_callback=job.progress)
        except ForecastCancelled:
            return
        except BaseException as exc:
            job.failed(exc)
        else:
            job.done(result)

    def _run_regional_queue(self) -> None:
        while True:
            job, payload, config, runner = self._regional_queue.get()
            try:
                if job.is_cancelled():
                    continue
                self._run(job, payload, config, runner)
            finally:
                self._regional_queue.task_done()

    def _cleanup(self) -> None:
        cutoff = time.monotonic() - 900
        with self._lock:
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job._completed_mono is not None and job._completed_mono < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)
                for dedupe_key, mapped_job_id in tuple(self._regional_dedupe.items()):
                    if mapped_job_id == job_id:
                        self._regional_dedupe.pop(dedupe_key, None)


FORECAST_JOBS = ForecastJobRegistry()


class SatelliteWarmupJob:
    """Bounded background calibrated Himawari warmup task.

    Forecast requests never invoke this worker. Keeping the segmented HSD
    download separate makes online scoring cache-only and deterministic.
    """

    def __init__(self, job_id: str, payload: dict[str, Any]) -> None:
        self.job_id = job_id
        self.payload = dict(payload)
        self.created_at = time.monotonic()
        self.completed_at: Optional[float] = None
        self.status = "queued"
        self.result: Optional[dict[str, Any]] = None
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "result": dict(self.result) if self.result is not None else None,
                "error": self.error,
                "elapsed_seconds": round(
                    (self.completed_at or time.monotonic()) - self.created_at, 1
                ),
            }


class SatelliteWarmupJobRegistry:
    def __init__(self, warmup_handler=None) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SatelliteWarmupJob] = {}
        self._active_by_key: dict[str, str] = {}
        self._warmup_handler = warmup_handler
        # Serialize cold HSD downloads so a burst of point requests cannot
        # multiply upstream bandwidth or duplicate the same raw segments.
        self._remote_gate = threading.Semaphore(1)

    @staticmethod
    def _dedupe_key(payload: dict[str, Any]) -> str:
        reference_raw = str(payload.get("reference_time_utc") or "")
        try:
            reference = parse_utc_iso_datetime(reference_raw, "reference_time_utc")
            # Himawari scans are ten-minute products; requests in the same bucket
            # should share one download task.
            reference_raw = reference.replace(
                minute=(reference.minute // 10) * 10,
                second=0,
                microsecond=0,
            ).isoformat()
        except (TypeError, ValueError):
            pass
        identity = {
            "lat": payload.get("lat"),
            "lon": payload.get("lon"),
            "radius_km": payload.get("radius_km"),
            "reference_time_utc": reference_raw,
        }
        return json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def start(self, payload: dict[str, Any], config: WebServerConfig) -> SatelliteWarmupJob:
        self._cleanup()
        key = self._dedupe_key(payload)
        with self._lock:
            active_id = self._active_by_key.get(key)
            if active_id is not None:
                active = self._jobs.get(active_id)
                if active is not None and active.completed_at is None:
                    return active
            job = SatelliteWarmupJob(uuid.uuid4().hex, payload)
            self._jobs[job.job_id] = job
            self._active_by_key[key] = job.job_id
        thread = threading.Thread(
            target=self._run,
            args=(job, config, key),
            daemon=True,
            name=f"firesky-satellite-warm-{job.job_id[:8]}",
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Optional[SatelliteWarmupJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: SatelliteWarmupJob, config: WebServerConfig, key: str) -> None:
        acquired = self._remote_gate.acquire(
            timeout=SATELLITE_WARMUP_REMOTE_GATE_TIMEOUT_SECONDS
        )
        if not acquired:
            with job._lock:
                job.status = "failed"
                job.error = "等待卫星预热远程下载槽超时。"
                job.completed_at = time.monotonic()
        else:
            try:
                with job._lock:
                    job.status = "running"
                try:
                    handler = self._warmup_handler or handle_satellite_cache_warmup
                    result = handler(job.payload, config)
                except BaseException as exc:
                    with job._lock:
                        job.status = "failed"
                        job.error = str(exc)
                        job.completed_at = time.monotonic()
                else:
                    with job._lock:
                        job.status = "done"
                        job.result = result
                        job.completed_at = time.monotonic()
            finally:
                self._remote_gate.release()
        with self._lock:
            if self._active_by_key.get(key) == job.job_id:
                self._active_by_key.pop(key, None)

    def _cleanup(self) -> None:
        cutoff = time.monotonic() - 3600.0
        with self._lock:
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job.completed_at is not None and job.completed_at < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)
            stale_keys = [key for key, job_id in self._active_by_key.items() if job_id not in self._jobs]
            for key in stale_keys:
                self._active_by_key.pop(key, None)


SATELLITE_WARMUP_JOBS = SatelliteWarmupJobRegistry()


def require_satellite_warmup_access(config: WebServerConfig, token: Optional[str]) -> None:
    """Require an explicit configured admin token for network warming."""
    configured = config.satellite_warmup_token
    if not configured:
        raise ValueError("卫星云柱下载接口未启用。")
    if not token or not hmac.compare_digest(str(token), configured):
        raise ValueError("卫星云柱下载需要有效的管理员令牌。")


def create_web_server(host: str, port: int, config: WebServerConfig) -> ThreadingHTTPServer:
    """Create the legacy stdlib server used by older CLI/tests.

    The production entry point is the FastAPI app in ``firesky.api``. This
    adapter keeps the old local HTTP contract available for scripts that still
    call ``create_web_server`` directly.
    """
    return ThreadingHTTPServer((host, port), make_request_handler(config))


def make_request_handler(config: WebServerConfig):
    class ForecastRequestHandler(BaseHTTPRequestHandler):
        server_version = "firesky/0.1 burning-sunset-engine"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/":
                    self.send_spa_index()
                elif parsed.path in {"/feedback", "/legacy/feedback"}:
                    self.send_static("feedback.html", "text/html; charset=utf-8")
                elif parsed.path == "/api/health":
                    self.send_json({"status": "ok"})
                elif parsed.path == "/api/point":
                    params = urllib.parse.parse_qs(parsed.query)
                    lat = parse_coordinate(params, "lat", -90.0, 90.0)
                    lon = parse_coordinate(params, "lon", -180.0, 180.0)
                    timezone_name, elevation_m = fetch_point_metadata(lat, lon)
                    self.send_json({
                        "lat": lat,
                        "lon": lon,
                        "timezone_name": timezone_name,
                        "elevation_m": elevation_m,
                    })
                elif parsed.path == "/api/forecast/events":
                    params = urllib.parse.parse_qs(parsed.query)
                    job = FORECAST_JOBS.get(params.get("job_id", [""])[0])
                    if job is None:
                        raise FileNotFoundError("预测任务不存在或已过期。")
                    self.send_event_stream(job)
                elif parsed.path == "/api/satellite/cache/warmup/status":
                    params = urllib.parse.parse_qs(parsed.query)
                    require_satellite_warmup_access(
                        config,
                        self.headers.get("X-Firesky-Warmup-Token"),
                    )
                    job = SATELLITE_WARMUP_JOBS.get(params.get("job_id", [""])[0])
                    if job is None:
                        raise FileNotFoundError("卫星云柱下载任务不存在或已过期。")
                    self.send_json(job.snapshot())
                elif parsed.path.startswith("/reports/"):
                    self.send_report(parsed.path.removeprefix("/reports/"))
                else:
                    self.send_spa_index()
            except ValueError as exc:
                self.send_error_json(400, str(exc))
            except FileNotFoundError as exc:
                self.send_error_json(404, str(exc))
            except Exception as exc:
                self.send_error_json(502, str(exc))

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path.startswith("/api/forecast/") and parsed.path.endswith("/cancel"):
                    job_id = parsed.path.removeprefix("/api/forecast/").removesuffix("/cancel").strip("/")
                    if not FORECAST_JOBS.cancel(job_id):
                        raise FileNotFoundError("预测任务不存在或已结束。")
                    self.send_json({"job_id": job_id, "status": "cancelled"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_REQUEST_BYTES:
                    raise ValueError("请求内容为空或过大。")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if parsed.path == "/api/forecast/start":
                    job = FORECAST_JOBS.start(payload, config)
                    result = {"job_id": job.job_id}
                elif parsed.path == "/api/forecast":
                    result = run_forecast_payload(payload, config)
                elif parsed.path == "/api/forecast/nowcast/start":
                    job = FORECAST_JOBS.start(nowcast_payload(payload), config)
                    result = {"job_id": job.job_id, "forecast_mode": "nowcast"}
                elif parsed.path == "/api/forecast/nowcast":
                    result = run_forecast_payload(nowcast_payload(payload), config)
                elif parsed.path == "/api/forecast/national/start":
                    job = FORECAST_JOBS.start(
                        payload,
                        config,
                        run_and_cache_national_forecast_public_payload,
                        defer_result=True,
                        regional=True,
                    )
                    result = {"job_id": job.job_id, "scope": "regional"}
                elif parsed.path == "/api/forecast/national":
                    result = run_and_cache_national_forecast_public_payload(payload, config)
                elif parsed.path == "/api/v1/firesky/data/requirements":
                    result = handle_data_requirements(payload)
                elif parsed.path == "/api/v1/firesky/data/fetch":
                    result = handle_data_fetch(payload, config)
                elif parsed.path == "/api/v1/firesky/data/prune":
                    result = handle_data_prune(payload)
                elif parsed.path == "/api/cache/warmup":
                    result = handle_cache_warmup(payload, config)
                elif parsed.path == "/api/satellite/cache/warmup":
                    require_satellite_warmup_access(
                        config,
                        self.headers.get("X-Firesky-Warmup-Token"),
                    )
                    job = SATELLITE_WARMUP_JOBS.start(payload, config)
                    result = {"job_id": job.job_id, "status": "queued"}
                else:
                    self.send_error_json(404, "接口不存在。")
                    return
                self.send_json(result)
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_error_json(400, str(exc))
            except GfsInputUnavailableError as exc:
                self.send_json({"status": "unavailable", "error": str(exc)}, 503)
            except Exception as exc:
                self.send_error_json(500, str(exc))

        def send_static(self, name: str, content_type: str) -> None:
            target = STATIC_DIR / name
            if not target.is_file():
                self.send_error_json(404, "页面资源不存在。")
                return
            self.send_bytes(target.read_bytes(), content_type)

        def send_spa_index(self) -> None:
            index = BUILD_DIR / "index.html"
            if index.is_file():
                self.send_bytes(index.read_bytes(), "text/html; charset=utf-8")
            else:
                self.send_static("map.html", "text/html; charset=utf-8")

        def send_report(self, name: str) -> None:
            root = config.report_dir.resolve()
            target = (root / Path(urllib.parse.unquote(name))).resolve()
            if not target.is_file() or root not in target.parents:
                self.send_error_json(404, "报告不存在。")
                return
            content_type = mimetypes.guess_type(target.name)[0] or "text/html"
            self.send_bytes(target.read_bytes(), f"{content_type}; charset=utf-8")

        def send_json(self, payload: Any, status: int = 200) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            use_gzip = (
                len(content) >= 16 * 1024
                and "gzip" in self.headers.get("Accept-Encoding", "")
            )
            self.send_bytes(
                gzip.compress(content, compresslevel=3) if use_gzip else content,
                "application/json; charset=utf-8",
                status,
                "gzip" if use_gzip else None,
            )

        def send_error_json(self, status: int, message: str) -> None:
            self.send_json({"error": message}, status)

        def send_event_stream(self, job: ForecastJob) -> None:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                while True:
                    event = job.next_event(timeout=15.0)
                    if event is None:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    event_name = str(event.get("event") or "progress")
                    content = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(
                        f"event: {event_name}\ndata: {content}\n\n".encode("utf-8")
                    )
                    self.wfile.flush()
                    if event_name in {"done", "failed", "cancelled"}:
                        break
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

        def send_bytes(
            self,
            content: bytes,
            content_type: str,
            status: int = 200,
            content_encoding: Optional[str] = None,
        ) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                if content_encoding:
                    self.send_header("Content-Encoding", content_encoding)
                self.end_headers()
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

        def log_message(self, format: str, *args: object) -> None:
            return

    return ForecastRequestHandler


class DataFetchJob:
    """Background data-fetch job with progress tracking. Queried by status polling."""

    def __init__(self, job_id: str, sources: list[str]) -> None:
        self.job_id = job_id
        self.sources = list(sources)
        self.created_at = time.monotonic()
        self.completed_at: Optional[float] = None
        self.status = "running"
        self._lock = threading.Lock()
        self._per_source: dict[str, dict[str, Any]] = {}
        self.results: list[dict] = []
        self.errors: list[str] = []
        self.worker_pid: Optional[int] = None

    def set_worker_pid(self, worker_pid: int) -> None:
        with self._lock:
            self.worker_pid = worker_pid

    def update(self, source: str, completed: int, total: int, current: str) -> None:
        with self._lock:
            self._per_source[source] = {
                "completed": completed,
                "total": total,
                "current": current,
            }

    def finish(self, results: list[dict]) -> None:
        with self._lock:
            self.status = "done"
            self.completed_at = time.monotonic()
            self.results = [dict(r) for r in results]

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = "failed"
            self.completed_at = time.monotonic()
            self.errors.append(error)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            completed = sum(p["completed"] for p in self._per_source.values())
            total = sum(p["total"] for p in self._per_source.values())
            current = ""
            for p in self._per_source.values():
                if p["current"]:
                    current = p["current"]
                    break
            elapsed = (self.completed_at or time.monotonic()) - self.created_at
            return {
                "job_id": self.job_id,
                "status": self.status,
                "sources": list(self.sources),
                "completed": completed,
                "total": total,
                "current": current,
                "results": [dict(r) for r in self.results],
                "errors": list(self.errors),
                "execution": "process",
                "worker_pid": self.worker_pid,
                "elapsed_seconds": round(elapsed, 1),
            }


class DataFetchJobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, DataFetchJob] = {}

    def start(self, payload: dict) -> DataFetchJob:
        self._cleanup()
        sources = list(payload.get("sources", ["gfs", "ecmwf", "cams"]))
        job = DataFetchJob(uuid.uuid4().hex, sources)
        with self._lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(target=self._run, args=(job, payload), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Optional[DataFetchJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: DataFetchJob, payload: dict) -> None:
        context = mp.get_context("spawn")
        events = context.Queue()
        process = context.Process(
            target=_run_data_fetch_process,
            args=(payload, events),
            name=f"firesky-data-{job.job_id[:8]}",
        )
        try:
            process.start()
            job.set_worker_pid(process.pid or 0)
            terminal = False
            started_at = time.monotonic()
            last_activity_at = started_at
            while not terminal:
                try:
                    kind, value = events.get(timeout=0.5)
                except queue.Empty:
                    if not process.is_alive():
                        # A multiprocessing.Queue feeder can publish the
                        # worker's final message after the child has exited.
                        # Reap it first, then allow a bounded grace period for
                        # the terminal event to become visible.
                        process.join(timeout=2.0)
                        deadline = time.monotonic() + 1.0
                        while not terminal and time.monotonic() < deadline:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            try:
                                kind, value = events.get(
                                    timeout=min(0.1, remaining)
                                )
                            except queue.Empty:
                                continue
                            last_activity_at = time.monotonic()
                            if kind == "progress":
                                source, completed, total, current = value
                                job.update(source, completed, total, current)
                            elif kind == "done":
                                job.finish(value)
                                terminal = True
                            elif kind == "failed":
                                job.fail(str(value))
                                terminal = True
                        break
                    now = time.monotonic()
                    if now - started_at >= DATA_FETCH_MAX_RUNTIME_SECONDS:
                        job.fail("下载 worker 超过最大运行时间，已终止。")
                        terminal = True
                        break
                    if now - last_activity_at >= DATA_FETCH_STALL_TIMEOUT_SECONDS:
                        job.fail("下载 worker 长时间没有进度，已终止。")
                        terminal = True
                        break
                    continue
                last_activity_at = time.monotonic()
                if kind == "progress":
                    source, completed, total, current = value
                    job.update(source, completed, total, current)
                elif kind == "done":
                    job.finish(value)
                    terminal = True
                elif kind == "failed":
                    job.fail(str(value))
                    terminal = True
            process.join(timeout=2.0)
            if not terminal and job.status == "running":
                message = f"下载 worker 异常退出（exit code {process.exitcode}）。"
                job.fail(message)
        except BaseException as exc:
            job.fail(str(exc))
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
            events.close()

    def _cleanup(self) -> None:
        cutoff = time.monotonic() - 3600
        with self._lock:
            stale = [
                job_id
                for job_id, job in self._jobs.items()
                if job.completed_at is not None and job.completed_at < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)


DATA_FETCH_JOBS = DataFetchJobRegistry()


def _canonical_gfs_point_request(payload: dict[str, Any]) -> ForecastRequest:
    request = forecast_request_from_payload(payload)
    if request.gfs_cycle_override is None:
        request = replace(request, gfs_cycle_override=_point_model_cycle(payload))
    return request


def run_forecast_payload(
    payload: Any,
    config: WebServerConfig,
    progress_callback=None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("预测参数必须是 JSON 对象。")
    force_refresh = bool(payload.get("force_refresh", False))
    normalized_payload = {key: value for key, value in payload.items() if key != "force_refresh"}
    requested_workers = normalized_payload.get("workers", config.workers)
    if requested_workers is not None:
        normalized_payload["workers"] = int(bounded_float(
            requested_workers,
            "workers",
            1,
            8,
        ))
    target_date, phenomenon = _point_target_identity(normalized_payload)
    normalized_payload["target_date"] = target_date.isoformat()
    normalized_payload["phenomenon"] = phenomenon
    forecast_mode = str(normalized_payload.get("forecast_mode") or "model").lower()
    requested_model_source = str(
        normalized_payload.get("model_source")
        or ("satellite" if forecast_mode == "nowcast" else "gfs")
    ).lower()
    if forecast_mode == "model" and requested_model_source == "ec":
        normalized_payload["model_source"] = "ec"
        normalized_payload["forecast_mode"] = "model"
        normalized_payload["satellite_source"] = "off"
        normalized_payload.setdefault("_grid_region", "ec-basic")
        result = _run_ecmwf_point_payload(
            normalized_payload,
            config,
            force_refresh=force_refresh,
            progress_callback=progress_callback,
        )
        return _attach_cached_external_point_map(result, normalized_payload, config)
    if not str(normalized_payload.get("_grid_region") or "").strip():
        normalized_payload["_grid_region"] = grid_domain_key_for_point(
            GeoPoint(
                lat=bounded_float(normalized_payload.get("lat"), "纬度", -90.0, 90.0),
                lon=bounded_float(normalized_payload.get("lon"), "经度", -180.0, 180.0),
            )
        )
    normalized_payload["_aerosol_source"] = config.aerosol_source
    if forecast_mode == "model":
        normalized_payload["forecast_mode"] = "model"
        normalized_payload["satellite_source"] = "off"
        if str(normalized_payload.get("model_source") or "gfs").lower() != "gfs":
            raise ValueError(
                "当前原生预测仅支持 model_source=gfs 或 ec；"
                f"{ECMWF_FORECAST_CONTRACT_REASON}"
            )
    elif forecast_mode == "nowcast":
        requested_source = str(normalized_payload.get("model_source") or "").lower()
        if requested_source != "satellite":
            if requested_source in {"ec", "ecmwf"}:
                raise ValueError(
                    "nowcast 模式的 model_source 必须是 satellite；"
                    f"{ECMWF_FORECAST_CONTRACT_REASON}"
                )
            raise ValueError("nowcast 模式的 model_source 必须是 satellite。")
    else:
        raise ValueError("forecast_mode 必须是 model 或 nowcast。")
    point_grid_resolution = _canonical_gfs_point_grid_resolution(normalized_payload)
    if point_grid_resolution is not None:
        normalized_payload.update({
            "lat": point_grid_resolution["resolved_lat"],
            "lon": point_grid_resolution["resolved_lon"],
            # national_points_from_payload fills this from the GFS orography
            # prepass; retaining 0 here keeps the cache identity model-grid.
            "elevation_m": 0.0,
            "timezone_name": point_grid_resolution["resolved_timezone_name"],
            "_grid_region": point_grid_resolution["region"],
            "_point_grid_mode": POINT_GRID_RESOLUTION_MODE,
        })
    model_cycle_cache_key = (
        _point_model_cycle(normalized_payload).isoformat()
        if forecast_mode == "model"
        else None
    )
    cache_identity_payload = (
        {
            key: value
            for key, value in normalized_payload.items()
            if key != "name"
        }
        if point_grid_resolution is not None
        else normalized_payload
    )
    cache_key = json.dumps(
        {
            "payload": cache_identity_payload,
            "model_cycle": model_cycle_cache_key,
            "algorithm": FORECAST_ALGORITHM_VERSION,
            "workers": config.workers,
            "report_dir": str(config.report_dir),
            "aerosol_source": config.aerosol_source,
            "map_cache_dir": str(config.map_cache_dir),
            **_cams_backend_identity(),
            **rust_astronomy.backend_identity(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if progress_callback is not None:
        progress_callback({"stage": "cache", "percent": 2, "message": "检查预测缓存和相同请求"})
    def compute() -> dict[str, Any]:
        if forecast_mode != "nowcast" and not force_refresh:
            cached = read_point_forecast_cache(normalized_payload, config.point_cache_dir)
            if cached is not None:
                cache_request = _canonical_gfs_point_request(normalized_payload)
                resolved_cache_request = _point_request_for_grid_result(
                    cache_request,
                    normalized_payload,
                    cached,
                )
                cached = dict(cached)
                cached = _attach_point_grid_resolution(
                    cached, point_grid_resolution
                )
                if progress_callback is not None:
                    progress_callback({"stage": "cache", "percent": 99, "message": "命中单点预测持久缓存"})
                return cached
        request = forecast_request_from_payload(normalized_payload)
        if request.forecast_mode == "model" and request.model_source == "gfs":
            # The native observer field has one canonical 1000 km maximum. Keep
            # the internal request identity equal to the radius actually scored.
            request = _canonical_gfs_point_request(normalized_payload)
            # Pin the in-memory point core to the same target-event cycle used
            # by the national batch.
            # A public point request carries an observer coordinate and altitude.
            # Never reuse a generated regional-grid core here: an exact 0.25°
            # coordinate can still have a measured altitude that differs from
            # the model terrain used by the regional observer.
            if progress_callback is not None:
                progress_callback({
                    "stage": "unified_grid",
                    "percent": 4,
                    "message": "生成目标坐标全量云场核心",
                })
            point_compute_payload, exact_point = _compute_unified_point_grid(
                request, normalized_payload, config, progress_callback
            )
            point_compute_result = point_compute_payload.pop(
                "_in_memory_result", None
            )
            point_profile_source = point_compute_payload.pop(
                "_in_memory_profile_source", None
            )
            resolved_request = _point_request_for_grid_result(
                request,
                normalized_payload,
                exact_point,
            )
            if progress_callback is not None:
                progress_callback({"stage": "diagnostics", "percent": 70, "message": "统一质量计算完成，生成单点云场与大气剖面"})
            exact_point = _attach_unified_point_diagnostics(
                resolved_request,
                normalized_payload,
                config,
                exact_point,
                point_compute_payload=point_compute_payload,
                point_compute_result=point_compute_result,
                point_profile_source=point_profile_source,
                progress_callback=progress_callback,
            )
            exact_point = _attach_point_grid_resolution(
                exact_point, point_grid_resolution
            )
            return write_point_forecast_cache(
                normalized_payload,
                exact_point,
                config.point_cache_dir,
                status="exact-native-point",
            )
        result = (
            _run_forecast_payload_uncached(normalized_payload, config, progress_callback)
            if progress_callback is not None
            else _run_forecast_payload_uncached(normalized_payload, config)
        )
        if forecast_mode == "nowcast":
            # Calibrated HSD/GFS identities can change inside the wall-clock
            # bucket; nowcast responses are deliberately never persisted.
            return result
        return write_point_forecast_cache(normalized_payload, result, config.point_cache_dir)
    if forecast_mode == "nowcast" or force_refresh:
        result = compute()
        if forecast_mode != "nowcast":
            FORECAST_RESPONSE_CACHE.put(cache_key, config.forecast_cache_seconds, result)
    else:
        result = FORECAST_RESPONSE_CACHE.get_or_compute(
            cache_key,
            config.forecast_cache_seconds,
            compute,
        )
    if point_grid_resolution is not None:
        result = copy.deepcopy(result)
        result["observer_name"] = str(
            payload.get("name") or result.get("observer_name") or "观测点"
        )[:120]
        result = _attach_point_grid_resolution(result, point_grid_resolution)
    return result


ECMWF_POINT_RESPONSE_VERSION = "ecmwf-ifs-basic-point-response-v2"


def _ecmwf_point_response_path(
    cache_dir: Path,
    response_digest: str,
    target_date: date,
    phenomenon: str,
) -> Path:
    return (
        cache_dir
        / "point-responses"
        / target_date.isoformat()
        / phenomenon
        / f"{ECMWF_POINT_RESPONSE_VERSION}-{response_digest[:24]}.json.gz"
    )


def _ecmwf_point_response_identity(
    request: ForecastRequest,
    cycle_utc: datetime,
    config: WebServerConfig,
) -> dict[str, Any]:
    identity_payload: dict[str, Any] = {
        "version": ECMWF_POINT_RESPONSE_VERSION,
        "namespace": "ecmwf-ifs-basic",
        "model_source": "ec",
        "forecast_mode": "model",
        "contract": "public-open-data/basic",
        "target_date": request.target_date.isoformat(),
        "phenomenon": request.phenomenon,
        "observer": {
            "name": request.name,
            "lat": float(request.point.lat),
            "lon": float(request.point.lon),
            "elevation_m": float(request.point.elevation_m),
            "timezone_name": request.timezone_name,
        },
        "radius_km": float(request.radius_km),
        "compute_backend_requested": request.compute_backend,
        "compute_backend": "cpu" if request.compute_backend == "auto" else request.compute_backend,
        "cycle_utc": cycle_utc.astimezone(timezone.utc).isoformat(),
        "cams_cache_dir": str(Path(config.cams_cache_dir).resolve()),
        "report_dir": str(Path(config.report_dir).resolve()),
    }
    encoded = json.dumps(
        identity_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "key": f"{ECMWF_POINT_RESPONSE_VERSION}/{digest}",
        "digest": digest,
        "payload": identity_payload,
    }


def _read_ecmwf_point_response_cache(
    path: Path,
    identity_key: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    metadata = payload.get("response_cache") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        return None
    if (
        metadata.get("version") != ECMWF_POINT_RESPONSE_VERSION
        or metadata.get("namespace") != "ecmwf-ifs-basic"
        or metadata.get("identity_key") != identity_key
    ):
        return None
    return payload


def _write_ecmwf_point_response_cache(
    path: Path,
    payload: dict[str, Any],
    identity_key: str,
    *,
    identity_payload: dict[str, Any],
) -> dict[str, Any]:
    cached = copy.deepcopy(payload)
    cached["response_cache"] = {
        "status": "refresh",
        "version": ECMWF_POINT_RESPONSE_VERSION,
        "namespace": "ecmwf-ifs-basic",
        "identity_key": identity_key,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "identity_payload": copy.deepcopy(identity_payload),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".part",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with gzip.GzipFile(fileobj=handle, mode="wb", compresslevel=3) as stream:
                stream.write(
                    json.dumps(
                        cached,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                )
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return cached


def _ecmwf_point_cycle(
    request: ForecastRequest,
    payload: dict[str, Any],
    *,
    cache_dir: Path | str | None = None,
) -> datetime:
    override = payload.get("ec_cycle_override")
    if override is None:
        override = payload.get("_ec_cycle_override")
    if override is not None:
        if isinstance(override, datetime):
            if override.tzinfo is None or override.utcoffset() != timedelta(0):
                raise ValueError("ec_cycle_override must use UTC")
            return override.astimezone(timezone.utc).replace(microsecond=0)
        return parse_utc_iso_datetime(override, "ec_cycle_override").replace(
            microsecond=0
        )
    anchor_local = solar_event_time_local(
        request.target_date,
        request.point,
        request.timezone_name,
        request.phenomenon,
    )
    return choose_ecmwf_ifs_cycle_for_event(
        anchor_local.astimezone(timezone.utc),
        ecmwf_point_evaluation_times(request),
        cache_dir=cache_dir,
    ).replace(microsecond=0)


def _external_point_region_candidates(point: GeoPoint) -> list[str]:
    """Return named regional cache identities that can supply a point map."""
    candidates: list[tuple[float, str]] = []
    for code, bounds in REGION_BBOXES.items():
        west, east, north, south = (float(value) for value in bounds)
        if west <= point.lon <= east and south <= point.lat <= north:
            candidates.append(((east - west) * (north - south), str(code)))
    candidates.sort()
    # A point outside the named catalogue may still have an operational
    # east-asia cache. Keep the fallback last so the narrowest identity wins.
    if "east-asia" not in {code for _, code in candidates}:
        candidates.append((float("inf"), "east-asia"))
    return [code for _, code in candidates]


def _attach_cached_external_point_map(
    result: dict[str, Any],
    payload: dict[str, Any],
    config: WebServerConfig,
) -> dict[str, Any]:
    """Attach a real external-model map only from an existing regional cache.

    Point scoring remains independent. This helper never starts a regional
    computation and never uses the regional score; it only projects the
    already-computed point timeline onto a matching cached model grid.
    """
    source = str(payload.get("model_source") or "").strip().lower()
    if source != "ec":
        return result
    try:
        request = forecast_request_from_payload(payload)
        cycle = parse_utc_iso_datetime(
            result.get("cycle_time_utc"), "external point cycle_time_utc"
        )
    except (TypeError, ValueError, KeyError):
        result["map_token"] = None
        result["map_times"] = []
        result["map_capability"] = {
            "available": False,
            "model_source": source,
            "reason": "当前模型点位结果缺少有效周期，无法绑定地图时间轴。",
        }
        return result

    override_key = "_ec_cycle_override"
    # Building a regional cache identity enumerates the whole coverage grid;
    # when no regional result exists for this date/phenomenon at all, the
    # identity work cannot pay off, so probe the results directory first.
    results_dir = dated_directory(
        config.regional_cache_dir,
        request.target_date,
        storage_phenomenon(request.phenomenon),
        "results",
    )
    if not any(results_dir.glob("regional-forecast-v*.json.gz")):
        enriched = dict(result)
        enriched["map_token"] = None
        enriched["map_times"] = []
        enriched["map_capability"] = {
            "available": False,
            "model_source": source,
            "reason": (
                f"当前 {source.upper()} 单点结果没有同周期的区域云场缓存；"
                "完成对应区域计算后可查看地图时间轴。"
            ),
            "regional_cache_candidates": 0,
        }
        return enriched
    base_payload: dict[str, Any] = {
        "target_date": request.target_date.isoformat(),
        "phenomenon": request.phenomenon,
        "model_source": source,
        "forecast_mode": "model",
        "satellite_source": "off",
        # Regional caches use the canonical analysis radius.  The point
        # request radius only limits which cached cells are projected below;
        # including it in the identity would make a narrower point lookup miss
        # the existing 1000 km regional cache.
        "radius_km": float(NATIONAL_ANALYSIS_RADIUS_KM),
        "spacing_deg": 0.25,
        "gfs_resolution": "0p25",
        "_aerosol_source": str(config.aerosol_source).strip().lower(),
        override_key: cycle.isoformat(),
    }
    searched = 0
    for region in _external_point_region_candidates(request.point):
        candidate = {**base_payload, "regions": [region]}
        try:
            identity = national_forecast_cache_identity(candidate)
            cache_path = national_forecast_cache_path(
                config.regional_cache_dir,
                _national_forecast_cache_key_from_identity(identity),
                candidate["target_date"],
                candidate["phenomenon"],
            )
            if not cache_path.is_file():
                continue
            searched += 1
            regional = read_national_forecast_cache(
                candidate, config.regional_cache_dir, identity=identity
            )
            if not _regional_cache_has_2d_map_grid(request, regional):
                continue
            token, times = _regional_point_map_frames(
                request, result, regional, config.map_cache_dir
            )
            if token and times:
                enriched = dict(result)
                enriched["map_token"] = token
                enriched["map_times"] = times
                enriched["map_capability"] = {
                    "available": True,
                    "model_source": source,
                    "region": region,
                    "source": "existing-regional-cache",
                }
                return enriched
        except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError, KeyError):
            continue

    enriched = dict(result)
    enriched["map_token"] = None
    enriched["map_times"] = []
    enriched["map_capability"] = {
        "available": False,
        "model_source": source,
        "reason": (
            f"当前 {source.upper()} 单点结果没有同周期的区域云场缓存；"
            "完成对应区域计算后可查看地图时间轴。"
        ),
        "regional_cache_candidates": searched,
    }
    return enriched


def _run_ecmwf_point_payload(
    payload: dict[str, Any],
    config: WebServerConfig,
    *,
    force_refresh: bool = False,
    progress_callback=None,
) -> dict[str, Any]:
    """Run EC basic through its own durable response cache namespace."""

    request = forecast_request_from_payload(payload)
    cycle = _ecmwf_point_cycle(request, payload, cache_dir=config.ecmwf_cache_dir)
    response_identity = _ecmwf_point_response_identity(request, cycle, config)
    response_path = _ecmwf_point_response_path(
        Path(config.ecmwf_cache_dir),
        response_identity["digest"],
        request.target_date,
        request.phenomenon,
    )
    cache_key = f"ec-point:{response_identity['key']}"

    def compute() -> dict[str, Any]:
        if not force_refresh:
            cached = _read_ecmwf_point_response_cache(
                response_path,
                response_identity["key"],
            )
            if cached is not None:
                cached["response_cache"]["status"] = "hit"
                return cached
        point_forecast = run_ecmwf_basic_point_forecast(
            request,
            cache_dir=config.ecmwf_cache_dir,
            cams_cache_dir=config.cams_cache_dir,
            cycle_override=cycle,
            progress_callback=progress_callback,
        )
        report = point_forecast.report
        quality_payload = point_forecast.native_quality
        if quality_payload is None:
            raise RuntimeError(
                "EC native point forecast did not return its authoritative quality payload"
            )
        html_path = write_html_report(report, config.report_dir)
        report = replace(report, html_report_path=str(html_path))
        result = web_report_payload(
            report,
            request.point.elevation_m,
            quality_payload=quality_payload,
        )
        result["source_roles"] = {
            "primary": "ec_numerical_model",
            "auxiliary": "cams_aerosol",
        }
        result["confidence_score_semantics"] = point_forecast.provenance.get(
            "confidence_semantics",
            "diagnostic_input_coverage_not_statistical_confidence",
        )
        result["ecmwf_provenance"] = dict(point_forecast.provenance)
        dominant = point_forecast.provenance.get("dominant_burnable") or {}
        result["dominant_burnable_pressure_family"] = dominant.get("pressure_family")
        result["dominant_burnable_cloud_phase"] = dominant.get("cloud_phase")
        result["dominant_burnable_cloud_base_asl_mean_m"] = dominant.get("cloud_base_asl_mean_m")
        result["dominant_burnable_cloud_top_asl_mean_m"] = dominant.get("cloud_top_asl_mean_m")
        result["dominant_burnable_target_terrain_asl_mean_m"] = dominant.get("target_terrain_asl_mean_m")
        result["dominant_burnable_cloud_base_agl_mean_m"] = dominant.get("cloud_base_agl_mean_m")
        result["dominant_burnable_cloud_top_agl_mean_m"] = dominant.get("cloud_top_agl_mean_m")
        result["dominant_burnable_cloud_base_above_observer_mean_m"] = dominant.get("cloud_base_above_observer_mean_m")
        result["dominant_burnable_cloud_top_above_observer_mean_m"] = dominant.get("cloud_top_above_observer_mean_m")
        result["point_refinement"] = point_forecast.provenance.get("native_core", "national_assess_batch+build_national_frame_payloads")
        result["diagnostic_source"] = "ec-native-regional-cloud-and-solar-profile"
        result["geometry_window"] = None
        result["boundary_uncertainty"] = None
        result["dominant_burnable_cloud_genus_diagnosis"] = dominant.get("genus_diagnosis")
        result["cloud_genus_proxy"] = dominant.get("genus_proxy")
        result["cloud_phase_proxy"] = dominant.get("phase_proxy")
        result["duration_status"] = quality_payload.get("duration_status", "estimated")
        result["compute_backend"] = point_forecast.report.compute_backend
        result["compute_backend_requested"] = point_forecast.report.compute_backend_requested
        result["data_provenance"] = {
            "model_source": "ec",
            "contract": point_forecast.provenance["contract"],
            "cycle_utc": point_forecast.provenance["cycle_utc"],
            "files": list(point_forecast.provenance["files"]),
            "gfs_cache_used": False,
            "cams_cache_used": True,
            "observation_used": False,
            "confidence_semantics": point_forecast.provenance.get(
                "confidence_semantics",
                "diagnostic_input_coverage_not_statistical_confidence",
            ),
            "cams_files": list(point_forecast.provenance.get("cams_files", [])),
            "cams_valid_times_utc": list(
                point_forecast.provenance.get("cams_valid_times_utc", [])
            ),
        }
        cross_section_source = dict(point_forecast.provenance)
        cross_section_source["target_date"] = request.target_date.isoformat()
        cross_section_source["phenomenon"] = request.phenomenon
        if cross_section_source.get("layer_profile_path"):
            profile_key = f"ec:{response_identity['digest']}"
            cross_section_request = {
                "target_date": request.target_date.isoformat(),
                "phenomenon": request.phenomenon,
                "model_source": "ec",
                "forecast_mode": "model",
                "satellite_source": "off",
                "radius_km": float(NATIONAL_ANALYSIS_RADIUS_KM),
                "spacing_deg": 0.25,
                "gfs_resolution": "0p25",
                "regions": ["custom"],
                "elevation_m": float(request.point.elevation_m),
                "profile_key": profile_key,
            }
            cross_section_offset = (
                0.0
                if report.best_time_local is None
                else (
                    report.best_time_local - report.anchor_local
                ).total_seconds() / 60.0
            )
            result["cross_section"] = _ecmwf_cross_section_from_provenance(
                cross_section_source,
                {
                    "name": request.name,
                    "lat": float(request.point.lat),
                    "lon": float(request.point.lon),
                    "elevation_m": float(request.point.elevation_m),
                    "timezone_name": request.timezone_name,
                },
                cross_section_offset,
            )
            result["cross_section_request"] = cross_section_request
        result["map_token"] = None
        result["map_times"] = []
        result["html_report_url"] = (
            f"/reports/{urllib.parse.quote(str(html_path.relative_to(config.report_dir.resolve())), safe='/')}"
        )
        return _write_ecmwf_point_response_cache(
            response_path,
            result,
            response_identity["key"],
            identity_payload=response_identity["payload"],
        )

    if force_refresh:
        return compute()
    had_durable_response = (
        _read_ecmwf_point_response_cache(
            response_path,
            response_identity["key"],
        )
        is not None
    )
    result = FORECAST_RESPONSE_CACHE.get_or_compute(
        cache_key,
        config.forecast_cache_seconds,
        compute,
    )
    if had_durable_response:
        result = copy.deepcopy(result)
        metadata = result.get("response_cache")
        if isinstance(metadata, dict):
            metadata["status"] = "hit"
    return result


def _compute_unified_point_grid(
    request: ForecastRequest,
    payload: dict[str, Any],
    config: WebServerConfig,
    progress_callback=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    uses_model_grid = payload.get("_point_grid_mode") == POINT_GRID_RESOLUTION_MODE
    point = {
        "name": request.name,
        "lat": request.point.lat,
        "lon": request.point.lon,
        "timezone_name": request.timezone_name,
    }
    if uses_model_grid:
        # Match generated regional points: the native batch fills elevation from
        # the GFS orography prepass before deriving solar/event geometry.
        point["elevation_source"] = "model-grid"
    else:
        # Ordinary custom/observer points retain their exact supplied altitude.
        point["elevation_m"] = request.point.elevation_m
        point["elevation_source"] = "observer"
    local_payload = {
        "target_date": request.target_date.isoformat(),
        "phenomenon": request.phenomenon,
        "model_source": request.model_source,
        "forecast_mode": request.forecast_mode,
        "satellite_source": request.satellite_source,
        "radius_km": request.radius_km,
        "compute_backend": request.compute_backend,
        "gfs_resolution": request.gfs_resolution,
        "spacing_deg": 0.25,
        "points": [point],
        "_grid_region": str(payload.get("_grid_region") or grid_domain_key_for_point(request.point)),
        "_aerosol_source": payload.get("_aerosol_source", config.aerosol_source),
    }
    if payload.get("workers") is not None:
        local_payload["workers"] = payload["workers"]
    # A point request only needs its own candidate field and incoming-light
    # envelope. Expanding every corner of a named region here turns one point
    # into a continent-sized cold-cache download.
    grid_region = str(local_payload["_grid_region"])
    west, east, north, south = national_gfs_bbox(
        [point],
        light_path_padding_km=_national_gfs_source_radius_km(request.radius_km),
    )
    local_payload.update({
        "regions": ["custom"],
        "custom_bounds": {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
        },
        "custom_label": "单点统一评分局部网格",
        "custom_timezone_name": request.timezone_name,
    })
    if request.gfs_cycle_override is not None:
        local_payload["_gfs_cycle_override"] = request.gfs_cycle_override.isoformat()
    computed = run_national_forecast_payload(
        local_payload,
        config,
        progress_callback,
        include_point_core=True,
    )
    if (
        computed["point_count"] != 1
        or computed["success_count"] != 1
        or computed["error_count"] != 0
        or not isinstance(computed["points"], list)
        or len(computed["points"]) != 1
    ):
        raise RuntimeError("单点统一评分必须只返回一个成功观测点。")
    computed_point = computed["points"][0]
    identity_values = (
        computed_point.get("lat") if isinstance(computed_point, dict) else None,
        computed_point.get("lon") if isinstance(computed_point, dict) else None,
        (
            computed_point.get("observer_elevation_asl_m")
            if isinstance(computed_point, dict)
            else None
        ),
    )
    if (
        not isinstance(computed_point, dict)
        or any(
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
            for value in identity_values
        )
        or float(identity_values[0]) != float(request.point.lat)
        or float(identity_values[1]) != float(request.point.lon)
        or (
            not uses_model_grid
            and float(identity_values[2]) != float(request.point.elevation_m)
        )
    ):
        raise RuntimeError("单点统一评分返回了不同坐标或海拔的观测点。")
    core = computed.pop("_point_core", None)
    if core is None:
        raise RuntimeError("单点统一评分没有生成当前内存 point core。")
    materialize_request = _point_request_for_grid_result(
        request,
        payload,
        computed_point,
    )
    unified = materialize_national_point_core(materialize_request, core)
    algorithm = computed.get("algorithm")
    if not isinstance(algorithm, str) or not algorithm.strip():
        raise RuntimeError("单点统一评分没有返回权威算法合同。")
    profile_source = computed.get("_profile_source")
    # Point responses only need the durable profile manifest. Keeping the
    # full one-point regional result in JSON.gz just to read it back during
    # diagnostics adds avoidable serialization and validation work.
    if isinstance(profile_source, dict) and profile_source.get("layer_profile_path"):
        write_national_profile_manifest(
            config.regional_cache_dir,
            national_forecast_cache_key(local_payload),
            str(computed["target_date"]),
            str(computed["phenomenon"]),
            profile_source,
        )
    local_payload["_in_memory_result"] = computed
    if isinstance(profile_source, dict):
        local_payload["_in_memory_profile_source"] = copy.deepcopy(profile_source)
    return local_payload, unified


def _attach_unified_point_diagnostics(
    request: ForecastRequest,
    payload: dict[str, Any],
    config: WebServerConfig,
    unified: dict[str, Any],
    *,
    point_compute_payload: dict[str, Any] | None,
    point_compute_result: dict[str, Any] | None = None,
    point_profile_source: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    """Attach cloud/path diagnostics without allowing them to redefine quality."""
    result = copy.deepcopy(unified)
    if point_profile_source is None and isinstance(point_compute_result, dict):
        candidate_profile_source = point_compute_result.get("_profile_source")
        if isinstance(candidate_profile_source, dict):
            point_profile_source = candidate_profile_source

    anchor_local = parse_zoned_iso_datetime(
        unified["anchor_local"], "unified.anchor_local"
    )
    try:
        unified_cycle = parse_utc_iso_datetime(
            unified["cycle_time_utc"], "unified.cycle_time_utc"
        )
    except (KeyError, TypeError, ValueError):
        unified_cycle = None
    best_time_local = unified["best_time_local"]
    offset_minutes = (
        0.0
        if best_time_local is None
        else (
            parse_zoned_iso_datetime(best_time_local, "unified.best_time_local")
            - anchor_local
        ).total_seconds()
        / 60.0
    )

    candidates: list[dict[str, Any]] = []
    requested_region = str(payload.get("_diagnostic_region") or "").strip().lower()
    region = (
        requested_region
        if requested_region in REGION_BBOXES
        and bbox_contains(
            REGION_BBOXES[requested_region],
            (request.point.lon, request.point.lon, request.point.lat, request.point.lat),
        )
        else region_for_point(request.point)
    )
    if region is not None:
        candidates.append({
            "target_date": request.target_date.isoformat(),
            "phenomenon": request.phenomenon,
            "model_source": request.model_source,
            "forecast_mode": request.forecast_mode,
            "satellite_source": request.satellite_source,
            # Regional refreshes use the canonical national analysis radius.
            # The point's radius only controls which cells are projected below.
            "radius_km": NATIONAL_ANALYSIS_RADIUS_KM,
            "spacing_deg": 0.25,
            "gfs_resolution": request.gfs_resolution,
            "_aerosol_source": config.aerosol_source,
            "_compute_region_override": region,
            "regions": [region],
            **(
                {"_gfs_cycle_override": unified_cycle.isoformat()}
                if unified_cycle is not None
                else {}
            ),
        })
    if point_compute_payload is not None and point_compute_result is None:
        candidates.append(point_compute_payload)

    profile_payload = None
    profile_source = point_profile_source
    map_source_result = None
    map_token = ""
    map_times: list[str] = []
    for candidate in candidates:
        try:
            candidate_result = read_national_forecast_cache(
                candidate, config.regional_cache_dir
            )
            candidate_cycle = parse_utc_iso_datetime(
                candidate_result["cycle_time_utc"],
                "regional diagnostic cycle_time_utc",
            )
            if unified_cycle is None or candidate_cycle != unified_cycle:
                continue
            candidate_key = national_forecast_cache_key(candidate)
            if not national_profile_manifest_path(
                config.regional_cache_dir,
                candidate_key,
                str(candidate["target_date"]),
                str(candidate.get("phenomenon") or "dusk"),
            ).is_file():
                continue
            if profile_payload is None:
                profile_payload = candidate
                profile_source = None
            if map_source_result is None and _regional_cache_has_2d_map_grid(
                request, candidate_result
            ):
                map_source_result = candidate_result
                break
        except (FileNotFoundError, OSError, ValueError, RuntimeError):
            continue
    if profile_payload is None:
        if point_compute_payload is not None and profile_source is not None:
            profile_payload = point_compute_payload
        else:
            local_payload, _ = _compute_unified_point_grid(request, payload, config, None)
            profile_payload = local_payload
    if map_source_result is not None:
        # A real regional grid is the only valid map source.  Let contract
        # errors remove the map only; the exact point score and pressure
        # profile remain usable when a regional diagnostic is incomplete.
        try:
            map_token, map_times = _regional_point_map_frames(
                request, unified, map_source_result, config.map_cache_dir
            )
        except (OSError, RuntimeError, TypeError, ValueError, KeyError):
            map_token, map_times = "", []
    result["map_token"] = map_token
    result["map_times"] = map_times
    cross_section_request = _cross_section_request_from_payload(
        profile_payload,
        elevation_m=request.point.elevation_m,
    )
    result["cross_section"] = (
        build_national_cross_section(
            profile_source,
            {
                "name": "剖面选点",
                "lat": request.point.lat,
                "lon": request.point.lon,
                "elevation_m": request.point.elevation_m,
                "timezone_name": request.timezone_name,
            },
            offset_minutes,
        )
        if profile_source is not None
        else read_national_cross_section(
            cross_section_request,
            config.regional_cache_dir,
            request.point.lat,
            request.point.lon,
            offset_minutes,
            timezone_name=request.timezone_name,
        )
    )
    result["cross_section_request"] = cross_section_request
    result["diagnostic_source"] = "unified-regional-cloud-and-solar-profile"
    return result


def _cross_section_request_from_payload(
    payload: dict[str, Any], *, elevation_m: float | None = None
) -> dict[str, Any]:
    """Build the public profile request from the scoring cache identity.

    Point scoring may retain a wider request radius for cloud selection, while
    the profile endpoint has one canonical 1000 km analysis domain. Keep the
    source cache key unchanged, but never expose the wider radius to the UI.
    """
    request = {
        key: payload[key]
        for key in (
            "target_date",
            "phenomenon",
            "model_source",
            "forecast_mode",
            "satellite_source",
            "radius_km",
            "spacing_deg",
            "gfs_resolution",
            "_aerosol_source",
            "_compute_region_override",
            "regions",
            "custom_bounds",
            "custom_label",
            "custom_timezone_name",
        )
        if key in payload
    }
    request["radius_km"] = float(NATIONAL_ANALYSIS_RADIUS_KM)
    if elevation_m is not None:
        request["elevation_m"] = float(elevation_m)
    request["profile_key"] = national_forecast_cache_key(payload)
    return request


def _regional_point_map_frames(
    request: ForecastRequest,
    unified: dict[str, Any],
    regional: dict[str, Any],
    cache_dir: str | Path,
) -> tuple[str, list[str]]:
    try:
        point_count, frames, decoded_frames = validate_current_national_frame_data(
            regional.get("frame_data")
        )
    except ValueError as exc:
        raise RuntimeError(f"区域缓存不符合 current national frame 合同：{exc}") from exc
    points = regional.get("points")
    if not isinstance(points, list) or len(points) != point_count:
        raise RuntimeError("区域缓存 points 与 current national frame point_count 不一致。")

    selected: list[tuple[dict[str, Any], int, float, float]] = []
    seen_frame_indices: set[int] = set()
    for point_index, point in enumerate(points):
        if not isinstance(point, dict):
            raise RuntimeError(f"区域缓存第 {point_index} 个 point 不是对象。")
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"区域缓存第 {point_index} 个 point 坐标无效。") from exc
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90.0 <= lat <= 90.0
            or not -180.0 <= lon <= 180.0
        ):
            raise RuntimeError(f"区域缓存第 {point_index} 个 point 坐标无效。")
        frame_index = point.get("frame_index")
        if (
            not isinstance(frame_index, int)
            or isinstance(frame_index, bool)
            or not 0 <= frame_index < point_count
        ):
            raise RuntimeError(f"区域缓存第 {point_index} 个 point frame_index 无效。")
        if frame_index in seen_frame_indices:
            raise RuntimeError("区域缓存 point frame_index 存在重复槽位。")
        seen_frame_indices.add(frame_index)
        target = GeoPoint(lat, lon)
        distance = haversine_km(
            request.point,
            target,
        )
        if distance <= request.radius_km:
            selected.append((
                point,
                frame_index,
                distance,
                initial_bearing_deg(
                    request.point,
                    target,
                ),
            ))
    if seen_frame_indices != set(range(point_count)):
        raise RuntimeError("区域缓存 point frame_index 未完整覆盖 current national frame 槽位。")
    if not selected:
        raise RuntimeError("区域缓存没有位于单点分析半径内的格点。")

    try:
        anchor = datetime.fromisoformat(str(unified["anchor_local"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("统一点位结果缺少有效 anchor_local。") from exc
    if anchor.tzinfo is None:
        raise RuntimeError("统一点位 anchor_local 必须包含时区。")
    canonical_steps = unified.get("steps")
    if not isinstance(canonical_steps, list) or not canonical_steps:
        raise RuntimeError("区域缓存缺少地图帧所需的逐时质量诊断。")
    if len(canonical_steps) != len(frames):
        raise RuntimeError("统一点位质量时次与区域帧数量不一致。")
    required_step_fields = {
        "valid_time_local",
        "valid",
        "physically_burnable",
        *REQUIRED_QUALITY_FLOAT_FIELDS,
    }
    for step_index, step in enumerate(canonical_steps):
        if not isinstance(step, dict) or not required_step_fields <= set(step):
            missing = sorted(required_step_fields - set(step) if isinstance(step, dict) else required_step_fields)
            raise RuntimeError(
                f"区域缓存第 {step_index} 个质量时次缺少字段：{missing}。"
            )
        if not isinstance(step["physically_burnable"], bool):
            raise RuntimeError(
                f"区域缓存第 {step_index} 个质量时次 physically_burnable 无效。"
            )
        if not isinstance(step["valid"], bool):
            raise RuntimeError(
                f"区域缓存第 {step_index} 个质量时次 valid 无效。"
            )
        for field in REQUIRED_QUALITY_FLOAT_FIELDS:
            value = step[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                raise RuntimeError(
                    f"区域缓存第 {step_index} 个质量时次 {field} 无效。"
                )
    output_frames: list[dict[str, Any]] = []

    for frame_index, (encoded_frame, decoded, canonical_step) in enumerate(
        zip(frames, decoded_frames, canonical_steps)
    ):
        offset = float(encoded_frame["offset_minutes"])
        valid_local = anchor + timedelta(minutes=offset)
        try:
            canonical_time = datetime.fromisoformat(str(canonical_step["valid_time_local"]))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"区域缓存第 {frame_index} 个质量时次 valid_time_local 无效。"
            ) from exc
        if canonical_time.tzinfo is None or abs(
            (canonical_time - valid_local).total_seconds()
        ) > 1.0:
            raise RuntimeError(
                f"区域帧 {frame_index} 与统一点位质量时次未对齐。"
            )
        if point_count == 1 and bool(decoded["valid"][0]) != canonical_step["valid"]:
            raise RuntimeError(
                f"区域帧 {frame_index} 与统一点位 valid 标记未对齐。"
            )
        valid_utc = valid_local.astimezone(timezone.utc)
        solar = solar_snapshot(request.point, valid_utc, request.timezone_name)
        cells = []
        if canonical_step["valid"]:
            for point, index, distance, bearing in selected:
                if decoded["valid"][index] == 0:
                    continue
                burn = decoded["burn_score"][index] / 255.0
                cells.append({
                    "lat": round(float(point["lat"]), 4),
                    "lon": round(float(point["lon"]), 4),
                    "distance_km": round(distance, 1),
                    "bearing_deg": canonical_map_bearing_deg(bearing),
                    "high_cloud": round(decoded["high_cloud"][index] / 255.0, 3),
                    "mid_cloud": round(decoded["mid_cloud"][index] / 255.0, 3),
                    "low_cloud": round(decoded["low_cloud"][index] / 255.0, 3),
                    "cloud_evidence": round(decoded["cloud_evidence"][index] / 255.0, 3),
                    "deep_cloud_risk": round(decoded["convective_risk"][index] / 255.0, 3),
                    "burn_score": round(burn, 3),
                    "illumination_score": round(decoded["illumination_score"][index] / 255.0, 3),
                    "path_clearance_score": round(decoded["path_clearance_score"][index] / 255.0, 3),
                    "blocking_risk": round(decoded["blocking_risk"][index] / 255.0, 3),
                })
        if not cells and canonical_step["valid"]:
            raise RuntimeError(
                f"区域帧 {frame_index} 在单点分析半径内没有有效格点。"
            )
        if not canonical_step["valid"]:
            continue
        output_frames.append({
            "valid_time_local": valid_local.isoformat(),
            "valid": canonical_step["valid"],
            "solar_elevation_deg": solar.elevation_deg,
            "solar_azimuth_deg": solar.azimuth_deg,
            "instant_quality_index": float(canonical_step["instant_quality_index"]),
            "physically_burnable": canonical_step["physically_burnable"],
            "burnable_cloud_coverage": float(
                canonical_step["burnable_cloud_coverage"]
            ),
            "forward_view_cloud_field_amount": float(
                canonical_step["forward_view_cloud_field_amount"]
            ),
            "forward_burnable_cloud_field_amount": float(
                canonical_step["forward_burnable_cloud_field_amount"]
            ),
            "forward_burnable_cloud_load": float(
                canonical_step["forward_burnable_cloud_load"]
            ),
            "forward_burnable_cloud_material_load": float(
                canonical_step["forward_burnable_cloud_material_load"]
            ),
            "forward_burnable_cloud_incoming_aerosol_load": float(
                canonical_step["forward_burnable_cloud_incoming_aerosol_load"]
            ),
            "solar_incoming_block_risk": float(
                canonical_step["solar_incoming_block_risk"]
            ),
            "forward_burnable_cloud_ratio": float(
                canonical_step["forward_burnable_cloud_ratio"]
            ),
            "observer": {"lat": request.point.lat, "lon": request.point.lon},
            "cells": cells,
        })
    if not output_frames:
        return "", []
    return write_map_frame_payloads(output_frames, request.point, cache_dir)


def _regional_cache_has_2d_map_grid(
    request: ForecastRequest,
    regional: dict[str, Any],
) -> bool:
    points = regional.get("points")
    if not isinstance(points, list):
        return False
    coordinates: set[tuple[float, float]] = set()
    for point in points:
        if not isinstance(point, dict):
            continue
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if haversine_km(request.point, GeoPoint(lat, lon)) <= request.radius_km:
            coordinates.add((round(lat, 4), round(lon, 4)))

    # Two unique latitudes and longitudes alone can describe an L-shaped
    # three-cell sample. Require all four coordinate combinations so the map
    # is backed by an actual 2x2 field rather than a sparse point set.
    latitudes = sorted({lat for lat, _ in coordinates})
    longitudes = sorted({lon for _, lon in coordinates})
    for lat_index, first_lat in enumerate(latitudes[:-1]):
        for second_lat in latitudes[lat_index + 1:]:
            for lon_index, first_lon in enumerate(longitudes[:-1]):
                for second_lon in longitudes[lon_index + 1:]:
                    if all(
                        (lat, lon) in coordinates
                        for lat in (first_lat, second_lat)
                        for lon in (first_lon, second_lon)
                    ):
                        return True
    return False


def region_for_point(point: GeoPoint) -> Optional[str]:
    """Return the most specific known region (from ``REGION_BBOXES``) that contains
    *point*, or ``None`` when the point lies outside every region.

    When several regions overlap (e.g. a country bbox inside a continent bbox) the
    smallest one wins, so a single point maps to the tightest download region the
    user actually prefetched on the data-assets page.
    """
    best_code: Optional[str] = None
    best_area: Optional[float] = None
    for code, bbox in REGION_BBOXES.items():
        west, east, north, south = bbox
        # bbox_contains expects two (left, right, top, bottom) boxes; a point is a
        # zero-size box centred on itself.
        if not bbox_contains(bbox, (point.lon, point.lon, point.lat, point.lat)):
            continue
        area = (east - west) * (north - south)
        if best_area is None or area < best_area:
            best_area = area
            best_code = code
    return best_code


def grid_domain_key_for_point(point: GeoPoint) -> str:
    """Return the named model/aerosol domain used for a point cache.

    China is nested inside the broader East-Asia box. Choosing the smallest
    named region keeps a point forecast on the same GFS/CAMS footprint as the
    China regional product instead of silently mixing overlapping domains.
    """
    region = region_for_point(point)
    if region:
        return region
    if bbox_contains(
        REGION_BBOXES["east-asia"],
        (point.lon, point.lon, point.lat, point.lat),
    ):
        return "east-asia"
    return "custom"


def _nearest_production_grid_coordinate(
    value: float,
    lower: float,
    upper: float,
    spacing: float,
) -> float:
    """Resolve a coordinate to the production grid; exact ties choose lower."""
    position = (value - lower) / spacing
    lower_index = math.floor(position)
    fraction = position - lower_index
    if fraction > 0.5 and not math.isclose(
        fraction, 0.5, rel_tol=0.0, abs_tol=1e-9
    ):
        lower_index += 1
    maximum_index = round((upper - lower) / spacing)
    index = min(max(lower_index, 0), maximum_index)
    return round(lower + index * spacing, 4)


def _canonical_gfs_point_grid_resolution(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Describe the 0.25-degree named-region identity for eligible point calls.

    The public China regional product is the only named production grid with a
    strict 0.25-degree contract today. Other named domains keep exact point
    semantics until they publish the same production-grid contract.
    """
    forecast_mode = str(payload.get("forecast_mode") or "model").strip().lower()
    model_source = str(payload.get("model_source") or "gfs").strip().lower()
    resolution = str(payload.get("gfs_resolution") or "0p25").strip().lower()
    if forecast_mode != "model" or model_source != "gfs" or resolution != "0p25":
        return None

    # Keep the existing point request contract: the original observer metadata
    # must still be supplied even though the canonical model-grid path replaces
    # its elevation and timezone with model values.
    if "elevation_m" not in payload:
        raise ValueError("缺少观测者海拔 elevation_m。")
    bounded_float(payload["elevation_m"], "海拔", -500.0, 9000.0)
    timezone_name = str(payload.get("timezone_name") or "").strip()
    if not timezone_name:
        raise ValueError("缺少坐标对应的时区。")
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("timezone_name 必须是有效 IANA 时区。") from exc

    lat = bounded_float(payload.get("lat"), "纬度", -90.0, 90.0)
    lon = bounded_float(payload.get("lon"), "经度", -180.0, 180.0)
    requested_region = str(payload.get("_grid_region") or "").strip().lower()
    requested_regions = payload.get("regions")
    if isinstance(requested_regions, list) and any(
        str(item).strip().lower() == "custom" for item in requested_regions
    ):
        return None
    if requested_region.startswith("custom") or requested_region.startswith("bbox:"):
        return None
    coordinate_region = region_for_point(GeoPoint(lat=lat, lon=lon))
    if (
        requested_region in REGION_BBOXES
        and not bbox_contains(
            REGION_BBOXES[requested_region], (lon, lon, lat, lat)
        )
    ):
        return None
    # A broad internal hint such as east-asia must not override the more
    # specific China production grid selected by the actual coordinate.
    region = coordinate_region
    # At present only the China product is a strict 0.25-degree production grid.
    if region != "china":
        return None
    west, east, north, south = REGION_BBOXES[region]
    if not bbox_contains(REGION_BBOXES[region], (lon, lon, lat, lat)):
        return None
    spacing = CHINA_PRODUCTION_SPACING_DEG
    resolved_lat = _nearest_production_grid_coordinate(
        lat, south, north, spacing
    )
    resolved_lon = _nearest_production_grid_coordinate(
        lon, west, east, spacing
    )
    resolved_timezone = str(
        REGIONAL_FORECAST_AREAS[region].get("timezone_name")
        or point_timezone(resolved_lat, resolved_lon)
    )
    return {
        "mode": POINT_GRID_RESOLUTION_MODE,
        "region": region,
        "grid_spacing_deg": spacing,
        "requested_lat": lat,
        "requested_lon": lon,
        "resolved_lat": resolved_lat,
        "resolved_lon": resolved_lon,
        "resolved_timezone_name": resolved_timezone,
    }


def _point_request_for_grid_result(
    request: ForecastRequest,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> ForecastRequest:
    """Use the model-grid observer identity after the national prepass."""
    if payload.get("_point_grid_mode") != POINT_GRID_RESOLUTION_MODE:
        return request
    try:
        elevation = bounded_float(
            result["observer_elevation_asl_m"],
            "模式地形海拔",
            -500.0,
            9000.0,
        )
    except (KeyError, ValueError) as exc:
        raise RuntimeError("单点统一评分缺少有效的 model-grid 地形海拔。") from exc
    return replace(
        request,
        point=GeoPoint(
            lat=request.point.lat,
            lon=request.point.lon,
            elevation_m=elevation,
        ),
    )


def _attach_point_grid_resolution(
    result: dict[str, Any],
    resolution: dict[str, Any] | None,
) -> dict[str, Any]:
    if resolution is None:
        return result
    presented = copy.deepcopy(result)
    metadata = dict(resolution)
    elevation = presented.get("observer_elevation_asl_m")
    if (
        not isinstance(elevation, bool)
        and isinstance(elevation, Real)
        and math.isfinite(float(elevation))
    ):
        metadata["resolved_elevation_asl_m"] = float(elevation)
    presented["point_grid_resolution"] = metadata
    return presented


def _point_cache_identity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize eligible point-cache identities to the model-grid cell."""
    resolution = _canonical_gfs_point_grid_resolution(payload)
    if resolution is None:
        return payload
    normalized = dict(payload)
    normalized.update({
        "lat": resolution["resolved_lat"],
        "lon": resolution["resolved_lon"],
        "elevation_m": 0.0,
        "timezone_name": resolution["resolved_timezone_name"],
        "_grid_region": resolution["region"],
        "_point_grid_mode": POINT_GRID_RESOLUTION_MODE,
    })
    return normalized


def _national_compute_domain(
    payload: dict[str, Any],
    points: list[dict[str, Any]],
    candidate_radius_km: float | None = None,
) -> tuple[str | None, tuple[float, float, float, float], str]:
    """Resolve the one model/CAMS footprint used by execution and cache identity."""
    if candidate_radius_km is None:
        candidate_radius_km = _analysis_radius_from_payload(payload)
    source_radius_km = _national_gfs_source_radius_km(candidate_radius_km)
    requested_regions = payload.get("regions") or ["east-asia"]
    forced_region = str(payload.get("_compute_region_override") or "").strip().lower()
    model_source = str(payload.get("model_source") or "gfs").strip().lower()
    explicit_points = _national_payload_uses_explicit_points(payload)
    if forced_region:
        if forced_region == "custom" or forced_region not in REGION_BBOXES:
            raise ValueError(f"未知计算区域 {forced_region}。")
        compute_region: str | None = forced_region
    else:
        compute_region = None
        if isinstance(requested_regions, list) and len(requested_regions) == 1:
            candidate = str(requested_regions[0]).strip().lower()
            if candidate != "custom" and candidate in REGION_BBOXES:
                compute_region = candidate
    # The catalog includes Sansha south of the mainland China grid.  A fixed
    # regional bbox would silently clamp that observer to its northern edge;
    # derive a point-covered operational bbox whenever the requested points do
    # not fit the named region.  The derived bbox also drives the CAMS area.
    if compute_region is not None and any(
        not bbox_contains(
            REGION_BBOXES[compute_region],
            (float(point["lon"]), float(point["lon"]), float(point["lat"]), float(point["lat"])),
        )
        for point in points
    ):
        compute_region = None
    if model_source == "ec" and explicit_points and not forced_region:
        # Explicit EC points need only the coordinate envelope. Expanding a
        # broad named region first can make its far corner cross the date line
        # even when every requested point is safely inland; _external_prepared_bbox
        # adds the complete EC physical source radius exactly once afterwards.
        longitudes = [float(point["lon"]) for point in points]
        latitudes = [float(point["lat"]) for point in points]
        if max(longitudes) - min(longitudes) >= 180.0:
            raise ValueError("EC 显式点共享范围跨越日期线，不能用单一规则网格表示")
        compute_region = None
        model_bbox = (
            min(longitudes),
            max(longitudes),
            max(latitudes),
            min(latitudes),
        )
    else:
        if compute_region == "china" and model_source == "gfs":
            # A named GFS region must carry the same full source domain as an
            # explicit point set. Expand its four corners so candidates on a
            # regional edge still have the complete incoming solar path.
            west, east, north, south = REGION_BBOXES[compute_region]
            model_bbox = national_gfs_bbox([
                {"lat": south, "lon": west},
                {"lat": south, "lon": east},
                {"lat": north, "lon": west},
                {"lat": north, "lon": east},
            ], light_path_padding_km=source_radius_km)
        elif compute_region is None and model_source == "gfs":
            model_bbox = national_gfs_bbox(
                points,
                light_path_padding_km=source_radius_km,
            )
        else:
            model_bbox = (
                REGION_BBOXES[compute_region]
                if compute_region is not None
                else national_gfs_bbox(points)
            )
    canonical_bbox = tuple(float(value) for value in model_bbox)
    domain_key = compute_region or (
        "bbox:" + hashlib.sha256(
            json.dumps(
                canonical_bbox, separators=(",", ":")
            ).encode("ascii")
        ).hexdigest()[:16]
    )
    return compute_region, canonical_bbox, domain_key


def _analysis_radius_from_payload(payload: dict[str, Any]) -> float:
    """Normalize the candidate cloud-field radius before resolving source domains."""
    forecast_mode = str(payload.get("forecast_mode") or "model").lower()
    radius_default = 900.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    radius_max = 1000.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    return bounded_float(
        payload.get("radius_km", radius_default),
        "搜索半径",
        100.0,
        radius_max,
    )


def _run_forecast_payload_uncached(
    payload: dict[str, Any],
    config: WebServerConfig,
    progress_callback=None,
) -> dict[str, Any]:
    if progress_callback is not None:
        progress_callback({"stage": "validate", "percent": 3, "message": "校验预测参数"})
    request = forecast_request_from_payload(payload)
    if request.forecast_mode == "nowcast":
        anchor_utc = solar_event_time_local(
            request.target_date,
            request.point,
            request.timezone_name,
            request.phenomenon,
        ).astimezone(timezone.utc)
        # Freeze the observation cutoff before any remote preparation. The
        # same request and cycle are then used by warmup and cache-only scoring.
        reference_utc = resolve_reference_time(request, anchor_utc)
        cycle = resolve_nowcast_gfs_cycle(
            replace(request, reference_time_utc=reference_utc),
            anchor_utc,
            reference_utc=reference_utc,
        )
        request = replace(
            request,
            reference_time_utc=reference_utc,
            gfs_cycle_override=cycle,
        )
        event_offset_minutes = (reference_utc - anchor_utc).total_seconds() / 60.0
        if (
            reference_utc <= anchor_utc
            and not nowcast_event_uses_satellite_scoring(event_offset_minutes)
        ):
            raise ValueError(
                "Satellite nowcast is limited to targets within 4 hours; use a model forecast outside that window."
            )
    if progress_callback is not None:
        progress_callback({"stage": "service", "percent": 4, "message": "初始化预测服务"})

    # Reuse the per-region download cache when the requested point falls inside a
    # known region: tag the GFS client with that region and use the region's exact
    # bbox / CAMS area so files land under .cache/<src>/<date>/<HHZ>/<region>/ and
    # hit the prefetch done from the data-assets page. Points outside every region
    # keep the previous East-Asia operational coverage.
    point_region = region_for_point(request.point)
    if point_region is not None:
        region_bbox = REGION_BBOXES[point_region]
        gfs_fixed_bbox = region_bbox
        gfs_region = point_region
        cams_fixed_area = _cams_area_from_bbox(region_bbox)
    else:
        gfs_fixed_bbox = EAST_ASIA_OPERATIONAL_BBOX
        gfs_region = None
        cams_fixed_area = national_cams_area(EAST_ASIA_OPERATIONAL_BBOX)

    if request.forecast_mode == "nowcast":
        # Nowcast scoring is cache-only, so prepare both the same-source HSD
        # motion pair and same-cycle GFS prior subsets before scoring.
        hsd_warmup: dict[str, Any] = {"source": "hsd", "warmed": 0, "errors": []}
        prior_warmup: dict[str, Any] = {"warmed": [], "errors": []}
        if progress_callback is not None:
            progress_callback({
                "stage": "satellite",
                "percent": 5,
                "message": "准备实况 HSD 云柱和同周期 GFS 垂直先验",
            })
        try:
            satellite_reference_utc = satellite_lookup_reference_time(
                request.reference_time_utc,
                anchor_utc,
            )
            hsd_warmup = _warm_nowcast_hsd(
                config,
                request.point,
                float(request.radius_km),
                satellite_reference_utc,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            hsd_warmup["errors"] = [str(exc)]
        try:
            warm_times = [
                anchor_utc + timedelta(minutes=5 * step) for step in range(-9, 10)
            ]
            prior_warmup = _warm_nowcast_gfs_prior(
                config,
                request.point,
                float(request.radius_km),
                warm_times,
                cycle_override=request.gfs_cycle_override,
                resolution=request.gfs_resolution,
                region=gfs_region,
            )
            if progress_callback is not None:
                warmup_errors = prior_warmup.get("errors") or []
                hsd_errors = hsd_warmup.get("errors") or []
                if warmup_errors:
                    warmup_message = (
                        "实况 GFS 垂直先验预热部分失败："
                        f"{len(prior_warmup['warmed'])} 个模型时次已缓存，"
                        f"{len(warmup_errors)} 个时次失败"
                    )
                else:
                    warmup_message = (
                        "实况 GFS 垂直先验已就绪："
                        f"{len(prior_warmup['warmed'])} 个模型时次"
                    )
                progress_callback({
                    "stage": "satellite",
                    "percent": 6,
                    "message": (
                        f"{warmup_message}；"
                        f"HSD {hsd_warmup.get('warmed', 0)} 帧"
                        + (f"（{len(hsd_errors)} 个问题）" if hsd_errors else "已就绪")
                    ),
                })
        except (OSError, RuntimeError, ValueError) as exc:
            prior_warmup["errors"] = [str(exc)]
            # Keep the strict scoring contract: a prior that cannot be
            # fetched fails the request during scoring, not here.

    service = ForecastService(
        gfs_client=GfsNomadsClient(
            cache_dir=config.gfs_cache_dir,
            fixed_bbox=gfs_fixed_bbox,
            region=gfs_region,
            feature_cache_enabled=False,
            shared_decode_enabled=True,
            resolution=str(payload.get("gfs_resolution") or "0p25"),
        ),
        aerosol_provider=resolve_aerosol_provider(
            None,
            config.aerosol_source,
            fixed_area=cams_fixed_area,
            cams_cache_dir=config.cams_cache_dir,
        ),
        nowcast_analyzer=JmaNowcastAnalyzer(
            calibrated_provider=_cached_satellite_frame_provider(config),
        ),
        progress_callback=progress_callback,
    )
    warmup_context: dict[str, Any] = {}
    if request.forecast_mode == "nowcast":
        warmup_context = {
            "hsd_warmup": dict(hsd_warmup),
            "prior_warmup": dict(prior_warmup),
            "gfs_cycle": f"{request.gfs_cycle_override:%Y-%m-%dT%HZ}",
        }
    try:
        report = service.forecast(request)
    except (RuntimeError, OSError, ValueError) as exc:
        if warmup_context:
            diagnostics = []
            for problem in (
                warmup_context["hsd_warmup"].get("errors") or []
            ) + (warmup_context["prior_warmup"].get("errors") or []):
                diagnostics.append(str(problem))
            detail = f"；预热诊断：{'；'.join(diagnostics)}" if diagnostics else ""
            raise RuntimeError(
                f"实况请求失败（GFS 周期 {warmup_context['gfs_cycle']}）：{exc}{detail}"
            ) from exc
        raise
    if progress_callback is not None:
        progress_callback({"stage": "report", "percent": 91, "message": "生成 HTML 诊断报告"})
    html_path = write_html_report(report, config.report_dir)
    report = replace(report, html_report_path=str(html_path))
    if progress_callback is not None:
        progress_callback({"stage": "map_frames", "percent": 95, "message": "生成地图云场时间帧"})
    quality_payload = fire_cloud_quality_from_assessments(
        report.steps,
        report.aerosol.aod550,
        event_time_local=report.anchor_local if report.forecast_mode == "nowcast" else None,
        core_window_minutes=(
            NOWCAST_CORE_WINDOW_MINUTES if report.forecast_mode == "nowcast" else None
        ),
        apply_duration=(
            nowcast_duration_is_qualified(report.nowcast, report.steps)
            if report.forecast_mode == "nowcast" and report.nowcast is not None
            else True
        ),
    )
    map_token, map_times = write_map_frames(
        report,
        request.point,
        config.map_cache_dir,
        quality_steps=quality_payload["steps"],
    )
    result = web_report_payload(
        report,
        request.point.elevation_m,
        quality_payload=quality_payload,
    )
    result["map_token"] = map_token
    result["map_times"] = map_times
    result["confidence_score_semantics"] = NATIONAL_CONFIDENCE_KIND
    result["data_provenance"] = {
        "model_source": "gfs",
        "cycle_utc": report.cycle_time_utc.isoformat(),
        "gfs_cache_used": True,
        "cams_cache_used": True,
        "observation_used": False,
    }
    if report.forecast_mode == "nowcast" and report.nowcast is not None and isinstance(
        getattr(report.nowcast, "satellite_vertical_profile", None), dict
    ):
        # Live sections render the same pressure-level view as model points:
        # the calibrated satellite frame supplies along-path cloud evidence
        # and the same-cycle GFS prior supplies heights/humidity.
        satellite_section = None
        satellite_profile = report.nowcast.satellite_vertical_profile
        profile_valid_utc = satellite_profile.get("valid_time_utc")
        try:
            section_time = parse_utc_iso_datetime(
                str(profile_valid_utc), "satellite section valid_time_utc"
            )
            snapshot = solar_snapshot(request.point, section_time, request.timezone_name)
            satellite_section = build_satellite_cross_section(
                satellite_profile,
                observer_lat=request.point.lat,
                observer_lon=request.point.lon,
                observer_elevation_m=request.point.elevation_m,
                timezone_name=request.timezone_name,
                solar_elevation_deg=snapshot.elevation_deg,
                solar_azimuth_deg=snapshot.azimuth_deg,
                analysis_radius_km=float(request.radius_km),
            )
        except (TypeError, ValueError, RuntimeError, KeyError, OSError):
            satellite_section = None
        if satellite_section is not None:
            result["cross_section"] = satellite_section
            result["cross_section_request"] = None
    report_relative = html_path.relative_to(config.report_dir.resolve())
    result["html_report_url"] = f"/reports/{urllib.parse.quote(str(report_relative), safe='/')}"
    if progress_callback is not None:
        progress_callback({"stage": "finalize", "percent": 99, "message": "整理预测结果"})
    return result


# Manifest-v5 provenance, GFS-only prediction inputs, and the public compatible-
# genus diagnosis invalidate older point responses even when their score matches.
# v121: public cloud-genus proxy moved to evidence-driven scoring (v2 source
# string) and now receives the real column phase — cached v120 responses would
# keep showing the old phase-blind genus labels.
# v122: 移除 history/calibration 死字段（calibrated_probability 等），旧缓存
# 响应仍携带这些字段，bump 使其失效。
POINT_CACHE_VERSION = 122


def _point_model_cycle(
    payload: dict[str, Any],
    *,
    now_utc: datetime | None = None,
) -> datetime:
    """Resolve a point cycle using each model's availability contract."""
    target_date, phenomenon = _point_target_identity(payload)
    lat = bounded_float(payload.get("lat"), "纬度", -90.0, 90.0)
    lon = bounded_float(payload.get("lon"), "经度", -180.0, 180.0)
    if "elevation_m" not in payload:
        raise ValueError("单点预测必须显式提供观测者海拔 elevation_m。")
    timezone_name = str(payload.get("timezone_name") or "").strip()
    if not timezone_name:
        raise ValueError("单点预测必须显式提供 timezone_name。")
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("单点预测 timezone_name 不是有效 IANA 时区。") from exc
    point = {
        "name": str(payload.get("name") or "观测点"),
        "lat": lat,
        "lon": lon,
        "elevation_m": bounded_float(
            payload["elevation_m"], "海拔", -500.0, 9000.0
        ),
        "timezone_name": timezone_name,
    }
    normalized = dict(payload)
    normalized["target_date"] = target_date.isoformat()
    normalized["phenomenon"] = phenomenon
    return _national_model_cycle(normalized, [point], now_utc=now_utc)


def point_forecast_cache_key(payload: dict[str, Any]) -> str:
    payload = _point_cache_identity_payload(payload)
    target_date, phenomenon = _point_target_identity(payload)
    forecast_mode = str(payload.get("forecast_mode") or "model").lower()
    gfs_resolution = str(payload.get("gfs_resolution") or "0p25").strip().lower()
    if gfs_resolution not in {"0p25", "0p50"}:
        raise ValueError("gfs_resolution 必须是 0p25 或 0p50。")
    radius_default = 900.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    radius_max = 1000.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    identity: dict[str, Any] = {
        "version": POINT_CACHE_VERSION,
        "algorithm": FORECAST_ALGORITHM_VERSION,
        "target_date": target_date.isoformat(),
        "phenomenon": phenomenon,
        "model_source": "satellite" if forecast_mode == "nowcast" else str(payload.get("model_source") or "gfs").lower(),
        "forecast_mode": forecast_mode,
        "compute_backend": str(payload.get("compute_backend") or "auto").lower(),
        "satellite_source": normalized_satellite_source(forecast_mode, payload.get("satellite_source")),
        "aerosol_source": str(payload.get("_aerosol_source") or "fallback").lower(),
        "gfs_resolution": gfs_resolution,
        "lat": bounded_float(payload.get("lat"), "纬度", -90.0, 90.0),
        "lon": bounded_float(payload.get("lon"), "经度", -180.0, 180.0),
        "elevation_m": bounded_float(
            payload.get("elevation_m"), "海拔", -500.0, 9000.0
        ),
        "timezone_name": str(payload.get("timezone_name") or ""),
        "radius_km": bounded_float(
            payload.get("radius_km", radius_default), "搜索半径", 100.0, radius_max
        ),
        "diagnostic_region": str(payload.get("_diagnostic_region") or "").lower(),
        "grid_region": str(payload.get("_grid_region") or "").lower(),
    }
    if forecast_mode == "nowcast":
        now = datetime.now(timezone.utc)
        identity["observation_bucket_utc"] = now.replace(
            minute=(now.minute // 10) * 10,
            second=0,
            microsecond=0,
        ).isoformat()
    else:
        identity["model_cycle"] = _point_model_cycle(payload).isoformat()
    identity.update(_cams_backend_identity())
    identity.update(rust_astronomy.backend_identity())
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def point_forecast_cache_path(
    cache_dir: str | Path,
    cache_key: str,
    target_date: str | None = None,
    phenomenon: str | None = None,
) -> Path:
    filename = f"point-forecast-v{POINT_CACHE_VERSION}-{cache_key}.json.gz"
    if target_date:
        return dated_directory(cache_dir, target_date, storage_phenomenon(phenomenon)) / filename
    return Path(cache_dir) / filename


def write_point_forecast_cache(
    request_payload: dict[str, Any],
    result: dict[str, Any],
    cache_dir: str | Path,
    status: str = "refresh",
) -> dict[str, Any]:
    cache_key = point_forecast_cache_key(request_payload)
    # Only top-level cache metadata is added below. A deep copy duplicates the
    # complete regional point/frame payload before JSON serialization.
    cached = dict(result)
    cached["response_cache"] = {
        "status": status,
        "key": cache_key,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "version": POINT_CACHE_VERSION,
        "algorithm": FORECAST_ALGORITHM_VERSION,
    }
    target = point_forecast_cache_path(
        cache_dir,
        cache_key,
        request_payload.get("target_date"),
        request_payload.get("phenomenon"),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".tmp-{uuid.uuid4().hex}")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as handle:
            json.dump(cached, handle, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return cached


def _point_cache_cycle_matches_request(
    cached: dict[str, Any],
    request_payload: dict[str, Any],
) -> bool:
    if str(request_payload.get("forecast_mode") or "model").lower() == "nowcast":
        return False
    request_payload = _point_cache_identity_payload(request_payload)
    try:
        expected = _point_model_cycle(request_payload)
        actual = parse_utc_iso_datetime(
            cached["cycle_time_utc"], "point cache cycle_time_utc"
        )
    except (KeyError, TypeError, ValueError):
        return False
    return actual == expected


def read_point_forecast_cache(
    request_payload: dict[str, Any],
    cache_dir: str | Path,
) -> Optional[dict[str, Any]]:
    cache_key = point_forecast_cache_key(request_payload)
    target = point_forecast_cache_path(
        cache_dir,
        cache_key,
        request_payload.get("target_date"),
        request_payload.get("phenomenon"),
    )
    if not target.is_file():
        # Date-less root files belong to the removed pre-date cache layout.
        # Delete a matching stale artifact instead of probing it as a fallback.
        point_forecast_cache_path(cache_dir, cache_key).unlink(missing_ok=True)
        return None
    try:
        with gzip.open(target, "rt", encoding="utf-8") as handle:
            cached = json.load(handle)
    except (OSError, json.JSONDecodeError):
        target.unlink(missing_ok=True)
        return None
    metadata = cached.get("response_cache")
    if (
        not isinstance(metadata, dict)
        or metadata.get("version") != POINT_CACHE_VERSION
        or metadata.get("algorithm") != FORECAST_ALGORITHM_VERSION
    ):
        # Runtime responses are schema-bound. Remove stale files instead of
        # leaving an obsolete cross-section available for a later lookup.
        target.unlink(missing_ok=True)
        return None
    if not _point_cache_cycle_matches_request(cached, request_payload):
        target.unlink(missing_ok=True)
        return None
    metadata["status"] = "hit"
    cached["observer_name"] = str(request_payload.get("name") or cached.get("observer_name") or "观测点")
    return cached


def optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("反馈评分必须是数字。") from exc


def optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def web_report_payload(
    report,
    observer_elevation_asl_m: float,
    *,
    quality_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observer_elevation_asl_m = bounded_float(
        observer_elevation_asl_m, "观测者海拔", -500.0, 9000.0
    )
    forecast_mode = getattr(report, "forecast_mode", "model")
    if quality_payload is None:
        quality_payload = fire_cloud_quality_from_assessments(
            report.steps,
            report.aerosol.aod550,
            event_time_local=report.anchor_local if forecast_mode == "nowcast" else None,
            core_window_minutes=(
                NOWCAST_CORE_WINDOW_MINUTES if forecast_mode == "nowcast" else None
            ),
            apply_duration=(
                nowcast_duration_is_qualified(report.nowcast, report.steps)
                if forecast_mode == "nowcast" and report.nowcast is not None
                else True
            ),
        )
    quality_best_index = quality_payload.get("best_index")
    quality_best_time = (
        report.steps[int(quality_best_index)].valid_time_local.isoformat()
        if quality_best_index is not None and report.steps else None
    )
    peak_quality_index = (
        float(quality_payload["steps"][int(quality_best_index)]["instant_quality_index"])
        if quality_best_index is not None and quality_payload.get("steps")
        else 0.0
    )
    quality_index = quality_payload["quality_index"]
    duration_value = quality_payload["quality_components"].get("duration_minutes")
    analysis_radius_value = quality_payload["quality_components"].get("forward_field_radius_km")
    analysis_radius_km = (
        float(analysis_radius_value)
        if isinstance(analysis_radius_value, Real)
        and not isinstance(analysis_radius_value, bool)
        and math.isfinite(float(analysis_radius_value))
        else None
    )
    duration_status = quality_payload.get("duration_status", "estimated")
    duration_minutes = (
        None
        if duration_value is None or duration_status == "unknown"
        else int(round(float(duration_value)))
    )
    payload = {
        "observer_name": report.observer_name,
        "phenomenon": report.phenomenon,
        "model_source": report.model_source,
        "forecast_mode": getattr(report, "forecast_mode", "model"),
        "compute_backend_requested": getattr(report, "compute_backend_requested", "auto"),
        "compute_backend": getattr(report, "compute_backend", "cpu"),
        "target_date": report.target_date.isoformat(),
        "analysis_radius_km": analysis_radius_km,
        "timezone_name": report.timezone_name,
        "observer_elevation_asl_m": observer_elevation_asl_m,
        "anchor_local": report.anchor_local.isoformat(),
        "cycle_time_utc": report.cycle_time_utc.isoformat(),
        "model_cycles": {
            model: cycle.isoformat()
            for model, cycle in report.model_cycles.items()
        },
        "best_time_local": quality_best_time,
        "overall_score": report.overall_score,
        "burn_score": report.burn_score,
        "intensity_score": report.intensity_score,
        "extent_score": report.extent_score,
        "vividness_score": report.vividness_score,
        "quality_index": quality_index,
        "quality_level": quality_payload["quality_level"],
        "peak_quality_index": peak_quality_index,
        "peak_quality_level": fire_cloud_quality_level(peak_quality_index),
        "quality_components": quality_payload["quality_components"],
        "confidence_score": report.confidence_score,
        "model_disagreement_score": report.model_disagreement_score,
        "duration_minutes": duration_minutes,
        "potential_duration_minutes": duration_minutes,
        "duration_status": duration_status,
        "model_weights": report.model_weights,
        "geometry_window": (
            report.geometry_window.to_dict()
            if getattr(report, "geometry_window", None) is not None
            else None
        ),
        "boundary_uncertainty": (
            report.boundary_uncertainty.to_dict()
            if getattr(report, "boundary_uncertainty", None) is not None
            else None
        ),
        "selected_regime": getattr(report, "selected_regime", "unknown"),
        "scene_scores": getattr(report, "scene_scores", {}),
        "local_sky_score": getattr(report, "local_sky_score", 0.0),
        "remote_sky_score": getattr(report, "remote_sky_score", 0.0),
        "convective_risk": getattr(report, "convective_risk", 0.0),
        "color_diagnosis": getattr(report, "color_diagnosis", {}),
        "cloud_evolution": getattr(report, "cloud_evolution", {}),
        "timing_window": getattr(report, "timing_window", None),
        "source_roles": {
            "primary": "satellite_observation" if getattr(report, "forecast_mode", "model") == "nowcast" else "numerical_model",
            "auxiliary": "aerosol_environment+weather_radar" if getattr(report, "forecast_mode", "model") == "nowcast" else "satellite_observation",
        },
        "aerosol": {
            "aod550": report.aerosol.aod550,
            "source": report.aerosol.source,
            "quality": report.aerosol.quality,
            "pm2_5_ug_m3": getattr(report.aerosol, "pm2_5_ug_m3", None),
            "pm10_ug_m3": getattr(report.aerosol, "pm10_ug_m3", None),
            "particulate_source": getattr(report.aerosol, "particulate_source", None),
        },
        "summary": report.summary,
        "nowcast": nowcast_payload_for_response(getattr(report, "nowcast", None)),
        "steps": [
            {
                "valid_time_local": step.valid_time_local.isoformat(),
                "cloud_source_times_utc": [
                    value.isoformat() for value in step.cloud_source_times_utc
                ],
                "solar_elevation_deg": step.solar_elevation_deg,
                "intensity_score": step.intensity_score,
                "extent_score": step.extent_score,
                "vividness_score": step.vividness_score,
                "visibility_score": step.visibility_score,
                "selected_regime": getattr(step, "selected_regime", "unknown"),
                "scene_scores": getattr(step, "scene_scores", {}),
                "local_sky_score": getattr(step, "local_sky_score", 0.0),
                "remote_sky_score": getattr(step, "remote_sky_score", 0.0),
                "convective_risk": getattr(step, "convective_risk", 0.0),
                "color_diagnosis": getattr(step, "color_diagnosis", {}),
                "top_candidate": (
                    {
                        "lat": step.top_candidate.lat,
                        "lon": step.top_candidate.lon,
                        "cloud_type": step.top_candidate.cloud_type,
                        "score": step.top_candidate.score,
                    }
                    if step.top_candidate
                    else None
                ),
                **quality_payload["steps"][index],
                "valid": True,
                "burn_score": max(
                    0.0,
                    min(
                        1.0,
                        float(quality_payload["steps"][index]["instant_quality_index"])
                        / FIRE_CLOUD_QUALITY_MAX_SCORE,
                    ),
                ),
            }
            for index, step in enumerate(report.steps)
        ],
        "html_report_path": report.html_report_path,
    }
    return payload


def nowcast_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("预测参数必须是 JSON 对象。")
    prepared = dict(payload)
    prepared["forecast_mode"] = "nowcast"
    prepared["model_source"] = "satellite"
    if str(prepared.get("satellite_source") or "auto").lower() == "off":
        prepared["satellite_source"] = "auto"
    return prepared


def normalized_satellite_source(forecast_mode: str, value: Any) -> str:
    satellite_source = str(value or ("auto" if forecast_mode == "nowcast" else "off")).lower()
    if satellite_source not in {"auto", "off"}:
        raise ValueError("satellite_source 必须是 auto 或 off。")
    if forecast_mode == "model":
        return "off"
    if satellite_source == "off":
        raise ValueError("实况主导预测不能关闭卫星实况。")
    return satellite_source


def nowcast_payload_for_response(nowcast) -> Optional[dict[str, Any]]:
    if nowcast is None:
        return None
    return {
        "source": nowcast.source,
        "image_valid_time_utc": nowcast.image_valid_time_utc.isoformat(),
        "image_channel": nowcast.image_channel,
        "image_area": nowcast.image_area,
        "near_cloud_ratio": nowcast.near_cloud_ratio,
        "far_cloud_ratio": nowcast.far_cloud_ratio,
        "trend_score": nowcast.trend_score,
        "confidence": nowcast.confidence,
        "motion_dx_px_per_hour": nowcast.motion_dx_px_per_hour,
        "motion_dy_px_per_hour": nowcast.motion_dy_px_per_hour,
        "motion_correlation": nowcast.motion_correlation,
        "projection_hours": nowcast.projection_hours,
        "satellite_source_times_utc": [value.isoformat() for value in nowcast.satellite_source_times_utc],
        "satellite_motion_age_minutes": nowcast.satellite_motion_age_minutes,
        "satellite_motion_quality": nowcast.satellite_motion_quality,
        "satellite_advection_horizon_hours": nowcast.satellite_advection_horizon_hours,
        "infrared_near_cloud_ratio": nowcast.infrared_near_cloud_ratio,
        "infrared_far_cloud_ratio": nowcast.infrared_far_cloud_ratio,
        "visible_near_cloud_ratio": nowcast.visible_near_cloud_ratio,
        "visible_far_cloud_ratio": nowcast.visible_far_cloud_ratio,
        "channel_fusion_weight_visible": nowcast.channel_fusion_weight_visible,
        "local_cloud_ratio": nowcast.local_cloud_ratio,
        "horizon_cloud_ratio": nowcast.horizon_cloud_ratio,
        "thin_cloud_ratio": nowcast.thin_cloud_ratio,
        "thick_cloud_ratio": nowcast.thick_cloud_ratio,
        "cloud_texture_score": nowcast.cloud_texture_score,
        "cloud_uniformity_score": nowcast.cloud_uniformity_score,
        "cloud_blanket_score": nowcast.cloud_blanket_score,
        "horizon_clearance_score": nowcast.horizon_clearance_score,
        "composition_score": nowcast.composition_score,
        "geometric_path_blocking_risk": nowcast.geometric_path_blocking_risk,
        "geometric_path_samples": nowcast.geometric_path_samples,
        "solar_path_profile": solar_path_profile_payload(nowcast.solar_path_profile),
        "multispectral_valid_time_utc": (
            nowcast.multispectral_valid_time_utc.isoformat()
            if nowcast.multispectral_valid_time_utc
            else None
        ),
        "spectral_b11_b13_index": nowcast.spectral_b11_b13_index,
        "spectral_b13_b15_index": nowcast.spectral_b13_b15_index,
        "ice_cloud_probability": nowcast.ice_cloud_probability,
        "water_cloud_probability": nowcast.water_cloud_probability,
        "thin_cirrus_probability": nowcast.thin_cirrus_probability,
        "altocumulus_probability": nowcast.altocumulus_probability,
        "altostratus_probability": nowcast.altostratus_probability,
        "cirrus_probability": nowcast.cirrus_probability,
        "cloud_type_label": nowcast.cloud_type_label,
        "cloud_type_confidence": nowcast.cloud_type_confidence,
        "radar_source": nowcast.radar_source,
        "radar_valid_time_utc": (
            nowcast.radar_valid_time_utc.isoformat() if nowcast.radar_valid_time_utc else None
        ),
        "radar_age_minutes": nowcast.radar_age_minutes,
        "radar_coverage_ratio": nowcast.radar_coverage_ratio,
        "radar_local_precip_ratio": nowcast.radar_local_precip_ratio,
        "radar_path_precip_ratio": nowcast.radar_path_precip_ratio,
        "radar_convective_ratio": nowcast.radar_convective_ratio,
        "radar_max_dbz": nowcast.radar_max_dbz,
        "radar_convective_risk": nowcast.radar_convective_risk,
        "radar_reliability_score": nowcast.radar_reliability_score,
        "effective_convective_risk": nowcast.effective_convective_risk,
        "effective_path_blocking_risk": nowcast.effective_path_blocking_risk,
        "convective_score_ceiling": nowcast.convective_score_ceiling,
        "calibrated_sample_coverage_ratio": nowcast.calibrated_sample_coverage_ratio,
        "nowcast_algorithm": nowcast.nowcast_algorithm,
        "cloud_column_schema_version": nowcast.cloud_column_schema_version,
        "cloud_column_sets": nowcast.cloud_column_sets,
        "vertical_prior_identity": nowcast.vertical_prior_identity,
        "vertical_prior_source": nowcast.vertical_prior_source,
        "satellite_retrieval_quality": nowcast.satellite_retrieval_quality,
        "satellite_low_cloud_state": nowcast.satellite_low_cloud_state,
        "satellite_range_bytes": nowcast.satellite_range_bytes,
        "satellite_range_blocks": nowcast.satellite_range_blocks,
        "nowcast_point_lat": nowcast.nowcast_point_lat,
        "nowcast_point_lon": nowcast.nowcast_point_lon,
        "nowcast_cache_identity": nowcast.nowcast_cache_identity,
        "satellite_vertical_profile": nowcast.satellite_vertical_profile,
    }


def solar_path_profile_payload(profile) -> list[dict[str, Any]]:
    return [
        {
            "distance_km": sample.distance_km,
            "bearing_deg": sample.bearing_deg,
            "cloud_base_m": sample.cloud_base_m,
            "cloud_top_m": sample.cloud_top_m,
            "cloud_type": sample.cloud_type,
            "high_presence": sample.high_presence,
            "mid_presence": sample.mid_presence,
            "low_presence": sample.low_presence,
            "deep_cloud_risk": sample.deep_cloud_risk,
            "thickness_risk": sample.thickness_risk,
            "required_illumination_height_m": sample.required_illumination_height_m,
            "blocker_risk": sample.blocker_risk,
            "lightpath_clearance_score": sample.lightpath_clearance_score,
            "target_cloud_score": sample.target_cloud_score,
            "illumination_status": sample.illumination_status,
            "levels": [
                {
                    "pressure_hpa": level.pressure_hpa,
                    "height_m": level.height_m,
                    "rh_percent": level.rh_percent,
                    "temperature_c": level.temperature_c,
                    "cloud_fraction_percent": level.cloud_fraction_percent,
                    "cloud_liquid_kg_kg": level.cloud_liquid_kg_kg,
                    "cloud_ice_kg_kg": level.cloud_ice_kg_kg,
                }
                for level in sample.levels
            ],
        }
        for sample in profile
    ]


def run_national_forecast_payload(
    payload: Any,
    config: WebServerConfig,
    progress_callback=None,
    *,
    include_point_core: bool = False,
) -> dict[str, Any]:
    run_started = time.monotonic()
    if not isinstance(payload, dict):
        raise ValueError("全国预测参数必须是 JSON 对象。")
    payload = dict(payload)
    if "_include_point_core" in payload:
        raise ValueError("_include_point_core 不是公共预测参数。")
    if not isinstance(include_point_core, bool):
        raise ValueError("include_point_core 必须是布尔值。")
    requested_model_source = str(payload.get("model_source") or "gfs").lower()
    if requested_model_source not in {"gfs", "ec"}:
        raise ValueError("区域预测 model_source 必须是 gfs 或 ec。")
    if not str(payload.get("target_date") or "").strip():
        payload["target_date"] = china_today().isoformat()
    points = national_points_from_payload(payload)
    coverage = national_coverage_from_payload(payload)
    requested_workers = payload.get("workers")
    worker_count = int(bounded_float(
        requested_workers if requested_workers is not None else 4,
        "workers",
        1,
        8,
    ))
    model_source = requested_model_source
    cycle_override_key = {
        "gfs": "_gfs_cycle_override",
        "ec": "_ec_cycle_override",
    }[model_source]
    cycle_override_value = payload.get(cycle_override_key)
    cycle_override = (
        parse_utc_iso_datetime(cycle_override_value, cycle_override_key)
        if cycle_override_value is not None
        else None
    )
    model_cycle = cycle_override or _national_model_cycle(
        payload,
        points,
    )
    explicit_points = _national_payload_uses_explicit_points(payload)
    if include_point_core and (not explicit_points or len(points) != 1):
        raise ValueError("point core 仅允许单个显式观测点。")
    analysis_radius_km = _analysis_radius_from_payload(payload)
    compute_region, model_bbox, grid_region = _national_compute_domain(
        payload,
        points,
        analysis_radius_km,
    )
    aerosol_source = str(
        payload.get("_aerosol_source") or config.aerosol_source
    ).strip().lower()
    if aerosol_source != str(config.aerosol_source).strip().lower():
        raise ValueError("区域请求的 aerosol source 与执行配置不一致。")
    ecmwf_source = str(payload.get("ecmwf_source") or "ecmwf").strip().lower()
    if ecmwf_source not in {"ecmwf", "aws", "google"}:
        raise ValueError("ecmwf_source 必须是 ecmwf、aws 或 google。")
    common = {
        "target_date": str(payload.get("target_date") or ""),
        "phenomenon": str(payload.get("phenomenon") or "dusk").lower(),
        "model_source": model_source,
        "forecast_mode": str(payload.get("forecast_mode") or "model").lower(),
        "satellite_source": normalized_satellite_source(
            str(payload.get("forecast_mode") or "model").lower(),
            payload.get("satellite_source"),
        ),
        "radius_km": analysis_radius_km,
        "compute_backend": str(payload.get("compute_backend") or "auto").lower(),
        "gfs_resolution": str(payload.get("gfs_resolution") or "0p25").lower(),
        "ecmwf_source": ecmwf_source,
        "_aerosol_source": aerosol_source,
        "spacing_deg": (
            0.0
            if explicit_points
            else _generated_regional_spacing(payload, coverage)
        ),
        "_compute_region": compute_region,
        "_grid_region": grid_region,
        "_model_fixed_bbox": model_bbox,
        "_explicit_points": explicit_points,
    }
    common["_model_cycle_override"] = model_cycle.isoformat()
    common["_model_fixed_bbox"] = model_bbox
    if model_source == "gfs":
        # ForecastRequest still uses this legacy field name for GFS only.
        common["_gfs_fixed_bbox"] = model_bbox
        common["_gfs_cycle_override"] = model_cycle.isoformat()
    else:
        common[f"_{model_source}_cycle_override"] = model_cycle.isoformat()
    # Keep the normalized request worker count attached to the execution
    # context even when the caller omitted the optional field. The national
    # prefetch stage consumes this value too; otherwise it silently fell back
    # to the old two-worker environment default while the public contract said
    # four workers.
    common["workers"] = worker_count
    if not common["target_date"]:
        raise ValueError("全国预测缺少 target_date。")
    date.fromisoformat(common["target_date"])
    if common["phenomenon"] not in {"dawn", "dusk"}:
        raise ValueError("phenomenon 必须是 dawn 或 dusk。")
    if common["model_source"] not in {"gfs", "ec"}:
        raise ValueError("区域批量计算 model_source 必须是 gfs 或 ec。")
    if common["forecast_mode"] not in {"model", "nowcast"}:
        raise ValueError("forecast_mode 必须是 model 或 nowcast。")
    if common["forecast_mode"] == "nowcast":
        # The regional batch path cannot execute a nowcast run: GFS lacks the
        # satellite observation leg and EC lacks the nowcast payload contract
        # (both fail later with KeyError/ValueError). Reject at the contract
        # layer so no half-failed result is cached.
        raise ValueError(
            "区域预测仅支持 forecast_mode=model；nowcast 请使用实况预测端点。"
        )
    if common["compute_backend"] not in {"auto", "cpu", "gpu"}:
        raise ValueError("compute_backend 必须是 auto、cpu 或 gpu。")
    if common["gfs_resolution"] not in {"0p25", "0p50"}:
        raise ValueError("gfs_resolution 必须是 0p25 或 0p50。")
    common["radius_km"] = analysis_radius_km
    if common["model_source"] == "ec" and common["gfs_resolution"] != "0p25":
        raise ValueError("EC 区域预测固定使用 0.25° 规则网格。")
    _prefetch_national_inputs(points, common, config, progress_callback)
    prefetch_elapsed = time.monotonic() - run_started
    if common["forecast_mode"] == "model":
        return _run_national_batch_payload(
            points,
            coverage,
            common,
            config,
            progress_callback,
            run_started=run_started,
            prefetch_elapsed=prefetch_elapsed,
            include_point_core=include_point_core,
        )
    if progress_callback is not None:
        point_label = "单点" if common.get("_explicit_points") else "区域网格"
        progress_callback({"stage": "national", "percent": 20, "message": f"开始计算{point_label} {len(points)} 个点"})

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    diagnostics_token = uuid.uuid4().hex
    diagnostics_path = diagnostic_bundle_path(
        config.regional_cache_dir,
        diagnostics_token,
        common["target_date"],
        common["phenomenon"],
    )
    diagnostics_writer = RegionalDiagnosticBundleWriter(
        diagnostics_path,
        {
            "target_date": common["target_date"],
            "phenomenon": common["phenomenon"],
            "model_source": common["model_source"],
            "forecast_mode": common["forecast_mode"],
            "compute_backend_requested": common["compute_backend"],
            "point_count": len(points),
        },
    )
    def collect_point(
        completed: int,
        point: dict[str, Any],
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        try:
            if error is not None:
                raise RuntimeError(error)
            point_result = result if result is not None else _run_national_point(point, common, config)
            diagnostic = point_result.pop("_diagnostic", None)
            if isinstance(diagnostic, dict):
                diagnostics_writer.add_payload(
                    diagnostic,
                    float(point["lat"]),
                    float(point["lon"]),
                    str(point["name"]),
                )
            results.append(point_result)
        except Exception as exc:
            errors.append({"name": point["name"], "lat": point["lat"], "lon": point["lon"], "error": str(exc)})
        if progress_callback is not None:
            progress_label = "单点" if common.get("_explicit_points") else "区域"
            progress_callback({
                "stage": "national",
                "percent": min(98, 20 + int(78 * completed / len(points))),
                "message": f"{progress_label}预测进度 {completed}/{len(points)}",
                "completed": completed,
                "total": len(points),
            })

    pool_size = min(worker_count, len(points))
    worker_state = threading.local()

    def compute_point(point: dict[str, Any]) -> dict[str, Any]:
        service = getattr(worker_state, "service", None)
        if service is None:
            service = _national_forecast_service(common, config)
            worker_state.service = service
        return _run_national_point_with_service(point, common, service)

    try:
        if pool_size == 1:
            for completed, point in enumerate(points, start=1):
                try:
                    collect_point(completed, point, result=compute_point(point))
                except Exception as exc:
                    collect_point(completed, point, error=str(exc))
        else:
            for completed, (point, result, error) in enumerate(
                daemon_task_results(points, compute_point, pool_size),
                start=1,
            ):
                if error is None:
                    collect_point(completed, point, result=result)
                else:
                    collect_point(completed, point, error=str(error))
    finally:
        diagnostics_count = diagnostics_writer.close()
    results.sort(key=lambda item: (-item["lat"], item["lon"]))
    actual_backends = {str(item.get("compute_backend") or "cpu") for item in results}
    return {
        "scope": "regional",
        "point_mode": (
            "explicit-single-points"
            if common.get("_explicit_points")
            else "generated-regional-grid"
        ),
        "regions": [item["code"] for item in coverage],
        "coverage": coverage,
        "target_date": common["target_date"],
        "phenomenon": common["phenomenon"],
        "model_source": common["model_source"],
        "forecast_mode": common["forecast_mode"],
        "compute_backend_requested": common["compute_backend"],
        "compute_backend": next(iter(actual_backends)) if len(actual_backends) == 1 else "hybrid",
        "point_count": len(points),
        "success_count": len(results),
        "error_count": len(errors),
        "points": results,
        "errors": errors,
        "diagnostics": {
            "token": diagnostics_token,
            "count": diagnostics_count,
            "format": "sqlite-zlib-json-v1",
            "endpoint": "/api/v1/firesky/forecasts/national/diagnostic",
        },
        "cache_recommendation": "区域批量计算前建议预取 east-asia 区域对应模式时次。",
    }


def _load_ec_national_prepared_cache(
    adapter: EcmwfIfsAdapter,
    cycle: datetime,
    forecast_hours: Sequence[int],
    bbox: Sequence[float],
    *,
    source_paths: Sequence[Path] | None = None,
    progress_callback=None,
) -> NationalBatchPrepared | None:
    """Restore an EC national grid from its strict derived cache.

    The raw files are only probed for their grid metadata. All values used by
    the national scorer, including coordinates, terrain, and surface TCC, are
    taken from the matching derived-cache sidecars. A miss returns ``None`` so
    the existing decoder path remains authoritative for cold or stale caches.
    """
    hours = tuple(sorted({int(hour) for hour in forecast_hours}))
    if not hours:
        return None
    try:
        if source_paths is None:
            source_paths = [
                Path(adapter.download(cycle, hour)).expanduser().resolve()
                for hour in hours
            ]
        else:
            source_paths = [Path(path).expanduser().resolve() for path in source_paths]
            if len(source_paths) != len(hours):
                return None
        cached = _load_existing_external_derived_cache(
            "ec",
            cycle,
            hours,
            bbox,
            source_paths,
            expected_pressure_levels=ECMWF_IFS_PRESSURE_LEVELS,
        )
        if cached is None:
            return None
        grid, profiles, pressure_levels, profile_path = cached
        grid_path = profile_path.with_name(f"{profile_path.stem}.grid.npy")
        metadata_path = profile_path.with_name(f"{profile_path.stem}.cache-v1.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            return None
        coordinates = metadata.get("coordinates")
        if not isinstance(coordinates, dict):
            return None
        full_latitudes = np.asarray(coordinates["latitudes"], dtype=np.float32)
        full_longitudes = np.asarray(coordinates["longitudes"], dtype=np.float32)
        if full_latitudes.size == 0 or full_longitudes.size != full_latitudes.size:
            return None
        subset = _external_cache_subset_indices(full_latitudes, full_longitudes, bbox)
        if subset is None:
            return None
        latitudes, longitudes, cell_indices = subset
        if grid.shape[1] != cell_indices.size:
            return None
        latitude_axis = np.unique(latitudes)
        longitude_axis = np.unique(longitudes)
        full_lat_axis = np.unique(full_latitudes)
        full_lon_axis = np.unique(full_longitudes)
        spacing_values = [
            np.diff(axis) for axis in (full_lat_axis, full_lon_axis) if axis.size > 1
        ]
        if not spacing_values:
            return None
        spacing = float(spacing_values[0][0])
        if any(
            not np.allclose(values, spacing, rtol=0.0, atol=1e-5)
            for values in spacing_values
        ):
            return None

        terrain = np.asarray(grid[:, :, 24], dtype=np.float32)
        if (
            terrain.shape != (len(hours), latitudes.size)
            or np.any(~np.isfinite(terrain))
            or np.any((terrain < -500.0) | (terrain > 9000.0))
            or not np.allclose(terrain, terrain[0], rtol=0.0, atol=2.0)
        ):
            return None
        surface_descriptor = metadata.get("surface_cloud_cover")
        if (
            not isinstance(surface_descriptor, dict)
            or surface_descriptor.get("units") != "percent"
        ):
            return None
        surface_path = metadata_path.parent / str(surface_descriptor.get("path") or "")
        expected_surface_path = _model_derived_surface_cloud_path(grid_path)
        if surface_path.resolve() != expected_surface_path.resolve():
            return None
        surface_tcc = np.load(surface_path, mmap_mode="r", allow_pickle=False)
        if (
            surface_tcc.dtype != np.float32
            or surface_tcc.shape != (len(hours), full_latitudes.size)
            or np.any(~np.isfinite(surface_tcc))
            or np.any((surface_tcc < 0.0) | (surface_tcc > 100.0))
        ):
            return None
        surface_tcc = surface_tcc[:, cell_indices]
        prepared_grid = np.array(grid, dtype=np.float32, order="C", copy=True)
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "tensor_ec",
                    "percent": 28,
                    "message": "复用 EC 严格同身份云柱张量，跳过 GRIB 值解码",
                }
            )
        return NationalBatchPrepared(
            grid=prepared_grid,
            hours=np.asarray(hours, dtype=np.float32),
            rows=int(latitude_axis.size),
            cols=int(longitude_axis.size),
            lat0=float(latitude_axis[0]),
            lon0=float(longitude_axis[0]),
            spacing=spacing,
            latitudes=np.asarray(latitudes, dtype=np.float32),
            longitudes=np.asarray(longitudes, dtype=np.float32),
            cache_paths=[str(path) for path in source_paths],
            layer_profiles=profiles,
            pressure_levels=pressure_levels,
            layer_profile_path=str(profile_path),
            model_source="ec",
            surface_elevation_m=np.ascontiguousarray(terrain[0], dtype=np.float32),
        )
    except (
        AttributeError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ):
        return None


def _durable_profile_source(prepared: NationalBatchPrepared) -> dict[str, Any]:
    """Describe the complete profile mmap, even when scoring used a subset."""
    source = {
        "model_source": str(prepared.model_source),
        "layer_profile_path": prepared.layer_profile_path,
        "hours": [float(value) for value in prepared.hours],
        "rows": prepared.rows,
        "cols": prepared.cols,
        "lat0": prepared.lat0,
        "lon0": prepared.lon0,
        "spacing": prepared.spacing,
        "pressure_levels": list(prepared.pressure_levels),
    }
    if not prepared.layer_profile_path:
        return source
    if prepared.model_source != "ec":
        return source
    profile_path = Path(str(prepared.layer_profile_path))
    metadata_path = profile_path.with_name(f"{profile_path.stem}.cache-v1.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        coordinates = metadata["coordinates"]
        identity = metadata["identity"]
        latitudes = np.asarray(coordinates["latitudes"], dtype=np.float32).reshape(-1)
        longitudes = np.asarray(coordinates["longitudes"], dtype=np.float32).reshape(-1)
        lat_axis = np.unique(latitudes)
        lon_axis = np.unique(longitudes)
        if (
            latitudes.size == 0
            or latitudes.shape != longitudes.shape
            or lat_axis.size * lon_axis.size != latitudes.size
            or not np.array_equal(latitudes, np.repeat(lat_axis, lon_axis.size))
            or not np.array_equal(longitudes, np.tile(lon_axis, lat_axis.size))
        ):
            return source
        spacing_values = [np.diff(axis) for axis in (lat_axis, lon_axis) if axis.size > 1]
        spacing = float(spacing_values[0][0]) if spacing_values else float(prepared.spacing)
        if any(
            not np.allclose(values, spacing, rtol=0.0, atol=1e-5)
            for values in spacing_values
        ):
            return source
        full_hours = identity["forecast_hours"]
        full_levels = metadata["pressure_levels"]
        source.update({
            "hours": [float(value) for value in full_hours],
            "pressure_levels": [int(value) for value in full_levels],
            "rows": int(lat_axis.size),
            "cols": int(lon_axis.size),
            "lat0": float(lat_axis[0]),
            "lon0": float(lon_axis[0]),
            "spacing": spacing,
        })
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return source


def _external_prepared_bbox(
    model_source: str,
    bbox: Sequence[float],
    radius_km: float = NATIONAL_ANALYSIS_RADIUS_KM,
) -> tuple[float, float, float, float]:
    """Expand a regional source bbox to cover every point's physical domain."""
    source = str(model_source).strip().lower()
    west, east, north, south = (float(value) for value in bbox)
    if west > east:
        raise ValueError("外部模型预热 bbox 跨越日期线，不能用单一规则网格表示")
    if source != "ec":
        raise ValueError("external prepared bbox requires model source ec")
    _, _, source_radius_km = _ecmwf_point_domain_geometry(radius_km)
    boxes = [
        bounding_box(GeoPoint(lat=latitude, lon=longitude), source_radius_km)
        for latitude in (south, north)
        for longitude in (west, east)
    ]
    if any(left > right for left, right, _, _ in boxes):
        raise ValueError("外部模型物理域跨越日期线，不能安全构造单一预热网格")
    return (
        max(-180.0, min(west, *(box[0] for box in boxes))),
        min(180.0, max(east, *(box[1] for box in boxes))),
        min(90.0, max(north, *(box[2] for box in boxes))),
        max(-90.0, min(south, *(box[3] for box in boxes))),
    )


def _external_target_axis(lower: float, upper: float, spacing: float) -> np.ndarray:
    """Build a regular axis that contains both bounds after discretization."""
    if not all(math.isfinite(float(value)) for value in (lower, upper, spacing)):
        raise ValueError("外部模型预热网格边界必须是有限数值")
    if spacing <= 0.0 or lower > upper:
        raise ValueError("外部模型预热网格边界无效")
    start = math.floor(lower / spacing + 1e-10) * spacing
    stop = math.ceil(upper / spacing - 1e-10) * spacing
    start = max(-180.0, start)
    stop = min(180.0, stop)
    axis = np.arange(start, stop + spacing * 0.5, spacing, dtype=np.float64)
    if axis.size == 0:
        raise ValueError("外部模型预热网格为空")
    return axis


def _run_national_batch_payload(
    points: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    common: dict[str, Any],
    config: WebServerConfig,
    progress_callback,
    *,
    run_started: float,
    prefetch_elapsed: float,
    include_point_core: bool,
) -> dict[str, Any]:
    model_source = str(common["model_source"])
    if model_source not in {"gfs", "ec"}:
        raise ValueError("区域批量计算 model_source 必须是 gfs 或 ec。")
    model_label = {"gfs": "GFS", "ec": "ECMWF IFS"}[model_source]
    cycle = parse_utc_iso_datetime(
        common["_model_cycle_override"], "_model_cycle_override"
    )
    field_radius_km = min(float(common["radius_km"]), NATIONAL_ANALYSIS_RADIUS_KM)
    forecast_hours = [int(value) for value in common.get("_model_forecast_hours", [])]
    bbox = tuple(
        common.get("_model_prepared_bbox") or common["_model_fixed_bbox"]
    )
    if progress_callback is not None:
        progress_callback({
            "stage": f"tensor_{model_source}",
            "percent": 20,
            "message": f"构建 {len(forecast_hours)} 个 {model_label} 时次的全国云柱张量",
        })
    tensor_started = time.monotonic()
    if model_source == "gfs":
        client = GfsNomadsClient(
            cache_dir=config.gfs_cache_dir,
            fixed_bbox=bbox,
            resolution=str(common.get("gfs_resolution") or "0p25"),
            feature_cache_enabled=False,
            shared_decode_enabled=True,
            shared_decode_cache_limit=max(4, len(forecast_hours)),
        )
        prepared = prepare_model_columns(client, cycle, forecast_hours, bbox, progress_callback)
    elif model_source == "ec":
        adapter = EcmwfIfsAdapter(
            cache_dir=config.ecmwf_cache_dir,
            source=str(common.get("ecmwf_source") or "ecmwf"),
        )
        prepared = common.pop("_ec_prefetched", None)
        if prepared is None:
            prepared = _load_ec_national_prepared_cache(
                adapter,
                cycle,
                forecast_hours,
                bbox,
                progress_callback=progress_callback,
            )
        if prepared is None:
            def load_ec_grid(cycle_value: datetime, hour: int):
                tensor = adapter.load_tensor(cycle_value, hour, bbox=bbox)
                grid, _, _, _ = _tensor_to_native_grid(tensor)
                return grid, tensor.source_file

            prepared = prepare_external_model_columns(
                "ec",
                cycle,
                forecast_hours,
                load_ec_grid,
                bbox=bbox,
                progress=progress_callback,
            )
    else:
        raise AssertionError("unreachable model source")
    validate_prepared_model_surface_elevations(prepared, points)
    tensor_elapsed = time.monotonic() - tensor_started
    if progress_callback is not None:
        progress_callback({
            "stage": "batch_compute",
            "percent": 30,
            "message": (
                f"全国张量单点批处理 {len(points)} 个点"
                if common.get("_explicit_points")
                else f"全国张量区域批处理 {len(points)} 个点"
            ),
            "completed": 0,
            "total": len(points),
        })
    compute_started = time.monotonic()
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if progress_callback is not None:
        def emit_compute_heartbeat() -> None:
            while not heartbeat_stop.wait(10.0):
                elapsed = int(time.monotonic() - compute_started)
                try:
                    progress_callback({
                        "stage": "batch_compute",
                        "percent": 30,
                        "message": f"全国张量区域批处理进行中，已运行 {elapsed}s",
                        "completed": 0,
                        "total": len(points),
                    })
                except ForecastCancelled:
                    # The native call remains cancellation-safe at its existing
                    # boundary; stop producing progress once cancellation wins.
                    return

        heartbeat_thread = threading.Thread(
            target=emit_compute_heartbeat,
            name="firesky-national-progress",
            daemon=True,
        )
        heartbeat_thread.start()
    try:
        (
            output,
            samples,
            evaluation_epoch,
            anchor_epoch,
            aerosol_sources,
            aerosol_qualities,
            aerosol_cache_paths,
        ) = run_native_national_batch(
            prepared,
            points,
            date.fromisoformat(str(common["target_date"])),
            str(common["phenomenon"]),
            cycle,
            field_radius_km,
            config.cams_cache_dir,
            national_cams_area(bbox),
            str(common["compute_backend"]),
        )
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
    if model_source == "ec":
        if len(aerosol_qualities) != len(points) or any(
            str(value).strip().lower() != "cams" for value in aerosol_qualities
        ):
            raise RuntimeError(
                f"{model_label} regional scoring requires CAMS aerosol quality; "
                "fallback or missing quality is not publishable"
            )
        if len(aerosol_sources) != len(points) or any(
            not str(value).strip() for value in aerosol_sources
        ):
            raise RuntimeError(
                f"{model_label} regional scoring did not return CAMS profile provenance"
            )
        if not aerosol_cache_paths or any(
            not str(path).strip() for path in aerosol_cache_paths
        ):
            raise RuntimeError(
                f"{model_label} regional scoring did not return CAMS profile files"
            )
    compute_elapsed = time.monotonic() - compute_started
    # The authoritative observer-field scorer is CPU-only until the complete
    # ray/compositor path has a validated GPU implementation.
    assessment_backend = "cpu"
    actual_backend = assessment_backend
    if progress_callback is not None:
        progress_callback({
            "stage": "frames",
            "percent": 40,
            "message": f"准备 {len(points)} 个格点的全量前方云场时间帧",
            "completed": 0,
            "total": len(points),
        })
    frames_started = time.monotonic()
    results, point_core, frame_payload = build_national_frame_payloads(
        prepared,
        points,
        date.fromisoformat(str(common["target_date"])),
        str(common["phenomenon"]),
        cycle,
        output,
        samples,
        evaluation_epoch,
        anchor_epoch,
        aerosol_sources,
        aerosol_qualities,
        assessment_backend,
        include_point_core=include_point_core,
        point_spacing_deg=float(common.get("spacing_deg") or 0.0),
        analysis_radius_km=field_radius_km,
        progress_callback=progress_callback,
    )
    frames_elapsed = time.monotonic() - frames_started
    input_manifest = build_national_input_manifest(
        prepared,
        aerosol_cache_paths,
        cycle,
        forecast_hours,
        bbox,
        model_resolution=str(common["gfs_resolution"]),
        requested_aerosol_source=str(common["_aerosol_source"]),
        actual_aerosol_sources=aerosol_qualities,
        compute_region=common["_compute_region"],
        compute_domain_key=str(common["_grid_region"]),
        terrain_source=common["_terrain_source"],
    )
    try:
        frame_point_count, _ = validate_current_national_frame_metadata(frame_payload)
    except ValueError as exc:
        raise RuntimeError(f"全国帧生成结果不符合 current national frame 合同：{exc}") from exc
    if frame_point_count != len(points):
        raise RuntimeError("全国帧 point_count 与请求点数不一致。")
    precision = dict(frame_payload["precision"])
    refinement_performance = dict(frame_payload["performance"])
    if progress_callback is not None:
        progress_callback({
            "stage": "frames",
            "percent": 98,
            "message": f"评分与云场时间帧完成，共 {len(frame_payload['frames'])} 帧",
            "completed": len(points),
            "total": len(points),
        })
    total_elapsed = time.monotonic() - run_started
    result = {
        "scope": "regional",
        "point_mode": (
            "explicit-single-points"
            if common.get("_explicit_points")
            else "generated-regional-grid"
        ),
        "regions": [item["code"] for item in coverage],
        "coverage": coverage,
        "target_date": common["target_date"],
        "phenomenon": common["phenomenon"],
        "model_source": common["model_source"],
        "forecast_mode": common["forecast_mode"],
        "compute_backend_requested": common["compute_backend"],
        "compute_backend": actual_backend,
        "precision": precision,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "cycle_time_utc": cycle.isoformat(),
        "point_count": len(points),
        "success_count": len(results),
        "error_count": 0,
        "points": results,
        "frame_data": frame_payload,
        "errors": [],
        "timing": {
            "prefetch_seconds": round(prefetch_elapsed, 3),
            "tensor_seconds": round(tensor_elapsed, 3),
            "compute_seconds": round(compute_elapsed, 3),
            "frames_seconds": round(frames_elapsed, 3),
            "frame_instant_scoring_seconds": refinement_performance[
                "instant_scoring_seconds"
            ],
            "frame_assembly_seconds": refinement_performance["assembly_seconds"],
            "frame_points_per_second": refinement_performance["points_per_second"],
            "frame_samples_per_second": refinement_performance["samples_per_second"],
            "total_seconds": round(total_elapsed, 3),
        },
        "_profile_source": {
            **_durable_profile_source(prepared),
            "target_date": common["target_date"],
            "phenomenon": common["phenomenon"],
            "cycle_utc": cycle.isoformat(),
        },
        "input_manifest": input_manifest,
        "cache_recommendation": f"全国批处理使用已缓存 {model_label}/CAMS 文件；下载耗时单独计入 prefetch。",
    }
    if point_core is not None:
        result["_point_core"] = point_core
    return result


# Manifest-v5 provenance and the GFS-only prediction contract require direct
# rejection of older regional payloads and summaries.
NATIONAL_CACHE_VERSION = 125
NATIONAL_SUMMARY_VERSION = 55
NATIONAL_PUBLIC_POINT_FORMAT = "array-v6"


def regional_forecast_contract() -> dict[str, Any]:
    """Return the cheap contract identity used before regional work starts."""
    return {
        "scope": "regional",
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "summary_version": NATIONAL_SUMMARY_VERSION,
        "cache_version": NATIONAL_CACHE_VERSION,
        "point_format": NATIONAL_PUBLIC_POINT_FORMAT,
    }


NATIONAL_PUBLIC_POINT_FIELDS = (
    "name",
    "lat",
    "lon",
    "observer_elevation_asl_m",
    "quality_index",
    "quality_level",
    "peak_quality_index",
    "peak_quality_level",
    "dominant_burnable_pressure_family",
    "confidence_score",
    "best_time_local",
    "burnable_cloud_coverage_effective",
    "forward_view_cloud_field_amount_peak",
    "forward_burnable_cloud_field_amount_peak",
    "forward_burnable_cloud_ratio_peak",
    "burnable_duration_minutes",
    "local_aod550",
    "quality_aod550",
    "incoming_aod_illumination_factor",
    "observer_aod_contrast_factor",
    "observer_return_optical_depth_mean",
    "observer_return_transmission_mean",
    "incoming_aod_adjustment",
    "observer_aod_adjustment",
    "high_cloud",
    "mid_cloud",
    "low_cloud",
)
NATIONAL_PUBLIC_POINT_COMPONENT_FIELDS = {
    "burnable_cloud_coverage_effective": "burnable_cloud_coverage_effective",
    "forward_view_cloud_field_amount_peak": "forward_view_cloud_field_amount_peak",
    "forward_burnable_cloud_field_amount_peak": "forward_burnable_cloud_field_amount_peak",
    "forward_burnable_cloud_ratio_peak": "forward_burnable_cloud_ratio_peak",
    "burnable_duration_minutes": "duration_minutes",
    "local_aod550": "local_aod550",
    "quality_aod550": "quality_aod550",
    "incoming_aod_illumination_factor": "incoming_aod_illumination_factor",
    "observer_aod_contrast_factor": "observer_aod_contrast_factor",
    "observer_return_optical_depth_mean": "observer_return_optical_depth_mean",
    "observer_return_transmission_mean": "observer_return_transmission_mean",
    "incoming_aod_adjustment": "incoming_aod_adjustment",
    "observer_aod_adjustment": "observer_aod_adjustment",
}
NATIONAL_PUBLIC_POINT_TEXT_FIELDS = frozenset(
    {"name", "quality_level", "peak_quality_level"}
)
NATIONAL_PUBLIC_POINT_NULLABLE_FIELDS = frozenset(
    {"dominant_burnable_pressure_family", "best_time_local"}
)
NATIONAL_SUMMARY_REQUIRED_SOURCE_FIELDS = (
    "scope",
    "point_mode",
    "regions",
    "coverage",
    "target_date",
    "phenomenon",
    "model_source",
    "algorithm",
    "cycle_time_utc",
    "forecast_mode",
    "compute_backend_requested",
    "compute_backend",
    "precision",
    "timing",
    "point_count",
    "success_count",
    "error_count",
    "cache_recommendation",
    "cache",
    "points",
    "errors",
)
NATIONAL_SUMMARY_FIELDS = frozenset({
    *NATIONAL_SUMMARY_REQUIRED_SOURCE_FIELDS,
    "data_provenance",
    "version",
    "point_format",
    "point_fields",
})
NATIONAL_SUMMARY_PROVENANCE_FIELDS = frozenset({
    "cache_status",
    "cams_source",
    "generated_at_utc",
    "stale",
    "inputs_complete",
    "requested_gfs_cycle_utc",
    "served_gfs_cycle_utc",
    "gfs_forecast_hours",
    "gfs_valid_times_utc",
    "cams_run_times_utc",
    "cams_valid_times_utc",
    "algorithm",
})
NATIONAL_SUMMARY_ERROR_FIELDS = frozenset({"name", "lat", "lon", "error"})
NATIONAL_SUMMARY_COVERAGE_FIELDS = frozenset({
    "code",
    "label",
    "bounds",
    "timezone_name",
    "point_prefix",
})
NATIONAL_SUMMARY_TIMING_FIELDS = frozenset({
    "prefetch_seconds",
    "tensor_seconds",
    "compute_seconds",
    "frames_seconds",
    "frame_instant_scoring_seconds",
    "frame_assembly_seconds",
    "frame_points_per_second",
    "frame_samples_per_second",
    "total_seconds",
})
NATIONAL_SUMMARY_CACHE_FIELDS = frozenset({
    "status",
    "key",
    "generated_at_utc",
    "version",
})


def _persist_national_forecast_result(
    payload: dict[str, Any],
    result: dict[str, Any],
    config: WebServerConfig,
) -> dict[str, Any]:
    if "_point_core" in result:
        raise RuntimeError("内存 point core 不得写入全国结果缓存。")
    profile_source = result.pop("_profile_source", None)
    if isinstance(profile_source, dict) and profile_source.get("layer_profile_path"):
        write_national_profile_manifest(
            config.regional_cache_dir,
            national_forecast_cache_key(payload),
            str(result["target_date"]),
            str(result["phenomenon"]),
            profile_source,
        )
    return write_national_forecast_cache(payload, result, config.regional_cache_dir)


def run_and_cache_national_forecast_payload(
    payload: Any,
    config: WebServerConfig,
    progress_callback=None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("全国预测参数必须是 JSON 对象。")
    force_refresh = bool(payload.get("force_refresh", False))
    native_public = _native_regional_backend_enabled()
    normalized_payload = {
        key: value for key, value in payload.items() if key != "force_refresh"
    }
    normalized_payload["_aerosol_source"] = str(config.aerosol_source).strip().lower()
    if not force_refresh:
        try:
            cached = read_national_forecast_cache(
                normalized_payload, config.regional_cache_dir
            )
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            cached = None
        if cached is not None:
            native = cached.get("native_forecast")
            if (
                not native_public
                or (
                    isinstance(native, dict)
                    and native.get("contract")
                    == "rust-public-regional-forecast-v1"
                    and native.get("runtime_python_used") is False
                    and native.get("public_forecast_complete") is True
                )
            ):
                if progress_callback is not None:
                    progress_callback({
                        "stage": "cache",
                        "percent": 99,
                        "message": "命中全国预测持久缓存",
                    })
                return cached
    if native_public and str(
        normalized_payload.get("forecast_mode") or "model"
    ).strip().lower() == "model":
        model = str(normalized_payload.get("model_source") or "gfs").strip().lower()
        if model not in {"gfs", "ec"}:
            raise ValueError("FIRESKY_REGIONAL_DATA_BACKEND=rust requires model_source=gfs or ec")
        from firesky.rust_desktop_data import forecast_regional

        return forecast_regional(
            normalized_payload,
            model=model,
            config=config,
            progress=progress_callback,
            public=True,
        )
    result = run_national_forecast_payload(
        normalized_payload, config, progress_callback
    )
    return _persist_national_forecast_result(normalized_payload, result, config)


def run_and_cache_national_forecast_public_payload(
    payload: Any,
    config: WebServerConfig,
    progress_callback=None,
) -> dict[str, Any]:
    """Run the full forecast, persist the full local cache, and return the public projection."""
    if not isinstance(payload, dict):
        raise ValueError("全国预测参数必须是 JSON 对象。")
    force_refresh = bool(payload.get("force_refresh", False))
    native_public = _native_regional_backend_enabled()
    coverage = national_coverage_from_payload(payload)
    if [str(area["code"]) for area in coverage] == ["china"]:
        if _national_payload_uses_explicit_points(payload):
            raise ValueError(
                "public china forecast only accepts the generated 0.25-degree "
                "36,685-point grid; explicit and city-catalog points are offline-only"
            )
        points = national_points_from_payload(payload)
        if len(points) != CHINA_PRODUCTION_POINT_COUNT:
            raise ValueError(
                "public china forecast requires exactly 36,685 generated points"
            )
    if not force_refresh and not native_public:
        summary_payload = dict(payload)
        summary_payload["_aerosol_source"] = str(config.aerosol_source).strip().lower()
        try:
            summary = read_national_forecast_summary(
                summary_payload,
                config.regional_cache_dir,
            )
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            summary = None
        if (
            summary is not None
            and isinstance(summary.get("data_provenance"), dict)
            and not bool(summary["data_provenance"].get("stale"))
        ):
            if progress_callback is not None:
                progress_callback({
                    "stage": "cache",
                    "percent": 99,
                    "message": "命中区域摘要缓存",
                })
            return summary
    cached = run_and_cache_national_forecast_payload(
        payload, config, progress_callback
    )
    # The Rust desktop bridge intentionally publishes an internal regional
    # result with ``public_forecast_complete=false``.  It shares the durable
    # cache root for reuse by the desktop UI, but that artifact must never be
    # promoted into the public array-v6 summary contract.  Recompute through
    # the owning Python/public path when such a cache is encountered.
    native = cached.get("native_forecast") if isinstance(cached, dict) else None
    if (
        isinstance(native, dict)
        and native.get("contract") == "rust-desktop-regional-forecast-v1"
        and native.get("runtime_python_used") is False
        and native.get("public_forecast_complete") is False
    ):
        cached = run_and_cache_national_forecast_payload(
            {**payload, "force_refresh": True},
            config,
            progress_callback,
        )
    if str(cached.get("model_source") or "gfs").lower() == "ec":
        return _external_national_forecast_summary(cached)
    return _national_forecast_summary(cached)


def national_forecast_cache_key(payload: dict[str, Any]) -> str:
    return _national_forecast_cache_key_from_identity(
        national_forecast_cache_identity(payload)
    )


def _national_forecast_cache_key_from_identity(identity: dict[str, Any]) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _national_model_reference_utc(
    points: list[dict[str, Any]],
    target_date: str,
    phenomenon: str,
    now_utc: datetime,
) -> datetime:
    """Use the target event time for retrospective batches, never a future cycle."""
    if not points:
        return now_utc
    import numpy as np

    _, _, _, _, _, anchor_epoch = prepare_solar_event_timeline(
        points,
        date.fromisoformat(target_date),
        phenomenon,
    )
    finite = np.asarray(anchor_epoch, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return now_utc
    target_event_utc = datetime.fromtimestamp(float(np.min(finite)), tz=timezone.utc)
    return min(now_utc, target_event_utc)


def _national_model_cycle(
    payload: dict[str, Any],
    points: list[dict[str, Any]],
    *,
    now_utc: datetime | None = None,
) -> datetime:
    """Resolve one model cycle for both execution and cache identity."""
    model_source = str(payload.get("model_source") or "gfs").lower()
    override_keys = {
        "gfs": ("_gfs_cycle_override", "gfs_cycle_override"),
        "ec": ("_ec_cycle_override", "ec_cycle_override"),
    }.get(model_source, ())
    override_key = next((key for key in override_keys if payload.get(key) is not None), None)
    override = payload.get(override_key) if override_key else None
    if override is not None:
        return parse_utc_iso_datetime(override, override_key or "model_cycle_override")
    reference = _national_model_reference_utc(
        points,
        str(payload.get("target_date") or china_today().isoformat()),
        str(payload.get("phenomenon") or "dusk").lower(),
        now_utc or datetime.now(timezone.utc),
    )
    if model_source == "gfs":
        return choose_gfs_cycle(reference)
    if model_source == "ec":
        return choose_ecmwf_ifs_cycle(reference)
    raise ValueError("区域预测 model_source 必须是 gfs 或 ec。")


def national_forecast_cache_identity(payload: dict[str, Any]) -> dict[str, Any]:
    target_date = str(payload.get("target_date") or "").strip()
    if not target_date:
        target_date = china_today().isoformat()
    date.fromisoformat(target_date)
    phenomenon = str(payload.get("phenomenon") or "dusk").lower()
    model_source = str(payload.get("model_source") or "gfs").lower()
    forecast_mode = str(payload.get("forecast_mode") or "model").lower()
    satellite_source = normalized_satellite_source(forecast_mode, payload.get("satellite_source"))
    compute_backend = str(payload.get("compute_backend") or "auto").lower()
    if phenomenon not in {"dawn", "dusk"}:
        raise ValueError("phenomenon 必须是 dawn 或 dusk。")
    if model_source not in {"gfs", "ec"}:
        raise ValueError("区域缓存 model_source 必须是 gfs 或 ec。")
    if forecast_mode not in {"model", "nowcast"}:
        raise ValueError("forecast_mode 必须是 model 或 nowcast。")
    if compute_backend not in {"auto", "cpu", "gpu"}:
        raise ValueError("compute_backend 必须是 auto、cpu 或 gpu。")
    model_resolution = str(payload.get("gfs_resolution") or "0p25").strip().lower()
    if model_resolution not in {"0p25", "0p50"}:
        raise ValueError("model resolution 必须是 0p25 或 0p50。")
    aerosol_source = str(
        payload.get("_aerosol_source")
        or payload.get("aerosol_source")
        or "cams"
    ).strip().lower()
    if aerosol_source not in {"auto", "cams"}:
        raise ValueError("aerosol_source 必须是 auto 或 cams。")
    if model_source == "ec" and aerosol_source == "auto":
        # External regional scoring has no fallback aerosol contract.  Keep
        # the read/write identity stable when execution config resolves auto
        # to the required strict CAMS profile.
        aerosol_source = "cams"
    # Model mode is scored on the canonical 1000 km observer field.  Nowcast
    # uses a separate satellite footprint and may request the wider 1000 km
    # operational envelope.  Canonicalize legacy model cache requests to the
    # field that is actually evaluated; execution validation still rejects an
    # oversized model request instead of silently clipping it.
    radius_default = 900.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    radius_max = 1000.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    requested_radius = bounded_float(
        payload.get("radius_km", radius_default),
        "搜索半径",
        100.0,
        radius_max,
    )
    effective_radius = (
        min(requested_radius, NATIONAL_ANALYSIS_RADIUS_KM)
        if forecast_mode == "model"
        else requested_radius
    )
    identity: dict[str, Any] = {
        "version": NATIONAL_CACHE_VERSION,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "target_date": target_date,
        "phenomenon": phenomenon,
        "model_source": model_source,
        "forecast_mode": forecast_mode,
        "satellite_source": satellite_source,
        "aerosol_source": aerosol_source,
        "radius_km": bounded_float(effective_radius, "搜索半径", 100.0, radius_max),
        **_cams_backend_identity(),
        **rust_astronomy.backend_identity(),
    }
    if model_source == "gfs":
        # Preserve the existing GFS cache key byte-for-byte.
        identity["gfs_resolution"] = model_resolution
    else:
        if model_resolution != "0p25":
            raise ValueError("EC 区域缓存固定使用 0.25° 规则网格。")
        identity["model_resolution"] = model_resolution
    point_mode = str(payload.get("point_mode") or "").strip().lower()
    coverage = national_coverage_from_payload(payload)
    identity["coverage"] = [
        {
            "code": item["code"],
            "bounds": item["bounds"],
            "label": item["label"],
            **(
                {"timezone_name": item["timezone_name"]}
                if item.get("timezone_name")
                else {}
            ),
        }
        for item in coverage
    ]
    points_for_cycle = national_points_from_payload(payload)
    compute_region, model_bbox, grid_region = _national_compute_domain(
        payload,
        points_for_cycle,
        float(identity["radius_km"]),
    )
    identity["compute_domain"] = {
        "region": compute_region,
        "bbox": list(model_bbox),
        "grid_region": grid_region,
    }
    if _national_payload_uses_explicit_points(payload):
        identity["point_mode"] = point_mode or "explicit-points"
        identity["points"] = points_for_cycle
    else:
        identity["spacing_deg"] = _generated_regional_spacing(payload, coverage)
    identity["model_cycle"] = _national_model_cycle(
        payload,
        points_for_cycle,
    ).isoformat()
    return identity


def national_forecast_cache_path(
    cache_dir: str | Path,
    cache_key: str,
    target_date: str | None = None,
    phenomenon: str | None = None,
) -> Path:
    filename = f"regional-forecast-v{NATIONAL_CACHE_VERSION}-{cache_key}.json.gz"
    if target_date:
        return dated_directory(
            cache_dir,
            target_date,
            storage_phenomenon(phenomenon),
            "results",
        ) / filename
    return Path(cache_dir) / filename


def national_forecast_summary_path(
    cache_dir: str | Path,
    cache_key: str,
    target_date: str | None = None,
    phenomenon: str | None = None,
) -> Path:
    filename = f"regional-summary-v{NATIONAL_SUMMARY_VERSION}-{cache_key}.json.gz"
    if target_date:
        return dated_directory(
            cache_dir,
            target_date,
            storage_phenomenon(phenomenon),
            "results",
        ) / filename
    return Path(cache_dir) / filename


def national_profile_manifest_path(
    cache_dir: str | Path,
    cache_key: str,
    target_date: str,
    phenomenon: str,
) -> Path:
    return dated_directory(
        cache_dir,
        target_date,
        storage_phenomenon(phenomenon),
        "profiles",
    ) / f"national-profile-v4-{cache_key}.json"


def write_national_profile_manifest(
    cache_dir: str | Path,
    cache_key: str,
    target_date: str,
    phenomenon: str,
    source: dict[str, Any],
) -> Path:
    target = national_profile_manifest_path(cache_dir, cache_key, target_date, phenomenon)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".tmp-{uuid.uuid4().hex}")
    temporary.write_text(
        json.dumps(
            {
                "version": 4,
                "algorithm": NATIONAL_BATCH_ALGORITHM,
                "layer_profile_field_count": len(LAYER_PROFILE_FIELDS),
                "layer_profile_fields": list(LAYER_PROFILE_FIELDS),
                **source,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def read_national_cross_section(
    payload: dict[str, Any],
    cache_dir: str | Path,
    lat: float,
    lon: float,
    offset_minutes: float,
    *,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    explicit_key = str(payload.get("profile_key") or "").strip().lower()
    if explicit_key and re.fullmatch(r"[0-9a-f]{24}", explicit_key) is None:
        raise ValueError("压力层缓存标识无效。")
    cache_key = explicit_key or national_forecast_cache_key(payload)
    target = national_profile_manifest_path(
        cache_dir,
        cache_key,
        str(payload["target_date"]),
        str(payload.get("phenomenon") or "dusk"),
    )
    if not target.is_file():
        raise FileNotFoundError("暂无匹配的全国压力层缓存，请重新运行全国计算。")
    source = json.loads(target.read_text(encoding="utf-8"))
    if (
        int(source.get("version", 0)) != 4
        or str(source.get("algorithm") or "") != NATIONAL_BATCH_ALGORITHM
        or int(source.get("layer_profile_field_count", 0)) != len(LAYER_PROFILE_FIELDS)
        or source.get("layer_profile_fields") != list(LAYER_PROFILE_FIELDS)
    ):
        raise RuntimeError("全国压力层缓存版本过期，请重新运行全国计算。")
    if timezone_name is None:
        timezone_name = point_timezone(lat, lon)
        for area in national_coverage_from_payload(payload):
            west, south, east, north = area["bounds"]
            if west <= lon <= east and south <= lat <= north and area.get("timezone_name"):
                timezone_name = str(area["timezone_name"])
                break
    point = {
        "name": "剖面选点",
        "lat": lat,
        "lon": lon,
        "elevation_m": bounded_float(
            payload.get("elevation_m"), "海拔", -500.0, 9000.0
        ),
        "timezone_name": timezone_name,
    }
    return build_national_cross_section(source, point, offset_minutes)


def _ecmwf_cross_section_from_provenance(
    provenance: dict[str, Any],
    point: dict[str, Any],
    offset_minutes: float,
) -> dict[str, Any]:
    """Build an EC-only cross-section from the strict derived profile cache."""
    profile_path = provenance.get("layer_profile_path")
    pressure_levels = provenance.get("pressure_levels")
    profile_grid = provenance.get("profile_grid")
    if (
        not isinstance(profile_path, str)
        or not profile_path
        or not isinstance(pressure_levels, list)
        or not isinstance(profile_grid, dict)
    ):
        raise RuntimeError("EC 压力层剖面缓存元数据不完整，请重新运行预测。")
    source = {
        "model_source": "ec",
        "layer_profile_path": profile_path,
        "pressure_levels": pressure_levels,
        "hours": provenance.get("forecast_hours"),
        "target_date": provenance.get("target_date"),
        "phenomenon": provenance.get("phenomenon"),
        "cycle_utc": provenance.get("cycle_utc"),
        "lat0": profile_grid.get("lat0"),
        "lon0": profile_grid.get("lon0"),
        "rows": profile_grid.get("rows"),
        "cols": profile_grid.get("cols"),
        "spacing": profile_grid.get("spacing"),
    }
    result = build_national_cross_section(source, point, offset_minutes)
    result["model_source"] = "ec"
    result["terrain_source"] = "EC IFS surface geopotential"
    return result


def read_ecmwf_point_cross_section(
    payload: dict[str, Any],
    cache_dir: str | Path,
    lat: float,
    lon: float,
    offset_minutes: float,
) -> dict[str, Any]:
    """Read an EC point response and expose its own pressure profile."""
    profile_key = str(payload.get("profile_key") or "")
    match = re.fullmatch(r"ec:([0-9a-f]{64})", profile_key)
    if match is None:
        raise ValueError("EC 压力层缓存标识无效。")
    target_date = date.fromisoformat(str(payload.get("target_date") or ""))
    phenomenon = str(payload.get("phenomenon") or "dusk")
    response_path = _ecmwf_point_response_path(
        Path(cache_dir), match.group(1), target_date, phenomenon
    )
    if not response_path.is_file():
        raise FileNotFoundError("暂无匹配的 EC 压力层缓存，请重新运行 EC 预测。")
    try:
        with gzip.open(response_path, "rt", encoding="utf-8") as handle:
            response = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("EC 压力层响应缓存已损坏，请重新运行 EC 预测。") from exc
    metadata = response.get("response_cache")
    provenance = response.get("ecmwf_provenance")
    if (
        not isinstance(metadata, dict)
        or not str(metadata.get("identity_key") or "").endswith(match.group(1))
        or not isinstance(provenance, dict)
        or provenance.get("model_source") != "ec"
    ):
        raise RuntimeError("EC 压力层响应缓存身份不匹配，请重新运行 EC 预测。")
    point = {
        "name": "EC 剖面选点",
        "lat": lat,
        "lon": lon,
        "elevation_m": bounded_float(payload.get("elevation_m"), "海拔", -500.0, 9000.0),
        "timezone_name": point_timezone(lat, lon),
    }
    enriched = dict(provenance)
    enriched["target_date"] = target_date.isoformat()
    enriched["phenomenon"] = phenomenon
    return _ecmwf_cross_section_from_provenance(enriched, point, offset_minutes)


def write_national_forecast_cache(
    request_payload: dict[str, Any],
    result: dict[str, Any],
    cache_dir: str | Path,
) -> dict[str, Any]:
    cache_key = national_forecast_cache_key(request_payload)
    generated_at = datetime.now(timezone.utc).isoformat()
    cached = copy.deepcopy(result)
    cached["algorithm"] = NATIONAL_BATCH_ALGORITHM
    cached["cache"] = {
        "status": "refresh",
        "key": cache_key,
        "generated_at_utc": generated_at,
        "version": NATIONAL_CACHE_VERSION,
    }
    target = national_forecast_cache_path(
        cache_dir,
        cache_key,
        request_payload.get("target_date"),
        request_payload.get("phenomenon"),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(cached, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    temporary = target.with_suffix(target.suffix + f".tmp-{uuid.uuid4().hex}")
    try:
        with gzip.open(temporary, "wb", compresslevel=3) as handle:
            handle.write(encoded)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    if (
        str(cached.get("model_source") or "gfs").lower() == "ec"
        and cached.get("success_count") == cached.get("point_count")
        and cached.get("error_count") == 0
    ) or (
        cached.get("point_mode") == "generated-regional-grid"
        and cached.get("regions") == ["china"]
        and cached.get("point_count") == CHINA_PRODUCTION_POINT_COUNT
        and cached.get("success_count") == CHINA_PRODUCTION_POINT_COUNT
        and cached.get("error_count") == 0
    ):
        write_national_forecast_summary(
            request_payload,
            cached,
            cache_dir,
        )
    return cached


def _validate_national_public_point_value(field: str, value: Any, *, row: int) -> None:
    if field in NATIONAL_PUBLIC_POINT_TEXT_FIELDS:
        if not isinstance(value, str) or not value:
            raise ValueError(f"array-v6 row {row} has invalid {field}")
        return
    if field == "dominant_burnable_pressure_family":
        if value not in {None, "high", "middle", "low"}:
            raise ValueError(f"array-v6 row {row} has invalid {field}")
        return
    if field == "best_time_local":
        if value is not None:
            try:
                parse_zoned_iso_datetime(value, f"array-v6 row {row} {field}")
            except ValueError as exc:
                raise ValueError(f"array-v6 row {row} has invalid {field}") from exc
        return
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"array-v6 row {row} has invalid {field}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"array-v6 row {row} has non-finite {field}")
    bounds: tuple[float, float] | None = None
    if field == "lat":
        bounds = (-90.0, 90.0)
    elif field == "lon":
        bounds = (-180.0, 180.0)
    elif field == "observer_elevation_asl_m":
        bounds = (-500.0, 9000.0)
    elif field in {"quality_index", "peak_quality_index"}:
        bounds = (0.0, 2.5)
    elif field in {
        "confidence_score",
        "burnable_cloud_coverage_effective",
        "forward_view_cloud_field_amount_peak",
        "forward_burnable_cloud_field_amount_peak",
        "forward_burnable_cloud_ratio_peak",
        "incoming_aod_illumination_factor",
        "observer_aod_contrast_factor",
        "observer_return_transmission_mean",
        "high_cloud",
        "mid_cloud",
        "low_cloud",
    }:
        bounds = (0.0, 1.0)
    elif field in {
        "burnable_duration_minutes",
        "local_aod550",
        "quality_aod550",
        "observer_return_optical_depth_mean",
    }:
        bounds = (0.0, float("inf"))
    elif field in {"incoming_aod_adjustment", "observer_aod_adjustment"}:
        bounds = (-2.5, 2.5)
    if bounds is not None and not bounds[0] <= number <= bounds[1]:
        raise ValueError(f"array-v6 row {row} has out-of-range {field}")


def _regional_data_provenance(
    cached: dict[str, Any],
    *,
    requested_gfs_cycle_utc: str | None = None,
    stale: bool = False,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Extract source times from the immutable full regional cache manifest.

    A missing manifest is represented as incomplete with empty source arrays. It
    is never converted into guessed GFS/CAMS timestamps, and incomplete data is
    not eligible for stale serving.
    """
    try:
        served_cycle = parse_utc_iso_datetime(
            cached["cycle_time_utc"], "regional served GFS cycle"
        )
        requested_cycle = parse_utc_iso_datetime(
            requested_gfs_cycle_utc or cached["cycle_time_utc"],
            "regional requested GFS cycle",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("regional provenance GFS cycle is invalid") from exc

    forecast_hours: list[int] = []
    gfs_valid_times: list[str] = []
    cams_run_times: list[str] = []
    cams_valid_times: list[str] = []
    inputs_complete = False
    manifest = cached.get("input_manifest")
    if manifest is not None:
        try:
            if not isinstance(manifest, dict):
                raise ValueError("manifest is not an object")
            model = manifest["model"]
            if (
                manifest["format"] != NATIONAL_INPUT_MANIFEST_FORMAT
                or manifest["algorithm"] != NATIONAL_BATCH_ALGORITHM
                or parse_utc_iso_datetime(
                    manifest["cycle_utc"], "regional manifest GFS cycle"
                )
                != served_cycle
                or not isinstance(model, dict)
                or model["source"] != "gfs"
            ):
                raise ValueError("manifest model identity is inconsistent")
            manifest_hours = manifest["forecast_hours"]
            model_files = model["files"]
            if (
                not isinstance(manifest_hours, list)
                or not manifest_hours
                or any(
                    isinstance(hour, bool)
                    or not isinstance(hour, int)
                    or hour < 0
                    for hour in manifest_hours
                )
                or manifest_hours != sorted(set(manifest_hours))
                or not isinstance(model_files, list)
                or len(model_files) != len(manifest_hours)
            ):
                raise ValueError("manifest GFS forecast hours are invalid")
            for hour, entry in zip(manifest_hours, model_files):
                if (
                    not isinstance(entry, dict)
                    or entry.get("forecast_hour") != hour
                    or not isinstance(entry.get("path"), str)
                    or not entry["path"]
                    or parse_utc_iso_datetime(
                        entry.get("cycle_utc"), "regional model file cycle"
                    )
                    != served_cycle
                ):
                    raise ValueError("manifest GFS file identity is invalid")
            forecast_hours = list(manifest_hours)
            gfs_valid_times = [
                (served_cycle + timedelta(hours=hour)).isoformat()
                for hour in forecast_hours
            ]

            aerosol = manifest["aerosol"]
            if (
                not isinstance(aerosol, dict)
                or set(aerosol) != {
                    "requested_source",
                    "actual_sources",
                    "files",
                    "profile_mode",
                    "pressure_levels_hpa",
                    "extinction_units",
                    "temporal_interpolation",
                    "spatial_interpolation",
                    "normalization",
                }
                or aerosol["requested_source"] not in {"auto", "cams"}
                or aerosol["actual_sources"] != ["cams"]
                or aerosol["profile_mode"] != NATIONAL_AEROSOL_PROFILE_MODE
                or aerosol["pressure_levels_hpa"]
                != list(CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS)
                or aerosol["extinction_units"] != NATIONAL_AEROSOL_EXTINCTION_UNITS
                or aerosol["temporal_interpolation"]
                != NATIONAL_AEROSOL_TEMPORAL_INTERPOLATION
                or aerosol["spatial_interpolation"]
                != NATIONAL_AEROSOL_SPATIAL_INTERPOLATION
                or aerosol["normalization"] != NATIONAL_AEROSOL_NORMALIZATION
            ):
                raise ValueError("manifest CAMS profile contract is incomplete")
            cams_files = aerosol["files"]
            if not isinstance(cams_files, list) or not cams_files:
                raise ValueError("manifest CAMS source is incomplete")
            parsed_cams: list[tuple[datetime, datetime]] = []
            for entry in cams_files:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise ValueError("manifest CAMS file identity is invalid")
                parsed = parse_cams_cache_name(Path(entry["path"]))
                if parsed is None:
                    raise ValueError("manifest CAMS filename is not parseable")
                _run_date, _run_time, lead_hours, run_utc = parsed
                parsed_cams.append((run_utc, run_utc + timedelta(hours=lead_hours)))
            cams_run_times = sorted({run.isoformat() for run, _valid in parsed_cams})
            cams_valid_times = sorted({valid.isoformat() for _run, valid in parsed_cams})
            inputs_complete = True
        except (KeyError, TypeError, ValueError) as exc:
            if require_complete:
                raise ValueError("regional stale cache source manifest is incomplete") from exc

    if require_complete and not inputs_complete:
        raise ValueError("regional stale cache source manifest is missing")
    metadata = cached.get("cache")
    generated_at = metadata.get("generated_at_utc") if isinstance(metadata, dict) else None
    try:
        generated_at = parse_utc_iso_datetime(
            generated_at, "regional cache generated_at_utc"
        ).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("regional cache generated_at_utc is invalid") from exc
    return {
        "cache_status": "stale" if stale else str(
            metadata.get("status") if isinstance(metadata, dict) else "refresh"
        ),
        "cams_source": "cams" if inputs_complete else None,
        "generated_at_utc": generated_at,
        "stale": bool(stale),
        "inputs_complete": inputs_complete,
        "requested_gfs_cycle_utc": requested_cycle.isoformat(),
        "served_gfs_cycle_utc": served_cycle.isoformat(),
        "gfs_forecast_hours": forecast_hours,
        "gfs_valid_times_utc": gfs_valid_times,
        "cams_run_times_utc": cams_run_times,
        "cams_valid_times_utc": cams_valid_times,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
    }


def _validate_national_summary_contract(summary: dict[str, Any]) -> None:
    if not isinstance(summary, dict) or set(summary) != NATIONAL_SUMMARY_FIELDS:
        raise ValueError("regional summary fields do not match summary53")
    if summary["version"] != NATIONAL_SUMMARY_VERSION:
        raise ValueError("regional summary version does not match summary53")
    if summary["algorithm"] != NATIONAL_BATCH_ALGORITHM:
        raise ValueError("regional summary algorithm is not current")
    provenance = summary.get("data_provenance")
    if not isinstance(provenance, dict) or set(provenance) != NATIONAL_SUMMARY_PROVENANCE_FIELDS:
        raise ValueError("regional summary data_provenance fields are invalid")
    for field in ("stale", "inputs_complete"):
        if not isinstance(provenance[field], bool):
            raise ValueError(f"regional summary data_provenance.{field} is invalid")
    if provenance["cache_status"] not in {"refresh", "hit", "stale"}:
        raise ValueError("regional summary data_provenance.cache_status is invalid")
    if provenance["cams_source"] not in {None, "cams"}:
        raise ValueError("regional summary data_provenance.cams_source is invalid")
    try:
        parse_utc_iso_datetime(
            provenance["generated_at_utc"],
            "regional summary data_provenance.generated_at_utc",
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("regional summary data_provenance.generated_at_utc is invalid") from exc
    if provenance["algorithm"] != NATIONAL_BATCH_ALGORITHM:
        raise ValueError("regional summary data_provenance algorithm is invalid")
    for field in ("requested_gfs_cycle_utc", "served_gfs_cycle_utc"):
        try:
            parse_utc_iso_datetime(
                provenance[field], f"regional summary data_provenance.{field}"
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"regional summary data_provenance.{field} is invalid") from exc
    for field in ("gfs_forecast_hours",):
        values = provenance[field]
        if (
            not isinstance(values, list)
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values)
            or values != sorted(set(values))
        ):
            raise ValueError(f"regional summary data_provenance.{field} is invalid")
    for field in ("gfs_valid_times_utc", "cams_run_times_utc", "cams_valid_times_utc"):
        values = provenance[field]
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"regional summary data_provenance.{field} is invalid")
        for value in values:
            try:
                parse_utc_iso_datetime(value, f"regional summary data_provenance.{field}")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"regional summary data_provenance.{field} is invalid") from exc
    if provenance["inputs_complete"] and (
        not provenance["gfs_forecast_hours"]
        or not provenance["gfs_valid_times_utc"]
        or not provenance["cams_run_times_utc"]
        or not provenance["cams_valid_times_utc"]
    ):
        raise ValueError("complete regional provenance must include GFS and CAMS times")
    if provenance["inputs_complete"] is not True and provenance["cams_source"] is not None:
        raise ValueError("incomplete regional provenance cannot claim a CAMS source")
    if summary.get("point_format") != NATIONAL_PUBLIC_POINT_FORMAT:
        raise ValueError("regional summary point_format is not array-v6")
    if summary.get("point_fields") != list(NATIONAL_PUBLIC_POINT_FIELDS):
        raise ValueError("regional summary point_fields do not match array-v6")
    points = summary.get("points")
    if not isinstance(points, list):
        raise ValueError("regional summary points must be an array")
    for row_index, row in enumerate(points):
        if not isinstance(row, list) or len(row) != len(NATIONAL_PUBLIC_POINT_FIELDS):
            raise ValueError(f"array-v6 row {row_index} has invalid shape")
        for field, value in zip(NATIONAL_PUBLIC_POINT_FIELDS, row):
            _validate_national_public_point_value(field, value, row=row_index)
    for field in NATIONAL_SUMMARY_REQUIRED_SOURCE_FIELDS:
        if field == "points":
            continue
        if field not in summary:
            raise ValueError(f"regional summary is missing {field}")
    for field in ("point_count", "success_count", "error_count"):
        value = summary[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"regional summary has invalid {field}")
    if summary["success_count"] != len(points):
        raise ValueError("regional summary success_count does not match points")
    if summary["point_count"] != summary["success_count"] + summary["error_count"]:
        raise ValueError("regional summary point_count does not match outcomes")
    if summary["scope"] != "regional":
        raise ValueError("regional summary scope is invalid")
    if summary["point_mode"] not in {
        "explicit-single-points",
        "generated-regional-grid",
    }:
        raise ValueError("regional summary point_mode is invalid")
    if summary["phenomenon"] not in {"dawn", "dusk"}:
        raise ValueError("regional summary phenomenon is invalid")
    if summary["model_source"] != "gfs":
        raise ValueError("regional summary model_source is invalid")
    if summary["forecast_mode"] not in {"model", "nowcast"}:
        raise ValueError("regional summary forecast_mode is invalid")
    if summary["compute_backend_requested"] not in {"auto", "cpu", "gpu"}:
        raise ValueError("regional summary compute_backend_requested is invalid")
    if summary["compute_backend"] not in {"cpu", "gpu", "hybrid"}:
        raise ValueError("regional summary compute_backend is invalid")
    regions = summary["regions"]
    coverage = summary["coverage"]
    valid_region_codes = {*REGIONAL_FORECAST_AREAS, "custom"}
    if (
        not isinstance(regions, list)
        or not regions
        or any(
            not isinstance(code, str) or code not in valid_region_codes
            for code in regions
        )
        or len(set(regions)) != len(regions)
        or not isinstance(coverage, list)
        or len(coverage) != len(regions)
    ):
        raise ValueError("regional summary regions/coverage are invalid")
    coverage_codes: list[str] = []
    for coverage_index, area in enumerate(coverage):
        if not isinstance(area, dict) or set(area) != NATIONAL_SUMMARY_COVERAGE_FIELDS:
            raise ValueError(
                f"regional summary coverage {coverage_index} has invalid fields"
            )
        code = area["code"]
        if not isinstance(code, str) or code not in valid_region_codes:
            raise ValueError(
                f"regional summary coverage {coverage_index} has invalid code"
            )
        coverage_codes.append(code)
        for field in ("label", "point_prefix"):
            if not isinstance(area[field], str) or not area[field]:
                raise ValueError(
                    f"regional summary coverage {coverage_index} has invalid {field}"
                )
        timezone_name = area["timezone_name"]
        if code in {"china", "custom"}:
            if not isinstance(timezone_name, str) or not timezone_name:
                raise ValueError(
                    f"regional summary coverage {coverage_index} requires timezone_name"
                )
            try:
                ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(
                    f"regional summary coverage {coverage_index} has invalid timezone_name"
                ) from exc
        elif timezone_name is not None:
            raise ValueError(
                f"regional summary coverage {coverage_index} must use per-point timezones"
            )
        bounds = area["bounds"]
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError(
                f"regional summary coverage {coverage_index} has invalid bounds"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
            for value in bounds
        ):
            raise ValueError(
                f"regional summary coverage {coverage_index} has invalid bounds"
            )
        west, south, east, north = (float(value) for value in bounds)
        if (
            not -180.0 <= west < east <= 180.0
            or not -90.0 <= south < north <= 90.0
        ):
            raise ValueError(
                f"regional summary coverage {coverage_index} has invalid bounds"
            )
    if coverage_codes != regions:
        raise ValueError("regional summary coverage does not match regions")
    for field in ("cycle_time_utc", "cache_recommendation"):
        if not isinstance(summary[field], str) or not summary[field]:
            raise ValueError(f"regional summary {field} is invalid")
    try:
        parse_iso_date(summary["target_date"], "regional summary target_date")
        parse_utc_iso_datetime(
            summary["cycle_time_utc"],
            "regional summary cycle_time_utc",
        )
        if parse_utc_iso_datetime(
            provenance["served_gfs_cycle_utc"],
            "regional summary data_provenance.served_gfs_cycle_utc",
        ) != parse_utc_iso_datetime(
            summary["cycle_time_utc"], "regional summary cycle_time_utc"
        ):
            raise ValueError("regional summary served GFS cycle does not match cycle_time_utc")
    except (AttributeError, ValueError) as exc:
        raise ValueError("regional summary target/cycle time is invalid") from exc

    precision = summary["precision"]
    if not isinstance(precision, dict) or set(precision) != NATIONAL_FRAME_PRECISION_FIELDS:
        raise ValueError("regional summary precision fields are invalid")
    if not isinstance(precision["mode"], str) or not precision["mode"]:
        raise ValueError("regional summary precision.mode is invalid")
    point_spacing = precision["point_spacing_deg"]
    numeric_precision_fields = ("model_grid_spacing_deg",)
    if point_spacing is not None:
        numeric_precision_fields = ("point_spacing_deg", *numeric_precision_fields)
    for field in numeric_precision_fields:
        value = precision[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
            or float(value) <= 0.0
        ):
            raise ValueError(f"regional summary precision.{field} is invalid")
    for field in ("point_count", "processed_sample_count", "candidate_limit_per_sample"):
        value = precision[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"regional summary precision.{field} is invalid")
    if precision["point_count"] != summary["point_count"]:
        raise ValueError("regional summary precision.point_count does not match point_count")
    for field in ("vertical_profiles", "full_point_coverage"):
        if not isinstance(precision[field], bool):
            raise ValueError(f"regional summary precision.{field} is invalid")
    if precision["aerosol_profile_mode"] != NATIONAL_AEROSOL_PROFILE_MODE:
        raise ValueError(
            "regional summary precision.aerosol_profile_mode does not match current contract"
        )
    if regions == ["china"] and (
        summary["point_mode"] != "generated-regional-grid"
        or summary["model_source"] != "gfs"
        or point_spacing != 0.25
        or float(precision["model_grid_spacing_deg"]) != 0.25
        or summary["point_count"] != CHINA_PRODUCTION_POINT_COUNT
        or summary["success_count"] != CHINA_PRODUCTION_POINT_COUNT
        or len(points) != CHINA_PRODUCTION_POINT_COUNT
        or summary["error_count"] != 0
        or precision["full_point_coverage"] is not True
    ):
        raise ValueError(
            "China production summary requires a complete 0.25-degree 36,685-point grid"
        )

    timing = summary["timing"]
    if not isinstance(timing, dict) or set(timing) != NATIONAL_SUMMARY_TIMING_FIELDS:
        raise ValueError("regional summary timing fields are invalid")
    for field, value in timing.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise ValueError(f"regional summary timing.{field} is invalid")

    cache = summary["cache"]
    if not isinstance(cache, dict) or set(cache) != NATIONAL_SUMMARY_CACHE_FIELDS:
        raise ValueError("regional summary cache fields are invalid")
    if cache["status"] not in {"refresh", "hit", "stale"}:
        raise ValueError("regional summary cache.status is invalid")
    if cache["status"] != provenance["cache_status"]:
        raise ValueError("regional summary cache status does not match data_provenance")
    if (cache["status"] == "stale") != provenance["stale"]:
        raise ValueError("regional summary stale status does not match data_provenance")
    if not isinstance(cache["key"], str) or not cache["key"]:
        raise ValueError("regional summary cache.key is invalid")
    if cache["version"] != NATIONAL_CACHE_VERSION:
        raise ValueError("regional summary cache.version is invalid")
    try:
        parse_utc_iso_datetime(
            cache["generated_at_utc"],
            "regional summary cache.generated_at_utc",
        )
    except ValueError as exc:
        raise ValueError("regional summary cache.generated_at_utc is invalid") from exc
    errors = summary["errors"]
    if not isinstance(errors, list) or len(errors) != summary["error_count"]:
        raise ValueError("regional summary errors do not match error_count")
    for error_index, error in enumerate(errors):
        if not isinstance(error, dict) or set(error) != NATIONAL_SUMMARY_ERROR_FIELDS:
            raise ValueError(f"regional summary error {error_index} has invalid fields")
        if not isinstance(error["name"], str) or not error["name"]:
            raise ValueError(f"regional summary error {error_index} has invalid name")
        if not isinstance(error["error"], str) or not error["error"]:
            raise ValueError(f"regional summary error {error_index} has invalid error")
        for field in ("lat", "lon"):
            value = error[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(float(value))
            ):
                raise ValueError(
                    f"regional summary error {error_index} has invalid {field}"
                )
        if not -90.0 <= float(error["lat"]) <= 90.0:
            raise ValueError(
                f"regional summary error {error_index} has invalid lat"
            )
        if not -180.0 <= float(error["lon"]) <= 180.0:
            raise ValueError(
                f"regional summary error {error_index} has invalid lon"
            )


def _national_forecast_summary(
    cached: dict[str, Any],
    *,
    requested_gfs_cycle_utc: str | None = None,
    stale: bool = False,
    require_complete_provenance: bool = False,
) -> dict[str, Any]:
    """Build the strict bandwidth-limited payload used by public regional views."""
    missing = [field for field in NATIONAL_SUMMARY_REQUIRED_SOURCE_FIELDS if field not in cached]
    if missing:
        raise ValueError(f"current regional result is missing {', '.join(missing)}")
    source_points = cached["points"]
    if not isinstance(source_points, list):
        raise ValueError("current regional result points must be an array")
    points: list[list[Any]] = []
    for point_index, point in enumerate(source_points):
        if not isinstance(point, dict):
            raise ValueError(f"current regional point {point_index} must be an object")
        quality = point.get("quality_components")
        if not isinstance(quality, dict):
            raise ValueError(
                f"current regional point {point_index} is missing quality_components"
            )
        row: list[Any] = []
        for field in NATIONAL_PUBLIC_POINT_FIELDS:
            component_field = NATIONAL_PUBLIC_POINT_COMPONENT_FIELDS.get(field)
            source = quality if component_field is not None else point
            source_field = component_field or field
            if source_field not in source:
                raise ValueError(
                    f"current regional point {point_index} is missing {source_field}"
                )
            value = source[source_field]
            _validate_national_public_point_value(field, value, row=point_index)
            if field in {"lat", "lon"}:
                value = round(float(value), 2)
            elif field == "observer_elevation_asl_m":
                value = float(value)
            elif field == "burnable_duration_minutes":
                value = round(float(value), 1)
            elif field not in (
                NATIONAL_PUBLIC_POINT_TEXT_FIELDS
                | NATIONAL_PUBLIC_POINT_NULLABLE_FIELDS
            ):
                value = round(float(value), 3)
            row.append(value)
        points.append(row)
    summary = {
        field: cached[field]
        for field in NATIONAL_SUMMARY_REQUIRED_SOURCE_FIELDS
        if field != "points"
    }
    summary["version"] = NATIONAL_SUMMARY_VERSION
    summary["point_format"] = NATIONAL_PUBLIC_POINT_FORMAT
    summary["point_fields"] = list(NATIONAL_PUBLIC_POINT_FIELDS)
    summary["points"] = points
    summary["data_provenance"] = _regional_data_provenance(
        cached,
        requested_gfs_cycle_utc=requested_gfs_cycle_utc,
        stale=stale,
        require_complete=require_complete_provenance,
    )
    if stale:
        summary["cache"]["status"] = "stale"
    _validate_national_summary_contract(summary)
    return summary


EXTERNAL_NATIONAL_SUMMARY_VERSION = 1
EXTERNAL_NATIONAL_SUMMARY_FIELDS = frozenset({
    "version", "algorithm", "scope", "point_mode", "regions", "coverage",
    "target_date", "phenomenon", "model_source", "model_resolution",
    "cycle_time_utc", "forecast_hours", "points", "point_fields",
    "point_format", "point_count", "success_count", "error_count", "errors",
    "precision", "timing", "cache", "data_provenance",
})


def _external_national_forecast_summary(cached: dict[str, Any]) -> dict[str, Any]:
    """Project the strict EC result into the public regional summary contract."""
    if cached.get("model_source") != "ec":
        raise ValueError("external regional summary requires EC source")
    if cached.get("algorithm") != NATIONAL_BATCH_ALGORITHM:
        raise ValueError("external regional summary algorithm is not current")
    source_points = cached.get("points")
    if not isinstance(source_points, list):
        raise ValueError("external regional result points must be an array")
    points: list[list[Any]] = []
    for point_index, point in enumerate(source_points):
        if not isinstance(point, dict) or not isinstance(point.get("quality_components"), dict):
            raise ValueError(f"external regional point {point_index} is incomplete")
        quality = point["quality_components"]
        row: list[Any] = []
        for field in NATIONAL_PUBLIC_POINT_FIELDS:
            component_field = NATIONAL_PUBLIC_POINT_COMPONENT_FIELDS.get(field)
            source = quality if component_field is not None else point
            source_field = component_field or field
            if source_field not in source:
                raise ValueError(f"external regional point {point_index} is missing {source_field}")
            value = source[source_field]
            _validate_national_public_point_value(field, value, row=point_index)
            if field in {"lat", "lon"}:
                value = round(float(value), 2)
            elif field == "observer_elevation_asl_m":
                value = float(value)
            elif field == "burnable_duration_minutes":
                value = round(float(value), 1)
            elif field not in (NATIONAL_PUBLIC_POINT_TEXT_FIELDS | NATIONAL_PUBLIC_POINT_NULLABLE_FIELDS):
                value = round(float(value), 3)
            row.append(value)
        points.append(row)
    manifest = cached.get("input_manifest")
    if not isinstance(manifest, dict) or manifest.get("model", {}).get("source") != cached["model_source"]:
        raise ValueError("external regional summary input manifest is incomplete")
    forecast_hours = manifest.get("forecast_hours")
    if not isinstance(forecast_hours, list) or not forecast_hours:
        raise ValueError("external regional summary forecast hours are incomplete")
    model = manifest["model"]
    resolution = model.get("resolution")
    if resolution != "0p25":
        raise ValueError("external regional summary model resolution is invalid")
    cams_files = cached.get("input_manifest", {}).get("aerosol", {}).get("files")
    provenance = {
        "model_source": cached["model_source"],
        "cycle_utc": cached["cycle_time_utc"],
        "forecast_hours": list(forecast_hours),
        "model_files": [entry["path"] for entry in model.get("files", [])],
        "cams_files": [entry["path"] for entry in cams_files] if isinstance(cams_files, list) else [],
    }
    summary = {
        "version": EXTERNAL_NATIONAL_SUMMARY_VERSION,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "scope": cached["scope"],
        "point_mode": cached["point_mode"],
        "regions": cached["regions"],
        "coverage": cached["coverage"],
        "target_date": cached["target_date"],
        "phenomenon": cached["phenomenon"],
        "model_source": cached["model_source"],
        "model_resolution": resolution,
        "cycle_time_utc": cached["cycle_time_utc"],
        "forecast_hours": list(forecast_hours),
        "points": points,
        "point_fields": list(NATIONAL_PUBLIC_POINT_FIELDS),
        "point_format": NATIONAL_PUBLIC_POINT_FORMAT,
        "point_count": cached["point_count"],
        "success_count": cached["success_count"],
        "error_count": cached["error_count"],
        "errors": cached["errors"],
        "precision": cached["precision"],
        "timing": cached["timing"],
        "cache": cached["cache"],
        "data_provenance": provenance,
    }
    if set(summary) != EXTERNAL_NATIONAL_SUMMARY_FIELDS:
        raise ValueError("external regional summary fields are invalid")
    if summary["success_count"] != len(points) or summary["point_count"] != summary["success_count"] + summary["error_count"]:
        raise ValueError("external regional summary counts are inconsistent")
    return summary


def _validate_external_national_summary_contract(summary: dict[str, Any]) -> None:
    if not isinstance(summary, dict) or set(summary) != EXTERNAL_NATIONAL_SUMMARY_FIELDS:
        raise ValueError("external regional summary fields are invalid")
    if summary["version"] != EXTERNAL_NATIONAL_SUMMARY_VERSION:
        raise ValueError("external regional summary version is invalid")
    if summary["algorithm"] != NATIONAL_BATCH_ALGORITHM:
        raise ValueError("external regional summary algorithm is invalid")
    if summary["model_source"] != "ec" or summary["model_resolution"] != "0p25":
        raise ValueError("external regional summary model identity is invalid")
    if summary["point_format"] != NATIONAL_PUBLIC_POINT_FORMAT or summary["point_fields"] != list(NATIONAL_PUBLIC_POINT_FIELDS):
        raise ValueError("external regional summary point contract is invalid")
    if not isinstance(summary["forecast_hours"], list) or not summary["forecast_hours"] or summary["forecast_hours"] != sorted(set(summary["forecast_hours"])):
        raise ValueError("external regional summary forecast hours are invalid")
    if summary["success_count"] != len(summary["points"]):
        raise ValueError("external regional summary success count is invalid")
    for row_index, row in enumerate(summary["points"]):
        if not isinstance(row, list) or len(row) != len(NATIONAL_PUBLIC_POINT_FIELDS):
            raise ValueError(f"external regional summary row {row_index} is invalid")
        for field, value in zip(NATIONAL_PUBLIC_POINT_FIELDS, row):
            _validate_national_public_point_value(field, value, row=row_index)
    provenance = summary["data_provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"model_source", "cycle_utc", "forecast_hours", "model_files", "cams_files"}:
        raise ValueError("external regional summary provenance is invalid")
    if provenance["model_source"] != summary["model_source"] or provenance["forecast_hours"] != summary["forecast_hours"]:
        raise ValueError("external regional summary provenance is inconsistent")
    parse_utc_iso_datetime(summary["cycle_time_utc"], "external regional summary cycle")
    parse_utc_iso_datetime(provenance["cycle_utc"], "external regional summary provenance cycle")
    if provenance["cycle_utc"] != summary["cycle_time_utc"] or not isinstance(provenance["model_files"], list) or not provenance["model_files"] or not isinstance(provenance["cams_files"], list) or not provenance["cams_files"]:
        raise ValueError("external regional summary provenance is incomplete")


def write_national_forecast_summary(
    request_payload: dict[str, Any],
    cached: dict[str, Any],
    cache_dir: str | Path,
) -> Path:
    cache_key = national_forecast_cache_key(request_payload)
    target = national_forecast_summary_path(
        cache_dir,
        cache_key,
        request_payload.get("target_date"),
        request_payload.get("phenomenon"),
    )
    summary = (
        _external_national_forecast_summary(cached)
        if str(cached.get("model_source") or "gfs").lower() == "ec"
        else _national_forecast_summary(cached)
    )
    _write_national_summary_document(target, summary)
    return target


def _write_national_summary_document(
    target: Path,
    summary: dict[str, Any],
) -> Path:
    """Atomically persist and index a previously validated public summary."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + f".tmp-{uuid.uuid4().hex}")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as handle:
            json.dump(
                summary,
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    _cache_national_summary_document(target, summary)
    return target


def _read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("缓存根节点必须是 JSON 对象。")
    return value


# Summary documents are the bandwidth-limited regional response (about 2 MB
# compressed for China's 36,685 points).  Keep only a few immutable decoded
# documents in this process.  Full regional results are deliberately excluded:
# they are tens of megabytes compressed and are used only for one-time
# summary materialization.
_NATIONAL_SUMMARY_DOCUMENT_CACHE_LIMIT = 8
_NATIONAL_SUMMARY_DOCUMENT_CACHE: OrderedDict[
    tuple[str, int, int], dict[str, Any]
] = OrderedDict()
_NATIONAL_SUMMARY_VALIDATION_CACHE: OrderedDict[tuple[str, int, int], None] = OrderedDict()
_NATIONAL_SUMMARY_DOCUMENT_CACHE_LOCK = threading.Lock()


def _summary_document_cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def _cache_national_summary_document(path: Path, summary: dict[str, Any]) -> None:
    key = _summary_document_cache_key(path)
    if key is None:
        return
    with _NATIONAL_SUMMARY_DOCUMENT_CACHE_LOCK:
        # Do not retain an old version of the same file after an atomic replace.
        for old_key in tuple(_NATIONAL_SUMMARY_DOCUMENT_CACHE):
            if old_key[0] == key[0] and old_key != key:
                _NATIONAL_SUMMARY_DOCUMENT_CACHE.pop(old_key, None)
                _NATIONAL_SUMMARY_VALIDATION_CACHE.pop(old_key, None)
        _NATIONAL_SUMMARY_DOCUMENT_CACHE[key] = summary
        _NATIONAL_SUMMARY_DOCUMENT_CACHE.move_to_end(key)
        while len(_NATIONAL_SUMMARY_DOCUMENT_CACHE) > _NATIONAL_SUMMARY_DOCUMENT_CACHE_LIMIT:
            evicted_key, _ = _NATIONAL_SUMMARY_DOCUMENT_CACHE.popitem(last=False)
            _NATIONAL_SUMMARY_VALIDATION_CACHE.pop(evicted_key, None)


def _read_cached_national_summary_document(path: Path) -> dict[str, Any]:
    key = _summary_document_cache_key(path)
    if key is None:
        raise FileNotFoundError(path)
    with _NATIONAL_SUMMARY_DOCUMENT_CACHE_LOCK:
        cached = _NATIONAL_SUMMARY_DOCUMENT_CACHE.get(key)
        if cached is not None:
            _NATIONAL_SUMMARY_DOCUMENT_CACHE.move_to_end(key)
            return cached
    summary = _read_gzip_json(path)
    _cache_national_summary_document(path, summary)
    return summary


def _validate_cached_national_summary_document(
    path: Path,
    summary: dict[str, Any],
) -> None:
    """Validate one immutable summary at most once per file fingerprint."""
    key = _summary_document_cache_key(path)
    if key is not None:
        with _NATIONAL_SUMMARY_DOCUMENT_CACHE_LOCK:
            if key in _NATIONAL_SUMMARY_VALIDATION_CACHE:
                _NATIONAL_SUMMARY_VALIDATION_CACHE.move_to_end(key)
                return
    if str(summary.get("model_source") or "gfs").lower() == "ec":
        _validate_external_national_summary_contract(summary)
    else:
        _validate_national_summary_contract(summary)
    if key is None:
        return
    with _NATIONAL_SUMMARY_DOCUMENT_CACHE_LOCK:
        _NATIONAL_SUMMARY_VALIDATION_CACHE[key] = None
        _NATIONAL_SUMMARY_VALIDATION_CACHE.move_to_end(key)
        while len(_NATIONAL_SUMMARY_VALIDATION_CACHE) > _NATIONAL_SUMMARY_DOCUMENT_CACHE_LIMIT:
            _NATIONAL_SUMMARY_VALIDATION_CACHE.popitem(last=False)


def _summary_response_copy(summary: dict[str, Any]) -> dict[str, Any]:
    """Copy mutable summary metadata while sharing the immutable point rows."""
    response = dict(summary)
    cache = summary.get("cache")
    if isinstance(cache, dict):
        response["cache"] = dict(cache)
    provenance = summary.get("data_provenance")
    if isinstance(provenance, dict):
        response["data_provenance"] = dict(provenance)
    return response


def _national_cache_cycle_matches_request(
    cached: dict[str, Any],
    payload: dict[str, Any],
    *,
    identity: dict[str, Any] | None = None,
) -> bool:
    """Require a cache to belong to the exact model cycle in its cache key."""
    try:
        identity = identity or national_forecast_cache_identity(payload)
        expected = parse_utc_iso_datetime(
            identity["model_cycle"], "national cache identity model_cycle"
        )
        actual = parse_utc_iso_datetime(
            cached["cycle_time_utc"], "national cache cycle_time_utc"
        )
    except (KeyError, TypeError, ValueError):
        return False
    return actual == expected


def read_national_forecast_cache(
    payload: dict[str, Any],
    cache_dir: str | Path,
    *,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(payload)
    if not str(payload.get("target_date") or "").strip():
        payload["target_date"] = china_today().isoformat()
    requested_identity = identity or national_forecast_cache_identity(payload)
    cache_key = _national_forecast_cache_key_from_identity(requested_identity)
    target = national_forecast_cache_path(
        cache_dir,
        cache_key,
        payload.get("target_date"),
        payload.get("phenomenon"),
    )
    if not target.is_file():
        national_forecast_cache_path(cache_dir, cache_key).unlink(missing_ok=True)
        raise FileNotFoundError("暂无匹配的区域预测缓存，请先从后端运行一次全国计算。")
    try:
        cached = _read_gzip_json(target)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("区域预测缓存损坏，请重新生成。") from exc
    metadata = cached.get("cache")
    if (
        not isinstance(metadata, dict)
        or metadata.get("version") != NATIONAL_CACHE_VERSION
        or str(cached.get("algorithm") or "") != NATIONAL_BATCH_ALGORITHM
    ):
        raise RuntimeError("区域预测缓存版本不兼容，请重新生成。")
    if not _national_cache_cycle_matches_request(
        cached, payload, identity=requested_identity
    ):
        raise RuntimeError("区域预测缓存不是当前模型周期，请重新生成。")
    metadata["status"] = "hit"
    return cached


def _read_latest_complete_stale_regional_cache(
    payload: dict[str, Any],
    cache_dir: str | Path,
    requested_cycle: datetime,
    *,
    requested_identity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Find the newest complete same-identity cache strictly before a cycle."""
    requested_identity = requested_identity or national_forecast_cache_identity(payload)
    results_dir = dated_directory(
        cache_dir,
        str(payload["target_date"]),
        storage_phenomenon(str(payload.get("phenomenon") or "dusk")),
        "results",
    )
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for path in results_dir.glob(f"regional-forecast-v{NATIONAL_CACHE_VERSION}-*.json.gz"):
        try:
            cached = _read_gzip_json(path)
            metadata = cached.get("cache")
            served_cycle = parse_utc_iso_datetime(
                cached["cycle_time_utc"], "regional stale cache cycle"
            )
            if (
                not isinstance(metadata, dict)
                or metadata.get("version") != NATIONAL_CACHE_VERSION
                or metadata.get("status") not in {"refresh", "hit"}
                or metadata.get("key") != path.name.removeprefix(
                    f"regional-forecast-v{NATIONAL_CACHE_VERSION}-"
                ).removesuffix(".json.gz")
                or str(cached.get("algorithm") or "") != NATIONAL_BATCH_ALGORITHM
                or cached.get("target_date") != payload["target_date"]
                or cached.get("phenomenon") != payload.get("phenomenon", "dusk")
                or served_cycle >= requested_cycle
                or cached.get("error_count") != 0
                or cached.get("success_count") != cached.get("point_count")
                or not isinstance(cached.get("points"), list)
                or len(cached["points"]) != cached["success_count"]
            ):
                continue
            served_identity = dict(requested_identity)
            served_identity["model_cycle"] = served_cycle.isoformat()
            if _national_forecast_cache_key_from_identity(served_identity) != metadata["key"]:
                continue
            _regional_data_provenance(
                cached,
                requested_gfs_cycle_utc=requested_cycle.isoformat(),
                stale=True,
                require_complete=True,
            )
            candidates.append((served_cycle, cached))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _read_latest_complete_stale_regional_summary(
    payload: dict[str, Any],
    cache_dir: str | Path,
    requested_cycle: datetime,
    *,
    requested_identity: dict[str, Any] | None = None,
    requested_key: str | None = None,
) -> dict[str, Any] | None:
    """Read a complete stale regional summary without opening a full regional result.

    A summary can be keyed by the served cycle (normal materialization) or by
    the requested cycle (the one-time stale fallback materialization).  In both
    cases its actual cycle and normalized regional identity must be checked
    before it is allowed to represent a stale response.
    """
    requested_identity = requested_identity or national_forecast_cache_identity(payload)
    requested_key = requested_key or _national_forecast_cache_key_from_identity(
        requested_identity
    )
    results_dir = dated_directory(
        cache_dir,
        str(payload["target_date"]),
        storage_phenomenon(str(payload.get("phenomenon") or "dusk")),
        "results",
    )
    try:
        expected_regions = [
            str(area["code"]) for area in national_coverage_from_payload(payload)
        ]
    except (KeyError, TypeError, ValueError):
        return None
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for path in results_dir.glob(f"regional-summary-v{NATIONAL_SUMMARY_VERSION}-*.json.gz"):
        try:
            path_key = path.name.removeprefix(
                f"regional-summary-v{NATIONAL_SUMMARY_VERSION}-"
            ).removesuffix(".json.gz")
            summary = _read_cached_national_summary_document(path)
            if (
                summary.get("version") != NATIONAL_SUMMARY_VERSION
                or str(summary.get("algorithm") or "") != NATIONAL_BATCH_ALGORITHM
                or summary.get("target_date") != payload["target_date"]
                or summary.get("phenomenon") != payload.get("phenomenon", "dusk")
                or summary.get("model_source") != "gfs"
                or summary.get("regions") != expected_regions
            ):
                continue
            metadata = summary.get("cache")
            provenance = summary.get("data_provenance")
            served_cycle = parse_utc_iso_datetime(
                summary["cycle_time_utc"], "regional stale summary cycle"
            )
            if (
                not isinstance(metadata, dict)
                or metadata.get("version") != NATIONAL_CACHE_VERSION
                or metadata.get("status") not in {"refresh", "hit", "stale"}
                or served_cycle >= requested_cycle
                or not isinstance(provenance, dict)
                or provenance.get("inputs_complete") is not True
                or parse_utc_iso_datetime(
                    provenance.get("served_gfs_cycle_utc"),
                    "regional stale summary served cycle",
                )
                != served_cycle
            ):
                continue
            served_identity = dict(requested_identity)
            served_identity["model_cycle"] = served_cycle.isoformat()
            served_key = _national_forecast_cache_key_from_identity(served_identity)
            if (
                metadata.get("key") != served_key
                or path_key not in {served_key, requested_key}
            ):
                continue
            _validate_cached_national_summary_document(path, summary)
            response = _summary_response_copy(summary)
            response["cache"]["status"] = "stale"
            response["data_provenance"].update(
                {
                    "cache_status": "stale",
                    "stale": True,
                    "requested_gfs_cycle_utc": requested_cycle.isoformat(),
                    "served_gfs_cycle_utc": served_cycle.isoformat(),
                }
            )
            _validate_cached_national_summary_document(path, response)
            candidates.append((served_cycle, response))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def read_national_forecast_summary(payload: dict[str, Any], cache_dir: str | Path) -> dict[str, Any]:
    """Read current summary, or the latest complete same-identity stale cache."""
    payload = dict(payload)
    if not str(payload.get("target_date") or "").strip():
        payload["target_date"] = china_today().isoformat()
    requested_identity = national_forecast_cache_identity(payload)
    cache_key = _national_forecast_cache_key_from_identity(requested_identity)
    target = national_forecast_summary_path(
        cache_dir,
        cache_key,
        payload.get("target_date"),
        payload.get("phenomenon"),
    )
    if str(requested_identity.get("model_source") or "gfs").lower() == "ec":
        if not target.is_file():
            raise FileNotFoundError("暂无匹配的 EC 区域预测摘要，请先重新运行全国计算。")
        try:
            summary = _summary_response_copy(
                _read_cached_national_summary_document(target)
            )
            if (
                summary.get("version") != EXTERNAL_NATIONAL_SUMMARY_VERSION
                or summary.get("model_source") != requested_identity["model_source"]
                or summary.get("cache", {}).get("key") != cache_key
            ):
                raise RuntimeError("EC 区域预测摘要缓存版本不兼容，请重新生成。")
            _validate_cached_national_summary_document(target, summary)
            if not _national_cache_cycle_matches_request(
                summary, payload, identity=requested_identity
            ):
                raise RuntimeError("EC 区域预测摘要不是当前模型周期，请重新生成。")
            summary["cache"]["status"] = "hit"
            return summary
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("EC 区域预测摘要不符合当前合同，请重新生成。") from exc
    current_error: Exception
    try:
        if not target.is_file():
            # Remove only the obsolete date-less sidecar; it is never a valid
            # stale candidate because it has no target/event directory identity.
            national_forecast_summary_path(cache_dir, cache_key).unlink(missing_ok=True)
            raise FileNotFoundError("暂无匹配的区域预测摘要，请先重新运行全国计算。")
        try:
            summary = _summary_response_copy(
                _read_cached_national_summary_document(target)
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("区域预测摘要缓存损坏，请重新生成。") from exc
        if (
            summary.get("version") != NATIONAL_SUMMARY_VERSION
            or str(summary.get("algorithm") or "") != NATIONAL_BATCH_ALGORITHM
        ):
            raise RuntimeError("区域预测摘要缓存版本不兼容，请重新生成。")
        metadata = summary.get("cache")
        if (
            not isinstance(metadata, dict)
            or metadata.get("version") != NATIONAL_CACHE_VERSION
            or metadata.get("key") != cache_key
            or str(summary.get("algorithm") or "") != NATIONAL_BATCH_ALGORITHM
        ):
            raise RuntimeError("区域预测摘要缓存版本不兼容，请重新生成。")
        try:
            _validate_cached_national_summary_document(target, summary)
        except ValueError as exc:
            raise RuntimeError("区域预测摘要不符合当前 array-v6 合同，请重新生成。") from exc
        if not _national_cache_cycle_matches_request(
            summary, payload, identity=requested_identity
        ):
            raise RuntimeError("区域预测摘要不是当前模型周期，请重新生成。")
        metadata["status"] = "hit"
        summary["data_provenance"]["cache_status"] = "hit"
        return summary
    except (FileNotFoundError, RuntimeError) as exc:
        current_error = exc

    try:
        requested_cycle = parse_utc_iso_datetime(
            requested_identity["model_cycle"],
            "regional requested GFS cycle",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise current_error from exc

    # An exact full-cache is preferred over stale data, but avoid even probing
    # it when the path is absent.  That keeps the common stale-sidecar hit from
    # entering the full-cache path at all.
    current_full_path = national_forecast_cache_path(
        cache_dir,
        cache_key,
        payload.get("target_date"),
        payload.get("phenomenon"),
    )
    if current_full_path.is_file():
        try:
            current_cached = read_national_forecast_cache(
                payload,
                cache_dir,
                identity=requested_identity,
            )
        except (FileNotFoundError, RuntimeError):
            current_cached = None
        if current_cached is not None:
            try:
                summary = _national_forecast_summary(
                    current_cached,
                    requested_gfs_cycle_utc=requested_cycle.isoformat(),
                )
                # Persist the object already materialized above. Calling the
                # public writer here would decode the 59 MB full cache twice.
                _write_national_summary_document(target, summary)
                return summary
            except ValueError:
                # Preserve the existing strict stale/error behavior when the
                # full cache cannot produce a valid public summary.
                pass

    # A previous request may already have materialized a complete stale
    # summary. It is much cheaper and safer to reuse that sidecar than to
    # decompress every tens-of-megabytes full result in the directory.
    stale_summary = _read_latest_complete_stale_regional_summary(
        payload,
        cache_dir,
        requested_cycle,
        requested_identity=requested_identity,
        requested_key=cache_key,
    )
    if stale_summary is not None:
        return stale_summary

    stale_cached = _read_latest_complete_stale_regional_cache(
        payload,
        cache_dir,
        requested_cycle,
        requested_identity=requested_identity,
    )
    if stale_cached is None:
        raise current_error
    try:
        summary = _national_forecast_summary(
            stale_cached,
            requested_gfs_cycle_utc=requested_cycle.isoformat(),
            stale=True,
            require_complete_provenance=True,
        )
    except ValueError as exc:
        raise RuntimeError("区域历史摘要来源不完整，请重新运行全国计算。") from exc
    summary["cache"]["status"] = "stale"
    # Persist the public projection under the requested-cycle key.  The source
    # cache key remains in the summary metadata, while the actual served cycle
    # and complete provenance make this a strict stale candidate on the next
    # request.  This is a one-time full-cache decode per stale cycle.
    _write_national_summary_document(target, summary)
    return summary


def national_cache_payload_from_query(params: dict[str, Any]) -> dict[str, Any]:
    def first(name: str, default: str = "") -> str:
        value = params.get(name, default)
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else default
        return str(value)

    payload: dict[str, Any] = {
        "target_date": first("target_date") or china_today().isoformat(),
        "phenomenon": first("phenomenon", "dusk"),
        "model_source": first("model_source", "gfs"),
        "forecast_mode": first("forecast_mode", "model"),
        "satellite_source": first("satellite_source", "off"),
        "gfs_resolution": first("gfs_resolution", "0p25"),
        "_aerosol_source": first("aerosol_source", "cams"),
        "radius_km": first("radius_km", str(int(NATIONAL_ANALYSIS_RADIUS_KM))),
        "spacing_deg": first("spacing_deg", "2"),
        "regions": [item for item in first("regions", "east-asia").split(",") if item],
    }
    point_mode = first("point_mode")
    if point_mode:
        payload["point_mode"] = point_mode
    if first("elevation_m"):
        payload["elevation_m"] = bounded_float(first("elevation_m"), "海拔", -500.0, 9000.0)
    profile_key = first("profile_key")
    if profile_key:
        payload["profile_key"] = profile_key
    if "custom" in payload["regions"]:
        payload["custom_bounds"] = {
            "west": first("custom_west"),
            "south": first("custom_south"),
            "east": first("custom_east"),
            "north": first("custom_north"),
        }
        payload["custom_label"] = first("custom_label", "自定义区域")
        payload["custom_timezone_name"] = first("custom_timezone_name")
    return payload


def point_timezone(lat: float, lon: float) -> str:
    """Real IANA timezone for a grid point, looked up from its coordinates.

    A region (e.g. east-asia) spans several timezones, so sunrise/sunset must be
    evaluated in each point's own local time. Falls back to a longitude-based
    zone for open-ocean / polar points the timezone database does not cover.
    """
    try:
        return fetch_timezone(lat, lon)
    except Exception:
        offset = round(lon / 15.0)
        # IANA Etc/GMT uses inverted sign: UTC+8 -> Etc/GMT-8.
        sign = "-" if offset >= 0 else "+"
        return f"Etc/GMT{sign}{abs(offset)}"


_NATIONAL_POINTS_MEMO_LIMIT = 32
_NATIONAL_POINTS_MEMO: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
_NATIONAL_POINTS_MEMO_LOCK = threading.Lock()


def _national_points_memo_key(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return ""


def national_points_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    memo_key = _national_points_memo_key(payload)
    if memo_key:
        with _NATIONAL_POINTS_MEMO_LOCK:
            cached = _NATIONAL_POINTS_MEMO.get(memo_key)
            if cached is not None:
                _NATIONAL_POINTS_MEMO.move_to_end(memo_key)
                return copy.deepcopy(cached)
    points = _national_points_from_payload_uncached(payload)
    if memo_key:
        with _NATIONAL_POINTS_MEMO_LOCK:
            _NATIONAL_POINTS_MEMO[memo_key] = copy.deepcopy(points)
            _NATIONAL_POINTS_MEMO.move_to_end(memo_key)
            while len(_NATIONAL_POINTS_MEMO) > _NATIONAL_POINTS_MEMO_LIMIT:
                _NATIONAL_POINTS_MEMO.popitem(last=False)
    return points


def _national_points_from_payload_uncached(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_points = payload.get("points")
    point_mode = str(payload.get("point_mode") or "").strip().lower()
    if raw_points is not None and point_mode:
        raise ValueError("points 与 point_mode 不能同时提供。")
    if point_mode == CHINA_CITY_CATALOG_POINT_MODE:
        regions = payload.get("regions")
        if (
            not isinstance(regions, list)
            or [str(item).strip().lower() for item in regions] != ["china"]
        ):
            raise ValueError("中国城市目录模式只能用于 china 区域。")
        return [dict(point) for point in _china_city_catalog_points()]
    if point_mode:
        raise ValueError(f"未知 point_mode：{point_mode}。")
    if raw_points is not None:
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError("points 必须是非空数组。")
        points = []
        for index, item in enumerate(raw_points):
            if not isinstance(item, dict):
                raise ValueError("points 中每一项必须是对象。")
            lat = bounded_float(item.get("lat"), "纬度", -90.0, 90.0)
            lon = bounded_float(item.get("lon"), "经度", -180.0, 180.0)
            if "elevation_source" not in item:
                raise ValueError("points 中显式点必须声明 elevation_source。")
            elevation_source = str(item["elevation_source"]).strip().lower()
            if elevation_source not in {"observer", "model-grid"}:
                raise ValueError(
                    "points.elevation_source 必须是 observer 或 model-grid。"
                )
            if elevation_source == "observer":
                if "elevation_m" not in item:
                    raise ValueError("observer 显式点必须提供 elevation_m。")
                elevation_m = bounded_float(
                    item["elevation_m"], "海拔", -500.0, 9000.0
                )
            else:
                if "elevation_m" in item:
                    raise ValueError(
                        "model-grid 显式点不得携带 observer elevation_m。"
                    )
                elevation_m = 0.0
            timezone_name = str(item.get("timezone_name") or "").strip()
            if not timezone_name:
                raise ValueError("points 中显式点必须提供 timezone_name。")
            try:
                ZoneInfo(timezone_name)
            except Exception as exc:
                raise ValueError(
                    "points.timezone_name 必须是有效 IANA 时区。"
                ) from exc
            points.append({
                "name": str(item.get("name") or f"point-{index + 1}")[:120],
                "lat": lat,
                "lon": lon,
                "elevation_m": elevation_m,
                "_elevation_source": elevation_source,
                "timezone_name": timezone_name,
            })
    else:
        coverage = national_coverage_from_payload(payload)
        spacing = _generated_regional_spacing(payload, coverage)
        points = []
        seen: set[tuple[float, float]] = set()
        for area in coverage:
            west, south, east, north = area["bounds"]
            lat = south
            while lat <= north + 1e-9:
                lon = west
                while lon <= east + 1e-9:
                    key = (round(lat, 4), round(lon, 4))
                    if key not in seen:
                        seen.add(key)
                        points.append({
                            "name": f'{area["point_prefix"]}-{lat:.2f}-{lon:.2f}',
                            "lat": key[0],
                            "lon": key[1],
                            "elevation_m": 0.0,
                            "_elevation_source": "model-grid",
                            "timezone_name": str(
                                area.get("timezone_name") or point_timezone(key[0], key[1])
                            ),
                        })
                    lon += spacing
                lat += spacing
        if (
            [str(area["code"]) for area in coverage] == ["china"]
            and len(points) != CHINA_PRODUCTION_POINT_COUNT
        ):
            raise ValueError(
                "china production grid must contain exactly 36,685 points"
            )
    return points


def _national_payload_uses_explicit_points(payload: dict[str, Any]) -> bool:
    return (
        payload.get("points") is not None
        or str(payload.get("point_mode") or "").strip().lower()
        == CHINA_CITY_CATALOG_POINT_MODE
    )


def _generated_regional_spacing(
    payload: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> float:
    spacing = bounded_float(
        payload.get("spacing_deg", 2.0),
        "spacing_deg",
        0.25,
        10.0,
    )
    if [str(area["code"]) for area in coverage] == ["china"]:
        model_source = str(payload.get("model_source") or "gfs").strip().lower()
        model_resolution = str(
            payload.get("gfs_resolution") or "0p25"
        ).strip().lower()
        if (
            spacing != CHINA_PRODUCTION_SPACING_DEG
            or model_source not in {"gfs", "ec"}
            or model_resolution != "0p25"
        ):
            raise ValueError(
                "china production grid requires model_source=gfs or ec, "
                "gfs_resolution=0p25, spacing_deg=0.25, and exactly 36,685 points"
            )
    return spacing


def national_coverage_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_regions = payload.get("regions", ["east-asia"])
    if not isinstance(raw_regions, list) or not raw_regions:
        raise ValueError("regions 必须是非空数组。")
    coverage: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_code in raw_regions:
        code = str(raw_code).strip().lower()
        if code == "custom":
            bounds = payload.get("custom_bounds")
            if not isinstance(bounds, dict):
                raise ValueError("选择 custom 区域时必须提供 custom_bounds。")
            west = bounded_float(bounds.get("west"), "自定义西界", -180.0, 180.0)
            south = bounded_float(bounds.get("south"), "自定义南界", -90.0, 90.0)
            east = bounded_float(bounds.get("east"), "自定义东界", -180.0, 180.0)
            north = bounded_float(bounds.get("north"), "自定义北界", -90.0, 90.0)
            if east <= west or north <= south:
                raise ValueError("自定义范围必须满足东界大于西界、北界大于南界。")
            timezone_name = payload.get("custom_timezone_name")
            if not isinstance(timezone_name, str) or not timezone_name.strip():
                raise ValueError("选择 custom 区域时必须提供 custom_timezone_name。")
            timezone_name = timezone_name.strip()
            try:
                ZoneInfo(timezone_name)
            except Exception as exc:
                raise ValueError("custom_timezone_name 必须是有效 IANA 时区。") from exc
            area = {
                "code": "custom",
                "label": str(payload.get("custom_label") or "自定义区域")[:40],
                "bounds": [west, south, east, north],
                "timezone_name": timezone_name,
                "point_prefix": "CUSTOM",
            }
        else:
            definition = REGIONAL_FORECAST_AREAS.get(code)
            if definition is None:
                choices = "、".join([*REGIONAL_FORECAST_AREAS, "custom"])
                raise ValueError(f"未知区域 {code}；可选值为 {choices}。")
            area = {"code": code, **definition, "bounds": list(definition["bounds"])}
            area["timezone_name"] = definition.get("timezone_name")
        if code not in seen:
            coverage.append(area)
            seen.add(code)
    return coverage


def _run_national_point(
    point: dict[str, Any],
    common: dict[str, Any],
    config: WebServerConfig,
) -> dict[str, Any]:
    service = _national_forecast_service(common, config)
    return _run_national_point_with_service(point, common, service)


def _national_forecast_service(
    common: dict[str, Any],
    config: WebServerConfig,
) -> ForecastService:
    resolution = str(common.get("gfs_resolution") or "0p25")
    # Tag the GFS cache with the region code when computing a single region, so it
    # lands in the same .cache/gfs/<date>/<HHZ>/<region>/ folder the data-assets
    # page prefetches into and is reused instead of being re-downloaded.
    compute_region = common.get("_compute_region")
    return ForecastService(
        gfs_client=GfsNomadsClient(
            cache_dir=config.gfs_cache_dir,
            fixed_bbox=tuple(common["_gfs_fixed_bbox"]),
            resolution=resolution,
            region=compute_region,
            feature_cache_enabled=False,
            shared_decode_enabled=True,
            shared_decode_cache_limit=max(4, len(common.get("_gfs_forecast_hours", []))),
        ),
        aerosol_provider=resolve_aerosol_provider(
            None,
            config.aerosol_source,
            fixed_area=national_cams_area(tuple(common["_gfs_fixed_bbox"])),
            include_path_samples=False,
            cams_cache_dir=config.cams_cache_dir,
        ),
        nowcast_analyzer=JmaNowcastAnalyzer(
            calibrated_provider=_cached_satellite_frame_provider(config),
        ),
    )


def _run_national_point_with_service(
    point: dict[str, Any],
    common: dict[str, Any],
    service: ForecastService,
) -> dict[str, Any]:
    request = replace(
        forecast_request_from_payload({**common, **point}),
        include_map_cells=False,
    )
    report = service.forecast(request)
    return {
        "name": point["name"],
        "lat": point["lat"],
        "lon": point["lon"],
        "burn_score": report.burn_score,
        "overall_score": report.overall_score,
        "confidence_score": report.confidence_score,
        "best_time_local": report.best_time_local.isoformat() if report.best_time_local else None,
        "anchor_local": report.anchor_local.isoformat(),
        "near_cloud_ratio": report.nowcast.near_cloud_ratio if report.nowcast else None,
        "far_cloud_ratio": report.nowcast.far_cloud_ratio if report.nowcast else None,
        "compute_backend": report.compute_backend,
        "_diagnostic": report.to_dict(),
    }


def _prefetch_national_inputs(
    points: list[dict[str, Any]],
    common: dict[str, Any],
    config: WebServerConfig,
    progress_callback=None,
) -> None:
    model_source = str(common["model_source"])
    model_label = "GFS"
    model_grid_observer_count = sum(
        point.get("_elevation_source") == "model-grid" for point in points
    )
    common["_terrain_source"] = {
        "explicit_observer_count": len(points) - model_grid_observer_count,
        "model_grid_observer_count": model_grid_observer_count,
        "model_grid_source": None,
    }
    if model_source == "ec":
        cycle = parse_utc_iso_datetime(common["_model_cycle_override"], "_model_cycle_override")
        bbox = _external_prepared_bbox(
            model_source,
            tuple(common["_model_fixed_bbox"]),
            float(common.get("radius_km", NATIONAL_ANALYSIS_RADIUS_KM)),
        )
        common["_model_prepared_bbox"] = bbox
        _, _, _, valid, evaluation_epoch, _ = prepare_solar_event_timeline(
            points, date.fromisoformat(str(common["target_date"])), str(common["phenomenon"])
        )
        evaluation_times = [
            datetime.fromtimestamp(float(epoch), tz=timezone.utc)
            for epoch in np.asarray(evaluation_epoch)[np.asarray(valid)]
        ]
        if not evaluation_times:
            raise RuntimeError(f"{model_source.upper()} 事件窗口没有可预取的有效时次。")
        terrain_model_path: Path | None = None
        terrain_model_hour: int | None = None
        if model_source == "ec":
            from firesky.ecmwf_point import _required_model_hours
            adapter = EcmwfIfsAdapter(
                cache_dir=config.ecmwf_cache_dir,
                source=str(common.get("ecmwf_source") or "ecmwf"),
            )
            model_grid_observers = any(
                point.get("_elevation_source") == "model-grid" for point in points
            )
            forecast_hours = sorted(_required_model_hours(cycle, evaluation_times))
            cached_prepared = None
            cached_source_paths: list[Path] | None = None
            if model_grid_observers:
                # A warmed derived cache contains the exact first-frame terrain,
                # so event-hour selection can be corrected without decoding a
                # GRIB value array. Retry when that correction changes the hours.
                for _ in range(3):
                    cached_source_paths = [
                        Path(adapter.download(cycle, hour)).expanduser().resolve()
                        for hour in forecast_hours
                    ]
                    cached_prepared = _load_ec_national_prepared_cache(
                        adapter,
                        cycle,
                        forecast_hours,
                        bbox,
                        source_paths=cached_source_paths,
                        progress_callback=progress_callback,
                    )
                    if cached_prepared is None:
                        break
                    terrain_model_hour = int(forecast_hours[0])
                    terrain_model_path = cached_source_paths[0]
                    terrain_grid = SimpleNamespace(
                        latitudes=cached_prepared.latitudes,
                        longitudes=cached_prepared.longitudes,
                        fields={
                            ("surface", 0, "orog"): cached_prepared.surface_elevation_m
                        },
                    )
                    apply_decoded_model_surface_elevations(terrain_grid, points)
                    _, _, _, valid, evaluation_epoch, _ = prepare_solar_event_timeline(
                        points,
                        date.fromisoformat(str(common["target_date"])),
                        str(common["phenomenon"]),
                    )
                    evaluation_times = [
                        datetime.fromtimestamp(float(epoch), tz=timezone.utc)
                        for epoch in np.asarray(evaluation_epoch)[np.asarray(valid)]
                    ]
                    corrected_hours = sorted(_required_model_hours(cycle, evaluation_times))
                    if corrected_hours == forecast_hours:
                        common["_ec_prefetched"] = cached_prepared
                        break
                    forecast_hours = corrected_hours
                    cached_prepared = None

                if cached_prepared is None:
                    # Preserve the established terrain prepass when the strict
                    # cache is absent or stale.
                    terrain_model_hour = 0
                    terrain_model_path = adapter.download(cycle, terrain_model_hour)
                    first_tensor = adapter.load_tensor(cycle, terrain_model_hour, bbox=bbox)
                    first_grid, _, _, _ = _tensor_to_native_grid(first_tensor)
                    apply_decoded_model_surface_elevations(first_grid, points)
                    _, _, _, valid, evaluation_epoch, _ = prepare_solar_event_timeline(
                        points, date.fromisoformat(str(common["target_date"])), str(common["phenomenon"])
                    )
                    evaluation_times = [
                        datetime.fromtimestamp(float(epoch), tz=timezone.utc)
                        for epoch in np.asarray(evaluation_epoch)[np.asarray(valid)]
                    ]
                    for _ in range(3):
                        forecast_hours = sorted(_required_model_hours(cycle, evaluation_times))
                        if terrain_model_hour in forecast_hours:
                            break
                        terrain_model_hour = min(forecast_hours)
                        terrain_model_path = adapter.download(cycle, terrain_model_hour)
                        terrain_tensor = adapter.load_tensor(cycle, terrain_model_hour, bbox=bbox)
                        terrain_native_grid, _, _, _ = _tensor_to_native_grid(terrain_tensor)
                        apply_decoded_model_surface_elevations(terrain_native_grid, points)
                        _, _, _, valid, evaluation_epoch, _ = prepare_solar_event_timeline(
                            points,
                            date.fromisoformat(str(common["target_date"])),
                            str(common["phenomenon"]),
                        )
                        evaluation_times = [
                            datetime.fromtimestamp(float(epoch), tz=timezone.utc)
                            for epoch in np.asarray(evaluation_epoch)[np.asarray(valid)]
                        ]
                    else:
                        raise RuntimeError("EC 模式地形预扫时次未落入最终事件窗口。")
            else:
                forecast_hours = sorted(_required_model_hours(cycle, evaluation_times))
            ec_download_workers = int(common.get(
                "workers",
                max(1, int(os.environ.get("FIRESKY_ECMWF_DOWNLOAD_WORKERS", "4"))),
            ))

            def download_ec_hour(hour: int) -> Path:
                return adapter.download(cycle, hour)

            ec_failures: list[str] = []
            for hour, _, error in daemon_task_results(
                forecast_hours,
                download_ec_hour,
                ec_download_workers,
            ):
                if error is not None:
                    ec_failures.append(f"f{hour:03d}: {error}")
            if ec_failures:
                raise RuntimeError("ECMWF 全国预取失败：" + "; ".join(ec_failures))
        common["_model_forecast_hours"] = forecast_hours
        if model_grid_observer_count and terrain_model_path is not None:
            common["_terrain_source"]["model_grid_source"] = {
                "source": "ec-orography",
                "forecast_hour": int(terrain_model_hour),
                "path": str(terrain_model_path),
            }
        target_times = sorted({bracket for sample in evaluation_times for bracket in cams_profile_bracket_times(sample)})
        if config.aerosol_source not in {"auto", "cams"}:
            raise RuntimeError("EC 区域预测要求完整 CAMS aerosol profile。")
        cams = CamsAdsAerosolProvider(cache_dir=config.cams_cache_dir, strict=True, fixed_area=national_cams_area(bbox), auto_download=True)
        # ADS 单请求排队以分钟计：用有界并发同时提交多个 bracket 下载，
        # 并发上限由 aerosol 的下载槽信号量统一控制。
        covered = cams.download_targets_batch(
            national_cams_area(bbox),
            list(target_times),
            require_exact_valid_time=True,
        )
        # 批量校验失败/未覆盖的目标回退单点路径（自带邻近 lead 回退）。
        for target_time in target_times:
            if target_time not in covered:
                cams.download_target(
                    national_cams_area(bbox),
                    target_time,
                    require_exact_valid_time=True,
                )
        return
    cycle = parse_utc_iso_datetime(
        common["_model_cycle_override"], "_model_cycle_override"
    )
    batch_threshold = max(1, int(os.environ.get("FIRESKY_NATIONAL_BATCH_MIN_POINTS", "64")))
    use_batch_times = len(points) >= batch_threshold and common["forecast_mode"] == "model"
    bbox = tuple(common["_model_fixed_bbox"])
    model_client = GfsNomadsClient(
        cache_dir=config.gfs_cache_dir,
        fixed_bbox=bbox,
        resolution=str(common["gfs_resolution"]),
        region=common["_compute_region"],
        feature_cache_enabled=False,
        shared_decode_enabled=True,
    )

    def time_requirements() -> tuple[set[int], set[datetime]]:
        forecast_hours: set[int] = set()
        evaluation_datetimes: set[datetime] = set()
        if use_batch_times:
            (
                _,
                _,
                _,
                valid,
                evaluation_epoch,
                _,
            ) = prepare_solar_event_timeline(
                points,
                date.fromisoformat(str(common["target_date"])),
                str(common["phenomenon"]),
            )
            valid_evaluation_epoch = evaluation_epoch[valid]
            evaluation_datetimes.update(
                datetime.fromtimestamp(float(epoch), tz=timezone.utc)
                for epoch in valid_evaluation_epoch
            )
            valid_offsets = (valid_evaluation_epoch - cycle.timestamp()) / 3600.0
            for offset in valid_offsets:
                cadence = 3 if offset > 120.0 else 1
                cadence_offset = float(offset) / cadence
                nearest_index = round(cadence_offset)
                if math.isclose(
                    cadence_offset,
                    nearest_index,
                    rel_tol=0.0,
                    abs_tol=1e-7,
                ):
                    required = (int(nearest_index) * cadence,)
                else:
                    required = (
                        int(math.floor(cadence_offset) * cadence),
                        int(math.ceil(cadence_offset) * cadence),
                    )
                if required[0] < 0:
                    raise RuntimeError(
                        "模式周期晚于有效太阳样本，无法构造完整时间 bracket。"
                    )
                forecast_hours.update(required)
        else:
            requests = [
                replace(
                    forecast_request_from_payload({**common, **point}),
                    include_map_cells=False,
                )
                for point in points
            ]
            for request in requests:
                anchor = solar_event_time_local(
                    request.target_date,
                    request.point,
                    request.timezone_name,
                    request.phenomenon,
                )
                request_times = evaluation_times_utc(request, anchor)
                evaluation_datetimes.update(request_times)
                forecast_hours.update(
                    required_model_hours(model_source, cycle, request_times)
                )
        return forecast_hours, evaluation_datetimes

    forecast_hours, evaluation_datetimes = time_requirements()
    if not forecast_hours:
        raise RuntimeError("全国事件窗口没有可预取的模式时次。")

    prepass_hour: int | None = None
    prepass_path: Any = None
    if any(
        point.get("_elevation_source") == "model-grid" for point in points
    ):
        prepass_hour = min(forecast_hours)
        prepass_path = model_client.prefetch_subset(cycle, prepass_hour, bbox)
        terrain_grid, _ = model_client.load_decoded_subset(cycle, prepass_hour, bbox)
        apply_decoded_model_surface_elevations(terrain_grid, points)
        forecast_hours, evaluation_datetimes = time_requirements()
        if not forecast_hours:
            raise RuntimeError("模式地形校正后的事件窗口没有可预取时次。")
        if prepass_hour not in forecast_hours:
            prepass_hour = min(forecast_hours)
            prepass_path = model_client.prefetch_subset(cycle, prepass_hour, bbox)
            terrain_grid, _ = model_client.load_decoded_subset(cycle, prepass_hour, bbox)
            apply_decoded_model_surface_elevations(terrain_grid, points)
            forecast_hours, evaluation_datetimes = time_requirements()
            if prepass_hour not in forecast_hours:
                raise RuntimeError(
                    "模式地形预扫时次未落入最终事件窗口，无法绑定输入证据。"
                )
        common["_terrain_source"]["model_grid_source"] = {
            "source": "gfs-orography",
            "forecast_hour": prepass_hour,
            "path": str(prepass_path),
        }

    common["_model_forecast_hours"] = sorted(forecast_hours)
    common["_gfs_forecast_hours"] = sorted(forecast_hours)
    if progress_callback is not None:
        progress_callback({
            "stage": f"prefetch_{model_source}",
            "percent": 4,
            "message": f"预取 {model_label} {cycle:%Y-%m-%d %HZ} 的 {len(forecast_hours)} 个时效",
        })
    failures: list[str] = []
    download_workers = min(8, max(1, int(common.get(
        "workers",
        int(os.environ.get("FIRESKY_GFS_DOWNLOAD_WORKERS", "4")),
    ))))
    hours = sorted(forecast_hours)

    def prefetch_hour(hour: int):
        if hour == prepass_hour:
            return prepass_path
        return model_client.prefetch_subset(cycle, hour, bbox)

    for hour, _, error in daemon_task_results(hours, prefetch_hour, download_workers):
        if error is not None:
            failures.append(f"f{hour:03d}: {error}")
    if failures:
        raise RuntimeError(f"{model_label} 全国预取失败：" + "; ".join(failures))
    if progress_callback is not None:
        progress_callback({
            "stage": "decode_gfs",
            "percent": 9,
            "message": f"共享解析并驻留 {len(forecast_hours)} 个 GFS 时次",
        })
    model_client.preload_snapshots(cycle, sorted(forecast_hours), bbox)

    if config.aerosol_source not in {"auto", "cams"}:
        raise RuntimeError(
            "全国预测要求完整 CAMS aerosol profile，不能使用 fallback。"
        )
    target_times = sorted({
        bracket_time
        for sample_time in evaluation_datetimes
        for bracket_time in cams_profile_bracket_times(sample_time)
    })
    if progress_callback is not None:
        progress_callback({
            "stage": "prefetch_cams",
            "percent": 12,
            "message": f"预取 CAMS 的 {len(target_times)} 个完整事件窗口时刻",
        })
    area = national_cams_area(bbox)
    cams = CamsAdsAerosolProvider(
        cache_dir=config.cams_cache_dir,
        strict=True,
        fixed_area=area,
        auto_download=True,
    )
    # 与 EC 分支相同：ADS 排队以分钟计，bracket 下载改为有界并发。
    covered = cams.download_targets_batch(
        area, list(target_times), require_exact_valid_time=True
    )
    for target_time in target_times:
        if target_time not in covered:
            cams.download_target(area, target_time, require_exact_valid_time=True)


def _national_gfs_source_radius_km(
    candidate_radius_km: float = NATIONAL_ANALYSIS_RADIUS_KM,
) -> float:
    return max(
        GFS_OPERATIONAL_PADDING_KM,
        source_domain_radius_km(candidate_radius_km),
    )


def national_gfs_bbox(
    points: list[dict[str, Any]],
    *,
    light_path_padding_km: float | None = None,
) -> tuple[float, float, float, float]:
    """Return one GFS subset covering every point and its solar source path.

    The default remains the bounded operational envelope used by broad
    East-Asia requests.  The domestic 695-city/China product passes the full
    candidate-plus-trace radius explicitly so edge candidates retain their
    incoming path.
    """
    padding_km = (
        max(GFS_OPERATIONAL_PADDING_KM, 1000.0)
        if light_path_padding_km is None
        else float(light_path_padding_km)
    )
    if not math.isfinite(padding_km) or padding_km <= 0.0:
        raise ValueError("全国共享 GFS 光路范围必须是正数。")
    boxes = [
        bounding_box(GeoPoint(lat=point["lat"], lon=point["lon"]), padding_km)
        for point in points
    ]
    if any(left > right for left, right, _, _ in boxes):
        raise ValueError("全国共享 GFS 范围暂不支持跨越日期变更线。")
    return canonical_subset_bbox((
        min(box[0] for box in boxes),
        max(box[1] for box in boxes),
        max(box[2] for box in boxes),
        min(box[3] for box in boxes),
    ))


def national_cams_area(gfs_bbox: tuple[float, float, float, float]) -> list[float]:
    return cams_area_from_model_bbox(gfs_bbox)


def _point_target_identity(payload: dict[str, Any]) -> tuple[date, str]:
    if "target_date" not in payload:
        raise ValueError("单点预测必须显式提供 target_date。")
    target_date = parse_iso_date(payload["target_date"], "target_date")
    if "phenomenon" not in payload or payload["phenomenon"] not in {"dawn", "dusk"}:
        raise ValueError("单点预测 phenomenon 必须显式为 dawn 或 dusk。")
    return target_date, str(payload["phenomenon"])


def forecast_request_from_payload(payload: dict[str, Any]) -> ForecastRequest:
    lat = bounded_float(payload.get("lat"), "纬度", -90.0, 90.0)
    lon = bounded_float(payload.get("lon"), "经度", -180.0, 180.0)
    timezone_name = str(payload.get("timezone_name") or "").strip()
    if not timezone_name:
        raise ValueError("缺少坐标对应的时区。")
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("timezone_name 必须是有效 IANA 时区。") from exc
    if "elevation_m" not in payload:
        raise ValueError("缺少观测者海拔 elevation_m。")
    target_date, phenomenon = _point_target_identity(payload)
    forecast_mode = str(payload.get("forecast_mode") or "model").lower()
    if forecast_mode not in {"model", "nowcast"}:
        raise ValueError("forecast_mode 必须是 model 或 nowcast。")
    model_source = str(payload.get("model_source") or ("satellite" if forecast_mode == "nowcast" else "gfs")).lower()
    allowed_model_sources = {"satellite"} if forecast_mode == "nowcast" else {"gfs", "ec"}
    if model_source not in allowed_model_sources:
        raise ValueError(
            "model 模式的 model_source 当前支持 gfs 或 ec；"
            "nowcast 模式只支持 satellite。"
        )
    satellite_source = normalized_satellite_source(forecast_mode, payload.get("satellite_source"))
    compute_backend = str(payload.get("compute_backend") or "auto").strip().lower()
    if compute_backend not in {"auto", "cpu", "gpu"}:
        raise ValueError("compute_backend 必须是 auto、cpu 或 gpu。")
    gfs_resolution = str(payload.get("gfs_resolution") or "0p25").strip().lower()
    if gfs_resolution not in {"0p25", "0p50"}:
        raise ValueError("gfs_resolution 必须是 0p25 或 0p50。")
    gfs_cycle_override = None
    model_cycle_override = None
    if model_source == "ec":
        cycle_value = payload.get("ec_cycle_override")
        if cycle_value is None:
            cycle_value = payload.get("_ec_cycle_override")
        if cycle_value is not None:
            model_cycle_override = parse_utc_iso_datetime(
                cycle_value,
                "ec_cycle_override",
            )
    else:
        cycle_value = payload.get("gfs_cycle_override")
        if cycle_value is None:
            cycle_value = payload.get("_gfs_cycle_override")
        if cycle_value is not None:
            gfs_cycle_override = parse_utc_iso_datetime(
                cycle_value,
                "gfs_cycle_override",
            )
    reference_time_utc = None
    reference_value = payload.get("reference_time_utc")
    if (
        forecast_mode == "nowcast"
        and reference_value is not None
        and str(reference_value).strip()
    ):
        reference_time_utc = parse_utc_iso_datetime(
            reference_value,
            "reference_time_utc",
        )
    point = GeoPoint(
        lat=lat,
        lon=lon,
        elevation_m=bounded_float(payload["elevation_m"], "海拔", -500.0, 9000.0),
    )
    grid_region = str(payload.get("_grid_region") or "").strip().lower()
    if not grid_region:
        grid_region = grid_domain_key_for_point(point)
    radius_default = 900.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    radius_max = 1000.0 if forecast_mode == "nowcast" else NATIONAL_ANALYSIS_RADIUS_KM
    request = ForecastRequest(
        target_date=target_date,
        timezone_name=timezone_name,
        point=point,
        phenomenon=phenomenon,
        name=str(payload.get("name") or f"{lat:.4f}, {lon:.4f}")[:120],
        radius_km=bounded_float(
            payload.get("radius_km", radius_default),
            "搜索半径",
            100.0,
            radius_max,
        ),
        model_source=model_source,
        forecast_mode=forecast_mode,
        reference_time_utc=reference_time_utc,
        satellite_source=satellite_source,
        gfs_cycle_override=gfs_cycle_override,
        model_cycle_override=model_cycle_override,
        include_map_cells=True,
        compute_backend=compute_backend,
        gfs_resolution=gfs_resolution,
        grid_region=grid_region,
    )
    return request


def parse_coordinate(params: dict[str, list[str]], key: str, low: float, high: float) -> float:
    value = params.get(key, [None])[0]
    return bounded_float(value, key, low, high)


def bounded_float(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是数字。") from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{label}必须在 {low:g} 到 {high:g} 之间。")
    return number


def location_payload(result) -> dict[str, Any]:
    payload = {
        "name": result.name,
        "display_name": result.display_name,
        "lat": result.lat,
        "lon": result.lon,
        "source": result.source,
    }
    if result.timezone_name is not None:
        payload["timezone_name"] = result.timezone_name
    if result.elevation_m is not None:
        payload["elevation_m"] = result.elevation_m
    return payload


# ── Data asset management endpoints (new unified API) ──


class _CycleMap(dict[str, datetime]):
    """Datetime cycle map with provenance kept outside the legacy mapping."""

    def __init__(
        self,
        values: dict[str, datetime] | None = None,
        *,
        cycle_sources: dict[str, str] | None = None,
    ) -> None:
        super().__init__(values or {})
        self.cycle_sources = dict(cycle_sources or {})


def _region_time_band_areas(
    region_code: str,
    region_name: str,
    bbox: tuple[float, float, float, float],
) -> tuple[list[str], list[dict]]:
    """Split a region into solar-time bands while retaining one spatial package."""
    west, east, north, south = bbox
    first_offset = max(-12, int(math.floor((west + 7.5) / 15.0)))
    last_offset = min(12, int(math.floor((east + 7.5) / 15.0)))
    codes: list[str] = []
    areas: list[dict] = []
    for offset in range(first_offset, last_offset + 1):
        band_west = max(west, (offset - 0.5) * 15.0)
        band_east = min(east, (offset + 0.5) * 15.0)
        if band_east <= band_west:
            continue
        code = f"REGION{offset + 12:02d}"
        timezone_name = (
            "Etc/GMT"
            if offset == 0
            else f"Etc/GMT{'-' if offset > 0 else '+'}{abs(offset)}"
        )
        codes.append(code)
        areas.append({
            "code": code,
            "name": region_name,
            "bounds": [band_west, band_east, north, south],
            "timezone": timezone_name,
            "regions": [region_code],
            "center_point": [(north + south) / 2.0, (band_west + band_east) / 2.0],
        })
    return codes, areas


def handle_data_requirements(payload: dict | None = None) -> dict:
    """Build a download plan from one named geographic region."""
    payload = payload or {}
    region_code = str(payload.get("region") or "east-asia").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,31}", region_code):
        raise ValueError("region 必须是合法的区域代码。")
    raw_bbox = payload.get("bbox") or REGION_BBOXES.get(region_code) or REGION_BBOXES["east-asia"]
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        raise ValueError("bbox 必须是 [west, east, north, south]。")
    bbox = tuple(float(value) for value in raw_bbox)
    west, east, north, south = bbox
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError("bbox 经纬度范围无效。")
    analysis_radius_km = _analysis_radius_from_payload(payload)
    region_timezone = str(payload.get("timezone") or "UTC")
    region_name = str(payload.get("region_name") or region_code)
    phenomena = [
        str(value).lower()
        for value in payload.get("phenomena") or ["dawn", "dusk"]
        if str(value).lower() in {"dawn", "dusk"}
    ]
    if not phenomena:
        phenomena = ["dawn", "dusk"]

    raw_dates = payload.get("target_dates")
    if isinstance(raw_dates, list) and raw_dates:
        target_dates = [date.fromisoformat(str(value)).isoformat() for value in raw_dates]
    else:
        days = max(1, min(7, int(payload.get("days", 3))))
        start_date = china_today() + timedelta(days=max(0, int(payload.get("start_offset_days", 1))))
        target_dates = [(start_date + timedelta(days=index)).isoformat() for index in range(days)]

    now_utc = datetime.now(timezone.utc)
    cycles = _cached_latest_cycles(now_utc)
    cycle_sources = dict(getattr(cycles, "cycle_sources", {}))
    selected_sources = [str(value).lower() for value in payload.get("sources") or []]
    # Desktop downloads can pin each source to the cycle just probed upstream,
    # without relying on a scheduler/Redis instance in the local application.
    for raw_source, raw_cycle in (payload.get("cycle_overrides") or {}).items():
        source = "ecmwf" if raw_source == "ec" else raw_source
        if source not in {"gfs", "ecmwf", "cams"}:
            raise ValueError(f"未知周期数据源：{raw_source}")
        cycles[source] = _parse_data_cycle(raw_cycle)
        cycle_sources[source] = "override"
    if payload.get("cycle") and len(selected_sources) == 1:
        source = "ecmwf" if selected_sources[0] == "ec" else selected_sources[0]
        cycles[source] = _parse_data_cycle(payload["cycle"])
        cycle_sources[source] = "override"

    coverage_codes, coverage_areas = _region_time_band_areas(
        region_code,
        region_name,
        bbox,
    )
    specs = resolve_datasets_for_areas(
        coverage_codes,
        target_dates=target_dates,
        phenomena=phenomena,
        model_source="all",
        cycle_overrides=cycles,
        area_overrides=coverage_areas,
        spacing_deg=float(payload.get("spacing_deg", 2.0)),
    )
    specs_by_source = {
        "ecmwf" if spec.model_source == "ec" else spec.model_source: spec
        for spec in specs
    }
    datasets = []
    for source in ("gfs", "ecmwf", "cams"):
        spec = specs_by_source[source]
        hours = spec.cams_lead_hours if source == "cams" else spec.forecast_hours
        spatial_count = len(spec.cams_areas) if source == "cams" else len(spec.bboxes) if source == "gfs" else 1
        spatial_count = max(1, spatial_count)
        dataset = {
            "source": source,
            "cycle_source": cycle_sources.get(source, "unverified"),
            "latest_cycle": spec.cycle_utc.strftime("%Y-%m-%d %HZ") if spec.cycle_utc else "",
            "expected_files": len(hours) * spatial_count,
            "required_hours_count": len(hours),
            "region_count": spatial_count,
            "regions": spec.regions if source in {"gfs", "cams"} else [],
            "summary": f"{source.upper()} {len(hours)} 个时效 / {spatial_count} 个区域",
            "bboxes": [list(bbox) for bbox in spec.bboxes],
        }
        if source == "cams":
            dataset["required_lead_hours"] = hours
            dataset["areas"] = spec.cams_areas
            if _cams_diagnostic_enabled(payload):
                dataset["diagnostic_contract"] = {
                    "schema_version": SPECTRAL_OPTICS_SCHEMA_VERSION,
                    "cache_prefix": SPECTRAL_OPTICS_CACHE_PREFIX,
                    "request_variables": list(
                        build_spectral_optics_request().get("variable", [])
                    ),
                    "score_consumed": False,
                }
        elif source == "gfs" and _gfs_diagnostic_enabled(payload):
            dataset["diagnostic_contract"] = {
                "schema_version": GFS_DIAGNOSTIC_REQUEST_SCHEMA_VERSION,
                "pressure_levels_hpa": list(GFS_DIAGNOSTIC_PRESSURE_LEVELS),
                "variables": list(GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES),
                "units": GFS_DIAGNOSTIC_HYDROMETEOR_UNITS,
                "score_consumed": False,
            }
        else:
            dataset["required_hours"] = hours
            if source == "ecmwf":
                # EC raw bundles are global, but the strict derived cache is
                # tied to the requested national compute bbox.
                dataset["bboxes"] = [list(bbox)]
        datasets.append(dataset)

    # Explicit region override: when the caller supplies a single bounding box
    # (GFS order: west, east, north, south) we clip exactly to it instead of the
    # internally sampled time-band footprints. GFS is subset-downloaded; CAMS uses the
    # matching [N, W, S, E] area. The ``region`` code is carried through to the
    # storage layout so files land under .cache/<src>/<date>/<HH>Z/<region>/.
    from firesky.cache_tasks import _cams_area_from_bbox

    # EC scoring uses one halo-expanded physical domain for its CAMS profile.
    selected_external_sources = {
        "ec" if source == "ecmwf" else source
        for source in selected_sources
        if source in {"ec", "ecmwf"}
    }
    if not selected_sources:
        selected_external_sources = {"ec"}
    elif not selected_external_sources and "cams" in selected_sources:
        selected_external_sources = {"ec"}
    prepared_bboxes = []
    for external_source in sorted(selected_external_sources):
        try:
            prepared_bboxes.append(
                _external_prepared_bbox(
                    external_source,
                    bbox,
                    analysis_radius_km,
                )
            )
        except ValueError:
            # A dateline-wrapping source cannot be represented by one CAMS
            # area; retain any other valid external-domain expansion.
            continue
    if prepared_bboxes:
        cams_download_bbox = (
            max(-180.0, min(bbox[0], *(item[0] for item in prepared_bboxes))),
            min(180.0, max(bbox[1], *(item[1] for item in prepared_bboxes))),
            min(90.0, max(bbox[2], *(item[2] for item in prepared_bboxes))),
            max(-90.0, min(bbox[3], *(item[3] for item in prepared_bboxes))),
        )
    else:
        cams_download_bbox = bbox

    for dataset in datasets:
        if dataset["source"] == "gfs":
            dataset["bboxes"] = [list(bbox)]
            dataset["region_count"] = 1
            dataset["regions"] = [region_code]
        elif dataset["source"] == "cams":
            dataset["areas"] = [_cams_area_from_bbox(cams_download_bbox)]
            dataset["bboxes"] = [list(bbox)]
            dataset["region_count"] = 1
            dataset["regions"] = [region_code]

    return {
        "region": {
            "code": region_code,
            "name": region_name,
            "bbox": list(bbox),
            "timezone": region_timezone,
        },
        "target_dates": target_dates,
        "phenomena": phenomena,
        "computed_at": now_utc.isoformat(),
        "datasets": datasets,
    }


def _parse_data_cycle(value: Any) -> datetime:
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %HZ", "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


_REGIONAL_REFRESH_LOCK = threading.Lock()
_LAST_REFRESHED_GFS_CYCLE: datetime | None = None


def _regional_update_spec(
    request_payload: dict[str, Any],
    config: WebServerConfig,
    gfs_cycle: datetime,
    cams_cycle: datetime,
    cams_cycle_source: str,
) -> tuple[dict[str, Any], str]:
    """Normalize the explicit scheduler request and fingerprint all inputs.

    The outer ``regional_forecast`` wrapper is intentionally not passed to the
    forecast runner.  The fingerprint includes the normalized runner payload,
    its cache identity, and the observed GFS/CAMS cycles so a cycle update can
    never accidentally reuse a previous input set.
    """
    requested = request_payload.get("regional_forecast")
    if not isinstance(requested, dict):
        raise ValueError("regional_forecast 必须是 JSON 对象。")
    normalized = dict(requested)
    if not str(normalized.get("target_date") or "").strip():
        raise ValueError("regional_forecast.target_date 必须显式配置。")
    if not str(normalized.get("phenomenon") or "").strip():
        raise ValueError("regional_forecast.phenomenon 必须显式配置。")
    normalized.setdefault("model_source", "gfs")
    normalized.setdefault("forecast_mode", "model")
    normalized.setdefault("satellite_source", "off")
    normalized.setdefault("regions", ["china"])
    normalized.setdefault("spacing_deg", CHINA_PRODUCTION_SPACING_DEG)
    normalized.setdefault("gfs_resolution", "0p25")
    normalized.setdefault("compute_backend", "auto")
    normalized["_aerosol_source"] = str(config.aerosol_source).strip().lower()
    normalized["_gfs_cycle_override"] = gfs_cycle.isoformat()

    identity = national_forecast_cache_identity(normalized)
    fingerprint_source = {
        "version": "regional-gfs-cycle-trigger-v1",
        "request": normalized,
        "cache_identity": identity,
        "gfs_cycle_utc": gfs_cycle.isoformat(),
        "cams_cycle_utc": (
            cams_cycle.isoformat() if cams_cycle_source == "provider" else None
        ),
        "cams_cycle_probe_utc": cams_cycle.isoformat(),
        "cams_cycle_source": cams_cycle_source,
    }
    encoded = json.dumps(
        fingerprint_source,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return normalized, hashlib.sha256(encoded).hexdigest()


def _regional_update_for_cycle(
    request_payload: dict[str, Any] | None,
    config: WebServerConfig,
    cycles: dict[str, datetime],
    previous_gfs_cycle: datetime | None,
    gfs_cycle_source: str,
    cams_cycle_source: str,
) -> dict[str, Any] | None:
    if not isinstance(request_payload, dict) or "regional_forecast" not in request_payload:
        return None
    base = {
        "gfs_cycle_utc": (
            cycles["gfs"].isoformat() if gfs_cycle_source == "provider" else None
        ),
        "gfs_cycle_source": gfs_cycle_source,
        "cams_cycle_utc": (
            cycles["cams"].isoformat() if cams_cycle_source == "provider" else None
        ),
        "cams_cycle_source": cams_cycle_source,
        "retry_policy": "next-verified-gfs-cycle-only",
    }
    if gfs_cycle_source != "provider":
        base["status"] = "cycle_unverified"
        return base

    normalized, fingerprint = _regional_update_spec(
        request_payload,
        config,
        cycles["gfs"],
        cycles["cams"],
        cams_cycle_source,
    )
    base["fingerprint"] = fingerprint
    base["gfs_cycle_utc"] = cycles["gfs"].isoformat()
    is_new_cycle = previous_gfs_cycle is None or cycles["gfs"] > previous_gfs_cycle
    if not is_new_cycle:
        existing = FORECAST_JOBS.find_regional_by_key(fingerprint)
        base["status"] = "unchanged"
        if existing is not None:
            base["job_id"] = existing.job_id
        return base

    existing = FORECAST_JOBS.find_regional_by_key(fingerprint)
    job = FORECAST_JOBS.start(
        normalized,
        config,
        run_and_cache_national_forecast_public_payload,
        defer_result=True,
        regional=True,
        dedupe_key=fingerprint,
    )
    base["status"] = (
        "deduplicated"
        if existing is not None and job.job_id == existing.job_id
        else "queued"
    )
    base["job_id"] = job.job_id
    return base


def _cached_latest_cycles(now_utc: datetime) -> dict:
    """Return cycle hints without doing network I/O in a request.

    The scheduler-owned ``refresh_latest_cycles`` endpoint performs provider
    probes and stores the result in Redis.  A user request must never fall
    through to those probes: a regional forecast can run for minutes and a
    missing Redis instance must not turn a status query into a serial network
    wait.  Heuristics are explicitly marked unverified until the scheduler
    refreshes them.
    """
    from firesky.redis_cache import (
        get_latest_cycles,
    )

    sources = ("gfs", "ecmwf", "cams")
    try:
        cached = get_latest_cycles()
    except Exception:
        cached = None
    if isinstance(cached, dict) and all(source in cached for source in sources):
        try:
            cycles = {source: datetime.fromisoformat(str(cached[source])) for source in sources}
            raw_sources = cached.get("cycle_sources")
            cycle_sources = (
                {source: str(raw_sources.get(source) or "unverified") for source in sources}
                if isinstance(raw_sources, dict)
                else {source: "unverified" for source in sources}
            )
            return _CycleMap(cycles, cycle_sources=cycle_sources)
        except (TypeError, ValueError):
            pass

    cycles = {source: _heuristic_cycle_for(source, now_utc) for source in sources}
    cycle_sources = {source: "heuristic" for source in sources}
    return _CycleMap(cycles, cycle_sources=cycle_sources)


def refresh_latest_cycles(
    request_payload: dict[str, Any] | None = None,
    config: WebServerConfig | None = None,
) -> dict:
    """Probe the latest published cycle for every source (slow upstream HEAD
    probes) and persist the result to Redis so /data/status can serve instantly.

    Called by the Go scheduler every ~10 minutes. Tolerates a single source
    failing: its entry is filled with a published-cycle heuristic so the cache
    stays complete and usable.
    """
    from firesky.cycle_availability import (
        get_cycle_source,
        query_latest_gfs_cycle,
        query_latest_ecmwf_cycle,
        query_latest_cams_cycle,
    )
    from firesky.redis_cache import get_latest_cycles, set_latest_cycles

    now_utc = datetime.now(timezone.utc)
    effective_request = request_payload
    configured_refresh = (
        config.regional_refresh_payload
        if config is not None
        else None
    )
    if (
        isinstance(configured_refresh, dict)
        and (
            not isinstance(effective_request, dict)
            or "regional_forecast" not in effective_request
        )
    ):
        effective_request = {"regional_forecast": dict(configured_refresh)}
    queries = {
        "gfs": query_latest_gfs_cycle,
        "ecmwf": query_latest_ecmwf_cycle,
        "cams": query_latest_cams_cycle,
    }
    cycles: dict[str, datetime] = {}
    cycle_sources: dict[str, str] = {}
    for source, fn in queries.items():
        try:
            cycles[source] = fn(now_utc)
            cycle_sources[source] = get_cycle_source(source) or "provider"
        except Exception:
            # Keep the cache complete even if one provider is briefly down.
            cycles[source] = _heuristic_cycle_for(source, now_utc)
            cycle_sources[source] = "heuristic"

    global _LAST_REFRESHED_GFS_CYCLE
    with _REGIONAL_REFRESH_LOCK:
        cached = get_latest_cycles()
        previous_gfs_cycle = _LAST_REFRESHED_GFS_CYCLE
        if isinstance(cached, dict) and cached.get("gfs"):
            cached_sources = cached.get("cycle_sources")
            cached_gfs_source = (
                cached_sources.get("gfs")
                if isinstance(cached_sources, dict)
                else "provider"
            )
            if cached_gfs_source == "heuristic":
                cached_gfs_cycle = None
            else:
                try:
                    cached_gfs_cycle = _parse_data_cycle(cached["gfs"])
                except (TypeError, ValueError):
                    cached_gfs_cycle = None
            if cached_gfs_cycle is not None and (
                previous_gfs_cycle is None or cached_gfs_cycle > previous_gfs_cycle
            ):
                previous_gfs_cycle = cached_gfs_cycle

        payload = {
            source: dt.strftime("%Y-%m-%d %HZ") for source, dt in cycles.items()
        }
        payload["refreshed_at"] = now_utc.isoformat()
        payload["cycle_sources"] = cycle_sources
        set_latest_cycles(payload)
        regional_update = _regional_update_for_cycle(
            effective_request,
            config or WebServerConfig(),
            cycles,
            previous_gfs_cycle,
            cycle_sources["gfs"],
            cycle_sources["cams"],
        )
        if (
            cycle_sources["gfs"] == "provider"
            and (
                _LAST_REFRESHED_GFS_CYCLE is None
                or cycles["gfs"] > _LAST_REFRESHED_GFS_CYCLE
            )
        ):
            _LAST_REFRESHED_GFS_CYCLE = cycles["gfs"]
    if regional_update is not None:
        payload["regional_update"] = regional_update
    return payload


def _heuristic_cycle_for(source: str, now_utc: datetime) -> datetime:
    """Conservative published-cycle guess, used only when a live probe fails.

    Reuse the provider-specific cycle selectors so status fallback and actual
    forecast selection cannot disagree about the publication lag.
    """
    from firesky.geometry import choose_cycle

    if source == "cams":
        return choose_cycle(now_utc, (0, 12), 10)
    if source == "gfs":
        return choose_gfs_cycle(now_utc)
    if source == "ecmwf":
        return choose_ecmwf_cycle(now_utc)
    raise ValueError(f"未知数据源：{source}")


def handle_refresh_latest_cycles(
    payload: dict | None = None,
    config: WebServerConfig | None = None,
) -> dict:
    """HTTP handler wrapper for the periodic latest-cycle refresh."""
    return refresh_latest_cycles(payload, config)


def _regional_cache_is_current_for_cycle(
    payload: dict[str, Any],
    cache_dir: str | Path,
    expected_cycle: datetime,
    cache_key: str,
) -> tuple[bool, str | None]:
    """Validate one complete China result against the inspected GFS cycle.

    The hourly inspector must never treat an old, partial, or corrupt artifact
    as current merely because its filename exists. Summaries are the cheap
    path; an exact full cache is accepted only as a fallback and is immediately
    materialized into a summary for the next inspection.
    """
    target_date = str(payload["target_date"])
    phenomenon = str(payload["phenomenon"])
    summary_path = national_forecast_summary_path(
        cache_dir, cache_key, target_date, phenomenon
    )
    if summary_path.is_file():
        try:
            summary = _read_cached_national_summary_document(summary_path)
            metadata = summary.get("cache")
            provenance = summary.get("data_provenance")
            if (
                summary.get("version") != NATIONAL_SUMMARY_VERSION
                or summary.get("algorithm") != NATIONAL_BATCH_ALGORITHM
                or not isinstance(metadata, dict)
                or metadata.get("version") != NATIONAL_CACHE_VERSION
                or metadata.get("key") != cache_key
                or metadata.get("status") not in {"refresh", "hit"}
                or not isinstance(provenance, dict)
                or provenance.get("stale") is not False
                or provenance.get("inputs_complete") is not True
                or summary.get("target_date") != target_date
                or summary.get("phenomenon") != phenomenon
                or parse_utc_iso_datetime(
                    str(summary.get("cycle_time_utc")),
                    "regional summary cycle_time_utc",
                )
                != expected_cycle
                or parse_utc_iso_datetime(
                    str(provenance.get("served_gfs_cycle_utc")),
                    "regional summary served GFS cycle",
                )
                != expected_cycle
                or summary.get("point_count") != CHINA_PRODUCTION_POINT_COUNT
                or summary.get("success_count") != CHINA_PRODUCTION_POINT_COUNT
                or summary.get("error_count") != 0
                or not isinstance(summary.get("points"), list)
                or len(summary["points"]) != CHINA_PRODUCTION_POINT_COUNT
            ):
                raise ValueError("summary is not a complete current China result")
            _validate_national_summary_contract(summary)
            return True, "summary"
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass

    full_path = national_forecast_cache_path(
        cache_dir, cache_key, target_date, phenomenon
    )
    if not full_path.is_file():
        return False, None
    try:
        cached = read_national_forecast_cache(payload, cache_dir)
        metadata = cached.get("cache")
        if (
            not isinstance(metadata, dict)
            or metadata.get("key") != cache_key
            or cached.get("target_date") != target_date
            or cached.get("phenomenon") != phenomenon
            or cached.get("regions") != ["china"]
            or cached.get("point_mode") != "generated-regional-grid"
            or cached.get("point_count") != CHINA_PRODUCTION_POINT_COUNT
            or cached.get("success_count") != CHINA_PRODUCTION_POINT_COUNT
            or cached.get("error_count") != 0
            or not isinstance(cached.get("points"), list)
            or len(cached["points"]) != CHINA_PRODUCTION_POINT_COUNT
            or parse_utc_iso_datetime(
                str(cached.get("cycle_time_utc")),
                "regional cache cycle_time_utc",
            )
            != expected_cycle
        ):
            return False, None
        # A full result is only current when its immutable GFS/CAMS manifest
        # proves the source inputs.  Do not promote a points-only legacy result
        # into a current summary during the hourly inspection.
        _national_forecast_summary(cached, require_complete_provenance=True)
        write_national_forecast_summary(payload, cached, cache_dir)
        return True, "full"
    except (FileNotFoundError, OSError, RuntimeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False, None


def refresh_national_forecast_range(
    payload: dict[str, Any] | None,
    config: WebServerConfig,
) -> dict[str, Any]:
    """Return missing China maps with an event-safe cycle for each window.

    The Go gateway owns the durable forecast queue.  This endpoint is only a
    read-only plan so a scheduler request cannot create a job that is absent
    from ``forecast_jobs``.
    """
    from firesky.cycle_availability import query_latest_gfs_cycle

    options = payload if isinstance(payload, dict) else {}
    days = options.get("days", 3)
    if isinstance(days, bool) or not isinstance(days, int) or days != 3:
        raise ValueError("区域图巡检固定检查最近三天。")
    start_date = china_today()
    now_utc = datetime.now(timezone.utc)
    latest_observed_gfs_cycle = query_latest_gfs_cycle(now_utc)
    # Cycle selection must use the same generated production grid as the
    # forecast.  A past dawn can precede the newest observed cycle, while
    # future events can still use that newest cycle.
    cycle_points = national_points_from_payload({
        "model_source": "gfs",
        "regions": ["china"],
        "spacing_deg": CHINA_PRODUCTION_SPACING_DEG,
        "gfs_resolution": "0p25",
    })
    entries: list[dict[str, Any]] = []
    for day_offset in range(3):
        target_date = (start_date + timedelta(days=day_offset)).isoformat()
        for phenomenon in ("dawn", "dusk"):
            event_reference_utc = _national_model_reference_utc(
                cycle_points,
                target_date,
                phenomenon,
                now_utc,
            )
            event_safe_cycle = choose_gfs_cycle(event_reference_utc)
            gfs_cycle = min(latest_observed_gfs_cycle, event_safe_cycle)
            request_payload = {
                "target_date": target_date,
                "phenomenon": phenomenon,
                "model_source": "gfs",
                "forecast_mode": "model",
                "satellite_source": "off",
                "regions": ["china"],
                "spacing_deg": CHINA_PRODUCTION_SPACING_DEG,
                "gfs_resolution": "0p25",
                "compute_backend": "auto",
                "_aerosol_source": str(config.aerosol_source).strip().lower(),
                "_gfs_cycle_override": gfs_cycle.isoformat(),
            }
            cache_key = national_forecast_cache_key(request_payload)
            entry = {
                "target_date": target_date,
                "phenomenon": phenomenon,
                "gfs_cycle_utc": gfs_cycle.isoformat(),
                "cache_key": cache_key,
            }
            current, source = _regional_cache_is_current_for_cycle(
                request_payload,
                config.regional_cache_dir,
                gfs_cycle,
                cache_key,
            )
            if current:
                entry["status"] = "current"
                entry["source"] = source
                entries.append(entry)
                continue
            entry["status"] = "missing"
            entry["request_payload"] = request_payload
            entries.append(entry)
    current_count = sum(entry["status"] == "current" for entry in entries)
    missing_count = sum(entry["status"] == "missing" for entry in entries)
    return {
        "status": "skipped" if missing_count == 0 else "planned",
        "latest_observed_gfs_cycle_utc": latest_observed_gfs_cycle.isoformat(),
        "checked_dates": [(start_date + timedelta(days=value)).isoformat() for value in range(3)],
        "entries": entries,
        "current_count": current_count,
        "queued_count": 0,
        "deduplicated_count": 0,
        "skipped_count": current_count,
    }


def handle_refresh_national_forecast_range(
    payload: dict[str, Any] | None,
    config: WebServerConfig,
) -> dict[str, Any]:
    return refresh_national_forecast_range(payload, config)


# ── Data status: scan local cache + merge requirements ──

_CAMS_RE = re.compile(
    r"^cams-optics-v2-(\d{4})-(\d{2})-(\d{2})"
    r"-(\d{4})-(\d{3})-([a-z0-9]+)\.grib$"
)
_CAMS_SPECTRAL_RE = re.compile(
    rf"^{re.escape(SPECTRAL_OPTICS_CACHE_PREFIX)}-(\d{{4}})-(\d{{2}})-(\d{{2}})"
    r"-(\d{4})-(\d{3})-([a-f0-9]+)\.grib$"
)
_ECMWF_RE = re.compile(r"^ifs-basic-v2-(\d{10})-f(\d{3})\.grib2$")


def _pick_cams_cycle_utc(now_utc: datetime) -> datetime:
    """Pick the latest CAMS run cycle that's actually published on CDS.

    Verified empirically against the CDS API: at 20:48 UTC the 12Z run was
    still NOT published (CDS returned time=['00:00'] only for 2026-07-18).
    So CAMS publication delay is much longer than GFS/ECMWF — use 10h.
    This must stay in sync with the download side's choose_best_cams_selection
    which queries CDS for actual availability.
    """
    step_h, offset_h = 12, 10
    utc_h = now_utc.hour - offset_h
    aligned = (utc_h // step_h) * step_h
    cycle = datetime(now_utc.year, now_utc.month, now_utc.day,
                     (aligned % 24 + 24) % 24, tzinfo=timezone.utc)
    if aligned < 0:
        cycle -= timedelta(days=1)
    return cycle



def _scan_gfs_local(
    cache_dir: Path,
    region: str | None = None,
    required_bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    """Scan the configured GFS cache recursively.

    When *region* is given, only files stored under that region's subfolder
    (``<cache_dir>/<date>/<HHZ>/<region>/``) are counted. A file outside that
    folder is also eligible when ``required_bbox`` is supplied and its metadata
    proves that the cached subset covers the requested bbox. GFS downloads can
    intentionally reuse a larger shared subset instead of copying the same GRIB
    into every region directory.
    """
    gfs_dir = Path(cache_dir)
    file_count = 0
    total_mb = 0.0
    cycles: dict[str, dict] = {}  # cycle_iso -> {hours, files, mb}

    if not gfs_dir.is_dir():
        return {"file_count": 0, "total_size_mb": 0, "latest_cycle": "", "cycles": cycles}

    for grib_path in gfs_dir.rglob("*.grib2"):
        in_region = not region or region in grib_path.parts
        if not in_region and required_bbox is None:
            continue
        info = grib_path.stat()
        if info.st_size <= 0:
            continue
        counted = in_region or required_bbox is None
        if counted:
            file_count += 1
            total_mb += info.st_size / (1024 * 1024)

        sidecar = grib_path.with_suffix(".grib2.json")
        if not sidecar.is_file():
            continue
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            cycle_iso = meta.get("cycle", "")
            fh = int(meta.get("forecast_hour", -1))
            bbox = tuple(float(value) for value in meta["bbox"]) if "bbox" in meta else None
            is_global = bool(meta.get("global"))
        except Exception:
            continue
        if not cycle_iso:
            continue
        if not in_region:
            covers_required_bbox = is_global or (
                bbox is not None and bbox_contains(bbox, required_bbox)
            )
            if not covers_required_bbox:
                continue
            file_count += 1
            total_mb += info.st_size / (1024 * 1024)

        if cycle_iso not in cycles:
            # Convert ISO to label: "2026-07-15T18:00:00Z" → "2026-07-15 18Z"
            try:
                dt = datetime.fromisoformat(cycle_iso.replace("Z", "+00:00"))
                label = dt.strftime("%Y-%m-%d %HZ")
            except Exception:
                label = cycle_iso
            cycles[cycle_iso] = {"hours": [], "files": 0, "mb": 0.0, "label": label, "coverage": {}}
        c = cycles[cycle_iso]
        c["hours"].append(fh)
        c["files"] += 1
        c["mb"] += info.st_size / (1024 * 1024)
        c["coverage"].setdefault(fh, []).append(None if is_global else bbox)

    # Sort and deduplicate hours within each cycle
    for c in cycles.values():
        c["hours"] = sorted(set(c["hours"]))

    # Find latest cycle
    latest_key = max(cycles.keys()) if cycles else ""
    latest_label = cycles[latest_key]["label"] if latest_key else ""
    return {
        "file_count": file_count,
        "total_size_mb": round(total_mb, 2),
        "latest_cycle": latest_label,
        "cycles": cycles,
    }


def _scan_ecmwf_local(cache_dir: Path) -> dict:
    """Scan strict EC basic bundles usable by point and regional prediction.

    Legacy ``ifs-v3`` files are intentionally excluded: they are pressure-only
    bundles without the TCC/surface fields required by the current contract.
    """
    ec_dir = Path(cache_dir)
    file_count = 0
    total_mb = 0.0
    cycles: dict[str, dict] = {}

    if not ec_dir.is_dir():
        return {"file_count": 0, "total_size_mb": 0, "latest_cycle": "", "cycles": cycles}

    for grib_path in ec_dir.rglob("*.grib2"):
        info = grib_path.stat()
        if info.st_size <= 0:
            continue
        m = _ECMWF_RE.match(grib_path.name)
        if not m:
            continue

        file_count += 1
        total_mb += info.st_size / (1024 * 1024)

        raw = m.group(1)  # YYYYMMDDHH
        fh = int(m.group(2))
        label = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}Z"

        if raw not in cycles:
            cycles[raw] = {
                "hours": [],
                "files": 0,
                "mb": 0.0,
                "label": label,
                "paths": {},
            }
        c = cycles[raw]
        c["hours"].append(fh)
        c["files"] += 1
        c["mb"] += info.st_size / (1024 * 1024)
        c["paths"][fh] = str(grib_path)

    for c in cycles.values():
        c["hours"] = sorted(set(c["hours"]))

    latest_key = max(cycles.keys()) if cycles else ""
    latest_label = cycles[latest_key]["label"] if latest_key else ""
    return {
        "file_count": file_count,
        "total_size_mb": round(total_mb, 2),
        "latest_cycle": latest_label,
        "cycles": cycles,
    }


def _scan_cams_local(
    cache_dir: Path,
    area_keys: set[str] | None = None,
    diagnostic: bool = False,
) -> dict:
    """Scan CAMS optical-profile cache files for available cycles and leads.

    When *area_keys* is given, only files whose trailing area hash matches one of
    the keys are counted — this is how a region's CAMS subset (area derived from
    the region bbox) is isolated from the global cache.
    """
    cams_dir = Path(cache_dir)
    file_count = 0
    total_mb = 0.0
    cycles: dict[str, dict] = {}

    if not cams_dir.is_dir():
        return {"file_count": 0, "total_size_mb": 0, "latest_cycle": "", "cycles": cycles}

    for grib_path in cams_dir.rglob("*.grib"):
        metadata_area_key: str | None = None
        if diagnostic:
            m = _CAMS_SPECTRAL_RE.match(grib_path.name)
            if m is None:
                continue
            try:
                metadata = read_spectral_optics_cache_metadata(grib_path)
                identity = metadata.get("identity")
                if not isinstance(identity, dict):
                    continue
                validate_spectral_optics_cache_metadata(grib_path, identity)
                from firesky.aerosol import cams_area_key

                metadata_area_key = cams_area_key(identity["area"])
            except Exception:
                continue
            if area_keys and metadata_area_key not in area_keys:
                continue
        else:
            m = _CAMS_RE.match(grib_path.name)
            if not m:
                continue
            if area_keys and m.group(6) not in area_keys:
                continue

        info = grib_path.stat()
        if info.st_size <= 0:
            continue
        file_count += 1
        total_mb += info.st_size / (1024 * 1024)


        year, month, day, run_time, lead, file_area_key = m.groups()
        area_key = metadata_area_key or file_area_key
        key = f"{year}-{month}-{day} {run_time}"
        label = f"{year}-{month}-{day} {run_time[:2]}Z"
        lh = int(lead)

        if key not in cycles:
            cycles[key] = {"hours": [], "files": 0, "mb": 0.0, "label": label, "area_keys": {}}
        c = cycles[key]
        c["hours"].append(lh)
        c["files"] += 1
        c["mb"] += info.st_size / (1024 * 1024)
        c["area_keys"].setdefault(lh, set()).add(area_key)

    for c in cycles.values():
        c["hours"] = sorted(set(c["hours"]))

    latest_key = max(cycles.keys()) if cycles else ""
    latest_label = cycles[latest_key]["label"] if latest_key else ""
    return {
        "file_count": file_count,
        "total_size_mb": round(total_mb, 2),
        "latest_cycle": latest_label,
        "cycles": cycles,
    }


def _scan_derived_local(cache_dir: Path, label: str, ext: str) -> dict:
    """Scan one configured derived cache directory recursively."""
    d = Path(cache_dir)
    info: dict = {"name": str(d), "label": label, "file_count": 0, "total_size_mb": 0.0, "latest_file": ""}
    if not d.is_dir():
        return info
    latest_mtime = 0.0
    for f in d.rglob(f"*{ext}"):
        st = f.stat()
        info["file_count"] += 1
        info["total_size_mb"] += st.st_size / (1024 * 1024)
        if st.st_mtime > latest_mtime:
            latest_mtime = st.st_mtime
            info["latest_file"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    info["total_size_mb"] = round(info["total_size_mb"], 2)
    return info


def handle_data_status(config: WebServerConfig, payload: dict | None = None) -> dict:
    """Full data status: requirements + local scan merged. Called by Go proxy."""
    req = handle_data_requirements(payload)
    from firesky.aerosol import cams_area_key
    region_code = req["region"]["code"]

    # CAMS area keys for the selected region (area derived from its bbox).
    cams_ds = next((d for d in req["datasets"] if d["source"] == "cams"), None)
    cams_area_keys = (
        {cams_area_key(a) for a in (cams_ds.get("areas") or [])}
        if cams_ds else set()
    )

    # 2. Scan local cache (GFS + CAMS scoped to the selected region so the
    #    inventory matches the download status shown above; ECMWF is global).
    gfs_local = _scan_gfs_local(
        config.gfs_cache_dir,
        region=region_code,
        required_bbox=tuple(float(value) for value in req["region"]["bbox"]),
    )
    ecmwf_local = _scan_ecmwf_local(config.ecmwf_cache_dir)
    cams_local = _scan_cams_local(
        config.cams_cache_dir,
        area_keys=cams_area_keys or None,
        diagnostic=_cams_diagnostic_enabled(payload),
    )

    local_map = {
        "gfs": gfs_local,
        "ecmwf": ecmwf_local,
        "cams": cams_local,
    }

    # 3. Scan derived caches
    derived = [
        _scan_derived_local(config.map_cache_dir, "地图帧", ".json.gz"),
        _scan_derived_local(config.point_cache_dir, "单点预测", ".json.gz"),
        _scan_derived_local(config.regional_cache_dir, "区域预测", ".json.gz"),
        # Independent derived caches, kept outside the raw grib tree
        _scan_derived_local(config.gfs_cache_dir / "derived/parsed", "GFS 特征缓存", ".pkl.gz"),
        _scan_derived_local(config.gfs_cache_dir / "derived/decoded", "GFS 解码网格", ".json"),
        _scan_derived_local(config.gfs_cache_dir / "derived/national-layer-profiles", "GFS 层剖面", ".npy"),
        _scan_derived_local(config.ecmwf_cache_dir / "derived/parsed", "ECMWF 特征缓存", ".pkl.gz"),
        _scan_derived_local(config.ecmwf_cache_dir / "derived/decoded", "ECMWF 区域解码网格", ".json"),
        _scan_derived_local(config.ecmwf_cache_dir / "derived/national-layer-profiles", "ECMWF 层剖面", ".npy"),
    ]

    # 4. Merge
    total_mb = gfs_local["total_size_mb"] + ecmwf_local["total_size_mb"] + cams_local["total_size_mb"]
    for d in derived:
        total_mb += d["total_size_mb"]

    overall_status = "current"
    datasets = []

    for r in req["datasets"]:
        source = r["source"]
        local = local_map.get(source, {"file_count": 0, "latest_cycle": "", "cycles": {}})

        latest_available = r.get("latest_cycle", "")
        latest_local = local["latest_cycle"]
        local_files = local["file_count"]
        required_files = r.get("expected_files", 0)

        missing_hours: list[int] = []
        required_hours = r.get("required_hours", r.get("required_lead_hours", []))
        target_cycle = next(
            (cycle for cycle in local.get("cycles", {}).values() if cycle.get("label") == latest_available),
            None,
        )
        if target_cycle is None:
            missing_hours = list(required_hours)
        elif source == "gfs":
            required_bboxes = [tuple(float(value) for value in bbox) for bbox in r.get("bboxes", [])]
            coverage = target_cycle.get("coverage", {})
            for hour in required_hours:
                cached_bboxes = coverage.get(hour, [])
                if not cached_bboxes or any(
                    not any(cached is None or bbox_contains(cached, required) for cached in cached_bboxes)
                    for required in required_bboxes
                ):
                    missing_hours.append(hour)
        elif source == "cams":
            from firesky.aerosol import cams_area_key

            required_area_keys = {cams_area_key(area) for area in r.get("areas", [])}
            area_keys = target_cycle.get("area_keys", {})
            missing_hours = [
                hour
                for hour in required_hours
                if "global" not in area_keys.get(hour, set())
                and not required_area_keys.issubset(area_keys.get(hour, set()))
            ]
        else:
            local_hours = set(target_cycle.get("hours", []))
            missing_hours = [hour for hour in required_hours if hour not in local_hours]

        prepared = True
        if source == "ecmwf" and not missing_hours:
            # Raw EC bundles are not sufficient for the management status:
            # regional scoring consumes the strict, bbox-bound derived cache.
            # This validation hashes/reads metadata and mmaps arrays only; it
            # does not decode GRIB values on the status/page request path.
            try:
                ec_cycle = _parse_data_cycle(latest_available)
                ec_bbox = tuple(
                    float(value)
                    for value in (r.get("bboxes") or [req["region"]["bbox"]])[0]
                )
                raw_paths = [
                    Path(target_cycle["paths"][hour]) for hour in required_hours
                ]
                prepared = (
                    _load_existing_external_derived_cache(
                        "ec",
                        ec_cycle,
                        required_hours,
                        ec_bbox,
                        raw_paths,
                        expected_pressure_levels=ECMWF_IFS_PRESSURE_LEVELS,
                    )
                    is not None
                )
            except (KeyError, OSError, TypeError, ValueError, RuntimeError):
                prepared = False
            if not prepared:
                missing_hours = list(required_hours)

        # Determine status
        if local_files == 0:
            status = "missing"
            overall_status = "update_available"
        elif target_cycle is None or missing_hours:
            status = "update_available"
            overall_status = "update_available"
        else:
            status = "current"

        # Build cycles array (sorted newest first)
        local_cycles = local.get("cycles", {})
        sorted_keys = sorted(local_cycles.keys(), reverse=True)
        cycles_list = []
        for k in sorted_keys:
            c = local_cycles[k]
            cycles_list.append({
                "cycle": c.get("label", k),
                "file_count": c["files"],
                "total_size_mb": round(c["mb"], 2),
                "forecast_hours": c["hours"],
            })

        datasets.append({
            "source": source,
            "status": status,
            "latest_available_cycle": latest_available,
            "latest_local_cycle": latest_local,
            "required_files": required_files,
            "required_hours_count": r.get("required_hours_count", len(required_hours)),
            "region_count": r.get("region_count", 1),
            "regions": r.get("regions", []),
            "local_files": local_files,
            "missing_hours": missing_hours,
            "cycles": cycles_list,
            "prepared": prepared,
            "prepared_message": "",
            "capability_status": r.get("capability_status", "available"),
            "capability_message": r.get("capability_message", ""),
            "unavailable_sample_count": r.get("unavailable_sample_count", 0),
        })

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "total_cache_size_mb": round(total_mb, 2),
        "datasets": datasets,
        "derived": derived,
    }


def _handle_cams_download(payload: dict, progress_callback=None, region: str | None = None) -> dict:
    """Download selected CAMS lead times for one spatial area.

    If *progress_callback* is provided, it is called as
    ``progress_callback(completed, total, label)`` at three points: before
    retrieve (0/N), after each split message (k/N), and after rename (N/N).
    """
    from .aerosol import (
        CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS,
        CAMS_OPTICS_CACHE_PREFIX,
        cams_area_key,
        choose_best_cams_selection,
        retrieve_cams_file,
        split_grib_messages,
    )
    import cdsapi
    import requests as req_lib

    diagnostic = _cams_diagnostic_enabled(payload)

    def _report(completed: int, total: int, label: str) -> None:
        if progress_callback is not None:
            try:
                progress_callback(completed, total, label)
            except Exception:
                pass

    cache_dir = Path(payload.get("cache_dir", ".cache/cams"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    area = payload.get("area")
    now_utc = datetime.now(timezone.utc)
    target_utc = now_utc.replace(minute=0, second=0, microsecond=0)

    lead_hours_list: list[int] | None = payload.get("lead_hours")

    if lead_hours_list is None:
        lead_hours_list = [0]

    lead_hours_list = sorted(set(int(h) for h in lead_hours_list))
    total_files = len(lead_hours_list)

    session = req_lib.Session()
    session.trust_env = False
    client = cdsapi.Client(
        quiet=True, progress=False, timeout=120,
        retry_max=3, sleep_max=5, session=session,
    )
    process = client.client.get_process(
        "cams-global-atmospheric-composition-forecasts"
    )
    base_query: dict = (
        build_spectral_optics_request(area)
        if diagnostic
        else {
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
    )
    if area is not None:
        base_query["area"] = area
    constrained = process.apply_constraints(base_query)
    if payload.get("cycle"):
        requested_cycle = _parse_data_cycle(payload["cycle"])
        run_date = requested_cycle.date()
        run_time = requested_cycle.strftime("%H:00")
    else:
        mid_lead = lead_hours_list[len(lead_hours_list) // 2]
        mid_target = target_utc + timedelta(hours=mid_lead)
        selection = choose_best_cams_selection(process, base_query, constrained, mid_target)
        if selection is None:
            raise RuntimeError("未找到可用的 CAMS 预报数据。")
        run_date, run_time, _ = selection
    area_key = cams_area_key(area) if area is not None else "global"
    cache_area = area if area is not None else [90.0, -180.0, -90.0, 180.0]
    run_utc = datetime.combine(
        run_date,
        datetime.strptime(run_time, "%H:%M").time(),
        tzinfo=timezone.utc,
    )
    run_cache_dir = dated_directory(cache_dir, run_date, f"{run_time[:2]}Z", region=region)
    run_cache_dir.mkdir(parents=True, exist_ok=True)
    name_prefix = (
        f"{SPECTRAL_OPTICS_CACHE_PREFIX}-{run_date:%Y-%m-%d}"
        f"-{run_time.replace(':', '')}-batch"
        if diagnostic
        else (
            f"{CAMS_OPTICS_CACHE_PREFIX}-{run_date.year}"
            f"-{run_date.month:02d}-{run_date.day:02d}"
            f"-{run_time.replace(':', '')}"
        )
    )

    def _target_path(lead: int) -> Path:
        if diagnostic:
            return run_cache_dir / spectral_optics_cache_filename(
                run_utc, lead, cache_area
            )
        return run_cache_dir / f"{name_prefix}-{lead:03d}-{area_key}.grib"

    def _cache_is_valid(target: Path, lead: int) -> bool:
        if not target.is_file() or target.stat().st_size <= 0:
            return False
        try:
            if diagnostic:
                identity = spectral_optics_cache_identity(run_utc, lead, cache_area)
                load_spectral_optics_cache(target, identity)
            else:
                optics = load_cams_optics_grid(target)
                if optics.valid_time != run_utc + timedelta(hours=lead):
                    return False
        except Exception:
            return False
        return True

    existing = 0
    for lead in lead_hours_list:
        target = _target_path(lead)
        candidates = (target, cache_dir / target.name)
        if diagnostic:
            if any(
                _cache_is_valid(candidate, lead)
                for candidate in candidates
            ):
                existing += 1
        elif any(_cache_is_valid(candidate, lead) for candidate in candidates):
            existing += 1

    expected = len(lead_hours_list)
    if existing >= expected:
        return {
            "message": f"CAMS 数据已完整 ({existing} 个文件)",
            "run_date": run_date.isoformat(),
            "run_time": run_time,
            "files": existing,
            "expected": expected,
            "lead_hours": lead_hours_list,
            "cached": True,
            "diagnostic": diagnostic,
            "schema_version": SPECTRAL_OPTICS_SCHEMA_VERSION if diagnostic else None,
        }

    query = {
        **base_query,
        "date": [run_date.isoformat()],
        "time": [run_time],
        "leadtime_hour": [str(h) for h in lead_hours_list],
    }
    batch_tmp = run_cache_dir / f"{name_prefix}-batch-{area_key}.grib.tmp"
    _report(0, total_files, f"CAMS 下载中 {run_date.isoformat()} {run_time}")
    retrieve_cams_file(client, query, batch_tmp)

    _report(0, total_files, "CAMS 拆分中")
    written = split_grib_messages(
        batch_tmp, run_cache_dir, f"{name_prefix}",
        progress_callback=lambda k: _report(k, total_files, f"CAMS 拆分中 {k}/{total_files}"),
    )
    renamed = 0
    if diagnostic:
        for p in written:
            match = re.search(r"-(\d{3})\.grib$", p.name)
            if match is None:
                p.unlink(missing_ok=True)
                raise RuntimeError(f"无法识别 CAMS 诊断文件时效: {p.name}")
            lead = int(match.group(1))
            if lead not in lead_hours_list:
                p.unlink(missing_ok=True)
                continue
            optics = load_cams_spectral_optics(p)
            expected_valid_time = run_utc + timedelta(hours=lead)
            if optics.valid_time != expected_valid_time:
                p.unlink(missing_ok=True)
                raise RuntimeError(
                    f"CAMS 诊断文件 valid time 不匹配: {p.name}"
                )
            final_path = _target_path(lead)
            if _cache_is_valid(final_path, lead):
                p.unlink(missing_ok=True)
                continue
            p.replace(final_path)
            write_spectral_optics_cache_metadata(
                final_path,
                spectral_optics_cache_identity(run_utc, lead, cache_area),
            )
            renamed += 1
            _report(renamed, total_files, f"CAMS 诊断缓存 {renamed}/{total_files}")
    else:
        for p in written:
            stem = p.stem
            final_name = f"{stem}-{area_key}.grib"
            final_path = run_cache_dir / final_name
            if final_path != p:
                if _cache_is_valid(final_path, lead=int(stem.rsplit('-', 1)[-1])):
                    p.unlink()
                else:
                    final_path.unlink(missing_ok=True)
                    p.rename(final_path)
                    renamed += 1
                    _report(renamed, total_files, f"CAMS 整理 {renamed}/{total_files}")
    batch_tmp.unlink(missing_ok=True)

    final_count = 0
    for lead in lead_hours_list:
        target = _target_path(lead)
        candidates = (target, cache_dir / target.name)
        if diagnostic:
            found = any(_cache_is_valid(candidate, lead) for candidate in candidates)
        else:
            found = any(_cache_is_valid(candidate, lead) for candidate in candidates)
        if found:
            final_count += 1

    if diagnostic and final_count != expected:
        raise RuntimeError(
            f"CAMS 诊断缓存不完整: {final_count}/{expected} 个文件"
        )

    return {
        "message": f"CAMS 下载完成: {final_count}/{expected} 个文件",
        "run_date": run_date.isoformat(),
        "run_time": run_time,
        "files": final_count,
        "expected": expected,
        "lead_hours": lead_hours_list,
        "cached": False,
        "diagnostic": diagnostic,
        "schema_version": SPECTRAL_OPTICS_SCHEMA_VERSION if diagnostic else None,
    }


def handle_data_fetch(
    payload: dict,
    config: WebServerConfig | None = None,
) -> dict:
    """Kick off async data fetch; return job_id immediately.

    Endpoint path is unchanged (POST /api/v1/firesky/data/fetch) but now returns
    {job_id, status} instead of waiting for the download to complete. Poll the
    job with GET /api/v1/firesky/data/fetch/status?job_id=...
    """
    job_payload = dict(payload)
    if config is not None:
        for name in (
            "gfs_cache_dir",
            "ecmwf_cache_dir",
            "cams_cache_dir",
        ):
            job_payload[name] = str(getattr(config, name))
    job = DATA_FETCH_JOBS.start(job_payload)
    return {"job_id": job.job_id, "status": "running", "sources": job.sources}


def handle_data_fetch_status(job_id: str) -> dict:
    """Return a snapshot of the data-fetch job's progress."""
    job = DATA_FETCH_JOBS.get(job_id)
    if job is None:
        raise FileNotFoundError("下载任务不存在或已过期。")
    return job.snapshot()


class _DataFetchProcessReporter:
    """Small IPC adapter used by the isolated download worker."""

    def __init__(self, events) -> None:
        self.events = events

    def update(self, source: str, completed: int, total: int, current: str) -> None:
        self.events.put(("progress", (source, completed, total, current)))


def _run_data_fetch_process(payload: dict, events) -> None:
    """Run downloads outside the API/forecast Python process.

    ``spawn`` keeps eccodes, NumPy and any native forecast libraries out of the
    download worker's parent process. The parent receives only small progress
    messages and the final list of cache paths.
    """
    try:
        results = _run_data_fetch(payload, _DataFetchProcessReporter(events))
    except BaseException as exc:
        terminal_event = ("failed", f"{type(exc).__name__}: {exc}")
    else:
        terminal_event = ("done", results)

    # multiprocessing.Queue uses a feeder thread in the child.  Without
    # joining that thread, the process can exit successfully before the
    # terminal event reaches the parent, which is then misreported as
    # ``exit code 0``.  Flush the terminal event before returning from the
    # worker; the fake queues used by unit tests need not implement the
    # optional lifecycle methods.
    events.put(terminal_event)
    close = getattr(events, "close", None)
    join_thread = getattr(events, "join_thread", None)
    if callable(close):
        close()
    if callable(join_thread):
        join_thread()


def _run_data_fetch(payload: dict, job: DataFetchJob) -> list[dict]:
    """Execute the exact data requirements plan and report progress."""
    sources = payload.get("sources", ["gfs", "ecmwf", "cams"])
    normalized_sources = ["ecmwf" if source == "ec" else str(source).lower() for source in sources]
    region = payload.get("region")
    # A model-only scheduled download has no observer to infer a footprint
    # from. Keep the historical east-Asia default for GFS/CAMS and use the
    # China regional contract for EC-only jobs.
    plan_payload = {**payload, "sources": normalized_sources}
    external_only = bool(normalized_sources) and set(normalized_sources).issubset(
        {"ecmwf"}
    )
    if external_only and not payload.get("region") and not payload.get("bbox"):
        plan_payload["region"] = "china"
        plan_payload["bbox"] = list(REGION_BBOXES["china"])
        region = "china"
    plan = handle_data_requirements(plan_payload)
    datasets = {item["source"]: item for item in plan["datasets"]}
    results: list[dict] = []

    for src in [source for source in normalized_sources if source in ("gfs", "ecmwf")]:
        requirement = datasets[src]
        cycle = _parse_data_cycle(requirement["latest_cycle"])
        model_source = "ec" if src == "ecmwf" else src
        forecast_hours = [int(value) for value in requirement.get("required_hours", [])]
        bboxes = [tuple(float(value) for value in bbox) for bbox in requirement.get("bboxes", [])]
        expected_total = int(requirement.get("expected_files", len(forecast_hours)))
        progress_total = expected_total + 1 if src == "ecmwf" else expected_total

        def progress_cb(completed: int, total: int, label: str, src=src) -> None:
            job.update(
                src,
                min(expected_total, int(completed)),
                progress_total,
                f"{src} 下载阶段：{label}" if src == "ecmwf" else label,
            )

        job.update(
            src,
            0,
            progress_total,
            f"{src} 下载阶段：准备中" if src == "ecmwf" else f"{src} 准备中",
        )
        prefetch_kwargs = {
            "model_source": model_source,
            "cycle_utc": cycle,
            "forecast_hours": forecast_hours,
            "regions": [],
            "progress_callback": progress_cb,
            "bboxes": bboxes,
            "region": region,
        }
        if src == "gfs":
            prefetch_kwargs["gfs_cache_dir"] = Path(
                payload.get("gfs_cache_dir", ".cache/gfs")
            )
        if src == "ecmwf":
            prefetch_kwargs["ecmwf_cache_dir"] = Path(
                payload.get("ecmwf_cache_dir", ".cache/ecmwf")
            )
            prefetch_kwargs["ecmwf_source"] = str(
                payload.get("ecmwf_source", "ecmwf")
            )
        if _gfs_diagnostic_enabled(payload):
            prefetch_kwargs["include_diagnostic_fields"] = True
        result = prefetch_cache(**prefetch_kwargs)
        if src == "ecmwf":
            if result.failed:
                raise RuntimeError(
                    "EC 下载失败，未开始 prepared 派生：" + "; ".join(result.failed)
                )
            job.update(src, expected_total, progress_total, "EC 派生阶段：prepared 预热中")
            adapter = EcmwfIfsAdapter(
                cache_dir=Path(payload.get("ecmwf_cache_dir", ".cache/ecmwf")),
                source=str(payload.get("ecmwf_source", "ecmwf")),
            )
            requested_ec_bbox = bboxes[0] if bboxes else tuple(
                float(value)
                for value in (payload.get("bbox") or REGION_BBOXES.get(region or "") or REGION_BBOXES["east-asia"])
            )
            ec_bbox = _external_prepared_bbox(
                "ec",
                requested_ec_bbox,
                _analysis_radius_from_payload(payload),
            )

            def load_ec_grid(cycle_value: datetime, hour: int):
                tensor = adapter.load_tensor(cycle_value, hour, bbox=ec_bbox)
                grid, _, _, _ = _tensor_to_native_grid(tensor)
                return grid, tensor.source_file

            def ec_prepare_progress(item: dict[str, Any]) -> None:
                message = str(item.get("message") or "准备中")
                job.update(src, expected_total, progress_total, f"EC 派生阶段：{message}")

            prepared = prepare_external_model_columns(
                "ec",
                cycle,
                forecast_hours,
                load_ec_grid,
                bbox=ec_bbox,
                progress=ec_prepare_progress,
            )
            if not prepared.layer_profile_path:
                raise RuntimeError("EC prepared national-layer-profile cache 未生成")
        result_payload = {"source": src, **result.to_dict()}
        if src == "ecmwf":
            job.update(src, progress_total, progress_total, "EC 下载与派生完成")
            result_payload.update({
                "files": len(forecast_hours),
                "forecast_hours": forecast_hours,
                "prepared": True,
                "layer_profile_path": prepared.layer_profile_path,
            })
        if src == "gfs" and _gfs_diagnostic_enabled(payload):
            result_payload["diagnostic"] = True
            result_payload["schema_version"] = GFS_DIAGNOSTIC_REQUEST_SCHEMA_VERSION
        results.append(result_payload)

    if "cams" in normalized_sources:
        requirement = datasets["cams"]
        cams_lead_hours = [int(value) for value in requirement.get("required_lead_hours", [])]
        areas = requirement.get("areas") or [None]
        cams_total = len(cams_lead_hours) * len(areas)
        job.update("cams", 0, cams_total, "CAMS 准备中")
        cams_downloaded: list[str] = []
        for area_index, area in enumerate(areas):
            offset = area_index * len(cams_lead_hours)

            def cams_progress_cb(completed: int, total: int, label: str, offset=offset) -> None:
                job.update("cams", offset + completed, cams_total, label)

            _handle_cams_download(
                {
                    **payload,
                    "cache_dir": payload.get("cams_cache_dir", ".cache/cams"),
                    "cycle": requirement["latest_cycle"],
                    "lead_hours": cams_lead_hours,
                    "area": area,
                },
                progress_callback=cams_progress_cb,
                region=region,
            )
            cams_downloaded.extend(
                f"cams {requirement['latest_cycle']} f{lead:03d} area={area}"
                for lead in cams_lead_hours
            )
        job.update("cams", cams_total, cams_total, "CAMS 完成")
        results.append({
            "source": "cams",
            "downloaded": cams_downloaded,
            "failed": [],
            "removed": [],
            "files": cams_total,
        })

    # Invalidate the cycle-availability cache so the next status poll sees
    # the freshly-downloaded cycle reflected as latest_local_cycle.
    try:
        from firesky.cycle_availability import clear_cycle_availability_cache
        clear_cycle_availability_cache()
    except Exception:
        pass

    return results


def handle_data_prune(payload: dict) -> dict:
    """Remove old cached data files."""
    cache_root = payload.get("cache_root", ".cache")
    max_age_days = payload.get("max_age_days", 2)
    result = prune_cache(cache_root=cache_root, max_age_days=max_age_days)
    return {"message": "prune completed", **result.to_dict()}


# ── Cache warmup (forecast cache, not data assets) ──

def handle_cache_warmup(payload: dict, config: WebServerConfig) -> dict:
    """Pre-compute forecast caches for hot cities."""
    cities = payload.get("cities", [])
    if not cities:
        return {"message": "no cities specified", "computed": 0}
    for index, city in enumerate(cities):
        if not isinstance(city, dict) or "elevation_m" not in city:
            raise ValueError(f"warmup city {index} requires elevation_m")
        elevation = city["elevation_m"]
        if (
            isinstance(elevation, bool)
            or not isinstance(elevation, (int, float))
            or not math.isfinite(float(elevation))
            or not -500.0 <= float(elevation) <= 9000.0
        ):
            raise ValueError(f"warmup city {index} elevation_m is invalid")

    computed = 0
    errors = []
    for city in cities:
        try:
            request_payload = {
                "name": city.get("name", "cache"),
                "lat": city["lat"],
                "lon": city["lon"],
                "elevation_m": city["elevation_m"],
                "timezone_name": city.get("timezone_name", "Asia/Shanghai"),
                "target_date": payload.get("target_date", china_today().isoformat()),
                "phenomenon": payload.get("phenomenon", "dusk"),
                "model_source": payload.get("model_source", "gfs"),
                "satellite_source": payload.get("satellite_source", "off"),
            }
            run_forecast_payload(request_payload, config)
            computed += 1
        except Exception as exc:
            errors.append({"city": city.get("name", "?"), "error": str(exc)})

    return {"message": f"warmup completed: {computed}/{len(cities)}", "computed": computed, "errors": errors}


def validate_satellite_cache_warmup_payload(payload: dict) -> tuple[float, float, float, datetime]:
    required = {"lat", "lon", "radius_km", "reference_time_utc"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError(
            "satellite preparation requires exactly lat, lon, radius_km and reference_time_utc"
        )
    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
        radius_km = float(payload["radius_km"])
    except (TypeError, ValueError) as exc:
        raise ValueError("satellite preparation coordinates/radius are invalid") from exc
    if (
        not math.isfinite(lat)
        or not math.isfinite(lon)
        or not -90.0 <= lat <= 90.0
        or not -180.0 <= lon <= 180.0
        or not math.isfinite(radius_km)
        or radius_km <= 0.0
        or radius_km > 1200.0
    ):
        raise ValueError("satellite preparation coordinates/radius are outside limits")
    reference = parse_utc_iso_datetime(
        str(payload["reference_time_utc"]), "reference_time_utc"
    )
    return lat, lon, radius_km, reference


def _warm_nowcast_gfs_prior(
    config: WebServerConfig,
    point: GeoPoint,
    radius_km: float,
    valid_times: list[datetime],
    *,
    cycle_override: datetime | None = None,
    resolution: str = "0p25",
    region: str | None = None,
) -> dict[str, Any]:
    """Prefetch the same-cycle GFS decoded subsets the nowcast prior needs.

    Synchronous nowcast scoring is cache-only: a missing vertical prior fails
    the request instead of downloading mid-request.  This background warmup
    reuses the provider's exact canonical footprint so a warmed scan window
    can actually be scored.
    """
    from firesky.gfs import LIGHTPATH_FETCH_RADIUS_KM
    from firesky.service import choose_model_cycle

    warmed: list[str] = []
    errors: list[str] = []
    client = GfsNomadsClient(
        cache_dir=config.gfs_cache_dir,
        feature_cache_enabled=False,
        shared_decode_enabled=True,
        resolution=resolution,
        region=region,
    )
    grid_step = float(getattr(client, "_res_grid_deg", 0.25))
    sampling_radius_km = max(float(radius_km), float(LIGHTPATH_FETCH_RADIUS_KM))
    interpolation_padding_km = max(35.0, 2.0 * grid_step * 111.2)
    bbox = canonical_subset_bbox(
        bounding_box(point, sampling_radius_km + interpolation_padding_km),
        grid_step,
    )
    seen: set[tuple[datetime, int]] = set()
    for valid_time in valid_times:
        try:
            cycle = choose_model_cycle(
                "gfs", valid_time.astimezone(timezone.utc), cycle_override
            )
            elapsed_hours = (valid_time.astimezone(timezone.utc) - cycle).total_seconds() / 3600.0
            if elapsed_hours < -1e-6:
                continue
            left_hour = max(0, int(math.floor(elapsed_hours)))
            hours = {left_hour}
            if abs(elapsed_hours - left_hour) >= 1e-6:
                hours.add(left_hour + 1)
            for hour in sorted(hours):
                key = (cycle, hour)
                if key in seen:
                    continue
                seen.add(key)
                client.load_decoded_subset(cycle, hour, bbox)
                warmed.append(f"gfs {cycle:%Y-%m-%dT%HZ} f{hour:03d}")
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    return {"warmed": warmed, "errors": errors, "bbox": [round(float(v), 4) for v in bbox]}


def _warm_nowcast_hsd(
    config: WebServerConfig,
    point: GeoPoint,
    radius_km: float,
    reference_utc: datetime,
) -> dict[str, Any]:
    """Prepare one complete, same-source HSD motion pair before scoring."""
    hsd = AutoHsdCompactCacheProvider(
        config.satellite_cache_dir,
        allow_foreground=True,
    )
    prepared = hsd.prepare_series(
        reference_utc, point, radius_km=radius_km, count=2
    )
    return {
        "source": "hsd",
        "warmed": len(prepared),
        "errors": [] if len(prepared) >= 2 else ["HSD motion pair is incomplete"],
    }


def handle_satellite_cache_warmup(payload: dict, config: WebServerConfig) -> dict:
    """Download one footprint's calibrated Himawari HSD frame."""
    lat, lon, radius_km, reference = validate_satellite_cache_warmup_payload(payload)
    point = GeoPoint(lat, lon)
    root = Path(config.satellite_cache_dir)
    compact = HsdCompactCache(root / "compact")
    existing = compact.read(reference, point, radius_km)
    if existing is not None:
        return {
            "status": "ready",
            "reference_time_utc": reference.isoformat(),
            "valid_time_utc": existing.valid_time_utc.isoformat(),
            "point": {"lat": lat, "lon": lon},
            "radius_km": radius_km,
        }
    remote = SegmentedHimawariHsdFrameProvider(
        root,
        max_age_minutes=30.0,
        lookback_minutes=60,
        max_workers=3,
        retries=3,
        request_timeout_seconds=20.0,
    )
    path = HsdCacheWarmer(remote, compact).warm(reference, point, radius_km)
    if path is None:
        return {
            "status": "unavailable",
            "reason": "no_recent_complete_hsd_scan",
            "reference_time_utc": reference.isoformat(),
            "point": {"lat": lat, "lon": lon},
            "radius_km": radius_km,
        }
    prepared = compact.read(reference, point, radius_km)
    if prepared is None:
        raise RuntimeError("Himawari HSD compact cache was not readable after atomic write")
    prior_warmup = _warm_nowcast_gfs_prior(
        config,
        point,
        radius_km,
        [reference, prepared.valid_time_utc],
    )
    return {
        "status": "warmed",
        "reference_time_utc": reference.isoformat(),
        "valid_time_utc": prepared.valid_time_utc.isoformat(),
        "point": {"lat": lat, "lon": lon},
        "radius_km": radius_km,
        "gfs_prior_warmup": prior_warmup,
    }
