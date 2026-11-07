from __future__ import annotations

import math
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from PIL import Image
import numpy as np

from firesky.geometry import (
    destination_point,
    required_illumination_height_m,
    solar_snapshot,
    spherical_horizon_margin_m,
    spherical_line_of_sight_height_m,
)
from firesky.bounded_lock import acquire_file_lock
from firesky.version import NOWCAST_ALGORITHM_VERSION
from firesky.models import ForecastRequest, GeoPoint, GridCellFeature, NowcastAdjustment
from firesky.scoring import clamp
from firesky.storage import dated_directory
from firesky.satellite_cloud_column import (
    CalibratedSatelliteFrame,
    DEFAULT_HSD_CACHE_DIR,
    AutoHsdCompactCacheProvider,
    HsdAdvectionPlan,
    HsdMotionEstimate,
    SATELLITE_CLOUD_COLUMN_ALGORITHM,
    SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
    SatelliteCloudColumn,
    SatelliteCloudColumnSet,
    VerticalPrior,
    aggregate_low_cloud_state,
    aggregate_local_low_cloud_state,
    aggregate_retrieval_quality,
    RETRIEVAL_QUALITIES_ORDER,
    aggregate_vertical_prior_identity,
    aggregate_vertical_prior_source,
    cloud_column_coverage,
    cloud_columns_to_features,
    advect_hsd_frame,
    estimate_hsd_motion,
    plan_hsd_advection,
    retrieve_cloud_columns,
    vertical_prior_from_features,
)

_LOGGER = logging.getLogger(__name__)

_HSD_FRAME_BANDS = frozenset({"B07", "B13", "B15"})

BASE_URL = "https://www.data.jma.go.jp/mscweb/data/himawari"
NICT_BASE_URL = "https://himawari8.nict.go.jp"
NICT_LATEST_URL = f"{NICT_BASE_URL}/img/FULL_24h/latest.json"
OPTION_RE = re.compile(r"<option value=([0-9]{4})>([^<]+)</option>")
ENTRY_CACHE_TTL_SECONDS = 300
MAX_MODEL_SATELLITE_LEAD_HOURS = 4.0
MAX_NOWCAST_ADVECTION_HOURS = 4.0
NOWCAST_TIME_OFFSETS_MINUTES: Tuple[int, ...] = tuple(range(-30, 31, 5))
# The event score should describe the sunrise/sunset itself. The outer window
# remains available for trend display, but it must not let a later spike replace
# the event-time verdict.
NOWCAST_CORE_WINDOW_MINUTES = 15
VISIBLE_MIN_SOLAR_ELEVATION_DEG = 1.5
VISIBLE_FULL_SOLAR_ELEVATION_DEG = 8.0
OBSERVED_FRAME_TOLERANCE_MINUTES = 6.0
SATELLITE_OUTER_DISTANCE_STEP_KM = 80.0
MIN_NOWCAST_CALIBRATED_SAMPLE_COVERAGE = 0.80
SATELLITE_CACHE_LOCK_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class JmaArea:
    code: str
    label: str
    west: float
    east: float
    north: float
    south: float

    @property
    def span(self) -> float:
        return (self.east - self.west) * (self.north - self.south)

    def contains(self, point: GeoPoint) -> bool:
        return self.west <= point.lon <= self.east and self.south <= point.lat <= self.north


@dataclass(frozen=True)
class ImageEntry:
    token: str
    valid_time_utc: datetime
    url: str


AREAS: Tuple[JmaArea, ...] = tuple(
    sorted(
        [
            JmaArea("jpn", "Japan", 115.0, 155.0, 48.0, 22.0),
            JmaArea("se3", "Southeast Asia 3", 105.0, 130.0, 17.0, 0.0),
            JmaArea("se2", "Southeast Asia 2", 105.0, 140.0, 30.0, 0.0),
            JmaArea("se4", "South Asia", 60.0, 125.0, 35.0, 0.0),
            JmaArea("ca1", "Central Asia", 55.0, 130.0, 50.0, 15.0),
            JmaArea("se1", "Southeast Asia 1", 90.0, 165.0, 30.0, -15.0),
        ],
        key=lambda area: area.span,
    )
)


def should_use_model_satellite_adjustment(
    request: ForecastRequest,
    anchor_utc: datetime,
    reference_utc: datetime,
) -> bool:
    """Limit optional satellite correction for model forecasts, not nowcast access."""
    if request.satellite_source == "off":
        return False
    if request.phenomenon not in {"dawn", "dusk"}:
        return False
    horizon_hours = (anchor_utc - reference_utc).total_seconds() / 3600.0
    return -0.5 <= horizon_hours <= MAX_MODEL_SATELLITE_LEAD_HOURS


def _satellite_frame_contract(frame: CalibratedSatelliteFrame) -> tuple[str, str, str, list[str]]:
    """Return stable API provenance for the complete calibrated frame source."""
    bands = frozenset(frame.bands)
    if bands == _HSD_FRAME_BANDS:
        return (
            "hsd",
            "noaa-himawari9-hsd-calibrated+gfs-prior",
            "hsd-b07+b13+b15",
            ["B07", "B13", "B15"],
        )
    raise ValueError("calibrated satellite frame has an unsupported band contract")


class JmaNowcastAnalyzer:
    def __init__(
        self,
        *,
        calibrated_provider: Optional[Any] = None,
        vertical_prior_provider: Optional[Callable[[GeoPoint, datetime], VerticalPrior]] = None,
    ) -> None:
        # Synchronous scoring is cache-only. A cold HSD cache is warmed by the
        # bounded background path and must never block a user request.
        # HSD 独占：HCAI 整文件下载过慢（JAXA P-Tree 全盘产品）已从实况链路移除。
        self.calibrated_provider = calibrated_provider or AutoHsdCompactCacheProvider(
            DEFAULT_HSD_CACHE_DIR,
            allow_foreground=False,
        )
        self.vertical_prior_provider = vertical_prior_provider

    def analyze(
        self,
        request: ForecastRequest,
        reference_utc: datetime,
        glow_azimuth_deg: float,
        target_time_utc: Optional[datetime] = None,
    ) -> Optional[NowcastAdjustment]:
        target_time = target_time_utc or reference_utc
        vertical_prior_provider = self.vertical_prior_provider
        if vertical_prior_provider is not None and hasattr(vertical_prior_provider, "for_request"):
            vertical_prior_provider = vertical_prior_provider.for_request(request)
        calibrated_frame: Optional[CalibratedSatelliteFrame] = None
        calibrated_frames: tuple[CalibratedSatelliteFrame, ...] = ()
        motion: Optional[HsdMotionEstimate] = None
        advection: Optional[HsdAdvectionPlan] = None
        single_historical_observation = False
        # ForecastService chooses the source reference deliberately: current
        # requests use the latest completed scan, while retrospective event
        # requests pass the event anchor so the provider selects historical
        # scans rather than silently scoring the current cloud field.
        calibrated_reference = reference_utc
        try:
            fetch_series = getattr(self.calibrated_provider, "fetch_series", None)
            if callable(fetch_series):
                calibrated_frames = tuple(fetch_series(
                    calibrated_reference,
                    request.point,
                    radius_km=float(request.radius_km),
                    count=2,
                ))
            else:
                single = self.calibrated_provider.fetch(
                    calibrated_reference,
                    request.point,
                    radius_km=float(request.radius_km),
                )
                calibrated_frames = (single,) if single is not None else ()
            if calibrated_frames:
                calibrated_frames = tuple(sorted(
                    calibrated_frames,
                    key=lambda item: item.valid_time_utc,
                    reverse=True,
                ))
                calibrated_frame = calibrated_frames[0]
                if len(calibrated_frames) >= 2:
                    motion = estimate_hsd_motion(calibrated_frames[1], calibrated_frames[0])
                advection = plan_hsd_advection(calibrated_frames, target_time, motion)
        except Exception:
            _LOGGER.exception(
                "calibrated Himawari frame preparation failed for %s at %s",
                request.name,
                calibrated_reference.isoformat(),
            )
            calibrated_frame = None
            calibrated_frames = ()
            motion = None
            advection = None
        if calibrated_frame is not None and advection is not None:
            if advection.horizon_hours > MAX_NOWCAST_ADVECTION_HOURS:
                # HSD is a nowcast observation. Beyond four hours it remains a
                # diagnostic source, never a numerical forecast input. Build
                # the report at the observation time so the caller can display
                # provenance without applying it to the distant event.
                target_time = calibrated_frame.valid_time_utc
            projected_frame = advect_hsd_frame(calibrated_frame, advection, motion)
            if projected_frame.valid_time_utc != calibrated_frame.valid_time_utc and advection.applied:
                calibrated_frame = projected_frame
            elif (
                advection.horizon_hours > 0.10
                and not advection.applied
                and advection.motion_quality != "diagnostic-only"
            ):
                # A pair is mandatory before claiming a future cloud motion.
                # Historical replay is different: when the event lookup is
                # anchored at the event itself, the latest complete scan may
                # be a few minutes earlier because the next scan was missing
                # or failed to download.  Publish that direct observation
                # with an explicit no-advection diagnostic; never turn it
                # into a forecast and never use this branch for future events.
                if target_time <= calibrated_reference:
                    single_historical_observation = True
                else:
                    return None
        if calibrated_frame is not None and vertical_prior_provider is not None:
            # The synchronous path is cache-only.  Display-image indexes and
            # JPEG downloads are optional diagnostics and must never add
            # latency or network dependence to a calibrated HSD result.
            return calibrated_only_nowcast_adjustment(
                request,
                target_time,
                glow_azimuth_deg,
                calibrated_frame,
                vertical_prior_provider,
                motion=motion,
                advection=advection,
                source_times_utc=tuple(frame.valid_time_utc for frame in calibrated_frames),
                observation_frames=calibrated_frames,
                single_historical_observation=single_historical_observation,
            )
        # A compact calibrated frame and matching GFS prior are mandatory for
        # scoring nowcast.  Do not fall through to the legacy display-image
        # path when either one is unavailable: that path has no vertical cloud
        # semantics and cannot produce a publishable observation.
        if calibrated_frame is None or vertical_prior_provider is None:
            return None


