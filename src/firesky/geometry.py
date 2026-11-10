from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, List, Tuple
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import azimuth, elevation, sun

from firesky.models import GeoPoint, GlowGeometryWindow, SolarSnapshot
from firesky import rust_astronomy

EARTH_RADIUS_KM = 6371.0
# Mean apparent solar semi-diameter.  The refraction term is evaluated at the
# current geometric solar-centre elevation instead of treating the standard
# sunrise/sunset ``0.833°`` horizon convention as constant throughout twilight.
SOLAR_SEMI_DIAMETER_DEG = 0.2666667


def _smoothstep(value: float, low: float, high: float) -> float:
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    t = (value - low) / (high - low)
    return t * t * (3.0 - 2.0 * t)


def cloud_edge_max_distance_km(
    cloud_base_m: float,
    observer_elevation_m: float = 0.0,
) -> float:
    """Maximum observer-to-edge distance before the cloud base meets Earth.

    ``edge_distance_km`` in the scoring callers is measured from the observer
    to the sunward clear edge.  That is a one-sided horizon distance.  A local
    deck's full observer-side-to-sunward span is exposed separately by
    :func:`cloud_deck_span_max_distance_km`; it must not be used as an edge
    distance or a cloud beyond the geometric horizon will be accepted.
    """
    effective_height_km = max(0.0, cloud_base_m - observer_elevation_m) / 1000.0
    if effective_height_km <= 0.0:
        return 0.0
    return math.sqrt(2.0 * EARTH_RADIUS_KM * effective_height_km)


def cloud_deck_span_max_distance_km(
    cloud_base_m: float,
    observer_elevation_m: float = 0.0,
) -> float:
    """Maximum two-sided span of a local deck at ``cloud_base_m``.

    This is the sum of the observer-side and sunward horizon legs.  It is a
    geometry diagnostic only; edge distances passed to scoring remain
    one-sided and use :func:`cloud_edge_max_distance_km`.
    """
    return 2.0 * cloud_edge_max_distance_km(cloud_base_m, observer_elevation_m)


def cloud_edge_reach_km(solar_elevation_deg: float) -> float:
    """Same-height cloud-base reach at the current effective solar elevation."""
    effective_elevation_rad = math.radians(
        solar_illumination_elevation_deg(solar_elevation_deg)
    )
    if effective_elevation_rad >= 0.0:
        return 0.0
    return -2.0 * EARTH_RADIUS_KM * math.tan(effective_elevation_rad)


def cloud_edge_distance_quality(
    edge_distance_km: float,
    cloud_base_m: float,
    observer_elevation_m: float = 0.0,
) -> float:
    """Prefer a visible deck edge around 35-70% of its geometric maximum."""
    maximum = cloud_edge_max_distance_km(cloud_base_m, observer_elevation_m)
    if edge_distance_km <= 0.0 or maximum <= 0.0:
        return 0.0
    ratio = edge_distance_km / maximum
    near_quality = 0.18 + 0.82 * _smoothstep(ratio, 0.12, 0.38)
    far_quality = 1.0 - _smoothstep(ratio, 0.72, 1.0)
    return max(0.0, min(1.0, near_quality * far_quality))


def cloud_edge_geometry_score(
    edge_distance_km: float,
    cloud_base_m: float,
    solar_elevation_deg: float,
    observer_elevation_m: float = 0.0,
) -> float:
    """Combine cloud-base-dependent distance quality with current sun reach."""
    distance_quality = cloud_edge_distance_quality(
        edge_distance_km,
        cloud_base_m,
        observer_elevation_m,
    )
    if distance_quality <= 0.0:
        return 0.0
    if solar_illumination_elevation_deg(solar_elevation_deg) >= 0.0:
        return distance_quality
    reach = cloud_edge_reach_km(solar_elevation_deg)
    reach_gate = _smoothstep(reach / max(edge_distance_km, 1.0), 0.85, 1.05)
    return distance_quality * reach_gate


