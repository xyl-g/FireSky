from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, List, Optional

from firesky.geometry import angular_difference_deg, haversine_km, initial_bearing_deg
from firesky.gfs import GfsNomadsClient
from firesky.models import DomainSnapshot, ForecastRequest, GeoPoint
from firesky.scoring import estimated_cloud_base_m, layer_label, layer_name
from firesky.service import choose_model_cycle, normalize_forecast_hours


AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"
USER_AGENT = "firesky/0.1 burning-sunset-engine"
METAR_RETRIES = 2
CLOUD_COVER_PRIORITY = {
    "FEW": 0.25,
    "SCT": 0.45,
    "BKN": 0.8,
    "OVC": 1.0,
    "VV": 1.0,
}
HANGZHOU_DEFAULT_METAR_STATION = "ZSHC"


@dataclass(frozen=True)
class CloudBaseLayer:
    layer: str
    cover: str
    base_m: float
    top_m: Optional[float]
    confidence: float
    source: str
    distance_km: Optional[float] = None
    bearing_deg: Optional[float] = None
    pressure_hpa: Optional[int] = None
    rh_percent: Optional[float] = None


@dataclass(frozen=True)
class CloudBaseReport:
    location_name: str
    point: GeoPoint
    timezone_name: str
    target_time_utc: datetime
    target_time_local: datetime
    source: str
    station_id: Optional[str]
    station_name: Optional[str]
    station_distance_km: Optional[float]
    raw_metar: Optional[str]
    layers: List[CloudBaseLayer]
    nearest_cloud_base_m: Optional[float]
    dominant_cloud_base_m: Optional[float]
    confidence: float
    firesky_blocking_summary: str
    observation_time_utc: Optional[datetime] = None
    station_bearing_deg: Optional[float] = None
    station_elevation_m: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location_name,
            "lat": self.point.lat,
            "lon": self.point.lon,
            "timezone": self.timezone_name,
            "time_utc": self.target_time_utc.isoformat(),
            "time_local": self.target_time_local.isoformat(),
            "source": self.source,
            "station_id": self.station_id,
            "station_name": self.station_name,
            "station_distance_km": round(self.station_distance_km, 1) if self.station_distance_km is not None else None,
            "station_bearing_deg": round(self.station_bearing_deg, 1) if self.station_bearing_deg is not None else None,
            "station_elevation_m": round(self.station_elevation_m) if self.station_elevation_m is not None else None,
            "raw_metar": self.raw_metar,
            "observation_time_utc": self.observation_time_utc.isoformat() if self.observation_time_utc else None,
            "nearest_cloud_base_m": round(self.nearest_cloud_base_m) if self.nearest_cloud_base_m is not None else None,
            "dominant_cloud_base_m": round(self.dominant_cloud_base_m) if self.dominant_cloud_base_m is not None else None,
            "confidence": round(self.confidence, 2),
            "firesky_blocking_summary": self.firesky_blocking_summary,
            "layers": [
                {
                    "layer": item.layer,
                    "cover": item.cover,
                    "base_m": round(item.base_m),
                    "top_m": round(item.top_m) if item.top_m is not None else None,
                    "confidence": round(item.confidence, 2),
                    "source": item.source,
                    "distance_km": round(item.distance_km, 1) if item.distance_km is not None else None,
                    "bearing_deg": round(item.bearing_deg, 1) if item.bearing_deg is not None else None,
                    "pressure_hpa": item.pressure_hpa,
                    "rh_percent": round(item.rh_percent, 1) if item.rh_percent is not None else None,
                }
                for item in self.layers
            ],
        }


def resolve_cloudbase(
    request: ForecastRequest,
    target_time_utc: datetime,
    source: str = "auto",
    station_id: Optional[str] = None,
    model_source: str = "gfs",
    metar_hours: int = 3,
) -> CloudBaseReport:
    target_time_utc = ensure_utc(target_time_utc)
    if source in {"auto", "metar"}:
        try:
            report = metar_cloudbase_report(
                request,
                target_time_utc,
                station_id or default_metar_station(request),
                hours=metar_hours,
            )
            if report.layers or source == "metar":
                return report
        except Exception:
            if source == "metar":
                raise

    if source in {"auto", "model", "gfs", "ec"}:
        selected_model = model_source if source in {"auto", "model"} else source
        return model_cloudbase_report(request, target_time_utc, selected_model)

    raise RuntimeError(f"不支持的云底来源：{source}")


def default_metar_station(request: ForecastRequest) -> str:
    if "杭州" in request.name or (29.5 <= request.point.lat <= 31.0 and 119.5 <= request.point.lon <= 121.0):
        return HANGZHOU_DEFAULT_METAR_STATION
    return HANGZHOU_DEFAULT_METAR_STATION