def calibrated_only_nowcast_adjustment(
    request: ForecastRequest,
    target_time: datetime,
    azimuth_deg: float,
    frame: CalibratedSatelliteFrame,
    vertical_prior_provider: Callable[[GeoPoint, datetime], VerticalPrior],
    *,
    motion: Optional[HsdMotionEstimate] = None,
    advection: Optional[HsdAdvectionPlan] = None,
    source_times_utc: Sequence[datetime] = (),
    observation_frames: Sequence[CalibratedSatelliteFrame] = (),
    single_historical_observation: bool = False,
) -> Optional[NowcastAdjustment]:
    """Build a model-assisted nowcast from one complete calibrated satellite source."""
    # A provider may return the same scan more than once while a raw/compact
    # cache is being reconciled.  Keep one frame per canonical UTC timestamp;
    # the timeline contract cannot represent two independent observations at
    # one valid time.
    unique_candidates: dict[datetime, CalibratedSatelliteFrame] = {}
    for candidate in observation_frames:
        valid_time = candidate.valid_time_utc.astimezone(timezone.utc)
        unique_candidates.setdefault(valid_time, candidate)
    if not unique_candidates:
        valid_time = frame.valid_time_utc.astimezone(timezone.utc)
        unique_candidates[valid_time] = frame
    scoring_frames = [
        candidate
        for candidate in unique_candidates.values()
        if int(round((candidate.valid_time_utc - target_time).total_seconds() / 300.0) * 5)
        in NOWCAST_TIME_OFFSETS_MINUTES
    ]
    if not scoring_frames:
        scoring_frames = [frame]
    unique_frames = {
        candidate.valid_time_utc.astimezone(timezone.utc): candidate
        for candidate in scoring_frames
    }
    observed_features: dict[int, list[GridCellFeature]] = {}
    times: dict[int, datetime] = {}
    all_column_sets = []
    primary_column_sets = []
    primary_features: list[GridCellFeature] = []
    primary_prior_samples: list[tuple[float, float, Optional[VerticalPrior]]] = []
    primary_distance = float("inf")
    motion_dx_for_retrieval = (
        motion.dx_px_per_hour if motion is not None and motion.quality == "strong" else 0.0
    )
    motion_dy_for_retrieval = (
        motion.dy_px_per_hour if motion is not None and motion.quality == "strong" else 0.0
    )
    for candidate in sorted(unique_frames.values(), key=lambda item: item.valid_time_utc):
        prior_samples_by_key: dict[
            tuple[float, float], Optional[VerticalPrior]
        ] = {}

        def recording_prior_provider(
            target: GeoPoint, valid_time: datetime
        ) -> Optional[VerticalPrior]:
            sample_key = (round(float(target.lat), 5), round(float(target.lon), 5))
            try:
                prior = vertical_prior_provider(target, valid_time)
            except Exception:
                # Retrieval owns the failure-to-unknown policy. Keep the
                # diagnostic sample so the profile can show a full unknown
                # column when the prior itself is unavailable.
                prior_samples_by_key[sample_key] = None
                raise
            prior_samples_by_key[sample_key] = (
                prior if isinstance(prior, VerticalPrior) else None
            )
            return prior

        features, column_sets = calibrated_satellite_cloud_retrieval(
            candidate,
            request.point,
            azimuth_deg,
            recording_prior_provider,
            radius_km=float(request.radius_km),
            motion_dx_px_per_hour=motion_dx_for_retrieval,
            motion_dy_px_per_hour=motion_dy_for_retrieval,
        )
        offset = int(round((candidate.valid_time_utc - target_time).total_seconds() / 300.0) * 5)
        if offset in times and times[offset] != candidate.valid_time_utc:
            raise ValueError(
                "independent HSD scans collapse to one nowcast timeline offset"
            )
        observed_features[offset] = features
        times[offset] = candidate.valid_time_utc
        all_column_sets.extend(column_sets)
        distance = abs((candidate.valid_time_utc - target_time).total_seconds())
        if distance < primary_distance:
            primary_distance = distance
            primary_column_sets = column_sets
            primary_features = features
            primary_prior_samples = [
                (lat, lon, prior)
                for (lat, lon), prior in prior_samples_by_key.items()
            ]
    column_sets = all_column_sets
    metrics = calibrated_cloud_metrics(primary_column_sets, azimuth_deg)
    expected_sample_count = len(
        satellite_sample_points(request.point, azimuth_deg, float(request.radius_km))
    )
    sample_coverage = calibrated_sample_coverage_ratio(
        primary_column_sets,
        sample_points=satellite_sample_points(
            request.point, azimuth_deg, float(request.radius_km)
        ),
    )
    if sample_coverage <= 0.0:
        # fail-closed：整个视图没有任何可用校准列（数据缺口，非晴空）。
        return None
    calibrated_quality = merge_retrieval_quality(item.retrieval_quality for item in column_sets)
    source_kind, source_label, image_channel, profile_bands = _satellite_frame_contract(frame)
    # The compact frame is an observation, not a projected field.  Attach it
    # only to the nearest five-minute event offset when it is in the window.
    if not observed_features:
        raise RuntimeError("Himawari calibrated scan is outside the supported five-minute nowcast window")
    low_state = aggregate_local_low_cloud_state(column_sets)
    motion_dx = motion.dx_px_per_hour if motion is not None and motion.quality == "strong" else 0.0
    motion_dy = motion.dy_px_per_hour if motion is not None and motion.quality == "strong" else 0.0
    motion_correlation = motion.correlation if motion is not None else 0.0
    # The projected frame carries target_time_utc when advection is applied;
    # keeping projection_hours at zero prevents service-level adjustment from
    # adding the same horizon a second time.
    projection_hours = 0.0
    confidence_factor = 1.0
    if advection is not None and advection.motion_quality == "decayed":
        confidence_factor = 0.55 + 0.45 * advection.scale
    normalized_source_times = sorted(
        {
            value.astimezone(timezone.utc)
            for value in source_times_utc
        },
        reverse=True,
    )
    if not normalized_source_times:
        normalized_source_times = [frame.valid_time_utc.astimezone(timezone.utc)]
    satellite_vertical_profile = build_satellite_vertical_profile(
        primary_column_sets,
        primary_features,
        expected_sample_count=expected_sample_count,
        valid_time_utc=frame.valid_time_utc,
        prior_samples=primary_prior_samples,
        source_label=source_label,
        bands=profile_bands,
    )
    observed_time = normalized_source_times[0]
    source_summary = (
        "HSD 使用 B07/B13/B15 校准观测；同周期 GFS 只提供模式垂直先验，"
        "不把结果标为探空或官方云高/云相产品。"
    )
    observation_summary = (
        f"NOAA Himawari {source_kind.upper()} 校准观测时次 {observed_time:%Y-%m-%d %H:%MZ}；"
        f"{source_summary}"
    )
    if frame.valid_time_utc != observed_time:
        observation_summary += (
            f" 平流后的有效云场时次 {frame.valid_time_utc:%Y-%m-%d %H:%MZ}。"
        )
    summary = [
        observation_summary,
        f"分段缓存下载 {frame.range_bytes / 1024 / 1024:.1f} MiB、"
        f"{frame.range_blocks} 个校准通道分段；在线评分只读取本地紧凑缓存。",
    ]
    if advection is not None:
        times_text = ", ".join(value.strftime("%H:%MZ") for value in normalized_source_times)
        summary.append(
            f"{source_kind.upper()} 来源时次 [{times_text or frame.valid_time_utc.strftime('%H:%MZ')}]；"
            f"运动质量 {advection.motion_quality}，运动年龄 "
            f"{advection.motion_age_minutes:.1f} 分钟" if advection.motion_age_minutes is not None
            else f"{source_kind.upper()} 来源时次 [{times_text or frame.valid_time_utc.strftime('%H:%MZ')}]；运动质量 {advection.motion_quality}。"
        )
        if single_historical_observation:
            summary.append(
                f"历史事件只获得一帧完整校准 {source_kind.upper()} 扫描（距事件 {advection.horizon_hours * 60:.0f} 分钟）；"
                "按事件前最近观测直接评分，不进行云场运动外推。"
            )
        else:
            summary.append(
                f"目标时次外推 {advection.horizon_hours:.2f} 小时，计划 {advection.reason}；"
                f"超过 {MAX_NOWCAST_ADVECTION_HOURS:.0f} 小时仅保留实况诊断。"
            )
    if low_state == "detected":
        summary.append(
            "校准多光谱与 GFS 先验共同支持观测者附近低云。"
        )
    elif low_state == "unknown":
        summary.append("低云状态仍不确定；没有把缺测或暖低云歧义回填为晴空。")
    adjustment = NowcastAdjustment(
        nowcast_algorithm=NOWCAST_ALGORITHM_VERSION,
        source=source_label,
        image_valid_time_utc=frame.valid_time_utc,
        image_valid_time_local=frame.valid_time_utc.astimezone(ZoneInfo(request.timezone_name)),
        image_channel=image_channel,
        image_area="Himawari HSD segmented footprint",
        near_cloud_ratio=metrics["near_projected"],
        far_cloud_ratio=metrics["far_projected"],
        trend_score=0.5,
        visibility_penalty=(
            clamp(1.0 - metrics["horizon_clearance_score"])
            if has_calibrated_scoring
            else 0.0
        ),
        target_bonus=(
            clamp(1.0 - abs(metrics["far_projected"] - 0.42) / 0.42)
            if has_calibrated_scoring
            else 0.5
        ),
        confidence=clamp(
            (0.62 if has_calibrated_scoring else 0.20)
            * (0.55 + 0.45 * cloud_column_coverage(column_sets))
            * confidence_factor
            * (
                # A single historical scan scored directly loses skill with
                # its distance from the event; fog decks evolve on ~45-minute
                # scales, so decay the confidence instead of reporting a
                # stale frame at full weight.
                math.exp(-advection.horizon_hours * 60.0 / 45.0)
                if single_historical_observation and advection is not None
                else 1.0
            )
        ),
        motion_dx_px_per_hour=motion_dx,
        motion_dy_px_per_hour=motion_dy,
        motion_correlation=motion_correlation,
        projection_hours=projection_hours,
        satellite_source_times_utc=normalized_source_times,
        satellite_motion_age_minutes=(motion.age_minutes if motion is not None else None),
        satellite_motion_quality=(advection.motion_quality if advection is not None else "unknown"),
        satellite_advection_horizon_hours=(advection.horizon_hours if advection is not None else 0.0),
        summary=summary,
        infrared_near_cloud_ratio=(metrics["near_projected"] if has_calibrated_scoring else None),
        infrared_far_cloud_ratio=(metrics["far_projected"] if has_calibrated_scoring else None),
        local_cloud_ratio=(metrics["local_cloud_ratio"] if has_calibrated_scoring else 0.0),
        horizon_cloud_ratio=(metrics["horizon_cloud_ratio"] if has_calibrated_scoring else 0.0),
        thin_cloud_ratio=(metrics["thin_cloud_ratio"] if has_calibrated_scoring else 0.0),
        thick_cloud_ratio=(metrics["thick_cloud_ratio"] if has_calibrated_scoring else 0.0),
        cloud_texture_score=(metrics["cloud_texture_score"] if has_calibrated_scoring else 0.0),
        cloud_uniformity_score=(metrics["cloud_uniformity_score"] if has_calibrated_scoring else 0.0),
        cloud_blanket_score=(metrics["cloud_blanket_score"] if has_calibrated_scoring else 0.0),
        horizon_clearance_score=(metrics["horizon_clearance_score"] if has_calibrated_scoring else 1.0),
        composition_score=(metrics["composition_score"] if has_calibrated_scoring else 0.0),
        observed_features_by_offset=observed_features,
        observed_feature_times_by_offset=times,
        cloud_column_schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
        cloud_column_sets=[item.as_dict() for item in column_sets],
        vertical_prior_identity=(
            aggregate_vertical_prior_identity(column_sets) if column_sets else "unknown"
        ),
        vertical_prior_source=(
            aggregate_vertical_prior_source(
                column for item in column_sets for column in item.columns
            )
            if column_sets else "unknown"
        ),
        satellite_retrieval_quality=calibrated_quality,
        satellite_low_cloud_state=low_state,
        satellite_range_bytes=frame.range_bytes,
        satellite_range_blocks=frame.range_blocks,
        nowcast_point_lat=float(request.point.lat),
        nowcast_point_lon=float(request.point.lon),
        nowcast_cache_identity=nowcast_cache_identity(
            request,
            frame,
            frame.valid_time_utc,
            vertical_prior_identity=(
                aggregate_vertical_prior_identity(column_sets) if column_sets else "unknown"
            ),
            source_times_utc=normalized_source_times,
        ),
        satellite_vertical_profile=satellite_vertical_profile,
        calibrated_sample_coverage_ratio=sample_coverage,
    )
    adjustment.summary.append(
        f'校准采样覆盖率 {sample_coverage * 100:.0f}/100：'
        + (
            '全视图可用，按满覆盖计分。'
            if sample_coverage >= 0.95
            else '部分采样模糊（拂晓/晨昏分类歧义或云边），未覆盖样本按 unknown 处理、不参与评分。'
        )
    )
    return adjustment


