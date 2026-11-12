"""Native desktop model-input preparation bridge checks."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from firesky import desktop_data, rust_desktop_data
from firesky.aerosol import cams_area_key, cams_profile_bracket_times
from firesky.gfs import GfsNomadsClient
from firesky.web_server import WebServerConfig
from test_rust_migration import engine_binary


def payload(model="gfs"):
    return {
        "name": "test",
        "lat": 40.0,
        "lon": -100.0,
        "elevation_m": 100.0,
        "timezone_name": "UTC",
        "target_date": "2026-11-08",
        "phenomenon": "dusk",
        "model_source": model,
    }


def test_native_prepare_writes_provider_request_and_validates_contract(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    captured = {}

    def fake_call(method, params):
        captured["method"] = method
        captured["params"] = params
        request = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        assert request["payload"]["model_source"] == "gfs"
        assert request["config"]["cams_cache_dir"].endswith("cams")
        return {
            "contract": "rust-desktop-prepare-v1",
            "runtime_python_used": False,
            "status": "ready",
            "model_source": "gfs",
            "cycle": "2026-11-08T00:00:00+00:00",
            "hours": [0, 1],
            "terrain_passes": 0,
            "terrain_pending": False,
        }

    monkeypatch.setattr(rust_desktop_data, "call_engine", fake_call)
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    events = []
    result = rust_desktop_data.prepare_model_data(
        payload(), config=config, progress=events.append
    )
    assert captured["method"] == "desktop_prepare"
    assert result["status"] == "ready"
    assert events[-1]["percent"] == 100


def test_desktop_data_native_prepare_does_not_enter_python_provider_path(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    called = []
    monkeypatch.setattr(
        rust_desktop_data,
        "prepare_model_data",
        lambda *args, **kwargs: called.append((args, kwargs)) or {
            "status": "ready", "model_source": "gfs",
        },
    )
    monkeypatch.setattr(
        desktop_data,
        "_python_model_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Python model preparation path was used")
        ),
    )
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    result = desktop_data.prepare_model_data(payload(), config=config)
    assert result["status"] == "ready"
    assert called


def test_native_prepare_both_models_keeps_model_results_separate(monkeypatch, tmp_path):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    models = []

    def fake_call(method, params):
        request = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        model = request["payload"]["model_source"]
        models.append(model)
        return {
            "contract": "rust-desktop-prepare-v1",
            "runtime_python_used": False,
            "status": "ready",
            "model_source": model,
            "cycle": "2026-11-08T00:00:00+00:00",
            "hours": [0],
            "terrain_passes": 0,
            "terrain_pending": False,
        }

    monkeypatch.setattr(rust_desktop_data, "call_engine", fake_call)
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    result = rust_desktop_data.prepare_model_data(
        {**payload("both")}, config=config
    )
    assert models == ["gfs", "ec"]
    assert result["status"] == "ready"
    assert set(result["models"]) == {"gfs", "ec"}
    assert all(item["status"] == "ready" for item in result["models"].values())


CONTINENTS = [
    ("africa", [38.0, -18.0, -35.0, 52.0]),
    ("asia", [81.0, 26.0, -11.0, 180.0]),
    ("europe", [72.0, -25.0, 34.0, 45.0]),
    ("north_america", [84.0, -179.0, 7.0, -52.0]),
    ("south_america", [13.0, -92.0, -56.0, -34.0]),
    ("oceania", [6.0, 110.0, -50.0, 180.0]),
    ("antarctica", [-60.0, -180.0, -90.0, 180.0]),
]


def envelope_for(area):
    """Mirror engine/src/continents.rs: smallest containing continent, else
    the union bbox of intersecting continents, else the request area."""
    def contains(box):
        return (box[1][0] + 1e-9 >= area[0] and box[1][1] - 1e-9 <= area[1]
                and box[1][2] - 1e-9 <= area[2] and box[1][3] + 1e-9 >= area[3])
    def intersects(box):
        return (box[1][2] <= area[0] + 1e-9 and area[2] <= box[1][0] + 1e-9
                and box[1][1] <= area[3] + 1e-9 and area[1] <= box[1][3] + 1e-9)
    containing = [box for box in CONTINENTS if contains(box)]
    if containing:
        return min(containing, key=lambda box: (box[1][0] - box[1][2]) * (box[1][3] - box[1][1]))[1]
    matched = [box[1] for box in CONTINENTS if intersects(box)]
    if not matched:
        return list(area)
    return [max(b[0] for b in matched), min(b[1] for b in matched),
            min(b[2] for b in matched), max(b[3] for b in matched)]


@pytest.mark.parametrize("scope,use_envelope", [(None, True), ("request", False)])
def test_native_prepare_resolves_local_gfs_and_cams_without_python_provider(
    engine_binary, monkeypatch, tmp_path, scope, use_envelope
):
    pytest.importorskip("eccodes")
    from test_rust_cams import write_cams

    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    request = {
        **payload(),
        "lat": 0.5,
        "lon": 101.0,
        "timezone_name": "UTC",
        "target_date": "2026-11-09",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
    }
    if scope is not None:
        request["cams_download_scope"] = scope
    context = rust_desktop_data.model_context(request, config=config, fast=False)
    client = GfsNomadsClient(cache_dir=config.gfs_cache_dir, resolution="0p25")
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    for hour in context["hours"]:
        path = config.gfs_cache_dir / "2026-11-09" / "00Z" / "0p25" / f"f{hour:03}.grib2"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GRIB native preparation fixture")
        url = client.build_subset_url(cycle, hour, context["bbox"])
        path.with_suffix(".grib2.json").write_text(
            json.dumps({
                "model": "gfs",
                "cycle": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "forecast_hour": hour,
                "file": path.name,
                "url": url,
                "bbox": list(context["bbox"]),
            }),
            encoding="utf-8",
        )
    # Full-cycle scope (2026-11-12) downloads the continental envelope; the
    # offline prepare cache is seeded with exactly the identity the engine
    # derives from the request area.
    download_area = envelope_for(context["area"]) if use_envelope else context["area"]
    key = cams_area_key(download_area)
    targets = sorted({
        bracket
        for sample in context["times"]
        for bracket in cams_profile_bracket_times(sample)
    })
    for target in targets:
        lead = int((target - cycle).total_seconds() / 3600)
        path = config.cams_cache_dir / (
            f"cams-optics-v2-{cycle:%Y-%m-%d-%H%M}-{lead:03}-{key}.grib"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_cams(path, hour=lead)

    monkeypatch.setattr(
        desktop_data,
        "_python_model_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Python preparation provider was used")
        ),
    )
    result = desktop_data.prepare_model_data(request, config=config)
    assert result["status"] == "ready"
    assert result["model_source"] == "gfs"
    assert result["terrain_passes"] == 0
    assert result["hours"] == context["hours"]
    assert result["cams"]["missing_valid_times"] == []


def test_native_preflight_uses_native_context_in_normal_path(
    engine_binary, monkeypatch, tmp_path
):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(
        desktop_data,
        "_python_model_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("normal Rust preflight used Python context")
        ),
    )
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    result = desktop_data.check_model_data(
        {
            **payload(),
            "lat": 40.0,
            "lon": -100.0,
            "timezone_name": "UTC",
            "gfs_cycle_override": "2026-11-09T00:00:00Z",
        },
        config=config,
        probe=False,
    )
    assert result["ready"] is False


def test_native_nowcast_prepare_bridge_writes_request_and_validates_contract(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    captured = {}

    def fake_call(method, params):
        captured["method"] = method
        request = json.loads(Path(params["path"]).read_text(encoding="utf-8"))
        captured["request"] = request
        assert request["payload"]["forecast_mode"] == "nowcast"
        assert request["payload"]["model_source"] == "satellite"
        return {
            "contract": "rust-desktop-nowcast-prepare-v1",
            "runtime_python_used": False,
            "status": "ready",
            "cycle": "2026-11-08T00:00:00+00:00",
            "hours": [0, 1],
            "hsd": {
                "contract": "rust-desktop-nowcast-hsd-cache-v1",
                "runtime_python_used": False,
                "frames": [
                    {"valid_time_utc": "2026-11-08T08:00:00+00:00"},
                    {"valid_time_utc": "2026-11-08T07:50:00+00:00"},
                ],
            },
            "cams": {
                "contract": "rust-cams-data-v1",
                "missing_valid_times": [],
            },
        }

    monkeypatch.setattr(rust_desktop_data, "call_engine", fake_call)
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    result = rust_desktop_data.prepare_nowcast_data(payload(), config=config)
    assert captured["method"] == "desktop_nowcast_prepare"
    assert result["status"] == "ready"


def test_desktop_data_native_nowcast_prepare_keeps_hsd_python_boundary(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    point = type("Point", (), {"lat": 30.0, "lon": 120.0, "radius_km": 900.0})()
    request = type("Request", (), {
        "point": point,
        "radius_km": 900.0,
    })()
    context = {
        "request": request,
        "lookup": datetime(2026, 11, 8, 8, tzinfo=timezone.utc),
        "config": config,
    }
    monkeypatch.setattr(desktop_data, "nowcast_context", lambda *args, **kwargs: context)

    class Frames:
        def prepare_series(self, *args, **kwargs):
            return [
                type("Frame", (), {
                    "valid_time_utc": datetime(2026, 11, 8, 7, 55, tzinfo=timezone.utc),
                })(),
                type("Frame", (), {
                    "valid_time_utc": datetime(2026, 11, 8, 7, 50, tzinfo=timezone.utc),
                })(),
            ]

    monkeypatch.setattr(
        "firesky.satellite_cloud_column.AutoHsdCompactCacheProvider",
        lambda *args, **kwargs: Frames(),
    )
    native_calls = []
    monkeypatch.setattr(
        rust_desktop_data,
        "prepare_nowcast_data",
        lambda *args, **kwargs: native_calls.append((args, kwargs)) or {
            "status": "ready",
            "contract": "rust-desktop-nowcast-prepare-v1",
            "hsd": {"frames": [{}, {}]},
            "cams": {"missing_valid_times": []},
        },
    )
    result = desktop_data.prepare_nowcast_data(payload(), config=config)
    assert result["status"] == "ready"
    assert native_calls


def test_native_nowcast_prepare_resolves_local_hsd_gfs_and_cams(
    engine_binary, monkeypatch, tmp_path
):
    pytest.importorskip("eccodes")
    import numpy as np
    from datetime import timedelta
    from firesky.satellite_cloud_column import CalibratedSatelliteFrame, HsdCompactCache
    from test_rust_cams import write_cams

    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )
    request = {
        **payload(),
        "lat": 30.2741,
        "lon": 120.1551,
        "elevation_m": 8,
        "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "radius_km": 900,
        "reference_time_utc": "2026-11-09T08:00:00Z",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
    }
    context = desktop_data.nowcast_context(request, config=config, fast=False)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    latitudes = np.full((2, 3), 30.2741, dtype=np.float32)
    longitudes = np.full((2, 3), 120.1551, dtype=np.float32)
    for valid_time in (
        context["lookup"] - timedelta(minutes=5),
        context["lookup"] - timedelta(minutes=15),
    ):
        cache.write(
            CalibratedSatelliteFrame(
                valid_time_utc=valid_time,
                latitudes=latitudes,
                longitudes=longitudes,
                bands={
                    "B07": np.full((2, 3), 280.0, dtype=np.float32),
                    "B13": np.full((2, 3), 281.0, dtype=np.float32),
                    "B15": np.full((2, 3), 282.0, dtype=np.float32),
                },
                units={"B07": "K", "B13": "K", "B15": "K"},
                source="native-nowcast-fixture",
                quality="medium",
                range_bytes=123,
                range_blocks=3,
            ),
            context["request"].point,
            context["request"].radius_km,
        )

    client = GfsNomadsClient(
        cache_dir=config.gfs_cache_dir,
        resolution="0p25",
        region=context["region"],
    )
    cycle = context["cycle"]
    frame_times = [
        context["lookup"] - timedelta(minutes=5),
        context["lookup"] - timedelta(minutes=15),
    ]
    hours = set()
    for moment in context["times"] + frame_times:
        elapsed = (moment - cycle).total_seconds() / 3600
        if elapsed >= 0:
            left = max(0, int(elapsed // 1))
            hours.add(left)
            if abs(elapsed - left) >= 1e-6:
                hours.add(left + 1)
    for hour in sorted(hours):
        path = (
            config.gfs_cache_dir
            / f"{cycle:%Y-%m-%d}"
            / f"{cycle:%H}Z"
            / str(context["region"])
            / "0p25"
            / f"f{hour:03}.grib2"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GRIB native nowcast preparation fixture")
        path.with_suffix(".grib2.json").write_text(
            json.dumps({
                "model": "gfs",
                "cycle": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "forecast_hour": hour,
                "file": path.name,
                "url": client.build_subset_url(cycle, hour, context["bbox"]),
                "bbox": list(context["bbox"]),
            }),
            encoding="utf-8",
        )

    key = cams_area_key(context["area"])
    for target in context["cams_valid_times"]:
        lead = int((target - cycle).total_seconds() / 3600)
        path = config.cams_cache_dir / (
            f"cams-optics-v2-{cycle:%Y-%m-%d-%H%M}-{lead:03}-{key}.grib"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_cams(path, hour=lead)

    result = desktop_data.prepare_nowcast_data(request, config=config)
    assert result["status"] == "ready"
    assert result["native"]["runtime_python_used"] is False
    assert result["native"]["hsd"]["frames"]
    assert result["native"]["cams"]["missing_valid_times"] == []
