from __future__ import annotations

import csv
import io
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PIL import Image

from firesky.models import ForecastRequest
from firesky.scoring import clamp


RADAR_METADATA_URL = "https://api.rainviewer.com/public/weather-maps.json"
RADAR_PALETTE_URL = "https://www.rainviewer.com/files/rainviewer_api_colors_table.csv"
DEFAULT_RADAR_CACHE_DIR = Path(".cache/rainviewer")
RADAR_ZOOM = 6
RADAR_SIZE = 256
METADATA_TTL_SECONDS = 120
PALETTE_TTL_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True)
class RadarObservation:
    source: str
    valid_time_utc: datetime
    age_minutes: float
    coverage_ratio: float
    local_precip_ratio: float
    path_precip_ratio: float
    convective_ratio: float
    max_dbz: Optional[float]
    convective_risk: float


class RainViewerRadarProvider:
    def __init__(self, cache_dir: str | Path = DEFAULT_RADAR_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)

    def get_observation(
        self,
        request: ForecastRequest,
        reference_utc: datetime,
        path_azimuth_deg: float,
    ) -> Optional[RadarObservation]:
        metadata = json.loads(
            cached_url_bytes(
                RADAR_METADATA_URL,
                self.cache_dir / "metadata.json",
                METADATA_TTL_SECONDS,
            )
        )
        frames = metadata.get("radar", {}).get("past", [])
        candidates: list[tuple[float, dict]] = []
        for frame in frames:
            try:
                frame_time = datetime.fromtimestamp(int(frame["time"]), tz=timezone.utc)
            except (KeyError, TypeError, ValueError, OSError):
                continue
            age_seconds = (reference_utc - frame_time).total_seconds()
            if -20 * 60 <= age_seconds <= 2.5 * 3600:
                candidates.append((abs(age_seconds), frame))
        if not candidates:
            return None
        _, frame = min(candidates, key=lambda item: item[0])
        valid_time = datetime.fromtimestamp(int(frame["time"]), tz=timezone.utc)
        host = str(metadata.get("host") or "").rstrip("/")
        path = str(frame.get("path") or "")
        if not host or not path:
            return None

        lat = request.point.lat
        lon = request.point.lon
        tile_url = (
            f"{host}{path}/{RADAR_SIZE}/{RADAR_ZOOM}/"
            f"{lat:.5f}/{lon:.5f}/2/0_0.png"
        )
        coverage_url = (
            f"{host}/v2/coverage/0/{RADAR_SIZE}/{RADAR_ZOOM}/"
            f"{lat:.5f}/{lon:.5f}/0/0_0.png"
        )
        stamp = valid_time.strftime("%Y%m%d%H%M")
        radar_image = Image.open(
            BytesIO(
                cached_url_bytes(
                    tile_url,
                    self.cache_dir / f"radar-{stamp}.png",
                    None,
                )
            )
        ).convert("RGBA")
        coverage_image = Image.open(
            BytesIO(
                cached_url_bytes(
                    coverage_url,
                    self.cache_dir / "coverage.png",
                    7 * 24 * 3600,
                )
            )
        ).convert("RGBA")
        palette = parse_universal_blue_palette(
            cached_url_bytes(
                RADAR_PALETTE_URL,
                self.cache_dir / "palette.csv",
                PALETTE_TTL_SECONDS,
            )
        )
        metrics = radar_image_metrics(
            radar_image,
            coverage_image,
            palette,
            lat,
            lon,
            path_azimuth_deg,
            RADAR_ZOOM,
        )
        if metrics["coverage_ratio"] < 0.25:
            return None
        return RadarObservation(
            source="rainviewer-composite",
            valid_time_utc=valid_time,
            age_minutes=max(0.0, (reference_utc - valid_time).total_seconds() / 60.0),
            coverage_ratio=metrics["coverage_ratio"],
            local_precip_ratio=metrics["local_precip_ratio"],
            path_precip_ratio=metrics["path_precip_ratio"],
            convective_ratio=metrics["convective_ratio"],
            max_dbz=metrics["max_dbz"],
            convective_risk=metrics["convective_risk"],
        )


def fetch_radar_observation(
    request: ForecastRequest,
    reference_utc: datetime,
    path_azimuth_deg: float,
) -> Optional[RadarObservation]:
    return RainViewerRadarProvider().get_observation(request, reference_utc, path_azimuth_deg)


