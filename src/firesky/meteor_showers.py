from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from astral import moon

from firesky.geometry import solar_snapshot
from firesky.models import GeoPoint


METEOR_SCHEMA_VERSION = "meteor-showers-v1"
METEOR_CATALOG_VERSION = "imo-calendar-2026"
IMO_CALENDAR_URL = "https://www.imo.net/files/meteor-shower/cal2026.pdf"


@dataclass(frozen=True)
class MeteorShower:
    code: str
    name: str
    name_zh: str
    active_start: str
    active_end: str
    peak: str
    peak_hour_utc: Optional[float]
    radiant_ra_deg: float
    radiant_dec_deg: float
    speed_km_s: float
    zhr: Optional[float]


# IMO 2026 Meteor Shower Calendar, Table 5. The list focuses on showers with
# ZHR >= 5 plus the major visually observed annual showers used in planning.
METEOR_SHOWERS_2026: tuple[MeteorShower, ...] = (
    MeteorShower("QUA", "Quadrantids", "象限仪座流星雨", "12-28", "01-12", "01-03", 21.0, 230, 49, 41, 80),
    MeteorShower("ACE", "alpha-Centaurids", "半人马座阿尔法流星雨", "01-31", "02-20", "02-08", None, 211, -58, 58, 6),
    MeteorShower("LYR", "April Lyrids", "天琴座流星雨", "04-14", "04-30", "04-22", 19.67, 271, 34, 49, 18),
    MeteorShower("PPU", "pi-Puppids", "船尾座π流星雨", "04-15", "04-28", "04-24", None, 110, -45, 18, None),
    MeteorShower("ETA", "eta-Aquariids", "宝瓶座η流星雨", "04-19", "05-28", "05-06", None, 338, -1, 66, 50),
    MeteorShower("ARI", "Daytime Arietids", "白昼白羊座流星雨", "05-14", "06-24", "06-07", None, 43, 24, 38, 30),
    MeteorShower("JBO", "June Bootids", "六月牧夫座流星雨", "06-22", "07-02", "06-22", None, 221, 48, 18, None),
    MeteorShower("GDR", "July gamma-Draconids", "七月天龙座γ流星雨", "07-25", "07-31", "07-28", 13.0, 280, 51, 27, 5),
    MeteorShower("SDA", "Southern delta-Aquariids", "宝瓶座δ南流星雨", "07-12", "08-23", "07-31", None, 340, -16, 41, 25),
    MeteorShower("CAP", "alpha-Capricornids", "摩羯座α流星雨", "07-03", "08-15", "07-31", None, 307, -10, 23, 5),
    MeteorShower("PER", "Perseids", "英仙座流星雨", "07-17", "08-24", "08-13", 3.0, 48, 58, 59, 100),
    MeteorShower("AUR", "Aurigids", "御夫座流星雨", "08-28", "09-05", "09-01", None, 91, 39, 66, 6),
    MeteorShower("SPE", "September epsilon-Perseids", "九月英仙座ε流星雨", "09-05", "09-21", "09-09", 18.0, 48, 40, 64, 8),
    MeteorShower("DSX", "Daytime Sextantids", "白昼六分仪座流星雨", "09-20", "10-06", "10-01", None, 156, -2, 32, 5),
    MeteorShower("OCT", "October Camelopardalids", "十月鹿豹座流星雨", "10-05", "10-06", "10-06", 4.67, 164, 79, 47, 5),
    MeteorShower("DRA", "Draconids", "天龙座流星雨", "10-06", "10-10", "10-09", 1.0, 262, 54, 20, 5),
    MeteorShower("ORI", "Orionids", "猎户座流星雨", "10-02", "11-07", "10-21", None, 95, 16, 66, 20),
    MeteorShower("STA", "Southern Taurids", "金牛座南流星雨", "09-20", "11-20", "11-05", None, 52, 15, 27, 7),
    MeteorShower("NTA", "Northern Taurids", "金牛座北流星雨", "10-20", "12-10", "11-12", None, 58, 22, 29, 5),
    MeteorShower("LEO", "Leonids", "狮子座流星雨", "11-06", "11-30", "11-17", None, 152, 22, 71, 15),
    MeteorShower("AMO", "alpha-Monocerotids", "麒麟座α流星雨", "11-15", "11-25", "11-22", None, 117, 1, 65, None),
    MeteorShower("PHO", "Phoenicids", "凤凰座流星雨", "12-01", "12-05", "12-02", None, 8, -27, 15, None),
    MeteorShower("PUP", "Puppid-Velids", "船尾-船帆座流星雨", "12-01", "12-15", "12-07", None, 123, -45, 44, 10),
    MeteorShower("HYD", "sigma-Hydrids", "长蛇座σ流星雨", "12-03", "12-20", "12-09", None, 125, 2, 58, 7),
    MeteorShower("GEM", "Geminids", "双子座流星雨", "12-04", "12-20", "12-14", 14.0, 112, 33, 35, 150),
    MeteorShower("URS", "Ursids", "小熊座流星雨", "12-17", "12-26", "12-22", 22.0, 217, 76, 33, 10),
)


