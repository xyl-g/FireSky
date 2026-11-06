from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=Warning, message="urllib3 v2 only supports OpenSSL.*")

import urllib3
import hashlib
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM
from firesky.bounded_lock import bounded_file_lock
from firesky.geometry import bounding_box, choose_cycle, haversine_km, initial_bearing_deg, normalize_lon
from firesky.feature_cache import load_or_build_feature_cache
from firesky.gfs import (
    _DecodedGfsGrid,
    _decoded_values_with_bitmap,
    load_mmap_decoded_grid,
    write_mmap_decoded_grid,
)
from firesky.models import DomainSnapshot, ForecastRequest, GeoPoint, GridCellFeature
from firesky.storage import cycle_directory, derived_cache_dir, first_existing


ECMWF_CACHE_LOCK_TIMEOUT_SECONDS = 300.0

# The strict pure-IFS adapter is kept in an independent module so this legacy
# research client and the GFS production path cannot silently share fields.
from firesky.ecmwf_ifs import (
    ECMWF_IFS_ADAPTER_VERSION,
    ECMWF_IFS_BASIC_CONTRACT,
    ECMWF_IFS_BASIC_CACHE_SCHEMA,
    ECMWF_IFS_CACHE_SCHEMA_VERSION,
    ECMWF_IFS_CLOUD_PARAMS,
    ECMWF_IFS_DOWNLOAD_PARAMS,
    ECMWF_IFS_FIELD_UNITS,
    ECMWF_IFS_FORECAST_CADENCE_HOURS,
    ECMWF_IFS_FULL_CONTRACT,
    ECMWF_IFS_FULL_CACHE_SCHEMA,
    ECMWF_IFS_GRAVITY_M_S2,
    ECMWF_IFS_PUBLIC_LAYER_CLOUD_REASON,
    ECMWF_IFS_MODEL_LEVEL_PARAMS,
    ECMWF_IFS_MODEL_LEVELS,
    ECMWF_IFS_MAX_RETRIES,
    ECMWF_IFS_OPEN_DATA_SOURCES,
    ECMWF_IFS_PUBLIC_CLOUD_PARAMS,
    ECMWF_IFS_PUBLIC_PRESSURE_PARAMS,
    ECMWF_IFS_PUBLIC_UNSUPPORTED_REASON,
    ECMWF_IFS_PRESSURE_LEVELS,
    ECMWF_IFS_PRESSURE_PARAMS,
    ECMWF_IFS_RETRY_AFTER_SECONDS,
    ECMWF_IFS_REQUIRED_FIELDS,
    ECMWF_IFS_REQUIRED_FIELD_KEYS,
    ECMWF_IFS_SURFACE_PARAMS,
    EcmwfIfsAdapter,
    EcmwfIfsCapability,
    EcmwfIfsCapabilityError,
    EcmwfIfsInputTensor,
    _configure_bounded_ecmwf_client,
    decode_ecmwf_ifs_basic_tensor,
    decode_ecmwf_ifs_full_catalog_tensor,
    decode_ecmwf_ifs_tensor,
    full_catalog_cache_path,
    probe_ecmwf_ifs_basic_capability,
    probe_ecmwf_ifs_capability,
    probe_ecmwf_ifs_full_catalog_capability,
    require_ecmwf_ifs_capability,
    require_ecmwf_ifs_full_catalog_capability,
)

PRESSURE_LEVELS = (200, 250, 300, 400, 500, 600, 700, 850, 925)
PARAMS = ("r", "gh", "t", "w", "u", "v")
LIGHTPATH_FETCH_RADIUS_KM = DEFAULT_ANALYSIS_RADIUS_KM
CACHE_SCHEMA_VERSION = 3
OPEN_DATA_SOURCE_URLS = {
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "google": "https://storage.googleapis.com/ecmwf-open-data",
    "ecmwf": "https://data.ecmwf.int/forecasts",
}


class EcmwfArtifactProbeError(RuntimeError):
    """The ECMWF artifact probe could not establish availability."""

urllib3.disable_warnings()


def choose_ecmwf_cycle(reference_utc: datetime, availability_lag_hours: int = 7) -> datetime:
    return choose_cycle(reference_utc, (0, 12), availability_lag_hours)


@dataclass
class _MutableCell:
    lat: float
    lon: float
    level: int
    values: Dict[str, float]