def nowcast_cache_identity(
    request: ForecastRequest,
    frame: Optional[CalibratedSatelliteFrame],
    valid_time_utc: datetime,
    *,
    vertical_prior_identity: str,
    source_times_utc: Sequence[datetime] = (),
) -> str:
    """Bind nowcast history/cache provenance to the requested point and scan."""
    source = frame.source if frame is not None else "display-only-unknown"
    source_times = ",".join(
        value.astimezone(timezone.utc).isoformat()
        for value in sorted(set(source_times_utc), reverse=True)
    )
    return (
        f"{NOWCAST_ALGORITHM_VERSION}|point={float(request.point.lat):+.5f},"
        f"{float(request.point.lon):+.5f}|radius={float(request.radius_km):.3f}|"
        f"valid={valid_time_utc.astimezone(timezone.utc).isoformat()}|source={source}|"
        f"vertical-prior={vertical_prior_identity}|source-times={source_times}"
    )


def pick_area(point: GeoPoint) -> Optional[JmaArea]:
    for area in AREAS:
        if area.contains(point):
            return area
    return None


def nearest_entry(entries: Sequence[ImageEntry], valid_time_utc: datetime) -> Optional[ImageEntry]:
    if not entries:
        return None
    candidate = min(entries, key=lambda item: abs((item.valid_time_utc - valid_time_utc).total_seconds()))
    return candidate if abs((candidate.valid_time_utc - valid_time_utc).total_seconds()) <= 20 * 60 else None


def visible_channel_weight(
    request: ForecastRequest,
    reference_utc: datetime,
    latest: Optional[Image.Image],
    previous: Optional[Image.Image],
) -> float:
    if latest is None or previous is None:
        return 0.0
    elevation = solar_snapshot(request.point, reference_utc, request.timezone_name).elevation_deg
    if elevation <= VISIBLE_MIN_SOLAR_ELEVATION_DEG:
        return 0.0
    latest_quality = image_texture_quality(latest)
    previous_quality = image_texture_quality(previous)
    light_factor = clamp(
        (elevation - VISIBLE_MIN_SOLAR_ELEVATION_DEG)
        / (VISIBLE_FULL_SOLAR_ELEVATION_DEG - VISIBLE_MIN_SOLAR_ELEVATION_DEG)
    )
    return clamp(light_factor * min(latest_quality, previous_quality))


def image_texture_quality(image: Image.Image) -> float:
    values = np.asarray(image, dtype=np.float32)
    if values.size == 0:
        return 0.0
    return clamp((float(values.std()) - 3.0) / 28.0)


def channel_sector_metrics(
    latest: Image.Image,
    previous: Image.Image,
    projected: Image.Image,
    area: JmaArea,
    point: GeoPoint,
    azimuth_deg: float,
) -> dict[str, float]:
    near_distances = (40.0, 80.0, 120.0)
    far_distances = (160.0, 220.0, 300.0, 380.0)
    return {
        "near_current": sector_cloud_ratio(latest, area, point, azimuth_deg, near_distances),
        "far_current": sector_cloud_ratio(latest, area, point, azimuth_deg, far_distances),
        "near_projected": sector_cloud_ratio(projected, area, point, azimuth_deg, near_distances),
        "far_projected": sector_cloud_ratio(projected, area, point, azimuth_deg, far_distances),
        "near_previous": sector_cloud_ratio(previous, area, point, azimuth_deg, near_distances),
        "far_previous": sector_cloud_ratio(previous, area, point, azimuth_deg, far_distances),
    }


def cloud_composition_metrics(
    infrared: Image.Image,
    visible: Optional[Image.Image],
    visible_weight: float,
    area: JmaArea,
    point: GeoPoint,
    azimuth_deg: float,
) -> dict[str, float]:
    local_points = [
        (bearing, distance)
        for distance in (30.0, 70.0, 120.0)
        for bearing in range(0, 360, 30)
    ]
    horizon_points = [
        ((azimuth_deg + offset) % 360.0, distance)
        for distance in (30.0, 60.0, 100.0, 140.0)
        for offset in (-30.0, -15.0, 0.0, 15.0, 30.0)
    ]
    local_values, local_ir_values = sampled_cloud_values(
        infrared, visible, visible_weight, area, point, local_points
    )
    horizon_values, horizon_ir_values = sampled_cloud_values(
        infrared, visible, visible_weight, area, point, horizon_points
    )
    local_cloud_ratio = sample_mean(local_values)
    horizon_cloud_ratio = sample_mean(horizon_values)
    texture = clamp(float(np.std(local_values)) / 0.28) if local_values else 0.0
    uniformity = clamp(1.0 - float(np.std(local_values)) / 0.22) if local_values else 0.0
    blanket = clamp(local_cloud_ratio * uniformity)
    thin_ratio = sample_mean([thin_cloud_proxy(value) for value in local_ir_values])
    thick_ratio = sample_mean([thick_cloud_proxy(value) for value in local_ir_values])
    horizon_thick = sample_mean([thick_cloud_proxy(value) for value in horizon_ir_values])
    horizon_clearance = clamp(1.0 - 0.62 * horizon_cloud_ratio - 0.38 * horizon_thick)
    canvas_score = clamp(1.0 - abs(local_cloud_ratio - 0.46) / 0.46)
    composition_score = clamp(
        0.30 * canvas_score
        + 0.30 * texture
        + 0.26 * horizon_clearance
        + 0.14 * (1.0 - thick_ratio)
        - 0.30 * blanket
    )
    return {
        "local_cloud_ratio": local_cloud_ratio,
        "horizon_cloud_ratio": horizon_cloud_ratio,
        "thin_cloud_ratio": thin_ratio,
        "thick_cloud_ratio": thick_ratio,
        "cloud_texture_score": texture,
        "cloud_uniformity_score": uniformity,
        "cloud_blanket_score": blanket,
        "horizon_clearance_score": horizon_clearance,
        "composition_score": composition_score,
    }


def sampled_cloud_values(
    infrared: Image.Image,
    visible: Optional[Image.Image],
    visible_weight: float,
    area: JmaArea,
    point: GeoPoint,
    samples: Sequence[tuple[float, float]],
) -> tuple[list[float], list[float]]:
    fused_values: list[float] = []
    infrared_values: list[float] = []
    for bearing_deg, distance_km in samples:
        target = destination_point(point, bearing_deg, distance_km)
        pixel = pixel_for(area, target, infrared.size)
        if pixel is None:
            continue
        infrared_cloud = patch_cloudiness(infrared, pixel)
        visible_cloud = None
        if visible is not None:
            visible_pixel = pixel_for(area, target, visible.size)
            if visible_pixel is not None:
                visible_cloud = patch_cloudiness(visible, visible_pixel)
        infrared_values.append(infrared_cloud)
        fused_values.append(fuse_channel_value(infrared_cloud, visible_cloud, visible_weight))
    return fused_values, infrared_values


def observed_satellite_feature_frames(
    request: ForecastRequest,
    area: JmaArea,
    glow_azimuth_deg: float,
    anchor_utc: datetime,
    infrared_frames: Dict[datetime, Image.Image],
    visible_frames: Dict[datetime, Image.Image],
    multispectral: Optional[dict],
    dx_px_per_hour: float,
    dy_px_per_hour: float,
    calibrated_frame: Optional[CalibratedSatelliteFrame] = None,
    vertical_prior_provider: Optional[Callable[[GeoPoint, datetime], VerticalPrior]] = None,
    radius_km: Optional[float] = None,
    canonical_calibrated_features: Optional[Sequence[GridCellFeature]] = None,
) -> tuple[Dict[int, List[GridCellFeature]], Dict[int, datetime]]:
    if not infrared_frames:
        if calibrated_frame is not None and vertical_prior_provider is not None:
            features = list(canonical_calibrated_features or calibrated_satellite_cloud_features(
                calibrated_frame,
                request.point,
                glow_azimuth_deg,
                vertical_prior_provider,
                radius_km=float(radius_km or request.radius_km),
                motion_dx_px_per_hour=dx_px_per_hour,
                motion_dy_px_per_hour=dy_px_per_hour,
            ))
            offset = int(round((calibrated_frame.valid_time_utc - anchor_utc).total_seconds() / 300.0) * 5)
            if offset in NOWCAST_TIME_OFFSETS_MINUTES:
                return {offset: features}, {offset: calibrated_frame.valid_time_utc}
        return {}, {}
    frames: Dict[int, List[GridCellFeature]] = {}
    source_times: Dict[int, datetime] = {}
    canonical_features: Optional[list[GridCellFeature]] = (
        list(canonical_calibrated_features) if canonical_calibrated_features is not None else None
    )
    if calibrated_frame is not None and vertical_prior_provider is not None and canonical_features is None:
        canonical_features = calibrated_satellite_cloud_features(
            calibrated_frame,
            request.point,
            glow_azimuth_deg,
            vertical_prior_provider,
            radius_km=float(radius_km or request.radius_km),
            motion_dx_px_per_hour=dx_px_per_hour,
            motion_dy_px_per_hour=dy_px_per_hour,
        )
    ordered_times = sorted(infrared_frames)
    for offset_minutes in NOWCAST_TIME_OFFSETS_MINUTES:
        valid_time = anchor_utc + timedelta(minutes=offset_minutes)
        if calibrated_frame is not None and vertical_prior_provider is not None:
            # A single calibrated scan is valid at its observation time only.
            # A single HSD retrieval has no advection contract, so do not copy it to other
            # event offsets and present that as a cloud motion forecast.
            calibrated_offset = int(
                round((calibrated_frame.valid_time_utc - anchor_utc).total_seconds() / 300.0)
                * 5
            )
            if offset_minutes == calibrated_offset:
                frames[offset_minutes] = list(canonical_features or [])
                source_times[offset_minutes] = calibrated_frame.valid_time_utc
            continue
        source_time = min(ordered_times, key=lambda value: abs((value - valid_time).total_seconds()))
        # Display JPEGs have no calibrated vertical semantics.  Keep only
        # their selected source time for diagnostics; scoring remains empty
        # until the calibrated HSD branch above supplies cloud features.
        frames[offset_minutes] = []
        source_times[offset_minutes] = source_time
    return frames, source_times