def metar_cloudbase_report(
    request: ForecastRequest,
    target_time_utc: datetime,
    station_id: str,
    hours: int = 3,
) -> CloudBaseReport:
    observations = fetch_metar(station_id, hours=hours)
    if not observations:
        raise RuntimeError(f"没有获取到 {station_id} 的 METAR。")
    best = nearest_metar(observations, target_time_utc)
    station_point = GeoPoint(float(best.get("lat", request.point.lat)), float(best.get("lon", request.point.lon)), float(best.get("elev", 0.0)))
    layers = parse_metar_cloud_layers(best, station_point.elevation_m)
    station_distance = haversine_km(request.point, station_point)
    return build_report(
        request=request,
        target_time_utc=target_time_utc,
        source="metar",
        station_id=str(best.get("icaoId") or station_id),
        station_name=str(best.get("name") or ""),
        station_distance_km=station_distance,
        station_bearing_deg=(
            initial_bearing_deg(request.point, station_point) if station_distance > 1.0 else None
        ),
        station_elevation_m=station_point.elevation_m,
        raw_metar=str(best.get("rawOb") or ""),
        layers=layers,
        observation_time_utc=parse_metar_time(best),
    )


def lightpath_metar_cloudbase_report(
    request: ForecastRequest,
    target_time_utc: datetime,
    path_azimuth_deg: float,
    hours: int = 3,
    radius_km: float = 250.0,
) -> CloudBaseReport:
    observations = fetch_nearby_metars(request.point, radius_km=radius_km, hours=hours)
    if not observations:
        raise RuntimeError("观测点周边没有可用机场 METAR。")
    target = ensure_utc(target_time_utc)
    nearest_by_station: dict[str, dict[str, Any]] = {}
    for item in observations:
        station_id = str(item.get("icaoId") or "").upper()
        if not station_id:
            continue
        current = nearest_by_station.get(station_id)
        if current is None or abs((parse_metar_time(item) - target).total_seconds()) < abs(
            (parse_metar_time(current) - target).total_seconds()
        ):
            nearest_by_station[station_id] = item

    candidates: list[tuple[float, float, dict[str, Any]]] = []
    for item in nearest_by_station.values():
        observed_at = parse_metar_time(item)
        age_hours = abs((observed_at - target).total_seconds()) / 3600.0
        if age_hours > hours:
            continue
        station = GeoPoint(
            float(item.get("lat", request.point.lat)),
            float(item.get("lon", request.point.lon)),
            float(item.get("elev", 0.0)),
        )
        distance = haversine_km(request.point, station)
        if distance > radius_km:
            continue
        bearing = initial_bearing_deg(request.point, station) if distance > 1.0 else path_azimuth_deg
        path_delta = angular_difference_deg(bearing, path_azimuth_deg)
        local_relevance = max(0.0, 1.0 - distance / 140.0)
        corridor_relevance = max(0.0, 1.0 - distance / radius_km) * max(0.0, 1.0 - path_delta / 55.0)
        time_quality = max(0.0, 1.0 - age_hours / max(1.0, hours))
        relevance = max(local_relevance, 0.90 * corridor_relevance) * (0.70 + 0.30 * time_quality)
        candidates.append((relevance, -distance, item))
    if not candidates:
        raise RuntimeError("周边机场 METAR 距离过远或时效不足。")
    best = max(candidates, key=lambda row: (row[0], row[1]))[2]
    station_point = GeoPoint(
        float(best.get("lat", request.point.lat)),
        float(best.get("lon", request.point.lon)),
        float(best.get("elev", 0.0)),
    )
    return build_report(
        request=request,
        target_time_utc=target,
        source="metar-lightpath",
        station_id=str(best.get("icaoId") or ""),
        station_name=str(best.get("name") or ""),
        station_distance_km=haversine_km(request.point, station_point),
        station_bearing_deg=(
            initial_bearing_deg(request.point, station_point)
            if haversine_km(request.point, station_point) > 1.0
            else path_azimuth_deg
        ),
        station_elevation_m=station_point.elevation_m,
        raw_metar=str(best.get("rawOb") or ""),
        layers=parse_metar_cloud_layers(best, station_point.elevation_m),
        observation_time_utc=parse_metar_time(best),
    )


