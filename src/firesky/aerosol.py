from __future__ import annotations

import atexit
import json
import hashlib
import math
import os
import re
import threading
import urllib.parse
import urllib.request
from contextlib import contextmanager
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

import numpy as np
from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release, codes_write

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM
from firesky.geometry import (
    destination_point,
    normalize_lon,
    solar_event_time_local,
    solar_snapshot,
)
from firesky.models import AerosolReading, AerosolSample, ForecastRequest, GeoPoint
from firesky.storage import dated_directory


CAMS_PATH_DISTANCE_KM = DEFAULT_ANALYSIS_RADIUS_KM
CAMS_PATH_HALF_WIDTH_KM = 150.0
CAMS_NATIVE_GRID_SPACING_DEG = 0.4
# The ADS area snaps to native cell centres, which can shave up to half a
# native step off every edge of the returned file, and the external-model
# target axes round outward by up to half a model step.  Pad the requested
# area by one native spacing plus both rounding margins so a successfully
# downloaded file always covers the grid it will be mapped onto.
CAMS_AREA_SAFETY_PAD_DEG = CAMS_NATIVE_GRID_SPACING_DEG + 0.2
CAMS_DOWNLOAD_LOCK_TIMEOUT_SECONDS = 300.0
# ADS allows a few concurrent queued requests per user.  A bounded semaphore
# (not a global mutex) lets prefetch overlap several bracket downloads while
# still capping concurrency; concurrent writers of the same target stay safe
# because retrieve_cams_file writes to a unique temp file and atomically
# renames it into place.
def _cams_download_worker_limit() -> int:
    raw = os.environ.get("FIRESKY_CAMS_DOWNLOAD_WORKERS", "2")
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = 2
    return max(1, min(4, value))


_CAMS_DOWNLOAD_SLOTS = threading.BoundedSemaphore(_cams_download_worker_limit())
_CAMS_GRID_CACHE_LIMIT = 8
_CAMS_GRID_CACHE: OrderedDict[
    tuple[str, int, int],
    CamsOpticsGrid,
] = OrderedDict()
_CAMS_GRID_CACHE_LOCK = threading.Lock()
_CAMS_AREA_CACHE_LIMIT = 8192
_CAMS_AREA_CACHE: OrderedDict[tuple[str, int, int], tuple[float, float, float, float]] = OrderedDict()
_CAMS_AREA_CACHE_LOCK = threading.Lock()
# Persistent per-directory sidecar that remembers the geographic footprint of
# every cached CAMS file.  A cold server process otherwise re-opens hundreds
# of GRIB headers on the synchronous forecast path just to answer
# "does this file cover the area".
_CAMS_AREA_SIDECAR_NAME = ".cams-area-index-v1.json"
_CAMS_AREA_SIDECARS: dict[Path, dict[str, dict[str, Any]]] = {}
_CAMS_AREA_SIDECAR_LOCK = threading.Lock()
_CAMS_AREA_SIDECAR_DIRTY: set[Path] = set()
_CAMS_PROBE_CACHE_LIMIT = 2048
_CAMS_PROBE_CACHE: OrderedDict[tuple[str, int, int], bool] = OrderedDict()
_CAMS_PROBE_CACHE_LOCK = threading.Lock()


@contextmanager
def _cams_download_guard():
    if not _CAMS_DOWNLOAD_SLOTS.acquire(timeout=CAMS_DOWNLOAD_LOCK_TIMEOUT_SECONDS):
        raise TimeoutError("等待 CAMS 下载并发槽超时。")
    try:
        yield
    finally:
        _CAMS_DOWNLOAD_SLOTS.release()


def _cams_optics_file_valid(path: Path) -> bool:
    """Memoized full-load validation for bracket candidates.

    ``find_cached_cams_bracket`` validates every candidate by loading its
    complete optics grid; repeated bracket searches on the synchronous
    forecast path re-paid that decode for the same unchanged files.  The
    result is cached per (path, mtime, size) so a file is decoded once per
    process while the load-failure contract is preserved.
    """
    stat = path.stat()
    probe_key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _CAMS_PROBE_CACHE_LOCK:
        cached = _CAMS_PROBE_CACHE.get(probe_key)
        if cached is not None:
            _CAMS_PROBE_CACHE.move_to_end(probe_key)
            return cached
    try:
        load_cams_optics_grid(path)
        valid = True
    except Exception:
        valid = False
    with _CAMS_PROBE_CACHE_LOCK:
        _CAMS_PROBE_CACHE[probe_key] = valid
        _CAMS_PROBE_CACHE.move_to_end(probe_key)
        while len(_CAMS_PROBE_CACHE) > _CAMS_PROBE_CACHE_LIMIT:
            _CAMS_PROBE_CACHE.popitem(last=False)
    return valid

CAMS_OPTICS_CACHE_PREFIX = "cams-optics-v2"
CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS = (
    1000,
    950,
    925,
    900,
    850,
    800,
    700,
    600,
    500,
    400,
    300,
)
CAMS_AEROSOL_PROFILE_COUNT = len(CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS)
CAMS_AEROSOL_PROFILE_HEIGHTS_ASL_M = tuple(
    44_330.0 * (1.0 - (level / 1013.25) ** 0.1903)
    for level in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
)
CAMS_AOD550_VALIDATION_MAX = 100.0
CAMS_DEFAULT_AEROSOL_SCALE_HEIGHT_KM = 1.2
CAMS_MAX_CACHE_VALID_TIME_DELTA = timedelta(hours=2)
_CAMS_CACHE_NAME = re.compile(
    rf"^{CAMS_OPTICS_CACHE_PREFIX}-(\d{{4}})-(\d{{2}})-(\d{{2}})"
    r"-(\d{4})-(\d{3})-([^.]+)\.grib$"
)


def pad_cams_area_to_native_grid(
    area: list[float] | tuple[float, float, float, float],
) -> list[float]:
    """Pad a CAMS [north, west, south, east] area by one native grid cell.

    CAMS returns native grid-cell centres.  A request boundary that lands
    between centres is rounded inward by the provider, so model-grid edge
    points can otherwise be absent from the returned field.  Padding is
    conservative and does not turn missing data into extrapolated values.
    """
    if len(area) != 4:
        raise ValueError("CAMS area must contain north, west, south and east")
    north, west, south, east = (float(value) for value in area)
    if not all(math.isfinite(value) for value in (north, west, south, east)):
        raise ValueError("CAMS area must contain finite values")
    if north <= south or east <= west:
        raise ValueError("CAMS area bounds are invalid")
    spacing = CAMS_NATIVE_GRID_SPACING_DEG
    return [
        round(min(90.0, north + spacing), 3),
        round(max(-180.0, west - spacing), 3),
        round(max(-90.0, south - spacing), 3),
        round(min(180.0, east + spacing), 3),
    ]