def calibrated_satellite_cloud_features(
    frame: CalibratedSatelliteFrame,
    point: GeoPoint,
    azimuth_deg: float,
    vertical_prior_provider: Callable[[GeoPoint, datetime], VerticalPrior],
    *,
    radius_km: float,
    motion_dx_px_per_hour: float = 0.0,
    motion_dy_px_per_hour: float = 0.0,
) -> list[GridCellFeature]:
    features, _ = calibrated_satellite_cloud_retrieval(
        frame,
        point,
        azimuth_deg,
        vertical_prior_provider,
        radius_km=radius_km,
        motion_dx_px_per_hour=motion_dx_px_per_hour,
        motion_dy_px_per_hour=motion_dy_px_per_hour,
    )
    return features


def calibrated_satellite_cloud_retrieval(
    frame: CalibratedSatelliteFrame,
    point: GeoPoint,
    azimuth_deg: float,
    vertical_prior_provider: Callable[[GeoPoint, datetime], VerticalPrior],
    *,
    radius_km: float,
    motion_dx_px_per_hour: float = 0.0,
    motion_dy_px_per_hour: float = 0.0,
) -> tuple[list[GridCellFeature], list[SatelliteCloudColumnSet]]:
    if radius_km <= 0.0:
        raise ValueError("calibrated satellite retrieval requires analysis radius")
    grouped: dict[str, list] = {}
    unknown_sets: list[SatelliteCloudColumnSet] = []
    features: list[GridCellFeature] = []

    def unknown_column(
        target: GeoPoint,
        distance_km: float,
        bearing_deg: float,
        prior: Optional[VerticalPrior] = None,
    ) -> SatelliteCloudColumn:
        identity = (
            prior.identity
            if prior is not None
            else f"unknown-sample:{float(target.lat):+.5f},{float(target.lon):+.5f}"
        )
        return SatelliteCloudColumn(
            schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
            algorithm=SATELLITE_CLOUD_COLUMN_ALGORITHM,
            valid_time_utc=frame.valid_time_utc,
            lat=float(target.lat),
            lon=float(target.lon),
            distance_km=float(distance_km),
            bearing_deg=float(bearing_deg) % 360.0,
            base_p10_asl_m=None,
            base_p50_asl_m=None,
            base_p90_asl_m=None,
            top_asl_m=None,
            terrain_asl_m=prior.terrain_asl_m if prior is not None else None,
            phase="unknown",
            optical_depth=None,
            cloud_probability_by_height={},
            horizontal_cloud_probability=None,
            low_cloud_state="unknown",
            retrieval_quality="unknown",
            source=frame.source,
            vertical_prior_identity=identity,
            vertical_prior_source=prior.source if prior is not None else "unknown",
            cloud_type="unknown",
            base_source="unknown",
            motion_dx_px_per_hour=motion_dx_px_per_hour,
            motion_dy_px_per_hour=motion_dy_px_per_hour,
        )

    sample_points = satellite_sample_points(point, azimuth_deg, radius_km)
    sample_distances = tuple(sorted({float(distance) for _, distance in sample_points}))
    for bearing_deg, distance_km in sample_points:
        target = destination_point(point, bearing_deg, distance_km)
        try:
            prior = vertical_prior_provider(target, frame.valid_time_utc)
            columns = retrieve_cloud_columns(
                frame,
                target,
                distance_km,
                bearing_deg,
                prior,
                motion_dx_px_per_hour=motion_dx_px_per_hour,
                motion_dy_px_per_hour=motion_dy_px_per_hour,
            )
        except (RuntimeError, ValueError, KeyError, TypeError):
            # A missing/mismatched prior is an unknown sample, never a clear
            # pixel and never a fixed-pressure fallback.
            unknown_target = destination_point(point, bearing_deg, distance_km)
            unknown_sets.append(
                SatelliteCloudColumnSet(
                    schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                    valid_time_utc=frame.valid_time_utc,
                    source=frame.source,
                    vertical_prior_identity=unknown_column(
                        unknown_target, distance_km, bearing_deg
                    ).vertical_prior_identity,
                    retrieval_quality="unknown",
                    low_cloud_state="unknown",
                    columns=(unknown_column(unknown_target, distance_km, bearing_deg),),
                    motion_dx_px_per_hour=motion_dx_px_per_hour,
                    motion_dy_px_per_hour=motion_dy_px_per_hour,
                )
            )
            continue
        if not columns:
            unknown_sets.append(
                SatelliteCloudColumnSet(
                    schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                    valid_time_utc=frame.valid_time_utc,
                    source=frame.source,
                    vertical_prior_identity=unknown_column(
                        target, distance_km, bearing_deg, prior
                    ).vertical_prior_identity,
                    retrieval_quality="unknown",
                    low_cloud_state="unknown",
                    columns=(unknown_column(target, distance_km, bearing_deg, prior),),
                    motion_dx_px_per_hour=motion_dx_px_per_hour,
                    motion_dy_px_per_hour=motion_dy_px_per_hour,
                )
            )
            continue
        features.extend(
            cloud_columns_to_features(
                columns,
                prior,
                target,
                footprint=satellite_sample_footprint(
                    distance_km,
                    radius_km,
                    sample_distances,
                ),
            )
        )
        if columns:
            grouped.setdefault(prior.identity, []).extend(columns)
    sets: list[SatelliteCloudColumnSet] = []
    for prior_identity, columns in grouped.items():
        sets.append(
            SatelliteCloudColumnSet(
                schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                valid_time_utc=frame.valid_time_utc,
                source=frame.source,
                vertical_prior_identity=prior_identity,
                retrieval_quality=merge_retrieval_quality(column.retrieval_quality for column in columns),
                low_cloud_state=merge_low_cloud_state(
                    column.low_cloud_state
                    for column in columns
                    if float(column.distance_km) <= 120.0
                ),
                columns=tuple(columns),
                motion_dx_px_per_hour=motion_dx_px_per_hour,
                motion_dy_px_per_hour=motion_dy_px_per_hour,
            )
        )
    return features, sets + unknown_sets


def satellite_sample_points(
    point: GeoPoint,
    azimuth_deg: float,
    radius_km: float,
) -> list[tuple[float, float]]:
    if not math.isfinite(float(radius_km)) or radius_km <= 0.0:
        raise ValueError("satellite sample radius must be positive")
    distances_list = [
        distance for distance in (0.0, 30.0, 70.0, 120.0)
        if distance <= radius_km + 1e-6
    ]
    # The outer fan uses one deliberate 80 km increment.  Construct the
    # sequence from its index so a loop cannot accidentally advance twice.
    outer_count = max(
        0,
        int(math.floor((radius_km - 160.0) / SATELLITE_OUTER_DISTANCE_STEP_KM)) + 1,
    )
    distances_list.extend(
        160.0 + index * SATELLITE_OUTER_DISTANCE_STEP_KM
        for index in range(outer_count)
    )
    if radius_km > distances_list[-1] + 1e-6:
        distances_list.append(float(radius_km))
    distances = tuple(distances_list)
    samples: list[tuple[float, float]] = []
    for distance in distances:
        bearings = (float(azimuth_deg),) if distance == 0.0 else (
            tuple(float(value) for value in range(0, 360, 15))
            if distance <= 120.0
            else tuple((float(azimuth_deg) + offset) % 360.0 for offset in (-42.0, -28.0, -14.0, 0.0, 14.0, 28.0, 42.0))
        )
        samples.extend((bearing, distance) for bearing in bearings)
    return samples


def satellite_sample_footprint(
    distance_km: float,
    radius_km: float,
    sample_distances: Sequence[float],
) -> tuple[float, float, float]:
    """Return a sample's azimuth width and non-overlapping radial bounds.

    ``sample_distances`` is the sorted radial ring set returned by
    :func:`satellite_sample_points`. The interval is a horizontal Voronoi
    footprint, not a cloud-height or optical-depth estimate.
    """
    distances = tuple(float(value) for value in sample_distances)
    distance = float(distance_km)
    radius = float(radius_km)
    if not distances or any(
        not math.isfinite(value)
        or value < 0.0
        for value in (*distances, distance, radius)
    ):
        raise ValueError("satellite sample distances are invalid")
    if radius <= 0.0 or distance not in distances:
        raise ValueError("satellite sample distance is outside the footprint")
    index = distances.index(distance)
    previous = distances[index - 1] if index else 0.0
    following = distances[index + 1] if index + 1 < len(distances) else radius
    near = 0.0 if index == 0 else 0.5 * (previous + distance)
    far = min(
        radius,
        distance if index == len(distances) - 1 else 0.5 * (distance + following),
    )
    width = 15.0 if distance <= 120.0 else 14.0
    return width, near, max(near, far)


def merge_retrieval_quality(values: Sequence[str] | Any) -> str:
    return aggregate_retrieval_quality(values)


def calibrated_sample_coverage_ratio(
    column_sets: Sequence[SatelliteCloudColumnSet],
    *,
    sample_points: Sequence[tuple[float, float]],
) -> float:
    """Return the fraction of scoring sample points with usable columns.

    发布契约（v4）：只要存在可用采样列就发布，覆盖率随结果披露；
    全部不可用（coverage == 0）时调用方 fail-closed。可用 = 反演质量
    非 unknown 且 GFS 垂直先验已命中。列的空间身份是 (bearing_deg,
    distance_km)（lat/lon 恒为观测点本身），与采样点 (bearing,
    distance) 同一空间：方位 ±1° 且距离 ±5 km 内视为同一采样点。
    """
    usable_by_key: dict[tuple[float, float, float, float], bool] = {}
    for column_set in column_sets:
        for column in column_set.columns:
            key = (
                round(float(column.lat), 5),
                round(float(column.lon), 5),
                round(float(column.distance_km), 2),
                round(float(column.bearing_deg), 2),
            )
            usable = (
                column.retrieval_quality != "unknown"
                and column.vertical_prior_identity != "unknown"
                and column.vertical_prior_source.startswith("gfs")
            )
            usable_by_key[key] = usable_by_key.get(key, False) or usable
    if not usable_by_key or not sample_points:
        return 0.0
    columns = [
        (key[3], key[2], usable) for key, usable in usable_by_key.items()
    ]
    usable_samples = 0
    for sample in sample_points:
        s_bearing = float(sample[0])
        s_distance = float(sample[1])
        best = None
        for c_bearing, c_distance, usable in columns:
            if abs(c_bearing - s_bearing) <= 1.0 and abs(c_distance - s_distance) <= 5.0:
                best = usable if best is None else (best or usable)
        usable_samples += int(bool(best)) if best is not None else 0
    return usable_samples / len(sample_points)


