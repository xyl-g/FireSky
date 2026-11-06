from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
from datetime import datetime
from numbers import Real
from pathlib import Path
from typing import Any

from firesky.storage import dated_directory
from firesky.bounded_lock import bounded_file_lock
from firesky.time_contract import parse_zoned_iso_datetime


# The frame exposes the current instantaneous quality diagnostics, including
# the incoming-aerosol-weighted burnable load, plus the real 13-field cell
# contract. Older or structurally different frames are rejected.
MAP_FRAME_VERSION = 25
MAP_FRAME_CACHE_LOCK_TIMEOUT_SECONDS = 300.0
TOKEN_PATTERN = re.compile(r"^[a-f0-9]{24}$")
REQUIRED_QUALITY_FLOAT_FIELDS = (
    "instant_quality_index",
    "burnable_cloud_coverage",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "forward_burnable_cloud_load",
    "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load",
    "solar_incoming_block_risk",
    "forward_burnable_cloud_ratio",
)
REQUIRED_FRAME_FLOAT_FIELDS = (
    "solar_elevation_deg",
    "solar_azimuth_deg",
    *REQUIRED_QUALITY_FLOAT_FIELDS,
)
MAP_FRAME_FIELDS = frozenset(
    {
        "valid_time_local",
        *REQUIRED_FRAME_FLOAT_FIELDS,
        "valid",
        "physically_burnable",
        "observer",
        "cells",
    }
)
MAP_FRAME_OBSERVER_FIELDS = frozenset({"lat", "lon"})
MAP_FRAME_CELL_FLOAT_FIELDS = (
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "high_cloud",
    "mid_cloud",
    "low_cloud",
    "cloud_evidence",
    "deep_cloud_risk",
    "burn_score",
    "illumination_score",
    "path_clearance_score",
    "blocking_risk",
)
MAP_FRAME_CELL_FIELDS = frozenset(MAP_FRAME_CELL_FLOAT_FIELDS)
MAP_FRAME_CACHE_FIELDS = frozenset({"version", "observer", "frames"})
MAP_FRAME_UNIT_INTERVAL_FIELDS = frozenset({
    "burnable_cloud_coverage",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "solar_incoming_block_risk",
    "forward_burnable_cloud_ratio",
})
MAP_FRAME_LOAD_FIELDS = frozenset({
    "forward_burnable_cloud_load",
    "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load",
})


def canonical_map_bearing_deg(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) < 360.0
    ):
        raise ValueError("地图格点 bearing_deg 必须在 [0, 360) 范围内。")
    return round(float(value) % 360.0, 1) % 360.0


def write_map_frames(
    report,
    observer,
    cache_dir: str | Path,
    *,
    quality_steps: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    if len(quality_steps) != len(report.steps):
        raise ValueError(
            "地图帧质量时次必须与预测时次一一对应："
            f"quality_steps={len(quality_steps)}, steps={len(report.steps)}。"
        )
    frames = [
        map_frame_payload(step, observer, quality=quality_steps[index])
        for index, step in enumerate(report.steps)
    ]
    return write_map_frame_payloads(frames, observer, cache_dir)


def write_map_frame_payloads(
    frames: list[dict[str, Any]],
    observer,
    cache_dir: str | Path,
) -> tuple[str, list[str]]:
    if not frames:
        raise ValueError("地图帧列表不能为空。")
    for index, frame in enumerate(frames):
        _validate_map_frame_contract(frame, index=index)
    payload = {
        "version": MAP_FRAME_VERSION,
        "observer": {"lat": observer.lat, "lon": observer.lon},
        "frames": frames,
    }
    _validate_map_frame_cache_payload(payload)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = hashlib.sha256(encoded).hexdigest()[:24]
    target_dates = tuple(dict.fromkeys(
        frame["valid_time_local"].split("T", 1)[0] for frame in frames
    ))
    for target_date in target_dates:
        target = map_frame_path(cache_dir, token, target_date)
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(target.suffix + ".lock")
        with lock_path.open("a+b") as lock_handle:
            with bounded_file_lock(
                lock_handle,
                timeout_seconds=MAP_FRAME_CACHE_LOCK_TIMEOUT_SECONDS,
                description="地图帧缓存锁",
            ):
                if not target.exists() or target.stat().st_size == 0:
                    temporary = target.with_suffix(target.suffix + f".tmp-{os.getpid()}")
                    with gzip.open(temporary, "wb", compresslevel=3) as handle:
                        handle.write(encoded)
                    temporary.replace(target)
    return token, [frame["valid_time_local"] for frame in frames]


def read_map_frame(cache_dir: str | Path, token: str, valid_time_local: str) -> dict[str, Any]:
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("地图帧 token 无效。")
    target = map_frame_path(cache_dir, token, valid_time_local)
    if not target.is_file() or target.stat().st_size == 0:
        raise FileNotFoundError("地图帧不存在或已经过期。")
    try:
        with gzip.open(target, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("地图帧缓存损坏。") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("地图帧缓存根节点必须是对象。")
    if payload.get("version") != MAP_FRAME_VERSION:
        raise RuntimeError("地图帧版本不兼容。")
    try:
        _validate_map_frame_cache_payload(payload)
    except ValueError as exc:
        raise RuntimeError("地图帧缓存不符合 map-frame-v23 合同。") from exc
    frames = payload["frames"]
    for index, frame in enumerate(frames):
        if frame.get("valid_time_local") == valid_time_local:
            return frame
    raise ValueError("找不到指定时刻的地图帧。")


def map_frame_path(
    cache_dir: str | Path,
    token: str,
    target_date: str,
) -> Path:
    filename = f"map-frame-v{MAP_FRAME_VERSION}-{token}.json.gz"
    return dated_directory(cache_dir, target_date) / filename


def _required_quality_float(quality: dict[str, Any], field: str) -> float:
    if field not in quality:
        raise ValueError(f"地图帧质量诊断缺少 {field}。")
    value = quality[field]
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"地图帧质量诊断 {field} 必须是有限数值。")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"地图帧质量诊断 {field} 必须是有限数值。")
    return result


