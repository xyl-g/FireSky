from __future__ import annotations

import math
from typing import Iterable, List, Optional

from firesky.geometry import angular_difference_deg, required_illumination_height_m
from firesky.models import (
    CloudColumn,
    CrossSectionDiagnosis,
    DomainSnapshot,
    SolarPathProfileLevel,
    SolarPathProfileSample,
)
from firesky.scoring import (
    ColumnIndex,
    build_cloud_columns,
    clamp,
    cloud_opacity_score,
    cloud_type_burnability_score,
    estimated_cloud_base_m,
    lightpath_blocker_opacity,
    MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM,
    sightline_height_at_distance_m,
    solar_ray_height_at_distance_m,
    vertical_ray_intersection_score,
)


PROFILE_DISTANCES_KM = (
    30.0, 55.0, 85.0, 120.0, 170.0, 230.0,
    310.0, 400.0, 520.0, 650.0, 780.0, 900.0,
)

def diagnose_cross_section(
    snapshot: DomainSnapshot,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    columns: Optional[ColumnIndex] = None,
    observer_elevation_m: float = 0.0,
) -> CrossSectionDiagnosis:
    column_index = columns if columns is not None else build_cloud_columns(snapshot)
    profile_columns = [
        column
        for column in column_index.values()
        if column.distance_km <= PROFILE_DISTANCES_KM[-1] + 42.0
    ]
    samples: List[CloudColumn] = []
    for distance_km in PROFILE_DISTANCES_KM:
        column = nearest_column(profile_columns, solar_azimuth_deg, distance_km)
        if column is not None:
            samples.append(column)

    if not samples:
        return CrossSectionDiagnosis(
            azimuth_deg=solar_azimuth_deg,
            samples_count=0,
            lightpath_integrity_score=0.0,
            path_extinction_score=0.0,
            vertical_cloud_type="unknown",
            cloud_type_burnability_score=0.0,
            deep_cloud_risk=0.0,
            blocking_risk=1.0,
            target_cloud_score=0.0,
            summary=["剖面诊断：太阳方向没有足够的模式网格样本。"],
        )

    extinction_score = path_extinction_score(samples)
    target_score = target_cloud_score(
        samples,
        solar_elevation_deg,
    )
    deep_risk = max((column.deep_cloud_risk for column in samples), default=0.0)
    best_column = max(
        samples,
        key=lambda column: target_column_score(
            column,
            solar_elevation_deg,
        ),
    )
    blocking_risk = lightpath_blocking_risk(
        samples,
        solar_elevation_deg,
        best_column,
        observer_elevation_m,
    )
    vertical_type = best_column.cloud_type
    burnability = best_column.burnability_score
    integrity = clamp(1.0 - 0.62 * blocking_risk - 0.28 * deep_risk + 0.16 * target_score)
    summary = [
        f"剖面诊断：太阳方位 {solar_azimuth_deg:.0f}°，样本 {len(samples)} 个，主导云型 {vertical_type}。",
        f"光路完整度 {integrity * 100:.0f}/100，路径通透度 {extinction_score * 100:.0f}/100，深厚云系风险 {deep_risk * 100:.0f}/100。",
        "实况剖面同时保留各距离云柱的云底、云顶、压力层云量代理和太阳入射阈值。",
    ]
    if blocking_risk > 0.55:
        summary.append("太阳方向低层或中低层遮挡云偏强，光路可能被削弱。")
    elif target_score > 0.55:
        summary.append("太阳方向存在可受光中高云目标。")
    if burnability < 0.45:
        summary.append("剖面主导云型可烧性偏低，可能偏厚云或低云。")

    return CrossSectionDiagnosis(
        azimuth_deg=solar_azimuth_deg,
        samples_count=len(samples),
        lightpath_integrity_score=integrity,
        path_extinction_score=extinction_score,
        vertical_cloud_type=vertical_type,
        cloud_type_burnability_score=burnability,
        deep_cloud_risk=deep_risk,
        blocking_risk=blocking_risk,
        target_cloud_score=target_score,
        summary=summary,
        profile=_build_solar_path_profile(
            samples,
            solar_elevation_deg,
            solar_azimuth_deg,
            best_column,
            observer_elevation_m,
        ),
    )


def _build_solar_path_profile(
    samples: List[CloudColumn],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    target_column: Optional[CloudColumn],
    observer_elevation_m: float = 0.0,
) -> List[SolarPathProfileSample]:
    return [
        _profile_sample(
            column,
            solar_elevation_deg,
            solar_azimuth_deg,
            target_column,
            observer_elevation_m,
        )
        for column in samples
    ]


