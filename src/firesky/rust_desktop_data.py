"""Native desktop planning, cache checks and HSD frame export."""
from datetime import datetime, timezone
import os
import hashlib
import json
import math
from collections import OrderedDict
import stat
import tempfile
import threading
from pathlib import Path

from firesky.rust_desktop_process import call_engine
from firesky.tensor_bundle import load_tensor_bundle

_HSD_BUNDLE_CACHE_MAX_BYTES = 128 * 1024 * 1024
_HSD_BUNDLE_CACHE_LOCK = threading.Lock()
_HSD_BUNDLE_CACHE = OrderedDict()

NOWCAST_INSTANT_FIELDS = (
    "index",
    "pre_response_index",
    "quality_response_scale",
    "quality_response_exponent",
    "quality_response_adjustment",
    "physically_burnable",
    "burnable_cloud_coverage",
    "brightness_color",
    "cloud_material",
    "material_factor",
    "material_adjustment",
    "incoming_aod_illumination_factor",
    "incoming_aod_adjustment",
    "observer_aod_contrast_factor",
    "observer_aod_adjustment",
    "coverage_base_score",
    "raw_quality_index",
    "quality_aod550",
    "local_aod550",
)
NOWCAST_EVENT_PROJECTION_FIELDS = (
    "index",
    "burnable_cloud_coverage_peak",
    "forward_burnable_cloud_field_amount_peak",
    "forward_burnable_cloud_load_peak",
    "forward_burnable_cloud_material_load_peak",
    "forward_burnable_cloud_incoming_aerosol_load_mean",
    "forward_burnable_cloud_ratio_peak",
    "brightness_color",
    "cloud_material",
    "quality_aod550",
)

_NOWCAST_ADJUSTMENT_FIELDS = {
    "contract",
    "runtime_python_used",
    "target_time_utc",
    "nowcast_algorithm",
    "source",
    "image_valid_time_utc",
    "image_valid_time_local",
    "image_channel",
    "image_area",
    "near_cloud_ratio",
    "far_cloud_ratio",
    "trend_score",
    "visibility_penalty",
    "target_bonus",
    "confidence",
    "summary",
    "motion_dx_px_per_hour",
    "motion_dy_px_per_hour",
    "motion_correlation",
    "projection_hours",
    "satellite_source_times_utc",
    "satellite_motion_age_minutes",
    "satellite_motion_quality",
    "satellite_advection_horizon_hours",
    "infrared_near_cloud_ratio",
    "infrared_far_cloud_ratio",
    "visible_near_cloud_ratio",
    "visible_far_cloud_ratio",
    "channel_fusion_weight_visible",
    "local_cloud_ratio",
    "horizon_cloud_ratio",
    "thin_cloud_ratio",
    "thick_cloud_ratio",
    "cloud_texture_score",
    "cloud_uniformity_score",
    "cloud_blanket_score",
    "horizon_clearance_score",
    "composition_score",
    "observed_features_by_offset",
    "observed_feature_times_by_offset",
    "cloud_column_schema_version",
    "cloud_column_sets",
    "vertical_prior_identity",
    "vertical_prior_source",
    "satellite_retrieval_quality",
    "satellite_low_cloud_state",
    "satellite_range_bytes",
    "satellite_range_blocks",
    "nowcast_point_lat",
    "nowcast_point_lon",
    "nowcast_cache_identity",
    "satellite_vertical_profile",
    "calibrated_sample_coverage_ratio",
}

_CLOUD_COLUMN_PAYLOAD_FIELDS = {
    "schema_version",
    "algorithm",
    "valid_time_utc",
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "base_p10_asl_m",
    "base_p50_asl_m",
    "base_p90_asl_m",
    "base_p10_agl_m",
    "base_p50_agl_m",
    "base_p90_agl_m",
    "top_asl_m",
    "top_agl_m",
    "terrain_asl_m",
    "phase",
    "cloud_type",
    "optical_depth",
    "cloud_probability_by_height",
    "horizontal_cloud_probability",
    "low_cloud_state",
    "retrieval_quality",
    "source",
    "vertical_prior_identity",
    "vertical_prior_source",
    "base_source",
    "motion_dx_px_per_hour",
    "motion_dy_px_per_hour",
}

_CLOUD_COLUMN_DERIVED_AGL_FIELDS = {
    "base_p10_agl_m",
    "base_p50_agl_m",
    "base_p90_agl_m",
    "top_agl_m",
}


def _parse_native_utc(raw, label):
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Rust nowcast adjustment {label} time is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RuntimeError(f"Rust nowcast adjustment {label} time is not UTC")
    return parsed.astimezone(timezone.utc)


def enabled():
    value = os.environ.get("FIRESKY_DESKTOP_DATA_BACKEND", "python").strip().lower()
    if value not in {"python", "rust"}:
        raise ValueError("FIRESKY_DESKTOP_DATA_BACKEND must be python or rust")
    return value == "rust"


def score_observer_fields(
    rows,
    *,
    mode="scalar",
    apply_duration=True,
    local_columns=True,
    score_ceiling=False,
    radius_km=200.0,
):
    """Score complete observer-field rows in the native engine."""
    if mode not in {"scalar", "regional"}:
        raise ValueError("native observer-field scoring mode is invalid")
    encoded_rows = [
        [[float(item) for item in row] for row in event]
        for event in rows
    ]
    value = call_engine(
        "score_observer_fields",
        {
            "rows": encoded_rows,
            "mode": mode,
            "apply_duration": bool(apply_duration),
            "local_columns": bool(local_columns),
            "score_ceiling": bool(score_ceiling),
            "radius_km": float(radius_km),
        },
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"events", "instant", "metadata"}
        or not isinstance(value["events"], list)
        or not isinstance(value["instant"], list)
        or not isinstance(value["metadata"], dict)
        or len(value["events"]) != len(value["instant"])
    ):
        raise RuntimeError("Rust observer-field scoring response identity is invalid")
    for event, instant in zip(value["events"], value["instant"], strict=True):
        if (
            not isinstance(event, dict)
            or set(event) != {"index", "components"}
            or not isinstance(event["index"], (int, float))
            or not isinstance(event["components"], dict)
            or not isinstance(instant, list)
            or any(not isinstance(item, dict) for item in instant)
        ):
            raise RuntimeError("Rust observer-field scoring payload is invalid")
    return value


def score_nowcast_fields(
    rows,
    *,
    apply_duration=True,
    local_columns=True,
    score_ceiling=False,
    radius_km=200.0,
    parallel_instants=False,
):
    """Score one nowcast event and all its instant ledgers in one native call."""
    encoded_rows = [
        [float(item) for item in row]
        for row in rows
    ]
    value = call_engine(
        "score_nowcast_fields",
        {
            "rows": encoded_rows,
            "apply_duration": bool(apply_duration),
            "local_columns": bool(local_columns),
            "score_ceiling": bool(score_ceiling),
            "radius_km": float(radius_km),
            "parallel_instants": bool(parallel_instants),
        },
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {
            "contract",
            "event",
            "instant",
            "instant_event_projection",
            "metadata",
        }
        or value.get("contract") != "rust-desktop-nowcast-quality-v2"
        or not isinstance(value.get("event"), dict)
        or not isinstance(value.get("instant"), list)
        or len(value["instant"]) != len(encoded_rows)
        or not isinstance(value.get("instant_event_projection"), list)
        or len(value["instant_event_projection"]) != len(encoded_rows)
        or not isinstance(value.get("metadata"), dict)
        or value["metadata"].get("nowcast_event_fields")
        != list(NOWCAST_EVENT_PROJECTION_FIELDS)
        or value["metadata"].get("instant_fields")
        != list(NOWCAST_INSTANT_FIELDS)
    ):
        raise RuntimeError("Rust nowcast quality response identity is invalid")
    event = value["event"]
    if (
        set(event) != {"index", "components"}
        or not isinstance(event["index"], (int, float))
        or isinstance(event["index"], bool)
        or not isinstance(event["components"], dict)
        or any(
            not isinstance(item, list)
            or len(item) != len(NOWCAST_INSTANT_FIELDS)
            or any(
                not isinstance(number, (int, float)) or isinstance(number, bool)
                for number in item
            )
            for item in value["instant"]
        )
        or any(
            not isinstance(item, list)
            or len(item) != len(NOWCAST_EVENT_PROJECTION_FIELDS)
            or any(
                not isinstance(number, (int, float)) or isinstance(number, bool)
                for number in item
            )
            for item in value["instant_event_projection"]
        )
    ):
        raise RuntimeError("Rust nowcast quality payload is invalid")
    return value


def inventory(config=None):
    """Read the complete desktop cache inventory in the Rust engine."""
    from firesky.web_server import WebServerConfig

    config = config or WebServerConfig()
    paths = {}
    for key in ("gfs_cache_dir", "ecmwf_cache_dir", "cams_cache_dir"):
        value = getattr(config, key, None)
        if value is None:
            raise RuntimeError(f"desktop inventory config is missing {key}")
        # Keep the caller's spelling for EC cycle paths; the Rust scanner
        # resolves only the displayed root, matching Python's rglob output.
        paths[key] = str(value)
    value = call_engine(
        "desktop_inventory",
        {"config": paths},
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "runtime_python_used", "datasets"}
        or value.get("contract") != "rust-desktop-inventory-v1"
        or value.get("runtime_python_used") is not False
        or not isinstance(value.get("datasets"), list)
    ):
        raise RuntimeError("Rust desktop inventory response identity is invalid")
    datasets = value["datasets"]
    expected_sources = ["gfs", "ecmwf", "cams"]
    if (
        len(datasets) != len(expected_sources)
        or any(
            not isinstance(dataset, dict)
            or dataset.get("source") != source
            for dataset, source in zip(datasets, expected_sources, strict=True)
        )
    ):
        raise RuntimeError("Rust desktop inventory dataset response is invalid")
    return datasets


def _location_config():
    from firesky import location as source

    timezone_manifest = os.environ.get("FIRESKY_TIMEZONE_MANIFEST")
    return {
        "geocoding_url": str(source.GEOCODING_URL),
        "metadata_url": str(source.POINT_METADATA_URL),
        "nominatim_url": str(source.NOMINATIM_URL),
        "srtm_base_url": str(source.SRTM_BASE_URL),
        "location_cache_path": str(source.LOCATION_CACHE_PATH),
        "srtm_cache_dir": str(source.SRTM_CACHE_DIR),
        "timezone_manifest": (
            str(Path(timezone_manifest))
            if timezone_manifest
            else None
        ),
        "request_attempts": int(source.HTTP_RETRIES),
        "retry_seconds": 0.8,
        "lock_timeout_seconds": float(source.SRTM_CACHE_LOCK_TIMEOUT_SECONDS),
    }


def location(method, params):
    """Run a production desktop search/metadata operation in Rust."""
    value = call_engine(
        "desktop_location",
        {
            "method": str(method),
            "params": dict(params),
            "config": _location_config(),
            "allow_download": True,
        },
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "runtime_python_used", "result"}
        or value.get("contract") != "rust-desktop-location-v1"
        or value.get("runtime_python_used") is not False
        or not isinstance(value.get("result"), dict)
    ):
        raise RuntimeError("Rust desktop location response identity is invalid")
    return value["result"]


def publications(sources, *, now_utc=None, timeout_seconds=2.0):
    """Probe publication cycles in the persistent Rust desktop engine."""
    value = call_engine(
        "desktop_publications",
        {
            "sources": [str(source) for source in sources],
            "now_utc": str(now_utc) if now_utc is not None else None,
            "timeout_seconds": float(timeout_seconds),
        },
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "runtime_python_used", "publications"}
        or value.get("contract") != "rust-desktop-publications-v1"
        or value.get("runtime_python_used") is not False
        or not isinstance(value.get("publications"), dict)
    ):
        raise RuntimeError("Rust desktop publications response identity is invalid")
    expected = list(dict.fromkeys(str(source) for source in sources))
    result = value["publications"]
    if set(result) != set(expected):
        raise RuntimeError("Rust desktop publications source response is invalid")
    for source in expected:
        item = result[source]
        if (
            not isinstance(item, dict)
            or set(item) - {"cycle", "label", "verified", "error"}
            or not isinstance(item.get("cycle"), str)
            or not isinstance(item.get("label"), str)
            or type(item.get("verified")) is not bool
            or ("error" in item and not isinstance(item["error"], str))
        ):
            raise RuntimeError("Rust desktop publication entry is invalid")
    return result


def session(action, owner, *, token=None, kind=None, payload=None, report=None):
    """Use Rust as the opt-in desktop session lifecycle authority."""
    request = {
        "action": str(action),
        "owner": str(owner),
    }
    if token is not None:
        request["session"] = str(token)
    if kind is not None:
        request["kind"] = str(kind)
    if payload is not None:
        request["payload"] = dict(payload)
    if report is not None:
        request["report"] = dict(report)
    value = call_engine("desktop_session", request, timeout_seconds=None)
    if (
        not isinstance(value, dict)
        or value.get("contract") != "rust-desktop-session-v1"
        or value.get("runtime_python_used") is not False
    ):
        raise RuntimeError("Rust desktop session response identity is invalid")
    if action == "validate":
        if (
            set(value)
            != {"contract", "runtime_python_used", "session", "kind"}
            or not isinstance(value["session"], str)
            or not isinstance(value["kind"], str)
        ):
            raise RuntimeError("Rust desktop session validation response is invalid")
    elif action in {"get", "update", "consume"}:
        if (
            set(value) != {
                "contract",
                "runtime_python_used",
                "session",
                "kind",
                "payload",
                "report",
            }
            or not isinstance(value["session"], str)
            or not isinstance(value["kind"], str)
            or not isinstance(value["payload"], dict)
            or not isinstance(value["report"], dict)
        ):
            raise RuntimeError("Rust desktop session snapshot is invalid")
    elif action == "create":
        if (
            set(value) != {"contract", "runtime_python_used", "session"}
            or not isinstance(value["session"], str)
            or not value["session"]
        ):
            raise RuntimeError("Rust desktop session create response is invalid")
    elif action == "dismiss":
        if set(value) != {"contract", "runtime_python_used", "dismissed"}:
            raise RuntimeError("Rust desktop session dismiss response is invalid")
    else:
        raise ValueError("unknown Rust desktop session action")
    return value


def plan(context, notes):
    from firesky.aerosol import cams_profile_bracket_times
    def iso(value):
        return value.astimezone(timezone.utc).isoformat()
    return dict(
        model_source=context["common"]["model_source"],
        cycle_utc=iso(context["cycle"]), event_start=iso(min(context["times"])),
        bbox=list(context["bbox"]), area=list(context["area"]),
        hours=list(context["hours"]),
        cams_valid_times=[iso(t) for t in sorted({
            bracket for target in context["times"] for bracket in cams_profile_bracket_times(target)
        })],
        point_count=len(context["points"]), terrain_pending=bool(context["terrain_pending"]),
        compute_region=context["common"]["_compute_region"], notes=notes,
    )


def model_context(payload, *, regional=False, config=None, fast=False):
    """Resolve raw point or regional desktop input in Rust."""
    from firesky.web_server import WebServerConfig, CHINA_CITY_CATALOG_PATH
    config = config or WebServerConfig()
    timezone_manifest = os.environ.get("FIRESKY_TIMEZONE_MANIFEST")
    request = {
        "payload": dict(payload),
        "regional": bool(regional),
        "fast": bool(fast),
        "now_utc": os.environ.get("FIRESKY_CONTEXT_NOW_UTC"),
        "config": {
            "gfs_cache_dir": str(Path(config.gfs_cache_dir).resolve()),
            "ecmwf_cache_dir": str(Path(config.ecmwf_cache_dir).resolve()),
            "cams_cache_dir": str(Path(config.cams_cache_dir).resolve()),
            "timezone_manifest": str(Path(timezone_manifest).resolve()) if timezone_manifest else None,
            "city_catalog_path": str(Path(CHINA_CITY_CATALOG_PATH).resolve()),
        },
    }
    with tempfile.TemporaryDirectory(prefix="firesky-rust-context-") as root:
        root = Path(root)
        input_path, output_path = root / "request.json", root / "context.json"
        input_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        result = call_engine("desktop_context", {
            "path": str(input_path), "output_path": str(output_path),
        }, timeout_seconds=None)
        if result.get("contract") != "rust-desktop-context-file-v1" or result.get("runtime_python_used") is not False:
            raise RuntimeError("Rust desktop context response identity is invalid")
        raw = output_path.read_bytes()
        if len(raw) != result.get("bytes") or hashlib.sha256(raw).hexdigest() != result.get("sha256"):
            raise RuntimeError("Rust desktop context output identity is invalid")
        document = json.loads(raw)
    if document.get("contract") != "rust-desktop-context-v1" or document.get("runtime_python_used") is not False:
        raise RuntimeError("Rust desktop context document identity is invalid")
    context = document.get("context")
    if not isinstance(context, dict):
        raise RuntimeError("Rust desktop context document is missing context")
    cycle = datetime.fromisoformat(context["cycle"].replace("Z", "+00:00"))
    times = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in context["times"]]
    context["cycle"] = cycle
    context["times"] = times
    context["bbox"] = tuple(context["bbox"])
    common = context.get("common")
    if isinstance(common, dict):
        for key in ("_model_fixed_bbox", "_gfs_fixed_bbox"):
            if isinstance(common.get(key), list):
                common[key] = tuple(common[key])
    return context