def _solar_elevation_deg(point: GeoPoint, when_utc: datetime) -> float:
    if rust_astronomy.enabled():
        return rust_astronomy.position(point, when_utc)[0]
    # Scoring uses the geometric solar-centre elevation.  Atmospheric
    # refraction and the solar-disc radius are applied explicitly by the
    # light-path geometry (the same convention as the regional vector path).
    return float(
        elevation(
            observer_for(point),
            when_utc.astimezone(timezone.utc),
            with_refraction=False,
        )
    )


def solar_elevation_crossing_utc(
    point: GeoPoint,
    anchor_utc: datetime,
    target_elevation_deg: float,
    phenomenon: str,
    search_minutes: float = 90.0,
) -> datetime | None:
    """Find the dawn/dusk crossing of a solar-elevation threshold."""
    if rust_astronomy.enabled():
        return rust_astronomy.crossing(point, anchor_utc, target_elevation_deg, phenomenon, search_minutes)
    if phenomenon not in {"dawn", "dusk"}:
        raise ValueError("phenomenon must be dawn or dusk")
    anchor = anchor_utc.astimezone(timezone.utc)
    start = anchor - timedelta(minutes=search_minutes)
    end = anchor + timedelta(minutes=search_minutes)
    start_value = _solar_elevation_deg(point, start)
    end_value = _solar_elevation_deg(point, end)
    lower, upper = sorted((start_value, end_value))
    if not lower <= target_elevation_deg <= upper:
        return None

    for _ in range(42):
        middle = start + (end - start) / 2
        value = _solar_elevation_deg(point, middle)
        if phenomenon == "dawn":
            if value < target_elevation_deg:
                start = middle
            else:
                end = middle
        elif value > target_elevation_deg:
            start = middle
        else:
            end = middle
    return start + (end - start) / 2


def solar_shadow_speed_km_per_min(point: GeoPoint, when_utc: datetime) -> float:
    """Speed of the tangent-light parabola vertex from the actual solar motion."""
    if rust_astronomy.enabled():
        return rust_astronomy.shadow_speed(point, when_utc)
    half_interval = timedelta(seconds=30)
    before = _solar_elevation_deg(point, when_utc - half_interval)
    after = _solar_elevation_deg(point, when_utc + half_interval)
    elevation_rate_deg_per_min = after - before
    return EARTH_RADIUS_KM * math.radians(abs(elevation_rate_deg_per_min))


def typical_deck_glow_window(
    point: GeoPoint,
    anchor_utc: datetime,
    timezone_name: str,
    phenomenon: str,
    edge_distance_km: float,
    cloud_base_m: float,
    observer_elevation_m: float | None = None,
) -> GlowGeometryWindow | None:
    """Solve the tutorial's canonical flat-deck glow window from geometry.

    The first threshold is when light through the sunward deck edge reaches the
    observer column. The second is when the local cloud base enters or exits the
    Earth's shadow. Dawn traverses the same interval in reverse.
    """
    if rust_astronomy.enabled():
        return rust_astronomy.glow_window(
            point, anchor_utc, timezone_name, phenomenon, kind="typical",
            edge_distance_km=edge_distance_km, cloud_base_m=cloud_base_m,
            observer_elevation_m=observer_elevation_m,
        )
    observer_elevation = point.elevation_m if observer_elevation_m is None else observer_elevation_m
    relative_base_m = cloud_base_m - observer_elevation
    maximum_distance = cloud_edge_max_distance_km(cloud_base_m, observer_elevation)
    if relative_base_m <= 0.0 or edge_distance_km <= 0.0 or edge_distance_km > maximum_distance:
        return None

    edge_depression = math.atan(edge_distance_km / (2.0 * EARTH_RADIUS_KM))
    base_height_km = relative_base_m / 1000.0
    shadow_depression = math.acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + base_height_km))
    edge_elevation = solar_geometric_elevation_for_illumination_deg(
        -math.degrees(edge_depression)
    )
    shadow_elevation = solar_geometric_elevation_for_illumination_deg(
        -math.degrees(shadow_depression)
    )
    edge_crossing = solar_elevation_crossing_utc(point, anchor_utc, edge_elevation, phenomenon)
    shadow_crossing = solar_elevation_crossing_utc(point, anchor_utc, shadow_elevation, phenomenon)
    if edge_crossing is None or shadow_crossing is None:
        return None

    start_utc, end_utc = sorted((edge_crossing, shadow_crossing))
    if end_utc <= start_utc:
        return None
    midpoint_utc = start_utc + (end_utc - start_utc) / 2
    tzinfo = ZoneInfo(timezone_name)
    return GlowGeometryWindow(
        regime="typical_local_deck",
        start_time_utc=start_utc,
        start_time_local=start_utc.astimezone(tzinfo),
        midpoint_time_utc=midpoint_utc,
        midpoint_time_local=midpoint_utc.astimezone(tzinfo),
        end_time_utc=end_utc,
        end_time_local=end_utc.astimezone(tzinfo),
        duration_minutes=(end_utc - start_utc).total_seconds() / 60.0,
        shadow_speed_km_per_min=solar_shadow_speed_km_per_min(point, midpoint_utc),
        edge_distance_km=edge_distance_km,
        cloud_base_m=cloud_base_m,
        geometric_max_distance_km=maximum_distance,
    )