def build_satellite_vertical_profile(
    column_sets: Sequence[SatelliteCloudColumnSet],
    features: Sequence[GridCellFeature],
    *,
    expected_sample_count: int,
    valid_time_utc: datetime,
    prior_samples: Optional[
        Sequence[tuple[float, float, Optional[VerticalPrior]]]
    ] = None,
    source_label: str = "Himawari HSD B07/B13/B15 calibrated posterior + same-cycle GFS vertical prior",
    bands: Sequence[str] = ("B07", "B13", "B15"),
) -> dict[str, Any]:
    """Build an auditable calibrated-satellite/GFS pressure-level section.

    The calibrated satellite frame supplies posterior cloud probabilities; GFS supplies the complete
    pressure, height, temperature, humidity and model-cloud-fraction grid.
    Scoring features intentionally omit low-probability levels, so they are
    only joined onto this independent diagnostic grid and never define it.
    """
    feature_by_key: dict[tuple[float, float, float, float, int], GridCellFeature] = {}
    pressure_levels: set[int] = set()
    for feature in features:
        key = (
            round(float(feature.lat), 5),
            round(float(feature.lon), 5),
            round(float(feature.distance_km), 2),
            round(float(feature.bearing_deg), 2),
            int(feature.pressure_hpa),
        )
        feature_by_key[key] = feature
        pressure_levels.add(int(feature.pressure_hpa))

    prior_by_sample_key: dict[tuple[float, float], VerticalPrior] = {}
    if prior_samples is not None:
        for lat, lon, prior in prior_samples:
            if prior is not None:
                prior_by_sample_key[(round(float(lat), 5), round(float(lon), 5))] = prior
                pressure_levels.update(int(level.pressure_hpa) for level in prior.levels)

    columns_by_key: dict[tuple[float, float, float, float], SatelliteCloudColumn] = {}
    for column_set in column_sets:
        for column in column_set.columns:
            key = (
                round(float(column.lat), 5),
                round(float(column.lon), 5),
                round(float(column.distance_km), 2),
                round(float(column.bearing_deg), 2),
            )
            current = columns_by_key.get(key)
            if current is None or RETRIEVAL_QUALITIES_ORDER.get(column.retrieval_quality, 0) > RETRIEVAL_QUALITIES_ORDER.get(current.retrieval_quality, 0):
                columns_by_key[key] = column

    samples: list[dict[str, Any]] = []
    usable_count = 0
    for key, column in sorted(columns_by_key.items(), key=lambda item: (item[1].distance_km, item[1].bearing_deg)):
        lat, lon, distance, bearing = key
        prior = prior_by_sample_key.get((lat, lon))
        usable = (
            column.retrieval_quality != "unknown"
            and (
                (
                    prior is not None
                    and prior.identity != "unknown"
                    and prior.source.startswith("gfs")
                )
                if prior_samples is not None
                else (
                    column.vertical_prior_identity != "unknown"
                    and column.vertical_prior_source.startswith("gfs")
                )
            )
        )
        if usable:
            usable_count += 1
        levels: list[dict[str, Any]] = []
        for pressure_hpa in sorted(pressure_levels, reverse=True):
            feature = feature_by_key.get((*key, pressure_hpa))
            prior_level = (
                next(
                    (
                        level
                        for level in prior.levels
                        if int(level.pressure_hpa) == pressure_hpa
                    ),
                    None,
                )
                if prior is not None
                else None
            )
            hsd_posterior = (
                float(feature.cloud_fraction_percent)
                if feature is not None and usable and feature.cloud_fraction_percent is not None
                else None
            )
            horizontal_probability = (
                float(feature.horizontal_cloud_probability)
                if feature is not None and usable and feature.horizontal_cloud_probability is not None
                else None
            )
            levels.append({
                "pressure_hpa": pressure_hpa,
                "height_m": float(prior_level.height_asl_m) if prior_level is not None else None,
                "rh_percent": float(prior_level.rh_percent) if prior_level is not None and prior_level.rh_percent is not None else None,
                "temperature_c": (
                    float(prior_level.temperature_k - 273.15)
                    if prior_level is not None and prior_level.temperature_k is not None
                    else None
                ),
                "model_cloud_fraction_percent": (
                    float(prior_level.model_cloud_fraction * 100.0)
                    if prior_level is not None and prior_level.model_cloud_fraction is not None
                    else None
                ),
                "model_cloud_fraction": (
                    float(prior_level.model_cloud_fraction)
                    if prior_level is not None and prior_level.model_cloud_fraction is not None
                    else None
                ),
                "hsd_posterior_percent": hsd_posterior,
                "hsd_posterior": (
                    hsd_posterior / 100.0 if hsd_posterior is not None else None
                ),
                # Retain the original name for API consumers written against
                # schema v1; it has always represented the HSD vertical posterior.
                "cloud_probability_percent": hsd_posterior,
                "horizontal_cloud_probability": horizontal_probability,
                "status": "retrieved" if hsd_posterior is not None else "unknown",
            })
        sample_identity = prior.identity if prior is not None else column.vertical_prior_identity
        sample_source = prior.source if prior is not None else column.vertical_prior_source
        samples.append({
            "distance_km": distance,
            "bearing_deg": bearing,
            "status": "retrieved" if usable else "unknown",
            "retrieval_quality": column.retrieval_quality,
            "vertical_prior_identity": sample_identity,
            "vertical_prior_source": sample_source,
            # The same-cycle GFS column carries the model terrain of the sample
            # cell; the cross-section view needs it to keep illumination
            # geometry in ASL coordinates along the path.
            "terrain_elevation_asl_m": (
                float(prior.terrain_asl_m) if prior is not None else None
            ),
            "levels": levels,
        })

    denominator = max(int(expected_sample_count), len(samples), 1)
    coverage = usable_count / denominator
    has_unknown = usable_count < denominator
    return {
        "schema_version": 1,
        "status": "unavailable" if not samples else ("partial_unknown" if has_unknown else "available"),
        "source": source_label,
        "valid_time_utc": valid_time_utc.astimezone(timezone.utc).isoformat(),
        "bands": list(bands),
        "prior_source": "gfs",
        "sample_count": len(samples),
        "usable_sample_count": usable_count,
        "unknown_sample_count": max(0, len(samples) - usable_count),
        "coverage": coverage,
        "numeric_clear_sky_allowed": False,
        "pressure_levels_hpa": sorted(pressure_levels, reverse=True),
        "samples": samples,
    }


_SATELLITE_SECTION_CLOUDY_MIN_AMOUNT = 0.06
_SATELLITE_SECTION_HIGH_MAX_PRESSURE_HPA = 400
_SATELLITE_SECTION_MID_MAX_PRESSURE_HPA = 700
_SATELLITE_SECTION_PATH_TERRAIN_CLEARANCE_M = 25.0


