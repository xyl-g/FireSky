# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 李晓洋
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 3.

"""firesky high-level API.

``predict_burn_sky`` runs the exact same point-forecast assembly as the HTTP
API (``run_forecast_payload`` → ``exact-native-point``), so CLI, Python and
HTTP numbers are one and the same. With ``auto_fetch=True`` (default) it
also fetches GFS itself and pre-downloads CAMS aerosol profiles for the
prediction domain via the ADS provider (needs ``~/.cdsapirc``), removing the
manual "warm the CAMS cache first" step.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from firesky.aerosol import cams_area_from_model_bbox
from firesky.gfs import GfsNomadsClient
from firesky.geometry import bounding_box
from firesky.national_batch import (
    _fire_cloud_quality,
    fire_cloud_quality_level,
    prepare_model_columns,
    prepare_solar_event_timeline,
    run_native_national_batch,
)
from firesky.version import FIRESKY_ALGORITHM_VERSION

__all__ = ["FIRESKY_ALGORITHM_VERSION", "predict_burn_sky"]

_EVENT_PAD_HOURS_BEFORE = 8.0
_EVENT_PAD_HOURS_AFTER = 8.0


def default_forecast_hours(cycle_utc: datetime, anchor_epoch: float) -> list[int]:
    """GFS-available hours covering anchor-8h..anchor+8h (1h to f120, 3h after)."""
    cycle_epoch = cycle_utc.timestamp()
    first = max(0, int((anchor_epoch - _EVENT_PAD_HOURS_BEFORE * 3600.0 - cycle_epoch) // 3600.0))
    last = int((anchor_epoch + _EVENT_PAD_HOURS_AFTER * 3600.0 - cycle_epoch) // 3600.0)
    hours: list[int] = []
    hour = first
    while hour <= last:
        if hour <= 120 or hour % 3 == 0:
            hours.append(hour)
        hour += 1
    return hours or [max(0, min(120, int(last)))]


def _point_dict(point: Any) -> dict[str, Any]:
    if isinstance(point, dict):
        return dict(point)
    return {
        "name": str(getattr(point, "name", "点")),
        "lat": float(getattr(point, "lat")),
        "lon": float(getattr(point, "lon")),
        "elevation_m": float(getattr(point, "elevation_m", 0.0)),
        "timezone_name": str(getattr(point, "timezone_name", "Asia/Shanghai")),
    }


def _auto_fetch_inputs(
    point_dicts: list[dict[str, Any]],
    target_date: date,
    phenomenon: str,
    cycle_utc: datetime | None,
    canonical_bbox: tuple[float, float, float, float],
    area_bbox: tuple[float, float, float, float],
    cams_cache_dir: Path,
    gfs_cache_dir: Path,
    timeout_seconds: int,
) -> None:
    """Best-effort data pre-fetch; scoring still fails closed when it can't.

    GFS: download every model hour the event timeline needs (same client as
    the API path).  CAMS: pre-download optical profiles for the prediction
    domain through the ADS provider — the cache area key must match the
    forecast domain, so we derive it from the same bounding box instead of
    asking the caller to guess.
    """
    from firesky.aerosol import CamsAdsAerosolProvider, cams_area_from_model_bbox
    from firesky.national_batch import prepare_solar_event_timeline

    (_, _, _, _, evaluation_epoch, _) = prepare_solar_event_timeline(
        point_dicts, target_date, phenomenon
    )
    if cycle_utc is None:
        # Mirror the API default: latest plausibly published 6-hourly cycle.
        candidate = datetime.now(timezone.utc) - timedelta(hours=5, minutes=45)
        cycle_utc = candidate.replace(
            hour=(candidate.hour // 6) * 6, minute=0, second=0, microsecond=0
        )
    hours = default_forecast_hours(cycle_utc, float(np.max(evaluation_epoch)))
    client = GfsNomadsClient(cache_dir=str(gfs_cache_dir), timeout_seconds=timeout_seconds)
    for hour in hours:
        try:
            client.load_decoded_subset(cycle_utc, int(hour), canonical_bbox)
        except Exception as exc:  # noqa: BLE001 — prefetch is best-effort
            print(f"[firesky] GFS f{hour:03d} 预取失败（评分时仍会 fail-closed）：{exc}")

    try:
        provider = CamsAdsAerosolProvider(cache_dir=cams_cache_dir)
        area = cams_area_from_model_bbox(area_bbox)
        for hour in (0, 3, 6, 9, 12, 15):
            valid_time = cycle_utc + timedelta(hours=hour)
            target = provider.download_target(area=area, target_utc=valid_time)
            print(f"[firesky] CAMS {valid_time:%H:%MZ} 就绪：{target.name}")
    except Exception as exc:  # noqa: BLE001 — prefetch is best-effort
        print(f"[firesky] CAMS 预取失败（评分时仍会 fail-closed）：{exc}")


def predict_burn_sky(
    points: Sequence[Any],
    target_date: date,
    phenomenon: str = "dusk",
    cycle_utc: datetime | None = None,
    *,
    cams_cache_dir: str | Path = ".cache/cams",
    gfs_cache_dir: str | Path = ".cache/firesky/gfs",
    radius_km: float = 1000.0,
    compute_backend: str = "cpu",
    timeout_seconds: int = 120,
    auto_fetch: bool = True,
) -> dict[str, Any]:
    """Predict the burning-sky event for one or more observers.

    Same scoring path as the HTTP API. With ``auto_fetch`` the engine
    downloads the GFS subset and pre-fetches CAMS profiles for the
    prediction domain before scoring (ADS key required for CAMS).

    Returns ``algorithm`` / ``cycle_time_utc`` / ``target_date`` /
    ``phenomenon`` plus per-point ``steps`` (five-minute quality ledger) and
    the ``peak`` step.
    """
    if phenomenon not in {"dawn", "dusk"}:
        raise ValueError("phenomenon 必须是 dawn 或 dusk")
    if not points:
        raise ValueError("至少需要一个观测点")

    point_dicts = [_point_dict(point) for point in points]
    latitudes = [float(item["lat"]) for item in point_dicts]
    longitudes = [float(item["lon"]) for item in point_dicts]
    center_lat = sum(latitudes) / len(latitudes)
    center_lon = sum(longitudes) / len(longitudes)
    max_offset_deg = max(
        max(abs(lat - center_lat), abs(lon - center_lon))
        for lat, lon in zip(latitudes, longitudes)
    )
    domain_radius_km = min(1000.0, radius_km + max_offset_deg * 111.0)
    center = type("CenterPoint", (), {"lat": center_lat, "lon": center_lon})()
    bbox = bounding_box(center, domain_radius_km)

    # A1: score through the exact same assembly as the HTTP API
    # (run_forecast_payload -> exact-native-point) so CLI/Python/HTTP numbers
    # are identical.
    # The single-point API contract takes one point per payload (top-level
    # lat/lon), so multi-point calls run one authoritative assembly each.
    payloads: list[dict[str, Any]] = []
    for point in point_dicts:
        payloads.append(
            {
                "name": point.get("name", "观测点"),
                "lat": point["lat"],
                "lon": point["lon"],
                "elevation_m": point.get("elevation_m", 0.0),
                "timezone_name": point.get("timezone_name", "Asia/Shanghai"),
                "target_date": target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date),
                "phenomenon": phenomenon,
                "model_source": "gfs",
                "forecast_mode": "model",
                "workers": 1,
                **({"cycle": cycle_utc.astimezone(timezone.utc).isoformat()} if cycle_utc is not None else {}),
            }
        )

    from firesky.gfs import canonical_subset_bbox
    from firesky.web_server import WebServerConfig, run_forecast_payload

    if auto_fetch:
        _auto_fetch_inputs(
            point_dicts,
            target_date,
            phenomenon,
            cycle_utc,
            canonical_subset_bbox(bbox, 0.25),
            bbox,
            Path(cams_cache_dir),
            Path(gfs_cache_dir),
            timeout_seconds,
        )

    config = WebServerConfig(
        gfs_cache_dir=Path(gfs_cache_dir),
        cams_cache_dir=Path(cams_cache_dir),
    )
    results: list[dict[str, Any]] = []
    for point_index, point_payload in enumerate(payloads):
        point = point_dicts[point_index]
        api_point = run_forecast_payload(point_payload, config)
        steps = api_point.get("steps") or []
        compact_steps = [
            {
                "valid_time_local": step.get("valid_time_local"),
                "quality_index": step.get("instant_quality_index"),
                "burnable_cloud_coverage": step.get("burnable_cloud_coverage"),
                "physically_burnable": step.get("physically_burnable"),
            }
            for step in steps
        ]
        peak_index = api_point.get("peak_quality_index")
        results.append(
            {
                "point": point,
                "steps": compact_steps,
                "peak": {
                    "quality_index": peak_index,
                    "quality_level": fire_cloud_quality_level(peak_index or 0.0),
                    "event_quality_index": api_point.get("quality_index"),
                    "event_quality_level": api_point.get("quality_level"),
                },
            }
        )

    return {
        "algorithm": FIRESKY_ALGORITHM_VERSION,
        "target_date": (
            target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date)
        ),
        "phenomenon": phenomenon,
        "cycle_time_utc": cycle_utc.astimezone(timezone.utc).isoformat(),
        "compute_backend": compute_backend,
        "points": results,
    }