def _bounded_float(
    values: dict[str, Any],
    field: str,
    minimum: float,
    maximum: float,
    *,
    maximum_inclusive: bool = True,
) -> float:
    value = _required_quality_float(values, field)
    upper_valid = value <= maximum if maximum_inclusive else value < maximum
    if value < minimum or not upper_valid:
        raise ValueError(
            f"地图帧 {field} 超出 {minimum}..{maximum} 物理范围。"
        )
    return value


def _validate_map_frame_contract(frame: dict[str, Any], *, index: int) -> None:
    if set(frame) != MAP_FRAME_FIELDS:
        raise ValueError(f"地图帧 {index} 的字段不符合 map-frame-v23 合同。")
    valid_time = frame.get("valid_time_local")
    if not isinstance(valid_time, str) or not valid_time:
        raise ValueError(f"地图帧 {index} 缺少 valid_time_local。")
    try:
        parse_zoned_iso_datetime(valid_time, f"地图帧 {index} valid_time_local")
    except ValueError as exc:
        raise ValueError(f"地图帧 {index} 的 valid_time_local 无效。") from exc
    for field in REQUIRED_FRAME_FLOAT_FIELDS:
        _required_quality_float(frame, field)
    _bounded_float(frame, "solar_elevation_deg", -90.0, 90.0)
    _bounded_float(frame, "solar_azimuth_deg", 0.0, 360.0, maximum_inclusive=False)
    _bounded_float(frame, "instant_quality_index", 0.0, 2.5)
    for field in MAP_FRAME_UNIT_INTERVAL_FIELDS:
        _bounded_float(frame, field, 0.0, 1.0)
    for field in MAP_FRAME_LOAD_FIELDS:
        _bounded_float(frame, field, 0.0, 2.5)
    if "valid" not in frame or not isinstance(frame["valid"], bool):
        raise ValueError(f"地图帧 {index} 的 valid 必须是布尔值。")
    if "physically_burnable" not in frame or not isinstance(
        frame["physically_burnable"], bool
    ):
        raise ValueError(f"地图帧 {index} 的 physically_burnable 必须是布尔值。")
    if not frame["valid"] and (
        frame["physically_burnable"]
        or float(frame["instant_quality_index"]) != 0.0
        or float(frame["burnable_cloud_coverage"]) != 0.0
    ):
        raise ValueError(f"地图帧 {index} 的无效时次携带了活动质量。")
    observer = frame.get("observer")
    if not isinstance(observer, dict) or set(observer) != MAP_FRAME_OBSERVER_FIELDS:
        raise ValueError(f"地图帧 {index} 缺少 observer。")
    for field in ("lat", "lon"):
        _required_quality_float(observer, field)
    _bounded_float(observer, "lat", -90.0, 90.0)
    _bounded_float(observer, "lon", -180.0, 180.0)
    cells = frame.get("cells")
    if not isinstance(cells, list):
        raise ValueError(f"地图帧 {index} 的 cells 必须是数组。")
    if not frame["valid"] and cells:
        raise ValueError(f"地图帧 {index} 的无效时次不能携带格点。")
    for cell_index, cell in enumerate(cells):
        if not isinstance(cell, dict) or set(cell) != MAP_FRAME_CELL_FIELDS:
            raise ValueError(
                f"地图帧 {index} 的 cell {cell_index} 字段不符合 map-frame-v23 合同。"
            )
        for field in MAP_FRAME_CELL_FLOAT_FIELDS:
            _required_quality_float(cell, field)
        _bounded_float(cell, "lat", -90.0, 90.0)
        _bounded_float(cell, "lon", -180.0, 180.0)
        if _required_quality_float(cell, "distance_km") < 0.0:
            raise ValueError("地图帧 distance_km 不能为负数。")
        _bounded_float(cell, "bearing_deg", 0.0, 360.0, maximum_inclusive=False)
        for field in (
            "high_cloud",
            "mid_cloud",
            "low_cloud",
            "cloud_evidence",
            "deep_cloud_risk",
            "burn_score",
            "illumination_score",
            "path_clearance_score",
            "blocking_risk",
        ):
            _bounded_float(cell, field, 0.0, 1.0)