class EcmwfOpenDataClient:
    model_name = "ec"

    def __init__(self, cache_dir: Path | str = ".cache/ecmwf", source: str = "ecmwf") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if source not in OPEN_DATA_SOURCE_URLS:
            raise ValueError(f"不支持的 ECMWF Open Data 数据源：{source}")
        self.source = source

    def fetch_snapshot(self, request: ForecastRequest, init_time_utc: datetime, forecast_hour: int) -> DomainSnapshot:
        cache_path = self.download(init_time_utc, forecast_hour)
        valid_time = init_time_utc + timedelta(hours=forecast_hour)
        features = self.parse_snapshot(cache_path, request.point, max(request.radius_km, LIGHTPATH_FETCH_RADIUS_KM))
        return DomainSnapshot(
            model_name=self.model_name,
            forecast_hour=forecast_hour,
            init_time_utc=init_time_utc,
            valid_time_utc=valid_time,
            features=features,
            source_url=f"ecmwf-open-data://ifs/{init_time_utc:%Y%m%d}/{init_time_utc:%H}/f{forecast_hour:03d}",
            cache_path=str(cache_path),
        )

    def download(self, init_time_utc: datetime, forecast_hour: int) -> Path:
        name = f"ifs-v{CACHE_SCHEMA_VERSION}-{init_time_utc:%Y%m%d%H}-f{forecast_hour:03d}.grib2"
        target_dir = cycle_directory(self.cache_dir, init_time_utc)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        cached = first_existing((target, self.cache_dir / name))
        if cached is not None:
            return cached

        lock_path = target.with_suffix(target.suffix + ".lock")
        with lock_path.open("a+b") as lock_handle:
            with bounded_file_lock(
                lock_handle,
                timeout_seconds=ECMWF_CACHE_LOCK_TIMEOUT_SECONDS,
                description="ECMWF 缓存锁",
            ):
                if target.exists() and target.stat().st_size > 0:
                    return target
                return self._download_unlocked(init_time_utc, forecast_hour, target)

    def load_decoded_subset(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: tuple[float, float, float, float],
    ) -> tuple[_DecodedGfsGrid, Path]:
        """Decode one ECMWF pressure-level file into a reusable regional grid."""
        path = self.download(init_time_utc, forecast_hour)
        normalized_bbox = tuple(round(float(value), 4) for value in bbox)
        signature = hashlib.sha256(
            f"ecmwf-regional-v1:{normalized_bbox}".encode("ascii")
        ).hexdigest()[:16]
        cache_path = derived_cache_dir(path.resolve(), "decoded") / f"region-{signature}"
        manifest_path = cache_path / "manifest.json"
        lock_path = path.with_suffix(path.suffix + f".{signature}.decode.lock")
        with lock_path.open("a+b") as lock_handle:
            with bounded_file_lock(
                lock_handle,
                timeout_seconds=ECMWF_CACHE_LOCK_TIMEOUT_SECONDS,
                description="ECMWF 解码缓存锁",
            ):
                if manifest_path.is_file():
                    try:
                        return load_mmap_decoded_grid(cache_path), path
                    except (OSError, ValueError, KeyError):
                        pass
                grid = self._decode_regional_grid(path, normalized_bbox)
                write_mmap_decoded_grid(cache_path, grid)
                return load_mmap_decoded_grid(cache_path), path

    @staticmethod
    def _decode_regional_grid(
        path: Path,
        bbox: tuple[float, float, float, float],
    ) -> _DecodedGfsGrid:
        west, east, north, south = bbox
        coordinates: tuple[np.ndarray, np.ndarray] | None = None
        fields: Dict[Tuple[str, int, str], np.ndarray] = {}
        with path.open("rb") as handle:
            while True:
                gid = codes_grib_new_from_file(handle)
                if gid is None:
                    break
                try:
                    short_name = str(codes_get(gid, "shortName"))
                    level = int(codes_get(gid, "level"))
                    latitudes = np.asarray(codes_get_array(gid, "latitudes"), dtype=np.float32)
                    longitudes = np.asarray(codes_get_array(gid, "longitudes"), dtype=np.float32)
                    longitudes = (longitudes + 180.0) % 360.0 - 180.0
                    mask = (latitudes >= south) & (latitudes <= north)
                    if west <= east:
                        mask &= (longitudes >= west) & (longitudes <= east)
                    else:
                        mask &= (longitudes >= west) | (longitudes <= east)
                    selected = np.flatnonzero(mask)
                    if selected.size == 0:
                        continue
                    selected_lats = latitudes[selected]
                    selected_lons = longitudes[selected]
                    order = np.lexsort((selected_lons, selected_lats))
                    selected_lats = selected_lats[order]
                    selected_lons = selected_lons[order]
                    if coordinates is None:
                        coordinates = (selected_lats, selected_lons)
                    elif not (
                        np.array_equal(coordinates[0], selected_lats)
                        and np.array_equal(coordinates[1], selected_lons)
                    ):
                        raise RuntimeError("ECMWF pressure-level messages do not share one grid")
                    values = np.asarray(codes_get_array(gid, "values"), dtype=np.float32)[selected][order]
                    values[np.abs(values) > 1e20] = np.nan
                    fields[("isobaricInhPa", level, short_name)] = values
                finally:
                    codes_release(gid)
        if coordinates is None or not fields:
            raise RuntimeError(f"ECMWF file has no grid cells inside bbox {bbox}")
        return _DecodedGfsGrid(
            latitudes=np.asarray(coordinates[0], dtype=np.float32),
            longitudes=np.asarray(coordinates[1], dtype=np.float32),
            fields=fields,
        )

    def _download_unlocked(self, init_time_utc: datetime, forecast_hour: int, target: Path) -> Path:

        try:
            from ecmwf.opendata import Client
        except ImportError as exc:
            raise RuntimeError("未安装 ecmwf-opendata，无法使用 EC 模式。") from exc

        configure_bounded_ecmwf_retries()
        sources = [self.source] + [name for name in OPEN_DATA_SOURCE_URLS if name != self.source]
        errors: List[str] = []
        for source in sources:
            temporary: Path | None = None
            try:
                client = Client(
                    source=source,
                    model="ifs",
                    resol="0p25",
                    verify=False,
                )
                _configure_bounded_ecmwf_client(client)
                temporary = target.with_suffix(target.suffix + f".tmp-{os.getpid()}")
                client.retrieve(
                    date=init_time_utc.strftime("%Y%m%d"),
                    time=init_time_utc.hour,
                    type="fc",
                    stream="oper",
                    step=forecast_hour,
                    param=list(PARAMS),
                    levelist=list(PRESSURE_LEVELS),
                    levtype="pl",
                    target=str(temporary),
                )
                if temporary.exists() and temporary.stat().st_size > 0:
                    temporary.replace(target)
                    self.source = source
                    return target
                raise RuntimeError("没有生成有效的 GRIB 文件")
            except Exception as exc:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                errors.append(f"{source}: {exc}")
        raise RuntimeError("ECMWF Open Data 镜像均下载失败：" + "；".join(errors))

    def parse_snapshot(self, path: Path, observer: GeoPoint, radius_km: float) -> List[GridCellFeature]:
        return load_or_build_feature_cache(
            path,
            observer,
            radius_km,
            lambda: self._parse_snapshot_uncached(path, observer, radius_km),
        )

    def _parse_snapshot_uncached(self, path: Path, observer: GeoPoint, radius_km: float) -> List[GridCellFeature]:
        cells: Dict[Tuple[float, float, int], _MutableCell] = {}
        leftlon, rightlon, toplat, bottomlat = bounding_box(observer, radius_km)
        with path.open("rb") as handle:
            while True:
                gid = codes_grib_new_from_file(handle)
                if gid is None:
                    break
                short_name = str(codes_get(gid, "shortName"))
                level = int(codes_get(gid, "level"))
                lats = codes_get_array(gid, "latitudes")
                lons = codes_get_array(gid, "longitudes")
                # Bitmap-masked decode: eccodes returns the 9999 missingValue
                # sentinel for bitmap gaps, which must not enter the feature
                # store as real data.
                values = _decoded_values_with_bitmap(gid)
                for lat, lon, value in zip(lats, lons, values):
                    lat_key = round(float(lat), 4)
                    lon_key = round(normalize_lon(float(lon)), 4)
                    if lat_key > toplat or lat_key < bottomlat:
                        continue
                    if leftlon <= rightlon:
                        if lon_key < leftlon or lon_key > rightlon:
                            continue
                    elif lon_key > rightlon and lon_key < leftlon:
                        continue
                    if not np.isfinite(value):
                        # Bitmap gap: record the field as absent instead of
                        # storing a sentinel that would masquerade as data.
                        continue
                    key = (lat_key, lon_key, level)
                    cell = cells.setdefault(key, _MutableCell(lat=lat_key, lon=lon_key, level=level, values={}))
                    cell.values[short_name] = float(value)
                codes_release(gid)

        features: List[GridCellFeature] = []
        for cell in cells.values():
            point = GeoPoint(lat=cell.lat, lon=cell.lon)
            if cell.level in PRESSURE_LEVELS:
                # Same fail-closed contract as the GFS decoded-grid path: a
                # pressure level without geopotential or humidity must not
                # silently become 0 m / 0 % (absolute dry) pseudo data.
                missing = [
                    name
                    for name, value in (
                        ("geopotential", cell.values.get("gh")),
                        ("relative humidity", cell.values.get("r")),
                    )
                    if value is None or not np.isfinite(float(value))
                ]
                if missing:
                    raise ValueError(
                        "EC vertical prior field missing or non-finite: "
                        + ", ".join(missing)
                        + f" at {cell.level} hPa"
                    )
            features.append(
                GridCellFeature(
                    lat=cell.lat,
                    lon=cell.lon,
                    distance_km=haversine_km(observer, point),
                    bearing_deg=initial_bearing_deg(observer, point),
                    pressure_hpa=cell.level,
                    geopotential_height_m=cell.values.get("gh", 0.0),
                    rh_percent=cell.values.get("r", 0.0),
                    temperature_k=cell.values.get("t"),
                    omega_pa_s=cell.values.get("w"),
                    u_ms=cell.values.get("u"),
                    v_ms=cell.values.get("v"),
                )
            )
        return features

    def find_available_source(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        timeout_seconds: int = 8,
    ) -> str | None:
        ordered_sources = [self.source] + [
            source for source in OPEN_DATA_SOURCE_URLS if source != self.source
        ]
        for source in ordered_sources:
            if ecmwf_index_available(
                source,
                init_time_utc,
                forecast_hour,
                timeout_seconds=timeout_seconds,
            ):
                return source
        return None