def cams_area_from_model_bbox(
    bbox: list[float] | tuple[float, float, float, float],
) -> list[float]:
    """Convert a model ``(west, east, north, south)`` bbox to a padded ADS area.

    CAMS native cells are spaced by ``CAMS_NATIVE_GRID_SPACING_DEG``.  Keeping
    at least one native spacing beyond every model edge prevents an edge row
    or column from being omitted when the model boundary falls between CAMS
    cell centres.  This only broadens the source request; model coverage and
    interpolation remain strict and never extrapolate.
    """
    if len(bbox) != 4:
        raise ValueError("model bbox must contain west, east, north and south")
    west, east, north, south = (float(value) for value in bbox)
    if not all(math.isfinite(value) for value in (west, east, north, south)):
        raise ValueError("model bbox must contain finite values")
    if east <= west or north <= south:
        raise ValueError("model bbox bounds are invalid")
    spacing = CAMS_NATIVE_GRID_SPACING_DEG
    pad = CAMS_AREA_SAFETY_PAD_DEG
    return [
        round(min(90.0, north + pad), 3),
        round(west - pad, 3),
        round(max(-90.0, south - pad), 3),
        round(east + pad, 3),
    ]


@dataclass(frozen=True)
class CamsOpticsGrid:
    path: Path
    valid_time: datetime
    lats: np.ndarray
    lons: np.ndarray
    aod550: np.ndarray
    aerext532_m_inv: np.ndarray


@dataclass(frozen=True)
class CamsProfileFileSelection:
    path: Path
    run_date: date
    run_time: str
    lead_hours: int
    run_time_utc: datetime
    valid_time: datetime


@dataclass(frozen=True)
class CamsProfileBracket:
    target_time: datetime
    left: CamsProfileFileSelection
    right: CamsProfileFileSelection
    right_weight: float


class AerosolProvider:
    def get_reading(self, request: ForecastRequest) -> Optional[AerosolReading]:
        raise NotImplementedError


@dataclass
class OverrideAerosolProvider(AerosolProvider):
    def get_reading(self, request: ForecastRequest) -> Optional[AerosolReading]:
        if request.aod550_override is None:
            return None
        return AerosolReading(aod550=request.aod550_override, source='cli-override', quality='override')


@dataclass
class DefaultAerosolProvider(AerosolProvider):
    default_aod550: float = 0.12

    def get_reading(self, request: ForecastRequest) -> Optional[AerosolReading]:
        return AerosolReading(aod550=self.default_aod550, source='default-heuristic', quality='fallback')


@dataclass
class JsonAerosolProvider(AerosolProvider):
    path: Path
    fallback_aod550: float = 0.12

    def get_reading(self, request: ForecastRequest) -> Optional[AerosolReading]:
        data = json.loads(self.path.read_text(encoding='utf-8'))
        value = float(data.get('aod550', self.fallback_aod550))
        source = str(data.get('source', self.path.name))
        return AerosolReading(aod550=value, source=source, quality='file')


