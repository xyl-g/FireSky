"""Immutable complete native EC response generations and registered profiles."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pytest

from firesky import national_batch
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_cross_section import identity, compare as compare_cross
from test_rust_cams_data import ads_server, config as ads_config, TOKEN as SYNTHETIC_TOKEN
from test_rust_ecmwf_point import recipe, plan, seed_cams, ec_server, ec_grib_fixture
from test_rust_migration import engine_binary, rpc

FORECAST = "forecast_ecmwf_service_cached"
PROFILE = "read_cached_ecmwf_service_cross_section"


def invoke(binary, method, params, *, unavailable=False):
    env = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python")
    if unavailable:
        env["FIRESKY_ECCODES_LIBRARY"] = "/no/ecCodes/library"
    return rpc(binary, [dict(id="test", method=method, params=params),
                        dict(id="alive", method="hello")], env=env)


def result(binary, method, params, **kwargs):
    response, alive = invoke(binary, method, params, **kwargs)
    assert alive["type"] == "result"
    assert response["type"] == "result", response
    return response["result"]


@pytest.fixture
def prepared(engine_binary, tmp_path, ec_server, request):
    endpoint, state = ec_server
    request = recipe(tmp_path, endpoint, getattr(request, "param", "dusk"))
    path, initial = plan(engine_binary, tmp_path, request)
    assert initial["type"] == "result", initial
    seed_cams(request, initial["result"])
    request["allow_ec_download"] = False
    path.write_text(json.dumps(request))
    params = dict(path=str(path), cache_dir=str(tmp_path/"responses"))
    return request, path, params, state


def profile_params(params, cached, **changes):
    point = cached["service"]["observer"]
    return dict(cache_dir=params["cache_dir"], response_key=cached["response_key"],
                lat=point["lat"], lon=point["lon"], offset_minutes=0, **changes)


def paths(cached):
    manifest = Path(cached["manifest_path"])
    generation = manifest.parent.parent
    return manifest.parent, generation/"receipt.json", generation.parent/"index.json"


def arrays(cached):
    root = Path(cached["manifest_path"]).parent
    bundle = load_tensor_bundle(root/"manifest.json", kind="ecmwf-service-forecast-v1")
    assert len(bundle.arrays) == 16
    values = {name: array.copy() for name, array in bundle.arrays.items()}
    for child in sorted(root.glob("profiles_*")):
        values[child.name] = np.load(child/"profiles.npy")
    return values


def same_arrays(first, second):
    assert first.keys() == second.keys()
    for key in first:
        # Preserve every stored float32/64 bit, including NaN payloads.
        assert first[key].dtype == second[key].dtype
        assert first[key].shape == second[key].shape
        assert first[key].tobytes() == second[key].tobytes(), key


@pytest.mark.parametrize("prepared", ["dawn", "dusk"], indirect=True)
def test_complete_cold_hit_and_offline_registered_profiles(engine_binary, prepared, tmp_path):
    request, path, params, state = prepared
    cold = result(engine_binary, FORECAST, params)
    assert cold["response_cache"]["status"] == "refresh"
    assert cold["response_cache"]["reason"] == "miss"
    assert cold["response_cache"]["source_freshness"] == "immutable-snapshot-not-current-raw-validation"
    assert cold["native_response_cache_available"]
    assert not cold["production_http_complete"] and not cold["public_forecast_complete"]
    assert not cold["service"]["response_cache_complete"]
    assert cold["service"]["response"]["cross_section_request"]["response_key"] == cold["response_key"]
    root, receipt_path, index = paths(cold)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["generation_request_source"] == identity(path)
    assert json.loads(index.read_text())["receipt"] == identity(receipt_path)
    original = arrays(cold)
    manifest_bytes = (root/"manifest.json").read_bytes()
    service_bytes = (root/"ecmwf_service.json").read_bytes()
    expected = result(engine_binary, PROFILE, profile_params(params, cold))
    before = dict(state.calls)
    shutil.rmtree(request["ec"]["cache_dir"])
    shutil.rmtree(request["cams_cache_dir"])
    warm = result(engine_binary, FORECAST, params, unavailable=True)
    assert warm["response_cache"]["status"] == "hit"
    assert warm["response_key"] == cold["response_key"]
    assert warm["service"]["report"] == cold["service"]["report"]
    assert warm["service"]["native_quality"] == cold["service"]["native_quality"]
    assert warm["service"]["provenance"] == cold["service"]["provenance"]
    same_arrays(arrays(warm), original)
    assert result(engine_binary, PROFILE, profile_params(params, warm), unavailable=True) == expected
    assert dict(state.calls) == before
    assert (root/"manifest.json").read_bytes() == manifest_bytes
    assert (root/"ecmwf_service.json").read_bytes() == service_bytes
    # A failed refresh must not remove the last published response.
    old_index = index.read_bytes()
    old_generations = set(index.parent.iterdir())
    failure, alive = invoke(engine_binary, FORECAST, dict(params, force_refresh=True), unavailable=True)
    assert failure["type"] == "error" and alive["type"] == "result"
    assert index.read_bytes() == old_index
    assert set(index.parent.iterdir()) == old_generations
    assert result(engine_binary, PROFILE, profile_params(params, cold), unavailable=True) == expected


@pytest.mark.parametrize("refresh", [dict(force_refresh=True), dict(max_age_seconds=0)])
def test_refresh_keeps_old_generation_and_full_tensors(engine_binary, prepared, refresh):
    _, _, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    expected = arrays(first)
    cross = result(engine_binary, PROFILE, profile_params(params, first))
    second = result(engine_binary, FORECAST, dict(params, **refresh))
    assert second["response_cache"]["status"] == "refresh"
    assert second["response_cache"]["reason"] == ("forced" if "force_refresh" in refresh else "expired")
    assert second["response_key"] != first["response_key"]
    same_arrays(expected, arrays(second))
    assert result(engine_binary, PROFILE, profile_params(params, first)) == cross
    assert result(engine_binary, PROFILE, profile_params(params, second)) == cross
    assert result(engine_binary, FORECAST, params)["response_key"] == second["response_key"]
    for cached in (first, second):
        assert all(p.stat().st_nlink == 1 for p in Path(cached["manifest_path"]).parent.rglob("*.npy"))


@pytest.mark.parametrize("prepared", ["dawn", "dusk"], indirect=True)
def test_selected_point_retains_complete_python_cross_section(engine_binary, prepared, tmp_path, monkeypatch):
    _, _, params, _ = prepared
    cached = result(engine_binary, FORECAST, params)
    root = paths(cached)[0]
    source = json.loads((root/"cross_section_source.json").read_text())
    combined = tmp_path/"combined.npy"
    np.save(combined, np.stack([np.load(root/v["manifest"].replace("manifest.json", "profiles.npy"))
                               for v in source["profile_bundles"]]))
    source.update(source["geometry"], layer_profile_path=str(combined))
    point = dict(lat=.5, lon=101.5, elevation_m=80.,
                 timezone_name="Asia/Shanghai" if source["phenomenon"] == "dawn" else "UTC")
    # Reference remains Python even during the all-Rust backend suite.
    for name in ("ASTRONOMY", "CLOUD", "EMPIRICAL", "ECMWF_DECODE"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")
    offsets = (-75, 0, 20) if source["phenomenon"] == "dawn" else (-20, 0, 75)
    for offset in offsets:
        read = dict(profile_params(params, cached), **point, offset_minutes=offset)
        actual = result(engine_binary, PROFILE, read, unavailable=True)
        expected = national_batch.build_national_cross_section(source, point, offset)
        compare_cross(actual, expected)
        assert len(actual["distances_km"]) == 41
    if source["phenomenon"] == "dawn":
        # Switching this local dawn date to UTC moves its solar event a day
        # beyond the stored hours. Do not silently clamp to another day.
        read = dict(profile_params(params, cached), **dict(point, timezone_name="UTC"))
        error, alive = invoke(engine_binary, PROFILE, read, unavailable=True)
        assert error["type"] == "error" and alive["type"] == "result"
        assert "does not cover valid solar samples" in error["error"]["message"]


def test_relocation_rebases_view_not_historical_artifact(engine_binary, prepared, tmp_path):
    _, _, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    root = paths(first)[0]
    original = (root/"ecmwf_service.json").read_bytes()
    cross = result(engine_binary, PROFILE, profile_params(params, first))
    relocated = tmp_path/"relocated"
    Path(params["cache_dir"]).rename(relocated)
    params["cache_dir"] = str(relocated)
    moved = result(engine_binary, FORECAST, params, unavailable=True)
    assert moved["response_key"] == first["response_key"]
    assert moved["response_cache"]["status"] == "hit"
    assert Path(moved["service"]["report"]["html_report_path"]).is_file()
    assert paths(moved)[0].parent.parent.parent == relocated
    assert (paths(moved)[0]/"ecmwf_service.json").read_bytes() == original
    assert result(engine_binary, PROFILE, profile_params(params, moved), unavailable=True) == cross


def test_semantic_identity_not_recipe_bytes_or_transfer_policy(engine_binary, prepared):
    request, path, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    request["ec"]["timeout_seconds"] = 3.
    request["allow_ec_download"] = True
    request["derived_cache"] = {}
    request["compute_backend"] = "auto"
    request["cycle_utc"] = request["cycle_utc"].replace("+00:00", "Z")
    path.write_text(json.dumps(request, indent=2))
    hit = result(engine_binary, FORECAST, params, unavailable=True)
    assert hit["response_key"] == first["response_key"]
    assert hit["response_cache"]["generation_request_source"] != identity(path)
    assert hit["response_cache"]["request_source"] == identity(path)
    assert hit["response_cache"]["generation_request_source"] == first["response_cache"]["request_source"]


@pytest.mark.parametrize("prepared", ["dawn"], indirect=True)
def test_automatic_cycle_selection_does_not_pin_old_snapshot(engine_binary, prepared):
    request, path, params, state = prepared
    first = result(engine_binary, FORECAST, params)
    del request["cycle_utc"]
    request["now_utc"] = "2026-11-09T08:00:00Z"
    path.write_text(json.dumps(request))
    # The canonical selected cycle still matches the stored explicit cycle.
    hit = result(engine_binary, FORECAST, params, unavailable=True)
    assert hit["response_key"] == first["response_key"]
    before = state.calls["head"]
    request["now_utc"] = "2026-11-10T08:00:00Z"
    path.write_text(json.dumps(request))
    error, alive = invoke(engine_binary, FORECAST, params, unavailable=True)
    assert error["type"] == "error" and alive["type"] == "result"
    assert state.calls["head"] > before  # A new eligible cycle was probed.
    assert len(list(Path(params["cache_dir"]).rglob("receipt.json"))) == 1
    assert result(engine_binary, PROFILE, profile_params(params, first), unavailable=True)["model_source"] == "ec"


@pytest.mark.parametrize("field,value", [
    ("name", "other observer"), ("lat", .3), ("lon", 100.3), ("elevation_m", 60.),
    ("timezone_name", "UTC"), ("radius_km", 100.), ("compute_backend", "cpu"),
    ("cycle_utc", "2026-11-07T00:00:00Z"), ("date", "2026-11-10"), ("phenomenon", "dawn"),
    ("source", "aws"), ("resolution", "0p4"),
])
def test_changed_forecast_identity_never_reuses_old_result(engine_binary, prepared, field, value):
    request, path, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    if field in request["point"]:
        request["point"][field] = value
    elif field in ("source", "resolution"):
        request["ec"][field] = value
    else:
        request[field] = value
    path.write_text(json.dumps(request))
    error, alive = invoke(engine_binary, FORECAST, params, unavailable=True)
    assert error["type"] == "error", error
    assert alive["type"] == "result"
    assert paths(first)[2].is_file()
    assert not list(Path(params["cache_dir"]).rglob("receipt.json"))[1:]


@pytest.mark.parametrize("target", [
    "index", "receipt", "manifest.json", "assessment.npy", "profiles_000/profiles.npy",
    "profiles_000/manifest.json", "forecast.json", "ecmwf_service.json", "report.html", "unknown",
])
def test_corruption_rebuilds_without_deleting_unowned_files(engine_binary, prepared, target):
    _, _, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    root, receipt, index = paths(first)
    damaged = index if target == "index" else receipt if target == "receipt" else root/target
    old = damaged.read_bytes() if damaged.is_file() else b""
    damaged.write_bytes(old[:-1]+b"!" if old else b"unowned")
    preserved = damaged.read_bytes()
    if target not in ("index",):
        failure, alive = invoke(engine_binary, PROFILE, profile_params(params, first))
        assert failure["type"] == "error" and alive["type"] == "result"
    refreshed = result(engine_binary, FORECAST, params)
    assert refreshed["response_cache"]["reason"] == "invalid"
    assert refreshed["response_key"] != first["response_key"]
    if target == "index":
        archived = list(index.parent.glob(".invalid-index-*.json"))
        assert len(archived) == 1 and archived[0].read_bytes() == preserved
    else:
        assert damaged.read_bytes() == preserved
    assert result(engine_binary, FORECAST, params)["response_key"] == refreshed["response_key"]


@pytest.mark.parametrize("fault", [
    "traversal", "dtype", "shape", "duplicate-array", "source",
    "receipt-key", "engine-fingerprint", "duplicate-inventory",
])
def test_forged_descriptors_fail_after_outer_checksums_are_rebound(engine_binary, prepared, fault):
    _, _, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    root, receipt_path, index_path = paths(first)
    manifest_path = root/"manifest.json"
    receipt = json.loads(receipt_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if fault in ("traversal", "dtype", "shape"):
        descriptor = manifest["arrays"]["assessment"]
        if fault == "traversal":
            descriptor["file"] = "../outside.npy"
        elif fault == "dtype":
            descriptor["dtype"] = "<i4"
        else:
            descriptor["shape"] = [1, 1]
        manifest_path.write_text(json.dumps(manifest))
    elif fault == "duplicate-array":
        encoded = json.dumps(manifest)
        descriptor = json.dumps(manifest["arrays"]["assessment"])
        manifest_path.write_text(encoded.replace('"arrays": {', f'"arrays": {{"assessment": {descriptor},', 1))
    elif fault == "source":
        receipt["generation_request_source"]["sha256"] = "0"*64
    elif fault == "receipt-key":
        receipt["key"] = "0"*64
    elif fault == "engine-fingerprint":
        receipt["identity"]["engine_source"] = "0"*64
    receipt["manifest"] = identity(manifest_path)
    receipt["files"]["manifest.json"] = receipt["manifest"]
    encoded = json.dumps(receipt)
    if fault == "duplicate-inventory":
        duplicate = json.dumps(receipt["files"]["manifest.json"])
        encoded = encoded.replace('"files": {', f'"files": {{"manifest.json": {duplicate},', 1)
    receipt_path.write_text(encoded)
    index = json.loads(index_path.read_text())
    index["receipt"] = identity(receipt_path)
    index_path.write_text(json.dumps(index))
    rebuilt = result(engine_binary, FORECAST, params)
    assert rebuilt["response_cache"]["reason"] == "invalid"
    assert rebuilt["response_key"] != first["response_key"]
    assert receipt_path.read_text() == encoded


@pytest.mark.parametrize("target", ["index", "lock", "key-directory", "generation", "array", "profile"])
def test_symlinks_never_modify_foreign_targets(engine_binary, prepared, tmp_path, target):
    _, _, params, _ = prepared
    first = result(engine_binary, FORECAST, params)
    root, _, index = paths(first)
    key = first["response_cache"]["identity_key"]
    outside = tmp_path/"foreign"
    outside.mkdir()
    sentinel = outside/"sentinel"
    sentinel.write_bytes(b"unowned")
    selected = {
        "index": index, "lock": Path(params["cache_dir"])/f"{key}.lock",
        "key-directory": index.parent, "generation": root.parent,
        "array": root/"assessment.npy", "profile": root/"profiles_000",
    }[target]
    saved = selected.with_name(selected.name+".saved")
    selected.rename(saved)
    selected.symlink_to(outside if target in ("key-directory", "generation", "profile") else sentinel)
    response, alive = invoke(engine_binary, FORECAST, params)
    assert alive["type"] == "result"
    if target in ("index", "lock", "key-directory"):
        assert response["type"] == "error", response
    else:
        assert response["type"] == "result", response
        assert response["result"]["response_cache"]["reason"] == "invalid"
    assert sentinel.read_bytes() == b"unowned"
    assert list(outside.iterdir()) == [sentinel]
    assert selected.is_symlink()


def test_concurrent_processes_publish_once_and_respect_lock_timeout(engine_binary, prepared):
    fcntl = pytest.importorskip("fcntl")
    _, _, params, _ = prepared
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(result, engine_binary, FORECAST, params) for _ in range(2)]
        values = [f.result() for f in futures]
    assert {v["response_cache"]["status"] for v in values} == {"hit", "refresh"}
    assert values[0]["response_key"] == values[1]["response_key"]
    key = values[0]["response_cache"]["identity_key"]
    with (Path(params["cache_dir"])/f"{key}.lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        error, alive = invoke(engine_binary, FORECAST, dict(params, lock_timeout_seconds=0))
        assert error["type"] == "error" and alive["type"] == "result"
    assert result(engine_binary, FORECAST, params)["response_key"] == values[0]["response_key"]
    assert len(list(Path(params["cache_dir"]).rglob("receipt.json"))) == 1


@pytest.mark.parametrize("changes", [
    dict(response_key="ec:wrong"), dict(response_key="ec-response:../:x:y"),
    dict(response_key="ec-response:"+"a"*64+":"+"B"*32+":"+"c"*64),
    dict(lat=91), dict(lon=-181), dict(lat=.5, lon=101.5),
    dict(offset_minutes=1), dict(offset_minutes=500),
    dict(elevation_m=100), dict(timezone_name="UTC"),
    dict(elevation_m=9001, timezone_name="UTC"),
    dict(elevation_m=100, timezone_name="invalid"),
    dict(elevation_m=100, timezone_name="UTC", unknown=True),
])
def test_invalid_registered_profile_keeps_rpc_alive(engine_binary, prepared, changes):
    _, _, params, _ = prepared
    cached = result(engine_binary, FORECAST, params)
    read = dict(profile_params(params, cached), **changes)
    error, alive = invoke(engine_binary, PROFILE, read)
    assert error["type"] == "error", error
    assert alive["type"] == "result"


@pytest.mark.parametrize("changes", [
    dict(max_age_seconds=-1), dict(max_age_seconds=365*86400+1),
    dict(lock_timeout_seconds=-1), dict(lock_timeout_seconds=3601),
    dict(force_refresh="true"), dict(cache_dir="relative"), dict(unknown=True),
])
def test_invalid_cache_options_do_not_publish(engine_binary, prepared, changes):
    _, _, params, _ = prepared
    error, alive = invoke(engine_binary, FORECAST, dict(params, **changes))
    assert error["type"] == "error", error
    assert alive["type"] == "result"
    assert not list(Path(params["cache_dir"]).rglob("index.json"))


def test_hit_does_not_open_ads_credentials_or_leak_environment(engine_binary, prepared, monkeypatch):
    request, path, params, _ = prepared
    cached = result(engine_binary, FORECAST, params)
    request["cams_download"] = dict(credentials_file="/no/private/credentials")
    # Validate the actual ADS config shape in its owning decoder; missing
    # credentials are harmless on an explicit-cycle immutable hit.
    path.write_text(json.dumps(request))
    monkeypatch.setenv("CDSAPI_KEY", "test-only-canary-never-persist")
    hit = result(engine_binary, FORECAST, params, unavailable=True)
    assert hit["response_key"] == cached["response_key"]
    for file in Path(params["cache_dir"]).rglob("*.json"):
        assert "test-only-canary-never-persist" not in file.read_text()


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_combined_ec_and_ads_download_publish_complete_service(
        engine_binary, tmp_path, ec_server, ads_server, monkeypatch, phenomenon):
    ec_endpoint, ec_state = ec_server
    ads_endpoint, ads_state = ads_server
    request = recipe(tmp_path, ec_endpoint, phenomenon)
    request["cams_download"] = ads_config(ads_endpoint)
    path = tmp_path/"request.json"
    path.write_text(json.dumps(request))
    params = dict(path=str(path), cache_dir=str(tmp_path/"responses"))
    cold = result(engine_binary, FORECAST, params)
    assert cold["response_cache"]["status"] == "refresh"
    assert ec_state.calls["range"] > 0 and ads_state.calls["asset"] > 0
    input_plan = json.loads((paths(cold)[0]/"input_plan.json").read_text())
    assert input_plan["inputs_located"] and input_plan["cams_download_enabled"]
    assert input_plan["cams_download"]["asset_downloads"] > 0
    original = arrays(cold)
    before_ec, before_ads = dict(ec_state.calls), dict(ads_state.calls)
    refreshed = result(engine_binary, FORECAST, dict(params, force_refresh=True))
    same_arrays(original, arrays(refreshed))
    assert ec_state.calls == before_ec
    assert ads_state.calls == before_ads
    # The durable hit needs neither a provider account nor current raw data.
    shutil.rmtree(request["ec"]["cache_dir"])
    shutil.rmtree(request["cams_cache_dir"])
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_URL", raising=False)
    request["cams_download"]["credentials_file"] = "/no/private/credentials"
    path.write_text(json.dumps(request))
    hit = result(engine_binary, FORECAST, params, unavailable=True)
    assert hit["response_key"] == refreshed["response_key"]
    assert ec_state.calls == before_ec and ads_state.calls == before_ads
    for file in Path(params["cache_dir"]).rglob("*.json"):
        assert SYNTHETIC_TOKEN not in file.read_text()