def build_satellite_cross_section(
    profile: Mapping[str, Any],
    *,
    observer_lat: float,
    observer_lon: float,
    observer_elevation_m: float,
    timezone_name: str,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    analysis_radius_km: float,
) -> Optional[dict[str, Any]]:
    """Project the satellite/GFS vertical profile onto the section contract.

    The live retrieval samples the calibrated frame along the solar bearing at
    the same distances the model section plots, so the nowcast page can render
    one real atmospheric section instead of a single simulated column.  The
    satellite posterior supplies per-level cloud evidence and the same-cycle
    GFS prior supplies heights, temperatures and humidity; wind, omega and
    condensate mixing ratios stay ``None`` because the prior does not carry
    them.
    """
    raw_samples = profile.get("samples")
    if not isinstance(raw_samples, list) or not raw_samples:
        return None

    def _bearing_matches(value: Any) -> bool:
        try:
            bearing = float(value)
        except (TypeError, ValueError):
            return False
        return abs(((bearing - float(solar_azimuth_deg)) + 180.0) % 360.0 - 180.0) < 0.5

    along_path = sorted(
        (sample for sample in raw_samples if _bearing_matches(sample.get("bearing_deg"))),
        key=lambda sample: float(sample.get("distance_km") or 0.0),
    )
    if len(along_path) < 2:
        return None
    distances = [float(sample["distance_km"]) for sample in along_path]
    level_keys = sorted(
        {
            int(level["pressure_hpa"])
            for sample in along_path
            for level in (sample.get("levels") or [])
            if isinstance(level, Mapping) and level.get("pressure_hpa") is not None
        }
    )
    if not level_keys:
        return None
    level_count = len(level_keys)

    def _level_value(sample: Mapping[str, Any], pressure: int, key: str) -> Any:
        for level in sample.get("levels") or []:
            if isinstance(level, Mapping) and int(level.get("pressure_hpa") or 0) == pressure:
                return level.get(key)
        return None

    def _matrix(pick) -> list[list[Optional[float]]]:
        matrix: list[list[Optional[float]]] = []
        for sample in along_path:
            row: list[Optional[float]] = []
            for pressure in level_keys:
                raw = pick(sample, pressure)
                if raw is None:
                    row.append(None)
                else:
                    try:
                        value = float(raw)
                    except (TypeError, ValueError):
                        row.append(None)
                    else:
                        row.append(round(value, 4) if math.isfinite(value) else None)
            matrix.append(row)
        return matrix

    height_matrix = _matrix(lambda s, p: _level_value(s, p, "height_m"))
    temperature_matrix = _matrix(lambda s, p: _level_value(s, p, "temperature_c"))
    rh_matrix = _matrix(lambda s, p: _level_value(s, p, "rh_percent"))

    def _amount(sample: Mapping[str, Any], pressure: int) -> Optional[float]:
        posterior = _level_value(sample, pressure, "hsd_posterior")
        if posterior is None:
            return None
        try:
            value = float(posterior)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, value)) if math.isfinite(value) else None

    amount_matrix = _matrix(_amount)
    fraction_matrix: list[list[Optional[float]]] = [
        [None if value is None else round(value * 100.0, 2) for value in row]
        for row in amount_matrix
    ]
    presence_matrix: list[list[Optional[float]]] = [
        [
            None
            if value is None
            else 1.0 if value >= _SATELLITE_SECTION_CLOUDY_MIN_AMOUNT else 0.0
            for value in row
        ]
        for row in amount_matrix
    ]
    family_matrix: list[list[int]] = []
    for _row_index in range(len(distances)):
        family_matrix.append([
            1 if pressure <= _SATELLITE_SECTION_HIGH_MAX_PRESSURE_HPA
            else 2 if pressure <= _SATELLITE_SECTION_MID_MAX_PRESSURE_HPA
            else 3
            for pressure in level_keys
        ])

    terrain_values: list[float] = []
    for distance, sample in zip(distances, along_path):
        raw_terrain = sample.get("terrain_elevation_asl_m")
        try:
            terrain = float(raw_terrain) if raw_terrain is not None else float("nan")
        except (TypeError, ValueError):
            terrain = float("nan")
        if not math.isfinite(terrain):
            if abs(distance) > 1e-6:
                return None
            terrain = float(observer_elevation_m)
        terrain_values.append(terrain)
    if distances and abs(distances[0]) <= 1e-6:
        terrain_values[0] = float(observer_elevation_m)

    required_relative = [
        required_illumination_height_m(distance, float(solar_elevation_deg))
        for distance in distances
    ]
    required_asl = [
        terrain + relative for terrain, relative in zip(terrain_values, required_relative)
    ]

    layer_status: list[list[Optional[int]]] = [
        [None] * level_count for _ in distances
    ]
    layer_base: list[list[Optional[float]]] = [
        [None] * level_count for _ in distances
    ]
    layer_top: list[list[Optional[float]]] = [
        [None] * level_count for _ in distances
    ]
    theoretical_targets: list[dict[str, Any]] = []
    layer_groups = (
        ("high", "高云压力族", 1),
        ("middle", "中云压力族", 2),
        ("low", "低云压力族", 3),
    )
    for distance_index, distance in enumerate(distances):
        sample = along_path[distance_index]
        amount_row = amount_matrix[distance_index]
        height_row = height_matrix[distance_index]
        for layer_name, layer_label, family_code in layer_groups:
            if layer_name == "high":
                member = [
                    index for index, pressure in enumerate(level_keys)
                    if pressure <= _SATELLITE_SECTION_HIGH_MAX_PRESSURE_HPA
                ]
            elif layer_name == "middle":
                member = [
                    index for index, pressure in enumerate(level_keys)
                    if _SATELLITE_SECTION_HIGH_MAX_PRESSURE_HPA < pressure <= _SATELLITE_SECTION_MID_MAX_PRESSURE_HPA
                ]
            else:
                member = [
                    index for index, pressure in enumerate(level_keys)
                    if pressure > _SATELLITE_SECTION_MID_MAX_PRESSURE_HPA
                ]
            cloudy = [
                index for index in member
                if amount_row[index] is not None
                and amount_row[index] >= _SATELLITE_SECTION_CLOUDY_MIN_AMOUNT
                and height_row[index] is not None
            ]
            if not cloudy:
                continue
            base_m = min(float(height_row[index]) for index in cloudy)
            top_m = max(float(height_row[index]) for index in cloudy)
            target_terrain_m = float(terrain_values[distance_index])
            relative_required = float(required_relative[distance_index])
            illuminated = base_m - target_terrain_m >= relative_required
            observer_relative_base_m = base_m - float(observer_elevation_m)
            observer_relative_top_m = top_m - float(observer_elevation_m)
            horizon_margin_m = spherical_horizon_margin_m(
                float(observer_elevation_m),
                base_m,
                float(distance),
            )
            terrain_clearance_m: float = math.inf
            terrain_blocked = False
            if distance_index > 0:
                path_rays = []
                path_terrain = []
                for prior_index in range(1, distance_index + 1):
                    ray_height = spherical_line_of_sight_height_m(
                        float(observer_elevation_m),
                        base_m,
                        float(distance),
                        float(distances[prior_index]),
                    )
                    path_rays.append(ray_height)
                    path_terrain.append(float(terrain_values[prior_index]))
                finite_pairs = [
                    (ray - ground - _SATELLITE_SECTION_PATH_TERRAIN_CLEARANCE_M)
                    for ray, ground in zip(path_rays, path_terrain)
                    if math.isfinite(ray) and math.isfinite(ground)
                ]
                if finite_pairs:
                    terrain_clearance_m = min(finite_pairs)
                    terrain_blocked = terrain_clearance_m <= 0.0
            visible = horizon_margin_m > 0.0 and not terrain_blocked
            if not illuminated:
                status = 1
            elif not visible:
                status = 2
            else:
                status = 3
            for index in cloudy:
                layer_status[distance_index][index] = status
                layer_base[distance_index][index] = round(base_m, 2)
                layer_top[distance_index][index] = round(top_m, 2)
            if status == 3:
                status_reason = "云底已受直射光且位于观测几何地平线以上"
            elif not illuminated:
                status_reason = "云底尚未高出地影照明线"
            elif horizon_margin_m <= 0.0 and terrain_blocked:
                status_reason = "云底已受光，但位于观测几何地平线以下，且回程视线被沿途地形遮挡"
            elif horizon_margin_m <= 0.0:
                status_reason = "云底已受光，但位于观测几何地平线以下"
            elif terrain_blocked:
                status_reason = "云底已受光，但观测者与云底之间的回程视线被沿途地形遮挡"
            else:
                status_reason = "云底已受光，但观测回程不可见"
            theoretical_targets.append({
                "distance_km": float(distance),
                "pressure_family": layer_name,
                "pressure_family_label": layer_label,
                "cloud_phase": {
                    "label": "unknown",
                    "ice_fraction": None,
                    "confidence": 0.0,
                    "source": "satellite-posterior-phase-not-classified",
                },
                "cloud_base_asl_m": base_m,
                "cloud_top_asl_m": top_m,
                "target_terrain_asl_m": target_terrain_m,
                "cloud_base_agl_m": max(base_m - target_terrain_m, 0.0),
                "cloud_top_agl_m": max(top_m - target_terrain_m, 0.0),
                "cloud_base_above_observer_m": observer_relative_base_m,
                "cloud_top_above_observer_m": observer_relative_top_m,
                "geometric_horizon_margin_m": horizon_margin_m,
                "observer_terrain_blocked": terrain_blocked,
                "observer_terrain_clearance_m": (
                    terrain_clearance_m if math.isfinite(terrain_clearance_m) else None
                ),
                "required_base_asl_m": float(required_asl[distance_index]),
                "required_base_agl_m": relative_required,
                "status": (
                    "burnable" if status == 3
                    else "illuminated_not_visible" if status == 2
                    else "not_burnable"
                ),
                "status_reason": status_reason,
            })

    valid_time_utc = profile.get("valid_time_utc")
    try:
        snapshot_valid = datetime.fromisoformat(str(valid_time_utc))
    except (TypeError, ValueError):
        snapshot_valid = None
    if snapshot_valid is not None and snapshot_valid.tzinfo is None:
        snapshot_valid = snapshot_valid.replace(tzinfo=timezone.utc)
    if snapshot_valid is None:
        snapshot_valid = datetime.now(timezone.utc)
    valid_time_local = snapshot_valid.astimezone(ZoneInfo(timezone_name)).isoformat()
    return {
        "model_source": None,
        "terrain_source": str(profile.get("prior_source") or "gfs") + "-vertical-prior-orography",
        "observer": {
            "lat": float(observer_lat),
            "lon": float(observer_lon),
            "elevation_asl_m": float(observer_elevation_m),
        },
        "offset_minutes": 0,
        "valid_time_local": valid_time_local,
        "solar_elevation_deg": float(solar_elevation_deg),
        "solar_azimuth_deg": float(solar_azimuth_deg),
        "spatial_sampling": "satellite-along-path-retrievals",
        "analysis_radius_km": float(analysis_radius_km),
        "distances_km": [float(value) for value in distances],
        "terrain_elevation_asl_m": [
            round(float(value), 2) if math.isfinite(float(value)) else None
            for value in terrain_values
        ],
        "pressure_levels_hpa": [int(value) for value in level_keys],
        "required_illumination_base_asl_m": [
            round(float(value), 2) for value in required_asl
        ],
        "required_illumination_base_agl_m": [
            round(float(value), 2) for value in required_relative
        ],
        "theoretical_targets": theoretical_targets,
        "fields": {
            "geopotential_height_asl_m": height_matrix,
            "temperature_c": temperature_matrix,
            "rh_percent": rh_matrix,
            "cloud_fraction_percent": fraction_matrix,
            "cloud_presence": presence_matrix,
            "cloud_amount": amount_matrix,
            "cloud_liquid_kg_kg": [[None] * level_count for _ in distances],
            "cloud_ice_kg_kg": [[None] * level_count for _ in distances],
            "omega_pa_s": [[None] * level_count for _ in distances],
            "u_wind_ms": [[None] * level_count for _ in distances],
            "v_wind_ms": [[None] * level_count for _ in distances],
            "cloud_layer_status": layer_status,
            "pressure_family_code": family_matrix,
            "cloud_layer_base_asl_m": layer_base,
            "cloud_layer_top_asl_m": layer_top,
        },
    }


def merge_low_cloud_state(values: Sequence[str] | Any) -> str:
    return aggregate_low_cloud_state(values)


def calibrated_cloud_metrics(
    column_sets: Sequence[SatelliteCloudColumnSet],
    azimuth_deg: float,
) -> dict[str, float]:
    """Compute horizontal diagnostics from the complete cloud-column sample set.

    A column contributes one horizontal probability after unioning its vertical
    layers. Clear columns contribute zero, while unknown columns are excluded
    from the usable denominator and remain represented in retrieval coverage.
    This prevents one cloudy pressure level from becoming several copies of the
    same horizontal cloud sample.
    """
    if not column_sets:
        return {
            "near_current": 0.0,
            "far_current": 0.0,
            "near_projected": 0.0,
            "far_projected": 0.0,
            "near_previous": 0.0,
            "far_previous": 0.0,
            "local_cloud_ratio": 0.0,
            "horizon_cloud_ratio": 0.0,
            "thin_cloud_ratio": 0.0,
            "thick_cloud_ratio": 0.0,
            "cloud_texture_score": 0.0,
            "cloud_uniformity_score": 0.0,
            "cloud_blanket_score": 0.0,
            # Unknown is neutral for every scoring input.  It is represented
            # by the separate retrieval quality/state fields, never by a
            # half-clear visibility value that silently becomes a penalty.
            "horizon_clearance_score": 1.0,
            "composition_score": 0.0,
        }
    horizontal: dict[tuple[float, float, float, float], float] = {}
    horizontal_meta: dict[tuple[float, float, float, float], tuple[float, float]] = {}
    for column_set in column_sets:
        for column in column_set.columns:
            if column.retrieval_quality == "unknown":
                continue
            key = (
                round(float(column.lat), 5),
                round(float(column.lon), 5),
                round(float(column.distance_km), 2),
                round(float(column.bearing_deg), 2),
            )
            probability = column.horizontal_cloud_probability
            if probability is None:
                # A detected column without the authoritative horizontal
                # probability is not evidence of clear sky.
                continue
            # Multiple inferred layers from one pixel share this one source
            # probability.  Repeated columns at the same horizontal point are
            # therefore de-duplicated with max, never unioned again.
            probability = clamp(float(probability))
            horizontal[key] = max(horizontal.get(key, 0.0), probability)
            horizontal_meta[key] = (float(column.distance_km), float(column.bearing_deg))
    samples = [(value, *horizontal_meta[key]) for key, value in horizontal.items()]
    values = [value for value, _, _ in samples]
    near = [value for value, distance, _ in samples if distance <= 120.0]
    far = [value for value, distance, _ in samples if distance > 120.0]
    horizon = [
        value
        for value, distance, bearing in samples
        if distance <= 140.0 and angular_distance(bearing, azimuth_deg) <= 35.0
    ]
    local_ratio = sample_mean(values)
    near_ratio = sample_mean(near)
    far_ratio = sample_mean(far)
    horizon_ratio = sample_mean(horizon or near)
    texture = clamp(float(np.std(values)) / 0.28) if values else 0.0
    uniformity = clamp(1.0 - float(np.std(values)) / 0.22) if values else 0.0
    blanket = clamp(local_ratio * uniformity)
    horizon_clearance = clamp(1.0 - horizon_ratio)
    canvas = clamp(1.0 - abs(local_ratio - 0.46) / 0.46)
    composition = clamp(0.30 * canvas + 0.30 * texture + 0.26 * horizon_clearance + 0.14 * (1.0 - blanket) - 0.30 * blanket)
    return {
        "near_current": near_ratio,
        "far_current": far_ratio,
        "near_projected": near_ratio,
        "far_projected": far_ratio,
        "near_previous": near_ratio,
        "far_previous": far_ratio,
        "local_cloud_ratio": local_ratio,
        "horizon_cloud_ratio": horizon_ratio,
        "thin_cloud_ratio": 0.0,
        "thick_cloud_ratio": 0.0,
        "cloud_texture_score": texture,
        "cloud_uniformity_score": uniformity,
        "cloud_blanket_score": blanket,
        "horizon_clearance_score": horizon_clearance,
        "composition_score": composition,
    }


