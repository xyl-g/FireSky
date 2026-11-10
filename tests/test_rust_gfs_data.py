"""GFS data access against the reference cache and a local HTTP fixture only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import subprocess
import threading
import time

import pytest

from firesky import gfs
from test_rust_assessment import inputs, ec_grib_fixture
from test_rust_migration import engine_binary, rpc

CYCLE = datetime(2026, 11, 9, tzinfo=timezone.utc)
BBOX = (100, 102, 1, 0)
BODY = b"GRIB" + b"fixture-not-a-decoded-weather-message" * 200 + b"7777"


@pytest.fixture(autouse=True)
def reference_backend(monkeypatch):
    monkeypatch.setenv("FIRESKY_GFS_DATA_BACKEND", "python")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")


def call(binary, operation, **params):
    return rpc(binary, [dict(id="gfs-data", method="gfs_data",
                            params=dict(operation=operation, **params))],
               env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]


def result(binary, operation, **params):
    response = call(binary, operation, **params)
    assert response["type"] == "result", response
    return response["result"]


@pytest.fixture
def http_source():
    state = dict(counts={}, headers=[], body=BODY, guard=threading.Lock())

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            with state["guard"]:
                state["counts"]["CONNECT"] = state["counts"].get("CONNECT", 0) + 1
            self.send_error(502, "Fixture proxy never connects to the public Internet")

        def do_GET(self):
            mode = self.path.split("?", 1)[0].strip("/")
            with state["guard"]:
                count = state["counts"][mode] = state["counts"].get(mode, 0) + 1
                state["headers"].append({key.lower(): value for key, value in self.headers.items()})
            try:
                if mode == "slow-header":
                    time.sleep(.15)
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/valid")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if mode == "error":
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = state["body"]
                if mode == "prefix" or (mode == "retry" and count == 1):
                    body = b"<html>" + body[6:]
                elif mode == "end":
                    body = body[:-4] + b"xxxx"
                elif mode == "small":
                    body = b"GRIB7777"
                self.send_response(200)
                if mode == "chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                elif mode != "no-length":
                    self.send_header("Content-Length", str(
                        513 * 1024 * 1024 if mode == "oversize" else len(body) + int(mode == "truncated") * 100))
                self.send_header("Connection", "close")
                self.end_headers()
                if mode == "oversize":
                    return
                if mode == "slow-body":
                    self.wfile.write(body[:4])
                    self.wfile.flush()
                    time.sleep(.15)
                    self.wfile.write(body[4:])
                elif mode == "chunked":
                    for start in range(0, len(body), 137):
                        chunk = body[start:start + 137]
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(body)
                self.close_connection = True
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.parametrize("resolution", ["0p25", "0p50"])
@pytest.mark.parametrize("diagnostic", [False, True])
@pytest.mark.parametrize("bbox", [None, BBOX, (-1.01, .01, 89.99, -89.99),
                                  (179.9, -179.9, 30.001, 29.999),
                                  (-0.0, 0.0, -0.0, -0.0),
                                  (-180, 180, 90, -90)])
def test_exact_nomads_url_and_selector_identity(engine_binary, tmp_path, resolution, diagnostic, bbox):
    client = gfs.GfsNomadsClient(cache_dir=tmp_path / "reference", resolution=resolution)
    cycles = [CYCLE, datetime.fromisoformat("2026-11-09T01:02:03.123456+08:00")]
    commands = []
    expected = []
    for cycle in cycles:
        for hour in (0, 9, 120, 123, 384):
            expected.append(client.build_subset_url(cycle, hour, bbox,
                            include_diagnostic_fields=diagnostic))
            commands.append(dict(id=str(len(commands)), method="gfs_data", params=dict(
                operation="url", cycle_utc=cycle.isoformat(), forecast_hour=hour,
                bbox=bbox, resolution=resolution, include_diagnostic_fields=diagnostic)))
    responses = rpc(engine_binary, commands,
                    env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    for response, url in zip(responses, expected):
        assert response["type"] == "result", response
        assert response["result"]["url"] == url


def metadata(root, name, *, bbox=(99, 105, 5, -1), global_cache=False,
             diagnostic=False, cycle=CYCLE, hour=9, resolution="0p25", raw=True):
    client = gfs.GfsNomadsClient(cache_dir=root, resolution=resolution)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw:
        path.write_bytes(BODY)
    url = client.build_subset_url(cycle, hour, None if global_cache else bbox,
                                 include_diagnostic_fields=diagnostic)
    data = dict(model="gfs", cycle=cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
                forecast_hour=hour, file=path.name, url=url,
                bbox=[-180, 180, 90, -90] if global_cache else bbox)
    if global_cache:
        data["global"] = True
    path.with_suffix(".grib2.json").write_text(json.dumps(data))
    return path, data


@pytest.mark.parametrize("case", ["covering", "global", "diagnostic", "resolution", "zero",
                                  "missing", "cycle", "hour", "spatial-global", "bad-global",
                                  "legacy-root", "global-request", "first-lexical",
                                  "numeric-strings", "float-hour"])
def test_cache_admission_and_python_index_interoperability(engine_binary, tmp_path, case):
    root = tmp_path / "中文-cache"
    options = {}
    if case in ("global", "spatial-global", "bad-global", "global-request"):
        options["global_cache"] = True
    if case == "diagnostic": options["diagnostic"] = True
    if case == "resolution": options["resolution"] = "0p50"
    if case == "cycle": options["cycle"] = CYCLE.replace(hour=6)
    if case == "hour": options["hour"] = 12
    if case == "missing": options["raw"] = False
    path, data = metadata(root, "2026-11-09/00Z/0p25/a.grib2", **options)
    if case == "zero": path.write_bytes(b"")
    if case == "spatial-global":
        data["url"] += "&subregion="
    if case == "bad-global":
        data["bbox"] = BBOX
    if case == "numeric-strings":
        data["bbox"] = [str(value) for value in data["bbox"]]
        data["forecast_hour"] = " 9 "
    if case == "float-hour":
        data["forecast_hour"] = 9.8  # Existing Python metadata admission uses int().
    if case == "legacy-root":
        replacement = root / path.name
        path.rename(replacement)
    if case == "first-lexical":
        metadata(root, "2026-11-09/00Z/0p25/z.grib2", bbox=BBOX)
    path.with_suffix(".grib2.json").write_text(json.dumps(data))
    client = gfs.GfsNomadsClient(cache_dir=root)
    bbox = None if case == "global-request" else BBOX
    url = client.build_subset_url(CYCLE, 9, bbox)
    wanted = client.find_covering_cache(CYCLE, 9, bbox, url)
    index_path, _ = gfs._gfs_metadata_index_paths(root)
    index_before = index_path.read_bytes()
    got = result(engine_binary, "find", cache_dir=str(root), cycle_utc=CYCLE.isoformat(),
                 forecast_hour=9, bbox=bbox, url=url)
    assert got["path"] == (str(wanted) if wanted else None)
    assert index_path.read_bytes() == index_before  # Reads Python's index, not a private substitute.
    index_path.unlink()
    got = result(engine_binary, "find", cache_dir=str(root), cycle_utc=CYCLE.isoformat(),
                 forecast_hour=9, bbox=bbox, url=url)
    assert got["path"] == (str(wanted) if wanted else None)
    assert gfs._read_gfs_metadata_index(root) is not None  # Python can read Rust's rebuilt index.
    assert client.find_covering_cache(CYCLE, 9, bbox, url) == wanted


@contextmanager
def stream(binary):
    process = subprocess.Popen([str(binary), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    def send(params):
        process.stdin.write(json.dumps(dict(id="live", method="gfs_data", params=params)) + "\n")
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 10)[0], "engine response timed out"
        response = json.loads(process.stdout.readline())
        assert response["type"] == "result", response
        return response["result"]
    try:
        yield send
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        assert process.returncode == 0, process.stderr.read()
        process.stdout.close()
        process.stderr.close()


def test_live_index_invalidation_rebuilds_and_preserves_other_files(engine_binary, tmp_path):
    root = tmp_path / "cache"
    first, _ = metadata(root, "a/first.grib2")
    second, _ = metadata(root, "b/second.grib2")
    keep = root / "keep.txt"
    keep.write_text("unowned")
    client = gfs.GfsNomadsClient(cache_dir=root)
    params = dict(operation="find", cache_dir=str(root), cycle_utc=CYCLE.isoformat(),
                  forecast_hour=9, bbox=BBOX, url=client.build_subset_url(CYCLE, 9, BBOX))
    with stream(engine_binary) as send:
        assert send(params)["path"] == str(first)
        # Metadata changes do not necessarily alter a directory's timestamp.
        p = first.with_suffix(".grib2.json")
        data = json.loads(p.read_text())
        data["forecast_hour"] = 8
        p.write_text(json.dumps(data))
        assert send(params)["path"] == str(second)
        new, _ = metadata(root, "0/new-directory/new.grib2")
        assert send(params)["path"] == str(new)
        index_path, _ = gfs._gfs_metadata_index_paths(root)
        index_path.write_text('{"format":"broken"}')
        assert send(params)["path"] == str(new)
        index_path.write_text("{")
        assert send(params)["path"] == str(new)
        new.with_suffix(".grib2.json").unlink()
        assert send(params)["path"] == str(second)
    assert keep.read_text() == "unowned"


@pytest.mark.parametrize("mode", ["valid", "no-length", "chunked", "redirect", "retry"])
def test_checked_download_and_shared_metadata(engine_binary, http_source, tmp_path, mode):
    base, state = http_source
    root = tmp_path / "download"
    url = f"{base}/{mode}?request=fixture"
    got = result(engine_binary, "download", cache_dir=str(root), url=url, bbox=BBOX,
                 cycle_utc=CYCLE.isoformat(), forecast_hour=9, region="china", download_attempts=2)
    path = Path(got["path"])
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    assert path == root / "2026-11-09/00Z/china/0p25" / f"{digest}.grib2"
    assert path.read_bytes() == BODY
    assert got["identity"] == dict(bytes=len(BODY), sha256=hashlib.sha256(BODY).hexdigest())
    assert got["model_fields_validated"] is False
    assert got["content_verified"] is True
    for headers in state["headers"]:
        assert headers["accept-encoding"] == "identity"
        assert headers["user-agent"] == "firesky/0.1 burning-sunset-engine"
    client = gfs.GfsNomadsClient(cache_dir=root)
    assert client.find_covering_cache(CYCLE, 9, BBOX, url) == path
    before = dict(state["counts"])
    cached = result(engine_binary, "download", cache_dir=str(root), url=url, bbox=BBOX,
                    cycle_utc=CYCLE.isoformat(), forecast_hour=9, region="china")
    assert cached["status"] == "exact_cache" and cached["content_verified"] is False
    assert state["counts"] == before
    assert not list(root.rglob(".firesky-tmp-*"))


@pytest.mark.parametrize("mode", ["prefix", "end", "small", "truncated", "oversize", "error",
                                  "slow-header", "slow-body"])
def test_failed_transfers_do_not_publish_or_leak_temporary_files(engine_binary, http_source, tmp_path, mode):
    base, state = http_source
    root = tmp_path / mode
    root.mkdir()
    keep = root / "keep"
    keep.write_text("unowned")
    got = call(engine_binary, "download", cache_dir=str(root), url=f"{base}/{mode}",
               download_attempts=1, timeout_seconds=.03 if mode.startswith("slow") else 2)
    assert got["type"] == "error", got
    assert "failed after 1 attempts" in got["error"]["message"]
    assert not list(root.rglob("*.grib2"))
    assert not list(root.rglob("*.grib2.json"))
    assert not list(root.rglob(".firesky-tmp-*"))
    assert keep.read_text() == "unowned"


def test_concurrent_processes_share_one_download(engine_binary, http_source, tmp_path):
    base, state = http_source
    root = tmp_path / "shared"
    params = dict(cache_dir=str(root), url=f"{base}/valid", cycle_utc=CYCLE.isoformat(),
                  forecast_hour=9, bbox=BBOX, download_attempts=1)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: result(engine_binary, "download", **params), range(6)))
    assert state["counts"] == {"valid": 1}
    assert len({row["path"] for row in responses}) == 1
    assert sum(row["status"] == "downloaded" for row in responses) == 1
    assert Path(responses[0]["path"]).read_bytes() == BODY
    assert not list(root.rglob(".firesky-tmp-*"))


def test_python_flock_contention_and_post_download_metadata_recovery(engine_binary, http_source, tmp_path):
    base, state = http_source
    root = tmp_path / "cache"
    url = f"{base}/valid"
    target = root / "2026-11-09/00Z/0p25" / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.grib2"
    target.parent.mkdir(parents=True)
    params = dict(cache_dir=str(root), url=url, cycle_utc=CYCLE.isoformat(),
                  forecast_hour=9, bbox=BBOX, lock_timeout_seconds=.02, download_attempts=1)
    with target.with_suffix(".grib2.lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        got = call(engine_binary, "download", **params)
        assert got["type"] == "error" and "lock timed out" in got["error"]["message"]
        assert not state["counts"]
    _, index_lock = gfs._gfs_metadata_index_paths(root)
    with index_lock.open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        got = call(engine_binary, "download", **params)
        assert got["type"] == "error" and "lock timed out" in got["error"]["message"]
        assert target.read_bytes() == BODY  # Complete raw file survives a metadata publication failure.
        assert not target.with_suffix(".grib2.json").exists()
    got = result(engine_binary, "download", **params)
    assert got["status"] == "exact_cache"
    assert state["counts"] == {"valid": 1}
    assert target.with_suffix(".grib2.json").is_file()


def test_opt_in_bridge_never_uses_python_network_or_url_builder(engine_binary, http_source, tmp_path, monkeypatch):
    from firesky import rust_gfs_data
    base, state = http_source
    client = gfs.GfsNomadsClient(cache_dir=tmp_path / "bridge")
    wanted_url = client.build_subset_url(CYCLE, 9, BBOX)
    monkeypatch.setenv("FIRESKY_GFS_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    def forbidden(*args, **kwargs):
        raise AssertionError("Python data access was invoked")
    monkeypatch.setattr(gfs.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(client, "_download_once", forbidden)
    monkeypatch.setattr(gfs, "canonical_subset_bbox", forbidden)
    assert rust_gfs_data.enabled()
    assert client.build_subset_url(CYCLE, 9, BBOX) == wanted_url
    path = client.download(f"{base}/valid", CYCLE, 9, BBOX)
    assert path.read_bytes() == BODY
    assert client.find_covering_cache(CYCLE, 9, BBOX, f"{base}/valid") == path


def test_explicit_cache_only_batch_reports_missing_hours_without_network(engine_binary, tmp_path):
    root = tmp_path / "batch"
    first, _ = metadata(root, "f009.grib2")
    third, _ = metadata(root, "f015.grib2", hour=15)
    got = result(engine_binary, "resolve", cache_dir=str(root), cycle_utc=CYCLE.isoformat(),
                 forecast_hours=[9, 12, 15], bbox=BBOX, allow_download=False)
    assert got["complete"] is False and got["missing_hours"] == [12]
    assert [row["path"] for row in got["files"]] == [str(first), str(third)]
    assert [row["hour"] for row in got["files"]] == [9, 15]
    assert all(row["content_verified"] is False for row in got["files"])


@pytest.mark.parametrize("changes", [
    {"resolution": "0p10"}, {"region": "../escape"}, {"region": "."}, {"region": ""},
    {"timeout_seconds": 0}, {"timeout_seconds": 3601}, {"download_attempts": 0},
    {"download_attempts": 33}, {"lock_timeout_seconds": -1},
    {"url": "file:///tmp/fixture"}, {"url": "http://name:secret@localhost/"},
    {"url": "http://localhost/file#fragment"}, {"bbox": [181, 102, 1, 0]},
    {"bbox": [100, 102, -1, 0]}, {"unexpected": True},
])
def test_invalid_download_configuration_never_publishes(engine_binary, tmp_path, changes):
    params = dict(cache_dir=str(tmp_path / "cache"), url="http://127.0.0.1:1/unused",
                  download_attempts=1)
    params.update(changes)
    response = call(engine_binary, "download", **params)
    assert response["type"] == "error", response
    assert not list(tmp_path.rglob("*.grib2"))
    assert not list(tmp_path.rglob(".firesky-tmp-*"))


@pytest.mark.parametrize("existing", ["zero", "nonempty", "symlink"])
def test_exact_cache_admission_and_destination_ownership(engine_binary, http_source, tmp_path, existing):
    base, state = http_source
    root = tmp_path / "cache"
    url = f"{base}/valid"
    target = root / "undated" / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.grib2"
    target.parent.mkdir(parents=True)
    unowned = tmp_path / "unowned"
    unowned.write_bytes(b"unowned")
    if existing == "symlink":
        target.symlink_to(unowned)
    else:
        target.write_bytes(b"" if existing == "zero" else b"not a GRIB")
    response = call(engine_binary, "download", cache_dir=str(root), url=url, download_attempts=1)
    if existing == "symlink":
        assert response["type"] == "error", response
        assert target.is_symlink() and not state["counts"]
    elif existing == "nonempty":
        assert response["result"]["content_verified"] is False
        assert response["result"]["status"] == "exact_cache"
        assert target.read_bytes() == b"not a GRIB" and not state["counts"]
    else:
        assert response["result"]["status"] == "downloaded"
        assert target.read_bytes() == BODY and state["counts"] == {"valid": 1}
    assert unowned.read_bytes() == b"unowned"


def test_bad_persisted_index_paths_are_rebuilt(engine_binary, tmp_path):
    root = tmp_path / "cache"
    wanted, _ = metadata(root, "valid.grib2")
    client = gfs.GfsNomadsClient(cache_dir=root)
    url = client.build_subset_url(CYCLE, 9, BBOX)
    assert client.find_covering_cache(CYCLE, 9, BBOX, url) == wanted
    index_path, _ = gfs._gfs_metadata_index_paths(root)
    data = json.loads(index_path.read_bytes())
    data["entries"][0]["file"] = "../../outside.grib2"
    index_path.write_text(json.dumps(data))
    assert result(engine_binary, "find", cache_dir=str(root), cycle_utc=CYCLE.isoformat(),
                  forecast_hour=9, bbox=BBOX, url=url)["path"] == str(wanted)


def test_downloaded_real_grib_reaches_rust_decoder(engine_binary, http_source, tmp_path):
    from test_rust_assessment import write_gfs
    from firesky.tensor_bundle import load_tensor_bundle
    source = write_gfs(tmp_path / "real.grib2", 9)
    base, state = http_source
    state["body"] = source.read_bytes()
    downloaded = result(engine_binary, "download", cache_dir=str(tmp_path / "cache"),
                        url=f"{base}/valid", cycle_utc=CYCLE.isoformat(),
                        forecast_hour=9, bbox=BBOX, download_attempts=1)
    output = tmp_path / "decoded"
    response = rpc(engine_binary, [dict(id="decode", method="decode_gfs_to_files",
                   params=dict(path=downloaded["path"], output_dir=str(output)))],
                   env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="gfs-decoded-v1",
                                source_path=downloaded["path"])
    assert bundle.arrays["latitudes"].shape == (6,)
    assert bundle.arrays["longitudes"].shape == (6,)
    assert bundle.manifest["source"]["sha256"] == hashlib.sha256(state["body"]).hexdigest()


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_cache_resolved_real_inputs_to_complete_stored_point(
        engine_binary, inputs, tmp_path, monkeypatch, phenomenon):
    from datetime import date
    from firesky.models import ForecastRequest, GeoPoint
    from firesky.national_point_core import materialize_national_point_core
    from test_rust_frames import compare, named_points, reference
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    frozen_path, request = inputs(phenomenon=phenomenon, points=named_points(True))
    root = tmp_path / "resolved-cache"
    for frame in request["frames"]:
        cached, _ = metadata(root, f"f{frame['hour']:03}.grib2", hour=frame["hour"])
        cached.write_bytes(Path(frame["path"]).read_bytes())
    catalog = result(engine_binary, "resolve", cache_dir=str(root), cycle_utc=CYCLE.isoformat(),
                     forecast_hours=[frame["hour"] for frame in request["frames"]],
                     bbox=BBOX, allow_download=False)
    assert catalog["complete"] is True and not catalog["missing_hours"]
    request["frames"] = [dict(hour=row["hour"], path=row["path"]) for row in catalog["files"]]
    frozen_path.write_text(json.dumps(request))
    _, _, expected = reference(dict(request, include_point_core=True), monkeypatch)
    p = request["points"][0]
    req = ForecastRequest(
        target_date=date.fromisoformat(request["date"]), timezone_name=p["timezone_name"],
        point=GeoPoint(p["lat"], p["lon"], p["elevation_m"]), name=p["name"],
        phenomenon=phenomenon, model_source="gfs", radius_km=request["radius_km"],
        gfs_cycle_override=CYCLE)
    output = tmp_path / "point"
    response = rpc(engine_binary, [dict(id="point", method="forecast_stored_point_to_files",
                   params=dict(path=str(frozen_path), output_dir=str(output)))],
                   env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    compare(json.loads((output / "point_forecast.json").read_bytes()),
            materialize_national_point_core(req, expected["point_core"]))
