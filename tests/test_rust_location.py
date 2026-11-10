"""Complete native location workflows, local rasters and geographic resources."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

import numpy as np
import pytest

from firesky import location as reference
from test_rust_migration import engine_binary, rpc


def identity(path):
    return dict(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def invoke(binary, params, *, extra=()):
    return rpc(binary, [dict(id="location", method="location", params=params), *extra],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python",
                 FIRESKY_ECCODES_LIBRARY="/no/ecCodes/library"))


def result(binary, params):
    response = invoke(binary, params)[0]
    assert response["type"] == "result", response
    assert not response["result"]["runtime_python_used"]
    assert not response["result"]["production_location_complete"]
    return response["result"]["result"]


@pytest.fixture
def timezone_source(tmp_path):
    root = tmp_path/"zones"
    root.mkdir()
    rings = [
        [[-180,-90], [0,-90], [0,90], [-180,90]],
        [[-2,49], [-1,49], [-1,50], [-2,50]],
        [[0,-90], [180,-90], [180,90], [0,90]],
        [[-2,49], [-1,49], [-1,50], [-2,50]],
    ]
    arrays = dict(coordinates=np.asarray(np.concatenate(rings)*10_000_000, dtype="<i4"),
        ring_offsets=np.asarray([0,4,8,12,16], dtype="<u4"),
        polygon_offsets=np.asarray([0,2,3,4], dtype="<u4"), zones=np.asarray([0,1,1], dtype="<u4"),
        table=np.full(480*240, -2, dtype="<i4"), blocks=np.full(16, -2, dtype="<i4"),
        starts=np.asarray([0], dtype="<u4"), ends=np.asarray([3], dtype="<u4"),
        payload=np.asarray([0,1,2], dtype="<u4"), last_change=np.asarray([1], dtype="<u4"))
    descriptors = {}
    for name, array in arrays.items():
        path = root/f"{name}.npy"
        np.save(path, array, allow_pickle=False)
        descriptors[name] = dict(file=path.name, dtype=array.dtype.str, shape=list(array.shape), **identity(path))
    licenses = {}
    for name in ("CODE_LICENSE", "DATA_LICENSE"):
        (root/name).write_text("Synthetic test resource, not real geographic data.")
        licenses[name] = identity(root/name)
    manifest = dict(contract="firesky-timezone-source-v1", exporter="timezonefinder-9.2.0",
        data_package_version="synthetic", data_version="synthetic", scale=10_000_000,
        root_columns=480, block_side=4, timezone_names=["Europe/London", "Europe/Paris"],
        arrays=descriptors, licenses=licenses)
    path = root/"manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def srtm(path, side=1201, mutation=None):
    rows = np.arange(side, dtype=np.int32)[:, None]
    cols = np.arange(side, dtype=np.int32)[None, :]
    values = ((rows*17+cols*3) % 30000).astype(">i2")
    if mutation:
        mutation(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(values.tobytes())
    return values


@pytest.fixture
def provider(tmp_path, monkeypatch, timezone_source):
    state = SimpleNamespace(calls=Counter(), queries=[], status={}, transient={}, headers=[],
        geo=dict(results=[dict(name="City", latitude=51.5, longitude=-.1, timezone="Europe/London",
                              elevation=42., admin2="District", admin1="Region", country="Country")]),
        nom=[dict(name="Town", lat="51.4", lon="-.2", display_name="Town, Country")],
        metadata=dict(timezone="Europe/London", elevation=80.), raw={}, invalid_json=False, redirect=False,
        started={}, gates={})

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            parsed = urlparse(self.path)
            state.calls[parsed.path] += 1
            state.queries.append((parsed.path, parse_qs(parsed.query)))
            state.headers.append({k.lower(): v for k, v in self.headers.items()})
            if parsed.path in state.started:
                state.started[parsed.path].set()
            if parsed.path in state.gates:
                assert state.gates[parsed.path].wait(15)
            status = state.transient.get(parsed.path, [])
            code = status.pop(0) if status else state.status.get(parsed.path, 200)
            payload = {"/geo": state.geo, "/nom": state.nom, "/metadata": state.metadata}.get(parsed.path)
            body = state.raw.get(parsed.path, json.dumps(payload).encode())
            if state.invalid_json and not parsed.path.startswith("/srtm"):
                body = b"not JSON"
            if state.redirect:
                code = 302
            self.send_response(code)
            if state.redirect:
                self.send_header("Location", "/unrequested")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    config = dict(geocoding_url=base+"/geo", metadata_url=base+"/metadata",
        nominatim_url=base+"/nom", srtm_base_url=base+"/srtm",
        location_cache_path=str(tmp_path/"locations.json"), srtm_cache_dir=str(tmp_path/"srtm"),
        timezone_manifest=str(timezone_source), retry_seconds=0, request_attempts=3, lock_timeout_seconds=2)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(reference, "GEOCODING_URL", config["geocoding_url"])
    monkeypatch.setattr(reference, "POINT_METADATA_URL", config["metadata_url"])
    monkeypatch.setattr(reference, "NOMINATIM_URL", config["nominatim_url"])
    monkeypatch.setattr(reference, "SRTM_BASE_URL", config["srtm_base_url"])
    monkeypatch.setattr(reference, "LOCATION_CACHE_PATH", tmp_path/"python-locations.json")
    monkeypatch.setattr(reference, "SRTM_CACHE_DIR", tmp_path/"python-srtm")
    reference._fetch_point_metadata_cached.cache_clear()
    reference._search_location_fast_cached.cache_clear()
    try:
        yield config, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        reference._fetch_point_metadata_cached.cache_clear()
        reference._search_location_fast_cached.cache_clear()


@pytest.mark.parametrize("operation", ["search", "resolve"])
@pytest.mark.parametrize("fallback", [False, True])
def test_search_resolve_fallback_order_and_full_python_parity(engine_binary, provider, operation, fallback):
    config, state = provider
    if fallback:
        state.status["/nom" if operation == "search" else "/geo"] = 500
    params = dict(operation=operation, query=" City ", config=config)
    expected = (reference.search_location_fast if operation == "search" else reference.resolve_location)(" City ")
    state.calls.clear()
    actual = result(engine_binary, params)
    assert actual == asdict(expected)
    first = "/nom" if operation == "search" else "/geo"
    second = "/geo" if operation == "search" else "/nom"
    expected_attempts = (1 if operation == "search" else 3) if fallback else 1
    assert state.calls[first] == expected_attempts
    assert bool(state.calls[second]) == fallback
    assert all(h["user-agent"] == reference.USER_AGENT for h in state.headers)
    if operation == "resolve":
        cache = json.loads(Path(config["location_cache_path"]).read_text())
        assert cache["_schema_version"] == 2 and cache["City"] == actual
        before = state.calls.copy()
        assert result(engine_binary, dict(params, allow_download=False)) == actual
        assert state.calls == before


def test_missing_open_meteo_fields_use_local_timezone_and_srtm(engine_binary, provider, monkeypatch):
    config, state = provider
    del state.geo["results"][0]["timezone"]
    del state.geo["results"][0]["elevation"]
    path = Path(config["srtm_cache_dir"])/"N51W001.hgt"
    srtm(path)
    monkeypatch.setattr(reference, "fetch_timezone", lambda *args: "Europe/London")
    monkeypatch.setattr(reference, "SRTM_CACHE_DIR", path.parent)
    expected = reference.resolve_location("City")
    assert result(engine_binary, dict(operation="resolve", query="City", config=config)) == asdict(expected)
    assert state.calls["/metadata"] == state.calls["/nom"] == 0


@pytest.mark.parametrize("lat,lon", [(51.23445,-.12345), (-0.00005,0.00005), (1.23455,2.34565), (-0.,180.)])
def test_online_metadata_matches_python_rounding(engine_binary, provider, lat, lon):
    config, state = provider
    expected = reference.fetch_point_metadata(lat, lon)
    actual = result(engine_binary, dict(operation="metadata", lat=lat, lon=lon, config=config))
    assert (actual["timezone_name"], actual["elevation_m"]) == expected
    assert actual["queried_lat"] == round(lat, 4)
    assert actual["queried_lon"] == round(lon, 4)
    assert state.calls["/metadata"] == 2


@pytest.mark.parametrize("fault", ["status", "missing", "zone", "nan"])
def test_metadata_offline_fallback_uses_complete_local_sources(engine_binary, provider, fault):
    config, state = provider
    if fault == "status": state.status["/metadata"] = 500
    elif fault == "missing": state.metadata.pop("elevation")
    elif fault == "zone": state.metadata["timezone"] = "invalid"
    else: state.metadata["elevation"] = float("nan")
    path = Path(config["srtm_cache_dir"])/"N51W001.hgt"
    srtm(path)
    actual = result(engine_binary, dict(operation="metadata", lat=51.5, lon=-.1, config=config))
    assert actual["timezone_name"] == "Europe/London"
    assert actual["source"] == "local-timezone+srtm"
    assert actual["elevation_m"] == reference.read_srtm_elevation(path, 51.5, -.1)
    assert state.calls["/metadata"] == 1
    before = state.calls.copy()
    assert result(engine_binary, dict(operation="metadata", lat=51.5, lon=-.1, config=config, allow_download=False)) == actual
    assert state.calls == before


@pytest.mark.parametrize("lat,lon,expected", [
    (0,-1,"Europe/London"), (0,1,"Europe/Paris"), (49.5,-1.5,"Europe/Paris"),
    (0,0,"Europe/London"), (49,-1.5,"Europe/London"), (50,-1.5,"Europe/Paris"),
    (0,-180,"Europe/Paris"), (0,180,"Europe/Paris"), (-90,-1,"Europe/London"), (90,1,"Europe/Paris"),
])
def test_geo_holes_exact_owned_edges_poles_and_dateline(engine_binary, timezone_source, lat, lon, expected):
    actual = result(engine_binary, dict(operation="timezone", lat=lat, lon=lon, allow_download=False,
                                     config=dict(timezone_manifest=str(timezone_source))))
    assert actual["timezone_name"] == expected
    assert actual["timezone_data"]["manifest_identity"] == identity(timezone_source)


@pytest.mark.parametrize("side", [1201, 3601])
@pytest.mark.parametrize("lat,lon", [(31.,120.), (31.123456,120.987654), (-.00001,-.00001), (-55.999,179.999)])
def test_all_raster_resolutions_interpolation_and_tile_names(engine_binary, tmp_path, side, lat, lon):
    name = reference.srtm_tile_name(lat, lon)
    path = tmp_path/f"{name}.hgt"
    srtm(path, side)
    expected = reference.read_srtm_elevation(path, lat, lon)
    actual = result(engine_binary, dict(operation="elevation", lat=lat, lon=lon, path=str(path), allow_download=False))
    assert actual["elevation_m"] == expected
    assert result(engine_binary, dict(operation="tile", lat=lat, lon=lon))["name"] == name


@pytest.mark.parametrize("voids", [1, 2, 3, 4])
def test_partial_and_all_void_samples_match_reference(engine_binary, tmp_path, voids):
    path = tmp_path/"N00E000.hgt"
    def mutate(values):
        for r, c in [(600,600), (600,601), (601,600), (601,601)][:voids]:
            values[r,c] = -32768
    srtm(path, mutation=mutate)
    params = dict(operation="elevation", lat=.5, lon=.5, path=str(path))
    if voids == 4:
        with pytest.raises(RuntimeError): reference.read_srtm_elevation(path, .5, .5)
        assert invoke(engine_binary, params)[0]["type"] == "error"
    else:
        assert result(engine_binary, params)["elevation_m"] == reference.read_srtm_elevation(path, .5, .5)


def test_srtm_download_lock_reuse_and_reference_compatibility(engine_binary, provider, tmp_path):
    config, state = provider
    original = tmp_path/"source.hgt"
    srtm(original)
    state.raw["/srtm/N51/N51W001.hgt.gz"] = gzip.compress(original.read_bytes())
    params = dict(operation="elevation", lat=51.5, lon=-.1, config=config)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: result(engine_binary, params), range(2)))
    assert values[0] == values[1]
    assert Path(values[0]["path"]).read_bytes() == original.read_bytes()
    assert state.calls["/srtm/N51/N51W001.hgt.gz"] == 1
    assert values[0]["elevation_m"] == reference.read_srtm_elevation(original, 51.5, -.1)
    assert not list(Path(config["srtm_cache_dir"]).glob(".firesky-srtm-*"))


@pytest.mark.parametrize("fault", ["html", "size", "crc", "truncated", "tail", "status", "redirect"])
def test_bad_srtm_transfer_never_publishes(engine_binary, provider, tmp_path, fault):
    config, state = provider
    path = tmp_path/"source.hgt"
    srtm(path)
    payload = gzip.compress(path.read_bytes())
    if fault == "html": payload = b"<html>no tile</html>"
    elif fault == "size": payload = gzip.compress(b"\0"*14)
    elif fault == "crc": payload = payload[:-8]+b"\0"*8
    elif fault == "truncated": payload = payload[:-10]
    elif fault == "tail": payload += b"unrequested trailing bytes"
    elif fault == "status": state.status["/srtm/N51/N51W001.hgt.gz"] = 500
    else: state.redirect = True
    state.raw["/srtm/N51/N51W001.hgt.gz"] = payload
    error, alive = invoke(engine_binary, dict(operation="elevation", lat=51.5, lon=-.1, config=config),
                          extra=[dict(id="alive", method="hello")])
    assert error["type"] == "error" and alive["type"] == "result"
    assert not list(Path(config["srtm_cache_dir"]).glob("*.hgt"))
    assert not list(Path(config["srtm_cache_dir"]).glob(".firesky-srtm-*"))
    assert state.calls["/unrequested"] == 0


def test_memory_cache_force_refresh_and_best_effort_persistent_cache(engine_binary, provider):
    config, state = provider
    search = dict(operation="search", query="Town", config=config)
    metadata = dict(operation="metadata", lat=51.5, lon=-.1, config=config)
    messages = [dict(id=str(i), method="location", params=p) for i, p in enumerate(
        [search, search, dict(search, force_refresh=True), metadata, metadata, dict(metadata, force_refresh=True)])]
    output = rpc(engine_binary, messages, env=dict(os.environ, PATH="", FIRESKY_PYTHON="/no/python"))
    assert all(v["type"] == "result" for v in output), output
    assert [v["result"]["memory_cache_hit"] for v in output] == [False, True, False, False, True, False]
    assert state.calls["/nom"] == state.calls["/metadata"] == 2
    cache = Path(config["location_cache_path"])
    cache.write_bytes(b"unowned invalid JSON")
    result(engine_binary, dict(operation="resolve", query="City", config=config))
    assert cache.read_bytes() == b"unowned invalid JSON"


def test_concurrent_resolutions_preserve_unrelated_cached_queries(engine_binary, provider):
    config, _ = provider
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda q: result(engine_binary, dict(operation="resolve", query=q, config=config)), ["a","b"]))
    cache = json.loads(Path(config["location_cache_path"]).read_text())
    assert set(cache) == {"_schema_version", "a", "b"}


@pytest.mark.parametrize("target", ["location", "srtm", "source", "array"])
def test_symlink_inputs_never_modify_foreign_files(engine_binary, provider, tmp_path, target):
    config, _ = provider
    foreign = tmp_path/"foreign"
    foreign.write_bytes(b"unowned")
    params = dict(operation="elevation", lat=51.5, lon=-.1, config=config)
    if target == "location":
        Path(config["location_cache_path"]).symlink_to(foreign)
        params = dict(operation="resolve", query="City", config=config)
    elif target == "srtm":
        root = Path(config["srtm_cache_dir"])
        root.mkdir()
        (root/"N51W001.hgt").symlink_to(foreign)
    else:
        path = Path(config["timezone_manifest"])
        selected = path if target == "source" else path.parent/"coordinates.npy"
        selected.unlink()
        selected.symlink_to(foreign)
        params = dict(operation="timezone", lat=51.5, lon=-.1, config=config)
    response = invoke(engine_binary, params)[0]
    assert response["type"] == ("result" if target == "location" else "error")
    assert foreign.read_bytes() == b"unowned"


@pytest.mark.parametrize("changes", [
    dict(lat=91), dict(lon=-181), dict(lat=None), dict(timeout_seconds=0),
    dict(timeout_seconds=121), dict(config=dict(geocoding_url="http://example.com")),
    dict(config=dict(metadata_url="https://user:secret@example.com")), dict(unknown=True),
    dict(operation="search", query=""), dict(operation="search", query="City", lat=1, lon=1),
    dict(operation="metadata", query="City"), dict(operation="metadata", path="/no/file"),
    dict(config=dict(request_attempts=0)), dict(config=dict(retry_seconds=-1)),
])
def test_invalid_inputs_fail_before_io_keep_rpc_alive(engine_binary, changes):
    params = dict(operation="metadata", lat=51.5, lon=-.1, allow_download=False)
    params.update(changes)
    error, alive = invoke(engine_binary, params, extra=[dict(id="alive", method="hello")])
    assert error["type"] == "error", error
    assert alive["type"] == "result"


def test_batch_preserves_scalar_order_rounding_duplicates_and_proof(engine_binary, timezone_source):
    points = [[0,-1], [49.5,-1.5], [0,0], [-90,-180], [1.23445,2.34565], [0,-1]]
    config = dict(timezone_manifest=str(timezone_source))
    requests = [dict(operation="timezone", lat=a, lon=b, config=config) for a,b in points]
    requests.append(dict(operation="timezones", points=points, config=config))
    output = rpc(engine_binary, [dict(id=str(i), method="location", params=p)
                                for i,p in enumerate(requests)])
    assert all(v["type"] == "result" for v in output), output
    scalar = [v["result"]["result"] for v in output[:-1]]
    batched = output[-1]["result"]["result"]
    assert batched["timezone_names"] == [v["timezone_name"] for v in scalar]
    assert all(v["timezone_data"] == batched["timezone_data"] for v in scalar)
    maximum = result(engine_binary, dict(operation="timezones", points=[[0,-1]]*4096, config=config))
    assert maximum["timezone_names"] == ["Europe/London"]*4096


@pytest.mark.parametrize("changes", [
    dict(points=[]), dict(points=[[0,0]]*4097), dict(points=[[90.000001,0]]),
    dict(points=[[0,-180.000001]]), dict(points=[[0,0,0]]), dict(points=[[0]]),
    dict(points=[[0,0]],lat=0), dict(points=[[0,0]],path="/no/file"),
    dict(points=[[0,0]],query="City"), dict(operation="timezone",points=[[0,0]],lat=0,lon=0),
])
def test_invalid_timezone_batch_is_atomic_and_keeps_rpc_alive(engine_binary, timezone_source, changes):
    params = dict(operation="timezones", config=dict(timezone_manifest=str(timezone_source)))
    params.update(changes)
    error, alive = invoke(engine_binary, params, extra=[dict(id="alive",method="hello")])
    assert error["type"] == "error" and alive["type"] == "result"


@pytest.mark.parametrize("fault", [
    "contract", "scale", "zone", "extra", "missing", "license", "checksum", "dtype",
    "shape", "fortran", "extent", "rings", "polygons", "zone_index", "table",
    "blocks", "span", "payload", "stop", "coordinates",
])
def test_malformed_geography_fails_closed_without_mutation(engine_binary, timezone_source, fault):
    manifest = json.loads(timezone_source.read_text())
    array_name = None
    if fault == "contract": manifest["contract"] = "unknown"
    elif fault == "scale": manifest["scale"] = 1
    elif fault == "zone": manifest["timezone_names"][0] = "not/a/zone"
    elif fault == "extra": manifest["arrays"]["extra"] = manifest["arrays"]["zones"]
    elif fault == "missing": del manifest["arrays"]["zones"]
    elif fault == "license": manifest["licenses"]["DATA_LICENSE"]["sha256"] = "0"*64
    elif fault == "checksum": manifest["arrays"]["zones"]["sha256"] = "0"*64
    elif fault == "dtype": manifest["arrays"]["coordinates"]["dtype"] = ">i4"
    elif fault == "shape": manifest["arrays"]["coordinates"]["shape"] = [32]
    else:
        array_name = dict(fortran="coordinates", extent="zones", rings="ring_offsets",
            polygons="polygon_offsets", zone_index="zones", table="table", blocks="blocks",
            span="ends", payload="payload", stop="last_change", coordinates="coordinates")[fault]
        path = timezone_source.parent/f"{array_name}.npy"
        values = np.load(path)
        if fault == "fortran": values = np.asfortranarray(values)
        elif fault == "rings": values[1] = values[0]
        elif fault == "polygons": values[1] = 99
        elif fault == "zone_index": values[0] = 2
        elif fault in ("table","blocks"): values[0] = -2147483648
        elif fault == "span": values[0] = 99
        elif fault == "payload": values[0] = 99
        elif fault == "stop": values[0] = 2
        elif fault == "coordinates": values[0,0] = 1800000001
        np.save(path, values, allow_pickle=False)
        if fault == "extent":
            with path.open("ab") as stream: stream.write(b"unrequested tail")
        manifest["arrays"][array_name].update(identity(path))
    timezone_source.write_text(json.dumps(manifest))
    before = {p.name: identity(p) for p in timezone_source.parent.iterdir()}
    error, alive = invoke(engine_binary, dict(operation="timezone", lat=51.5,lon=-.1,
        config=dict(timezone_manifest=str(timezone_source))),
        extra=[dict(id="alive",method="hello")])
    assert error["type"] == "error" and alive["type"] == "result", error
    assert before == {p.name: identity(p) for p in timezone_source.parent.iterdir()}


@pytest.mark.parametrize("provider_name", ["geo","nom","cache"])
def test_oversized_labels_do_not_enter_resident_cache(engine_binary, provider, provider_name):
    config, state = provider
    if provider_name == "geo":
        state.geo["results"][0]["name"] = "x"*4097
        params = dict(operation="resolve",query="City",config=config)
        expected = "Town"
    else:
        params = dict(operation="search",query="City",config=config)
        if provider_name == "nom": state.nom[0]["display_name"] = "x"*16385
        else:
            Path(config["location_cache_path"]).write_text(json.dumps({"_schema_version":2,"City":
                dict(name="x"*4097, lat=0,lon=0,timezone_name="Europe/London",elevation_m=1)}))
        expected = "City" if provider_name == "nom" else "Town"
    assert result(engine_binary,params)["name"] == expected
