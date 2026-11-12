"""Real GRIB fixtures through the Python-free CLI and desktop workflow."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import subprocess
import time

import pytest

from firesky import aerosol, rust_desktop_data
from firesky.ecmwf_ifs import EcmwfIfsAdapter
from firesky.web_server import WebServerConfig
from test_rust_assessment import rewrite_time, write_gfs
from test_rust_input_plan import seed_cams
from test_rust_gfs_data import metadata
from test_rust_migration import ec_grib_fixture, engine_binary, rpc
from test_rust_ecmwf_point import plan as ec_plan, recipe as ec_recipe
from test_rust_cams import write_cams


@pytest.fixture
def runtime_inputs(engine_binary, ec_grib_fixture, tmp_path, monkeypatch):
    pytest.importorskip("eccodes")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    config = WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs", ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
    )
    payload = {
        "name": "runtime-fixture", "lat": 0.25, "lon": 100.25,
        "elevation_m": 50.0, "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-09", "phenomenon": "dusk",
        "radius_km": 200.0, "model_source": "both",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
        "ec_cycle_override": "2026-11-09T00:00:00Z",
    }
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    for model in ("gfs", "ec"):
        context = rust_desktop_data.model_context(
            {**payload, "model_source": model}, config=config, fast=False,
        )
        for hour in context["hours"]:
            if model == "gfs":
                path, _ = metadata(config.gfs_cache_dir, f"f{hour:03}.grib2",
                                   hour=hour, bbox=list(context["bbox"]))
                write_gfs(path, hour)
            else:
                path = (config.ecmwf_cache_dir / "2026-11-09" / "00Z"
                        / f"ifs-basic-v2-2026110900-f{hour:03}.grib2")
                path.parent.mkdir(parents=True, exist_ok=True)
                ec_grib_fixture(path, clipped=False)
                rewrite_time(path, hour)
                EcmwfIfsAdapter(config.ecmwf_cache_dir)._write_cache_metadata(
                    path, cycle, hour,
                )
        # Re-resolve after EC input discovery: its consumed CAMS area is the
        # decoded model domain rather than the initial requested envelope.
        context = rust_desktop_data.model_context(
            {**payload, "model_source": model}, config=config, fast=False,
        )
        from firesky.aerosol import cams_profile_bracket_times
        targets = sorted({
            bracket for sample in context["times"]
            for bracket in cams_profile_bracket_times(sample)
        })
        seed_cams(config.cams_cache_dir, targets, context["area"], run=cycle)
        if model == "ec":
            _, response = ec_plan(engine_binary, tmp_path, ec_recipe(tmp_path))
            assert response["type"] == "result", response
            resolved = response["result"]
            seed_cams(
                config.cams_cache_dir,
                [datetime.fromisoformat(value) for value in resolved["cams_valid_times"]],
                resolved["cams_area"], run=cycle,
            )
    env = dict(
        os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python",
        FIRESKY_GFS_CACHE_DIR=str(config.gfs_cache_dir),
        FIRESKY_ECMWF_CACHE_DIR=str(config.ecmwf_cache_dir),
        FIRESKY_CAMS_CACHE_DIR=str(config.cams_cache_dir),
        FIRESKY_REPORT_DIR=str(tmp_path / "reports"),
        FIRESKY_POINT_CACHE_DIR=str(tmp_path / "point-cache"),
        FIRESKY_REGIONAL_CACHE_DIR=str(tmp_path / "regional-cache"),
        FIRESKY_MAP_CACHE_DIR=str(tmp_path / "map-cache"),
    )
    return payload, env


def run_forecast(binary, root, payload, env, *, regional=False):
    path = root / "runtime-request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    command = [str(binary), "forecast", "--workspace", str(root), "--input", str(path)]
    if regional:
        command.append("--regional")
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_native_cli_scores_both_models_caches_and_invalidates_changed_source(
    engine_binary, runtime_inputs, tmp_path,
):
    payload, env = runtime_inputs
    result = run_forecast(engine_binary, tmp_path, payload, env)
    assert set(result["model_results"]) == {"gfs", "ec"}, result
    assert result["model_errors"] == {}
    for model, value in result["model_results"].items():
        assert value["model_source"] == model
        assert value["native_forecast"]["runtime_python_used"] is False
        assert value["cross_section"] is not None
        assert value["input_manifest"]["sources"]
        assert value["html_report_url"].startswith("/api/v1/firesky/reports/")
        assert Path(value["html_report_path"]).is_file()
    cached = run_forecast(engine_binary, tmp_path, payload, env)
    assert cached["cache"]["status"] == "hit"
    Path(result["model_results"]["gfs"]["html_report_path"]).unlink()
    regenerated = run_forecast(engine_binary, tmp_path, payload, env)
    assert regenerated["cache"]["status"] == "refresh"
    assert Path(regenerated["model_results"]["gfs"]["html_report_path"]).is_file()
    source = next(
        Path(row["path"]) for row in result["model_results"]["gfs"]["input_manifest"]["sources"]
        if row["role"].startswith("model:")
    )
    original = source.read_bytes()
    try:
        source.write_bytes(b"corrupted raw model")
        changed = run_forecast(engine_binary, tmp_path, payload, env)
        assert changed["cache"]["status"] == "refresh"
        assert set(changed["model_results"]) == {"ec"}, changed
        assert "gfs" in changed["model_errors"]
    finally:
        source.write_bytes(original)


@pytest.mark.parametrize("model", ["gfs", "ec"])
def test_native_cli_regional_has_real_points_frames_and_separate_registry(
    engine_binary, runtime_inputs, tmp_path, model,
):
    payload, env = runtime_inputs
    point = {key: payload[key] for key in
             ("name", "lat", "lon", "elevation_m", "timezone_name")}
    point["elevation_source"] = "observer"
    request = {
        **payload, "model_source": model, "points": [point],
        "regions": ["custom"], "custom_bounds": {
            "west": 100.0, "east": 102.0, "south": 0.0, "north": 1.0,
        }, "custom_timezone_name": "Asia/Shanghai", "spacing_deg": 0.25,
    }
    result = run_forecast(engine_binary, tmp_path, request, env, regional=True)
    regional = result["model_results"][model]
    assert regional["point_count"] == regional["success_count"] == 1
    assert regional["points"] and regional["frame_data"]["frames"]
    assert regional["native_forecast"]["frame_payload_complete"] is True
    assert regional["native_forecast"]["public_forecast_complete"] is False
    assert regional["input_manifest"]["sources"]
    records = list((tmp_path / "regional-cache" / "rust-runtime-v1").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["map_artifacts"][model]["identity"]["sha256"]


@contextmanager
def desktop_session(binary, workspace, env):
    process = subprocess.Popen(
        [str(binary), "desktop", "--workspace", str(workspace)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    pending = bytearray()

    def call(request_id, method, params):
        process.stdin.write((json.dumps({
            "id": request_id, "method": method, "params": params,
        }) + "\n").encode())
        process.stdin.flush()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                response = json.loads(line)
                assert response["id"] == request_id, response
                if response["type"] != "progress":
                    return response
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if ready:
                chunk = os.read(process.stdout.fileno(), 65536)
                assert chunk, process.stderr.read().decode()
                pending.extend(chunk)
        pytest.fail("native desktop response timed out")

    try:
        yield call
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def test_native_desktop_check_forecast_confirmation_and_single_use(
    engine_binary, runtime_inputs, tmp_path,
):
    payload, env = runtime_inputs
    with desktop_session(engine_binary, tmp_path, env) as call:
        checked = call("check", "check", {"kind": "point", "payload": payload})
        assert checked["type"] == "result", checked
        assert checked["result"]["report"]["ready"] is True
        session = checked["result"]["session"]
        denied = call("unconfirmed", "forecast", {"session": session})
        assert denied["type"] == "error"
        assert "confirmation" in denied["error"]["message"]
        forecast = call("forecast", "forecast", {
            "session": session,
            "confirmed": True,
            "cycle_age_confirmed": True,
        })
        assert forecast["type"] == "result", forecast
        assert set(forecast["result"]["model_results"]) == {"gfs", "ec"}
        replay = call("replay", "forecast", {
            "session": session,
            "confirmed": True,
            "cycle_age_confirmed": True,
        })
        assert replay["type"] == "error"


@pytest.mark.skipif(
    not os.environ.get("FIRESKY_NATIVE_NOWCAST_REQUEST"),
    reason="set FIRESKY_NATIVE_NOWCAST_REQUEST to a pinned, fully cached nowcast request",
)
def test_native_desktop_nowcast_cached_lifecycle(engine_binary, tmp_path):
    request = Path(os.environ["FIRESKY_NATIVE_NOWCAST_REQUEST"]).resolve()
    payload = json.loads(request.read_text())
    root = Path(__file__).resolve().parents[1]
    env = dict(
        os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python",
        FIRESKY_REPORT_DIR=str(tmp_path / "reports"),
        FIRESKY_MAP_CACHE_DIR=str(tmp_path / "map-cache"),
        FIRESKY_CAMS_CACHE_DIR=str(tmp_path / "synthetic-cams"),
    )
    with desktop_session(engine_binary, root, env) as call:
        missing = call("missing-cams", "check", {"kind": "nowcast", "payload": payload})
        assert missing["type"] == "result", missing
        assert missing["result"]["report"]["ready"] is False
    context = rpc(engine_binary, [{
        "id": "context", "method": "desktop_nowcast_context",
        "params": {"payload": payload, "fast": False},
    }])[0]
    assert context["type"] == "result", context
    seed_nowcast_cams(Path(env["FIRESKY_CAMS_CACHE_DIR"]), context["result"])
    with desktop_session(engine_binary, root, env) as call:
        checked = call("check", "check", {"kind": "nowcast", "payload": payload})
        assert checked["type"] == "result", checked
        report = checked["result"]["report"]
        assert report["ready"] is True, report
        assert report["satellite_frames"] >= 2
        session = checked["result"]["session"]
        prepared = call("prepare", "prepare", {"session": session})
        assert prepared["type"] == "result", prepared
        assert prepared["result"]["report"]["ready"] is True
        denied = call("unconfirmed", "forecast", {"session": session})
        assert denied["type"] == "error"
        forecast = call("forecast", "forecast", {"session": session, "confirmed": True})
        assert forecast["type"] == "result", forecast
        result = forecast["result"]
        assert result["forecast_mode"] == "nowcast"
        assert result["model_source"] == "satellite"
        assert result["runtime_python_used"] is False
        assert result["html_report_url"].startswith("/api/v1/firesky/reports/")
        assert Path(result["html_report_path"]).is_file()
        replay = call("replay", "forecast", {"session": session, "confirmed": True})
        assert replay["type"] == "error"


def seed_nowcast_cams(directory, context):
    """Synthetic covering GRIBs test preflight/session behavior, not real CAMS acceptance."""
    codes = pytest.importorskip("eccodes")
    directory.mkdir()
    cycle = datetime.fromisoformat(context["cycle"])
    north, west, south, east = context["area"]
    for value in context["cams_valid_times"]:
        target = datetime.fromisoformat(value)
        hour = int((target - cycle).total_seconds() / 3600)
        path = directory / (
            f"cams-optics-v2-{cycle:%Y-%m-%d-%H%M}-{hour:03}-"
            f"{aerosol.cams_area_key(context['area'])}.grib"
        )
        write_cams(path, hour=hour)
        messages = []
        with path.open("rb") as stream:
            while (handle := codes.codes_grib_new_from_file(stream)) is not None:
                messages.append(handle)
        try:
            with path.open("wb") as stream:
                for handle in messages:
                    for key, value in {
                        "dataDate": int(cycle.strftime("%Y%m%d")),
                        "dataTime": int(cycle.strftime("%H%M")),
                        "latitudeOfFirstGridPointInDegrees": north,
                        "latitudeOfLastGridPointInDegrees": south,
                        "longitudeOfFirstGridPointInDegrees": west,
                        "longitudeOfLastGridPointInDegrees": east,
                        "iDirectionIncrementInDegrees": (east - west) / 2,
                        "jDirectionIncrementInDegrees": north - south,
                    }.items():
                        codes.codes_set(handle, key, value)
                    codes.codes_write(handle, stream)
        finally:
            for handle in messages:
                codes.codes_release(handle)