@dataclass
class CamsAdsAerosolProvider(AerosolProvider):
    cache_dir: Path = Path('.cache/cams')
    strict: bool = False
    timeout_seconds: int = 25
    retry_max: int = 2
    sleep_max_seconds: int = 3
    fixed_area: Optional[List[float]] = None
    include_path_samples: bool = True
    auto_download: bool = False

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_reading(self, request: ForecastRequest) -> Optional[AerosolReading]:
        target_utc = aerosol_target_time_utc(request)
        path_azimuth_deg = solar_snapshot(
            request.point, target_utc, request.timezone_name
        ).azimuth_deg
        corridor_area = cams_corridor_area(
            request.point,
            path_azimuth_deg,
            max_distance_km=max(0.0, float(request.radius_km)),
        )
        area = self.fixed_area or corridor_area
        cached_selection = find_cached_cams_file(self.cache_dir, area, target_utc)
        # Download regions deliberately overlap. A point can therefore be assigned
        # to a different region than the cached file that still covers its entire
        # sunlight path. Treat the fixed region as a preference, not a requirement.
        if cached_selection is None and self.fixed_area is not None:
            cached_selection = find_cached_cams_file(
                self.cache_dir, corridor_area, target_utc
            )
        if cached_selection is None and self.auto_download:
            self.download_target(area, target_utc)
            cached_selection = find_cached_cams_file(self.cache_dir, area, target_utc)
        if cached_selection is None:
            if self.strict:
                raise RuntimeError(
                    'No cached CAMS file found. Run prefetch-cams first.'
                )
            return None
        target, run_date, run_time, lead_hours = cached_selection
        try:
            return cams_reading_from_file(
                target,
                request,
                path_azimuth_deg,
                run_date,
                run_time,
                lead_hours,
                include_path_samples=self.include_path_samples,
            )
        except RuntimeError as exc:
            # Geometry/coverage failures ("corridor did not contain usable
            # AOD samples") mean the file is fine but this request's fan has
            # no on-topic samples; the cached GRIB is shared with the strict
            # profile path and must survive. Only undecodable files are
            # evicted.
            if "usable AOD samples" in str(exc):
                if self.strict:
                    raise
                return None
            target.unlink(missing_ok=True)
            if self.strict:
                raise
            return None
        except Exception:
            target.unlink(missing_ok=True)
            if self.strict:
                raise
            return None

    def download_target(
        self,
        area: List[float],
        target_utc: datetime,
        *,
        require_exact_valid_time: bool = False,
    ) -> Path:
        import cdsapi
        import requests

        with _cams_download_guard():
            if require_exact_valid_time:
                left_valid, right_valid = cams_profile_bracket_times(target_utc)
                if left_valid != right_valid:
                    raise ValueError(
                        'Exact CAMS profile downloads require a 3-hour valid time.'
                    )
                bracket = find_cached_cams_bracket(
                    self.cache_dir, area, target_utc
                )
                cached = (
                    (
                        bracket.left.path,
                        bracket.left.run_date,
                        bracket.left.run_time,
                        bracket.left.lead_hours,
                    )
                    if bracket is not None
                    else None
                )
            else:
                cached = find_cached_cams_file(self.cache_dir, area, target_utc)
            if cached is not None:
                return cached[0]
            session = requests.Session()
            session.trust_env = False
            client = cdsapi.Client(
                quiet=True,
                progress=False,
                timeout=max(120, self.timeout_seconds),
                retry_max=max(3, self.retry_max),
                sleep_max=max(5, self.sleep_max_seconds),
                session=session,
            )
            process = client.client.get_process(
                'cams-global-atmospheric-composition-forecasts'
            )
            base_query = {
                'variable': [
                    'total_aerosol_optical_depth_550nm',
                    'aerosol_extinction_coefficient_532nm',
                ],
                'pressure_level': [
                    str(level) for level in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
                ],
                'type': ['forecast'],
                'data_format': 'grib',
                'area': area,
            }
            constrained = process.apply_constraints(base_query)
            selection = choose_best_cams_selection(
                process, base_query, constrained, target_utc
            )
            if selection is None:
                raise RuntimeError(
                    f'No CAMS forecast run is available for {target_utc:%Y-%m-%d %H:%MZ}.'
                )
            run_date, run_time, lead_hours = selection
            if require_exact_valid_time:
                run_utc = datetime.combine(
                    run_date,
                    datetime.strptime(run_time, '%H:%M').time(),
                    tzinfo=timezone.utc,
                )
                if run_utc + timedelta(hours=lead_hours) != target_utc.astimezone(
                    timezone.utc
                ):
                    raise RuntimeError(
                        f'CAMS did not provide exact valid time {target_utc:%Y-%m-%d %H:%MZ}.'
                    )
            area_key = cams_area_key(area)
            target_dir = dated_directory(self.cache_dir, run_date, f'{run_time[:2]}Z')
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / (
                f'{CAMS_OPTICS_CACHE_PREFIX}-{run_date:%Y-%m-%d}'
                f'-{run_time.replace(":", "")}'
                f'-{lead_hours:03d}-{area_key}.grib'
            )
            if target.exists() and target.stat().st_size > 0:
                try:
                    if _cams_optics_file_valid(target):
                        return target
                except Exception:
                    pass
                target.unlink(missing_ok=True)
            # ADS occasionally publishes a lead time whose aerext532 profile is
            # all zeros while aod550 is populated (observed on the step-0
            # analysis of recent runs). Fall back to a neighbouring lead so one
            # bad step cannot block an entire forecast.
            eligible_leads = {
                int(value)
                for value in constrained.get('leadtime_hour', [str(lead_hours)])
            }
            fallback_leads = [
                candidate
                for candidate in (lead_hours, lead_hours + 3, lead_hours - 3, lead_hours + 6)
                if candidate >= 0 and (not eligible_leads or candidate in eligible_leads)
            ] or [lead_hours]
            last_error: Optional[Exception] = None
            for attempt_lead in fallback_leads:
                attempt_target = target_dir / (
                    f'{CAMS_OPTICS_CACHE_PREFIX}-{run_date:%Y-%m-%d}'
                    f'-{run_time.replace(":", "")}'
                    f'-{attempt_lead:03d}-{area_key}.grib'
                )
                if attempt_target.exists() and attempt_target.stat().st_size > 0:
                    try:
                        if _cams_optics_file_valid(attempt_target):
                            return attempt_target
                    except Exception:
                        pass
                    attempt_target.unlink(missing_ok=True)
                retrieve_cams_file(
                    client,
                    {
                        **base_query,
                        'date': [run_date.isoformat()],
                        'time': [run_time],
                        'leadtime_hour': [str(attempt_lead)],
                    },
                    attempt_target,
                )
                try:
                    load_cams_optics_grid(attempt_target)
                except Exception as exc:
                    # Do not leave a provider-side partial or semantically
                    # invalid GRIB in the cache for a later strict forecast.
                    attempt_target.unlink(missing_ok=True)
                    last_error = exc
                    continue
                return attempt_target
            raise RuntimeError(
                f'Downloaded CAMS optical profile failed strict validation: {last_error}'
            )


    def download_targets_batch(
        self,
        area: List[float],
        target_utcs: Sequence[datetime],
        *,
        require_exact_valid_time: bool = False,
    ) -> dict[datetime, Path]:
        """Download every missing CAMS valid time with as few ADS requests as possible.

        ADS 单请求排队以分钟计，是冷启动实况/区域预测的主要时延。把同一
        run 的多个 lead 合并为一次 retrieve（leadtime_hour 列表），再按
        GRIB 消息的 step 拆分写回与 :meth:`download_target` 同名的缓存
        文件，下游 bracket 查找无需感知。选择/严格校验语义与单点路径一致；
        批量中校验失败的目标不在返回值里，由调用方回退到单点路径（其自带
        ±3/±6 h 邻近 lead 回退）。
        """
        import cdsapi
        import requests

        results: dict[datetime, Path] = {}
        missing: list[datetime] = []
        for target_utc in target_utcs:
            if require_exact_valid_time:
                bracket = find_cached_cams_bracket(self.cache_dir, area, target_utc)
                if bracket is not None and bracket.left.path == bracket.right.path:
                    results[target_utc] = bracket.left.path
                    continue
            else:
                cached = find_cached_cams_file(self.cache_dir, area, target_utc)
                if cached is not None:
                    results[target_utc] = cached[0]
                    continue
            missing.append(target_utc)
        if not missing:
            return results

        with _cams_download_guard():
            session = requests.Session()
            session.trust_env = False
            client = cdsapi.Client(
                quiet=True,
                progress=False,
                timeout=max(120, self.timeout_seconds),
                retry_max=max(3, self.retry_max),
                sleep_max=max(5, self.sleep_max_seconds),
                session=session,
            )
            process = client.client.get_process(
                'cams-global-atmospheric-composition-forecasts'
            )
            base_query = {
                'variable': [
                    'total_aerosol_optical_depth_550nm',
                    'aerosol_extinction_coefficient_532nm',
                ],
                'pressure_level': [
                    str(level) for level in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
                ],
                'type': ['forecast'],
                'data_format': 'grib',
                'area': area,
            }
            constrained = process.apply_constraints(base_query)
            area_key = cams_area_key(area)

            # 逐目标选 run 并按 run 分组（跨 run 的目标各自成批）。
            groups: dict[tuple[date, str], list[datetime]] = {}
            for target_utc in missing:
                selection = choose_best_cams_selection(
                    process, base_query, constrained, target_utc
                )
                if selection is None:
                    continue
                run_date, run_time, _ = selection
                groups.setdefault((run_date, run_time), []).append(target_utc)

            for (run_date, run_time), targets in groups.items():
                try:
                    leads = sorted(
                        {
                            int(
                                (
                                    target_utc.astimezone(timezone.utc)
                                    - datetime.combine(
                                        run_date,
                                        datetime.strptime(
                                            run_time, '%H:%M'
                                        ).time(),
                                        tzinfo=timezone.utc,
                                    )
                                ).total_seconds()
                                // 3600
                            )
                            for target_utc in targets
                        }
                    )
                    if any(lead < 0 for lead in leads):
                        continue
                    target_dir = dated_directory(
                        self.cache_dir, run_date, f'{run_time[:2]}Z'
                    )
                    target_dir.mkdir(parents=True, exist_ok=True)
                    bundle = target_dir / (
                        f'{CAMS_OPTICS_CACHE_PREFIX}-batch-'
                        f'{run_date:%Y-%m-%d}-{run_time.replace(":", "")}'
                        f'-{area_key}.tmp-{os.getpid()}-{threading.get_ident()}'
                    )
                    retrieve_cams_file(
                        client,
                        {
                            **base_query,
                            'date': [run_date.isoformat()],
                            'time': [run_time],
                            'leadtime_hour': [str(lead) for lead in leads],
                        },
                        bundle,
                    )
                    written = _split_cams_bundle_by_lead(
                        bundle,
                        target_dir,
                        run_date,
                        run_time,
                        area_key,
                    )
                    bundle.unlink(missing_ok=True)
                    for target_utc in targets:
                        lead = int(
                            (
                                target_utc.astimezone(timezone.utc)
                                - datetime.combine(
                                    run_date,
                                    datetime.strptime(run_time, '%H:%M').time(),
                                    tzinfo=timezone.utc,
                                )
                            ).total_seconds()
                            // 3600
                        )
                        path = written.get(lead)
                        if path is None or not path.is_file():
                            continue
                        try:
                            load_cams_optics_grid(path)
                        except Exception:
                            # 严格校验失败（如全零剖面）→ 留给单点路径的
                            # 邻近 lead 回退处理，不留坏文件。
                            path.unlink(missing_ok=True)
                            continue
                        results[target_utc] = path
                except Exception:
                    # 整组失败 → 该组目标全部回退到单点路径。
                    continue
        return results