def fetch_metar(station_id: str, hours: int = 3, timeout_seconds: int = 20) -> list[dict[str, Any]]:
    params = {
        "ids": station_id.upper(),
        "format": "json",
        "hours": str(hours),
    }
    url = f"{AWC_METAR_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: BaseException | None = None
    for attempt in range(1, METAR_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise RuntimeError("AWC METAR 返回格式不是列表。")
            return [item for item in payload if isinstance(item, dict)]
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < METAR_RETRIES:
                time.sleep(0.8 * attempt)
    reason = getattr(last_error, "reason", last_error)
    raise RuntimeError(f"AWC METAR 请求失败：{reason}") from last_error


def fetch_nearby_metars(
    point: GeoPoint,
    radius_km: float = 250.0,
    hours: int = 3,
    timeout_seconds: int = 20,
) -> list[dict[str, Any]]:
    latitude_span = radius_km / 111.0
    longitude_span = radius_km / max(20.0, 111.0 * math.cos(math.radians(point.lat)))
    params = {
        "format": "json",
        "hours": str(hours),
        "bbox": ",".join(
            f"{value:.3f}"
            for value in (
                max(-90.0, point.lat - latitude_span),
                max(-180.0, point.lon - longitude_span),
                min(90.0, point.lat + latitude_span),
                min(180.0, point.lon + longitude_span),
            )
        ),
    }
    url = f"{AWC_METAR_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("AWC METAR 返回格式不是列表。")
    return [item for item in payload if isinstance(item, dict)]


def nearest_metar(observations: list[dict[str, Any]], target_time_utc: datetime) -> dict[str, Any]:
    target = ensure_utc(target_time_utc)

    def gap(item: dict[str, Any]) -> float:
        return abs((parse_metar_time(item) - target).total_seconds())

    return min(observations, key=gap)


def parse_metar_time(item: dict[str, Any]) -> datetime:
    value = item.get("reportTime") or item.get("receiptTime")
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    obs_time = item.get("obsTime")
    if obs_time is not None:
        return datetime.fromtimestamp(float(obs_time), tz=timezone.utc)
    raise RuntimeError("METAR 记录没有可用时间。")


def parse_metar_cloud_layers(item: dict[str, Any], station_elevation_m: float = 0.0) -> List[CloudBaseLayer]:
    layers: List[CloudBaseLayer] = []
    clouds = item.get("clouds") or []
    if isinstance(clouds, list):
        for cloud in clouds:
            if not isinstance(cloud, dict):
                continue
            cover = str(cloud.get("cover") or "").upper()
            base_ft = cloud.get("base")
            if base_ft is None:
                continue
            layers.append(metar_layer_from_base_ft(cover, float(base_ft), station_elevation_m))
    if not layers:
        layers.extend(parse_raw_metar_cloud_layers(str(item.get("rawOb") or ""), station_elevation_m))
    return sorted(layers, key=lambda layer: layer.base_m)


def parse_raw_metar_cloud_layers(raw: str, station_elevation_m: float = 0.0) -> List[CloudBaseLayer]:
    layers: List[CloudBaseLayer] = []
    for match in re.finditer(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3})\b", raw.upper()):
        cover, height = match.groups()
        layers.append(metar_layer_from_base_ft(cover, int(height) * 100.0, station_elevation_m))
    return sorted(layers, key=lambda layer: layer.base_m)


def metar_layer_from_base_ft(cover: str, base_ft: float, station_elevation_m: float = 0.0) -> CloudBaseLayer:
    base_m = station_elevation_m + base_ft * 0.3048
    cover_weight = CLOUD_COVER_PRIORITY.get(cover, 0.5)
    return CloudBaseLayer(
        layer=height_layer_name(base_m),
        cover=cover,
        base_m=base_m,
        top_m=None,
        confidence=0.72 + 0.22 * cover_weight,
        source="metar",
    )


def model_cloudbase_report(request: ForecastRequest, target_time_utc: datetime, model_source: str = "gfs") -> CloudBaseReport:
    client = GfsNomadsClient() if model_source == "gfs" else None
    if client is None:
        from firesky.ecmwf import EcmwfOpenDataClient

        client = EcmwfOpenDataClient()
    cycle_utc = choose_model_cycle(model_source, target_time_utc, request.gfs_cycle_override)
    forecast_hour = normalize_forecast_hours(model_source, [round((target_time_utc - cycle_utc).total_seconds() / 3600.0)])[0]
    snapshot = client.fetch_snapshot(request, cycle_utc, forecast_hour)
    layers = estimate_model_cloud_layers(snapshot)
    return build_report(
        request=request,
        target_time_utc=target_time_utc,
        source=model_source,
        station_id=None,
        station_name=None,
        station_distance_km=None,
        raw_metar=None,
        layers=layers,
    )