def configure_bounded_ecmwf_retries() -> None:
    import ecmwf.opendata.client as client_module
    from multiurl import download as multiurl_download
    from multiurl import robust as multiurl_robust

    def bounded_download(url, target, **kwargs):
        return multiurl_download(
            url,
            target=target,
            maximum_retries=3,
            retry_after=(1, 5, 2),
            **kwargs,
        )

    def bounded_robust(call, maximum_tries=3, retry_after=(1, 5, 2), mirrors=None):
        return multiurl_robust(
            call,
            maximum_tries=maximum_tries,
            retry_after=retry_after,
            mirrors=mirrors,
        )

    client_module.download = bounded_download
    client_module.robust = bounded_robust


def ecmwf_index_url(source: str, init_time_utc: datetime, forecast_hour: int) -> str:
    base_url = OPEN_DATA_SOURCE_URLS[source]
    cycle = init_time_utc.astimezone(timezone.utc)
    filename = f"{cycle:%Y%m%d%H}0000-{forecast_hour}h-oper-fc.index"
    return f"{base_url}/{cycle:%Y%m%d}/{cycle:%H}z/ifs/0p25/oper/{filename}"


def _head_status(url: str, *, timeout: float) -> int:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "firesky/0.1 burning-sunset-engine"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EcmwfArtifactProbeError(
            f"ECMWF artifact probe network error for {url}: {exc}"
        ) from exc


def ecmwf_artifact_available(
    source: str,
    init_time_utc: datetime,
    forecast_hour: int,
    timeout_seconds: float = 8.0,
) -> bool:
    """Check that one step's byte-range index is published.

    A 404 means the step is not published yet and is safe for cycle fallback.
    Other HTTP/network failures are raised so an outage is never reported as an
    older-but-valid cycle.
    """
    if source not in OPEN_DATA_SOURCE_URLS:
        raise ValueError(f"不支持的 ECMWF Open Data 数据源：{source}")
    index_status = _head_status(
        ecmwf_index_url(source, init_time_utc, forecast_hour),
        timeout=timeout_seconds,
    )
    if 200 <= index_status < 300:
        return True
    if index_status != 404:
        raise EcmwfArtifactProbeError(
            f"ECMWF index probe returned HTTP {index_status}"
        )
    return False


def ecmwf_index_available(
    source: str,
    init_time_utc: datetime,
    forecast_hour: int,
    timeout_seconds: int = 8,
) -> bool:
    request = urllib.request.Request(
        ecmwf_index_url(source, init_time_utc, forecast_hour),
        method="HEAD",
        headers={"User-Agent": "firesky/0.1 burning-sunset-engine"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False