def _profile_sample(
    column: CloudColumn,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    target_column: Optional[CloudColumn],
    observer_elevation_m: float = 0.0,
) -> SolarPathProfileSample:
    sector_delta = angular_difference_deg(column.bearing_deg, solar_azimuth_deg)
    required_height = required_illumination_height_m(
        column.distance_km,
        solar_elevation_deg,
        sector_delta,
    )
    blocker_risk = _profile_blocking_risk(
        column,
        solar_elevation_deg,
        target_column,
        observer_elevation_m,
    )
    if column.cloud_top_m <= 0.0:
        illumination_status = "clear"
    elif column.cloud_top_m < required_height:
        illumination_status = "below_illumination_threshold"
    elif column.cloud_base_m >= required_height:
        illumination_status = "sunlit_cloud"
    else:
        illumination_status = "ray_intersects_cloud"
    levels = [
        SolarPathProfileLevel(
            pressure_hpa=pressure_hpa,
            height_m=feature.geopotential_height_m,
            rh_percent=feature.rh_percent,
            temperature_c=(
                feature.temperature_k - 273.15
                if feature.temperature_k is not None
                else None
            ),
            cloud_fraction_percent=feature.cloud_fraction_percent,
            cloud_liquid_kg_kg=feature.cloud_liquid_kg_kg,
            cloud_ice_kg_kg=feature.cloud_ice_kg_kg,
        )
        for pressure_hpa, feature in sorted(column.layers.items())
    ]
    return SolarPathProfileSample(
        distance_km=column.distance_km,
        bearing_deg=column.bearing_deg,
        cloud_base_m=column.cloud_base_m,
        cloud_top_m=column.cloud_top_m,
        cloud_type=column.cloud_type,
        high_presence=column.high_presence,
        mid_presence=column.mid_presence,
        low_presence=column.low_presence,
        deep_cloud_risk=column.deep_cloud_risk,
        thickness_risk=column.thickness_risk,
        required_illumination_height_m=required_height,
        blocker_risk=blocker_risk,
        lightpath_clearance_score=clamp(1.0 - blocker_risk),
        target_cloud_score=target_column_score(
            column,
            solar_elevation_deg,
        ),
        illumination_status=illumination_status,
        levels=levels,
    )


def _profile_blocking_risk(
    column: CloudColumn,
    solar_elevation_deg: float,
    target_column: Optional[CloudColumn],
    observer_elevation_m: float = 0.0,
) -> float:
    required_height = required_illumination_height_m(
        column.distance_km,
        solar_elevation_deg,
    )
    low_block = column_blocker_opacity(column, "low") * clamp(1.0 - column.distance_km / 160.0)
    mid_block = (
        column_blocker_opacity(column, "mid")
        * clamp((column.distance_km - 25.0) / 160.0)
        * clamp((5200.0 - required_height) / 5200.0)
    )
    deep_block = column.deep_cloud_risk * clamp((column.distance_km - 40.0) / 220.0)
    risk = clamp(0.75 * low_block + 0.30 * mid_block + 0.45 * deep_block)
    if target_column is not None and target_column_score(
        target_column,
        solar_elevation_deg,
    ) > 0.18:
        if column is target_column:
            risk = max(
                risk,
                clamp(
                    0.45 * column_blocker_opacity(column, "low")
                    + 0.30 * column.deep_cloud_risk
                    + 0.18 * column.thickness_risk
                ),
            )
        else:
            risk = max(
                risk,
                column_3d_blocking_risk(
                    column,
                    target_column.distance_km,
                    target_column.cloud_base_m,
                    solar_elevation_deg,
                    observer_elevation_m,
                ),
            )
    return clamp(risk)


def nearest_column(columns: Iterable[CloudColumn], solar_azimuth_deg: float, distance_km: float) -> Optional[CloudColumn]:
    # Geometry identifies the sampled ray.  Cloud amount is deliberately only
    # a final tie-break: a denser lateral or more distant column must not
    # replace the cloud on the requested solar ray.
    candidates: List[tuple[float, float, float, float, CloudColumn]] = []
    for column in columns:
        az_delta = angular_difference_deg(column.bearing_deg, solar_azimuth_deg)
        # Cross-track distance alone cannot distinguish a forward ray from its
        # reciprocal bearing because sin(180 degrees) is also zero.
        if az_delta >= 90.0:
            continue
        distance_delta = abs(column.distance_km - distance_km)
        cross_track_km = column.distance_km * math.sin(math.radians(az_delta))
        corridor_half_width_km = max(18.0, 0.08 * distance_km)
        if abs(cross_track_km) > corridor_half_width_km or distance_delta > 42.0:
            continue
        proximity = clamp(1.0 - distance_delta / 42.0) * clamp(
            1.0 - abs(cross_track_km) / corridor_half_width_km
        )
        cloud_relevance = max(column.high_presence, column.mid_presence, column.low_presence)
        candidates.append((proximity, distance_delta, az_delta, cloud_relevance, column))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (item[0], -item[1], -item[2], item[3]),
        reverse=True,
    )
    return candidates[0][4]


