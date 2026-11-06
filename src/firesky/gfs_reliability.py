"""Offline GFS reliability evaluation and bounded sensitivity plans.

This module deliberately never reads forecast caches, calls the production
scorer, or treats a Bot response as an observation.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from firesky.national_batch import NATIONAL_QUALITY_INDEX_SEMANTICS


SCHEMA_VERSION = "gfs-reliability-evaluation-v1"
SENSITIVITY_SCHEMA_VERSION = "gfs-sensitivity-v1"
QUALITY_INDEX_SCALE = "quality_index_0_2.5"
QUALITY_INDEX_SEMANTICS = NATIONAL_QUALITY_INDEX_SEMANTICS
_SENSITIVITY_FIELDS = (
    "cloud_height_m",
    "cloud_thickness_m",
    "opacity",
    "aerosol_load",
    "temporal_sampling_minutes",
    "spatial_sampling_km",
)
_SENSITIVITY_BOUNDS = {
    "cloud_height_m": (0.0, 30_000.0),
    "cloud_thickness_m": (0.0, 30_000.0),
    "opacity": (0.0, 1.0),
    "aerosol_load": (0.0, 100.0),
    "temporal_sampling_minutes": (0.1, 1_440.0),
    "spatial_sampling_km": (0.01, 1_000.0),
}


def _load(value: Mapping[str, Any] | Path | str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    path = Path(value)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read evaluation JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("evaluation input must be a JSON object")
    return payload


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be an RFC3339 UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed) or (minimum is not None and parsed < minimum):
        raise ValueError(f"{field} must be a finite number")
    return parsed


def _coordinates(payload: Mapping[str, Any], field: str) -> tuple[float, float]:
    lat = _number(payload.get("lat"), f"{field}.lat")
    lon = _number(payload.get("lon"), f"{field}.lon")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError(f"{field} coordinates are out of range")
    return lat, lon


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_forecast(value: Mapping[str, Any], event_id: str) -> dict[str, Any]:
    required = {
        "issue_time_utc", "valid_time_utc", "cycle_time_utc", "lat", "lon",
        "phenomenon", "algorithm_version", "quality_index",
        "quality_index_scale", "quality_index_semantics",
    }
    missing = required - set(value)
    if missing:
        raise ValueError(f"event {event_id!r} forecast is missing {sorted(missing)!r}")
    issue = _utc(value["issue_time_utc"], f"event {event_id}.forecast.issue_time_utc")
    valid = _utc(value["valid_time_utc"], f"event {event_id}.forecast.valid_time_utc")
    cycle = _utc(value["cycle_time_utc"], f"event {event_id}.forecast.cycle_time_utc")
    if not cycle <= issue <= valid:
        raise ValueError(f"event {event_id!r} forecast times violate cycle <= issue <= valid")
    lead = (valid - cycle).total_seconds() / 3600.0
    if "forecast_lead_hours" in value and not math.isclose(
        _number(value["forecast_lead_hours"], f"event {event_id}.forecast.forecast_lead_hours"),
        lead, rel_tol=0.0, abs_tol=1e-6,
    ):
        raise ValueError(f"event {event_id!r} forecast lead does not match timestamps")
    lat, lon = _coordinates(value, f"event {event_id}.forecast")
    _text(value["phenomenon"], f"event {event_id}.forecast.phenomenon")
    _text(value["algorithm_version"], f"event {event_id}.forecast.algorithm_version")
    score = _number(value["quality_index"], f"event {event_id}.forecast.quality_index", minimum=0.0)
    if score > 2.5 or value["quality_index_scale"] != QUALITY_INDEX_SCALE:
        raise ValueError(f"event {event_id!r} forecast quality index scale is invalid")
    if value["quality_index_semantics"] != QUALITY_INDEX_SEMANTICS:
        raise ValueError(f"event {event_id!r} forecast quality semantics are invalid")
    result = dict(value)
    result.update({"issue": issue, "valid": valid, "cycle": cycle, "lead_hours": lead, "lat": lat, "lon": lon})
    return result


def _validate_observation(value: Mapping[str, Any], event_id: str, forecast: Mapping[str, Any] | None) -> dict[str, Any]:
    required = {"source", "observed_time_utc", "lat", "lon", "label_semantics"}
    missing = required - set(value)
    if missing:
        raise ValueError(f"event {event_id!r} observation is missing {sorted(missing)!r}")
    observed = _utc(value["observed_time_utc"], f"event {event_id}.observation.observed_time_utc")
    lat, lon = _coordinates(value, f"event {event_id}.observation")
    _text(value["source"], f"event {event_id}.observation.source")
    _text(value["label_semantics"], f"event {event_id}.observation.label_semantics")
    if forecast is not None:
        if abs(lat - forecast["lat"]) > 1e-6 or abs(lon - forecast["lon"]) > 1e-6:
            raise ValueError(f"event {event_id!r} observation location does not match forecast")
        if not forecast["issue"] <= observed <= forecast["valid"] + timedelta(hours=6):
            raise ValueError(f"event {event_id!r} observation time is outside the forecast event window")
    has_binary = "binary_event" in value
    has_numeric = "quality_index" in value
    if not has_binary and not has_numeric:
        raise ValueError(f"event {event_id!r} observation needs binary_event or quality_index")
    result = dict(value)
    result.update({"observed": observed, "lat": lat, "lon": lon, "has_binary": has_binary, "has_numeric": has_numeric})
    if has_binary and not isinstance(value["binary_event"], bool):
        raise ValueError(f"event {event_id!r} observation.binary_event must be boolean")
    if has_numeric:
        result["quality_index"] = _number(value["quality_index"], f"event {event_id}.observation.quality_index", minimum=0.0)
        if result["quality_index"] > 2.5 or value.get("quality_index_scale") != QUALITY_INDEX_SCALE:
            raise ValueError(f"event {event_id!r} observation numeric scale is incompatible")
    return result


def _binary_metrics(rows: list[tuple[bool, bool]]) -> dict[str, Any]:
    if not rows:
        return {"status": "not_evaluated", "count": 0}
    tp = sum(pred and obs for pred, obs in rows)
    fp = sum(pred and not obs for pred, obs in rows)
    fn = sum(not pred and obs for pred, obs in rows)
    tn = sum(not pred and not obs for pred, obs in rows)
    return {"status": "evaluated", "count": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _numeric_metrics(rows: list[tuple[float, float]]) -> dict[str, Any]:
    if not rows:
        return {"status": "not_evaluated", "count": 0}
    errors = [pred - obs for pred, obs in rows]
    return {
        "status": "evaluated",
        "count": len(rows),
        "mae": round(sum(abs(error) for error in errors) / len(errors), 10),
        "bias": round(sum(errors) / len(errors), 10),
    }


def evaluate_manifest(value: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    payload = _load(value)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty array")
    raw_calibration_ids = payload.get("calibration_event_ids", [])
    if not isinstance(raw_calibration_ids, list):
        raise ValueError("calibration_event_ids must be an array")
    calibration_ids = set(raw_calibration_ids)
    if not all(isinstance(item, str) and item for item in calibration_ids):
        raise ValueError("calibration_event_ids must contain non-empty strings")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    binary_rows: list[tuple[bool, bool]] = []
    numeric_rows: list[tuple[float, float]] = []
    bot_rows: list[tuple[float, float]] = []
    matched = unmatched = unknown = 0
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("each event must be an object")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be a non-empty string")
        if event_id in seen or event_id in calibration_ids:
            raise ValueError(f"event {event_id!r} appears in multiple evaluation partitions")
        seen.add(event_id)
        if event.get("partition", "evaluation") != "evaluation":
            raise ValueError(f"event {event_id!r} is not in the evaluation partition")
        forecast_value = event.get("forecast")
        observation_value = event.get("observation")
        forecast = _validate_forecast(forecast_value, event_id) if isinstance(forecast_value, Mapping) else None
        observation = _validate_observation(observation_value, event_id, forecast) if isinstance(observation_value, Mapping) else None
        if forecast is None and observation is None:
            raise ValueError(f"event {event_id!r} needs a forecast or observation")
        if forecast is not None and observation is not None:
            matched += 1
            if "binary_prediction" in forecast:
                if not isinstance(forecast["binary_prediction"], bool) or not observation["has_binary"]:
                    raise ValueError(f"event {event_id!r} binary labels are incompatible")
                binary_rows.append((forecast["binary_prediction"], observation["binary_event"]))
            if observation["has_numeric"]:
                numeric_rows.append((forecast["quality_index"], observation["quality_index"]))
        elif forecast is not None:
            unknown += 1
        else:
            unmatched += 1
        if forecast is not None and isinstance(event.get("bot"), Mapping):
            bot = event["bot"]
            bot_score = _number(bot.get("quality_index"), f"event {event_id}.bot.quality_index", minimum=0.0)
            if bot.get("quality_index_scale") != QUALITY_INDEX_SCALE:
                raise ValueError(f"event {event_id!r} Bot scale is incompatible")
            bot_rows.append((forecast["quality_index"], bot_score))
        normalized.append({
            "event_id": event_id,
            "status": "matched" if forecast is not None and observation is not None else "unknown" if forecast is not None else "unmatched",
            "forecast_present": forecast is not None,
            "observation_present": observation is not None,
        })
    observations_available = matched > 0
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "evaluated" if observations_available else "not_evaluated",
        "forecast_skill_scope": "independent_observation_only",
        "bot_alignment_scope": "separate_diagnostic_not_observation_skill",
        "production_defaults_changed": False,
        "counts": {"matched": matched, "unmatched": unmatched, "unknown": unknown},
        "observation_skill": {
            "binary": _binary_metrics(binary_rows),
            "numeric_quality_index": _numeric_metrics(numeric_rows),
        },
        "bot_alignment": _numeric_metrics(bot_rows),
        "events": normalized,
    }


def run_sensitivity(value: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    payload = _load(value)
    if payload.get("schema_version") != SENSITIVITY_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SENSITIVITY_SCHEMA_VERSION!r}")
    baseline = payload.get("baseline")
    experiments = payload.get("experiments")
    if not isinstance(baseline, Mapping) or not isinstance(experiments, Mapping):
        raise ValueError("baseline and experiments must be objects")
    unknown = (set(baseline) | set(experiments)) - set(_SENSITIVITY_FIELDS)
    if unknown:
        raise ValueError(f"unsupported sensitivity field(s): {sorted(unknown)!r}")
    missing_baseline = set(experiments) - set(baseline)
    if missing_baseline:
        raise ValueError(
            "experiments require matching baseline field(s): "
            f"{sorted(missing_baseline)!r}"
        )
    normalized_baseline: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    for field in _SENSITIVITY_FIELDS:
        if field not in baseline:
            continue
        minimum, maximum = _SENSITIVITY_BOUNDS[field]
        base = _number(baseline[field], f"baseline.{field}", minimum=minimum)
        if base > maximum:
            raise ValueError(f"baseline.{field} is outside [{minimum}, {maximum}]")
        normalized_baseline[field] = base
        values = experiments.get(field, [base])
        if not isinstance(values, list) or not values:
            raise ValueError(f"experiments.{field} must be a non-empty array")
        for value_item in values:
            value_number = _number(value_item, f"experiments.{field}", minimum=minimum)
            if value_number > maximum:
                raise ValueError(f"experiments.{field} is outside [{minimum}, {maximum}]")
            rows.append({"field": field, "value": value_number, "delta_from_baseline": value_number - base})
    return {
        "schema_version": SENSITIVITY_SCHEMA_VERSION,
        "status": "sensitivity_only",
        "result_kind": "parameter_matrix_plan",
        "execution_status": "not_executed",
        "model_scorer_invoked": False,
        "production_defaults_changed": False,
        "accuracy_evaluation": "not_evaluated",
        "baseline": normalized_baseline,
        "experiments": rows,
    }