def _split_cams_bundle_by_lead(
    bundle: Path,
    target_dir: Path,
    run_date: date,
    run_time: str,
    area_key: str,
) -> dict[int, Path]:
    """Split a multi-lead CAMS GRIB into per-lead cache files (atomic)."""
    written: dict[int, Path] = {}
    handles: dict[int, tuple[Path, Any]] = {}
    try:
        with bundle.open('rb') as input_handle:
            while True:
                gid = codes_grib_new_from_file(input_handle)
                if gid is None:
                    break
                try:
                    lead = _grib_message_step(gid)
                    if lead in handles:
                        _, output = handles[lead]
                    else:
                        final = target_dir / (
                            f'{CAMS_OPTICS_CACHE_PREFIX}-{run_date:%Y-%m-%d}'
                            f'-{run_time.replace(":", "")}'
                            f'-{lead:03d}-{area_key}.grib'
                        )
                        temporary = final.with_suffix(
                            final.suffix + f'.tmp-{os.getpid()}-{threading.get_ident()}'
                        )
                        output = temporary.open('wb')
                        handles[lead] = (final, temporary, output)
                    codes_write(gid, output)
                finally:
                    codes_release(gid)
    finally:
        for entry in handles.values():
            final, temporary, output = entry
            output.flush()
            os.fsync(output.fileno())
            output.close()
        # 全部句柄关闭后再原子改名（避免半写文件被并发读）。
        for lead, entry in handles.items():
            final, temporary, _ = entry
            if temporary.is_file() and temporary.stat().st_size > 0:
                temporary.replace(final)
                written[lead] = final
            else:
                temporary.unlink(missing_ok=True)
    return written


@dataclass
class CompositeAerosolProvider(AerosolProvider):
    providers: List[AerosolProvider]

    def get_reading(self, request: ForecastRequest) -> Optional[AerosolReading]:
        for provider in self.providers:
            reading = provider.get_reading(request)
            if reading is not None:
                return reading
        return None


