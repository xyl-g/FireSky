from __future__ import annotations

import json
import gzip
import math
import os
import requests
import struct
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from firesky.bounded_lock import bounded_file_lock


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
POINT_METADATA_URL = "https://api.open-meteo.com/v1/forecast"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SRTM_BASE_URL = "https://elevation-tiles-prod.s3.amazonaws.com/skadi"
USER_AGENT = "firesky/0.1 burning-sunset-engine"
LOCATION_CACHE_PATH = Path(".cache/locations.json")
SRTM_CACHE_DIR = Path(".cache/srtm")
LOCATION_CACHE_VERSION = 2
HTTP_RETRIES = 3
SRTM_CACHE_LOCK_TIMEOUT_SECONDS = 30.0

_timezone_finder = None
_json_sessions: dict[str, requests.Session] = {}
_json_session_locks: dict[str, threading.Lock] = {}
_json_sessions_lock = threading.Lock()


@dataclass(frozen=True)
class LocationResult:
    name: str
    lat: float
    lon: float
    timezone_name: str
    elevation_m: float
    source: str
    display_name: str


@dataclass(frozen=True)
class LocationCandidate:
    name: str
    lat: float
    lon: float
    source: str
    display_name: str
    timezone_name: Optional[str] = None
    elevation_m: Optional[float] = None


def resolve_location(query: str, timeout_seconds: int = 20) -> LocationResult:
    clean = query.strip()
    if not clean:
        raise RuntimeError("地名不能为空。")

    cached = read_location_cache(clean)
    if cached is not None:
        return cached

    errors: list[str] = []
    try:
        result = resolve_open_meteo_location(clean, timeout_seconds)
        if result is not None:
            write_location_cache(clean, result)
            return result
    except Exception as exc:
        errors.append(f"Open-Meteo: {exc}")

    try:
        result = resolve_nominatim_location(clean, timeout_seconds)
        if result is not None:
            write_location_cache(clean, result)
            return result
    except Exception as exc:
        errors.append(f"Nominatim: {exc}")

    suffix = f"；{'；'.join(errors)}" if errors else ""
    raise RuntimeError(f"无法解析地名：{query}{suffix}")


def search_location_fast(query: str, timeout_seconds: int = 6) -> LocationCandidate:
    """Return coordinates quickly for the web UI; point metadata loads separately."""
    clean = query.strip()
    if not clean:
        raise RuntimeError("地名不能为空。")
    return _search_location_fast_cached(clean, timeout_seconds)


@lru_cache(maxsize=512)
def _search_location_fast_cached(query: str, timeout_seconds: int) -> LocationCandidate:
    cached = read_location_cache(query)
    if cached is not None:
        return LocationCandidate(
            name=cached.name,
            lat=cached.lat,
            lon=cached.lon,
            source=cached.source,
            display_name=cached.display_name,
            timezone_name=cached.timezone_name,
            elevation_m=cached.elevation_m,
        )

    errors: list[str] = []
    try:
        payload = get_json(
            NOMINATIM_URL,
            {
                "q": query,
                "format": "jsonv2",
                "limit": "1",
                "addressdetails": "1",
                "accept-language": "zh-CN",
            },
            timeout_seconds,
            retries=1,
        )
        if isinstance(payload, list) and payload:
            item = payload[0]
            address = item.get("address") or {}
            name = str(item.get("name") or address.get("city") or address.get("region") or query)
            return LocationCandidate(
                name=name,
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                source="nominatim-openstreetmap",
                display_name=str(item.get("display_name") or name),
            )
    except Exception as exc:
        errors.append(f"Nominatim: {exc}")

    try:
        result = resolve_open_meteo_location(query, timeout_seconds)
        if result is not None:
            return LocationCandidate(
                name=result.name,
                lat=result.lat,
                lon=result.lon,
                source=result.source,
                display_name=result.display_name,
                timezone_name=result.timezone_name,
                elevation_m=result.elevation_m,
            )
    except Exception as exc:
        errors.append(f"Open-Meteo: {exc}")

    suffix = f"；{'；'.join(errors)}" if errors else ""
    raise RuntimeError(f"无法解析地名：{query}{suffix}")


def resolve_open_meteo_location(query: str, timeout_seconds: int) -> Optional[LocationResult]:
    payload = get_json(
        GEOCODING_URL,
        {
            "name": query,
            "count": "1",
            "language": "zh",
            "format": "json",
        },
        timeout_seconds,
    )
    results = payload.get("results") or []
    if not results:
        return None
    item = results[0]
    lat = float(item["latitude"])
    lon = float(item["longitude"])
    timezone_name = str(item.get("timezone") or "") or fetch_timezone(lat, lon, timeout_seconds)
    elevation = item.get("elevation")
    elevation_m = float(elevation) if elevation is not None else fetch_elevation(lat, lon, timeout_seconds)
    display_parts = [
        str(item.get("name") or query),
        str(item.get("admin2") or ""),
        str(item.get("admin1") or ""),
        str(item.get("country") or ""),
    ]
    display_name = ", ".join(part for part in display_parts if part)
    return LocationResult(
        name=str(item.get("name") or query),
        lat=lat,
        lon=lon,
        timezone_name=timezone_name,
        elevation_m=elevation_m,
        source="open-meteo-geocoding",
        display_name=display_name,
    )