def _validate_map_frame_cache_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or set(payload) != MAP_FRAME_CACHE_FIELDS:
        raise ValueError("地图帧缓存顶层字段不符合 map-frame-v23 合同。")
    observer = payload.get("observer")
    if not isinstance(observer, dict) or set(observer) != MAP_FRAME_OBSERVER_FIELDS:
        raise ValueError("地图帧缓存 observer 不符合 map-frame-v23 合同。")
    for field in ("lat", "lon"):
        _required_quality_float(observer, field)
    _bounded_float(observer, "lat", -90.0, 90.0)
    _bounded_float(observer, "lon", -180.0, 180.0)
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("地图帧缓存 frames 不符合 map-frame-v23 合同。")
    previous_time: datetime | None = None
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError(f"地图帧 {index} 必须是对象。")
        _validate_map_frame_contract(frame, index=index)
        frame_time = parse_zoned_iso_datetime(
            frame["valid_time_local"], "地图帧 valid_time_local"
        )
        if previous_time is not None and frame_time <= previous_time:
            raise ValueError("地图帧 valid_time_local 必须唯一且严格递增。")
        previous_time = frame_time
        if any(
            float(frame["observer"][field]) != float(observer[field])
            for field in ("lat", "lon")
        ):
            raise ValueError(f"地图帧 {index} 的 observer 与缓存顶层不一致。")


def map_frame_payload(step, observer, *, quality: dict[str, Any]) -> dict[str, Any]:
    quality_values = {
        field: _required_quality_float(quality, field)
        for field in REQUIRED_QUALITY_FLOAT_FIELDS
    }
    if "physically_burnable" not in quality or not isinstance(
        quality["physically_burnable"], bool
    ):
        raise ValueError("地图帧质量诊断 physically_burnable 必须是布尔值。")
    instant_quality = quality_values["instant_quality_index"]
    return {
        "valid_time_local": step.valid_time_local.isoformat(),
        "valid": True,
        "solar_elevation_deg": step.solar_elevation_deg,
        "solar_azimuth_deg": step.solar_azimuth_deg,
        "instant_quality_index": instant_quality,
        "physically_burnable": quality["physically_burnable"],
        "burnable_cloud_coverage": quality_values["burnable_cloud_coverage"],
        "forward_view_cloud_field_amount": quality_values[
            "forward_view_cloud_field_amount"
        ],
        "forward_burnable_cloud_field_amount": quality_values[
            "forward_burnable_cloud_field_amount"
        ],
        "forward_burnable_cloud_load": quality_values["forward_burnable_cloud_load"],
        "forward_burnable_cloud_material_load": quality_values[
            "forward_burnable_cloud_material_load"
        ],
        "forward_burnable_cloud_incoming_aerosol_load": quality_values[
            "forward_burnable_cloud_incoming_aerosol_load"
        ],
        "solar_incoming_block_risk": quality_values["solar_incoming_block_risk"],
        "forward_burnable_cloud_ratio": quality_values["forward_burnable_cloud_ratio"],
        "observer": {"lat": observer.lat, "lon": observer.lon},
        "cells": [compact_cell(cell) for cell in step.map_cells],
    }


def compact_cell(cell) -> dict[str, Any]:
    return {
        "lat": round(cell.lat, 4),
        "lon": round(cell.lon, 4),
        "distance_km": round(cell.distance_km, 1),
        "bearing_deg": canonical_map_bearing_deg(cell.bearing_deg),
        "high_cloud": round(cell.high_cloud, 3),
        "mid_cloud": round(cell.mid_cloud, 3),
        "low_cloud": round(cell.low_cloud, 3),
        "cloud_evidence": round(cell.cloud_evidence, 3),
        "deep_cloud_risk": round(cell.deep_cloud_risk, 3),
        "burn_score": round(cell.burn_score, 3),
        "illumination_score": round(cell.illumination_score, 3),
        "path_clearance_score": round(cell.path_clearance_score, 3),
        "blocking_risk": round(cell.blocking_risk, 3),
    }