def prepare_model_data(payload, *, regional=False, config=None, progress=None):
    """Prepare model and CAMS inputs entirely inside the native engine."""
    from firesky.web_server import WebServerConfig, CHINA_CITY_CATALOG_PATH

    config = config or WebServerConfig()
    if payload.get("model_source") == "both":
        results = {}
        for index, model in enumerate(("gfs", "ec")):
            def model_progress(event, index=index, model=model):
                if progress:
                    progress({
                        **event,
                        "percent": index * 50 + int(event.get("percent", 0)) // 2,
                        "message": f"{model.upper()} · {event.get('message', '准备数据')}",
                    })

            model_progress({"stage": "prepare", "percent": 0})
            results[model] = prepare_model_data(
                {**payload, "model_source": model},
                regional=regional,
                config=config,
                progress=model_progress,
            )
        return {"status": "ready", "models": results}

    model = str(payload.get("model_source") or "gfs")
    if model not in {"gfs", "ec"}:
        raise ValueError("native model preparation requires gfs, ec or both")
    request = {
        "payload": dict(payload),
        "regional": bool(regional),
        "fast": False,
        "now_utc": os.environ.get("FIRESKY_CONTEXT_NOW_UTC"),
        "config": {
            "gfs_cache_dir": str(Path(config.gfs_cache_dir).resolve()),
            "ecmwf_cache_dir": str(Path(config.ecmwf_cache_dir).resolve()),
            "cams_cache_dir": str(Path(config.cams_cache_dir).resolve()),
            "timezone_manifest": (
                str(Path(os.environ["FIRESKY_TIMEZONE_MANIFEST"]).resolve())
                if os.environ.get("FIRESKY_TIMEZONE_MANIFEST") else None
            ),
            "city_catalog_path": str(Path(CHINA_CITY_CATALOG_PATH).resolve()),
        },
    }
    if progress:
        progress({"stage": "prepare", "percent": 1, "message": "Rust 解析事件窗口与空间范围"})
    with tempfile.TemporaryDirectory(prefix="firesky-rust-prepare-") as root:
        input_path = Path(root) / "request.json"
        input_path.write_text(
            json.dumps(request, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        value = call_engine("desktop_prepare", {"path": str(input_path)})
    if (
        not isinstance(value, dict)
        or value.get("contract") != "rust-desktop-prepare-v1"
        or value.get("runtime_python_used") is not False
        or value.get("status") != "ready"
        or value.get("model_source") != model
        or value.get("terrain_pending") is not False
        or not isinstance(value.get("hours"), list)
        or not value.get("hours")
        or any(type(hour) is not int or hour < 0 for hour in value["hours"])
    ):
        raise RuntimeError("Rust desktop preparation response identity is invalid")
    if progress:
        progress({
            "stage": "prepare",
            "percent": 100,
            "message": f"{model.upper()} 模型与 CAMS 数据已准备",
        })
    return value


def _native_point_observer(point):
    """Translate the desktop context point to the frozen-plan observer schema."""
    if not isinstance(point, dict):
        raise RuntimeError("Rust desktop point context is invalid")
    source = str(point.get("_elevation_source") or "").strip().lower()
    if source not in {"observer", "model-grid"}:
        raise RuntimeError("Rust desktop point elevation source is invalid")
    required = ("name", "lat", "lon", "timezone_name")
    if any(key not in point for key in required):
        raise RuntimeError("Rust desktop point context is incomplete")
    observer = {
        "name": str(point["name"]),
        "lat": float(point["lat"]),
        "lon": float(point["lon"]),
        "timezone_name": str(point["timezone_name"]),
        "elevation_source": source,
    }
    if source == "observer":
        elevation = point.get("elevation_m")
        if isinstance(elevation, bool) or not isinstance(elevation, (int, float)):
            raise RuntimeError("Rust desktop observer elevation is invalid")
        if not math.isfinite(float(elevation)):
            raise RuntimeError("Rust desktop observer elevation is invalid")
        observer["elevation_m"] = float(elevation)
    return observer


def _source_identity(path):
    path = Path(path)
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _native_artifact_path(root, entry, expected_name):
    if not isinstance(entry, dict):
        raise RuntimeError(f"Rust point artifact {expected_name} metadata is invalid")
    name = entry.get("file")
    if name != expected_name or not isinstance(entry.get("identity"), dict):
        raise RuntimeError(f"Rust point artifact {expected_name} identity is invalid")
    path = (Path(root) / name).resolve()
    if path.parent != Path(root).resolve() or not path.is_file():
        raise RuntimeError(f"Rust point artifact {expected_name} path is invalid")
    if entry["identity"] != _source_identity(path):
        raise RuntimeError(f"Rust point artifact {expected_name} checksum is invalid")
    return path


def _native_derived_cache(config, model):
    """Keep decoded native frames across desktop point requests."""
    cache_root = (
        Path(config.gfs_cache_dir)
        if model == "gfs"
        else Path(config.ecmwf_cache_dir)
    ).resolve()
    return {
        "directory": str(cache_root / "derived" / "rust-v1"),
        "lock_timeout_seconds": 30.0,
    }


def _native_point_result(root, request_path, model, context, manifest):
    if not isinstance(manifest, dict):
        raise RuntimeError("Rust point forecast manifest is invalid")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("Rust point forecast manifest metadata is invalid")
    if model == "ec":
        artifact_key = "ecmwf_point_report"
        artifact_name = "ecmwf_point.json"
        artifact_contract = "rust-ecmwf-point-report-v1"
    else:
        artifact_key = "point_forecast"
        artifact_name = "point_forecast.json"
        artifact_contract = "rust-stored-point-forecast-v1"
    artifact = metadata.get(artifact_key)
    if not isinstance(artifact, dict) or artifact.get("contract") != artifact_contract:
        raise RuntimeError(f"Rust {model} point artifact contract is incomplete")
    if model == "gfs" and (
        artifact.get("point_core_validated") is not True
        or artifact.get("stored_point_payload_complete") is not True
    ):
        raise RuntimeError("Rust GFS point artifact contract is incomplete")
    if model == "ec" and (
        artifact.get("report_complete") is not True
        or artifact.get("provenance_complete") is not False
    ):
        raise RuntimeError("Rust EC point artifact contract is incomplete")
    if artifact.get("public_forecast_complete") is not False:
        raise RuntimeError("Rust point artifact public contract identity is invalid")
    point_path = _native_artifact_path(root, artifact, artifact_name)
    raw = point_path.read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("Rust point forecast payload exceeds 8 MiB")
    document = json.loads(raw)
    if model == "ec":
        if (
            not isinstance(document, dict)
            or document.get("contract") != "rust-ecmwf-point-report-v1"
            or not isinstance(document.get("report"), dict)
            or not isinstance(document.get("native_quality"), dict)
            or not isinstance(document.get("web_report_payload"), dict)
            or not isinstance(document.get("dominant_burnable"), dict)
            or not isinstance(document.get("confidence_semantics"), str)
            or document.get("public_forecast_complete") is not False
        ):
            raise RuntimeError("Rust EC point report payload identity is invalid")
        result = dict(document["web_report_payload"])
        result["confidence_score_semantics"] = document["confidence_semantics"]
        result["native_ecmwf_report"] = {
            "contract": document["contract"],
            "native_quality": document["native_quality"],
            "dominant_burnable": document["dominant_burnable"],
            "public_forecast_complete": False,
        }
        dominant = document["dominant_burnable"]
        for suffix, value in (
            ("pressure_family", dominant.get("pressure_family")),
            ("cloud_phase", dominant.get("cloud_phase")),
            ("cloud_base_asl_mean_m", dominant.get("cloud_base_asl_mean_m")),
            ("cloud_top_asl_mean_m", dominant.get("cloud_top_asl_mean_m")),
            ("target_terrain_asl_mean_m", dominant.get("target_terrain_asl_mean_m")),
            ("cloud_base_agl_mean_m", dominant.get("cloud_base_agl_mean_m")),
            ("cloud_top_agl_mean_m", dominant.get("cloud_top_agl_mean_m")),
            (
                "cloud_base_above_observer_mean_m",
                dominant.get("cloud_base_above_observer_mean_m"),
            ),
            (
                "cloud_top_above_observer_mean_m",
                dominant.get("cloud_top_above_observer_mean_m"),
            ),
        ):
            result[f"dominant_burnable_{suffix}"] = value
        result["dominant_burnable_cloud_genus_diagnosis"] = dominant.get(
            "genus_diagnosis"
        )
        result["cloud_genus_proxy"] = dominant.get("genus_proxy")
        result["cloud_phase_proxy"] = dominant.get("phase_proxy")
        result["point_refinement"] = "rust-ecmwf-point-report-v1"
        result["diagnostic_source"] = "ec-native-regional-cloud-and-solar-profile"
        result["data_provenance"] = {
            "model_source": "ec",
            "cycle_utc": result.get("cycle_time_utc"),
            "gfs_cache_used": False,
            "cams_cache_used": True,
            "observation_used": False,
            "confidence_semantics": document["confidence_semantics"],
        }
    else:
        result = document
    if (
        not isinstance(result, dict)
        or result.get("model_source") != model
        or result.get("forecast_mode") != "model"
        or result.get("target_date") != request_path["date"]
        or result.get("phenomenon") != request_path["phenomenon"]
    ):
        raise RuntimeError("Rust point forecast payload identity is invalid")

    cross_entry = metadata.get("cross_section_source")
    if isinstance(cross_entry, dict):
        source_path = _native_artifact_path(
            root, cross_entry, "cross_section_source.json"
        )
        anchor = datetime.fromisoformat(
            str(result["anchor_local"]).replace("Z", "+00:00")
        )
        best = result.get("best_time_local")
        if best:
            offset_minutes = (
                datetime.fromisoformat(str(best).replace("Z", "+00:00")) - anchor
            ).total_seconds() / 60.0
        else:
            offset_minutes = 0.0
        point = context["points"][0]
        cross = call_engine(
            "build_stored_cross_section",
            {
                "source_path": str(source_path),
                "source_identity": cross_entry["identity"],
                "point": {
                    "lat": float(point["lat"]),
                    "lon": float(point["lon"]),
                    "elevation_m": float(point["elevation_m"]),
                    "timezone_name": str(point["timezone_name"]),
                },
                "offset_minutes": float(offset_minutes),
            },
            timeout_seconds=None,
        )
        if not isinstance(cross, dict):
            raise RuntimeError("Rust point cross-section response is invalid")
        result["cross_section"] = cross
        result["cross_section_request"] = None
    else:
        result.setdefault("cross_section", None)
        result.setdefault("cross_section_request", None)
    result["native_forecast"] = {
        "contract": "rust-desktop-point-forecast-v1",
        "runtime_python_used": False,
        "model_source": model,
        "point_payload_complete": True,
        "native_artifact_contract": artifact_contract,
        "provenance_complete": artifact.get("provenance_complete") is True,
        "public_web_contract_complete": False,
    }
    return result


def forecast_point(payload, *, model, config=None, progress=None):
    """Run one desktop point forecast through the native frozen-input path.

    The desktop result is the validated native point payload. HTML/report
    publication and regional map products remain outside this boundary.
    """
    from firesky.web_server import WebServerConfig

    config = config or WebServerConfig()
    model = str(model).strip().lower()
    if model not in {"gfs", "ec"}:
        raise ValueError("native desktop point forecast requires gfs or ec")
    prepared = dict(payload)
    prepared.update({
        "model_source": model,
        "forecast_mode": "model",
        "satellite_source": "off",
    })
    context = model_context(prepared, regional=False, config=config, fast=False)
    points = context.get("points")
    if not isinstance(points, list) or len(points) != 1:
        raise RuntimeError("Rust desktop point context must contain one point")
    observer = _native_point_observer(points[0])
    cycle = context.get("cycle")
    if not isinstance(cycle, datetime):
        raise RuntimeError("Rust desktop point context cycle is invalid")
    date_value = str(prepared.get("target_date") or "")
    phenomenon = str(prepared.get("phenomenon") or "")
    radius = float(prepared.get("radius_km") or 1000.0)
    if progress:
        progress({
            "stage": "native_plan",
            "percent": 5,
            "message": f"{model.upper()} Rust 生成冻结输入计划",
        })

    if model == "gfs":
        request = {
            "date": date_value,
            "phenomenon": phenomenon,
            "points": [observer],
            "radius_km": radius,
            "cycle_utc": cycle.astimezone(timezone.utc).isoformat(),
            "gfs": {
                "cache_dir": str(Path(config.gfs_cache_dir).resolve()),
                "resolution": "0p25",
                "region": context.get("common", {}).get("_compute_region"),
            },
            "cams_cache_dir": str(Path(config.cams_cache_dir).resolve()),
            "bbox": list(context["bbox"]),
            "batch_min_points": 64,
            "allow_gfs_download": False,
            "derived_cache": _native_derived_cache(config, model),
        }
        method = "forecast_cached_gfs_point_to_files"
    else:
        request = {
            "date": date_value,
            "phenomenon": phenomenon,
            "point": observer,
            "radius_km": radius,
            "cycle_utc": cycle.astimezone(timezone.utc).isoformat(),
            "ec": {
                "cache_dir": str(Path(config.ecmwf_cache_dir).resolve()),
                "source": "ecmwf",
                "resolution": "0p25",
            },
            "cams_cache_dir": str(Path(config.cams_cache_dir).resolve()),
            "compute_backend": str(prepared.get("compute_backend") or "auto"),
            "allow_ec_download": False,
            "derived_cache": _native_derived_cache(config, model),
        }
        method = "forecast_cached_ecmwf_point_to_files"

    with tempfile.TemporaryDirectory(prefix="firesky-rust-desktop-point-") as root:
        root = Path(root)
        request_path = root / "request.json"
        output = root / "output"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        value = call_engine(
            method,
            {"path": str(request_path), "output_dir": str(output)},
            timeout_seconds=None,
        )
        if (
            not isinstance(value, dict)
            or value.get("manifest_path") != str(output / "manifest.json")
        ):
            raise RuntimeError("Rust desktop point forecast manifest identity is invalid")
        if progress:
            progress({
                "stage": "native_score",
                "percent": 90,
                "message": f"{model.upper()} Rust 完成解码与评分",
            })
        bundle = load_tensor_bundle(
            value["manifest_path"],
            kind="point-forecast-v1",
            source_path=request_path,
            # The bridge consumes only the manifest and JSON artifacts. Keep
            # array validation/hash checks, but avoid copying NPY payloads into
            # the Python heap.
            mmap=True,
        )
        result = _native_point_result(
            output, request, model, context, bundle.manifest
        )
    if progress:
        progress({
            "stage": "native_finalize",
            "percent": 100,
            "message": f"{model.upper()} Rust 单点结果已整理",
        })
    return result


def _native_regional_model_files(prepared, model):
    """Extract the resolved model file list from native preparation."""
    if not isinstance(prepared, dict):
        raise RuntimeError("Rust regional preparation response is invalid")
    model_result = prepared.get("model")
    if not isinstance(model_result, dict):
        raise RuntimeError("Rust regional preparation model response is invalid")
    if model == "gfs":
        rows = model_result.get("files")
    else:
        resolution = model_result.get("resolution")
        rows = resolution.get("files") if isinstance(resolution, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Rust regional preparation has no model files")
    normalized = []
    seen_hours = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Rust regional model file entry is invalid")
        hour = row.get("forecast_hour")
        path = row.get("path")
        if (
            isinstance(hour, bool)
            or not isinstance(hour, int)
            or hour < 0
            or not isinstance(path, str)
            or not path
            or hour in seen_hours
        ):
            raise RuntimeError("Rust regional model file identity is invalid")
        seen_hours.add(hour)
        normalized.append({"hour": hour, "path": path})
    normalized.sort(key=lambda row: row["hour"])
    return normalized


def _native_regional_cams_files(prepared):
    """Extract exact-valid-time CAMS files from native preparation."""
    cams = prepared.get("cams") if isinstance(prepared, dict) else None
    rows = cams.get("files") if isinstance(cams, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Rust regional preparation has no CAMS files")
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Rust regional CAMS file entry is invalid")
        requested = row.get("requested_valid_time")
        valid = row.get("valid_time")
        path = row.get("path")
        if (
            not isinstance(requested, str)
            or not isinstance(valid, str)
            or requested != valid
            or not isinstance(path, str)
            or not path
            or valid in seen
            or row.get("exact") is not True
        ):
            raise RuntimeError("Rust regional CAMS file identity is invalid")
        seen.add(valid)
        normalized.append({"valid_time": valid, "path": path})
    normalized.sort(key=lambda row: row["valid_time"])
    return normalized


def _native_regional_input_manifest(
    context,
    model,
    model_files,
    cams_files,
):
    """Reuse the current national cache manifest contract for native outputs."""
    from firesky.national_batch import (
        NationalBatchPrepared,
        build_national_input_manifest,
    )

    points = context.get("points")
    common = context.get("common")
    if not isinstance(points, list) or not isinstance(common, dict):
        raise RuntimeError("Rust regional context is incomplete")
    model_paths = [row["path"] for row in model_files]
    cams_paths = [row["path"] for row in cams_files]
    model_grid_points = [
        point
        for point in points
        if str(point.get("_elevation_source") or "").lower() == "model-grid"
    ]
    model_source = "gfs" if model == "gfs" else "ec"
    terrain_source = None
    if model_grid_points:
        terrain_source = {
            "source": f"{model_source}-orography",
            "forecast_hour": model_files[0]["hour"],
            "path": str(Path(model_files[0]["path"]).expanduser().resolve()),
        }
    prepared_stub = NationalBatchPrepared(
        grid=None,
        hours=[],
        rows=0,
        cols=0,
        lat0=0.0,
        lon0=0.0,
        spacing=0.25,
        latitudes=[],
        longitudes=[],
        cache_paths=model_paths,
        model_source=model_source,
    )
    cycle = context.get("cycle")
    if not isinstance(cycle, datetime):
        raise RuntimeError("Rust regional context cycle is invalid")
    manifest = build_national_input_manifest(
        prepared_stub,
        cams_paths,
        cycle,
        [row["hour"] for row in model_files],
        [float(value) for value in context["bbox"]],
        model_resolution="0p25",
        requested_aerosol_source="cams",
        actual_aerosol_sources=["cams"],
        compute_region=common.get("_compute_region"),
        compute_domain_key=str(common.get("_grid_region") or "custom"),
        terrain_source={
            "explicit_observer_count": len(points) - len(model_grid_points),
            "model_grid_observer_count": len(model_grid_points),
            "model_grid_source": terrain_source,
        },
    )
    return manifest


def _native_regional_result(
    root,
    request_path,
    request_payload,
    context,
    model,
    prepared,
    model_files,
    cams_files,
):
    """Read and validate the native regional frame artifact."""
    manifest_path = Path(root) / "manifest.json"
    bundle = load_tensor_bundle(
        manifest_path,
        kind="regional-frames-v1",
        source_path=request_path,
        mmap=True,
    )
    metadata = bundle.manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("Rust regional frame manifest metadata is invalid")
    artifact = metadata.get("frame_payload")
    if (
        not isinstance(artifact, dict)
        or artifact.get("contract") != "rust-regional-frames-v1"
        or artifact.get("public_forecast_complete") is not False
    ):
        raise RuntimeError("Rust regional frame artifact contract is incomplete")
    frame_path = _native_artifact_path(root, artifact, "forecast.json")
    try:
        document = json.loads(frame_path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Rust regional frame payload is invalid") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"results", "point_core", "frames"}
        or document.get("point_core") is not None
        or not isinstance(document.get("results"), list)
        or not isinstance(document.get("frames"), dict)
    ):
        raise RuntimeError("Rust regional frame payload identity is invalid")
    points = context.get("points")
    if not isinstance(points, list) or len(document["results"]) != len(points):
        raise RuntimeError("Rust regional result point count is invalid")
    frame_data = document["frames"]
    precision = frame_data.get("precision")
    if not isinstance(precision, dict):
        raise RuntimeError("Rust regional frame precision is invalid")
    prepared_result = prepared if isinstance(prepared, dict) else {}
    common = context.get("common") if isinstance(context.get("common"), dict) else {}
    try:
        from firesky.web_server import (
            NATIONAL_BATCH_ALGORITHM,
            _national_payload_uses_explicit_points,
            national_coverage_from_payload,
        )

        coverage = national_coverage_from_payload(request_payload)
        explicit_points = _national_payload_uses_explicit_points(request_payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Rust regional coverage identity is invalid") from exc
    timings = (
        metadata.get("timings_ms")
        if isinstance(metadata.get("timings_ms"), dict)
        else {}
    )
    performance = (
        frame_data.get("performance")
        if isinstance(frame_data.get("performance"), dict)
        else {}
    )
    prepare_ms = float(prepared_result.get("elapsed_ms") or 0.0)
    frame_seconds = float(performance.get("total_seconds") or 0.0)
    assembly_seconds = float(performance.get("assembly_seconds") or frame_seconds)
    native_ms = float(timings.get("native_assessment") or 0.0)
    tensor_seconds = (
        float(timings.get("model_profiles_and_solar") or 0.0)
        + float(timings.get("model_decode_and_source_hash") or 0.0)
        + float(timings.get("model_pack_and_derive") or 0.0)
        + float(timings.get("cams_assembly") or 0.0)
    ) / 1000.0
    result = {
        "scope": "regional",
        "point_mode": (
            "explicit-single-points"
            if explicit_points
            else "generated-regional-grid"
        ),
        "regions": [str(item["code"]) for item in coverage],
        "coverage": coverage,
        "target_date": str(request_payload["target_date"]),
        "phenomenon": str(request_payload["phenomenon"]),
        "model_source": model,
        "forecast_mode": "model",
        "compute_backend_requested": str(
            request_payload.get("compute_backend") or "auto"
        ),
        "compute_backend": "cpu",
        "precision": precision,
        "algorithm": NATIONAL_BATCH_ALGORITHM,
        "cycle_time_utc": context["cycle"].astimezone(timezone.utc).isoformat(),
        "point_count": len(document["results"]),
        "success_count": len(document["results"]),
        "error_count": 0,
        "points": document["results"],
        "frame_data": frame_data,
        "errors": [],
        "timing": {
            "prefetch_seconds": round(prepare_ms / 1000.0, 3),
            "tensor_seconds": round(tensor_seconds, 3),
            "compute_seconds": round(native_ms / 1000.0, 3),
            "frames_seconds": round(frame_seconds, 3),
            "frame_instant_scoring_seconds": float(
                performance.get("instant_scoring_seconds") or 0.0
            ),
            "frame_assembly_seconds": round(assembly_seconds, 3),
            "frame_points_per_second": float(
                performance.get("points_per_second") or 0.0
            ),
            "frame_samples_per_second": float(
                performance.get("samples_per_second") or 0.0
            ),
            "total_seconds": round(
                prepare_ms / 1000.0
                + tensor_seconds
                + native_ms / 1000.0
                + frame_seconds,
                3,
            ),
        },
        "input_manifest": _native_regional_input_manifest(
            context,
            model,
            model_files,
            cams_files,
        ),
        "cache_recommendation": (
            f"全国批处理使用已缓存 "
            f"{'GFS' if model == 'gfs' else 'ECMWF IFS'}/CAMS 文件；"
            "下载耗时单独计入 prefetch。"
        ),
        "native_forecast": {
            "contract": "rust-desktop-regional-forecast-v1",
            "runtime_python_used": False,
            "model_source": model,
            "point_payload_complete": True,
            "frame_payload_complete": True,
            "public_forecast_complete": False,
        },
    }
    return result


def forecast_regional(
    payload,
    *,
    model,
    config=None,
    progress=None,
    public=False,
):
    """Run a desktop regional forecast through the native frozen-input path."""
    from firesky.web_server import WebServerConfig

    config = config or WebServerConfig()
    model = str(model).strip().lower()
    if model not in {"gfs", "ec"}:
        raise ValueError("native desktop regional forecast requires gfs or ec")
    prepared_payload = dict(payload)
    prepared_payload.update(
        {
            "model_source": model,
            "forecast_mode": "model",
            "satellite_source": "off",
            "_aerosol_source": str(config.aerosol_source).strip().lower(),
        }
    )
    request_payload = {
        key: value
        for key, value in prepared_payload.items()
        if key != "force_refresh"
    }
    expected_contract = (
        "rust-public-regional-forecast-v1"
        if public
        else "rust-desktop-regional-forecast-v1"
    )
    if not prepared_payload.get("force_refresh"):
        try:
            from firesky.web_server import read_national_forecast_cache

            cached = read_national_forecast_cache(
                request_payload, config.regional_cache_dir
            )
            native = cached.get("native_forecast")
            if (
                isinstance(native, dict)
                and native.get("contract") == expected_contract
                and native.get("runtime_python_used") is False
                and native.get("public_forecast_complete") is bool(public)
            ):
                if progress:
                    progress(
                        {
                            "stage": "cache",
                            "percent": 99,
                            "message": f"{model.upper()} 命中 Rust 区域缓存",
                        }
                    )
                return cached
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            pass
    context = model_context(
        prepared_payload,
        regional=True,
        config=config,
        fast=False,
    )
    if progress:
        progress(
            {
                "stage": "native_context",
                "percent": 5,
                "message": f"{model.upper()} Rust 生成区域上下文",
            }
        )
    prepared = prepare_model_data(
        prepared_payload,
        regional=True,
        config=config,
        progress=progress,
    )
    model_files = _native_regional_model_files(prepared, model)
    cams_files = _native_regional_cams_files(prepared)
    if prepared.get("model_source") != model:
        raise RuntimeError("Rust regional preparation model source does not match context")
    prepared_cycle = prepared.get("cycle")
    if not isinstance(prepared_cycle, str):
        raise RuntimeError("Rust regional preparation cycle is missing")
    try:
        prepared_cycle = datetime.fromisoformat(
            prepared_cycle.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Rust regional preparation cycle is invalid") from exc
    context_cycle = context.get("cycle")
    if not isinstance(context_cycle, datetime):
        raise RuntimeError("Rust regional context cycle is invalid")
    if prepared_cycle != context_cycle.astimezone(timezone.utc):
        raise RuntimeError(
            "Rust regional context and preparation resolved different model cycles"
        )
    prepared_hours = prepared.get("hours")
    if (
        not isinstance(prepared_hours, list)
        or any(type(hour) is not int or hour < 0 for hour in prepared_hours)
        or sorted(set(prepared_hours)) != [row["hour"] for row in model_files]
    ):
        raise RuntimeError(
            "Rust regional preparation hours do not match resolved model files"
        )
    points = [_native_point_observer(point) for point in context.get("points", [])]
    if not points:
        raise RuntimeError("Rust regional context has no observers")
    spacing_value = prepared_payload.get("spacing_deg")
    point_spacing = (
        float(spacing_value)
        if (
            isinstance(spacing_value, (int, float))
            and not isinstance(spacing_value, bool)
            and float(spacing_value) > 0.0
        )
        else 0.25
    )
    request = {
        "model": "gfs" if model == "gfs" else "ecmwf-basic",
        "cycle_utc": context["cycle"].astimezone(timezone.utc).isoformat(),
        "date": str(prepared_payload["target_date"]),
        "phenomenon": str(prepared_payload["phenomenon"]),
        "points": points,
        "frames": model_files,
        "cams": cams_files,
        "radius_km": float(context["common"]["radius_km"]),
        "bbox": list(context["bbox"]),
        "include_point_core": False,
        "point_spacing_deg": point_spacing,
        "compute_backend_requested": str(
            prepared_payload.get("compute_backend") or "auto"
        ),
        # The stored-frame path already writes and verifies complete profile
        # children. A measured warm regional run was slower with the
        # point-oriented derived cache because every frame was revalidated and
        # reloaded before profile assembly; keep that cache for point requests.
        "derived_cache": None,
    }
    if progress:
        progress(
            {
                "stage": "native_score",
                "percent": 55,
                "message": f"{model.upper()} Rust 完成区域解码与评分",
            }
        )
    with tempfile.TemporaryDirectory(
        prefix="firesky-rust-desktop-regional-"
    ) as root:
        root = Path(root)
        request_path = root / "request.json"
        output = root / "output"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        value = call_engine(
            "build_stored_frames_to_files",
            {"path": str(request_path), "output_dir": str(output)},
            timeout_seconds=None,
        )
        if (
            not isinstance(value, dict)
            or value.get("manifest_path") != str(output / "manifest.json")
        ):
            raise RuntimeError("Rust regional forecast manifest identity is invalid")
        result = _native_regional_result(
            output,
            request_path,
            request_payload,
            context,
            model,
            prepared,
            model_files,
            cams_files,
        )
    if public:
        # The Rust frame artifact already contains every numeric field needed
        # by the public array-v6 projection. Validate that projection before
        # publishing a distinct public cache contract; this keeps the desktop
        # internal artifact and the public response boundary explicit.
        from firesky.web_server import (
            _external_national_forecast_summary,
            _national_forecast_summary,
        )

        validation_result = {
            **result,
            "cache": {
                "status": "refresh",
                "key": "native-public-validation",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "version": 125,
            },
        }
        if model == "ec":
            _external_national_forecast_summary(validation_result)
        else:
            _national_forecast_summary(validation_result)
        result["native_forecast"] = {
            **result["native_forecast"],
            "contract": expected_contract,
            "public_forecast_complete": True,
        }
    if progress:
        progress(
            {
                "stage": "native_finalize",
                "percent": 95,
                "message": f"{model.upper()} Rust 区域结果已整理",
            }
        )
    from firesky.web_server import write_national_forecast_cache

    cached = write_national_forecast_cache(
        request_payload,
        result,
        config.regional_cache_dir,
    )
    if progress:
        progress(
            {
                "stage": "native_finalize",
                "percent": 100,
                "message": f"{model.upper()} Rust 区域结果已缓存",
            }
        )
    return cached


def nowcast_context(payload, config=None, *, fast=False):
    """Resolve raw nowcast geometry and timing in Rust."""
    from firesky.web_server import WebServerConfig, forecast_request_from_payload, nowcast_payload
    config = config or WebServerConfig()
    prepared = nowcast_payload(payload)
    value = call_engine("desktop_nowcast_context", {
        "payload": prepared,
        "fast": bool(fast),
    }, timeout_seconds=None)
    if (not isinstance(value, dict)
            or value.get("contract") != "rust-desktop-nowcast-context-v1"
            or value.get("runtime_python_used") is not False):
        raise RuntimeError("Rust nowcast context response identity is invalid")
    reference = datetime.fromisoformat(value["reference"].replace("Z", "+00:00"))
    cycle = datetime.fromisoformat(value["cycle"].replace("Z", "+00:00"))
    anchor = datetime.fromisoformat(value["anchor"].replace("Z", "+00:00"))
    lookup = datetime.fromisoformat(value["lookup"].replace("Z", "+00:00"))
    prepared["reference_time_utc"] = reference.isoformat()
    prepared["_gfs_cycle_override"] = cycle.isoformat()
    request = forecast_request_from_payload(prepared)
    times = [datetime.fromisoformat(item.replace("Z", "+00:00")) for item in value["times"]]
    return {
        "request": request,
        "anchor": anchor,
        "reference": reference,
        "lookup": lookup,
        "cycle": cycle,
        "bbox": tuple(value["bbox"]),
        "times": times,
        "cams_valid_times": [
            datetime.fromisoformat(item.replace("Z", "+00:00"))
            for item in value.get("cams_valid_times", [])
        ],
        "region": value.get("region"),
        "area": list(value["area"]),
        "config": config,
    }


def nowcast_gfs_plan(context, valid_times):
    """Resolve the nowcast GFS prior footprint and forecast hours in Rust."""
    request = context["request"]
    value = call_engine("desktop_nowcast_gfs_plan", {
        "lat": float(request.point.lat),
        "lon": float(request.point.lon),
        "radius_km": float(request.radius_km),
        "cycle_utc": context["cycle"].astimezone(timezone.utc).isoformat(),
        "valid_times": [
            item.astimezone(timezone.utc).isoformat() for item in valid_times
        ],
    }, timeout_seconds=None)
    if (not isinstance(value, dict)
            or value.get("contract") != "rust-desktop-nowcast-gfs-plan-v1"
            or value.get("runtime_python_used") is not False):
        raise RuntimeError("Rust nowcast GFS plan response identity is invalid")
    cycle = datetime.fromisoformat(value["cycle"].replace("Z", "+00:00"))
    bbox = tuple(float(item) for item in value["bbox"])
    hours = [int(item) for item in value["hours"]]
    if len(bbox) != 4 or hours != sorted(set(hours)) or any(item < 0 for item in hours):
        raise RuntimeError("Rust nowcast GFS plan response is invalid")
    return {
        "cycle": cycle,
        "bbox": bbox,
        "hours": hours,
        "valid_times": [
            datetime.fromisoformat(item.replace("Z", "+00:00"))
            for item in value.get("valid_times", [])
        ],
    }


def prepare_nowcast_data(payload, *, config=None):
    """Prepare native GFS/CAMS inputs after the Python HSD cache fill."""
    from firesky.web_server import WebServerConfig, nowcast_payload

    config = config or WebServerConfig()
    request = {
        "payload": nowcast_payload(dict(payload)),
        "fast": False,
        "config": {
            "gfs_cache_dir": str(Path(config.gfs_cache_dir).resolve()),
            "cams_cache_dir": str(Path(config.cams_cache_dir).resolve()),
            "satellite_cache_dir": str(Path(config.satellite_cache_dir).resolve()),
        },
    }
    with tempfile.TemporaryDirectory(prefix="firesky-rust-nowcast-prepare-") as root:
        input_path = Path(root) / "request.json"
        input_path.write_text(
            json.dumps(request, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        value = call_engine("desktop_nowcast_prepare", {"path": str(input_path)})
    if (
        not isinstance(value, dict)
        or value.get("contract") != "rust-desktop-nowcast-prepare-v1"
        or value.get("runtime_python_used") is not False
        or value.get("status") != "ready"
        or not isinstance(value.get("hours"), list)
        or not value["hours"]
        or any(type(hour) is not int or hour < 0 for hour in value["hours"])
    ):
        raise RuntimeError("Rust nowcast preparation response identity is invalid")
    hsd = value.get("hsd")
    if (
        not isinstance(hsd, dict)
        or hsd.get("contract") != "rust-desktop-nowcast-hsd-cache-v1"
        or hsd.get("runtime_python_used") is not False
        or not isinstance(hsd.get("frames"), list)
        or len(hsd["frames"]) < 2
    ):
        raise RuntimeError("Rust nowcast preparation HSD response is invalid")
    cams = value.get("cams")
    if (
        not isinstance(cams, dict)
        or cams.get("contract") != "rust-cams-data-v1"
        or not isinstance(cams.get("missing_valid_times"), list)
        or cams["missing_valid_times"]
    ):
        raise RuntimeError("Rust nowcast preparation CAMS response is invalid")
    return value


def nowcast_hsd_cache(context, *, count=2):
    """Read and validate compact HSD frames in the Rust engine."""
    request = context["request"]
    value = call_engine("desktop_nowcast_hsd_cache", {
        "cache_dir": str(context["config"].satellite_cache_dir),
        "reference_time_utc": context["lookup"].astimezone(timezone.utc).isoformat(),
        "lat": float(request.point.lat),
        "lon": float(request.point.lon),
        "radius_km": float(request.radius_km),
        "count": int(count),
    }, timeout_seconds=None)
    if (not isinstance(value, dict)
            or value.get("contract") != "rust-desktop-nowcast-hsd-cache-v1"
            or value.get("runtime_python_used") is not False
            or not isinstance(value.get("frames"), list)):
        raise RuntimeError("Rust nowcast HSD cache response identity is invalid")
    frames = []
    for item in value["frames"]:
        if not isinstance(item, dict):
            raise RuntimeError("Rust nowcast HSD cache frame is invalid")
        valid = datetime.fromisoformat(
            str(item["valid_time_utc"]).replace("Z", "+00:00")
        )
        frames.append({
            "valid_time_utc": valid,
            "source": str(item.get("source") or ""),
            "quality": str(item.get("quality") or ""),
            "range_bytes": int(item.get("range_bytes") or 0),
            "range_blocks": int(item.get("range_blocks") or 0),
        })
    return frames


def nowcast_hsd_frames(
    cache_dir,
    *,
    reference_time_utc,
    lat,
    lon,
    radius_km,
    count=2,
    max_age_minutes=30.0,
):
    """Decode compact HSD frames in Rust and return calibrated frame objects."""
    from firesky.satellite_cloud_column import CalibratedSatelliteFrame
    from firesky.tensor_bundle import load_tensor_bundle

    output_dir = Path(cache_dir).resolve() / ".rust-bundles"
    output_dir.mkdir(parents=True, exist_ok=True)
    value = call_engine(
        "desktop_nowcast_hsd_export",
        {
            "cache_dir": str(Path(cache_dir).resolve()),
            "reference_time_utc": reference_time_utc,
            "lat": float(lat),
            "lon": float(lon),
            "radius_km": float(radius_km),
            "count": int(count),
            "max_age_minutes": float(max_age_minutes),
            "output_dir": str(output_dir),
        },
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or value.get("contract") != "rust-desktop-nowcast-hsd-export-v1"
        or value.get("runtime_python_used") is not False
        or not isinstance(value.get("frames"), list)
    ):
        raise RuntimeError("Rust nowcast HSD export response identity is invalid")
    frames = []
    expected_arrays = {
        "latitudes", "longitudes", "band_b07", "band_b13", "band_b15",
    }
    expected_metadata = {
        "cache_schema_version", "cache_algorithm", "frame_schema_version",
        "valid_time_utc", "ready_time_utc", "source", "quality",
        "range_bytes", "range_blocks", "target_lat", "target_lon",
        "radius_km", "projection_identity", "projection_hash",
        "bands", "units",
    }

    def bundle_version(manifest_path):
        paths = [manifest_path] + [
            manifest_path.parent / f"{name}.npy"
            for name in sorted(expected_arrays)
        ]
        identity = []
        for path in paths:
            try:
                info = path.lstat()
            except OSError:
                return None
            if not stat.S_ISREG(info.st_mode):
                return None
            identity.append((str(path), info.st_ino, info.st_size, info.st_mtime_ns))
        return tuple(identity)

    def remember_bundle(manifest_path, version, frame):
        bytes_used = sum(
            int(getattr(array, "nbytes", 0))
            for array in (
                frame.latitudes,
                frame.longitudes,
                *frame.bands.values(),
            )
        )
        if bytes_used > _HSD_BUNDLE_CACHE_MAX_BYTES:
            return
        key = str(manifest_path)
        with _HSD_BUNDLE_CACHE_LOCK:
            old = _HSD_BUNDLE_CACHE.pop(key, None)
            _HSD_BUNDLE_CACHE[key] = (version, frame, bytes_used)
            total = sum(item[2] for item in _HSD_BUNDLE_CACHE.values())
            while total > _HSD_BUNDLE_CACHE_MAX_BYTES and _HSD_BUNDLE_CACHE:
                _, (_, _, removed) = _HSD_BUNDLE_CACHE.popitem(last=False)
                total -= removed

    for item in value["frames"]:
        if not isinstance(item, dict):
            raise RuntimeError("Rust nowcast HSD export frame is invalid")
        manifest_path = Path(str(item.get("manifest_path") or ""))
        source_path = Path(str(item.get("path") or ""))
        if (
            not manifest_path.is_absolute()
            or not source_path.is_absolute()
            or not manifest_path.is_file()
            or not source_path.is_file()
        ):
            raise RuntimeError("Rust nowcast HSD export paths are invalid")
        version = bundle_version(manifest_path)
        if version is None:
            raise RuntimeError("Rust nowcast HSD export bundle files are invalid")
        with _HSD_BUNDLE_CACHE_LOCK:
            cached = _HSD_BUNDLE_CACHE.get(str(manifest_path))
            if cached is not None and cached[0] == version:
                _HSD_BUNDLE_CACHE.move_to_end(str(manifest_path))
                cached_frame = cached[1]
            else:
                cached_frame = None
        if cached_frame is not None:
            if (
                item.get("valid_time_utc") != cached_frame.valid_time_utc.isoformat()
                or item.get("source") != cached_frame.source
                or item.get("quality") != cached_frame.quality
            ):
                raise RuntimeError("Rust nowcast HSD cached frame identity is invalid")
            frames.append(cached_frame)
            continue
        bundle = load_tensor_bundle(
            manifest_path,
            kind="hsd-compact-frame-v1",
            source_path=source_path,
            mmap=False,
        )
        metadata = bundle.manifest.get("metadata")
        if (
            not isinstance(metadata, dict)
            or set(metadata) != expected_metadata
            or set(bundle.arrays) != expected_arrays
        ):
            raise RuntimeError("Rust nowcast HSD export bundle contract is invalid")
        arrays = bundle.arrays
        shapes = {
            name: tuple(array.shape)
            for name, array in arrays.items()
        }
        if (
            any(array.dtype != "<f4" for array in arrays.values())
            or any(array.ndim != 2 for array in arrays.values())
            or len(set(shapes.values())) != 1
        ):
            raise RuntimeError("Rust nowcast HSD export arrays are invalid")
        valid_time = datetime.fromisoformat(
            str(metadata["valid_time_utc"]).replace("Z", "+00:00")
        )
        if (
            item.get("valid_time_utc") != metadata["valid_time_utc"]
            or item.get("source") != metadata["source"]
            or item.get("quality") != metadata["quality"]
        ):
            raise RuntimeError("Rust nowcast HSD export metadata identity is invalid")
        frame = CalibratedSatelliteFrame(
            valid_time_utc=valid_time,
            latitudes=arrays["latitudes"],
            longitudes=arrays["longitudes"],
            bands={
                "B07": arrays["band_b07"],
                "B13": arrays["band_b13"],
                "B15": arrays["band_b15"],
            },
            units=dict(metadata["units"]),
            source=str(metadata["source"]),
            quality=str(metadata["quality"]),
            schema_version=int(metadata["frame_schema_version"]),
            range_bytes=int(metadata["range_bytes"]),
            range_blocks=int(metadata["range_blocks"]),
            native_manifest_path=manifest_path,
        )
        remember_bundle(manifest_path, version, frame)
        frames.append(frame)
    return tuple(frames)


def nowcast_hsd_motion(
    previous_manifest,
    latest_manifest,
    *,
    min_overlap=0.85,
    min_correlation=0.35,
):
    """Estimate HSD translation from source-bound native tensor bundles."""
    value = call_engine("desktop_nowcast_hsd_motion", {
        "previous_manifest": str(Path(previous_manifest).resolve()),
        "latest_manifest": str(Path(latest_manifest).resolve()),
        "min_overlap": float(min_overlap),
        "min_correlation": float(min_correlation),
    }, timeout_seconds=None)
    if (
        not isinstance(value, dict)
        or value.get("contract") != "rust-desktop-nowcast-hsd-motion-v1"
        or value.get("runtime_python_used") is not False
    ):
        raise RuntimeError("Rust nowcast HSD motion response identity is invalid")
    required = {
        "dx_px_per_hour", "dy_px_per_hour", "correlation", "overlap_ratio",
        "age_minutes", "quality", "previous_time_utc", "latest_time_utc",
    }
    if set(value) != {
        "contract", "runtime_python_used", *required,
    }:
        raise RuntimeError("Rust nowcast HSD motion response fields are invalid")
    result = {
        "dx_px_per_hour": float(value["dx_px_per_hour"]),
        "dy_px_per_hour": float(value["dy_px_per_hour"]),
        "correlation": float(value["correlation"]),
        "overlap_ratio": float(value["overlap_ratio"]),
        "age_minutes": float(value["age_minutes"]),
        "quality": str(value["quality"]),
        "previous_time_utc": datetime.fromisoformat(
            str(value["previous_time_utc"]).replace("Z", "+00:00")
        ),
        "latest_time_utc": datetime.fromisoformat(
            str(value["latest_time_utc"]).replace("Z", "+00:00")
        ),
    }
    if (
        result["quality"] not in {"strong", "unknown"}
        or not all(
            isinstance(result[key], float) and result[key] == result[key]
            for key in (
                "dx_px_per_hour", "dy_px_per_hour", "correlation",
                "overlap_ratio", "age_minutes",
            )
        )
    ):
        raise RuntimeError("Rust nowcast HSD motion diagnostics are invalid")
    return result


def nowcast_hsd_advection(frame, plan, motion, *, output_dir):
    """Project one exported HSD frame through native bilinear advection."""
    from firesky.satellite_cloud_column import CalibratedSatelliteFrame
    from firesky.tensor_bundle import load_tensor_bundle

    if frame.native_manifest_path is None:
        raise ValueError("Rust HSD advection requires a tensor-bundle manifest")
    manifest_path = Path(frame.native_manifest_path).resolve()
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    value = call_engine(
        "desktop_nowcast_hsd_advection",
        {
            "manifest_path": str(manifest_path),
            "output_dir": str(output_root),
            "target_time_utc": plan.target_time_utc.astimezone(timezone.utc).isoformat(),
            "horizon_hours": float(plan.horizon_hours),
            "scale": float(plan.scale),
            "applied": bool(plan.applied),
            "motion_dx_px_per_hour": float(motion.dx_px_per_hour),
            "motion_dy_px_per_hour": float(motion.dy_px_per_hour),
            "motion_quality": str(plan.motion_quality),
            "reason": str(plan.reason),
        },
        timeout_seconds=None,
    )
    expected_response = {
        "contract",
        "runtime_python_used",
        "manifest_path",
        "applied",
        "valid_time_utc",
        "source",
        "quality",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_response
        or value.get("contract") != "rust-desktop-nowcast-hsd-advection-v1"
        or value.get("runtime_python_used") is not False
        or value.get("applied") is not True
    ):
        raise RuntimeError("Rust nowcast HSD advection response identity is invalid")
    projected_manifest = Path(str(value.get("manifest_path") or ""))
    if not projected_manifest.is_absolute() or not projected_manifest.is_file():
        raise RuntimeError("Rust nowcast HSD advection manifest path is invalid")
    bundle = load_tensor_bundle(
        projected_manifest,
        kind="hsd-compact-frame-v1",
        source_path=manifest_path,
        mmap=False,
    )
    metadata = bundle.manifest.get("metadata")
    expected_metadata = {
        "cache_schema_version",
        "cache_algorithm",
        "frame_schema_version",
        "valid_time_utc",
        "ready_time_utc",
        "source",
        "quality",
        "range_bytes",
        "range_blocks",
        "target_lat",
        "target_lon",
        "radius_km",
        "projection_identity",
        "projection_hash",
        "bands",
        "units",
    }
    expected_arrays = {
        "latitudes",
        "longitudes",
        "band_b07",
        "band_b13",
        "band_b15",
    }
    if (
        not isinstance(metadata, dict)
        or set(metadata) != expected_metadata
        or set(bundle.arrays) != expected_arrays
    ):
        raise RuntimeError("Rust nowcast HSD advection bundle contract is invalid")
    arrays = bundle.arrays
    shapes = {name: tuple(array.shape) for name, array in arrays.items()}
    if (
        any(array.dtype != "<f4" for array in arrays.values())
        or any(array.ndim != 2 for array in arrays.values())
        or len(set(shapes.values())) != 1
    ):
        raise RuntimeError("Rust nowcast HSD advection arrays are invalid")
    if (
        value.get("valid_time_utc") != metadata["valid_time_utc"]
        or value.get("source") != metadata["source"]
        or value.get("quality") != metadata["quality"]
    ):
        raise RuntimeError("Rust nowcast HSD advection metadata identity is invalid")
    valid_time = datetime.fromisoformat(
        str(metadata["valid_time_utc"]).replace("Z", "+00:00")
    )
    return CalibratedSatelliteFrame(
        valid_time_utc=valid_time,
        latitudes=arrays["latitudes"],
        longitudes=arrays["longitudes"],
        bands={
            "B07": arrays["band_b07"],
            "B13": arrays["band_b13"],
            "B15": arrays["band_b15"],
        },
        units=dict(metadata["units"]),
        source=str(metadata["source"]),
        quality=str(metadata["quality"]),
        schema_version=int(metadata["frame_schema_version"]),
        range_bytes=int(metadata["range_bytes"]),
        range_blocks=int(metadata["range_blocks"]),
        native_manifest_path=projected_manifest,
    )


def nowcast_hsd_retrieval(frame, samples, *, azimuth_deg=0.0, include_metrics=False):
    """Run the calibrated HSD/GFS posterior in one native batch."""
    from firesky.satellite_cloud_column import (
        SATELLITE_CLOUD_COLUMN_ALGORITHM,
        SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
        SatelliteCloudColumn,
    )

    if frame.native_manifest_path is None:
        raise ValueError("Rust HSD retrieval requires a tensor-bundle manifest")
    request_samples = []
    for item in samples:
        prior = item["prior"]
        request_samples.append(
            {
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "distance_km": float(item["distance_km"]),
                "bearing_deg": float(item["bearing_deg"]),
                "b07": (
                    float(item["sample"]["B07"])
                    if item["sample"].get("B07") is not None
                    and math.isfinite(float(item["sample"]["B07"]))
                    else None
                ),
                "b13": (
                    float(item["sample"]["B13"])
                    if item["sample"].get("B13") is not None
                    and math.isfinite(float(item["sample"]["B13"]))
                    else None
                ),
                "b15": (
                    float(item["sample"]["B15"])
                    if item["sample"].get("B15") is not None
                    and math.isfinite(float(item["sample"]["B15"]))
                    else None
                ),
                "motion_dx_px_per_hour": float(item["motion_dx_px_per_hour"]),
                "motion_dy_px_per_hour": float(item["motion_dy_px_per_hour"]),
                "prior": {
                    "valid_time_utc": prior.valid_time_utc.astimezone(timezone.utc).isoformat(),
                    "source": str(prior.source),
                    "identity": str(prior.identity),
                    "terrain_asl_m": float(prior.terrain_asl_m),
                    "target_lat": float(prior.target_lat),
                    "target_lon": float(prior.target_lon),
                    "levels": [
                        {
                            "pressure_hpa": int(level.pressure_hpa),
                            "height_asl_m": float(level.height_asl_m),
                            "temperature_k": (
                                float(level.temperature_k)
                                if level.temperature_k is not None
                                else None
                            ),
                            "rh_percent": (
                                float(level.rh_percent)
                                if level.rh_percent is not None
                                else None
                            ),
                            "model_cloud_fraction": (
                                float(level.model_cloud_fraction)
                                if level.model_cloud_fraction is not None
                                else None
                            ),
                        }
                        for level in prior.levels
                    ],
                },
            }
        )
    value = call_engine(
        "desktop_nowcast_hsd_retrieval",
        {
            "manifest_path": str(Path(frame.native_manifest_path).resolve()),
            "samples": request_samples,
            "compact": True,
            "metrics": bool(include_metrics),
            "azimuth_deg": float(azimuth_deg),
        },
        timeout_seconds=None,
    )
    expected_contract = (
        "rust-desktop-nowcast-hsd-retrieval-v3"
        if include_metrics
        else "rust-desktop-nowcast-hsd-retrieval-v2"
    )
    expected_keys = {
        "contract",
        "runtime_python_used",
        "valid_time_utc",
        "source",
        "samples",
    }
    if include_metrics:
        expected_keys.add("metrics")
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("contract") != expected_contract
        or value.get("runtime_python_used") is not False
        or value.get("valid_time_utc") != frame.valid_time_utc.isoformat()
        or value.get("source") != frame.source
        or not isinstance(value.get("samples"), list)
        or len(value["samples"]) != len(samples)
    ):
        raise RuntimeError("Rust nowcast HSD retrieval response identity is invalid")
    metrics = None
    if include_metrics:
        expected_metric_fields = {
            "near_current",
            "far_current",
            "near_projected",
            "far_projected",
            "near_previous",
            "far_previous",
            "local_cloud_ratio",
            "horizon_cloud_ratio",
            "thin_cloud_ratio",
            "thick_cloud_ratio",
            "cloud_texture_score",
            "cloud_uniformity_score",
            "cloud_blanket_score",
            "horizon_clearance_score",
            "composition_score",
        }
        raw_metrics = value.get("metrics")
        if (
            not isinstance(raw_metrics, dict)
            or set(raw_metrics) != expected_metric_fields
            or any(
                not isinstance(number, (int, float))
                or isinstance(number, bool)
                or not math.isfinite(float(number))
                for number in raw_metrics.values()
            )
        ):
            raise RuntimeError("Rust nowcast HSD retrieval metrics are invalid")
        metrics = {key: float(raw_metrics[key]) for key in expected_metric_fields}
    columns_by_sample = []
    expected_compact_fields = {
        "base_p10_asl_m",
        "base_p50_asl_m",
        "base_p90_asl_m",
        "top_asl_m",
        "phase",
        "optical_depth",
        "cloud_probability_by_height",
        "horizontal_cloud_probability",
        "low_cloud_state",
        "retrieval_quality",
        "cloud_type",
        "base_source",
    }
    for request_sample, item in zip(samples, value["samples"], strict=True):
        if not isinstance(item, dict) or set(item) != {"columns"} or not isinstance(
            item["columns"], list
        ):
            raise RuntimeError("Rust nowcast HSD retrieval sample response is invalid")
        prior = request_sample["prior"]
        columns = []
        for raw in item["columns"]:
            if not isinstance(raw, dict) or set(raw) != expected_compact_fields:
                raise RuntimeError("Rust nowcast HSD retrieval column is invalid")
            raw = {
                **dict(raw),
                "schema_version": SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
                "algorithm": SATELLITE_CLOUD_COLUMN_ALGORITHM,
                "valid_time_utc": frame.valid_time_utc,
                "lat": float(request_sample["lat"]),
                "lon": float(request_sample["lon"]),
                "distance_km": float(request_sample["distance_km"]),
                "bearing_deg": float(request_sample["bearing_deg"]) % 360.0,
                "terrain_asl_m": float(prior.terrain_asl_m),
                "source": frame.source,
                "vertical_prior_identity": str(prior.identity),
                "vertical_prior_source": str(prior.source),
                "motion_dx_px_per_hour": float(
                    request_sample["motion_dx_px_per_hour"]
                ),
                "motion_dy_px_per_hour": float(
                    request_sample["motion_dy_px_per_hour"]
                ),
            }
            profile = raw.get("cloud_probability_by_height")
            if not isinstance(profile, dict):
                raise RuntimeError("Rust nowcast HSD retrieval profile is invalid")
            raw["cloud_probability_by_height"] = {
                float(height): float(probability)
                for height, probability in profile.items()
            }
            try:
                columns.append(SatelliteCloudColumn(**raw))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Rust nowcast HSD retrieval column contract is invalid") from exc
        columns_by_sample.append(tuple(columns))
    result = tuple(columns_by_sample)
    return (result, metrics) if include_metrics else result


def _encode_gfs_prior_feature(feature):
    return {
        "lat": float(feature.lat),
        "lon": float(feature.lon),
        "pressure_hpa": int(feature.pressure_hpa),
        "geopotential_height_m": float(feature.geopotential_height_m),
        "rh_percent": float(feature.rh_percent),
        "temperature_k": (
            float(feature.temperature_k)
            if feature.temperature_k is not None
            else None
        ),
        "cloud_fraction_percent": (
            float(feature.cloud_fraction_percent)
            if feature.cloud_fraction_percent is not None
            else None
        ),
    }


def _encode_gfs_prior_request(request):
    return {
        "valid_time_utc": request["valid_time_utc"].astimezone(timezone.utc).isoformat(),
        "cycle_time_utc": request["cycle_time_utc"].astimezone(timezone.utc).isoformat(),
        "forecast_hour": int(request["forecast_hour"]),
        "source": str(request["source"]),
        "identity": str(request["identity"]),
        "terrain_left_asl_m": float(request["terrain_left_asl_m"]),
        "terrain_right_asl_m": float(request["terrain_right_asl_m"]),
        "target_lat": float(request["target_lat"]),
        "target_lon": float(request["target_lon"]),
        "fraction": float(request["fraction"]),
        "left_features": [
            _encode_gfs_prior_feature(feature) for feature in request["left_features"]
        ],
        "right_features": [
            _encode_gfs_prior_feature(feature) for feature in request["right_features"]
        ],
    }


def _encode_gfs_prior_shared_batch(request):
    return {
        "valid_time_utc": request["valid_time_utc"].astimezone(timezone.utc).isoformat(),
        "cycle_time_utc": request["cycle_time_utc"].astimezone(timezone.utc).isoformat(),
        "forecast_hour": int(request["forecast_hour"]),
        "source": str(request["source"]),
        "fraction": float(request["fraction"]),
        "left_features": [
            _encode_gfs_prior_feature(feature) for feature in request["left_features"]
        ],
        "right_features": [
            _encode_gfs_prior_feature(feature) for feature in request["right_features"]
        ],
        "requests": [
            {
                "identity": str(item["identity"]),
                "terrain_left_asl_m": float(item["terrain_left_asl_m"]),
                "terrain_right_asl_m": float(item["terrain_right_asl_m"]),
                "target_lat": float(item["target_lat"]),
                "target_lon": float(item["target_lon"]),
            }
            for item in request["requests"]
        ],
    }


def _decode_gfs_prior_response(value, request):
    valid_time_utc = request["valid_time_utc"]
    cycle_time_utc = request["cycle_time_utc"]
    forecast_hour = request["forecast_hour"]
    source = request["source"]
    identity = request["identity"]
    expected_keys = {
        "contract",
        "runtime_python_used",
        "valid_time_utc",
        "cycle_time_utc",
        "forecast_hour",
        "source",
        "identity",
        "terrain_asl_m",
        "target_lat",
        "target_lon",
        "levels",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("contract") != "rust-desktop-nowcast-gfs-prior-v1"
        or value.get("runtime_python_used") is not False
        or value.get("valid_time_utc") != valid_time_utc.astimezone(timezone.utc).isoformat()
        or value.get("cycle_time_utc") != cycle_time_utc.astimezone(timezone.utc).isoformat()
        or value.get("forecast_hour") != int(forecast_hour)
        or value.get("source") != str(source)
        or value.get("identity") != str(identity)
        or not isinstance(value.get("levels"), list)
    ):
        raise RuntimeError("Rust nowcast GFS prior response identity is invalid")
    from firesky.satellite_cloud_column import VerticalPrior, VerticalPriorLevel

    levels = []
    for raw in value["levels"]:
        if (
            not isinstance(raw, dict)
            or set(raw)
            != {
                "pressure_hpa",
                "height_asl_m",
                "temperature_k",
                "rh_percent",
                "model_cloud_fraction",
            }
        ):
            raise RuntimeError("Rust nowcast GFS prior level response is invalid")
        try:
            levels.append(
                VerticalPriorLevel(
                    pressure_hpa=int(raw["pressure_hpa"]),
                    height_asl_m=float(raw["height_asl_m"]),
                    temperature_k=(
                        float(raw["temperature_k"])
                        if raw["temperature_k"] is not None
                        else None
                    ),
                    rh_percent=(
                        float(raw["rh_percent"])
                        if raw["rh_percent"] is not None
                        else None
                    ),
                    model_cloud_fraction=(
                        float(raw["model_cloud_fraction"])
                        if raw["model_cloud_fraction"] is not None
                        else None
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Rust nowcast GFS prior level contract is invalid") from exc
    try:
        return VerticalPrior(
            valid_time_utc=valid_time_utc.astimezone(timezone.utc),
            cycle_time_utc=cycle_time_utc.astimezone(timezone.utc),
            forecast_hour=int(forecast_hour),
            source=str(value["source"]),
            terrain_asl_m=float(value["terrain_asl_m"]),
            levels=tuple(levels),
            identity=str(value["identity"]),
            target_lat=float(value["target_lat"]),
            target_lon=float(value["target_lon"]),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Rust nowcast GFS prior contract is invalid") from exc


def nowcast_gfs_prior(**request):
    """Construct one interpolated GFS vertical prior in Rust."""
    value = call_engine(
        "desktop_nowcast_prior",
        _encode_gfs_prior_request(request),
        timeout_seconds=None,
    )
    return _decode_gfs_prior_response(value, request)


def nowcast_gfs_priors(requests):
    """Construct many interpolated GFS vertical priors in one native call."""
    request_list = list(requests)
    if not request_list or len(request_list) > 128:
        raise ValueError("Rust nowcast GFS prior batch must contain 1..128 requests")
    value = call_engine(
        "desktop_nowcast_prior_batch",
        {"requests": [_encode_gfs_prior_request(request) for request in request_list]},
        timeout_seconds=None,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "runtime_python_used", "results"}
        or value.get("contract") != "rust-desktop-nowcast-gfs-prior-batch-v1"
        or value.get("runtime_python_used") is not False
        or not isinstance(value.get("results"), list)
        or len(value["results"]) != len(request_list)
    ):
        raise RuntimeError("Rust nowcast GFS prior batch response identity is invalid")
    return tuple(
        _decode_gfs_prior_response(response, request)
        for response, request in zip(value["results"], request_list, strict=True)
    )


def nowcast_gfs_prior_shared_batch(
    *,
    valid_time_utc,
    cycle_time_utc,
    forecast_hour,
    source,
    fraction,
    left_features,
    right_features,
    requests,
):
    """Construct priors with one shared feature interpolation in Rust."""
    request_list = list(requests)
    if not request_list or len(request_list) > 128:
        raise ValueError("Rust shared GFS prior batch must contain 1..128 requests")
    common = {
        "valid_time_utc": valid_time_utc,
        "cycle_time_utc": cycle_time_utc,
        "forecast_hour": int(forecast_hour),
        "source": str(source),
        "fraction": float(fraction),
        "left_features": left_features,
        "right_features": right_features,
        "requests": request_list,
    }
    value = call_engine(
        "desktop_nowcast_prior_shared_batch",
        _encode_gfs_prior_shared_batch(common),
        timeout_seconds=None,
    )
    expected_common = {
        "contract",
        "runtime_python_used",
        "valid_time_utc",
        "cycle_time_utc",
        "forecast_hour",
        "source",
        "results",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_common
        or value.get("contract") != "rust-desktop-nowcast-gfs-prior-shared-batch-v1"
        or value.get("runtime_python_used") is not False
        or value.get("valid_time_utc")
        != valid_time_utc.astimezone(timezone.utc).isoformat()
        or value.get("cycle_time_utc")
        != cycle_time_utc.astimezone(timezone.utc).isoformat()
        or value.get("forecast_hour") != int(forecast_hour)
        or value.get("source") != str(source)
        or not isinstance(value.get("results"), list)
        or len(value["results"]) != len(request_list)
    ):
        raise RuntimeError("Rust shared GFS prior batch response identity is invalid")
    decode_requests = [
        {
            **common,
            **item,
        }
        for item in request_list
    ]
    return tuple(
        _decode_gfs_prior_response(response, request)
        for response, request in zip(value["results"], decode_requests, strict=True)
    )


def nowcast_gfs_prior_decoded_batch(
    *,
    valid_time_utc,
    cycle_time_utc,
    left_forecast_hour,
    right_forecast_hour,
    source,
    fraction,
    local_radius_km,
    left_cache_path,
    right_cache_path,
    domain_hash,
    resolution,
    points,
):
    """Build priors directly from validated decoded-grid sidecars in Rust."""
    point_list = list(points)
    if not point_list or len(point_list) > 2000:
        raise ValueError("Rust decoded GFS prior batch must contain 1..2000 points")
    value = call_engine(
        "desktop_nowcast_prior_decoded_batch",
        {
            "valid_time_utc": valid_time_utc.astimezone(timezone.utc).isoformat(),
            "cycle_time_utc": cycle_time_utc.astimezone(timezone.utc).isoformat(),
            "left_forecast_hour": int(left_forecast_hour),
            "right_forecast_hour": int(right_forecast_hour),
            "source": str(source),
            "fraction": float(fraction),
            "local_radius_km": float(local_radius_km),
            "left_cache_path": str(Path(left_cache_path).resolve()),
            "right_cache_path": str(Path(right_cache_path).resolve()),
            "domain_hash": str(domain_hash),
            "resolution": str(resolution),
            "requests": [
                {
                    "target_lat": float(point.lat),
                    "target_lon": float(point.lon),
                }
                for point in point_list
            ],
        },
        timeout_seconds=None,
    )
    expected_keys = {
        "contract",
        "runtime_python_used",
        "valid_time_utc",
        "cycle_time_utc",
        "forecast_hour",
        "source",
        "results",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("contract")
        != "rust-desktop-nowcast-gfs-prior-decoded-batch-v1"
        or value.get("runtime_python_used") is not False
        or value.get("valid_time_utc")
        != valid_time_utc.astimezone(timezone.utc).isoformat()
        or value.get("cycle_time_utc")
        != cycle_time_utc.astimezone(timezone.utc).isoformat()
        or value.get("forecast_hour") != int(left_forecast_hour)
        or value.get("source") != str(source)
        or not isinstance(value.get("results"), list)
        or len(value["results"]) != len(point_list)
    ):
        raise RuntimeError("Rust decoded GFS prior batch response identity is invalid")
    decode_requests = [
        {
            "valid_time_utc": valid_time_utc,
            "cycle_time_utc": cycle_time_utc,
            "forecast_hour": int(left_forecast_hour),
            "source": str(source),
            "identity": (
                f"gfs-prior:{cycle_time_utc.astimezone(timezone.utc):%Y%m%d%H}:"
                f"f{int(left_forecast_hour):03d}-{int(right_forecast_hour):03d}:"
                f"{resolution}:domain={domain_hash}:"
                f"point={float(point.lat):+.5f},{float(point.lon):+.5f}"
            ),
            "target_lat": float(point.lat),
            "target_lon": float(point.lon),
        }
        for point in point_list
    ]
    return tuple(
        _decode_gfs_prior_response(response, request)
        for response, request in zip(value["results"], decode_requests, strict=True)
    )


def nowcast_calibrated_retrieval(
    *,
    frame,
    point,
    azimuth_deg,
    radius_km,
    retrieval_context,
    motion_dx_px_per_hour=0.0,
    motion_dy_px_per_hour=0.0,
    return_priors=False,
    return_timings=False,
):
    """Run calibrated HSD sampling, GFS priors and posterior retrieval in Rust."""
    if frame.native_manifest_path is None:
        raise ValueError("native calibrated retrieval requires a tensor-bundle frame")
    required = {
        "cycle_time_utc",
        "left_forecast_hour",
        "right_forecast_hour",
        "fraction",
        "left_cache_path",
        "right_cache_path",
        "local_radius_km",
        "domain_hash",
        "resolution",
    }
    if not isinstance(retrieval_context, dict) or set(retrieval_context) != required:
        raise ValueError("native calibrated retrieval context is invalid")
    from firesky.satellite import satellite_sample_points

    samples = satellite_sample_points(point, azimuth_deg, radius_km)
    with tempfile.TemporaryDirectory(prefix="firesky-rust-nowcast-retrieval-") as root:
        output_path = Path(root) / "retrieval.json"
        response = call_engine(
            "desktop_nowcast_calibrated_retrieval",
            {
            "manifest_path": str(frame.native_manifest_path.resolve()),
            "valid_time_utc": frame.valid_time_utc.astimezone(timezone.utc).isoformat(),
            "point_lat": float(point.lat),
            "point_lon": float(point.lon),
            "azimuth_deg": float(azimuth_deg),
            "radius_km": float(radius_km),
            "motion_dx_px_per_hour": float(motion_dx_px_per_hour),
            "motion_dy_px_per_hour": float(motion_dy_px_per_hour),
            "cycle_time_utc": retrieval_context["cycle_time_utc"].astimezone(
                timezone.utc
            ).isoformat(),
            "left_forecast_hour": int(retrieval_context["left_forecast_hour"]),
            "right_forecast_hour": int(retrieval_context["right_forecast_hour"]),
            "source": "gfs-interpolated-vertical-prior",
            "fraction": float(retrieval_context["fraction"]),
            "local_radius_km": float(retrieval_context["local_radius_km"]),
            "left_cache_path": str(Path(retrieval_context["left_cache_path"]).resolve()),
            "right_cache_path": str(Path(retrieval_context["right_cache_path"]).resolve()),
            "domain_hash": str(retrieval_context["domain_hash"]),
            "resolution": str(retrieval_context["resolution"]),
                "output_path": str(output_path),
            },
            timeout_seconds=None,
        )
        expected_receipt = {
            "contract",
            "runtime_python_used",
            "output_path",
            "bytes",
            "sha256",
        }
        if (
            not isinstance(response, dict)
            or set(response) != expected_receipt
            or response.get("contract")
            != "rust-desktop-nowcast-calibrated-retrieval-file-v1"
            or response.get("runtime_python_used") is not False
            or response.get("output_path") != str(output_path)
        ):
            raise RuntimeError("Rust calibrated retrieval receipt is invalid")
        try:
            payload = output_path.read_bytes()
        except OSError as exc:
            raise RuntimeError("Rust calibrated retrieval output is unavailable") from exc
        if (
            type(response.get("bytes")) is not int
            or response["bytes"] != len(payload)
            or len(payload) > 128 * 1024 * 1024
            or response.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            raise RuntimeError("Rust calibrated retrieval output identity is invalid")
        try:
            value = json.loads(payload)
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Rust calibrated retrieval output is invalid JSON") from exc
    expected_keys = {
        "contract",
        "runtime_python_used",
        "features",
        "columns",
        "metrics",
        "priors",
        "timings_ms",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("contract")
        != "rust-desktop-nowcast-calibrated-retrieval-v1"
        or value.get("runtime_python_used") is not False
        or not isinstance(value.get("features"), list)
        or not isinstance(value.get("columns"), list)
        or not isinstance(value.get("metrics"), dict)
        or not isinstance(value.get("priors"), list)
        or len(value["priors"]) != len(samples)
        or not isinstance(value.get("timings_ms"), dict)
        or set(value["timings_ms"]) != {
            "hsd_frame_load",
            "hsd_sampling",
            "gfs_grid_load",
            "retrieval",
            "assembly",
        }
        or not all(
            type(timing) in {int, float} and math.isfinite(float(timing)) and timing >= 0.0
            for timing in value["timings_ms"].values()
        )
    ):
        raise RuntimeError("Rust calibrated retrieval response identity is invalid")
    from firesky.satellite_cloud_column import (
        SatelliteCloudColumn,
        SatelliteCloudColumnSet,
    )
    from firesky.models import GridCellFeature
    features = [GridCellFeature(**item) for item in value["features"]]
    columns = []
    for raw in value["columns"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("columns"), list):
            raise RuntimeError("Rust calibrated column set is invalid")
        column_sets = []
        for column in raw["columns"]:
            if not isinstance(column, dict):
                raise RuntimeError("Rust calibrated cloud column is invalid")
            normalized = dict(column)
            normalized["valid_time_utc"] = datetime.fromisoformat(
                str(normalized["valid_time_utc"]).replace("Z", "+00:00")
            )
            profile = normalized.get("cloud_probability_by_height")
            if not isinstance(profile, dict):
                raise RuntimeError("Rust calibrated cloud profile is invalid")
            normalized["cloud_probability_by_height"] = {
                float(height): float(probability)
                for height, probability in profile.items()
            }
            try:
                column_sets.append(SatelliteCloudColumn(**normalized))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Rust calibrated cloud column contract is invalid") from exc
        columns.append(
            SatelliteCloudColumnSet(
                schema_version=int(raw["schema_version"]),
                valid_time_utc=datetime.fromisoformat(
                    str(raw["valid_time_utc"]).replace("Z", "+00:00")
                ),
                source=str(raw["source"]),
                vertical_prior_identity=str(raw["vertical_prior_identity"]),
                retrieval_quality=str(raw["retrieval_quality"]),
                low_cloud_state=str(raw["low_cloud_state"]),
                columns=tuple(column_sets),
                motion_dx_px_per_hour=float(raw["motion_dx_px_per_hour"]),
                motion_dy_px_per_hour=float(raw["motion_dy_px_per_hour"]),
            )
        )
    priors = []
    from firesky.rust_desktop_data import _decode_gfs_prior_response
    from firesky.geometry import destination_point

    targets = [destination_point(point, bearing, distance) for bearing, distance in samples]
    for target, raw in zip(targets, value["priors"], strict=True):
        if raw is None:
            priors.append((float(target.lat), float(target.lon), None))
            continue
        if not isinstance(raw, dict) or set(raw) != {
            "valid_time_utc",
            "source",
            "identity",
            "terrain_asl_m",
            "target_lat",
            "target_lon",
            "levels",
        }:
            raise RuntimeError("Rust calibrated retrieval prior is invalid")
        embedded = {
            **raw,
            "contract": "rust-desktop-nowcast-gfs-prior-v1",
            "runtime_python_used": False,
            "cycle_time_utc": retrieval_context["cycle_time_utc"]
            .astimezone(timezone.utc)
            .isoformat(),
            "forecast_hour": int(retrieval_context["left_forecast_hour"]),
        }
        try:
            prior = _decode_gfs_prior_response(embedded, {
                "valid_time_utc": frame.valid_time_utc,
                "cycle_time_utc": retrieval_context["cycle_time_utc"],
                "forecast_hour": int(retrieval_context["left_forecast_hour"]),
                "source": "gfs-interpolated-vertical-prior",
                "identity": str(raw["identity"]),
                "target_lat": float(target.lat),
                "target_lon": float(target.lon),
            })
            priors.append((float(target.lat), float(target.lon), prior))
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError("Rust calibrated retrieval prior is invalid") from exc
    result = [features, columns]
    if return_priors:
        result.extend((priors, value["metrics"]))
    else:
        result.append(value["metrics"])
    if return_timings:
        result.append(value["timings_ms"])
    return tuple(result)


def nowcast_adjustment(
    *,
    target_time_utc,
    timezone_name,
    point,
    azimuth_deg,
    radius_km,
    display_frame,
    frames,
    retrieval_context_provider,
    source_times_utc,
    motion,
    advection,
    single_historical_observation,
):
    """Assemble one multi-scan calibrated nowcast entirely in Rust."""
    if display_frame.native_manifest_path is None:
        raise ValueError("Rust nowcast adjustment requires a display tensor-bundle frame")
    normalized_frames = tuple(frames)
    if not 1 <= len(normalized_frames) <= 16:
        raise ValueError("Rust nowcast adjustment requires 1..16 observation frames")
    required_context = {
        "cycle_time_utc",
        "left_forecast_hour",
        "right_forecast_hour",
        "fraction",
        "left_cache_path",
        "right_cache_path",
        "local_radius_km",
        "domain_hash",
        "resolution",
    }
    encoded_frames = []
    context_cache = {}

    def retrieval_context(valid_time):
        key = valid_time.astimezone(timezone.utc)
        if key not in context_cache:
            context_cache[key] = retrieval_context_provider(key)
        return context_cache[key]

    def encode_context(value):
        if not isinstance(value, dict) or set(value) != required_context:
            raise ValueError("Rust nowcast retrieval context is unavailable")
        return {
            "cycle_time_utc": value["cycle_time_utc"]
            .astimezone(timezone.utc)
            .isoformat(),
            "left_forecast_hour": int(value["left_forecast_hour"]),
            "right_forecast_hour": int(value["right_forecast_hour"]),
            "fraction": float(value["fraction"]),
            "left_cache_path": str(Path(value["left_cache_path"]).resolve()),
            "right_cache_path": str(Path(value["right_cache_path"]).resolve()),
            "local_radius_km": float(value["local_radius_km"]),
            "domain_hash": str(value["domain_hash"]),
            "resolution": str(value["resolution"]),
        }

    for frame in normalized_frames:
        if frame.native_manifest_path is None:
            raise ValueError("Rust nowcast adjustment requires tensor-bundle observation frames")
        encoded_frames.append(
            {
                "manifest_path": str(Path(frame.native_manifest_path).resolve()),
                "valid_time_utc": frame.valid_time_utc.astimezone(timezone.utc).isoformat(),
                "retrieval": encode_context(retrieval_context(frame.valid_time_utc)),
            }
        )
    display_retrieval = encode_context(retrieval_context(display_frame.valid_time_utc))
    request = {
        "target_time_utc": target_time_utc.astimezone(timezone.utc).isoformat(),
        "timezone_name": str(timezone_name),
        "point_lat": float(point.lat),
        "point_lon": float(point.lon),
        "radius_km": float(radius_km),
        "azimuth_deg": float(azimuth_deg),
        "display_frame": {
            "manifest_path": str(Path(display_frame.native_manifest_path).resolve()),
            "valid_time_utc": display_frame.valid_time_utc.astimezone(
                timezone.utc
            ).isoformat(),
            "retrieval": display_retrieval,
        },
        "frames": encoded_frames,
        "source_times_utc": [
            value.astimezone(timezone.utc).isoformat() for value in tuple(source_times_utc)
        ],
        "motion_dx_px_per_hour": (
            float(motion.dx_px_per_hour)
            if motion is not None and motion.quality == "strong"
            else 0.0
        ),
        "motion_dy_px_per_hour": (
            float(motion.dy_px_per_hour)
            if motion is not None and motion.quality == "strong"
            else 0.0
        ),
        "motion_correlation": float(motion.correlation) if motion is not None else 0.0,
        "motion_quality": str(motion.quality) if motion is not None else "unknown",
        "motion_age_minutes": (
            float(motion.age_minutes)
            if motion is not None and motion.age_minutes is not None
            else None
        ),
        "advection": (
            {
                "horizon_hours": float(advection.horizon_hours),
                "scale": float(advection.scale),
                "applied": bool(advection.applied),
                "reason": str(advection.reason),
                "motion_age_minutes": (
                    float(advection.motion_age_minutes)
                    if advection.motion_age_minutes is not None
                    else None
                ),
                "motion_quality": str(advection.motion_quality),
            }
            if advection is not None
            else None
        ),
        "single_historical_observation": bool(single_historical_observation),
    }
    with tempfile.TemporaryDirectory(prefix="firesky-rust-nowcast-adjustment-") as root:
        root = Path(root)
        request_path = root / "request.json"
        output_path = root / "adjustment.json"
        payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        request_path.write_bytes(payload)
        response = call_engine(
            "desktop_nowcast_adjustment",
            {"request_path": str(request_path), "output_path": str(output_path)},
            timeout_seconds=None,
        )
        expected_receipt = {
            "contract",
            "runtime_python_used",
            "output_path",
            "bytes",
            "sha256",
            "request_sha256",
        }
        if (
            not isinstance(response, dict)
            or set(response) != expected_receipt
            or response.get("contract")
            != "rust-desktop-nowcast-adjustment-file-v1"
            or response.get("runtime_python_used") is not False
            or response.get("output_path") != str(output_path)
        ):
            raise RuntimeError("Rust nowcast adjustment receipt is invalid")
        if (
            type(response.get("bytes")) is not int
            or type(response.get("request_sha256")) is not str
            or response["request_sha256"] != hashlib.sha256(payload).hexdigest()
        ):
            raise RuntimeError("Rust nowcast adjustment request identity is invalid")
        try:
            encoded = output_path.read_bytes()
        except OSError as exc:
            raise RuntimeError("Rust nowcast adjustment output is unavailable") from exc
        if (
            response["bytes"] != len(encoded)
            or len(encoded) > 128 * 1024 * 1024
            or response.get("sha256") != hashlib.sha256(encoded).hexdigest()
        ):
            raise RuntimeError("Rust nowcast adjustment output identity is invalid")
        try:
            value = json.loads(encoded)
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Rust nowcast adjustment output is invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or set(value) != _NOWCAST_ADJUSTMENT_FIELDS
        or value.get("contract") != "rust-desktop-nowcast-adjustment-v1"
        or value.get("runtime_python_used") is not False
        or value.get("nowcast_algorithm") != "satellite-nowcast-2026.11.04.87-hsd-only-multi-observation-aerosol-ground-touchdown-coverage-gate-v4"
        or value.get("source") != "noaa-himawari9-hsd-calibrated+gfs-prior"
        or value.get("image_channel") != "hsd-b07+b13+b15"
        or value.get("image_area") != "Himawari HSD segmented footprint"
        or value.get("cloud_column_schema_version") != 4
    ):
        raise RuntimeError("Rust nowcast adjustment response identity is invalid")
    return value


def nowcast_adjustment_as_model(value):
    """Rebuild the public nowcast adjustment from a validated native payload."""
    from firesky.models import GridCellFeature, NowcastAdjustment
    from firesky.satellite_cloud_column import (
        SatelliteCloudColumn,
        SatelliteCloudColumnSet,
    )

    if not isinstance(value, dict) or set(value) != _NOWCAST_ADJUSTMENT_FIELDS:
        raise RuntimeError("Rust nowcast adjustment model payload is invalid")
    column_sets = []
    for raw_set in value["cloud_column_sets"]:
        if not isinstance(raw_set, dict) or set(raw_set) != {
            "schema_version",
            "valid_time_utc",
            "source",
            "vertical_prior_identity",
            "retrieval_quality",
            "low_cloud_state",
            "columns",
            "motion_dx_px_per_hour",
            "motion_dy_px_per_hour",
        }:
            raise RuntimeError("Rust nowcast cloud-column set is invalid")
        columns = []
        for raw in raw_set["columns"]:
            if (
                not isinstance(raw, dict)
                or set(raw) != _CLOUD_COLUMN_PAYLOAD_FIELDS
            ):
                raise RuntimeError("Rust nowcast cloud column is invalid")
            normalized = {
                key: value
                for key, value in raw.items()
                if key not in _CLOUD_COLUMN_DERIVED_AGL_FIELDS
            }
            normalized["valid_time_utc"] = _parse_native_utc(
                raw["valid_time_utc"], "cloud column"
            )
            normalized["cloud_probability_by_height"] = {
                float(height): float(probability)
                for height, probability in raw["cloud_probability_by_height"].items()
            }
            try:
                columns.append(SatelliteCloudColumn(**normalized))
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("Rust nowcast cloud column contract is invalid") from exc
        try:
            column_sets.append(
                SatelliteCloudColumnSet(
                    schema_version=int(raw_set["schema_version"]),
                    valid_time_utc=_parse_native_utc(
                        raw_set["valid_time_utc"], "cloud-column set"
                    ),
                    source=str(raw_set["source"]),
                    vertical_prior_identity=str(raw_set["vertical_prior_identity"]),
                    retrieval_quality=str(raw_set["retrieval_quality"]),
                    low_cloud_state=str(raw_set["low_cloud_state"]),
                    columns=tuple(columns),
                    motion_dx_px_per_hour=float(raw_set["motion_dx_px_per_hour"]),
                    motion_dy_px_per_hour=float(raw_set["motion_dy_px_per_hour"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Rust nowcast cloud-column set contract is invalid") from exc
    features_by_offset = {}
    for offset, raw_features in value["observed_features_by_offset"].items():
        if not isinstance(raw_features, list):
            raise RuntimeError("Rust nowcast observed features are invalid")
        features_by_offset[int(offset)] = [GridCellFeature(**item) for item in raw_features]
    times_by_offset = {
        int(offset): _parse_native_utc(raw, "observed feature")
        for offset, raw in value["observed_feature_times_by_offset"].items()
    }
    summary = [str(item) for item in value["summary"]]
    try:
        return NowcastAdjustment(
            nowcast_algorithm=str(value["nowcast_algorithm"]),
            source=str(value["source"]),
            image_valid_time_utc=_parse_native_utc(
                value["image_valid_time_utc"], "image"
            ),
            image_valid_time_local=datetime.fromisoformat(
                str(value["image_valid_time_local"]).replace("Z", "+00:00")
            ),
            image_channel=str(value["image_channel"]),
            image_area=str(value["image_area"]),
            near_cloud_ratio=float(value["near_cloud_ratio"]),
            far_cloud_ratio=float(value["far_cloud_ratio"]),
            trend_score=float(value["trend_score"]),
            visibility_penalty=float(value["visibility_penalty"]),
            target_bonus=float(value["target_bonus"]),
            confidence=float(value["confidence"]),
            motion_dx_px_per_hour=float(value["motion_dx_px_per_hour"]),
            motion_dy_px_per_hour=float(value["motion_dy_px_per_hour"]),
            motion_correlation=float(value["motion_correlation"]),
            projection_hours=float(value["projection_hours"]),
            satellite_source_times_utc=[
                _parse_native_utc(item, "satellite source")
                for item in value["satellite_source_times_utc"]
            ],
            satellite_motion_age_minutes=(
                float(value["satellite_motion_age_minutes"])
                if value["satellite_motion_age_minutes"] is not None
                else None
            ),
            satellite_motion_quality=str(value["satellite_motion_quality"]),
            satellite_advection_horizon_hours=float(value["satellite_advection_horizon_hours"]),
            infrared_near_cloud_ratio=value["infrared_near_cloud_ratio"],
            infrared_far_cloud_ratio=value["infrared_far_cloud_ratio"],
            visible_near_cloud_ratio=value["visible_near_cloud_ratio"],
            visible_far_cloud_ratio=value["visible_far_cloud_ratio"],
            channel_fusion_weight_visible=float(value["channel_fusion_weight_visible"]),
            local_cloud_ratio=float(value["local_cloud_ratio"]),
            horizon_cloud_ratio=float(value["horizon_cloud_ratio"]),
            thin_cloud_ratio=float(value["thin_cloud_ratio"]),
            thick_cloud_ratio=float(value["thick_cloud_ratio"]),
            cloud_texture_score=float(value["cloud_texture_score"]),
            cloud_uniformity_score=float(value["cloud_uniformity_score"]),
            cloud_blanket_score=float(value["cloud_blanket_score"]),
            horizon_clearance_score=float(value["horizon_clearance_score"]),
            composition_score=float(value["composition_score"]),
            summary=summary,
            observed_features_by_offset=features_by_offset,
            observed_feature_times_by_offset=times_by_offset,
            cloud_column_schema_version=int(value["cloud_column_schema_version"]),
            cloud_column_sets=[item.as_dict() for item in column_sets],
            vertical_prior_identity=str(value["vertical_prior_identity"]),
            vertical_prior_source=str(value["vertical_prior_source"]),
            satellite_retrieval_quality=str(value["satellite_retrieval_quality"]),
            satellite_low_cloud_state=str(value["satellite_low_cloud_state"]),
            satellite_range_bytes=int(value["satellite_range_bytes"]),
            satellite_range_blocks=int(value["satellite_range_blocks"]),
            nowcast_point_lat=float(value["nowcast_point_lat"]),
            nowcast_point_lon=float(value["nowcast_point_lon"]),
            nowcast_cache_identity=str(value["nowcast_cache_identity"]),
            satellite_vertical_profile=value["satellite_vertical_profile"],
            calibrated_sample_coverage_ratio=float(value["calibrated_sample_coverage_ratio"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Rust nowcast adjustment model contract is invalid") from exc


def check_model_data(payload, *, regional, probe, config, refresh, fast, session=None):
    from firesky import desktop_data as data
    from firesky.web_server import WebServerConfig
    config = config or WebServerConfig()
    session = session if session is not None else {}
    models = ("gfs", "ec") if payload.get("model_source") == "both" else (
        str(payload.get("model_source") or "gfs"),)
    if len(models) == 2 and probe and "publications" not in session:
        session["publications"] = publications(["gfs", "ecmwf", "cams"])
    plans = []
    for model in models:
        request = {**payload, "model_source": model}
        resolver = data.model_context
        # Keep the seam used by differential tests and callers that inject a
        # resolved context. In the normal Rust backend, use this module's
        # native context bridge instead of recursing through desktop_data.
        if getattr(resolver, "__module__", "") == "firesky.desktop_data":
            resolver = model_context
        context = resolver(request, regional=regional, config=config, fast=fast)
        source = "ecmwf" if context["common"]["model_source"] == "ec" else "gfs"
        if probe and "publications" not in session:
            session["publications"] = publications([source, "cams"])
        context, notes = data._refresh_model_context(
            request, context, session.get("publications", {}).get(source, {}),
            regional=regional, config=config, refresh=refresh,
        )
        plans.append(plan(context, notes))
    params = dict(plans=plans, fast=bool(fast), publications=session.get("publications", {}),
        config={key: str(getattr(config, key)) for key in (
            "gfs_cache_dir", "ecmwf_cache_dir", "cams_cache_dir")})
    value = call_engine("check_desktop_data", params, timeout_seconds=None)
    if (not isinstance(value, dict) or value.get("contract") != "rust-desktop-preflight-v1"
        or value.get("runtime_python_used") is not False or value.get("resolved_plan_only") is not True):
        raise RuntimeError("Rust desktop preflight response identity is invalid")
    report = value.get("report")
    expected = (["gfs", "ecmwf", "cams"] if len(models) == 2 else
                ["ecmwf" if models[0] == "ec" else "gfs", "cams"])
    if (not isinstance(report, dict) or type(report.get("ready")) is not bool
        or not isinstance(report.get("datasets"), list)
        or [row.get("source") for row in report["datasets"] if isinstance(row, dict)] != expected
        or len(report["datasets"]) != len(expected)
        or any(type(row.get("ready")) is not bool
               or type(row.get("missing_count")) is not int or row["missing_count"] < 0
               or row["ready"] != (row["missing_count"] == 0) for row in report["datasets"])
        or report["ready"] != all(row["ready"] for row in report["datasets"])):
        raise RuntimeError("Rust desktop preflight report is invalid")
    if len(models) == 2:
        if report.get("model_cycles") != {p["model_source"]: p["cycle_utc"] for p in plans}:
            raise RuntimeError("Rust desktop preflight model cycles differ from the resolved plans")
    elif (report.get("model_cycle") != plans[0]["cycle_utc"]
          or report.get("model_source") != models[0]):
        raise RuntimeError("Rust desktop preflight model cycle differs from the resolved plan")
    return report


def check_nowcast_data(payload, *, config=None, probe=True, fast=False):
    """Check nowcast GFS/CAMS caches and the native HSD compact-cache gate."""
    import math
    from firesky import desktop_data as data
    from firesky import web_server as engine
    from firesky.aerosol import cams_profile_bracket_times
    config = config or engine.WebServerConfig()
    context = data.nowcast_context(payload, config=config, fast=fast)
    request = context["request"]
    frames = nowcast_hsd_cache(context, count=2)
    hours = set()
    for target in context["times"]:
        offset = (target - context["cycle"]).total_seconds() / 3600.0
        if offset >= 0:
            hours.update((math.floor(offset), math.ceil(offset)))
    publication_rows = publications(["gfs", "cams"]) if probe else {}
    params = {
        "plans": [{
            "model_source": "gfs",
            "cycle_utc": context["cycle"].astimezone(timezone.utc).isoformat(),
            "event_start": min(context["times"]).astimezone(timezone.utc).isoformat(),
            "bbox": list(context["bbox"]),
            "area": list(context["area"]),
            "hours": sorted(hours),
            "cams_valid_times": sorted({
                bracket.astimezone(timezone.utc).isoformat()
                for target in context["times"]
                for bracket in cams_profile_bracket_times(target)
            }),
            "point_count": 1,
            "terrain_pending": False,
            "compute_region": context["region"],
            "notes": [],
        }],
        "fast": bool(fast),
        "publications": publication_rows,
        "config": {
            key: str(getattr(config, key))
            for key in ("gfs_cache_dir", "ecmwf_cache_dir", "cams_cache_dir")
        },
    }
    value = call_engine("check_desktop_data", params, timeout_seconds=None)
    if (not isinstance(value, dict)
            or value.get("contract") != "rust-desktop-preflight-v1"
            or value.get("runtime_python_used") is not False
            or value.get("resolved_plan_only") is not True):
        raise RuntimeError("Rust nowcast preflight response identity is invalid")
    report = value.get("report")
    if (not isinstance(report, dict)
            or report.get("model_source") != "gfs"
            or report.get("model_cycle") != context["cycle"].isoformat()
            or [row.get("source") for row in report.get("datasets", [])] != ["gfs", "cams"]):
        raise RuntimeError("Rust nowcast preflight report identity is invalid")
    report = dict(report)
    for key in ("bbox", "checked_at", "event_start", "notes", "point_count", "terrain_pending"):
        report.pop(key, None)
    for row in report.get("datasets", []):
        row.pop("missing_keys", None)
    report.update({
        "ready": len(frames) >= 2 and bool(report["ready"]),
        "satellite_frames": len(frames),
        "satellite_reference": context["lookup"].isoformat(),
        "satellite_valid_times": [
            frame["valid_time_utc"].isoformat() for frame in frames
        ],
        "reference_time_utc": context["reference"].isoformat(),
        "cams_update_cycle": (
            publication_rows.get("cams", {}).get("cycle")
            if publication_rows.get("cams", {}).get("verified") else None
        ),
    })
    return report


_NOWCAST_ASSESSMENT_FIELDS = {
    "contract",
    "feature_row_contract",
    "runtime_python_used",
    "detail",
    "scan_count",
    "rows",
    "score_ceiling_applied",
    "quality",
    "scans",
}

# Per-scan fields that only exist on the "full" detail level: the acceptance
# differential walks them element by element, but the model rebuild never
# consumes them (TimeStepAssessment has no columns field and reads only the
# top-* members of the signal families).
_ASSESSMENT_FULL_ONLY_FIELDS = {
    "columns",
    "candidate_signals",
    "local_deck_signals",
    "remote_boundary_signals",
    "boundary_signals",
    "cloud_hole_signals",
}

_BOUNDARY_SIGNAL_RESPONSE_FIELDS = {
    "layer",
    "distance_km",
    "bearing_deg",
    "sector_delta_deg",
    "cloud_base_m",
    "cloud_top_m",
    "required_base_m",
    "apparent_base_elevation_deg",
    "path_clearance_score",
    "aerosol_transmission_score",
    "score",
    "edge_contrast_score",
    "clear_slot_score",
    "thickness_score",
    "cloud_type",
    "cloud_type_burnability_score",
    "deep_cloud_risk",
    "aerosol_sensitivity_score",
    "aerosol_path_variability_score",
    "observer_path_aod550",
    "inflow_path_aod550",
    "regime",
    "local_cloud_presence_score",
    "geometric_reach_km",
    "physical_transmission_min",
    "physical_transmission_max",
    "atmospheric_clarity_score",
    "edge_distance_lower_km",
    "edge_distance_upper_km",
}

_CLOUD_HOLE_SIGNAL_RESPONSE_FIELDS = {
    "distance_km",
    "bearing_deg",
    "sector_delta_deg",
    "clear_slot_score",
    "edge_contrast_score",
    "rear_target_distance_km",
    "rear_target_bearing_deg",
    "rear_target_cloud_type",
    "rear_target_burnability_score",
    "rear_target_score",
    "alignment_score",
    "path_clearance_score",
    "rear_blocking_risk",
    "score",
}

_SOLAR_INFLOW_RESPONSE_FIELDS = {
    "score",
    "distance_km",
    "bearing_deg",
    "pressure_hpa",
    "rh_percent",
    "cloud_base_m",
    "cloud_top_m",
    "ray_height_m",
    "target_distance_km",
    "target_bearing_deg",
    "target_height_m",
}

_SURFACE_WEATHER_RESPONSE_FIELDS = {"penalty", "visibility_m", "precip_rate_kg_m2_s"}

_CROSS_SECTION_RESPONSE_FIELDS = {
    "azimuth_deg",
    "samples_count",
    "lightpath_integrity_score",
    "path_extinction_score",
    "vertical_cloud_type",
    "cloud_type_burnability_score",
    "deep_cloud_risk",
    "blocking_risk",
    "target_cloud_score",
    "summary",
    "profile",
}

_PROFILE_SAMPLE_RESPONSE_FIELDS = {
    "distance_km",
    "bearing_deg",
    "cloud_base_m",
    "cloud_top_m",
    "cloud_type",
    "high_presence",
    "mid_presence",
    "low_presence",
    "deep_cloud_risk",
    "thickness_risk",
    "required_illumination_height_m",
    "blocker_risk",
    "lightpath_clearance_score",
    "target_cloud_score",
    "illumination_status",
    "levels",
}

_PROFILE_LEVEL_RESPONSE_FIELDS = {
    "pressure_hpa",
    "height_m",
    "rh_percent",
    "temperature_c",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
}

_ASSESSMENT_SCAN_FIELDS = {
    "forecast_hour",
    "valid_time_utc",
    "valid_time_local",
    "solar_elevation_deg",
    "solar_azimuth_deg",
    "burn_score",
    "intensity_score",
    "extent_score",
    "vividness_score",
    "visibility_score",
    "low_cloud_penalty",
    "candidate_count",
    "burning_cloud_count",
    "top_candidate",
    "strong_burning_cloud_count",
    "top_boundary",
    "boundary_score",
    "boundary_uncertainty",
    "top_cloud_hole",
    "cloud_hole_score",
    "cross_section",
    "solar_inflow_blockage",
    "selected_regime",
    "scene_scores",
    "local_sky_score",
    "remote_sky_score",
    "convective_risk",
    "color_diagnosis",
    "reasons",
    "map_cells",
    "geometric_cloud_coverage",
    "structure_gate_score",
    "satellite_retrieval_material_evidence",
    "selected_observer_path_aod550",
    "selected_inflow_path_aod550",
    "selected_aerosol_path_variability_score",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "observer_local_cloud_field_amount",
    "observation_burnable",
    "observation_score_ceiling",
    "surface_weather_penalty",
    "columns",
    "candidate_signals",
    "local_deck_signals",
    "remote_boundary_signals",
    "boundary_signals",
    "cloud_hole_signals",
}

_BOUNDARY_UNCERTAINTY_RESPONSE_FIELDS = {
    "regime",
    "source",
    "nominal_distance_km",
    "lower_distance_km",
    "upper_distance_km",
    "width_km",
    "score_min",
    "score_max",
    "event_threshold",
    "classification_stable",
    "nearest_flip_displacement_km",
}

_MAP_CELL_RESPONSE_FIELDS = {
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "high_cloud",
    "mid_cloud",
    "low_cloud",
    "cloud_evidence",
    "cloud_type",
    "cloud_base_m",
    "cloud_top_m",
    "deep_cloud_risk",
    "burn_score",
    "illumination_score",
    "path_clearance_score",
    "aerosol_transmission_score",
    "blocking_risk",
    "atmospheric_clarity_score",
}

_CLOUD_COLUMN_RESPONSE_FIELDS = {
    "key",
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "high_presence",
    "mid_presence",
    "low_presence",
    "deep_cloud_risk",
    "thickness_risk",
    "cloud_type",
    "burnability_score",
    "cloud_base_m",
    "cloud_top_m",
    "liquid_presence",
    "ice_presence",
    "explicit_cloud_evidence",
    "horizontal_cloud_probability",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "layers",
}

_CANDIDATE_SIGNAL_RESPONSE_FIELDS = {
    "lat",
    "lon",
    "pressure_hpa",
    "distance_km",
    "bearing_deg",
    "sector_delta_deg",
    "apparent_elevation_deg",
    "aerosol_transmission_score",
    "path_clearance_score",
    "illumination_height_m",
    "cloud_height_m",
    "score",
    "rh_percent",
    "cloud_base_m",
    "cloud_type",
    "cloud_type_burnability_score",
    "deep_cloud_risk",
    "aerosol_sensitivity_score",
    "aerosol_path_variability_score",
    "observer_path_aod550",
    "inflow_path_aod550",
    "cloud_evidence_score",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
    "atmospheric_clarity_score",
    "illumination_score",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "horizontal_cloud_probability",
}

NOWCAST_ASSESSMENT_BACKEND_ENV = "FIRESKY_NOWCAST_ASSESSMENT_BACKEND"


def nowcast_assessment_backend() -> str:
    """Report the opt-in backend for the batch nowcast assessment.

    Default stays `python`; `rust` routes the future batch operation onto the
    engine operation and any other value fails closed.
    """
    backend = os.environ.get(NOWCAST_ASSESSMENT_BACKEND_ENV, "python").strip().lower()
    if backend not in {"python", "rust"}:
        raise ValueError(
            f"{NOWCAST_ASSESSMENT_BACKEND_ENV} must be python or rust"
        )
    return backend


def _encode_assessment_feature_row(feature) -> list:
    from firesky.native_core import feature_to_row_v2

    row = feature_to_row_v2(feature)
    return [
        None if isinstance(value, float) and math.isnan(value) else value
        for value in row
    ]


def _encode_assessment_scan(scan, aerosol) -> dict:
    """Encode one batch scan: features, solar geometry, radar and material."""
    encoded_features = [_encode_assessment_feature_row(feature) for feature in scan["features"]]
    radar = scan.get("radar")
    if radar is not None and not isinstance(radar, dict):
        raise ValueError("Rust nowcast assessment radar input must be a mapping or None")
    return {
        "valid_time_utc": _scan_time_iso(scan["valid_time_utc"]),
        "forecast_hour": int(scan.get("forecast_hour") or 0),
        "features": encoded_features,
        "solar_elevation_deg": float(scan["solar_elevation_deg"]),
        "solar_azimuth_deg": float(scan["solar_azimuth_deg"]),
        "radar": (
            {
                "source": radar.get("source"),
                "coverage_ratio": radar.get("coverage_ratio"),
                "age_minutes": radar.get("age_minutes"),
                "max_dbz": radar.get("max_dbz"),
                "local_precip_ratio": radar.get("local_precip_ratio"),
                "path_precip_ratio": radar.get("path_precip_ratio"),
                "convective_ratio": radar.get("convective_ratio"),
                "convective_risk": radar.get("convective_risk"),
            }
            if radar is not None
            else None
        ),
        "satellite_material_evidence": scan.get("satellite_material_evidence"),
        "solar_incoming_block_risk": float(scan.get("solar_incoming_block_risk") or 0.0),
        "forward_burnable_cloud_load": scan.get("forward_burnable_cloud_load"),
        "forward_burnable_cloud_material_load": scan.get("forward_burnable_cloud_material_load"),
        "forward_burnable_cloud_incoming_aerosol_load": scan.get(
            "forward_burnable_cloud_incoming_aerosol_load"
        ),
        # scoring.py:3955: observer_lat comes from the aerosol reading first,
        # then falls back to snapshot.features[0].lat inside the engine.
        "observer_lat": scan.get("observer_lat") if scan.get("observer_lat") is not None else (
            float(aerosol.observer_lat) if aerosol.observer_lat is not None else None
        ),
        "feature_count": len(encoded_features),
    }


def _scan_time_iso(value):
    if isinstance(value, str):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = value
    return parsed.astimezone(timezone.utc).isoformat()


def nowcast_assessment(
    *,
    scans,
    timezone_name,
    aerosol,
    observer_elevation_m,
    max_distance_km=None,
    map_radius_km=None,
    include_map_cells=False,
    event_time_utc=None,
    core_window_minutes=None,
    apply_duration=True,
    detail="full",
):
    """Run the batch nowcast assessment kernel in Rust.

    Each scan carries its own ``hsd-assessment-feature-row-v2`` packable
    features and solar geometry. Returns the validated native payload dict
    (per-scan assessments, 18-value ledger rows and the canonical event/instant
    quality over those rows).

    ``detail`` selects the response level: ``"full"`` keeps every diagnostic
    list (the acceptance differential walks them element by element);
    ``"model"`` drops the six fields the model rebuild never consumes
    (columns + five signal families) and validates only key invariants.
    """
    from firesky.analysis_constants import MAX_ANALYSIS_RADIUS_KM

    if not isinstance(scans, (list, tuple)) or not 1 <= len(scans) <= 16:
        raise ValueError("Rust nowcast assessment requires 1..16 scans")
    if aerosol.path_samples and (
        aerosol.observer_lat is None or aerosol.observer_lon is None
    ):
        raise ValueError("Rust nowcast assessment aerosol path samples need an observer")
    encoded_scans = [_encode_assessment_scan(scan, aerosol) for scan in scans]
    request = {
        "scans": [
            {key: value for key, value in scan.items() if key != "feature_count"}
            for scan in encoded_scans
        ],
        "timezone_name": str(timezone_name),
        "observer_elevation_m": float(observer_elevation_m),
        "aerosol": {
            "aod550": float(aerosol.aod550),
            "source": str(aerosol.source),
            "quality": str(aerosol.quality),
            "observer_lat": (
                float(aerosol.observer_lat) if aerosol.observer_lat is not None else None
            ),
            "observer_lon": (
                float(aerosol.observer_lon) if aerosol.observer_lon is not None else None
            ),
            "path_samples": [
                {
                    "lat": float(sample.lat),
                    "lon": float(sample.lon),
                    "distance_km": float(sample.distance_km),
                    "bearing_deg": float(sample.bearing_deg),
                    "aod550": float(sample.aod550),
                }
                for sample in aerosol.path_samples
            ],
        },
        "max_distance_km": float(
            MAX_ANALYSIS_RADIUS_KM if max_distance_km is None else max_distance_km
        ),
        "map_radius_km": float(
            MAX_ANALYSIS_RADIUS_KM if map_radius_km is None else map_radius_km
        ),
        "include_map_cells": bool(include_map_cells),
        "event_time_utc": (
            _scan_time_iso(event_time_utc) if event_time_utc is not None else None
        ),
        "core_window_minutes": (
            float(core_window_minutes) if core_window_minutes is not None else None
        ),
        "apply_duration": bool(apply_duration),
        "detail": str(detail),
    }
    with tempfile.TemporaryDirectory(prefix="firesky-rust-nowcast-assessment-") as root:
        root = Path(root)
        request_path = root / "request.json"
        output_path = root / "assessment.json"
        payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode()
        request_path.write_bytes(payload)
        response = call_engine(
            "desktop_nowcast_assessment",
            {"request_path": str(request_path), "output_path": str(output_path)},
            timeout_seconds=None,
        )
        expected_receipt = {
            "contract",
            "runtime_python_used",
            "output_path",
            "bytes",
            "sha256",
            "request_sha256",
        }
        if (
            not isinstance(response, dict)
            or set(response) != expected_receipt
            or response.get("contract") != "rust-desktop-nowcast-assessment-file-v1"
            or response.get("runtime_python_used") is not False
            or response.get("output_path") != str(output_path)
        ):
            raise RuntimeError("Rust nowcast assessment receipt is invalid")
        if (
            type(response.get("bytes")) is not int
            or type(response.get("request_sha256")) is not str
            or response["request_sha256"] != hashlib.sha256(payload).hexdigest()
        ):
            raise RuntimeError("Rust nowcast assessment request identity is invalid")
        try:
            encoded = output_path.read_bytes()
        except OSError as exc:
            raise RuntimeError("Rust nowcast assessment output is unavailable") from exc
        if (
            response["bytes"] != len(encoded)
            or len(encoded) > 128 * 1024 * 1024
            or response.get("sha256") != hashlib.sha256(encoded).hexdigest()
        ):
            raise RuntimeError("Rust nowcast assessment output identity is invalid")
        try:
            value = json.loads(encoded)
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("Rust nowcast assessment output is invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or set(value) != _NOWCAST_ASSESSMENT_FIELDS
        or value.get("contract") != "rust-desktop-nowcast-assessment-v2"
        or value.get("feature_row_contract") != "hsd-assessment-feature-row-v2"
        or value.get("runtime_python_used") is not False
        or value.get("detail") not in ("full", "model")
        or value.get("scan_count") != len(encoded_scans)
        or not isinstance(value.get("rows"), list)
        or len(value.get("rows") or []) != len(encoded_scans)
    ):
        raise RuntimeError("Rust nowcast assessment response identity is invalid")
    if not isinstance(value.get("quality"), dict) or value["quality"].get(
        "contract"
    ) != "rust-desktop-nowcast-quality-v2":
        raise RuntimeError("Rust nowcast assessment quality payload is invalid")
    full_detail = value["detail"] == "full"
    expected_scan_fields = _ASSESSMENT_SCAN_FIELDS
    if not full_detail:
        expected_scan_fields = _ASSESSMENT_SCAN_FIELDS - _ASSESSMENT_FULL_ONLY_FIELDS
    for scan_index, scan in enumerate(value["scans"]):
        if not isinstance(scan, dict) or set(scan) != expected_scan_fields:
            raise RuntimeError("Rust nowcast assessment scan payload is invalid")
        if scan["candidate_count"] > encoded_scans[scan_index]["feature_count"]:
            raise RuntimeError("Rust nowcast assessment scan identity is invalid")
        if not full_detail:
            # Light validation: key invariants only (the model rebuild reads
            # the top-* members and scalar fields, not the per-element lists).
            continue
        for raw_column in scan["columns"]:
            if (
                not isinstance(raw_column, dict)
                or set(raw_column) != _CLOUD_COLUMN_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment cloud column is invalid")
        for raw_signal in scan["candidate_signals"]:
            if (
                not isinstance(raw_signal, dict)
                or set(raw_signal) != _CANDIDATE_SIGNAL_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment candidate signal is invalid")
        for key in ("local_deck_signals", "remote_boundary_signals", "boundary_signals"):
            for raw_signal in scan[key]:
                if (
                    not isinstance(raw_signal, dict)
                    or set(raw_signal) != _BOUNDARY_SIGNAL_RESPONSE_FIELDS
                ):
                    raise RuntimeError("Rust nowcast assessment boundary signal is invalid")
        for raw_signal in scan["cloud_hole_signals"]:
            if (
                not isinstance(raw_signal, dict)
                or set(raw_signal) != _CLOUD_HOLE_SIGNAL_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment cloud hole signal is invalid")
        if scan["solar_inflow_blockage"] is not None and (
            not isinstance(scan["solar_inflow_blockage"], dict)
            or set(scan["solar_inflow_blockage"]) != _SOLAR_INFLOW_RESPONSE_FIELDS
        ):
            raise RuntimeError("Rust nowcast assessment inflow blockage is invalid")
        if (
            not isinstance(scan["surface_weather_penalty"], dict)
            or set(scan["surface_weather_penalty"]) != _SURFACE_WEATHER_RESPONSE_FIELDS
        ):
            raise RuntimeError("Rust nowcast assessment weather penalty is invalid")
        cross_section = scan["cross_section"]
        if not isinstance(cross_section, dict) or set(cross_section) != _CROSS_SECTION_RESPONSE_FIELDS:
            raise RuntimeError("Rust nowcast assessment cross section is invalid")
        for raw_sample in cross_section["profile"]:
            if (
                not isinstance(raw_sample, dict)
                or set(raw_sample) != _PROFILE_SAMPLE_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment profile sample is invalid")
            for raw_level in raw_sample["levels"]:
                if (
                    not isinstance(raw_level, dict)
                    or set(raw_level) != _PROFILE_LEVEL_RESPONSE_FIELDS
                ):
                    raise RuntimeError("Rust nowcast assessment profile level is invalid")
        if scan["boundary_uncertainty"] is not None and (
            not isinstance(scan["boundary_uncertainty"], dict)
            or set(scan["boundary_uncertainty"]) != _BOUNDARY_UNCERTAINTY_RESPONSE_FIELDS
        ):
            raise RuntimeError("Rust nowcast assessment boundary uncertainty is invalid")
        for raw_cell in scan["map_cells"]:
            if (
                not isinstance(raw_cell, dict)
                or set(raw_cell) != _MAP_CELL_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment map cell is invalid")
    return value


def _parse_native_assessment_time(raw, label):
    """Parse an ISO-8601 assessment timestamp with any timezone offset."""
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Rust nowcast assessment {label} time is invalid") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"Rust nowcast assessment {label} time lacks a timezone")
    return parsed


def _rebuild_boundary_signal(raw_signal):
    from firesky.models import BoundarySignal

    return BoundarySignal(
        layer=str(raw_signal["layer"]),
        distance_km=float(raw_signal["distance_km"]),
        bearing_deg=float(raw_signal["bearing_deg"]),
        sector_delta_deg=float(raw_signal["sector_delta_deg"]),
        cloud_base_m=float(raw_signal["cloud_base_m"]),
        cloud_top_m=float(raw_signal["cloud_top_m"]),
        required_base_m=float(raw_signal["required_base_m"]),
        apparent_base_elevation_deg=float(raw_signal["apparent_base_elevation_deg"]),
        path_clearance_score=float(raw_signal["path_clearance_score"]),
        aerosol_transmission_score=float(raw_signal["aerosol_transmission_score"]),
        score=float(raw_signal["score"]),
        edge_contrast_score=float(raw_signal["edge_contrast_score"]),
        clear_slot_score=float(raw_signal["clear_slot_score"]),
        thickness_score=float(raw_signal["thickness_score"]),
        cloud_type=str(raw_signal["cloud_type"]),
        cloud_type_burnability_score=float(raw_signal["cloud_type_burnability_score"]),
        deep_cloud_risk=float(raw_signal["deep_cloud_risk"]),
        aerosol_sensitivity_score=float(raw_signal["aerosol_sensitivity_score"]),
        aerosol_path_variability_score=float(raw_signal["aerosol_path_variability_score"]),
        observer_path_aod550=raw_signal["observer_path_aod550"],
        inflow_path_aod550=raw_signal["inflow_path_aod550"],
        regime=str(raw_signal["regime"]),
        local_cloud_presence_score=float(raw_signal["local_cloud_presence_score"]),
        geometric_reach_km=float(raw_signal["geometric_reach_km"]),
        physical_transmission_min=float(raw_signal["physical_transmission_min"]),
        physical_transmission_max=float(raw_signal["physical_transmission_max"]),
        atmospheric_clarity_score=float(raw_signal["atmospheric_clarity_score"]),
        edge_distance_lower_km=raw_signal["edge_distance_lower_km"],
        edge_distance_upper_km=raw_signal["edge_distance_upper_km"],
    )


def _rebuild_cloud_hole_signal(raw_signal):
    from firesky.models import CloudHoleSignal

    return CloudHoleSignal(
        distance_km=float(raw_signal["distance_km"]),
        bearing_deg=float(raw_signal["bearing_deg"]),
        sector_delta_deg=float(raw_signal["sector_delta_deg"]),
        clear_slot_score=float(raw_signal["clear_slot_score"]),
        edge_contrast_score=float(raw_signal["edge_contrast_score"]),
        rear_target_distance_km=float(raw_signal["rear_target_distance_km"]),
        rear_target_bearing_deg=float(raw_signal["rear_target_bearing_deg"]),
        rear_target_cloud_type=str(raw_signal["rear_target_cloud_type"]),
        rear_target_burnability_score=float(raw_signal["rear_target_burnability_score"]),
        rear_target_score=float(raw_signal["rear_target_score"]),
        alignment_score=float(raw_signal["alignment_score"]),
        path_clearance_score=float(raw_signal["path_clearance_score"]),
        rear_blocking_risk=float(raw_signal["rear_blocking_risk"]),
        score=float(raw_signal["score"]),
    )


def _rebuild_cross_section(raw_cross):
    from firesky.models import (
        CrossSectionDiagnosis,
        SolarPathProfileLevel,
        SolarPathProfileSample,
    )

    profile = []
    for raw_sample in raw_cross["profile"]:
        levels = [
            SolarPathProfileLevel(
                pressure_hpa=int(raw_level["pressure_hpa"]),
                height_m=float(raw_level["height_m"]),
                rh_percent=float(raw_level["rh_percent"]),
                temperature_c=raw_level["temperature_c"],
                cloud_fraction_percent=raw_level["cloud_fraction_percent"],
                cloud_liquid_kg_kg=raw_level["cloud_liquid_kg_kg"],
                cloud_ice_kg_kg=raw_level["cloud_ice_kg_kg"],
            )
            for raw_level in raw_sample["levels"]
        ]
        profile.append(
            SolarPathProfileSample(
                distance_km=float(raw_sample["distance_km"]),
                bearing_deg=float(raw_sample["bearing_deg"]),
                cloud_base_m=float(raw_sample["cloud_base_m"]),
                cloud_top_m=float(raw_sample["cloud_top_m"]),
                cloud_type=str(raw_sample["cloud_type"]),
                high_presence=float(raw_sample["high_presence"]),
                mid_presence=float(raw_sample["mid_presence"]),
                low_presence=float(raw_sample["low_presence"]),
                deep_cloud_risk=float(raw_sample["deep_cloud_risk"]),
                thickness_risk=float(raw_sample["thickness_risk"]),
                required_illumination_height_m=float(
                    raw_sample["required_illumination_height_m"]
                ),
                blocker_risk=float(raw_sample["blocker_risk"]),
                lightpath_clearance_score=float(raw_sample["lightpath_clearance_score"]),
                target_cloud_score=float(raw_sample["target_cloud_score"]),
                illumination_status=str(raw_sample["illumination_status"]),
                levels=levels,
            )
        )
    return CrossSectionDiagnosis(
        azimuth_deg=float(raw_cross["azimuth_deg"]),
        samples_count=int(raw_cross["samples_count"]),
        lightpath_integrity_score=float(raw_cross["lightpath_integrity_score"]),
        path_extinction_score=float(raw_cross["path_extinction_score"]),
        vertical_cloud_type=str(raw_cross["vertical_cloud_type"]),
        cloud_type_burnability_score=float(raw_cross["cloud_type_burnability_score"]),
        deep_cloud_risk=float(raw_cross["deep_cloud_risk"]),
        blocking_risk=float(raw_cross["blocking_risk"]),
        target_cloud_score=float(raw_cross["target_cloud_score"]),
        summary=[str(item) for item in raw_cross["summary"]],
        profile=profile,
    )


def nowcast_assessment_as_model(value, *, scans):
    """Rebuild full TimeStepAssessment objects from the native batch payload."""
    from firesky.models import (
        BoundaryUncertainty,
        CandidateSignal,
        MapCloudCell,
        SolarInflowBlockage,
        TimeStepAssessment,
    )

    if (
        not isinstance(value, dict)
        or set(value) != _NOWCAST_ASSESSMENT_FIELDS
        or value.get("detail") not in ("full", "model")
    ):
        raise RuntimeError("Rust nowcast assessment model payload is invalid")
    if not isinstance(scans, (list, tuple)) or len(scans) != value["scan_count"]:
        raise RuntimeError("Rust nowcast assessment scan list is invalid")
    rebuilt = []
    for raw_scan in value["scans"]:
        # The model rebuild reads only the top member of each signal family
        # (TimeStepAssessment has no columns field; local-deck and
        # remote-boundary lists were never consumed), so on either detail
        # level only the top element of each family is materialized.
        raw_top_candidate = raw_scan["top_candidate"]
        if raw_top_candidate is not None:
            if (
                not isinstance(raw_top_candidate, dict)
                or set(raw_top_candidate) != _CANDIDATE_SIGNAL_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment candidate signal is invalid")
            signals = [
                CandidateSignal(
                    lat=float(raw_top_candidate["lat"]),
                    lon=float(raw_top_candidate["lon"]),
                    pressure_hpa=int(raw_top_candidate["pressure_hpa"]),
                    distance_km=float(raw_top_candidate["distance_km"]),
                    bearing_deg=float(raw_top_candidate["bearing_deg"]),
                    sector_delta_deg=float(raw_top_candidate["sector_delta_deg"]),
                    apparent_elevation_deg=float(raw_top_candidate["apparent_elevation_deg"]),
                    aerosol_transmission_score=float(raw_top_candidate["aerosol_transmission_score"]),
                    path_clearance_score=float(raw_top_candidate["path_clearance_score"]),
                    illumination_height_m=float(raw_top_candidate["illumination_height_m"]),
                    cloud_height_m=float(raw_top_candidate["cloud_height_m"]),
                    score=float(raw_top_candidate["score"]),
                    rh_percent=float(raw_top_candidate["rh_percent"]),
                    cloud_base_m=float(raw_top_candidate["cloud_base_m"]),
                    cloud_type=str(raw_top_candidate["cloud_type"]),
                    cloud_type_burnability_score=float(raw_top_candidate["cloud_type_burnability_score"]),
                    deep_cloud_risk=float(raw_top_candidate["deep_cloud_risk"]),
                    aerosol_sensitivity_score=float(raw_top_candidate["aerosol_sensitivity_score"]),
                    aerosol_path_variability_score=float(raw_top_candidate["aerosol_path_variability_score"]),
                    observer_path_aod550=raw_top_candidate["observer_path_aod550"],
                    inflow_path_aod550=raw_top_candidate["inflow_path_aod550"],
                    cloud_evidence_score=float(raw_top_candidate["cloud_evidence_score"]),
                    cloud_fraction_percent=raw_top_candidate["cloud_fraction_percent"],
                    cloud_liquid_kg_kg=raw_top_candidate["cloud_liquid_kg_kg"],
                    cloud_ice_kg_kg=raw_top_candidate["cloud_ice_kg_kg"],
                    atmospheric_clarity_score=float(raw_top_candidate["atmospheric_clarity_score"]),
                    illumination_score=float(raw_top_candidate["illumination_score"]),
                    footprint_azimuth_width_deg=raw_top_candidate["footprint_azimuth_width_deg"],
                    footprint_near_distance_km=raw_top_candidate["footprint_near_distance_km"],
                    footprint_far_distance_km=raw_top_candidate["footprint_far_distance_km"],
                    horizontal_cloud_probability=raw_top_candidate["horizontal_cloud_probability"],
                )
            ]
        else:
            signals = []

        def rebuild_boundary(raw_signal):
            if (
                not isinstance(raw_signal, dict)
                or set(raw_signal) != _BOUNDARY_SIGNAL_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment boundary signal is invalid")
            return _rebuild_boundary_signal(raw_signal)

        def rebuild_hole(raw_signal):
            if (
                not isinstance(raw_signal, dict)
                or set(raw_signal) != _CLOUD_HOLE_SIGNAL_RESPONSE_FIELDS
            ):
                raise RuntimeError("Rust nowcast assessment cloud hole signal is invalid")
            return _rebuild_cloud_hole_signal(raw_signal)

        top_candidate = signals[0] if signals else None
        top_boundary = (
            rebuild_boundary(raw_scan["top_boundary"])
            if raw_scan["top_boundary"] is not None
            else None
        )
        top_cloud_hole = (
            rebuild_hole(raw_scan["top_cloud_hole"])
            if raw_scan["top_cloud_hole"] is not None
            else None
        )
        boundary_uncertainty = None
        if raw_scan["boundary_uncertainty"] is not None:
            raw_uncertainty = raw_scan["boundary_uncertainty"]
            if set(raw_uncertainty) != _BOUNDARY_UNCERTAINTY_RESPONSE_FIELDS:
                raise RuntimeError("Rust nowcast assessment boundary uncertainty is invalid")
            boundary_uncertainty = BoundaryUncertainty(
                regime=str(raw_uncertainty["regime"]),
                source=str(raw_uncertainty["source"]),
                nominal_distance_km=float(raw_uncertainty["nominal_distance_km"]),
                lower_distance_km=float(raw_uncertainty["lower_distance_km"]),
                upper_distance_km=float(raw_uncertainty["upper_distance_km"]),
                width_km=float(raw_uncertainty["width_km"]),
                score_min=float(raw_uncertainty["score_min"]),
                score_max=float(raw_uncertainty["score_max"]),
                event_threshold=float(raw_uncertainty["event_threshold"]),
                classification_stable=bool(raw_uncertainty["classification_stable"]),
                nearest_flip_displacement_km=raw_uncertainty["nearest_flip_displacement_km"],
            )
        solar_inflow_blockage = None
        if raw_scan["solar_inflow_blockage"] is not None:
            raw_blockage = raw_scan["solar_inflow_blockage"]
            if set(raw_blockage) != _SOLAR_INFLOW_RESPONSE_FIELDS:
                raise RuntimeError("Rust nowcast assessment inflow blockage is invalid")
            solar_inflow_blockage = SolarInflowBlockage(
                score=float(raw_blockage["score"]),
                distance_km=float(raw_blockage["distance_km"]),
                bearing_deg=float(raw_blockage["bearing_deg"]),
                pressure_hpa=int(raw_blockage["pressure_hpa"]),
                rh_percent=float(raw_blockage["rh_percent"]),
                cloud_base_m=float(raw_blockage["cloud_base_m"]),
                cloud_top_m=float(raw_blockage["cloud_top_m"]),
                ray_height_m=float(raw_blockage["ray_height_m"]),
                target_distance_km=float(raw_blockage["target_distance_km"]),
                target_bearing_deg=float(raw_blockage["target_bearing_deg"]),
                target_height_m=float(raw_blockage["target_height_m"]),
            )
        cross_section = _rebuild_cross_section(raw_scan["cross_section"])
        map_cells = []
        for raw_cell in raw_scan["map_cells"]:
            if not isinstance(raw_cell, dict) or set(raw_cell) != _MAP_CELL_RESPONSE_FIELDS:
                raise RuntimeError("Rust nowcast assessment map cell is invalid")
            map_cells.append(
                MapCloudCell(
                    lat=float(raw_cell["lat"]),
                    lon=float(raw_cell["lon"]),
                    distance_km=float(raw_cell["distance_km"]),
                    bearing_deg=float(raw_cell["bearing_deg"]),
                    high_cloud=float(raw_cell["high_cloud"]),
                    mid_cloud=float(raw_cell["mid_cloud"]),
                    low_cloud=float(raw_cell["low_cloud"]),
                    cloud_evidence=float(raw_cell["cloud_evidence"]),
                    cloud_type=str(raw_cell["cloud_type"]),
                    cloud_base_m=float(raw_cell["cloud_base_m"]),
                    cloud_top_m=float(raw_cell["cloud_top_m"]),
                    deep_cloud_risk=float(raw_cell["deep_cloud_risk"]),
                    burn_score=float(raw_cell["burn_score"]),
                    illumination_score=float(raw_cell["illumination_score"]),
                    path_clearance_score=float(raw_cell["path_clearance_score"]),
                    aerosol_transmission_score=float(raw_cell["aerosol_transmission_score"]),
                    blocking_risk=float(raw_cell["blocking_risk"]),
                    atmospheric_clarity_score=float(raw_cell["atmospheric_clarity_score"]),
                )
            )
        rebuilt.append(
            TimeStepAssessment(
                forecast_hour=int(raw_scan["forecast_hour"]),
                valid_time_utc=_parse_native_assessment_time(
                    raw_scan["valid_time_utc"], "assessment valid_time_utc"
                ),
                valid_time_local=_parse_native_assessment_time(
                    raw_scan["valid_time_local"], "assessment valid_time_local"
                ),
                solar_elevation_deg=float(raw_scan["solar_elevation_deg"]),
                solar_azimuth_deg=float(raw_scan["solar_azimuth_deg"]),
                burn_score=float(raw_scan["burn_score"]),
                intensity_score=float(raw_scan["intensity_score"]),
                extent_score=float(raw_scan["extent_score"]),
                vividness_score=float(raw_scan["vividness_score"]),
                visibility_score=float(raw_scan["visibility_score"]),
                low_cloud_penalty=float(raw_scan["low_cloud_penalty"]),
                candidate_count=int(raw_scan["candidate_count"]),
                burning_cloud_count=int(raw_scan["burning_cloud_count"]),
                top_candidate=top_candidate,
                strong_burning_cloud_count=int(raw_scan["strong_burning_cloud_count"]),
                top_boundary=top_boundary,
                boundary_score=float(raw_scan["boundary_score"]),
                boundary_uncertainty=boundary_uncertainty,
                top_cloud_hole=top_cloud_hole,
                cloud_hole_score=float(raw_scan["cloud_hole_score"]),
                cross_section=cross_section,
                solar_inflow_blockage=solar_inflow_blockage,
                selected_regime=str(raw_scan["selected_regime"]),
                scene_scores={str(name): float(value) for name, value in raw_scan["scene_scores"]},
                local_sky_score=float(raw_scan["local_sky_score"]),
                remote_sky_score=float(raw_scan["remote_sky_score"]),
                convective_risk=float(raw_scan["convective_risk"]),
                color_diagnosis=raw_scan["color_diagnosis"],
                reasons=[str(item) for item in raw_scan["reasons"]],
                map_cells=map_cells,
                geometric_cloud_coverage=float(raw_scan["geometric_cloud_coverage"]),
                structure_gate_score=float(raw_scan["structure_gate_score"]),
                satellite_retrieval_material_evidence=raw_scan["satellite_retrieval_material_evidence"],
                selected_observer_path_aod550=raw_scan["selected_observer_path_aod550"],
                selected_inflow_path_aod550=raw_scan["selected_inflow_path_aod550"],
                selected_aerosol_path_variability_score=float(
                    raw_scan["selected_aerosol_path_variability_score"]
                ),
                forward_view_cloud_field_amount=float(raw_scan["forward_view_cloud_field_amount"]),
                forward_burnable_cloud_field_amount=float(
                    raw_scan["forward_burnable_cloud_field_amount"]
                ),
                observer_local_cloud_field_amount=float(
                    raw_scan["observer_local_cloud_field_amount"]
                ),
                observation_burnable=bool(raw_scan["observation_burnable"]),
                observation_score_ceiling=raw_scan["observation_score_ceiling"],
            )
        )
    return {
        "assessments": rebuilt,
        "rows": value["rows"],
        "quality": value["quality"],
    }