def retrieve_cams_file(client, query: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(
        target.suffix + f'.tmp-{os.getpid()}-{threading.get_ident()}'
    )
    temporary.unlink(missing_ok=True)
    try:
        client.retrieve(
            'cams-global-atmospheric-composition-forecasts',
            query,
            str(temporary),
        )
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise RuntimeError('CAMS ADS returned an empty GRIB file.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _grib_message_step(gid) -> int:
    """Return the forecast lead represented by one GRIB message.

    CDS can expose the lead as either the integer ``step`` key or a
    ``stepRange`` such as ``0-3``.  A range ending at 3 belongs to the 3-hour
    output, so use its end rather than silently grouping it with hour 0.
    """

    def _parse(value) -> int:
        if isinstance(value, bool):
            raise ValueError("boolean is not a GRIB forecast step")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid GRIB forecast step: {value!r}") from exc
        if not math.isfinite(numeric) or not numeric.is_integer() or numeric < 0:
            raise ValueError(f"invalid GRIB forecast step: {value!r}")
        return int(numeric)

    try:
        step_range = str(codes_get(gid, 'stepRange')).strip()
        if not step_range:
            raise ValueError("empty GRIB stepRange")
        if '-' in step_range:
            start_text, end_text = step_range.split('-', 1)
            start = _parse(start_text)
            end = _parse(end_text)
            if end < start:
                raise ValueError(f"invalid GRIB stepRange: {step_range!r}")
            return end
        return _parse(step_range)
    except Exception as range_error:
        try:
            return _parse(codes_get(gid, 'step'))
        except Exception:
            raise ValueError(
                'GRIB message has no valid forecast step or stepRange'
            ) from range_error


def split_grib_messages(
    source: Path,
    output_dir: Path,
    name_prefix: str,
    progress_callback=None,
) -> list[Path]:
    """Split a multi-time GRIB into one complete optical-profile file per step.

    Each output file is named ``{name_prefix}-{step:03d}.grib`` where *step* is
    the forecast step (in hours) read from the GRIB message. Every AOD and
    pressure-level extinction message for that step is retained. Returns the
    paths that were atomically written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_paths: dict[int, Path] = {}
    with source.open('rb') as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                step = _grib_message_step(gid)
            except Exception:
                codes_release(gid)
                raise
            temporary = temporary_paths.get(step)
            if temporary is None:
                temporary = (
                    output_dir
                    / f".{name_prefix}-{step:03d}.tmp-{os.getpid()}"
                    f"-{threading.get_ident()}.grib"
                )
                temporary.unlink(missing_ok=True)
                temporary_paths[step] = temporary
            try:
                with temporary.open('ab') as out_fh:
                    codes_write(gid, out_fh)
            finally:
                codes_release(gid)
    written: list[Path] = []
    for step, temporary in sorted(temporary_paths.items()):
        out_path = output_dir / f"{name_prefix}-{step:03d}.grib"
        temporary.replace(out_path)
        written.append(out_path)
        if progress_callback is not None:
            progress_callback(len(written))
    return written


def cams_area_key(area: List[float]) -> str:
    return hashlib.sha256(json.dumps(area).encode('ascii')).hexdigest()[:12]


def find_cached_cams_file(
    cache_dir: Path,
    area: List[float],
    target_utc: datetime,
) -> Optional[tuple[Path, date, str, int]]:
    suffix = f"-{cams_area_key(area)}.grib"
    exact_paths = list(cache_dir.rglob(f"{CAMS_OPTICS_CACHE_PREFIX}-*{suffix}"))
    exact_path_set = set(exact_paths)
    candidate_paths = exact_paths + [
        path
        for path in cache_dir.rglob(f"{CAMS_OPTICS_CACHE_PREFIX}-*.grib")
        if path not in exact_path_set and cams_file_covers_area(path, area)
    ]
    matches: List[tuple[float, int, datetime, Path, date, str, int]] = []
    for path in candidate_paths:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        parsed = parse_cams_cache_name(path)
        if parsed is None:
            continue
        run_date, run_time, lead_hours, run_utc = parsed
        valid_dt = run_utc + timedelta(hours=lead_hours)
        delta = abs((valid_dt - target_utc).total_seconds())
        if delta > CAMS_MAX_CACHE_VALID_TIME_DELTA.total_seconds():
            continue
        exact_rank = 0 if path in exact_path_set else 1
        matches.append((delta, exact_rank, run_utc, path, run_date, run_time, lead_hours))
    if not matches:
        return None
    # A spatial subset does not define the forecast cycle. For the same valid
    # time, use the freshest run everywhere and only then prefer an exact area
    # file. Otherwise a point and a regional grid can select different CAMS
    # runs solely because their request-area hashes differ.
    matches.sort(key=lambda item: (item[0], -item[2].timestamp(), item[1]))
    _, _, _, path, run_date, run_time, lead_hours = matches[0]
    return path, run_date, run_time, lead_hours


def cams_profile_bracket_times(target_utc: datetime) -> tuple[datetime, datetime]:
    if target_utc.tzinfo is None:
        raise ValueError('CAMS profile target time must be timezone-aware')
    target = target_utc.astimezone(timezone.utc)
    left_hour = target.hour - target.hour % 3
    left = target.replace(hour=left_hour, minute=0, second=0, microsecond=0)
    if target == left:
        return left, left
    return left, left + timedelta(hours=3)


def find_cached_cams_bracket(
    cache_dir: Path,
    area: List[float],
    target_utc: datetime,
) -> Optional[CamsProfileBracket]:
    left_valid, right_valid = cams_profile_bracket_times(target_utc)
    target = target_utc.astimezone(timezone.utc)
    suffix = f"-{cams_area_key(area)}.grib"
    exact_paths = list(cache_dir.rglob(f"{CAMS_OPTICS_CACHE_PREFIX}-*{suffix}"))
    exact_path_set = set(exact_paths)
    candidate_paths = exact_paths + [
        path
        for path in cache_dir.rglob(f"{CAMS_OPTICS_CACHE_PREFIX}-*.grib")
        if path not in exact_path_set and cams_file_covers_area(path, area)
    ]

    selections: dict[datetime, CamsProfileFileSelection] = {}
    ranks: dict[datetime, tuple[float, int]] = {}
    required = {left_valid, right_valid}
    for path in candidate_paths:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        parsed = parse_cams_cache_name(path)
        if parsed is None:
            continue
        run_date, run_time, lead_hours, run_time_utc = parsed
        valid_time = run_time_utc + timedelta(hours=lead_hours)
        if valid_time not in required:
            continue
        try:
            if not _cams_optics_file_valid(path):
                continue
        except Exception:
            continue
        rank = (-run_time_utc.timestamp(), 0 if path in exact_path_set else 1)
        if valid_time in ranks and ranks[valid_time] <= rank:
            continue
        ranks[valid_time] = rank
        selections[valid_time] = CamsProfileFileSelection(
            path=path,
            run_date=run_date,
            run_time=run_time,
            lead_hours=lead_hours,
            run_time_utc=run_time_utc,
            valid_time=valid_time,
        )

    left = selections.get(left_valid)
    right = selections.get(right_valid)
    if left is None or right is None:
        return None
    if left_valid == right_valid:
        right_weight = 0.0
    else:
        right_weight = (target - left_valid).total_seconds() / (
            right_valid - left_valid
        ).total_seconds()
    return CamsProfileBracket(
        target_time=target,
        left=left,
        right=right,
        right_weight=right_weight,
    )


def parse_cams_cache_name(
    path: Path,
) -> Optional[tuple[date, str, int, datetime]]:
    match = _CAMS_CACHE_NAME.match(path.name)
    if match is None:
        return None
    try:
        year, month, day, compact_time, lead, _area_key = match.groups()
        run_date = date(int(year), int(month), int(day))
        run_time = f"{int(compact_time[:2]):02d}:{int(compact_time[2:]):02d}"
        lead_hours = int(lead)
        run_utc = datetime(
            run_date.year,
            run_date.month,
            run_date.day,
            int(run_time[:2]),
            int(run_time[3:]),
            tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        return None
    return run_date, run_time, lead_hours, run_utc


def cams_file_covers_area(
    path: Path,
    requested_area: List[float],
    tolerance_deg: float = 0.0,
) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        north, west, south, east = cams_file_area(path)
    except Exception:
        return False
    requested_north, requested_west, requested_south, requested_east = requested_area
    # ADS rounds requested boundaries inward to the nearest native cell centre.
    # The request builder pads by one full native cell, so accepting half a
    # cell here still leaves the model bbox covered while rejecting a real
    # missing edge.  The extra epsilon handles decimal representation noise.
    edge_tolerance = max(
        float(tolerance_deg), CAMS_NATIVE_GRID_SPACING_DEG / 2.0 + 1e-9
    )
    return (
        north + edge_tolerance >= requested_north
        and west - edge_tolerance <= requested_west
        and south - edge_tolerance <= requested_south
        and east + edge_tolerance >= requested_east
    )


def _cams_area_sidecar_load(directory: Path) -> dict[str, dict[str, Any]]:
    sidecar = directory / _CAMS_AREA_SIDECAR_NAME
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _cams_area_sidecar_flush() -> None:
    for directory in list(_CAMS_AREA_SIDECAR_DIRTY):
        entries = _CAMS_AREA_SIDECARS.get(directory)
        if entries is None:
            continue
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / _CAMS_AREA_SIDECAR_NAME
            temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(entries, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError:
            continue
        _CAMS_AREA_SIDECAR_DIRTY.discard(directory)


def _cams_area_sidecar_lookup(
    path: Path, stat: os.stat_result
) -> tuple[float, float, float, float] | None:
    directory = path.parent
    with _CAMS_AREA_SIDECAR_LOCK:
        entries = _CAMS_AREA_SIDECARS.get(directory)
        if entries is None:
            entries = _cams_area_sidecar_load(directory)
            _CAMS_AREA_SIDECARS[directory] = entries
        entry = entries.get(path.name)
    if not isinstance(entry, dict):
        return None
    try:
        if int(entry["m"]) != stat.st_mtime_ns or int(entry["s"]) != stat.st_size:
            return None
        area = entry["a"]
        if not isinstance(area, list) or len(area) != 4:
            return None
        return (float(area[0]), float(area[1]), float(area[2]), float(area[3]))
    except (KeyError, TypeError, ValueError):
        return None


def _cams_area_sidecar_store(
    path: Path, stat: os.stat_result, area: tuple[float, float, float, float]
) -> None:
    directory = path.parent
    with _CAMS_AREA_SIDECAR_LOCK:
        entries = _CAMS_AREA_SIDECARS.setdefault(directory, {})
        entries[path.name] = {
            "m": int(stat.st_mtime_ns),
            "s": int(stat.st_size),
            "a": [float(value) for value in area],
        }
        _CAMS_AREA_SIDECAR_DIRTY.add(directory)
        # Keep one atomic sidecar write per process exit rather than rewriting
        # an O(n)-sized index for every file in a large cache directory.


atexit.register(_cams_area_sidecar_flush)


def cams_file_area(path: Path) -> tuple[float, float, float, float]:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _CAMS_AREA_CACHE_LOCK:
        cached = _CAMS_AREA_CACHE.get(key)
        if cached is not None:
            _CAMS_AREA_CACHE.move_to_end(key)
            return cached

    sidecar_area = _cams_area_sidecar_lookup(path, stat)
    if sidecar_area is not None:
        with _CAMS_AREA_CACHE_LOCK:
            _CAMS_AREA_CACHE[key] = sidecar_area
        return sidecar_area

    with path.open('rb') as handle:
        gid = codes_grib_new_from_file(handle)
        if gid is None:
            raise RuntimeError(f'No GRIB messages found in {path}')
        try:
            latitudes = np.asarray(codes_get_array(gid, 'latitudes'), dtype=np.float64)
            longitudes = np.asarray(codes_get_array(gid, 'longitudes'), dtype=np.float64)
        finally:
            codes_release(gid)
    normalized_longitudes = (longitudes + 180.0) % 360.0 - 180.0
    result = (
        float(np.max(latitudes)),
        float(np.min(normalized_longitudes)),
        float(np.min(latitudes)),
        float(np.max(normalized_longitudes)),
    )
    _cams_area_sidecar_store(path, stat, result)

    with _CAMS_AREA_CACHE_LOCK:
        stale_keys = [item for item in _CAMS_AREA_CACHE if item[0] == key[0] and item != key]
        for stale_key in stale_keys:
            _CAMS_AREA_CACHE.pop(stale_key, None)
        _CAMS_AREA_CACHE[key] = result
        _CAMS_AREA_CACHE.move_to_end(key)
        while len(_CAMS_AREA_CACHE) > _CAMS_AREA_CACHE_LIMIT:
            _CAMS_AREA_CACHE.popitem(last=False)
    return result


def cams_reading_from_file(
    path: Path,
    request: ForecastRequest,
    path_azimuth_deg: float,
    run_date: date,
    run_time: str,
    lead_hours: int,
    include_path_samples: bool = True,
) -> AerosolReading:
    samples = read_aod_corridor(
        path,
        request.point,
        path_azimuth_deg,
        max_distance_km=max(0.0, float(request.radius_km)),
        half_width_km=CAMS_PATH_HALF_WIDTH_KM,
    )
    if not samples:
        raise RuntimeError('CAMS corridor did not contain usable AOD samples.')
    value = min(samples, key=lambda sample: sample.distance_km).aod550
    valid_time = datetime(
        run_date.year,
        run_date.month,
        run_date.day,
        int(run_time.split(':', 1)[0]),
        tzinfo=timezone.utc,
    ) + timedelta(hours=lead_hours)
    source = (
        f"cams-ads corridor {run_date.isoformat()} {run_time} +{lead_hours:03d}h "
        f"-> {valid_time.strftime('%Y-%m-%d %H:%MZ')} ({len(samples)} samples)"
    )
    return AerosolReading(
        aod550=value,
        source=source,
        quality='cams',
        observer_lat=request.point.lat,
        observer_lon=request.point.lon,
        path_azimuth_deg=path_azimuth_deg,
        path_samples=samples if include_path_samples else [],
    )



def aerosol_target_time_utc(request: ForecastRequest) -> datetime:
    event_local = solar_event_time_local(request.target_date, request.point, request.timezone_name, request.phenomenon)
    return rounded_hour(event_local.astimezone(timezone.utc))


def rounded_hour(value: datetime) -> datetime:
    if value.minute >= 30:
        value = value + timedelta(hours=1)
    return value.replace(minute=0, second=0, microsecond=0)



def expand_date_specs(specs: List[str], start_date: date, end_date: date) -> List[date]:
    results: List[date] = []
    for spec in specs:
        if '/' in spec:
            start_text, end_text = spec.split('/', 1)
            current = max(date.fromisoformat(start_text), start_date)
            stop = min(date.fromisoformat(end_text), end_date)
            while current <= stop:
                results.append(current)
                current += timedelta(days=1)
        else:
            current = date.fromisoformat(spec)
            if start_date <= current <= end_date:
                results.append(current)
    results.sort()
    return results



def choose_best_cams_selection(process, base_query: dict, constrained: dict, target_utc: datetime) -> Optional[tuple[date, str, int]]:
    availability_query = dict(base_query)
    requested_variables = list(base_query.get('variable', []))
    if 'aerosol_extinction_coefficient_532nm' in requested_variables:
        # Surface AOD is hourly, while the pressure-level extinction profile is
        # published every three hours. CDS accepts a mixed request at an hourly
        # lead but silently returns only AOD, so the profile must define which
        # run/lead combinations are eligible for a complete optical cache.
        availability_query['variable'] = [
            'aerosol_extinction_coefficient_532nm'
        ]
    available_dates = expand_date_specs(
        constrained.get('date', []),
        target_utc.date() - timedelta(days=5),
        target_utc.date(),
    )
    if not available_dates:
        return None
    best: Optional[tuple[float, float, date, str, int]] = None
    for run_date in available_dates:
        day_constraints = process.apply_constraints({
            **availability_query,
            'date': [run_date.isoformat()],
        })
        available_times = day_constraints.get('time', ['00:00', '12:00'])
        available_leads = sorted(int(value) for value in day_constraints.get('leadtime_hour', ['0']))
        for run_time in available_times:
            run_hour = int(run_time.split(':', 1)[0])
            run_dt = datetime(run_date.year, run_date.month, run_date.day, run_hour, tzinfo=timezone.utc)
            if run_dt > target_utc:
                continue
            age_seconds = (target_utc - run_dt).total_seconds()
            for lead in available_leads:
                valid_dt = run_dt + timedelta(hours=lead)
                delta = abs((valid_dt - target_utc).total_seconds())
                candidate = (delta, age_seconds, run_date, run_time, lead)
                if best is None or candidate < best:
                    best = candidate
    if best is None:
        return None
    _, _, run_date, run_time, lead = best
    return run_date, run_time, lead



def nearest_value(path: Path, lat: float, lon: float) -> float:
    with path.open('rb') as handle:
        gid = codes_grib_new_from_file(handle)
        if gid is None:
            raise RuntimeError(f'No GRIB messages found in {path}')
        lats = codes_get_array(gid, 'latitudes')
        lons = codes_get_array(gid, 'longitudes')
        values = codes_get_array(gid, 'values')
        best_index = min(
            range(len(values)),
            key=lambda index: abs(float(lats[index]) - lat) + abs(normalize_lon(float(lons[index])) - lon),
        )
        result = float(values[best_index])
        codes_release(gid)
        return result


def cams_corridor_area(
    observer: GeoPoint,
    azimuth_deg: float,
    max_distance_km: float = CAMS_PATH_DISTANCE_KM,
    half_width_km: float = CAMS_PATH_HALF_WIDTH_KM,
) -> List[float]:
    points = [observer]
    for distance_km in (250.0, 500.0, 1000.0, 1500.0, 2000.0, max_distance_km):
        distance = min(distance_km, max_distance_km)
        center = destination_point(observer, azimuth_deg, distance)
        points.append(center)
        points.append(destination_point(center, azimuth_deg - 90.0, half_width_km))
        points.append(destination_point(center, azimuth_deg + 90.0, half_width_km))
    fan_distance = min(550.0, max_distance_km)
    points.append(destination_point(observer, azimuth_deg - 72.0, fan_distance))
    points.append(destination_point(observer, azimuth_deg + 72.0, fan_distance))

    longitudes = [unwrap_longitude(point.lon, observer.lon) for point in points]
    west = min(longitudes) - 0.5
    east = max(longitudes) + 0.5
    if west < -180.0 or east > 180.0:
        raise RuntimeError('CAMS path corridor crossing the date line is not yet supported.')
    north = min(90.0, max(point.lat for point in points) + 0.5)
    south = max(-90.0, min(point.lat for point in points) - 0.5)
    return [round(north, 3), round(west, 3), round(south, 3), round(east, 3)]


def unwrap_longitude(lon: float, reference_lon: float) -> float:
    value = lon
    while value - reference_lon > 180.0:
        value -= 360.0
    while value - reference_lon < -180.0:
        value += 360.0
    return value


def read_aod_corridor(
    path: Path,
    observer: GeoPoint,
    azimuth_deg: float,
    max_distance_km: float = CAMS_PATH_DISTANCE_KM,
    half_width_km: float = CAMS_PATH_HALF_WIDTH_KM,
) -> List[AerosolSample]:
    lats, lons, values = load_cams_grid(path)
    return cams_corridor_samples(
        lats,
        lons,
        values,
        observer,
        azimuth_deg,
        max_distance_km=max_distance_km,
        half_width_km=half_width_km,
    )


def load_cams_grid(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    grid = load_cams_optics_grid(path)
    return grid.lats, grid.lons, grid.aod550


def _cams_message_valid_time(gid) -> datetime:
    validity_date = str(codes_get(gid, 'validityDate'))
    validity_time = int(codes_get(gid, 'validityTime'))
    try:
        return datetime.strptime(
            f'{validity_date}{validity_time:04d}', '%Y%m%d%H%M'
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RuntimeError(
            f'Invalid CAMS message valid time: {validity_date} {validity_time}'
        ) from exc


def load_cams_optics_grid(path: Path) -> CamsOpticsGrid:
    """Load one complete, single-valid-time CAMS optical profile."""
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _CAMS_GRID_CACHE_LOCK:
        cached = _CAMS_GRID_CACHE.get(key)
        if cached is not None:
            _CAMS_GRID_CACHE.move_to_end(key)
            return cached

    latitudes: np.ndarray | None = None
    longitudes: np.ndarray | None = None
    aod550: np.ndarray | None = None
    extinction: dict[int, np.ndarray] = {}
    valid_time: datetime | None = None
    with path.open('rb') as handle:
        while True:
            gid = codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, 'shortName'))
                level = int(codes_get(gid, 'level'))
                level_type = str(codes_get(gid, 'typeOfLevel'))
                message_valid_time = _cams_message_valid_time(gid)
                if valid_time is None:
                    valid_time = message_valid_time
                elif valid_time != message_valid_time:
                    raise RuntimeError(
                        f'CAMS optical messages use different valid times: {path}'
                    )
                if short_name == 'aod550':
                    if level_type != 'surface' or level != 0:
                        raise RuntimeError(
                            f'CAMS aod550 must use surface level 0: {path}'
                        )
                    if aod550 is not None:
                        raise RuntimeError(f'Duplicate CAMS aod550 message: {path}')
                elif short_name == 'aerext532':
                    if level_type != 'isobaricInhPa':
                        raise RuntimeError(
                            f'CAMS aerext532 must use isobaricInhPa levels: {path}'
                        )
                    if level not in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS:
                        raise RuntimeError(
                            f'Unexpected CAMS aerext532 pressure level {level}: {path}'
                        )
                    if str(codes_get(gid, 'units')) != 'm**-1':
                        raise RuntimeError(
                            f'CAMS aerext532 unit must be m**-1: {path}'
                        )
                    if level in extinction:
                        raise RuntimeError(
                            f'Duplicate CAMS aerext532 level {level}: {path}'
                        )
                else:
                    raise RuntimeError(
                        f'Unexpected CAMS optical shortName {short_name!r}: {path}'
                    )
                message_lats = np.ascontiguousarray(
                    codes_get_array(gid, 'latitudes'), dtype=np.float64
                )
                message_lons = np.ascontiguousarray(
                    codes_get_array(gid, 'longitudes'), dtype=np.float64
                )
                values = np.ascontiguousarray(
                    codes_get_array(gid, 'values'), dtype=np.float64
                )
                number_of_points = int(codes_get(gid, 'numberOfPoints'))
                if not (
                    message_lats.ndim == message_lons.ndim == values.ndim == 1
                    and len(message_lats)
                    == len(message_lons)
                    == len(values)
                    == number_of_points
                ):
                    raise RuntimeError(
                        f'CAMS optical message has inconsistent grid lengths: {path}'
                    )
                if not (
                    np.all(np.isfinite(message_lats))
                    and np.all(np.isfinite(message_lons))
                    and np.all(np.isfinite(values))
                ):
                    raise RuntimeError(
                        f'CAMS optical message contains non-finite values: {path}'
                    )
                if latitudes is None:
                    latitudes = message_lats
                    longitudes = message_lons
                elif not (
                    np.array_equal(latitudes, message_lats)
                    and np.array_equal(longitudes, message_lons)
                ):
                    raise RuntimeError(f'CAMS optical messages use different grids: {path}')
                if short_name == 'aod550':
                    if np.any(
                        (values < 0.0) | (values > CAMS_AOD550_VALIDATION_MAX)
                    ):
                        raise RuntimeError(
                            f'CAMS aod550 is outside 0..{CAMS_AOD550_VALIDATION_MAX:g}: {path}'
                        )
                    aod550 = values
                else:
                    if np.any(values < 0.0):
                        raise RuntimeError(
                            f'CAMS aerext532 contains negative extinction: {path}'
                        )
                    extinction[level] = values
            finally:
                codes_release(gid)
    missing_levels = [
        level
        for level in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
        if level not in extinction
    ]
    if (
        latitudes is None
        or longitudes is None
        or aod550 is None
        or valid_time is None
        or missing_levels
    ):
        raise RuntimeError(
            f'CAMS optical cache is incomplete: {path}; missing levels={missing_levels}'
        )
    profile = np.ascontiguousarray(
        np.stack(
            [extinction[level] for level in CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS],
            axis=0,
        ),
        dtype=np.float64,
    )
    zero_profile = np.sum(profile, axis=0) <= 0.0
    if np.any((aod550 > 0.0) & zero_profile):
        raise RuntimeError(
            f'CAMS optical cache has positive aod550 with zero aerext532 profile: {path}'
        )
    grid = CamsOpticsGrid(
        path=path,
        valid_time=valid_time,
        lats=latitudes,
        lons=longitudes,
        aod550=aod550,
        aerext532_m_inv=profile,
    )

    with _CAMS_GRID_CACHE_LOCK:
        stale_keys = [item for item in _CAMS_GRID_CACHE if item[0] == key[0] and item != key]
        for stale_key in stale_keys:
            _CAMS_GRID_CACHE.pop(stale_key, None)
        _CAMS_GRID_CACHE[key] = grid
        _CAMS_GRID_CACHE.move_to_end(key)
        while len(_CAMS_GRID_CACHE) > _CAMS_GRID_CACHE_LIMIT:
            _CAMS_GRID_CACHE.popitem(last=False)
    return grid


def effective_aerosol_scale_height_km(extinction_m_inv: np.ndarray) -> np.ndarray:
    """Fit the tutorial's exponential aerosol profile to CAMS extinction.

    The fit uses the lower troposphere, where aerosol controls twilight light
    paths. Relative extinction weights preserve elevated/deep aerosol layers
    without letting the weak upper-atmosphere background flatten the slope.
    """
    values = np.asarray(extinction_m_inv, dtype=np.float64)
    if values.ndim < 2 or values.shape[0] != len(CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS):
        raise ValueError('CAMS extinction profile has an unexpected shape')
    pressure = np.asarray(CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS, dtype=np.float64)
    height_km = 44.330 * (1.0 - (pressure / 1013.25) ** 0.1903)
    lower_troposphere = height_km <= 4.5
    physical = np.isfinite(values) & (values > 1e-9) & (values < 0.02)
    valid_values = np.where(physical, values, 0.0)
    profile_max = np.max(valid_values, axis=0)
    relative_floor = 0.02 * profile_max
    valid = (
        physical
        & lower_troposphere.reshape((-1,) + (1,) * (values.ndim - 1))
        & (valid_values >= relative_floor)
    )
    relative = np.divide(
        valid_values,
        profile_max,
        out=np.zeros_like(valid_values),
        where=profile_max > 0.0,
    )
    weights = np.where(valid, np.sqrt(relative), 0.0)
    log_alpha = np.zeros_like(valid_values)
    np.log(valid_values, out=log_alpha, where=valid)
    heights = height_km.reshape((-1,) + (1,) * (values.ndim - 1))
    weight_sum = np.sum(weights, axis=0)
    mean_height = np.divide(
        np.sum(weights * heights, axis=0),
        weight_sum,
        out=np.zeros_like(weight_sum),
        where=weight_sum > 0.0,
    )
    mean_log_alpha = np.divide(
        np.sum(weights * log_alpha, axis=0),
        weight_sum,
        out=np.zeros_like(weight_sum),
        where=weight_sum > 0.0,
    )
    centered_height = heights - mean_height
    numerator = np.sum(
        weights * centered_height * (log_alpha - mean_log_alpha), axis=0
    )
    denominator = np.sum(weights * centered_height * centered_height, axis=0)
    slope = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 1e-8,
    )
    use_fit = (np.sum(valid, axis=0) >= 3) & (slope < -0.05)
    fitted = np.divide(
        -1.0,
        slope,
        out=np.full_like(slope, CAMS_DEFAULT_AEROSOL_SCALE_HEIGHT_KM),
        where=use_fit,
    )
    return np.ascontiguousarray(np.clip(fitted, 0.35, 4.0), dtype=np.float64)


def cams_corridor_samples(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    values: np.ndarray,
    observer: GeoPoint,
    azimuth_deg: float,
    max_distance_km: float = CAMS_PATH_DISTANCE_KM,
    half_width_km: float = CAMS_PATH_HALF_WIDTH_KM,
) -> List[AerosolSample]:
    lats = np.asarray(latitudes, dtype=np.float64)
    lons = (np.asarray(longitudes, dtype=np.float64) + 180.0) % 360.0 - 180.0
    aod = np.asarray(values, dtype=np.float64)
    valid = (
        np.isfinite(aod)
        & (aod >= 0.0)
        & (aod <= CAMS_AOD550_VALIDATION_MAX)
    )

    lat1 = math.radians(observer.lat)
    lat2 = np.radians(lats)
    delta_lat = lat2 - lat1
    delta_lon = np.radians((lons - observer.lon + 180.0) % 360.0 - 180.0)
    haversine = np.sin(delta_lat / 2.0) ** 2 + math.cos(lat1) * np.cos(lat2) * np.sin(delta_lon / 2.0) ** 2
    distance_km = 2.0 * 6371.0088 * np.arctan2(np.sqrt(haversine), np.sqrt(np.maximum(0.0, 1.0 - haversine)))
    bearing_deg = (
        np.degrees(
            np.arctan2(
                np.sin(delta_lon) * np.cos(lat2),
                math.cos(lat1) * np.sin(lat2) - math.sin(lat1) * np.cos(lat2) * np.cos(delta_lon),
            )
        )
        + 360.0
    ) % 360.0
    bearing_deg = np.where(distance_km > 1.0, bearing_deg, azimuth_deg)
    delta_deg = np.abs((bearing_deg - azimuth_deg + 180.0) % 360.0 - 180.0)
    delta_rad = np.radians(delta_deg)
    along_path_km = distance_km * np.cos(delta_rad)
    cross_path_km = np.abs(distance_km * np.sin(delta_rad))
    in_near_fan = (distance_km <= max_distance_km) & (delta_deg <= 75.0)
    in_far_corridor = (
        (along_path_km >= 0.0)
        & (along_path_km <= max_distance_km)
        & (cross_path_km <= half_width_km)
    )
    selected = np.flatnonzero(
        valid
        & (distance_km <= max_distance_km + half_width_km)
        & (in_near_fan | in_far_corridor)
    )
    return [
        AerosolSample(
            lat=float(lats[index]),
            lon=float(lons[index]),
            distance_km=float(distance_km[index]),
            bearing_deg=float(bearing_deg[index]),
            aod550=float(aod[index]),
        )
        for index in selected
    ]



def resolve_aerosol_provider(
    aod_file: Optional[str],
    aerosol_source: str = 'auto',
    fixed_area: Optional[List[float]] = None,
    include_path_samples: bool = True,
    cams_cache_dir: Path = Path('.cache/cams'),
) -> AerosolProvider:
    providers: List[AerosolProvider] = [OverrideAerosolProvider()]
    if aod_file:
        providers.append(JsonAerosolProvider(Path(aod_file)))
    if aerosol_source == 'cams':
        providers.append(CamsAdsAerosolProvider(
            cache_dir=cams_cache_dir,
            strict=False,
            fixed_area=fixed_area,
            include_path_samples=include_path_samples,
        ))
    elif aerosol_source == 'auto':
        providers.append(CamsAdsAerosolProvider(
            cache_dir=cams_cache_dir,
            strict=False,
            fixed_area=fixed_area,
            include_path_samples=include_path_samples,
        ))
    providers.append(DefaultAerosolProvider())
    return CompositeAerosolProvider(providers)


def fetch_particulate_reading(
    request: ForecastRequest,
    target_time_utc: datetime,
    timeout_seconds: int = 15,
) -> tuple[Optional[float], Optional[float], str]:
    target = target_time_utc.astimezone(timezone.utc)
    params = {
        'latitude': f'{request.point.lat:.5f}',
        'longitude': f'{request.point.lon:.5f}',
        'hourly': 'pm2_5,pm10',
        'timezone': 'UTC',
        'start_date': target.date().isoformat(),
        'end_date': target.date().isoformat(),
    }
    url = 'https://air-quality-api.open-meteo.com/v1/air-quality?' + urllib.parse.urlencode(params)
    http_request = urllib.request.Request(url, headers={'User-Agent': 'firesky/0.1 burning-sunset-engine'})
    with urllib.request.urlopen(http_request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode('utf-8'))
    hourly = payload.get('hourly') if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        raise RuntimeError('空气质量接口缺少逐小时数据。')
    times = hourly.get('time') or []
    pm25_values = hourly.get('pm2_5') or []
    pm10_values = hourly.get('pm10') or []
    candidates: list[tuple[float, int]] = []
    for index, value in enumerate(times):
        try:
            valid_time = datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        candidates.append((abs((valid_time - target).total_seconds()), index))
    if not candidates:
        raise RuntimeError('空气质量接口没有目标时刻数据。')
    _, index = min(candidates)
    pm25 = float(pm25_values[index]) if index < len(pm25_values) and pm25_values[index] is not None else None
    pm10 = float(pm10_values[index]) if index < len(pm10_values) and pm10_values[index] is not None else None
    return pm25, pm10, 'open-meteo-cams-air-quality'