def meteor_shower_forecast(
    *,
    target_date: date,
    lat: float,
    lon: float,
    timezone_name: str,
    elevation_m: float = 0.0,
) -> dict[str, Any]:
    tzinfo = ZoneInfo(timezone_name)
    instances = [_instance(shower, target_date.year) for shower in METEOR_SHOWERS_2026]
    visible = [item for item in instances if item["start"] <= target_date <= item["end"]]
    upcoming = [item for item in instances if target_date < item["start"] <= target_date + timedelta(days=30)]
    selected = visible + upcoming[:3]

    events = [
        _event_payload(
            item["shower"],
            item["start"],
            item["end"],
            item["peak"],
            target_date,
            lat,
            lon,
            elevation_m,
            tzinfo,
        )
        for item in selected
    ]
    events.sort(key=lambda item: (not item["active"], -item["observing_score"], item["peak_time_utc"]))
    return {
        "schema_version": METEOR_SCHEMA_VERSION,
        "catalog_version": METEOR_CATALOG_VERSION,
        "request": {
            "target_date": target_date.isoformat(),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "timezone_name": timezone_name,
            "elevation_m": round(elevation_m, 1),
        },
        "source": {
            "provider": "International Meteor Organization",
            "title": "2026 Meteor Shower Calendar",
            "documentation_url": IMO_CALENDAR_URL,
            "maximum_dates_valid_for_year": 2026,
        },
        "events": events,
    }


def _instance(shower: MeteorShower, year: int) -> dict[str, Any]:
    start_month, start_day = _month_day(shower.active_start)
    end_month, end_day = _month_day(shower.active_end)
    peak_month, peak_day = _month_day(shower.peak)
    peak = date(year, peak_month, peak_day)
    start_year = year - 1 if start_month > peak_month else year
    end_year = year + 1 if end_month < peak_month else year
    return {
        "shower": shower,
        "start": date(start_year, start_month, start_day),
        "end": date(end_year, end_month, end_day),
        "peak": peak,
    }


def _event_payload(
    shower: MeteorShower,
    start: date,
    end: date,
    peak: date,
    target_date: date,
    lat: float,
    lon: float,
    elevation_m: float,
    tzinfo: ZoneInfo,
) -> dict[str, Any]:
    peak_hour = shower.peak_hour_utc if shower.peak_hour_utc is not None else 12.0
    peak_time_utc = datetime.combine(peak, time(), tzinfo=timezone.utc) + timedelta(hours=peak_hour)
    night_samples = _night_samples(target_date, lat, lon, elevation_m, tzinfo)
    best_time: Optional[datetime] = None
    max_altitude = -90.0
    for sample in night_samples:
        altitude = _radiant_altitude(shower.radiant_ra_deg, shower.radiant_dec_deg, lat, lon, sample)
        if altitude > max_altitude:
            max_altitude = altitude
            best_time = sample

    active_span = max(1.0, (end - start).days + 1.0)
    distance_days = abs((target_date - peak).days)
    width_days = max(0.7, active_span / 7.0)
    activity_factor = math.exp(-0.5 * (distance_days / width_days) ** 2)
    nominal_zhr = shower.zhr or 6.0
    activity_score = min(100.0, 22.0 * math.log10(nominal_zhr + 1.0) * activity_factor)
    altitude_factor = _smoothstep(max_altitude, 5.0, 50.0)
    illumination = _moon_illumination(target_date)
    moon_factor = 1.0 - 0.65 * illumination
    observing_score = activity_score * (0.25 + 0.75 * altitude_factor) * moon_factor
    estimated_zhr = nominal_zhr * activity_factor

    return {
        "code": shower.code,
        "name": shower.name,
        "name_zh": shower.name_zh,
        "active": start <= target_date <= end,
        "activity_start": start.isoformat(),
        "activity_end": end.isoformat(),
        "peak_time_utc": peak_time_utc.isoformat(),
        "peak_time_local": peak_time_utc.astimezone(tzinfo).isoformat(),
        "zhr": shower.zhr,
        "estimated_zhr": round(estimated_zhr, 1) if shower.zhr is not None else None,
        "observing_score": round(max(0.0, min(100.0, observing_score)), 1),
        "radiant_ra_deg": shower.radiant_ra_deg,
        "radiant_dec_deg": shower.radiant_dec_deg,
        "radiant_max_altitude_deg": round(max_altitude, 1),
        "best_time_local": best_time.isoformat() if best_time is not None else None,
        "speed_km_s": shower.speed_km_s,
        "moon_illumination_percent": round(100.0 * illumination, 1),
        "assumption": "观测分只含活动强度、辐射点高度和月光，不含云量及地面光污染。",
    }


def _night_samples(
    target_date: date,
    lat: float,
    lon: float,
    elevation_m: float,
    tzinfo: ZoneInfo,
) -> list[datetime]:
    point = GeoPoint(lat, lon, elevation_m)
    start = datetime.combine(target_date, time(12), tzinfo=tzinfo)
    samples: list[datetime] = []
    for offset in range(25):
        local_time = start + timedelta(hours=offset)
        solar = solar_snapshot(point, local_time.astimezone(timezone.utc), str(tzinfo))
        if solar.elevation_deg <= -12.0:
            samples.append(local_time)
    return samples


def _radiant_altitude(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    local_time: datetime,
) -> float:
    utc_time = local_time.astimezone(timezone.utc)
    julian_day = utc_time.timestamp() / 86400.0 + 2440587.5
    centuries = (julian_day - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (julian_day - 2451545.0)
        + 0.000387933 * centuries * centuries
        - centuries * centuries * centuries / 38710000.0
    ) % 360.0
    hour_angle = math.radians((gmst + lon_deg - ra_deg) % 360.0)
    latitude = math.radians(lat_deg)
    declination = math.radians(dec_deg)
    sine = (
        math.sin(latitude) * math.sin(declination)
        + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sine))))


def _moon_illumination(target_date: date) -> float:
    phase = float(moon.phase(target_date))
    return max(0.0, min(1.0, (1.0 - math.cos(2.0 * math.pi * phase / 28.0)) / 2.0))


def _smoothstep(value: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0 if value >= high else 0.0
    ratio = max(0.0, min(1.0, (value - low) / (high - low)))
    return ratio * ratio * (3.0 - 2.0 * ratio)


def _month_day(value: str) -> tuple[int, int]:
    month, day = value.split("-", 1)
    return int(month), int(day)