def angular_distance(left: float, right: float) -> float:
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0)


def multispectral_profile_for_point(
    point: GeoPoint,
    cloudiness: float,
    infrared_cloud: float,
    multispectral: Optional[dict[str, object]],
) -> Optional[dict[str, object]]:
    """Build display-only NICT diagnostics; never produces scoring columns."""
    arrays = (multispectral or {}).get("_band_arrays")
    if not isinstance(arrays, dict):
        return None
    b11 = arrays.get("B11")
    b13 = arrays.get("B13")
    b15 = arrays.get("B15")
    if not all(isinstance(array, np.ndarray) for array in (b11, b13, b15)):
        return None
    pixel = nict_pixel_for(point, (b13.shape[1], b13.shape[0]))
    if pixel is None:
        return None
    triplets = nict_patch_triplets(b11, b13, b15, pixel)
    if not triplets:
        return None
    texture = thin_cloud_proxy(infrared_cloud)
    return classify_multispectral_samples(
        triplets,
        {
            "cloud_texture_score": texture,
            "cloud_blanket_score": clamp(cloudiness * (1.0 - 0.55 * texture)),
            "local_cloud_ratio": cloudiness,
            "thick_cloud_ratio": thick_cloud_proxy(infrared_cloud),
        },
    )


def nict_patch_triplets(
    b11: np.ndarray,
    b13: np.ndarray,
    b15: np.ndarray,
    pixel: tuple[int, int],
    radius: int = 2,
) -> list[tuple[float, float, float]]:
    arrays = (b11, b13, b15)
    x, y = pixel
    height, width = arrays[0].shape
    x0, x1 = max(0, x - radius), min(width, x + radius + 1)
    y0, y1 = max(0, y - radius), min(height, y + radius + 1)
    patches = [array[y0:y1, x0:x1].reshape(-1) for array in arrays]
    if min((len(patch) for patch in patches), default=0) == 0:
        return []
    return [
        (float(v11), float(v13), float(v15))
        for v11, v13, v15 in zip(*patches)
    ]


def thin_cloud_proxy(cloudiness: float) -> float:
    if cloudiness <= 0.12 or cloudiness >= 0.88:
        return 0.0
    return clamp(1.0 - abs(cloudiness - 0.50) / 0.38)


def thick_cloud_proxy(cloudiness: float) -> float:
    return clamp((cloudiness - 0.68) / 0.32)


def sample_mean(values: Sequence[float]) -> float:
    return clamp(sum(values) / len(values)) if values else 0.0


def fuse_channel_value(
    infrared_value: float,
    visible_value: Optional[float],
    visible_weight: float,
) -> float:
    """Fuse cloud-presence evidence without treating display B03 as reflectance.

    JMA's B03 JPEG is a display-scaled image.  Its absolute brightness can be
    much lower than the B13 display range even for a plainly visible cloud.
    Infrared therefore remains the cloud-presence baseline.  Visible light can
    corroborate an otherwise weak infrared signal, but cannot erase a cold
    cloud solely because the two display palettes have different levels.
    """
    infrared = clamp(infrared_value)
    if visible_value is None or visible_weight <= 0.0:
        return infrared
    visible = clamp(visible_value)
    visible_excess = max(0.0, visible - infrared)
    # Keep B03 as a bounded corroborating signal until a calibrated
    # reflectance product is available; it must never down-weight B13 cloud.
    return clamp(infrared + 0.45 * clamp(visible_weight) * visible_excess)


def fetch_nict_multispectral_metrics(
    request: ForecastRequest,
    reference_utc: datetime,
    azimuth_deg: float,
    composition: dict[str, float],
    cache_dir: str | Path,
) -> dict[str, object]:
    root = Path(cache_dir) / "nict"
    raw_latest = nict_cached_download(
        NICT_LATEST_URL,
        root / "entries",
        suffix=".json",
        max_age_seconds=120,
    )
    latest_value = json.loads(raw_latest.decode("utf-8"))["date"]
    latest_time = datetime.strptime(latest_value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    reference_floor = reference_utc.replace(
        minute=(reference_utc.minute // 10) * 10,
        second=0,
        microsecond=0,
    )
    valid_time = min(latest_time, reference_floor)
    if abs((reference_utc - valid_time).total_seconds()) > 2.5 * 3600:
        raise RuntimeError("NICT multispectral image is too old")

    stamp = valid_time.strftime("%Y/%m/%d/%H%M%S")
    channels = ("B11", "B13", "B15")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            channel: executor.submit(
                download_nict_band,
                f"{NICT_BASE_URL}/img/FULL_24h/{channel}/1d/550/{stamp}_0_0.png",
                root,
                valid_time,
            )
            for channel in channels
        }
        images = {channel: future.result() for channel, future in futures.items()}
    metrics = multispectral_cloud_metrics(
        images["B11"],
        images["B13"],
        images["B15"],
        request.point,
        azimuth_deg,
        composition,
    )
    band_arrays = {
        channel: np.asarray(image.convert("LA"), dtype=np.float32)[:, :, 1] / 255.0
        for channel, image in images.items()
    }
    return {"valid_time_utc": valid_time, "_band_arrays": band_arrays, **metrics}


def download_nict_band(
    url: str,
    cache_dir: str | Path,
    valid_time_utc: datetime,
) -> Image.Image:
    raw = nict_cached_download(
        url,
        dated_directory(Path(cache_dir), valid_time_utc, "images"),
        suffix=".png",
    )
    return Image.open(BytesIO(raw)).convert("LA")


def multispectral_cloud_metrics(
    b11: Image.Image,
    b13: Image.Image,
    b15: Image.Image,
    point: GeoPoint,
    azimuth_deg: float,
    composition: dict[str, float],
) -> dict[str, float | str]:
    samples = [
        (bearing, distance)
        for distance in (30.0, 70.0, 120.0)
        for bearing in range(0, 360, 30)
    ]
    samples.extend(
        ((azimuth_deg + offset) % 360.0, distance)
        for distance in (50.0, 100.0, 170.0, 240.0, 320.0)
        for offset in (-28.0, -14.0, 0.0, 14.0, 28.0)
    )
    triplets: list[tuple[float, float, float]] = []
    for bearing_deg, distance_km in samples:
        target = destination_point(point, bearing_deg, distance_km)
        pixel = nict_pixel_for(target, b13.size)
        if pixel is None:
            continue
        values = tuple(nict_patch_intensity(image, pixel) for image in (b11, b13, b15))
        if min(values) < 0.0:
            continue
        triplets.append(values)
    return classify_multispectral_samples(triplets, composition)


def classify_multispectral_samples(
    samples: Sequence[tuple[float, float, float]],
    composition: dict[str, float],
) -> dict[str, float | str]:
    if not samples:
        return {
            "spectral_b11_b13_index": 0.0,
            "spectral_b13_b15_index": 0.0,
            "ice_cloud_probability": 0.0,
            "water_cloud_probability": 0.0,
            "thin_cirrus_probability": 0.0,
            "altocumulus_probability": 0.0,
            "altostratus_probability": 0.0,
            "cirrus_probability": 0.0,
            "cloud_type_label": "无法分类",
            "cloud_type_confidence": 0.0,
        }

    array = np.asarray(samples, dtype=np.float32)
    b11_values, b13_values, b15_values = array.T
    cloud_weight = np.clip((b13_values - 0.08) / 0.52, 0.0, 1.0)
    if float(cloud_weight.sum()) < 0.5:
        cloud_weight = np.ones_like(b13_values)
    d11_13 = b11_values - b13_values
    d13_15 = b13_values - b15_values
    index_11_13 = float(np.average(d11_13, weights=cloud_weight))
    index_13_15 = float(np.average(d13_15, weights=cloud_weight))

    # NICT tiles encode the infrared signal in the alpha channel. These are
    # display-space spectral indices, so confidence is capped below direct
    # calibrated-brightness-temperature retrievals.
    phase_index = clamp((index_11_13 - 0.085) / 0.105)
    ice_probability = clamp(0.18 + 0.76 * phase_index)
    water_probability = clamp(1.0 - ice_probability)
    split_window = clamp((index_13_15 - 0.015) / 0.085)
    thin_cirrus = clamp(
        split_window
        * (0.45 + 0.55 * ice_probability)
        * (1.0 - 0.38 * composition.get("thick_cloud_ratio", 0.0))
    )

    texture = composition.get("cloud_texture_score", 0.0)
    blanket = composition.get("cloud_blanket_score", 0.0)
    local_cloud = composition.get("local_cloud_ratio", 0.0)
    altocumulus_raw = water_probability * (0.42 + 0.58 * texture) * (1.0 - 0.62 * blanket)
    altostratus_raw = local_cloud * (0.35 + 0.65 * blanket) * (0.65 + 0.35 * water_probability)
    cirrus_raw = ice_probability * (0.35 + 0.65 * thin_cirrus) * (1.0 - 0.35 * blanket)
    raw = np.asarray((altocumulus_raw, altostratus_raw, cirrus_raw), dtype=np.float64)
    total = float(raw.sum())
    probabilities = raw / total if total > 0.0 else np.asarray((1 / 3, 1 / 3, 1 / 3))
    labels = ("高积云", "高层云", "卷云/卷层云")
    winner = int(np.argmax(probabilities))
    sorted_probabilities = sorted((float(value) for value in probabilities), reverse=True)
    separation = sorted_probabilities[0] - sorted_probabilities[1]
    sample_support = clamp(len(samples) / 45.0)
    confidence = clamp((0.35 + 0.65 * separation) * sample_support * 0.72)
    return {
        "spectral_b11_b13_index": index_11_13,
        "spectral_b13_b15_index": index_13_15,
        "ice_cloud_probability": ice_probability,
        "water_cloud_probability": water_probability,
        "thin_cirrus_probability": thin_cirrus,
        "altocumulus_probability": float(probabilities[0]),
        "altostratus_probability": float(probabilities[1]),
        "cirrus_probability": float(probabilities[2]),
        "cloud_type_label": labels[winner],
        "cloud_type_confidence": confidence,
    }


def nict_pixel_for(point: GeoPoint, size: tuple[int, int]) -> Optional[tuple[int, int]]:
    width, height = size
    relative_lon = ((point.lon + 180.0 - 140.7) % 360.0 - 180.0) / 180.0 * math.pi
    lat_rad = point.lat / 180.0 * math.pi
    visible_limit = math.radians(81.3025)
    if abs(relative_lon) > visible_limit or abs(lat_rad) > visible_limit:
        return None
    radius = width * (1.0 - 0.0045 - 0.0045) / 2.0
    eccentricity_squared = 0.00669438003
    normal = radius / math.sqrt(
        1.0 - eccentricity_squared * math.sin(lat_rad) * math.sin(lat_rad)
    )
    z = radius * 6.613 - normal * math.cos(lat_rad) * math.cos(relative_lon)
    x = normal * math.cos(lat_rad) * math.sin(relative_lon)
    y = normal * (1.0 - eccentricity_squared) * math.sin(lat_rad)
    pixel_x = width / 2.0 * math.atan(x / z) / 0.1535 + width / 2.0
    pixel_y = height / 2.0 - width / 2.0 * math.atan(y / z) / 0.1535
    result = (round(pixel_x), round(pixel_y))
    if not (0 <= result[0] < width and 0 <= result[1] < height):
        return None
    return result


def nict_patch_intensity(image: Image.Image, pixel: tuple[int, int], radius: int = 2) -> float:
    alpha = np.asarray(image.convert("LA"), dtype=np.float32)[:, :, 1] / 255.0
    x, y = pixel
    patch = alpha[
        max(0, y - radius) : min(alpha.shape[0], y + radius + 1),
        max(0, x - radius) : min(alpha.shape[1], x + radius + 1),
    ]
    return float(np.median(patch)) if patch.size else -1.0


def nict_cached_download(
    url: str,
    cache_dir: Path,
    suffix: str,
    max_age_seconds: Optional[int] = None,
) -> bytes:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    target = cache_dir / f"{digest}{suffix}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with lock_path.open("a+b") as lock_handle:
        acquire_file_lock(
            lock_handle,
            timeout_seconds=SATELLITE_CACHE_LOCK_TIMEOUT_SECONDS,
            description="Himawari NICT 缓存锁",
        )
        if cache_file_is_fresh(target, max_age_seconds):
            return target.read_bytes()
        last_error: Optional[Exception] = None
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "firesky/0.1 burning-sunset-engine"},
        )
        for attempt in range(3):
            try:
                payload = urllib.request.urlopen(request, timeout=45).read()
                temporary = target.with_suffix(target.suffix + f".tmp-{os.getpid()}")
                temporary.write_bytes(payload)
                temporary.replace(target)
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (attempt + 1))
        assert last_error is not None
        raise last_error