def target_cloud_glow_window(
    point: GeoPoint,
    anchor_utc: datetime,
    timezone_name: str,
    phenomenon: str,
    target_distance_km: float,
    cloud_base_m: float,
    cloud_top_m: float,
    regime: str,
) -> GlowGeometryWindow | None:
    """Solve the Earth-shadow interval across a remote target cloud's depth."""
    if rust_astronomy.enabled():
        return rust_astronomy.glow_window(
            point, anchor_utc, timezone_name, phenomenon, kind="target",
            target_distance_km=target_distance_km, cloud_base_m=cloud_base_m,
            cloud_top_m=cloud_top_m, regime=regime,
        )
    relative_base_km = max(0.0, cloud_base_m - point.elevation_m) / 1000.0
    relative_top_km = max(relative_base_km, cloud_top_m - point.elevation_m) / 1000.0
    if relative_top_km <= 0.0 or target_distance_km <= 0.0:
        return None
    observer_horizon = math.sqrt(2.0 * EARTH_RADIUS_KM * max(0.0, point.elevation_m) / 1000.0)
    target_horizon = math.sqrt(2.0 * EARTH_RADIUS_KM * relative_top_km)
    maximum_distance = observer_horizon + target_horizon
    if target_distance_km > maximum_distance * 1.08:
        return None

    def shadow_elevation(height_km: float) -> float:
        return solar_geometric_elevation_for_illumination_deg(
            -math.degrees(
                math.acos(
                    EARTH_RADIUS_KM
                    / (EARTH_RADIUS_KM + max(0.001, height_km))
                )
            )
        )

    base_crossing = solar_elevation_crossing_utc(
        point, anchor_utc, shadow_elevation(max(0.001, relative_base_km)), phenomenon
    )
    top_crossing = solar_elevation_crossing_utc(
        point, anchor_utc, shadow_elevation(relative_top_km), phenomenon
    )
    if base_crossing is None or top_crossing is None:
        return None
    start_utc, end_utc = sorted((base_crossing, top_crossing))
    if end_utc <= start_utc:
        return None
    midpoint_utc = start_utc + (end_utc - start_utc) / 2
    tzinfo = ZoneInfo(timezone_name)
    return GlowGeometryWindow(
        regime=regime,
        start_time_utc=start_utc,
        start_time_local=start_utc.astimezone(tzinfo),
        midpoint_time_utc=midpoint_utc,
        midpoint_time_local=midpoint_utc.astimezone(tzinfo),
        end_time_utc=end_utc,
        end_time_local=end_utc.astimezone(tzinfo),
        duration_minutes=(end_utc - start_utc).total_seconds() / 60.0,
        shadow_speed_km_per_min=solar_shadow_speed_km_per_min(point, midpoint_utc),
        edge_distance_km=target_distance_km,
        cloud_base_m=cloud_base_m,
        geometric_max_distance_km=maximum_distance,
    )