def lightpath_blocking_risk(
    samples: List[CloudColumn],
    solar_elevation_deg: float,
    target_column: Optional[CloudColumn] = None,
    observer_elevation_m: float = 0.0,
) -> float:
    legacy_risk = heuristic_lightpath_blocking_risk(
        samples,
        solar_elevation_deg,
        observer_elevation_m,
    )
    if target_column is not None and target_column_score(
        target_column,
        solar_elevation_deg,
    ) > 0.18:
        target_feature = top_feature(target_column)
        if target_feature is not None:
            target_height_m = target_column.cloud_base_m
            risks = [
                0.45 * column_blocker_opacity(target_column, "low")
                + 0.30 * target_column.deep_cloud_risk
                + 0.18 * target_column.thickness_risk
            ]
            for column in samples:
                if column is target_column:
                    continue
                risks.append(
                    column_3d_blocking_risk(
                        column,
                        target_column.distance_km,
                        target_height_m,
                        solar_elevation_deg,
                        observer_elevation_m,
                    )
                )
            return clamp(max(max(risks, default=0.0), legacy_risk))

    return legacy_risk


def heuristic_lightpath_blocking_risk(
    samples: List[CloudColumn],
    solar_elevation_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    risks: List[float] = []
    for column in samples:
        required_height = required_illumination_height_m(
            column.distance_km,
            solar_elevation_deg,
        )
        low_block = column_blocker_opacity(column, "low") * clamp(1.0 - column.distance_km / 160.0)
        mid_block = column_blocker_opacity(column, "mid") * clamp((column.distance_km - 25.0) / 160.0) * clamp((5200.0 - required_height) / 5200.0)
        deep_block = column.deep_cloud_risk * clamp((column.distance_km - 40.0) / 220.0)
        risks.append(clamp(0.75 * low_block + 0.30 * mid_block + 0.45 * deep_block))
    return max(risks, default=0.0)


def top_feature(column: CloudColumn):
    if not column.layers:
        return None
    return min(column.layers.values(), key=lambda feature: feature.pressure_hpa)


def column_blocker_opacity(column: CloudColumn, layer: str) -> float:
    opacities = [
        lightpath_blocker_opacity(feature)
        for feature in column.layers.values()
        if (
            layer == "low" and feature.pressure_hpa >= 700
            or layer == "mid" and 500 <= feature.pressure_hpa <= 700
        )
    ]
    return max(opacities, default=0.0)


def column_3d_blocking_risk(
    column: CloudColumn,
    target_distance_km: float,
    target_height_m: float,
    solar_elevation_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    if abs(column.distance_km - target_distance_km) < 8.0:
        return 0.0
    if column.distance_km < target_distance_km:
        ray_height = sightline_height_at_distance_m(
            target_distance_km,
            target_height_m,
            column.distance_km,
            observer_elevation_m,
        )
        distance_factor = clamp(1.0 - column.distance_km / max(target_distance_km, 1.0))
    else:
        ray_height = solar_ray_height_at_distance_m(
            target_distance_km,
            target_height_m,
            column.distance_km,
            solar_elevation_deg,
        )
        distance_factor = clamp(1.0 - (column.distance_km - target_distance_km - 20.0) / (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - 20.0))

    layer_risks: List[float] = []
    for feature in column.layers.values():
        opacity = lightpath_blocker_opacity(feature)
        if opacity <= 0.0:
            continue
        base_m = estimated_cloud_base_m(feature)
        intersection = vertical_ray_intersection_score(ray_height, base_m, feature.geopotential_height_m, tolerance_m=850.0)
        if intersection <= 0.0:
            continue
        layer_risks.append(opacity * intersection * (0.50 + 0.50 * distance_factor))
    deep_risk = column.deep_cloud_risk * (0.35 + 0.35 * distance_factor)
    return clamp(max(layer_risks, default=0.0) + deep_risk)


def path_extinction_score(samples: List[CloudColumn]) -> float:
    low_extinction = sum(column_blocker_opacity(column, "low") * max(0.0, 1.0 - column.distance_km / 240.0) for column in samples) / max(1, len(samples))
    mid_extinction = sum(column_blocker_opacity(column, "mid") * 0.45 for column in samples) / max(1, len(samples))
    cloud_extinction = clamp(low_extinction + mid_extinction)
    return clamp(1.0 - 0.42 * cloud_extinction)


def target_cloud_score(
    samples: List[CloudColumn],
    solar_elevation_deg: float,
) -> float:
    return max(
        (
            target_column_score(
                column,
                solar_elevation_deg,
            )
            for column in samples
        ),
        default=0.0,
    )


def target_column_score(
    column: CloudColumn,
    solar_elevation_deg: float,
) -> float:
    high_mid = max(column.high_presence, 0.82 * column.mid_presence)
    distance_score = clamp(1.0 - abs(column.distance_km - 170.0) / 210.0)
    required_height = required_illumination_height_m(
        column.distance_km,
        solar_elevation_deg,
    )
    high_enough = clamp((column.cloud_base_m - required_height + 1200.0) / 4200.0)
    return clamp(high_mid * column.burnability_score * distance_score * high_enough * (1.0 - 0.55 * column.deep_cloud_risk))