def resolve_nominatim_location(query: str, timeout_seconds: int) -> Optional[LocationResult]:
    payload = get_json(
        NOMINATIM_URL,
        {
            "q": query,
            "format": "jsonv2",
            "limit": "1",
            "addressdetails": "1",
            "accept-language": "zh-CN",
        },
        timeout_seconds,
    )
    if not isinstance(payload, list) or not payload:
        return None
    item = payload[0]
    lat = float(item["lat"])
    lon = float(item["lon"])
    address = item.get("address") or {}
    name = str(item.get("name") or address.get("city") or address.get("region") or query)
    timezone_name, elevation_m = fetch_point_metadata(lat, lon, min(timeout_seconds, 6))
    return LocationResult(
        name=name,
        lat=lat,
        lon=lon,
        timezone_name=timezone_name,
        elevation_m=elevation_m,
        source="nominatim-openstreetmap",
        display_name=str(item.get("display_name") or name),
    )


def fetch_elevation(lat: float, lon: float, timeout_seconds: int = 20) -> float:
    tile = ensure_srtm_tile(lat, lon, timeout_seconds)
    return read_srtm_elevation(tile, lat, lon)


def fetch_point_metadata(lat: float, lon: float, timeout_seconds: int = 6) -> tuple[str, float]:
    """Resolve interactive-map metadata without downloading a full SRTM tile."""
    return _fetch_point_metadata_cached(round(lat, 4), round(lon, 4), timeout_seconds)


@lru_cache(maxsize=512)
def _fetch_point_metadata_cached(lat: float, lon: float, timeout_seconds: int) -> tuple[str, float]:
    try:
        payload = get_json(
            POINT_METADATA_URL,
            {
                "latitude": str(lat),
                "longitude": str(lon),
                "timezone": "auto",
                "forecast_days": "1",
            },
            timeout_seconds,
            retries=1,
        )
        timezone_name = str(payload.get("timezone") or "")
        elevation = payload.get("elevation")
        if not timezone_name or elevation is None:
            raise RuntimeError("Open-Meteo 未返回完整的坐标元数据。")
        return timezone_name, float(elevation)
    except Exception:
        # Retain the local, high-resolution path for offline use and provider outages.
        return fetch_timezone(lat, lon, timeout_seconds), fetch_elevation(lat, lon, timeout_seconds)


@lru_cache(maxsize=1_048_576)
def _fetch_timezone_cached(lat: float, lon: float) -> str:
    global _timezone_finder
    if _timezone_finder is None:
        from timezonefinder import TimezoneFinder

        _timezone_finder = TimezoneFinder(in_memory=True)
    value = _timezone_finder.timezone_at(lat=lat, lng=lon)
    if not value:
        raise RuntimeError("本地时区数据没有覆盖该坐标。")
    return str(value)


def fetch_timezone(lat: float, lon: float, timeout_seconds: int = 20) -> str:
    del timeout_seconds
    # Regional identity builders resolve the timezone for every grid point and
    # the same coordinates repeat across rebuilds, so the lookup is memoized
    # to keep those rebuilds off the hot path.
    return _fetch_timezone_cached(round(float(lat), 4), round(float(lon), 4))


def srtm_tile_name(lat: float, lon: float) -> str:
    lat_floor = math.floor(lat)
    lon_floor = math.floor(lon)
    lat_prefix = "N" if lat_floor >= 0 else "S"
    lon_prefix = "E" if lon_floor >= 0 else "W"
    return f"{lat_prefix}{abs(lat_floor):02d}{lon_prefix}{abs(lon_floor):03d}"


