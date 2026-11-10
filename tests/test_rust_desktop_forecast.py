"""Native desktop point-forecast bridge contract tests."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest_plugins = ("test_rust_ecmwf_data",)

from firesky import rust_desktop_data
from firesky.web_server import WebServerConfig
from test_rust_ecmwf_point import (
    recipe as ec_recipe,
    seed_cams as seed_ec_cams,
    plan as ec_plan,
)
from test_rust_input_plan import planned, recipe, seed_cams, seed_gfs
from test_rust_migration import engine_binary


def _identity(path: Path) -> dict:
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _context(source="model-grid"):
    point = {
        "name": "fixture",
        "lat": 31.25,
        "lon": 121.5,
        "elevation_m": 42.5,
        "timezone_name": "Asia/Shanghai",
        "_elevation_source": source,
    }
    return {
        "points": [point],
        "common": {"_compute_region": None},
        "cycle": datetime(2026, 11, 9, tzinfo=timezone.utc),
        "bbox": (100.0, 125.0, 45.0, 10.0),
    }


def test_native_point_bridge_freezes_model_grid_and_validates_artifacts(
    monkeypatch, tmp_path
):
    captured = []
    context = _context()

    def fake_call(method, params, *, timeout_seconds=None):
        captured.append((method, params))
        if method == "build_stored_cross_section":
            return {"model_source": "gfs", "profile": []}
        request = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        assert request["points"][0]["elevation_source"] == "model-grid"
        assert "elevation_m" not in request["points"][0]
        assert request["bbox"] == list(context["bbox"])
        assert request["derived_cache"]["directory"].endswith("gfs/derived/rust-v1")
        output = Path(params["output_dir"])
        output.mkdir()
        point = {
            "model_source": "gfs",
            "forecast_mode": "model",
            "target_date": "2026-11-09",
            "phenomenon": "dusk",
            "anchor_local": "2026-11-09T16:30:00+08:00",
            "best_time_local": "2026-11-09T16:30:00+08:00",
        }
        point_path = output / "point_forecast.json"
        point_path.write_text(json.dumps(point), encoding="utf-8")
        cross_path = output / "cross_section_source.json"
        cross_path.write_text("{}", encoding="utf-8")
        manifest = {
            "metadata": {
                "point_forecast": {
                    "file": "point_forecast.json",
                    "identity": _identity(point_path),
                    "contract": "rust-stored-point-forecast-v1",
                    "point_core_validated": True,
                    "stored_point_payload_complete": True,
                    "public_forecast_complete": False,
                },
                "cross_section_source": {
                    "file": "cross_section_source.json",
                    "identity": _identity(cross_path),
                },
            }
        }
        fake_call.manifest = manifest
        return {"manifest_path": str(output / "manifest.json")}

    monkeypatch.setattr(rust_desktop_data, "call_engine", fake_call)
    monkeypatch.setattr(rust_desktop_data, "model_context", lambda *a, **k: context)
    monkeypatch.setattr(
        rust_desktop_data,
        "load_tensor_bundle",
        lambda *a, **k: SimpleNamespace(manifest=fake_call.manifest),
    )

    result = rust_desktop_data.forecast_point(
        {
            "name": "fixture",
            "lat": 31.25,
            "lon": 121.5,
            "elevation_m": 8.0,
            "timezone_name": "Asia/Shanghai",
            "target_date": "2026-11-09",
            "phenomenon": "dusk",
            "radius_km": 900.0,
        },
        model="gfs",
        config=WebServerConfig(
            gfs_cache_dir=tmp_path / "gfs",
            ecmwf_cache_dir=tmp_path / "ec",
            cams_cache_dir=tmp_path / "cams",
        ),
    )

    assert result["native_forecast"]["runtime_python_used"] is False
    assert result["cross_section"] == {"model_source": "gfs", "profile": []}
    assert [method for method, _ in captured] == [
        "forecast_cached_gfs_point_to_files",
        "build_stored_cross_section",
    ]


def test_native_ec_point_bridge_promotes_report_payload(monkeypatch, tmp_path):
    captured = []
    context = _context(source="observer")

    def fake_call(method, params, *, timeout_seconds=None):
        captured.append((method, params))
        if method == "build_stored_cross_section":
            return {"model_source": "ec", "profile": []}
        request = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        assert request["point"]["elevation_source"] == "observer"
        assert request["point"]["elevation_m"] == 42.5
        assert request["derived_cache"]["directory"].endswith("ec/derived/rust-v1")
        output = Path(params["output_dir"])
        output.mkdir()
        report = {
            "model_source": "ec",
            "forecast_mode": "model",
            "target_date": "2026-11-09",
            "phenomenon": "dusk",
            "anchor_local": "2026-11-09T16:30:00+08:00",
            "best_time_local": "2026-11-09T16:30:00+08:00",
            "cycle_time_utc": "2026-11-09T00:00:00+00:00",
        }
        native_quality = {"quality_index": 1.5, "duration_status": "estimated"}
        dominant = {
            "pressure_family": "low",
            "cloud_phase": "unknown",
            "cloud_base_asl_mean_m": 900.0,
            "cloud_top_asl_mean_m": 1800.0,
            "target_terrain_asl_mean_m": 42.5,
            "cloud_base_agl_mean_m": 857.5,
            "cloud_top_agl_mean_m": 1757.5,
            "cloud_base_above_observer_mean_m": 857.5,
            "cloud_top_above_observer_mean_m": 1757.5,
            "genus_diagnosis": None,
            "genus_proxy": None,
            "phase_proxy": None,
        }
        payload = {
            **report,
            "quality_index": 1.5,
            "steps": [],
        }
        document = {
            "contract": "rust-ecmwf-point-report-v1",
            "report": report,
            "native_quality": native_quality,
            "dominant_burnable": dominant,
            "web_report_payload": payload,
            "confidence_semantics": "diagnostic_input_coverage_not_statistical_confidence",
            "public_forecast_complete": False,
        }
        report_path = output / "ecmwf_point.json"
        report_path.write_text(json.dumps(document), encoding="utf-8")
        cross_path = output / "cross_section_source.json"
        cross_path.write_text("{}", encoding="utf-8")
        fake_call.manifest = {
            "metadata": {
                "ecmwf_point_report": {
                    "file": "ecmwf_point.json",
                    "identity": _identity(report_path),
                    "contract": "rust-ecmwf-point-report-v1",
                    "report_complete": True,
                    "provenance_complete": False,
                    "public_forecast_complete": False,
                },
                "cross_section_source": {
                    "file": "cross_section_source.json",
                    "identity": _identity(cross_path),
                },
            }
        }
        return {"manifest_path": str(output / "manifest.json")}

    monkeypatch.setattr(rust_desktop_data, "call_engine", fake_call)
    monkeypatch.setattr(rust_desktop_data, "model_context", lambda *a, **k: context)
    monkeypatch.setattr(
        rust_desktop_data,
        "load_tensor_bundle",
        lambda *a, **k: SimpleNamespace(manifest=fake_call.manifest),
    )

    result = rust_desktop_data.forecast_point(
        {
            "name": "fixture",
            "lat": 31.25,
            "lon": 121.5,
            "elevation_m": 8.0,
            "timezone_name": "Asia/Shanghai",
            "target_date": "2026-11-09",
            "phenomenon": "dusk",
            "radius_km": 900.0,
        },
        model="ec",
        config=WebServerConfig(
            gfs_cache_dir=tmp_path / "gfs",
            ecmwf_cache_dir=tmp_path / "ec",
            cams_cache_dir=tmp_path / "cams",
        ),
    )

    assert result["model_source"] == "ec"
    assert "report" not in result
    assert result["native_ecmwf_report"]["contract"] == "rust-ecmwf-point-report-v1"
    assert result["dominant_burnable_cloud_base_agl_mean_m"] == 857.5
    assert result["cross_section"] == {"model_source": "ec", "profile": []}
    assert [method for method, _ in captured] == [
        "forecast_cached_ecmwf_point_to_files",
        "build_stored_cross_section",
    ]


def test_desktop_point_forecast_uses_native_route_when_enabled(monkeypatch):
    from firesky import desktop_rpc, rust_desktop_data

    service = desktop_rpc.DesktopService()
    monkeypatch.setattr(service, "_native_sessions_enabled", lambda: True)
    calls = []

    def native(payload, *, model, config, progress):
        calls.append((model, payload["model_source"]))
        return {"model_source": model, "peak_quality_index": 1.0}

    monkeypatch.setattr(rust_desktop_data, "forecast_point", native)
    result = service._forecast("point", {"name": "fixture"}, lambda _event: None)

    assert calls == [("gfs", "gfs"), ("ec", "ec")]
    assert set(result["model_results"]) == {"gfs", "ec"}
    assert result["model_errors"] == {}


def test_desktop_regional_forecast_uses_native_route_when_enabled(monkeypatch):
    from firesky import desktop_rpc, rust_desktop_data

    service = desktop_rpc.DesktopService()
    monkeypatch.setattr(service, "_native_sessions_enabled", lambda: True)
    calls = []

    def native(payload, *, model, config, progress):
        calls.append((model, payload["model_source"], payload["regions"]))
        return {"model_source": model, "point_count": 2}

    monkeypatch.setattr(rust_desktop_data, "forecast_regional", native)
    result = service._forecast(
        "regional",
        {"regions": ["custom"], "model_source": "both"},
        lambda _event: None,
    )

    assert calls == [
        ("gfs", "gfs", ["custom"]),
        ("ec", "ec", ["custom"]),
    ]
    assert set(result["model_results"]) == {"gfs", "ec"}
    assert result["model_errors"] == {}


def test_native_regional_bridge_freezes_frames_and_persists_result(
    monkeypatch, tmp_path
):
    from firesky import web_server

    context = {
        "points": [
            {
                "name": "grid-a",
                "lat": 31.25,
                "lon": 121.5,
                "timezone_name": "Asia/Shanghai",
                "elevation_m": 0.0,
                "_elevation_source": "model-grid",
            },
            {
                "name": "grid-b",
                "lat": 31.5,
                "lon": 121.75,
                "timezone_name": "Asia/Shanghai",
                "elevation_m": 0.0,
                "_elevation_source": "model-grid",
            },
        ],
        "common": {
            "_compute_region": None,
            "_grid_region": "custom",
            "radius_km": 900.0,
        },
        "cycle": datetime(2026, 11, 9, tzinfo=timezone.utc),
        "bbox": (100.0, 125.0, 45.0, 10.0),
    }
    prepared = {
        "status": "ready",
        "model_source": "gfs",
        "cycle": "2026-11-09T00:00:00+00:00",
        "hours": [0, 3],
        "elapsed_ms": 12.5,
        "model": {
            "files": [
                {"forecast_hour": 3, "path": "/tmp/gfs-003.grib2"},
                {"forecast_hour": 0, "path": "/tmp/gfs-000.grib2"},
            ]
        },
        "cams": {
            "files": [
                {
                    "requested_valid_time": "2026-11-09T00:00:00+00:00",
                    "valid_time": "2026-11-09T00:00:00+00:00",
                    "path": "/tmp/cams-00.grib2",
                    "exact": True,
                }
            ]
        },
    }
    payload = {
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "regions": ["custom"],
        "custom_bounds": {
            "west": 100.0,
            "south": 10.0,
            "east": 125.0,
            "north": 45.0,
        },
        "custom_timezone_name": "Asia/Shanghai",
        "spacing_deg": 0.25,
        "compute_backend": "auto",
    }
    captured = []

    def fake_call(method, params, *, timeout_seconds=None):
        assert method == "build_stored_frames_to_files"
        request = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        captured.append(request)
        assert request["model"] == "gfs"
        assert request["cycle_utc"] == "2026-11-09T00:00:00+00:00"
        assert request["frames"] == [
            {"hour": 0, "path": "/tmp/gfs-000.grib2"},
            {"hour": 3, "path": "/tmp/gfs-003.grib2"},
        ]
        assert request["cams"] == [
            {
                "valid_time": "2026-11-09T00:00:00+00:00",
                "path": "/tmp/cams-00.grib2",
            }
        ]
        assert request["points"][0]["elevation_source"] == "model-grid"
        assert "elevation_m" not in request["points"][0]
        assert request["include_point_core"] is False
        assert request["point_spacing_deg"] == 0.25
        assert request["derived_cache"] is None

        output = Path(params["output_dir"])
        output.mkdir()
        document = {
            "results": [
                {"name": "grid-a", "quality_index": 1.25},
                {"name": "grid-b", "quality_index": 1.0},
            ],
            "point_core": None,
            "frames": {
                "precision": {
                    "mode": "float32-native",
                    "point_spacing_deg": 0.25,
                    "model_grid_spacing_deg": 0.25,
                    "point_count": 2,
                    "processed_sample_count": 40,
                    "candidate_limit_per_sample": 300,
                    "vertical_profiles": True,
                    "full_point_coverage": True,
                    "aerosol_profile_mode": "cams-vertical-profile-v1",
                },
                "performance": {
                    "total_seconds": 1.2,
                    "assembly_seconds": 0.8,
                    "instant_scoring_seconds": 0.4,
                    "points_per_second": 1.6,
                    "samples_per_second": 33.3,
                },
                "frames": {"0": {"rows": []}},
            },
        }
        frame_path = output / "forecast.json"
        frame_path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        fake_call.manifest = {
            "metadata": {
                "frame_payload": {
                    "file": "forecast.json",
                    "identity": _identity(frame_path),
                    "contract": "rust-regional-frames-v1",
                    "public_forecast_complete": False,
                },
                "timings_ms": {
                    "native_assessment": 25.0,
                    "model_profiles_and_solar": 4.0,
                    "model_decode_and_source_hash": 1.0,
                    "model_pack_and_derive": 2.0,
                    "cams_assembly": 3.0,
                },
            }
        }
        return {"manifest_path": str(output / "manifest.json")}

    monkeypatch.setattr(rust_desktop_data, "call_engine", fake_call)
    monkeypatch.setattr(rust_desktop_data, "model_context", lambda *a, **k: context)
    monkeypatch.setattr(
        rust_desktop_data, "prepare_model_data", lambda *a, **k: prepared
    )
    monkeypatch.setattr(
        rust_desktop_data,
        "_native_regional_input_manifest",
        lambda *a, **k: {"format": "test-native-manifest-v1"},
    )
    monkeypatch.setattr(
        rust_desktop_data,
        "load_tensor_bundle",
        lambda *a, **k: SimpleNamespace(manifest=fake_call.manifest),
    )
    monkeypatch.setattr(
        web_server,
        "read_national_forecast_cache",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        web_server,
        "write_national_forecast_cache",
        lambda _payload, result, _cache_dir: result,
    )

    result = rust_desktop_data.forecast_regional(
        payload,
        model="gfs",
        config=WebServerConfig(
            gfs_cache_dir=tmp_path / "gfs",
            ecmwf_cache_dir=tmp_path / "ec",
            cams_cache_dir=tmp_path / "cams",
            regional_cache_dir=tmp_path / "regional",
        ),
    )

    assert len(captured) == 1
    assert result["scope"] == "regional"
    assert result["point_mode"] == "generated-regional-grid"
    assert result["point_count"] == 2
    assert result["success_count"] == 2
    assert result["error_count"] == 0
    assert result["regions"] == ["custom"]
    assert result["input_manifest"]["format"] == "test-native-manifest-v1"
    assert result["frame_data"]["frames"] == {"0": {"rows": []}}
    assert result["native_forecast"] == {
        "contract": "rust-desktop-regional-forecast-v1",
        "runtime_python_used": False,
        "model_source": "gfs",
        "point_payload_complete": True,
        "frame_payload_complete": True,
        "public_forecast_complete": False,
    }


def test_native_regional_bridge_does_not_serve_python_cache(
    monkeypatch, tmp_path
):
    from firesky import web_server

    entered = []
    monkeypatch.setattr(
        web_server,
        "read_national_forecast_cache",
        lambda *a, **k: {"scope": "regional", "model_source": "gfs"},
    )

    def context(*args, **kwargs):
        entered.append((args, kwargs))
        raise RuntimeError("native regional path reached")

    monkeypatch.setattr(rust_desktop_data, "model_context", context)

    with pytest.raises(RuntimeError, match="native regional path reached"):
        rust_desktop_data.forecast_regional(
            {
                "target_date": "2026-11-09",
                "phenomenon": "dusk",
                "regions": ["east-asia"],
            },
            model="gfs",
            config=WebServerConfig(
                gfs_cache_dir=tmp_path / "gfs",
                ecmwf_cache_dir=tmp_path / "ec",
                cams_cache_dir=tmp_path / "cams",
                regional_cache_dir=tmp_path / "regional",
            ),
        )
    assert entered


def test_public_regional_summary_refreshes_incomplete_native_cache(
    monkeypatch, tmp_path
):
    from firesky import web_server

    payload = {
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "regions": ["custom"],
        "custom_bounds": {
            "west": 100.0,
            "south": 10.0,
            "east": 125.0,
            "north": 45.0,
        },
        "custom_timezone_name": "Asia/Shanghai",
    }
    native = {
        "model_source": "gfs",
        "native_forecast": {
            "contract": "rust-desktop-regional-forecast-v1",
            "runtime_python_used": False,
            "public_forecast_complete": False,
        },
    }
    complete = {"model_source": "gfs", "public": True}
    calls = []
    monkeypatch.setattr(
        web_server,
        "read_national_forecast_summary",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )

    def run(request, config, progress_callback=None):
        calls.append(dict(request))
        return native if len(calls) == 1 else complete

    monkeypatch.setattr(
        web_server, "run_and_cache_national_forecast_payload", run
    )
    monkeypatch.setattr(
        web_server, "_national_forecast_summary", lambda result: result
    )

    result = web_server.run_and_cache_national_forecast_public_payload(
        payload,
        WebServerConfig(regional_cache_dir=tmp_path / "regional"),
    )

    assert result is complete
    assert len(calls) == 2
    assert calls[1]["force_refresh"] is True


def test_public_regional_backend_routes_native_and_ignores_python_cache(
    monkeypatch, tmp_path
):
    from firesky import web_server

    payload = {
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "regions": ["custom"],
        "custom_bounds": {
            "west": 100.0,
            "south": 10.0,
            "east": 125.0,
            "north": 45.0,
        },
        "custom_timezone_name": "Asia/Shanghai",
    }
    monkeypatch.setenv("FIRESKY_REGIONAL_DATA_BACKEND", "rust")
    monkeypatch.setattr(
        web_server,
        "read_national_forecast_cache",
        lambda *a, **k: {
            "model_source": "gfs",
            "native_forecast": {
                "contract": "rust-desktop-regional-forecast-v1",
                "runtime_python_used": False,
                "public_forecast_complete": False,
            },
        },
    )
    calls = []

    def native(request, *, model, config, progress, public):
        calls.append(
            {
                "request": dict(request),
                "model": model,
                "config": config,
                "progress": progress,
                "public": public,
            }
        )
        return {
            "model_source": model,
            "native_forecast": {
                "contract": "rust-public-regional-forecast-v1",
                "runtime_python_used": False,
                "public_forecast_complete": True,
            },
        }

    monkeypatch.setattr(rust_desktop_data, "forecast_regional", native)
    result = web_server.run_and_cache_national_forecast_payload(
        payload,
        WebServerConfig(regional_cache_dir=tmp_path / "regional"),
    )

    assert result["native_forecast"]["contract"] == "rust-public-regional-forecast-v1"
    assert len(calls) == 1
    assert calls[0]["model"] == "gfs"
    assert calls[0]["public"] is True
    assert "force_refresh" not in calls[0]["request"]


def test_public_regional_summary_skips_python_summary_cache_in_native_mode(
    monkeypatch, tmp_path
):
    from firesky import web_server

    payload = {
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "regions": ["custom"],
        "custom_bounds": {
            "west": 100.0,
            "south": 10.0,
            "east": 125.0,
            "north": 45.0,
        },
        "custom_timezone_name": "Asia/Shanghai",
    }
    monkeypatch.setenv("FIRESKY_REGIONAL_DATA_BACKEND", "rust")
    monkeypatch.setattr(
        web_server,
        "read_national_forecast_summary",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("Python summary cache must not be read in Rust mode")
        ),
    )
    native = {
        "contract": "rust-public-regional-forecast-v1",
        "runtime_python_used": False,
        "public_forecast_complete": True,
    }
    monkeypatch.setattr(
        rust_desktop_data,
        "forecast_regional",
        lambda *a, **k: {"model_source": "gfs", "native_forecast": native},
    )
    monkeypatch.setattr(
        web_server,
        "_national_forecast_summary",
        lambda result: {"native": result["native_forecast"]},
    )

    result = web_server.run_and_cache_national_forecast_public_payload(
        payload,
        WebServerConfig(regional_cache_dir=tmp_path / "regional"),
    )

    assert result["native"] is native


def test_native_regional_bridge_reads_real_rust_frame_artifact(
    engine_binary, monkeypatch, tmp_path
):
    request = recipe(tmp_path, count=2, batch_min_points=64)
    request["points"][1].update(lat=0.5, lon=101.5)
    initial = planned(engine_binary, tmp_path, request)
    seed_gfs(
        Path(request["gfs"]["cache_dir"]),
        initial["forecast_hours"],
    )
    plan = planned(engine_binary, tmp_path, request)
    seed_cams(
        Path(request["cams_cache_dir"]),
        [
            datetime.fromisoformat(item)
            for item in plan["cams_valid_times"]
        ],
        plan["cams_area"],
    )
    ready = planned(engine_binary, tmp_path, request)
    context = {
        "points": [
            {
                **point,
                "_elevation_source": "observer",
            }
            for point in request["points"]
        ],
        "common": {
            "_compute_region": None,
            "_grid_region": "custom",
            "radius_km": request["radius_km"],
        },
        "cycle": datetime.fromisoformat(request["cycle_utc"]),
        "bbox": tuple(initial["bbox"]),
    }
    prepared = {
        "status": "ready",
        "model_source": "gfs",
        "cycle": request["cycle_utc"],
        "hours": list(ready["forecast_hours"]),
        "elapsed_ms": 1.0,
        "model": {
            "files": [
                {
                    "forecast_hour": row["hour"],
                    "path": row["path"],
                }
                for row in ready["model_files"]
            ]
        },
        "cams": {
            "files": [
                {
                    "requested_valid_time": row["valid_time"],
                    "valid_time": row["valid_time"],
                    "path": row["path"],
                    "exact": True,
                }
                for row in ready["cams_files"]
            ]
        },
    }
    payload = {
        "target_date": request["date"],
        "phenomenon": request["phenomenon"],
        "regions": ["custom"],
        "points": request["points"],
        "custom_bounds": {
            "west": 100.0,
            "south": 0.0,
            "east": 102.0,
            "north": 1.0,
        },
        "custom_timezone_name": "Asia/Shanghai",
        "spacing_deg": 0.25,
        "compute_backend": "auto",
        "_gfs_cycle_override": request["cycle_utc"],
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(
        rust_desktop_data, "model_context", lambda *a, **k: context
    )
    monkeypatch.setattr(
        rust_desktop_data, "prepare_model_data", lambda *a, **k: prepared
    )

    config = WebServerConfig(
        gfs_cache_dir=Path(request["gfs"]["cache_dir"]),
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=Path(request["cams_cache_dir"]),
        regional_cache_dir=tmp_path / "regional",
    )
    result = rust_desktop_data.forecast_regional(
        payload,
        model="gfs",
        config=config,
    )

    assert result["scope"] == "regional"
    assert result["point_mode"] == "explicit-single-points"
    assert result["regions"] == ["custom"]
    assert result["point_count"] == 2
    assert result["success_count"] == 2
    assert result["error_count"] == 0
    assert result["native_forecast"]["runtime_python_used"] is False
    assert result["native_forecast"]["frame_payload_complete"] is True
    assert result["frame_data"]["frames"]
    assert result["input_manifest"]["model"]["source"] == "gfs"
    assert len(result["input_manifest"]["model"]["files"]) == len(
        ready["model_files"]
    )
    from firesky.web_server import _national_forecast_summary

    summary = _national_forecast_summary(result)
    assert summary["point_format"] == "array-v6"
    assert len(summary["points"]) == result["point_count"]
    public_result = rust_desktop_data.forecast_regional(
        payload,
        model="gfs",
        config=config,
        public=True,
    )
    assert public_result["native_forecast"]["contract"] == (
        "rust-public-regional-forecast-v1"
    )
    assert public_result["native_forecast"]["public_forecast_complete"] is True


def test_native_point_bridge_reads_real_rust_point_artifact(
    engine_binary, monkeypatch, tmp_path
):
    pytest.importorskip("eccodes")

    request = recipe(tmp_path, phenomenon="dusk", batch_min_points=64)
    initial = planned(engine_binary, tmp_path, request)
    seed_gfs(
        Path(request["gfs"]["cache_dir"]),
        initial["forecast_hours"],
    )
    plan = planned(engine_binary, tmp_path, request)
    seed_cams(
        Path(request["cams_cache_dir"]),
        [
            datetime.fromisoformat(item)
            for item in plan["cams_valid_times"]
        ],
        plan["cams_area"],
    )
    context = {
        "points": [
            {
                "name": request["points"][0]["name"],
                "lat": request["points"][0]["lat"],
                "lon": request["points"][0]["lon"],
                "elevation_m": request["points"][0]["elevation_m"],
                "timezone_name": request["points"][0]["timezone_name"],
                "_elevation_source": "observer",
            }
        ],
        "common": {"_compute_region": None},
        "cycle": datetime.fromisoformat(request["cycle_utc"]),
        "bbox": tuple(request["bbox"]),
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(rust_desktop_data, "model_context", lambda *a, **k: context)
    real_call = rust_desktop_data.call_engine

    def call_without_large_profile_projection(method, params, **kwargs):
        if method == "build_stored_cross_section":
            return {}
        return real_call(method, params, **kwargs)

    monkeypatch.setattr(
        rust_desktop_data, "call_engine", call_without_large_profile_projection
    )
    result = rust_desktop_data.forecast_point(
        {
            "name": request["points"][0]["name"],
            "lat": request["points"][0]["lat"],
            "lon": request["points"][0]["lon"],
            "elevation_m": request["points"][0]["elevation_m"],
            "timezone_name": request["points"][0]["timezone_name"],
            "target_date": request["date"],
            "phenomenon": request["phenomenon"],
            "radius_km": request["radius_km"],
        },
        model="gfs",
        config=WebServerConfig(
            gfs_cache_dir=Path(request["gfs"]["cache_dir"]),
            cams_cache_dir=Path(request["cams_cache_dir"]),
        ),
    )

    assert result["model_source"] == "gfs"
    assert result["forecast_mode"] == "model"
    assert result["native_forecast"]["point_payload_complete"] is True


def test_native_ec_point_bridge_reads_real_rust_report(
    engine_binary, ec_server, monkeypatch, tmp_path
):
    endpoint, _state = ec_server
    request = ec_recipe(tmp_path, endpoint, "dusk")
    _initial_path, initial_response = ec_plan(
        engine_binary, tmp_path, request, forecast=False
    )
    assert initial_response["type"] == "result", initial_response
    initial = initial_response["result"]
    seed_ec_cams(request, initial)
    request["allow_ec_download"] = False
    context = {
        "points": [
            {
                "name": request["point"]["name"],
                "lat": request["point"]["lat"],
                "lon": request["point"]["lon"],
                "elevation_m": request["point"]["elevation_m"],
                "timezone_name": request["point"]["timezone_name"],
                "_elevation_source": "observer",
            }
        ],
        "common": {"_compute_region": None},
        "cycle": datetime.fromisoformat(request["cycle_utc"]),
        "bbox": tuple(initial["bbox"]),
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(rust_desktop_data, "model_context", lambda *a, **k: context)
    result = rust_desktop_data.forecast_point(
        {
            "name": request["point"]["name"],
            "lat": request["point"]["lat"],
            "lon": request["point"]["lon"],
            "elevation_m": request["point"]["elevation_m"],
            "timezone_name": request["point"]["timezone_name"],
            "target_date": request["date"],
            "phenomenon": request["phenomenon"],
            "radius_km": request["radius_km"],
        },
        model="ec",
        config=WebServerConfig(
            gfs_cache_dir=tmp_path / "gfs",
            ecmwf_cache_dir=Path(request["ec"]["cache_dir"]),
            cams_cache_dir=Path(request["cams_cache_dir"]),
        ),
    )

    assert result["model_source"] == "ec"
    assert result["forecast_mode"] == "model"
    assert result["native_ecmwf_report"]["contract"] == "rust-ecmwf-point-report-v1"
    assert result["native_forecast"]["public_web_contract_complete"] is False
    assert result["data_provenance"]["cams_cache_used"] is True