def estimate_model_cloud_layers(snapshot: DomainSnapshot, radius_km: float = 120.0) -> List[CloudBaseLayer]:
    candidates: List[CloudBaseLayer] = []
    for feature in snapshot.features:
        if feature.distance_km > radius_km:
            continue
        confidence = model_layer_confidence(feature.rh_percent, feature.distance_km)
        if confidence <= 0.0:
            continue
        base_m = estimated_cloud_base_m(feature)
        candidates.append(
            CloudBaseLayer(
                layer=layer_label(layer_name(feature.pressure_hpa)),
                cover=model_cover_from_rh(feature.rh_percent),
                base_m=base_m,
                top_m=feature.geopotential_height_m,
                confidence=confidence,
                source=snapshot.model_name,
                distance_km=feature.distance_km,
                bearing_deg=feature.bearing_deg,
                pressure_hpa=feature.pressure_hpa,
                rh_percent=feature.rh_percent,
            )
        )
    candidates.sort(key=lambda item: (-item.confidence, item.base_m, item.distance_km or 0.0))
    return candidates[:8]


def model_layer_confidence(rh_percent: float, distance_km: float) -> float:
    if rh_percent < 82.0:
        return 0.0
    moisture = min(1.0, (rh_percent - 82.0) / 16.0)
    distance = max(0.25, 1.0 - distance_km / 180.0)
    return 0.35 + 0.45 * moisture + 0.20 * distance


def model_cover_from_rh(rh_percent: float) -> str:
    if rh_percent >= 96.0:
        return "OVC"
    if rh_percent >= 90.0:
        return "BKN"
    return "SCT"


def build_report(
    request: ForecastRequest,
    target_time_utc: datetime,
    source: str,
    station_id: Optional[str],
    station_name: Optional[str],
    station_distance_km: Optional[float],
    raw_metar: Optional[str],
    layers: List[CloudBaseLayer],
    observation_time_utc: Optional[datetime] = None,
    station_bearing_deg: Optional[float] = None,
    station_elevation_m: Optional[float] = None,
) -> CloudBaseReport:
    target_time_local = target_time_utc.astimezone(timezone_from_name(request.timezone_name))
    nearest = min((layer.base_m for layer in layers), default=None)
    dominant_layer = max(layers, key=lambda item: item.confidence * CLOUD_COVER_PRIORITY.get(item.cover, 0.7), default=None)
    dominant = dominant_layer.base_m if dominant_layer is not None else None
    confidence = mean([layer.confidence for layer in layers[:3]]) if layers else 0.0
    return CloudBaseReport(
        location_name=request.name,
        point=request.point,
        timezone_name=request.timezone_name,
        target_time_utc=target_time_utc,
        target_time_local=target_time_local,
        source=source,
        station_id=station_id,
        station_name=station_name,
        station_distance_km=station_distance_km,
        station_bearing_deg=station_bearing_deg,
        station_elevation_m=station_elevation_m,
        raw_metar=raw_metar,
        layers=layers,
        nearest_cloud_base_m=nearest,
        dominant_cloud_base_m=dominant,
        confidence=confidence,
        firesky_blocking_summary=firesky_blocking_summary(layers),
        observation_time_utc=observation_time_utc,
    )


def cloudbase_blocking_risk(report: CloudBaseReport) -> float:
    if not report.layers:
        return 0.0 if report.raw_metar and "CAVOK" in report.raw_metar.upper() else 0.15
    lowest = min(report.layers, key=lambda item: item.base_m)
    cover = CLOUD_COVER_PRIORITY.get(lowest.cover, 0.45)
    height = max(0.0, min(1.0, (2600.0 - lowest.base_m) / 2200.0))
    distance_quality = max(0.25, 1.0 - (report.station_distance_km or 0.0) / 300.0)
    return max(0.0, min(1.0, cover * (0.25 + 0.75 * height) * distance_quality))


def firesky_blocking_summary(layers: List[CloudBaseLayer]) -> str:
    if not layers:
        return "没有识别到明确云底，无法判断晚霞低空遮挡。"
    lowest = min(layers, key=lambda item: item.base_m)
    if lowest.base_m < 1200.0 and lowest.cover in {"BKN", "OVC", "VV"}:
        return "低云云底较低且覆盖较多，晚霞低空光路遮挡风险偏高。"
    if lowest.base_m < 1800.0:
        return "存在较低云底，晚霞可能受局地低云影响。"
    if lowest.base_m < 4500.0:
        return "云底处于中低层，若西侧有云墙会影响晚霞通视。"
    return "云底较高，对近地面晚霞光路遮挡较小。"


def height_layer_name(base_m: float) -> str:
    if base_m < 2000.0:
        return "低云"
    if base_m < 6000.0:
        return "中云"
    return "高云"


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def timezone_from_name(name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def resolve_target_time(value: Optional[datetime], reference: Optional[datetime] = None) -> datetime:
    if value is not None:
        return ensure_utc(value)
    current = reference or datetime.now(timezone.utc)
    return ensure_utc(current).replace(second=0, microsecond=0)