def parse_universal_blue_palette(payload: bytes) -> dict[tuple[int, int, int, int], float]:
    rows = csv.reader(io.StringIO(payload.decode("utf-8-sig")))
    header = next(rows, [])
    try:
        color_index = header.index("Universal Blue")
    except ValueError as exc:
        raise ValueError("RainViewer palette does not contain Universal Blue") from exc
    palette: dict[tuple[int, int, int, int], float] = {}
    # The table appends a second snow palette after the first -32..95 dBZ block.
    for row_number, row in enumerate(rows):
        if row_number >= 128 or len(row) <= color_index:
            break
        try:
            dbz = float(row[0])
            rgba = hex_rgba(row[color_index])
        except (TypeError, ValueError):
            continue
        palette[rgba] = dbz
    return palette


def hex_rgba(value: str) -> tuple[int, int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 8:
        raise ValueError(f"invalid RGBA value: {value}")
    return tuple(int(raw[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]


def radar_image_metrics(
    radar_image: Image.Image,
    coverage_image: Image.Image,
    palette: dict[tuple[int, int, int, int], float],
    center_lat: float,
    center_lon: float,
    path_azimuth_deg: float,
    zoom: int,
) -> dict[str, float | None]:
    radar = np.asarray(radar_image.convert("RGBA"), dtype=np.uint8)
    coverage = np.asarray(coverage_image.convert("RGBA"), dtype=np.uint8)
    height, width = radar.shape[:2]
    coverage_valid = coverage[:, :, 3] == 0
    coverage_ratio = float(np.mean(coverage_valid))

    dbz = np.full((height, width), np.nan, dtype=np.float32)
    for rgba, value in palette.items():
        color = np.asarray(rgba, dtype=np.uint8)
        matches = np.all(radar == color, axis=2)
        dbz[matches] = value

    local_mask = circular_mask(width, height, radius_km=65.0, center_lat=center_lat, zoom=zoom)
    path_points = path_sample_pixels_with_weights(
        center_lat,
        center_lon,
        path_azimuth_deg,
        width,
        height,
        zoom,
    )
    path_values = sample_pixel_patches(
        dbz, [pixel for pixel, _ in path_points], radius=2
    )
    # Per-point horizon weights repeat once per 5x5 patch sample.
    path_weights = np.concatenate(
        [
            np.full(25, weight, dtype=np.float64)
            for _, weight in path_points
        ]
    )
    if path_weights.size != path_values.size:
        path_weights = np.ones(path_values.size, dtype=np.float64)
    local_values = dbz[local_mask & coverage_valid]
    valid_local = local_values[np.isfinite(local_values)]
    valid_all = dbz[np.isfinite(dbz) & coverage_valid]

    local_precip_ratio = echo_ratio(valid_local, threshold_dbz=10.0, denominator=int(local_mask.sum()))
    path_precip_ratio = weighted_echo_ratio(
        path_values, path_weights, threshold_dbz=10.0
    )
    convective_values = np.concatenate((valid_local, path_values))
    convective_weights = np.concatenate(
        (np.ones(valid_local.size, dtype=np.float64), path_weights)
    )
    convective_ratio = weighted_echo_ratio(
        convective_values, convective_weights, threshold_dbz=35.0
    )
    # The convective peak must come from the observer's neighbourhood and the
    # light path, not the whole ±270 km tile: one distant storm used to drag
    # down the color diagnosis of an otherwise clear sunset site.
    convective_finite = convective_values[np.isfinite(convective_values)]
    max_dbz = float(np.max(convective_finite)) if convective_finite.size else None
    peak_risk = clamp(((max_dbz or 0.0) - 32.0) / 23.0)
    convective_risk = clamp(
        0.54 * peak_risk
        + 0.30 * min(1.0, convective_ratio * 45.0)
        + 0.16 * min(1.0, path_precip_ratio * 12.0)
    )
    return {
        "coverage_ratio": coverage_ratio,
        "local_precip_ratio": local_precip_ratio,
        "path_precip_ratio": path_precip_ratio,
        "convective_ratio": convective_ratio,
        "max_dbz": max_dbz,
        "convective_risk": convective_risk,
    }


def circular_mask(
    width: int,
    height: int,
    radius_km: float,
    center_lat: float,
    zoom: int,
) -> np.ndarray:
    km_per_pixel = 156.54303392 * math.cos(math.radians(center_lat)) / (2**zoom)
    radius_px = radius_km / max(km_per_pixel, 0.1)
    yy, xx = np.ogrid[:height, :width]
    return (xx - width / 2.0) ** 2 + (yy - height / 2.0) ** 2 <= radius_px**2


def path_sample_weight(distance_km: float) -> float:
    """Weight a light-path sample by how much it can still block the view.

    Without terrain data the exact horizon is unknown, but a low deck beyond
    ~120 km is increasingly likely to sit below the observer's horizon line,
    so far samples decay toward a small floor instead of counting at full
    weight (the old flat 220 km reach over-weighted mountain-shadowed echo).
    """
    if distance_km <= 120.0:
        return 1.0
    return max(0.25, (220.0 - distance_km) / 100.0)


def path_sample_pixels_with_weights(
    center_lat: float,
    center_lon: float,
    azimuth_deg: float,
    width: int,
    height: int,
    zoom: int,
) -> list[tuple[tuple[int, int], float]]:
    points: list[tuple[tuple[int, int], float]] = []
    for distance_km in (40.0, 80.0, 120.0, 170.0, 220.0):
        weight = path_sample_weight(distance_km)
        for offset in (-24.0, -12.0, 0.0, 12.0, 24.0):
            lat, lon = destination_lat_lon(
                center_lat,
                center_lon,
                (azimuth_deg + offset) % 360.0,
                distance_km,
            )
            x, y = centered_mercator_pixel(
                lat,
                lon,
                center_lat,
                center_lon,
                width,
                height,
                zoom,
            )
            if 0 <= x < width and 0 <= y < height:
                points.append(((x, y), weight))
    return points


def path_sample_pixels(
    center_lat: float,
    center_lon: float,
    azimuth_deg: float,
    width: int,
    height: int,
    zoom: int,
) -> list[tuple[int, int]]:
    return [
        pixel
        for pixel, _ in path_sample_pixels_with_weights(
            center_lat, center_lon, azimuth_deg, width, height, zoom
        )
    ]


def destination_lat_lon(lat: float, lon: float, bearing_deg: float, distance_km: float) -> tuple[float, float]:
    radius_km = 6371.0088
    angular = distance_km / radius_km
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 180.0) % 360.0) - 180.0


def centered_mercator_pixel(
    lat: float,
    lon: float,
    center_lat: float,
    center_lon: float,
    width: int,
    height: int,
    zoom: int,
) -> tuple[int, int]:
    world_size = 256.0 * (2**zoom)

    def world_xy(value_lat: float, value_lon: float) -> tuple[float, float]:
        clipped_lat = max(-85.05112878, min(85.05112878, value_lat))
        x = (value_lon + 180.0) / 360.0 * world_size
        y = (
            1.0
            - math.asinh(math.tan(math.radians(clipped_lat))) / math.pi
        ) / 2.0 * world_size
        return x, y

    x, y = world_xy(lat, lon)
    center_x, center_y = world_xy(center_lat, center_lon)
    return round(width / 2.0 + x - center_x), round(height / 2.0 + y - center_y)


def sample_pixel_patches(
    values: np.ndarray,
    pixels: Sequence[tuple[int, int]],
    radius: int,
) -> np.ndarray:
    samples: list[np.ndarray] = []
    height, width = values.shape
    for x, y in pixels:
        patch = values[
            max(0, y - radius) : min(height, y + radius + 1),
            max(0, x - radius) : min(width, x + radius + 1),
        ]
        samples.append(patch.reshape(-1))
    return np.concatenate(samples) if samples else np.asarray([], dtype=np.float32)


def weighted_echo_ratio(
    values: np.ndarray, weights: np.ndarray, threshold_dbz: float
) -> float:
    finite = np.isfinite(values) & np.isfinite(weights)
    total = float(np.sum(weights[finite]))
    if total <= 0.0:
        return 0.0
    hits = float(
        np.sum(weights[finite & (values >= threshold_dbz)])
    )
    return clamp(hits / total)


def echo_ratio(values: np.ndarray, threshold_dbz: float, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return clamp(float(np.sum(np.isfinite(values) & (values >= threshold_dbz))) / denominator)


def cached_url_bytes(url: str, target: Path, max_age_seconds: Optional[int]) -> bytes:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        if max_age_seconds is None or time.time() - target.stat().st_mtime <= max_age_seconds:
            return target.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "firesky/0.1 burning-sunset-engine"})
    payload = urllib.request.urlopen(request, timeout=30).read()
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return payload