def normalize_lon(lon: float) -> float:
    value = ((lon + 180.0) % 360.0) - 180.0
    if value == -180.0 and lon > 0:
        return 180.0
    return value


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    lat1 = math.radians(a.lat)
    lon1 = math.radians(a.lon)
    lat2 = math.radians(b.lat)
    lon2 = math.radians(b.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    sin_dlat = math.sin(dlat / 2.0)
    sin_dlon = math.sin(dlon / 2.0)
    h = sin_dlat**2 + math.cos(lat1) * math.cos(lat2) * sin_dlon**2
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def initial_bearing_deg(a: GeoPoint, b: GeoPoint) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def destination_point(origin: GeoPoint, bearing_deg: float, distance_km: float) -> GeoPoint:
    angular_distance = distance_km / EARTH_RADIUS_KM
    bearing_rad = math.radians(bearing_deg)
    lat1 = math.radians(origin.lat)
    lon1 = math.radians(origin.lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing_rad)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing_rad) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return GeoPoint(lat=math.degrees(lat2), lon=normalize_lon(math.degrees(lon2)), elevation_m=origin.elevation_m)


def angular_difference_deg(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def bounding_box(point: GeoPoint, radius_km: float) -> Tuple[float, float, float, float]:
    lat_pad = radius_km / 111.0
    lon_pad = radius_km / max(25.0, 111.0 * math.cos(math.radians(point.lat)))
    return (
        normalize_lon(point.lon - lon_pad),
        normalize_lon(point.lon + lon_pad),
        min(90.0, point.lat + lat_pad),
        max(-90.0, point.lat - lat_pad),
    )


def required_illumination_height_m(
    distance_km: float,
    solar_elevation_deg: float,
    sector_delta_deg: float = 0.0,
    observer_elevation_m: float = 0.0,
) -> float:
    directional_distance_km = max(0.0, distance_km) * math.cos(math.radians(sector_delta_deg))
    local_elevation_rad = math.radians(
        solar_illumination_elevation_deg(solar_elevation_deg)
    ) + directional_distance_km / EARTH_RADIUS_KM
    if local_elevation_rad >= 0.0:
        return observer_elevation_m
    depression_rad = min(math.radians(20.0), -local_elevation_rad)
    relative_height_m = EARTH_RADIUS_KM * 1000.0 * (
        1.0 / math.cos(depression_rad) - 1.0
    )
    return observer_elevation_m + relative_height_m


def spherical_horizon_margin_m(
    observer_elevation_m: float,
    target_elevation_m: float,
    distance_km: float,
) -> float:
    """Signed target height above the geometric horizon on a spherical Earth.

    Heights are mean-sea-level elevations.  The old parabolic shortcut used
    ``target - observer - d²/(2R)`` and therefore silently treated an elevated
    observer as if it were at sea level.  Solve the tangent line from the
    observer's radius instead; a positive result means the target cloud base
    clears the spherical horizon, before local terrain is considered.
    """
    radius_m = EARTH_RADIUS_KM * 1000.0
    observer_radius_m = radius_m + float(observer_elevation_m)
    target_radius_m = radius_m + float(target_elevation_m)
    if distance_km <= 1e-6:
        return float(target_elevation_m) - float(observer_elevation_m)

    # A below-sea-level station is still outside the local terrain in the
    # model, but the mean spherical horizon cannot be below the sea-level
    # reference surface.  Keep the horizon convention well-defined there.
    horizon_radius_m = max(radius_m, observer_radius_m)
    horizon_depression_rad = -math.acos(
        min(1.0, radius_m / horizon_radius_m)
    )
    central_angle_rad = float(distance_km) / EARTH_RADIUS_KM
    denominator = (
        math.cos(central_angle_rad)
        - math.tan(horizon_depression_rad) * math.sin(central_angle_rad)
    )
    if denominator <= 1e-9:
        return float("-inf")
    required_target_radius_m = horizon_radius_m / denominator
    return target_radius_m - required_target_radius_m


def spherical_line_of_sight_height_m(
    observer_elevation_m: float,
    target_elevation_m: float,
    target_distance_km: float,
    sample_distance_km: float,
) -> float:
    """Return the MSL height of a straight observer-to-target chord.

    ``sample_distance_km`` is measured along the great-circle azimuth used by
    the profile.  This keeps terrain occlusion on the same spherical geometry
    as :func:`spherical_horizon_margin_m`, including high-altitude stations.
    """
    target_distance = max(0.0, float(target_distance_km))
    sample_distance = min(max(0.0, float(sample_distance_km)), target_distance)
    if target_distance <= 1e-6 or sample_distance <= 1e-6:
        return float(observer_elevation_m)
    if sample_distance >= target_distance - 1e-6:
        return float(target_elevation_m)

    radius_m = EARTH_RADIUS_KM * 1000.0
    observer_radius_m = radius_m + float(observer_elevation_m)
    target_radius_m = radius_m + float(target_elevation_m)
    target_angle_rad = target_distance / EARTH_RADIUS_KM
    sample_angle_rad = sample_distance / EARTH_RADIUS_KM
    sin_target = math.sin(target_angle_rad)
    cos_target = math.cos(target_angle_rad)
    tangent_sample = math.tan(sample_angle_rad)
    denominator = target_radius_m * sin_target - tangent_sample * (
        target_radius_m * cos_target - observer_radius_m
    )
    if abs(denominator) <= 1e-9:
        # The operational profile never approaches this singular geometry, but
        # retain a stable fallback for synthetic or future wide-angle inputs.
        fraction = sample_distance / target_distance
        return float(observer_elevation_m) + fraction * (
            float(target_elevation_m) - float(observer_elevation_m)
        )
    fraction = tangent_sample * observer_radius_m / denominator
    fraction = min(1.0, max(0.0, fraction))
    x = observer_radius_m + fraction * (target_radius_m * cos_target - observer_radius_m)
    y = fraction * target_radius_m * sin_target
    return math.hypot(x, y) - radius_m


def solar_refraction_deg(geometric_elevation_deg: float) -> float:
    """Return standard-atmosphere refraction for a geometric solar height.

    This is the same piecewise low-altitude approximation used by Astral for
    the apparent solar position.  It is only a clear-standard-atmosphere
    correction; local pressure, temperature and haze are intentionally not
    invented here.  The caller adds the solar semi-diameter separately.
    """
    elevation_deg = float(geometric_elevation_deg)
    if not math.isfinite(elevation_deg) or elevation_deg >= 85.0:
        return 0.0
    tangent = math.tan(math.radians(elevation_deg))
    if abs(tangent) < 1e-12:
        return 1735.0 / 3600.0
    if elevation_deg > 5.0:
        arc_seconds = (
            58.1 / tangent
            - 0.07 / tangent**3
            + 0.000086 / tangent**5
        )
    elif elevation_deg > -0.575:
        step1 = -12.79 + elevation_deg * 0.711
        step2 = 103.4 + elevation_deg * step1
        step3 = -518.2 + elevation_deg * step2
        arc_seconds = 1735.0 + elevation_deg * step3
    else:
        arc_seconds = -20.774 / tangent
    return arc_seconds / 3600.0


def solar_illumination_elevation_deg(geometric_elevation_deg: float) -> float:
    """Effective upper-limb elevation for direct cloud illumination.

    ``0.833°`` is valid as a horizon event convention (refraction at the
    horizon plus the solar radius), but not as a constant at -5° or -10°.
    Use the instantaneous standard refraction and a fixed mean solar radius.
    """
    return float(geometric_elevation_deg) + solar_refraction_deg(
        geometric_elevation_deg
    ) + SOLAR_SEMI_DIAMETER_DEG


def solar_geometric_elevation_for_illumination_deg(
    effective_elevation_deg: float,
) -> float:
    """Invert :func:`solar_illumination_elevation_deg` by monotonic search."""
    target = float(effective_elevation_deg)
    if not math.isfinite(target):
        raise ValueError("effective solar elevation must be finite")
    lower = -90.0
    upper = 90.0
    for _ in range(48):
        middle = 0.5 * (lower + upper)
        if solar_illumination_elevation_deg(middle) < target:
            lower = middle
        else:
            upper = middle
    return 0.5 * (lower + upper)


def observer_for(point: GeoPoint) -> Observer:
    return Observer(latitude=point.lat, longitude=point.lon, elevation=point.elevation_m)


def solar_event_time_local(target_date: date, point: GeoPoint, timezone_name: str, phenomenon: str) -> datetime:
    if rust_astronomy.enabled():
        return rust_astronomy.event(target_date, point, timezone_name, phenomenon)
    tzinfo = ZoneInfo(timezone_name)
    events = sun(observer_for(point), date=target_date, tzinfo=tzinfo)
    key = "sunrise" if phenomenon == "dawn" else "sunset"
    return events[key]


def solar_snapshot(point: GeoPoint, when_utc: datetime, timezone_name: str) -> SolarSnapshot:
    tzinfo = ZoneInfo(timezone_name)
    when_local = when_utc.astimezone(tzinfo)
    if rust_astronomy.enabled():
        geometric_elevation, solar_azimuth = rust_astronomy.position(point, when_utc)
        return SolarSnapshot(
            valid_time_utc=when_utc, valid_time_local=when_local,
            elevation_deg=geometric_elevation, azimuth_deg=solar_azimuth,
        )
    obs = observer_for(point)
    return SolarSnapshot(
        valid_time_utc=when_utc,
        valid_time_local=when_local,
        # Keep point and regional scoring on one geometric solar-centre
        # convention.  The path equations apply the instantaneous standard
        # refraction and solar-limb radius; using Astral's apparent elevation
        # here would count that correction twice.
        elevation_deg=float(elevation(obs, when_local, with_refraction=False)),
        azimuth_deg=float(azimuth(obs, when_local)),
    )


def nearest_hour_offsets(init_utc: datetime, times_utc: Iterable[datetime]) -> List[int]:
    if rust_astronomy.enabled():
        return rust_astronomy.time_operation(
            "nearest_hour_offsets", init=rust_astronomy._time(init_utc),
            times=[rust_astronomy._time(value) for value in times_utc],
        )["values"]
    offsets = set()
    for value in times_utc:
        hour = round((value - init_utc).total_seconds() / 3600.0)
        if hour >= 0:
            offsets.add(int(hour))
    return sorted(offsets)


def choose_cycle(reference_utc: datetime, cycle_hours: Tuple[int, ...], availability_lag_hours: int) -> datetime:
    if reference_utc.tzinfo is None:
        raise ValueError("reference_utc must be timezone-aware")
    if rust_astronomy.enabled():
        return datetime.fromisoformat(rust_astronomy.time_operation(
            "choose_cycle", reference=rust_astronomy._time(reference_utc),
            cycle_hours=list(cycle_hours), availability_lag_hours=availability_lag_hours,
        )["value"])

    adjusted = reference_utc.astimezone(timezone.utc) - timedelta(hours=availability_lag_hours)
    if cycle_hours and adjusted.hour < cycle_hours[0]:
        adjusted = adjusted - timedelta(days=1)
        cycle_hour = cycle_hours[-1]
    else:
        cycle_hour = max(hour for hour in cycle_hours if hour <= adjusted.hour)
    return adjusted.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
