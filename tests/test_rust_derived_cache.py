"""Native model-only durable caches retain complete tensors and diagnostics."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

import numpy as np
import pytest

fcntl = pytest.importorskip("fcntl")

from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import inputs
from test_rust_migration import engine_binary, rpc, ec_grib_fixture
from test_rust_ecmwf_data import ec_server
from test_rust_ecmwf_point import recipe, plan, seed_cams
from test_rust_input_plan import (
    recipe as gfs_recipe, planned, seed_gfs, seed_cams as seed_gfs_cams,
)


def execute(binary, path, output, method="diagnose_stored_profiles_to_files", *, extra_env=None):
    return rpc(binary, [dict(id="forecast", method=method, params=dict(
        path=str(path), output_dir=str(output)))],
        env=dict(dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"),
                 **(extra_env or {})))[0]


def bundle(binary, path, output, method="diagnose_stored_profiles_to_files", **kwargs):
    response = execute(binary, path, output, method, **kwargs)
    assert response["type"] == "result", response
    return load_tensor_bundle(Path(response["result"]["manifest_path"]), source_path=path)


def compare_tensors(left, right, *, same_source=True):
    assert left.arrays.keys() == right.arrays.keys()
    for name in left.arrays:
        a, b = left.arrays[name], right.arrays[name]
        assert a.shape == b.shape and a.dtype == b.dtype
        # Include the original NaN payload bits, not just equal_nan numerics.
        assert a.tobytes() == b.tobytes(), name
    for a, b in zip(left.manifest["metadata"]["profile_bundles"],
                    right.manifest["metadata"]["profile_bundles"], strict=True):
        if same_source:
            assert a["identity"] == b["identity"]


def save(path, request):
    path.write_text(json.dumps(request, allow_nan=False))


def statuses(value):
    return [row["status"] for row in value.manifest["metadata"]["derived_cache"]["frames"]]


def entry(root, value, index=0):
    return root / value.manifest["metadata"]["derived_cache"]["frames"][index]["key"]


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
def test_complete_cold_warm_disabled_bitwise(engine_binary, tmp_path, inputs, model):
    path, request = inputs(model=model)
    expected = bundle(engine_binary, path, tmp_path/"disabled")
    assert set(statuses(expected)) == {"disabled"}
    request["derived_cache"] = dict(directory="derived")
    save(path, request)
    cold = bundle(engine_binary, path, tmp_path/"cold")
    warm = bundle(engine_binary, path, tmp_path/"warm")
    assert set(statuses(cold)) == {"miss"}
    assert set(statuses(warm)) == {"hit"}
    assert warm.manifest["metadata"]["timings_ms"]["model_pack_and_derive"] == 0
    assert warm.manifest["metadata"]["ec_basic_proxy_coverage"] == cold.manifest["metadata"]["ec_basic_proxy_coverage"]
    compare_tensors(expected, cold)
    compare_tensors(cold, warm)
    assert not list((tmp_path/"derived").glob(".derived-pending-*"))
    for child in (tmp_path/"warm").glob("profiles_*"):
        assert child.joinpath("profiles.npy").stat().st_nlink == 1


@pytest.mark.parametrize("fault", [
    "array_hash", "array_missing", "manifest_missing", "manifest_hash_source",
    "kind", "dtype", "shape", "overflow", "key", "levels", "cells", "boundary",
    "array_symlink", "duplicate", "metadata_duplicate", "escape", "geometry", "terrain", "extra_array",
])
def test_corruption_rebuilds_preserving_unknown_files(engine_binary, tmp_path, inputs, fault):
    path, request = inputs()
    request["derived_cache"] = dict(directory="derived")
    save(path, request)
    cold = bundle(engine_binary, path, tmp_path/"cold")
    target = entry(tmp_path/"derived", cold)
    manifest_path = target/"manifest.json"
    manifest = json.loads(manifest_path.read_text())
    (target/"unowned.txt").write_text("preserve")
    if fault == "array_hash":
        with (target/"profiles.npy").open("r+b") as stream:
            stream.seek(-1, 2)
            stream.write(b"\xff")
    elif fault == "array_missing": (target/"profiles.npy").unlink()
    elif fault == "manifest_missing": manifest_path.unlink()
    elif fault == "manifest_hash_source": manifest["source"]["sha256"] = "0"*64
    elif fault == "kind": manifest["kind"] = "other"
    elif fault == "dtype": manifest["arrays"]["profiles"]["dtype"] = "<f8"
    elif fault == "shape": manifest["arrays"]["profiles"]["shape"][1] += 1
    elif fault == "overflow": manifest["arrays"]["profiles"]["shape"] = [2**63, 8]
    elif fault == "key": manifest["metadata"]["key"] = "0"*64
    elif fault == "levels": manifest["metadata"]["pressure_levels"] = [300, 200]
    elif fault == "cells": manifest["metadata"]["cells"] = 500001
    elif fault == "boundary": manifest["metadata"]["boundary_semantics"] = "made-up"
    elif fault == "array_symlink":
        other = tmp_path/"external.npy"
        shutil.copyfile(target/"profiles.npy", other)
        (target/"profiles.npy").unlink()
        (target/"profiles.npy").symlink_to(other)
    elif fault == "escape": manifest["arrays"]["profiles"]["file"] = "../profiles.npy"
    elif fault in ("geometry", "terrain"):
        name = "latitudes" if fault == "geometry" else "surface"
        values = np.load(target/f"{name}.npy")
        values[0] = np.nan
        np.save(target/f"{name}.npy", values)
        payload = (target/f"{name}.npy").read_bytes()
        manifest["arrays"][name].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    elif fault == "extra_array": manifest["arrays"]["extra"] = manifest["arrays"]["profiles"]
    if fault not in ("array_hash", "array_missing", "manifest_missing", "array_symlink"):
        text = json.dumps(manifest)
        if fault == "duplicate":
            text = text.replace('"arrays": {', '"arrays": {"profiles": '+json.dumps(manifest["arrays"]["profiles"])+",", 1)
        if fault == "metadata_duplicate":
            text = text.replace('"metadata": {', '"metadata": {"cells": 6,', 1)
        manifest_path.write_text(text)
    rebuilt = bundle(engine_binary, path, tmp_path/"rebuilt")
    assert statuses(rebuilt) == ["rebuilt", "hit", "hit"]
    compare_tensors(cold, rebuilt)
    preserved = list((tmp_path/"derived").glob(".derived-invalid-*/**/unowned.txt"))
    assert len(preserved) == 1 and preserved[0].read_text() == "preserve"
    assert not list((tmp_path/"derived").glob(".derived-pending-*"))


def test_raw_content_invalidation_and_cams_not_cached(engine_binary, tmp_path, inputs):
    path, request = inputs()
    request["derived_cache"] = dict(directory="derived")
    save(path, request)
    before = bundle(engine_binary, path, tmp_path/"before")
    # Change the source bytes without changing decoded values.
    first = Path(request["frames"][0]["path"])
    first.write_bytes(first.read_bytes()+b"\n")
    after = bundle(engine_binary, path, tmp_path/"after")
    assert statuses(after) == ["miss", "hit", "hit"]
    compare_tensors(before, after, same_source=False)
    for child in (tmp_path/"before").glob("profiles_*"):
        assert (child/"profiles.npy").read_bytes() == (
            tmp_path/"after"/child.name/"profiles.npy").read_bytes()
    # An invalid CAMS source must still fail after every model cache hit.
    Path(request["cams"][0]["path"]).write_bytes(b"not GRIB")
    result = execute(engine_binary, path, tmp_path/"bad-cams")
    assert result["type"] == "error"
    assert not (tmp_path/"bad-cams"/"manifest.json").exists()


def test_identical_raw_files_can_move_without_stale_paths(engine_binary, tmp_path, inputs):
    path, request = inputs(model="ecmwf-basic")
    request["derived_cache"] = dict(directory="derived")
    save(path, request)
    cold = bundle(engine_binary, path, tmp_path/"cold")
    moved = tmp_path/"moved"
    moved.mkdir()
    for frame in request["frames"]:
        old = Path(frame["path"])
        shutil.copyfile(old, moved/old.name)
        frame["path"] = str(Path("moved")/old.name)
    save(path, request)
    warm = bundle(engine_binary, path, tmp_path/"warm")
    assert set(statuses(warm)) == {"hit"}
    compare_tensors(cold, warm)
    for source in warm.manifest["metadata"]["sources"]:
        if source["role"].startswith("model:"):
            assert Path(source["path"]).parent == moved


def test_decoder_resource_contents_not_only_timestamps_invalidate(engine_binary, tmp_path, inputs):
    import eccodes
    path, request = inputs()
    request["derived_cache"] = dict(directory="derived")
    save(path, request)
    overlay = tmp_path/"definitions"
    overlay.mkdir()
    sentinel = overlay/"unused-cache-identity.txt"
    sentinel.write_text("first")
    env = dict(ECCODES_DEFINITION_PATH=str(overlay)+os.pathsep+eccodes.codes_definition_path())
    cold = bundle(engine_binary, path, tmp_path/"cold", extra_env=env)
    warm = bundle(engine_binary, path, tmp_path/"warm", extra_env=env)
    assert set(statuses(warm)) == {"hit"}
    stamp = sentinel.stat()
    sentinel.write_text("other")
    os.utime(sentinel, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    changed = bundle(engine_binary, path, tmp_path/"changed", extra_env=env)
    assert set(statuses(changed)) == {"miss"}
    assert changed.manifest["metadata"]["derived_cache"]["decoder"] != cold.manifest["metadata"]["derived_cache"]["decoder"]
    compare_tensors(cold, changed)


@pytest.mark.parametrize("fault", ["root_symlink", "entry_symlink", "entry_file", "lock_symlink", "lock_timeout"])
def test_cache_collisions_fail_closed(engine_binary, tmp_path, inputs, fault):
    path, request = inputs()
    root = tmp_path/"derived"
    request["derived_cache"] = dict(directory=str(root), lock_timeout_seconds=0)
    save(path, request)
    cold = bundle(engine_binary, path, tmp_path/"cold")
    target = entry(root, cold)
    lock = None
    if fault == "root_symlink":
        root.rename(tmp_path/"other-root")
        root.symlink_to(tmp_path/"other-root", target_is_directory=True)
    elif fault == "entry_symlink":
        target.rename(tmp_path/"other-entry")
        target.symlink_to(tmp_path/"other-entry", target_is_directory=True)
    elif fault == "entry_file":
        target.rename(tmp_path/"other-entry")
        target.write_text("unowned")
    else:
        lock_path = root/(target.name+".lock")
        if fault == "lock_symlink":
            lock_path.unlink()
            other = tmp_path/"unowned-lock"
            other.write_text("preserve")
            lock_path.symlink_to(other)
        else:
            lock = lock_path.open("r+b")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = execute(engine_binary, path, tmp_path/"bad")
        assert result["type"] == "error", result
        assert not (tmp_path/"bad"/"manifest.json").exists()
    finally:
        if lock: lock.close()
    if fault == "entry_file": assert target.read_text() == "unowned"
    if fault == "lock_symlink": assert (tmp_path/"unowned-lock").read_text() == "preserve"


def test_two_processes_share_one_builder(engine_binary, tmp_path, inputs):
    path, request = inputs()
    request["derived_cache"] = dict(directory="derived")
    save(path, request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: bundle(engine_binary, path, tmp_path/f"out-{index}"), range(2)))
    compare_tensors(*results)
    assert sum(status == "miss" for result in results for status in statuses(result)) == len(request["frames"])
    assert sum(status == "hit" for result in results for status in statuses(result)) == len(request["frames"])


@pytest.mark.parametrize("config", [
    {"directory": ""}, {"lock_timeout_seconds": -1}, {"lock_timeout_seconds": 3601},
    {"unexpected": True},
])
def test_invalid_config_does_not_create_cache(engine_binary, tmp_path, inputs, config):
    path, request = inputs()
    request["derived_cache"] = config
    save(path, request)
    assert execute(engine_binary, path, tmp_path/"bad")["type"] == "error"
    assert not list(tmp_path.glob(".rust-derived-v1"))


@pytest.mark.parametrize("relative_request", [False, True])
def test_gfs_planned_forecast_reuses_full_profiles(engine_binary, tmp_path, relative_request):
    request = gfs_recipe(tmp_path)
    request["derived_cache"] = dict(directory="derived") if relative_request else {}
    initial = planned(engine_binary, tmp_path, request)
    assert not list(tmp_path.rglob(".rust-derived-v1"))
    seed_gfs(Path(request["gfs"]["cache_dir"]), initial["forecast_hours"])
    seed_gfs_cams(Path(request["cams_cache_dir"]),
                  [datetime.fromisoformat(v) for v in initial["cams_valid_times"]], initial["cams_area"])
    path = tmp_path/"request.json"
    save(path, request)
    if relative_request:
        path = Path(os.path.relpath(path))
    cold = bundle(engine_binary, path, tmp_path/"cold", "forecast_cached_gfs_point_to_files")
    warm = bundle(engine_binary, path, tmp_path/"warm", "forecast_cached_gfs_point_to_files")
    assert set(statuses(cold)) == {"miss"} and set(statuses(warm)) == {"hit"}
    compare_tensors(cold, warm)
    if relative_request:
        assert (tmp_path/"derived").is_dir()


def test_ec_point_cache_reused_by_complete_service(engine_binary, tmp_path, ec_server):
    endpoint, _ = ec_server
    request = recipe(tmp_path, endpoint)
    request["derived_cache"] = dict(directory="derived")
    path, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    seed_cams(request, response["result"])
    request["allow_ec_download"] = False
    save(path, request)
    cold = bundle(engine_binary, path, tmp_path/"point", "forecast_cached_ecmwf_point_to_files")
    warm = bundle(engine_binary, path, tmp_path/"service", "forecast_ecmwf_service_to_files")
    assert set(statuses(cold)) == {"miss"} and set(statuses(warm)) == {"hit"}
    compare_tensors(cold, warm)
    service = json.loads((tmp_path/"service"/"ecmwf_service.json").read_text())
    assert service["provenance"]["native_derived_cache"]["hits"] == len(response["result"]["forecast_hours"])
    for source in service["provenance"]["source_tensor_metadata"]:
        assert Path(source["file"]).is_file()
    assert service["response"]["cross_section"]["model_source"] == "ec"
    assert (tmp_path/"service"/"report.html").stat().st_size > 10000
    request["derived_cache"] = None
    save(path, request)
    uncached = bundle(engine_binary, path, tmp_path/"uncached-service", "forecast_ecmwf_service_to_files")
    compare_tensors(warm, uncached)
    reference = json.loads((tmp_path/"uncached-service"/"ecmwf_service.json").read_text())
    assert service["native_quality"] == reference["native_quality"]
    assert service["provenance"]["dominant_burnable"] == reference["provenance"]["dominant_burnable"]
    assert service["response"]["cross_section"] == reference["response"]["cross_section"]
    for key, value in reference["report"].items():
        if key != "html_report_path":
            assert service["report"][key] == value, key
    def html(root):
        return re.sub(r"生成时间 [^<]*</span>", "生成时间 [fixed]</span>", (root/"report.html").read_text())
    assert html(tmp_path/"service") == html(tmp_path/"uncached-service")