def ensure_srtm_tile(lat: float, lon: float, timeout_seconds: int = 20) -> Path:
    if lat < -56.0 or lat >= 60.0:
        raise RuntimeError("SRTM 30m 数据仅覆盖约南纬 56° 至北纬 60°。")
    name = srtm_tile_name(lat, lon)
    target = SRTM_CACHE_DIR / f"{name}.hgt"
    if target.is_file() and target.stat().st_size > 0:
        validate_srtm_tile(target)
        return target

    SRTM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(".hgt.lock")
    with lock_path.open("a+b") as lock_handle:
        with bounded_file_lock(
            lock_handle,
            timeout_seconds=SRTM_CACHE_LOCK_TIMEOUT_SECONDS,
            description="SRTM 缓存锁",
        ):
            if target.is_file() and target.stat().st_size > 0:
                validate_srtm_tile(target)
                return target
            band = name[:3]
            url = f"{SRTM_BASE_URL}/{band}/{name}.hgt.gz"
            compressed = target.with_suffix(f".hgt.gz.tmp-{os.getpid()}")
            temporary = target.with_suffix(f".hgt.tmp-{os.getpid()}")
            try:
                import requests

                with requests.Session() as session:
                    session.trust_env = False
                    with session.get(
                        url,
                        headers={"User-Agent": USER_AGENT},
                        timeout=(10, max(60, timeout_seconds)),
                        stream=True,
                    ) as response, compressed.open("wb") as output:
                        response.raise_for_status()
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                output.write(chunk)
                with gzip.open(compressed, "rb") as source, temporary.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                validate_srtm_tile(temporary)
                temporary.replace(target)
            except Exception as exc:
                raise RuntimeError(f"SRTM 瓦片 {name} 下载或解析失败：{exc}") from exc
            finally:
                compressed.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
    return target


def validate_srtm_tile(path: Path) -> int:
    sample_count = path.stat().st_size // 2
    side = math.isqrt(sample_count)
    if path.stat().st_size % 2 or side * side != sample_count or side not in {1201, 3601}:
        raise RuntimeError(f"SRTM 瓦片尺寸无效：{path}")
    return side


def read_srtm_elevation(path: Path, lat: float, lon: float) -> float:
    side = validate_srtm_tile(path)
    lat_floor = math.floor(lat)
    lon_floor = math.floor(lon)
    row = (lat_floor + 1.0 - lat) * (side - 1)
    col = (lon - lon_floor) * (side - 1)
    row0 = max(0, min(side - 1, math.floor(row)))
    col0 = max(0, min(side - 1, math.floor(col)))
    row1 = min(side - 1, row0 + 1)
    col1 = min(side - 1, col0 + 1)
    row_fraction = row - row0
    col_fraction = col - col0

    def sample(handle, row_index: int, col_index: int) -> int:
        handle.seek((row_index * side + col_index) * 2)
        return struct.unpack(">h", handle.read(2))[0]

    with path.open("rb") as handle:
        values = [
            sample(handle, row0, col0),
            sample(handle, row0, col1),
            sample(handle, row1, col0),
            sample(handle, row1, col1),
        ]
    valid_values = [value for value in values if value != -32768]
    if not valid_values:
        raise RuntimeError(f"SRTM 瓦片在 {lat:.6f},{lon:.6f} 附近没有有效高程。")
    if len(valid_values) < 4:
        return float(sum(valid_values) / len(valid_values))
    north = values[0] * (1.0 - col_fraction) + values[1] * col_fraction
    south = values[2] * (1.0 - col_fraction) + values[3] * col_fraction
    return float(north * (1.0 - row_fraction) + south * row_fraction)


def get_json(
    url: str,
    params: dict[str, str],
    timeout_seconds: int,
    retries: int = HTTP_RETRIES,
) -> Any:
    host = url.split("/", 3)[2]
    with _json_sessions_lock:
        session = _json_sessions.get(host)
        if session is None:
            session = requests.Session()
            _json_sessions[host] = session
            _json_session_locks[host] = threading.Lock()
        session_lock = _json_session_locks[host]
    last_error: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            with session_lock:
                response = session.get(
                    url,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.8 * attempt)
    raise RuntimeError(f"{url} 请求失败：{last_error}") from last_error


def read_location_cache(query: str) -> Optional[LocationResult]:
    try:
        if not LOCATION_CACHE_PATH.exists():
            return None
        payload = json.loads(LOCATION_CACHE_PATH.read_text(encoding="utf-8"))
        if payload.get("_schema_version") != LOCATION_CACHE_VERSION:
            return None
        item = payload.get(query)
        if not isinstance(item, dict):
            return None
        return LocationResult(
            name=str(item["name"]),
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            timezone_name=str(item["timezone_name"]),
            elevation_m=float(item["elevation_m"]),
            source=str(item.get("source") or "cache"),
            display_name=str(item.get("display_name") or item["name"]),
        )
    except Exception:
        return None


def write_location_cache(query: str, result: LocationResult) -> None:
    try:
        LOCATION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        if LOCATION_CACHE_PATH.exists():
            existing = json.loads(LOCATION_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("_schema_version") == LOCATION_CACHE_VERSION:
                payload = existing
        payload["_schema_version"] = LOCATION_CACHE_VERSION
        payload[query] = {
            "name": result.name,
            "lat": result.lat,
            "lon": result.lon,
            "timezone_name": result.timezone_name,
            "elevation_m": result.elevation_m,
            "source": result.source,
            "display_name": result.display_name,
        }
        LOCATION_CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return None