def list_recent_entries(
    area_code: str,
    channel: str,
    reference_utc: datetime,
    cache_dir: str | Path | None = None,
) -> List[ImageEntry]:
    image_type = "vis" if channel == "b03" else "ir"
    url = f"{BASE_URL}/sat_img.php?area={area_code}&type={image_type}&color={channel}"
    root = Path(cache_dir) if cache_dir is not None else None
    raw = cached_download(
        url,
        dated_directory(root, reference_utc, "entries") if root is not None else None,
        suffix=".html",
        max_age_seconds=ENTRY_CACHE_TTL_SECONDS,
        legacy_cache_dir=root / "entries" if root is not None else None,
    )
    page = raw.decode("utf-8", errors="ignore")
    entries: List[ImageEntry] = []
    for token, label in OPTION_RE.findall(page):
        valid_time = parse_option_time(unescape(label))
        if valid_time is None or valid_time > reference_utc:
            continue
        url = f"{BASE_URL}/img/{area_code}/{area_code}_{channel}_{token}.jpg"
        entries.append(ImageEntry(token=token, valid_time_utc=valid_time, url=url))
    entries.sort(key=lambda item: item.valid_time_utc, reverse=True)
    return entries[:12]


def parse_option_time(label: str) -> Optional[datetime]:
    try:
        return datetime.strptime(label.strip(), "%H:%M UTC %d %B %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def download_image(
    url: str,
    cache_dir: str | Path | None = None,
    valid_time_utc: datetime | None = None,
) -> Image.Image:
    root = Path(cache_dir) if cache_dir is not None else None
    image_time = valid_time_utc or datetime.now(timezone.utc)
    raw = cached_download(
        url,
        dated_directory(root, image_time, "images") if root is not None else None,
        suffix=".jpg",
        legacy_cache_dir=root / "images" if root is not None else None,
    )
    return Image.open(BytesIO(raw)).convert("L")


def cached_download(
    url: str,
    cache_dir: Optional[Path],
    suffix: str,
    max_age_seconds: Optional[int] = None,
    legacy_cache_dir: Optional[Path] = None,
) -> bytes:
    if cache_dir is None:
        return urllib.request.urlopen(url, timeout=30).read()
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    target = cache_dir / f"{digest}{suffix}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    legacy_target = legacy_cache_dir / target.name if legacy_cache_dir is not None else None
    lock_path = target.with_suffix(target.suffix + ".lock")
    with lock_path.open("a+b") as lock_handle:
        acquire_file_lock(
            lock_handle,
            timeout_seconds=SATELLITE_CACHE_LOCK_TIMEOUT_SECONDS,
            description="Himawari 卫星缓存锁",
        )
        if cache_file_is_fresh(target, max_age_seconds):
            return target.read_bytes()
        if legacy_target is not None and cache_file_is_fresh(legacy_target, max_age_seconds):
            return legacy_target.read_bytes()
        payload = urllib.request.urlopen(url, timeout=30).read()
        temporary = target.with_suffix(target.suffix + f".tmp-{os.getpid()}")
        temporary.write_bytes(payload)
        temporary.replace(target)
        return payload


def cache_file_is_fresh(path: Path, max_age_seconds: Optional[int]) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    if max_age_seconds is None:
        return True
    return time.time() - path.stat().st_mtime <= max_age_seconds


def sector_cloud_ratio(image: Image.Image, area: JmaArea, point: GeoPoint, azimuth_deg: float, distances_km: Sequence[float]) -> float:
    offsets = (-28.0, -14.0, 0.0, 14.0, 28.0)
    values: List[float] = []
    for distance_km in distances_km:
        for delta in offsets:
            target = destination_point(point, (azimuth_deg + delta) % 360.0, distance_km)
            pixel = pixel_for(area, target, image.size)
            if pixel is None:
                continue
            values.append(patch_cloudiness(image, pixel))
    if not values:
        return 0.0
    return clamp(sum(values) / len(values))


def pixel_for(area: JmaArea, point: GeoPoint, size: Tuple[int, int]) -> Optional[Tuple[int, int]]:
    if not area.contains(point):
        return None
    width, height = size
    x_ratio = (point.lon - area.west) / (area.east - area.west)
    y_ratio = (area.north - point.lat) / (area.north - area.south)
    x = int(round(x_ratio * (width - 1)))
    y = int(round(y_ratio * (height - 1)))
    if not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def patch_cloudiness(image: Image.Image, pixel: Tuple[int, int], radius: int = 3) -> float:
    x, y = pixel
    width, height = image.size
    x0 = max(0, x - radius)
    y0 = max(0, y - radius)
    x1 = min(width, x + radius + 1)
    y1 = min(height, y + radius + 1)
    values = list(image.crop((x0, y0, x1, y1)).getdata())
    brightness = sum(values) / len(values)
    return cloudiness_from_brightness(brightness)


def cloudiness_from_brightness(brightness: float) -> float:
    if brightness <= 20.0:
        return 0.0
    if brightness >= 170.0:
        return 1.0
    return clamp((brightness - 20.0) / 150.0)


def phase_correlation_motion(
    previous: Image.Image,
    latest: Image.Image,
    max_size: int = 512,
) -> tuple[float, float, float]:
    width, height = latest.size
    scale = min(1.0, max_size / max(width, height))
    sample_size = (max(16, round(width * scale)), max(16, round(height * scale)))
    previous_array = np.asarray(previous.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float32)
    latest_array = np.asarray(latest.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float32)
    margin_y = max(1, sample_size[1] // 12)
    margin_x = max(1, sample_size[0] // 12)
    previous_array = previous_array[margin_y:-margin_y, margin_x:-margin_x]
    latest_array = latest_array[margin_y:-margin_y, margin_x:-margin_x]
    previous_array -= previous_array.mean()
    latest_array -= latest_array.mean()
    if previous_array.std() < 1e-4 or latest_array.std() < 1e-4:
        return 0.0, 0.0, 0.0
    window = np.outer(np.hanning(previous_array.shape[0]), np.hanning(previous_array.shape[1]))
    previous_fft = np.fft.fft2(previous_array * window)
    latest_fft = np.fft.fft2(latest_array * window)
    cross_power = latest_fft * np.conj(previous_fft)
    cross_power /= np.maximum(np.abs(cross_power), 1e-9)
    correlation = np.abs(np.fft.ifft2(cross_power))
    peak_y, peak_x = np.unravel_index(np.argmax(correlation), correlation.shape)
    if peak_x > correlation.shape[1] // 2:
        peak_x -= correlation.shape[1]
    if peak_y > correlation.shape[0] // 2:
        peak_y -= correlation.shape[0]
    aligned = np.roll(previous_array, (peak_y, peak_x), axis=(0, 1))
    quality = float(np.corrcoef(aligned.ravel(), latest_array.ravel())[0, 1])
    if not math.isfinite(quality):
        quality = 0.0
    return peak_x / scale, peak_y / scale, clamp(quality)


def clamp_shift(value: float, dimension: int) -> float:
    return max(-0.25 * dimension, min(0.25 * dimension, value))


def translate_image(image: Image.Image, dx: float, dy: float) -> Image.Image:
    return image.transform(
        image.size,
        Image.Transform.AFFINE,
        (1.0, 0.0, -dx, 0.0, 1.0, -dy),
        resample=Image.Resampling.BILINEAR,
        fillcolor=0,
    )
