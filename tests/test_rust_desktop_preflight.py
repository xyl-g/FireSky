"""Owning desktop checks versus native resolved-plan checks, without downloads."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from firesky import aerosol, desktop_data as data, rust_desktop_data, web_server as web
from test_rust_cams import write_cams
from test_rust_gfs_data import metadata
from test_rust_migration import engine_binary, rpc

CYCLE = datetime(2026, 11, 9, tzinfo=timezone.utc)
BBOX = [100., 102., 1., 0.]
AREA = [1., 100., 0., 102.]


@pytest.fixture(autouse=True)
def isolated_reference(monkeypatch):
    from firesky import rust_desktop_process
    rust_desktop_process.close()
    for name in ("DESKTOP_DATA", "GFS_DATA", "CAMS_DATA", "CAMS", "GFS_DECODE",
                 "ECMWF_DECODE", "ECMWF_DATA", "ASTRONOMY"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")
    monkeypatch.setattr(aerosol, "_CAMS_AREA_CACHE", OrderedDict())
    monkeypatch.setattr(aerosol, "_CAMS_AREA_SIDECARS", {})
    monkeypatch.setattr(aerosol, "_CAMS_AREA_SIDECAR_DIRTY", set())
    monkeypatch.setattr(aerosol, "_CAMS_PROBE_CACHE", OrderedDict())
    yield
    rust_desktop_process.close()


@pytest.fixture
def config(tmp_path):
    return web.WebServerConfig(gfs_cache_dir=tmp_path/"gfs",
        ecmwf_cache_dir=tmp_path/"ec", cams_cache_dir=tmp_path/"cams")


def context(model, *, area=AREA, hours=(6, 9), cycle=CYCLE, terrain=False):
    return dict(common=dict(model_source=model, _compute_region=None),
        cycle=cycle, times=[CYCLE+timedelta(hours=6, minutes=30)],
        hours=list(hours), bbox=tuple(BBOX), area=list(area),
        points=[dict(name="point")], terrain_pending=terrain)


def cleaned(report):
    result = copy.deepcopy(report)
    result.pop("checked_at", None)
    return result


def both(binary, monkeypatch, config, *, model="both", fast=True, contexts=None,
         publications=None, refresh=False, forbid_python=False):
    contexts = contexts or {m: context(m) for m in ("gfs", "ec")}
    monkeypatch.setattr(data, "model_context",
        lambda payload, **kw: copy.deepcopy(contexts[payload["model_source"]]))
    publication_probe = lambda sources: publications or {}
    monkeypatch.setattr(data, "published_cycles", publication_probe)
    monkeypatch.setattr(rust_desktop_data, "publications", publication_probe)
    payload = dict(model_source=model)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "python")
    expected = data.check_model_data(payload, config=config, probe=True, fast=fast, refresh=refresh)
    if forbid_python:
        def forbidden(*args, **kwargs):
            raise AssertionError("legacy cache check was reached")
        for name in ("_scan_gfs_local", "_scan_ecmwf_local", "_scan_cams_local"):
            monkeypatch.setattr(web, name, forbidden)
        monkeypatch.setattr(web.GfsNomadsClient, "find_covering_cache", forbidden)
        monkeypatch.setattr(aerosol, "find_cached_cams_bracket", forbidden)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(binary))
    actual = data.check_model_data(payload, config=config, probe=True, fast=fast, refresh=refresh)
    assert cleaned(actual) == cleaned(expected)
    return actual


def seed_models(config, hours=(6, 9), **options):
    for hour in hours:
        metadata(config.gfs_cache_dir, f"f{hour:03}.grib2", hour=hour, **options)
        path = config.ecmwf_cache_dir/f"ifs-basic-v2-2026110900-f{hour:03}.grib2"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"EC quick inventory does not decode this file")


def seed_cams(config, *, hours=(6, 9), area=AREA, key=None, real=False, mutation=None, run=CYCLE):
    config.cams_cache_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for hour in hours:
        target = CYCLE+timedelta(hours=hour)
        lead = int((target-run).total_seconds()/3600)
        path = config.cams_cache_dir/f"cams-optics-v2-{run:%Y-%m-%d-%H%M}-{lead:03}-{key or aerosol.cams_area_key(area)}.grib"
        if real:
            write_cams(path, hour=hour, mutation=mutation)
        else:
            path.write_bytes(b"not a valid GRIB; fast exact-area selection only")
        paths.append(path)
    return paths


@pytest.mark.parametrize("model", ["gfs", "ec", "both"])
@pytest.mark.parametrize("fast", [True, False])
def test_missing_caches_complete_report_and_no_python_checker(engine_binary, monkeypatch, config, model, fast):
    report = both(engine_binary, monkeypatch, config, model=model, fast=fast, forbid_python=True)
    assert not report["ready"]
    assert all(row["missing_count"] == 2 for row in report["datasets"])


@pytest.mark.parametrize("model", ["gfs", "ec", "both"])
def test_fast_checks_preserve_metadata_only_admission(engine_binary, monkeypatch, config, model):
    seed_models(config)
    seed_cams(config)
    monkeypatch.setenv("FIRESKY_ECCODES_LIBRARY", "/no/native/library")
    report = both(engine_binary, monkeypatch, config, model=model, forbid_python=True)
    assert report["ready"]


@pytest.mark.parametrize("fast", [True, False])
@pytest.mark.parametrize("mutation", [None, "missing", "duplicate", "time", "grid", "aod", "negative", "zero"])
def test_real_cams_full_validation_and_failure_semantics(
        engine_binary, monkeypatch, config, fast, mutation):
    seed_models(config)
    seed_cams(config, real=True, mutation=mutation)
    report = both(engine_binary, monkeypatch, config, fast=fast)
    if not fast and mutation not in (None, "zero"):
        assert not report["ready"]


def test_strict_bad_newest_run_falls_back_while_fast_admits_it(engine_binary, monkeypatch, config):
    seed_models(config)
    seed_cams(config, real=True)
    seed_cams(config, run=CYCLE+timedelta(hours=6))
    fast = both(engine_binary, monkeypatch, config, fast=True)
    assert fast["datasets"][-1]["request_cycle"] == "2026-11-09 06Z"
    strict = both(engine_binary, monkeypatch, config, fast=False)
    assert strict["ready"]
    assert strict["datasets"][-1]["request_cycle"] == "2026-11-09 00Z"


@pytest.mark.parametrize("delta", [0., .2, .200001, 1.])
@pytest.mark.parametrize("fast", [True, False])
def test_nonexact_area_first_header_coverage_tolerance(engine_binary, monkeypatch, config, delta, fast):
    seed_models(config)
    seed_cams(config, key="different-area", real=True)
    area = [1.+delta, 100.-delta, -delta, 102.+delta]
    contexts = {m: context(m, area=area) for m in ("gfs", "ec")}
    report = both(engine_binary, monkeypatch, config, contexts=contexts, fast=fast)
    assert report["ready"] is (delta <= .2)


def test_nonexact_sidecar_fast_path_works_without_eccodes(engine_binary, monkeypatch, config):
    seed_models(config)
    paths = seed_cams(config, key="different-area")
    records = {}
    for path in paths:
        stat = path.stat()
        records[path.name] = dict(m=stat.st_mtime_ns, s=stat.st_size, a=AREA)
    (config.cams_cache_dir/".cams-area-index-v1.json").write_text(json.dumps(records))
    monkeypatch.setenv("FIRESKY_ECCODES_LIBRARY", "/no/native/library")
    assert both(engine_binary, monkeypatch, config)["ready"]
    assert not both(engine_binary, monkeypatch, config, fast=False)["ready"]


@pytest.mark.parametrize("case", ["covering", "narrow", "diagnostic", "global", "bad-global", "zero", "stale"])
def test_gfs_covering_index_schema_and_raw_metadata(engine_binary, monkeypatch, config, case):
    seed_models(config)
    seed_cams(config)
    for path in config.gfs_cache_dir.glob("*.grib2*"):
        path.unlink()
    for hour in (6, 9):
        options = dict(hour=hour, bbox=([100.5, 101.5, .5, .25] if case=="narrow" else BBOX))
        if case=="diagnostic":
            options["diagnostic"] = True
        if case in ("global", "bad-global"):
            options["global_cache"] = True
        path, meta = metadata(config.gfs_cache_dir, f"f{hour:03}.grib2", **options)
        if case=="bad-global":
            meta["bbox"] = BBOX
            path.with_suffix(".grib2.json").write_text(json.dumps(meta))
        if case=="zero":
            path.write_bytes(b"")
        if case=="stale":
            path.unlink()
    report = both(engine_binary, monkeypatch, config, model="gfs")
    assert report["ready"] is (case in ("covering", "global"))


@pytest.mark.parametrize("same_area", [True, False])
def test_dual_areas_keep_separate_missing_keys(engine_binary, monkeypatch, config, same_area):
    area = AREA if same_area else [2., 99., -1., 103.]
    contexts = dict(gfs=context("gfs"), ec=context("ec", area=area))
    report = both(engine_binary, monkeypatch, config, contexts=contexts)
    assert report["datasets"][-1]["missing_count"] == (2 if same_area else 4)
    assert len(report["datasets"][-1]["requirements"]) == 2


@pytest.mark.parametrize("verified", [True, False])
@pytest.mark.parametrize("fresh", [True, False])
def test_publication_flags_do_not_create_readiness(engine_binary, monkeypatch, config, verified, fresh):
    seed_models(config)
    seed_cams(config, hours=(6,))
    cycle = CYCLE if fresh else CYCLE+timedelta(hours=6)
    publications = {source: dict(cycle=cycle.isoformat(),
        label=cycle.strftime("%Y-%m-%d %HZ"), verified=verified)
        for source in ("gfs", "ecmwf", "cams")}
    report = both(engine_binary, monkeypatch, config, publications=publications)
    assert not report["ready"]
    assert not report["datasets"][-1]["is_latest"]
    assert report["cams_update_cycle"] == (cycle.isoformat() if verified else None)


@pytest.mark.parametrize("regional,count", [(False, 1), (True, 2), (True, 65)])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_real_event_planner_adapter_preserves_point_and_regional_context(
        engine_binary, monkeypatch, config, regional, count, phenomenon):
    payload = dict(model_source="both", name="observer", lat=30.27, lon=120.15,
        timezone_name="Asia/Shanghai", elevation_m=8., target_date="2026-11-10",
        phenomenon=phenomenon, _gfs_cycle_override=CYCLE.isoformat(),
        _ec_cycle_override=CYCLE.isoformat())
    if regional:
        payload.update(regions=["custom"], points=[dict(
            name=f"p{i}", lat=30.+i*.001, lon=120., elevation_source="model-grid",
            timezone_name="Asia/Shanghai") for i in range(count)])
    expected = data.check_model_data(payload, regional=regional, config=config, probe=False, fast=True)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    actual = data.check_model_data(payload, regional=regional, config=config, probe=False, fast=True)
    assert actual == expected
    if regional:
        assert actual["terrain_pending"]


@pytest.mark.parametrize("invalid", ["", "auto", "native", "1"])
def test_backend_selection_is_explicit(monkeypatch, invalid):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", invalid)
    with pytest.raises(ValueError, match="FIRESKY_DESKTOP_DATA_BACKEND"):
        data.check_model_data({}, probe=False)


def params(config):
    return dict(plans=[rust_desktop_data.plan(context("gfs"), [])],
        config={k: str(getattr(config,k)) for k in ("gfs_cache_dir", "ecmwf_cache_dir", "cams_cache_dir")})


@pytest.mark.parametrize("change", [
    lambda p:p.update(plans=[]),
    lambda p:p.update(plans=p["plans"]*3),
    lambda p:p.update(plans=p["plans"]*2),
    lambda p:p["plans"][0].update(model_source="both"),
    lambda p:p["plans"][0].update(hours=[]),
    lambda p:p["plans"][0].update(hours=[-1]),
    lambda p:p["plans"][0].update(hours=[1]*257),
    lambda p:p["plans"][0].update(cams_valid_times=[]),
    lambda p:p["plans"][0].update(cams_valid_times=["2026-11-09T07:00:00Z"]),
    lambda p:p["plans"][0].update(cycle_utc="2026-11-10T00:00:00Z"),
    lambda p:p["plans"][0].update(point_count=0),
    lambda p:p["plans"][0].update(bbox=[102,100,1,0]),
    lambda p:p["plans"][0].update(area=[0,100,1,102]),
    lambda p:p["plans"][0].update(unknown=True),
    lambda p:p["config"].update(gfs_cache_dir=""),
    lambda p:p.update(publications={"cams":dict(verified=True)}),
])
def test_invalid_plans_fail_before_creating_cache_roots(engine_binary, config, change):
    request = params(config)
    change(request)
    error, hello = rpc(engine_binary, [
        dict(id="bad", method="check_desktop_data", params=request), dict(id="hi", method="hello")],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    assert error["type"] == "error"
    assert hello["type"] == "result"
    assert not config.gfs_cache_dir.exists()
    assert not config.ecmwf_cache_dir.exists()
    assert not config.cams_cache_dir.exists()


def test_bridge_never_falls_back_after_engine_failure(engine_binary, monkeypatch, config):
    monkeypatch.setattr(data, "model_context", lambda *a, **kw: context("gfs"))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    def failed(*args, **kwargs):
        raise RuntimeError("native engine failed")
    monkeypatch.setattr(rust_desktop_data, "call_engine", failed)
    with pytest.raises(RuntimeError, match="native engine failed"):
        data.check_model_data(dict(model_source="gfs"), config=config, probe=False)


@pytest.mark.parametrize("candidate_hour,available", [(6, True), (6, False), (12, True)])
def test_refresh_keeps_authoritative_ec_artifact_gate(
        engine_binary, monkeypatch, config, candidate_hour, available):
    from firesky import ecmwf_point
    calls = []
    def make_context(payload, **kw):
        cycle = datetime.fromisoformat(payload["_ec_cycle_override"]) if "_ec_cycle_override" in payload else CYCLE
        calls.append((cycle, kw.get("fast", False)))
        return context("ec", cycle=cycle, hours=(0, 3) if cycle.hour==6 else (6, 9))
    monkeypatch.setattr(data, "model_context", make_context)
    probed = []
    monkeypatch.setattr(ecmwf_point, "ecmwf_artifact_available",
        lambda *a, **kw: probed.append((a, kw)) or available)
    candidate = CYCLE+timedelta(hours=candidate_hour)
    publication_probe = lambda sources: {
        "ecmwf": dict(
            cycle=candidate.isoformat(),
            label=candidate.strftime("%Y-%m-%d %HZ"),
            verified=True,
        )
    }
    monkeypatch.setattr(data, "published_cycles", publication_probe)
    monkeypatch.setattr(rust_desktop_data, "publications", publication_probe)
    expected = data.check_model_data(dict(model_source="ec"), config=config, refresh=True, fast=True)
    expected_calls, expected_probes = list(calls), list(probed)
    calls.clear()
    probed.clear()
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    actual = data.check_model_data(dict(model_source="ec"), config=config, refresh=True, fast=True)
    assert cleaned(actual)==cleaned(expected)
    assert calls==expected_calls and probed==expected_probes
    assert actual["model_cycle"]==(candidate if candidate_hour==6 and available else CYCLE).isoformat()


@pytest.mark.parametrize("change", [
    lambda v:v.update(contract="wrong"),
    lambda v:v.update(runtime_python_used=True),
    lambda v:v.update(resolved_plan_only=False),
    lambda v:v.update(report=None),
    lambda v:v["report"].update(ready="yes"),
    lambda v:v["report"].update(datasets=[]),
    lambda v:v["report"]["datasets"][0].update(missing_count=-1),
    lambda v:v["report"]["datasets"][0].update(ready=True),
    lambda v:v["report"].update(model_cycle="wrong"),
])
def test_bridge_rejects_malformed_response_without_fallback(
        engine_binary, monkeypatch, config, change):
    frozen = context("gfs")
    monkeypatch.setattr(data,"model_context",lambda *a, **kw: copy.deepcopy(frozen))
    response = rpc(engine_binary, [dict(id="preflight",method="check_desktop_data",params=params(config))])[0]["result"]
    change(response)
    monkeypatch.setattr(rust_desktop_data,"call_engine",lambda *a, **kw: response)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND","rust")
    with pytest.raises(RuntimeError, match="Rust desktop preflight"):
        data.check_model_data(dict(model_source="gfs"),config=config,probe=False)


@pytest.mark.parametrize("fast", [True, False])
def test_cams_regular_file_links_preserve_selection(engine_binary, monkeypatch, config, fast):
    seed_models(config)
    paths = seed_cams(config, real=True)
    for path in paths:
        target = path.with_suffix(".target")
        path.rename(target)
        path.symlink_to(target)
    assert both(engine_binary, monkeypatch, config, fast=fast)["ready"]


def test_strict_selection_rechecks_changed_raw_files_in_same_process(engine_binary, config):
    seed_models(config)
    paths = seed_cams(config, real=True)
    request = params(config)
    process = subprocess.Popen([str(engine_binary), "serve"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    def call(i):
        process.stdin.write(json.dumps(dict(id=str(i), method="check_desktop_data", params=request))+"\n")
        process.stdin.flush()
        value = json.loads(process.stdout.readline())
        assert value["type"]=="result", value
        return value["result"]["report"]
    try:
        assert call(1)["ready"]
        paths[0].write_bytes(b"broken")
        assert not call(2)["ready"]
        paths[0].unlink()
        assert not call(3)["ready"]
    finally:
        process.stdin.close()
        process.wait(timeout=15)
        process.stdout.close()
        process.stderr.close()
    assert process.returncode==0
