"""Actual desktop wire payloads, bounded native workers and client routing."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

import pytest

from firesky import desktop_rpc as reference_rpc
from firesky import location as reference
from test_rust_location import engine_binary, provider, timezone_source, srtm


class Wire:
    def __init__(self, binary, tmp_path, config, **settings):
        path = tmp_path/"location-ipc.json"
        path.write_text(json.dumps(dict(location=config, **settings)))
        self.process = subprocess.Popen([str(binary), "desktop-location", "--config", str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python",
                     FIRESKY_ECCODES_LIBRARY="/no/ecCodes/library"))
        self.output, self.saved = queue.Queue(), []
        def read():
            try:
                for line in self.process.stdout:
                    self.output.put(json.loads(line))
            finally:
                self.output.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def write(self, id, method, params=None):
        self.raw(json.dumps(dict(id=id, method=method, params=params or {}))+"\n")

    def raw(self, line):
        self.process.stdin.write(line)
        self.process.stdin.flush()

    def read(self, id, timeout=8):
        deadline = time.monotonic()+timeout
        while True:
            for i,value in enumerate(self.saved):
                if value["id"] == id:
                    return self.saved.pop(i)
            try:
                value = self.output.get(timeout=max(.001,deadline-time.monotonic()))
            except queue.Empty:
                pytest.fail(f"no reply for {id}, saved={self.saved}")
            assert value is not None, f"native location process exited before {id}"
            self.saved.append(value)

    def call(self, id, method, params=None):
        self.write(id, method, params)
        return self.read(id)

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        self.reader.join(timeout=2)
        stderr = self.process.stderr.read()
        self.process.stdout.close()
        self.process.stderr.close()
        assert self.process.returncode == 0, stderr


@contextmanager
def wire(binary, tmp_path, config, **settings):
    client = Wire(binary, tmp_path, config, **settings)
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize("method", ["search","metadata"])
@pytest.mark.parametrize("fallback", [False,True])
def test_original_desktop_payload_and_composition(engine_binary, provider, tmp_path, method, fallback):
    config, state = provider
    if fallback: state.status["/nom"] = 500
    params = dict(query=" City ") if method=="search" else dict(lat=51.23445,lon=-.12345)
    expected = reference_rpc.DesktopService().dispatch(method, params)
    state.calls.clear()
    with wire(engine_binary, tmp_path, config) as client:
        hello = client.call("hello","hello")["result"]
        assert hello["protocol"] == 1 and hello["engine"] == "firesky-rust-location"
        assert not hello["runtime_python_used"] and not hello["production_desktop_complete"]
        actual = client.call("one", method, params)
        assert actual == dict(id="one",type="result",result=expected)
        assert actual["result"]["elevation_m"] == 80
        assert state.calls["/metadata"] == 1
        if method=="metadata":
            assert actual["result"]["lat"] == params["lat"]
            assert state.queries[-1][1]["latitude"] == [str(round(params["lat"],4))]


@pytest.mark.parametrize(
    "method,params",
    [
        ("search", {"query": " City "}),
        ("metadata", {"lat": 51.23445, "lon": -0.12345}),
    ],
)
def test_production_desktop_service_location_uses_rust_bridge(
    engine_binary, provider, monkeypatch, method, params
):
    from firesky import desktop_rpc
    from firesky.rust_desktop_process import close

    expected = desktop_rpc.DesktopService().dispatch(method, params)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(
        reference,
        "search_location_fast",
        lambda *_args, **_kwargs: pytest.fail("production location used Python search"),
    )
    monkeypatch.setattr(
        reference,
        "fetch_point_metadata",
        lambda *_args, **_kwargs: pytest.fail("production location used Python metadata"),
    )
    try:
        actual = desktop_rpc.DesktopService().dispatch(method, params)
    finally:
        close()
    assert actual == expected


def test_offline_metadata_has_original_coordinates_and_reference_raster(engine_binary, provider, tmp_path):
    config, state = provider
    path = Path(config["srtm_cache_dir"])/"N51W001.hgt"
    srtm(path)
    with wire(engine_binary,tmp_path,config,allow_download=False) as client:
        actual = client.call("one","metadata",dict(lat=51.23445,lon=-.12345))["result"]
        assert actual == dict(lat=51.23445,lon=-.12345,timezone_name="Europe/London",
            elevation_m=reference.read_srtm_elevation(path,round(51.23445,4),round(-.12345,4)))
        assert not state.calls


def test_pool_capacity_controls_stay_responsive_and_cancelled_queue_does_no_io(engine_binary, provider, tmp_path):
    config, state = provider
    started, release = threading.Event(), threading.Event()
    state.started["/metadata"], state.gates["/metadata"] = started, release
    try:
        with wire(engine_binary,tmp_path,config,workers=1,max_active_requests=2) as client:
            client.write("a","metadata",dict(lat=51,lon=-1))
            assert started.wait(3)
            client.write("b","metadata",dict(lat=52,lon=-1))
            assert client.call("c","metadata",dict(lat=53,lon=-1))["error"]["code"] == "busy"
            assert client.call("h","hello")["type"] == "result"
            assert client.call("cancel","cancel",dict(request_id="b"))["result"] == {"requested":True}
            assert client.call("unknown","cancel",dict(request_id="absent"))["result"] == {"requested":False}
            release.set()
            assert client.read("a")["type"] == "result"
            assert client.read("b")["error"]["code"] == "cancelled"
            assert state.calls["/metadata"] == 1
            assert client.call("b","metadata",dict(lat=54,lon=-1))["type"] == "result"
    finally:
        release.set()


def test_multiple_workers_complete_out_of_order_without_blocking_input(engine_binary, provider, tmp_path):
    config, state = provider
    started, release = threading.Event(), threading.Event()
    state.started["/nom"], state.gates["/nom"] = started, release
    try:
        with wire(engine_binary,tmp_path,config,workers=2) as client:
            client.write("search","search",dict(query="City"))
            assert started.wait(3)
            assert client.call("metadata","metadata",dict(lat=51,lon=-1))["type"] == "result"
            assert client.call("cancel","cancel",dict(request_id="search"))["result"]["requested"]
            release.set()
            assert client.read("search")["error"]["code"] == "cancelled"
            assert state.calls["/metadata"] == 1
    finally:
        release.set()


def test_default_pool_bounds_sixteen_active_requests_to_four_workers(engine_binary,provider,tmp_path):
    config,state = provider
    release = threading.Event()
    state.gates["/metadata"] = release
    try:
        with wire(engine_binary,tmp_path,config) as client:
            for i in range(16):
                client.write(str(i),"metadata",dict(lat=20+i*.1,lon=-1))
            assert client.call("overflow","metadata",dict(lat=55,lon=-1))["error"]["code"] == "busy"
            deadline = time.monotonic()+3
            while state.calls["/metadata"]<4 and time.monotonic()<deadline:
                time.sleep(.01)
            assert state.calls["/metadata"] == 4
            for i in range(16):
                assert client.call(f"cancel-{i}","cancel",dict(request_id=str(i)))["result"]["requested"]
            release.set()
            for i in range(16):
                assert client.read(str(i))["error"]["code"] == "cancelled"
            assert state.calls["/metadata"] == 4
    finally:
        release.set()


def test_retry_backoff_is_cooperatively_cancelled(engine_binary, provider, tmp_path):
    config, state = provider
    config["retry_seconds"] = 10
    state.status["/nom"] = state.status["/geo"] = 500
    state.started["/geo"] = threading.Event()
    with wire(engine_binary,tmp_path,config) as client:
        client.write("search","search",dict(query="City"))
        assert state.started["/geo"].wait(3)
        assert client.call("cancel","cancel",dict(request_id="search"))["result"]["requested"]
        assert client.read("search",timeout=2)["error"]["code"] == "cancelled"
        assert state.calls["/geo"] == 1


def test_waiting_srtm_lock_is_cooperatively_cancelled(engine_binary, provider, tmp_path):
    fcntl = pytest.importorskip("fcntl")
    config, state = provider
    state.status["/metadata"] = 500
    state.started["/metadata"] = threading.Event()
    config["lock_timeout_seconds"] = 20
    root = Path(config["srtm_cache_dir"])
    root.mkdir()
    with (root/"N51W001.hgt.lock").open("wb") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with wire(engine_binary,tmp_path,config) as client:
            client.write("metadata","metadata",dict(lat=51.5,lon=-.1))
            assert state.started["/metadata"].wait(3)
            assert client.call("cancel","cancel",dict(request_id="metadata"))["result"]["requested"]
            assert client.read("metadata",timeout=2)["error"]["code"] == "cancelled"
            assert not list(root.glob("*.hgt"))
            assert not any(p.startswith("/srtm/") for p in state.calls)


def test_cancelled_srtm_transfer_never_publishes(engine_binary, provider, tmp_path):
    import gzip
    config, state = provider
    state.status["/metadata"] = 500
    original = tmp_path/"source.hgt"
    srtm(original)
    endpoint = "/srtm/N51/N51W001.hgt.gz"
    state.raw[endpoint] = gzip.compress(original.read_bytes())
    started, release = threading.Event(), threading.Event()
    state.started[endpoint], state.gates[endpoint] = started, release
    try:
        with wire(engine_binary,tmp_path,config) as client:
            client.write("a","metadata",dict(lat=51.5,lon=-.1))
            assert started.wait(3)
            assert client.call("cancel","cancel",dict(request_id="a"))["result"]["requested"]
            release.set()
            assert client.read("a")["error"]["code"] == "cancelled"
            root = Path(config["srtm_cache_dir"])
            assert not list(root.glob("*.hgt"))
            assert not list(root.glob(".firesky-srtm-*"))
    finally:
        release.set()


def test_eof_cancels_and_drains_workers(engine_binary, provider, tmp_path):
    config, state = provider
    started, release = threading.Event(), threading.Event()
    state.started["/metadata"], state.gates["/metadata"] = started, release
    client = Wire(engine_binary,tmp_path,config)
    try:
        client.write("a","metadata",dict(lat=51,lon=-1))
        assert started.wait(3)
        assert client.call("cancel","cancel",dict(request_id="a"))["result"]["requested"]
        client.process.stdin.close()
        release.set()
        assert client.read("a")["error"]["code"] == "cancelled"
    finally:
        release.set()
        client.close()


def test_eof_marks_running_and_queued_work_cancelled(engine_binary,provider,tmp_path):
    config,state = provider
    started,release = threading.Event(),threading.Event()
    state.started["/metadata"],state.gates["/metadata"] = started,release
    client = Wire(engine_binary,tmp_path,config,workers=1)
    try:
        client.write("a","metadata",dict(lat=51,lon=-1))
        assert started.wait(3)
        client.write("b","metadata",dict(lat=52,lon=-1))
        assert client.call("barrier","hello")["type"]=="result"
        client.process.stdin.close()
        time.sleep(.1)
        release.set()
        assert client.read("a")["error"]["code"]=="cancelled"
        assert client.read("b")["error"]["code"]=="cancelled"
        assert state.calls["/metadata"]==1
    finally:
        release.set()
        client.close()


@pytest.mark.parametrize("raw", [
    "not JSON", "[]", "null", '{"id":0,"method":"hello"}',
    '{"id":"","method":"hello"}', '{"id":"x","method":"hello","params":[]}',
    '{"id":"x","method":false}', '{"id":"x","method":"hello","extra":1}',
    json.dumps(dict(id="x"*129,method="hello")),
    "x"*(1024*1024+50),
])
def test_invalid_frames_are_bounded_drained_and_keep_process_alive(engine_binary, provider, tmp_path, raw):
    config,_ = provider
    with wire(engine_binary,tmp_path,config) as client:
        client.raw(raw+"\n")
        reply = client.output.get(timeout=3)
        assert reply["error"]["code"] == "invalid_request"
        assert client.call("alive","hello")["type"] == "result"


def test_active_duplicate_ids_are_rejected_and_unsupported_work_is_not_advertised(engine_binary, provider, tmp_path):
    config,state = provider
    started, release = threading.Event(),threading.Event()
    state.started["/metadata"],state.gates["/metadata"] = started,release
    try:
        with wire(engine_binary,tmp_path,config) as client:
            client.write("a","metadata",dict(lat=51,lon=-1))
            assert started.wait(3)
            assert client.call("a","metadata",dict(lat=52,lon=-1))["error"]["code"] == "invalid_request"
            assert client.call("forecast","forecast",{})["error"]["code"] == "unsupported_method"
            release.set()
            assert client.read("a")["type"] == "result"
    finally:
        release.set()


@pytest.mark.parametrize("method,params", [
    ("search",{}), ("search",dict(query=" ")), ("search",dict(query="x"*201)),
    ("search",dict(query=3)), ("metadata",{}), ("metadata",dict(lat=91,lon=0)),
    ("metadata",dict(lat=90.000001,lon=0)), ("metadata",dict(lat=0,lon="nan")),
])
def test_invalid_desktop_params_do_not_request_providers(engine_binary,provider,tmp_path,method,params):
    config,state = provider
    with wire(engine_binary,tmp_path,config) as client:
        assert client.call("bad",method,params)["type"] == "error"
        assert client.call("alive","hello")["type"] == "result"
        assert not state.calls


def test_unicode_query_limit_and_memo_reuse(engine_binary,provider,tmp_path):
    config,state = provider
    with wire(engine_binary,tmp_path,config) as client:
        first = client.call("a","search",dict(query="\U0001f30d"*200))
        assert first["type"] == "result"
        assert client.call("b","search",dict(query="\U0001f30d"*200))["result"] == first["result"]
        assert state.calls["/nom"] == state.calls["/metadata"] == 1
        assert client.call("bad","search",dict(query="\U0001f30d"*201))["type"] == "error"


@pytest.mark.parametrize("settings", [
    dict(workers=0),dict(workers=9),dict(max_active_requests=3),
    dict(max_active_requests=65),dict(workers=1,max_active_requests=0),
    dict(unknown=True),dict(location=dict(request_attempts=0)),
])
def test_invalid_startup_settings_fail_before_serving(engine_binary,tmp_path,settings):
    path = tmp_path/"settings.json"
    path.write_text(json.dumps(settings))
    process = subprocess.run([str(engine_binary),"desktop-location","--config",str(path)],
        input='{"id":"h","method":"hello"}\n',capture_output=True,text=True,timeout=3)
    assert process.returncode==1 and not process.stdout
    assert process.stderr.startswith("firesky-engine:")


@pytest.mark.parametrize("fault", ["oversized","malformed","symlink"])
def test_bounded_startup_config_does_not_follow_links(engine_binary,tmp_path,fault):
    path = tmp_path/"settings.json"
    data = b"x"*65537 if fault=="oversized" else b"unowned malformed data"
    target = tmp_path/"foreign"
    target.write_bytes(data)
    if fault=="symlink": path.symlink_to(target)
    else: path.write_bytes(data)
    process = subprocess.run([str(engine_binary),"desktop-location","--config",str(path)],
        input="",capture_output=True,text=True,timeout=3)
    assert process.returncode==1 and not process.stdout
    assert target.read_bytes()==data


@pytest.fixture(scope="session")
def swift_location_probe(tmp_path_factory):
    compiler = shutil.which("swiftc")
    if sys.platform != "darwin" or not compiler:
        pytest.skip("macOS Swift compiler required for real native client probe")
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path_factory.mktemp("swift-location")/"probe"
    subprocess.run([compiler,"-swift-version","5","-parse-as-library",
        str(root/"apps/macos/Sources/FireSkyNative/EngineClient.swift"),
        str(root/"apps/macos/Sources/FireSkyNative/FFIEngineClient.swift"),
        str(root/"tests/fixtures/rust_location_client.swift"),"-o",str(binary)],
        check=True,capture_output=True,text=True,timeout=120)
    return binary


@pytest.mark.parametrize("mode", ["success","failure","restart","native-default","oversized"])
def test_real_swift_client_routes_location_without_python_fallback(
    engine_binary,swift_location_probe,provider,tmp_path,mode
):
    config,state = provider
    if mode=="failure":
        state.status["/nom"] = state.status["/geo"] = 500
    settings = tmp_path/"settings.json"
    settings.write_text(json.dumps(dict(location=config)))
    root = Path(__file__).resolve().parents[1]
    stub = tmp_path/"legacy-engine"
    stub.write_text(f"#!{sys.executable}\n"+(root/"tests/fixtures/native_location_python_stub.py").read_text())
    stub.chmod(0o755)
    record = tmp_path/"python-calls.ndjson"
    workspace = tmp_path/"workspace"
    workspace.mkdir()
    env = dict(os.environ,FIRESKY_RUST_ENGINE_BIN=str(engine_binary),
        FIRESKY_LOCATION_ENGINE_BIN=str(engine_binary),FIRESKY_PYTHON=str(stub),
        FIRESKY_LOCATION_CONFIG=str(settings),FIRESKY_NATIVE_LOG_DIR=str(tmp_path/"logs"),
        FIRESKY_PROBE_PYTHON_RECORD=str(record))
    if mode=="native-default":
        del env["FIRESKY_LOCATION_ENGINE_BIN"]
        del env["FIRESKY_LOCATION_CONFIG"]
    process = subprocess.run([str(swift_location_probe),str(workspace),str(engine_binary),mode],
        env=env,capture_output=True,text=True,timeout=30)
    assert process.returncode==0, process.stdout+process.stderr
    output = json.loads(process.stdout)
    assert output["passed"]
    assert not record.exists(), "native client started the forbidden Python sidecar"
    assert not (workspace/"pyproject.toml").exists()
    if mode in ("success","restart","oversized"):
        assert output["search"] == dict(name="Town",lat=51.4,lon=-.2,
            timezone_name="Europe/London",elevation_m=80)
        assert output["metadata"]["lat"] == 51.23445


def test_real_swift_cancellation_targets_native_child(
    engine_binary,swift_location_probe,provider,tmp_path
):
    config,state = provider
    started,release = threading.Event(),threading.Event()
    state.started["/metadata"],state.gates["/metadata"] = started,release
    root = Path(__file__).resolve().parents[1]
    stub = tmp_path/"legacy-engine"
    stub.write_text(f"#!{sys.executable}\n"+(root/"tests/fixtures/native_location_python_stub.py").read_text())
    stub.chmod(0o755)
    settings,record,marker = tmp_path/"settings.json",tmp_path/"calls.ndjson",tmp_path/"cancel"
    settings.write_text(json.dumps(dict(location=config)))
    workspace = tmp_path/"workspace"
    workspace.mkdir()
    process = subprocess.Popen([str(swift_location_probe),str(workspace),str(engine_binary),"cancel"],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        env=dict(os.environ,FIRESKY_RUST_ENGINE_BIN=str(engine_binary),
            FIRESKY_LOCATION_ENGINE_BIN=str(engine_binary),FIRESKY_PYTHON=str(stub),
            FIRESKY_LOCATION_CONFIG=str(settings),FIRESKY_NATIVE_LOG_DIR=str(tmp_path/"logs"),
            FIRESKY_PROBE_PYTHON_RECORD=str(record),FIRESKY_PROBE_CANCEL_MARKER=str(marker)))
    try:
        assert started.wait(3)
        marker.touch()
        assert json.loads(process.stdout.readline()) == {"cancel_sent":True}
        time.sleep(.1)
        release.set()
        stdout,stderr = process.communicate(timeout=10)
        assert process.returncode==0,stdout+stderr
        assert json.loads(stdout)["passed"]
        assert not record.exists(), "native cancellation reached the forbidden Python sidecar"
    finally:
        release.set()
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("fault", ["configuration","protocol","executable"])
def test_real_swift_hello_rejects_invalid_native_setup(
    engine_binary,swift_location_probe,tmp_path,fault
):
    root = Path(__file__).resolve().parents[1]
    stub = tmp_path/"legacy-engine"
    stub.write_text(f"#!{sys.executable}\n"+(root/"tests/fixtures/native_location_python_stub.py").read_text())
    stub.chmod(0o755)
    path,record = tmp_path/"settings.json",tmp_path/"calls.ndjson"
    path.write_text(json.dumps(dict(workers=0)))
    workspace = tmp_path/"workspace"
    workspace.mkdir()
    env = dict(os.environ,FIRESKY_RUST_ENGINE_BIN=str(engine_binary),
        FIRESKY_LOCATION_ENGINE_BIN=str(engine_binary),FIRESKY_PYTHON="/forbidden/python",
        FIRESKY_LOCATION_CONFIG=str(path),FIRESKY_NATIVE_LOG_DIR=str(tmp_path/"logs"),
        FIRESKY_PROBE_PYTHON_RECORD=str(record))
    if fault=="protocol":
        env["FIRESKY_LOCATION_ENGINE_BIN"] = str(stub)
    elif fault=="executable":
        env["FIRESKY_LOCATION_ENGINE_BIN"] = str(tmp_path/"missing-engine")
    process = subprocess.run([str(swift_location_probe),str(workspace),str(engine_binary),"success"],
        env=env,capture_output=True,text=True,timeout=15)
    assert process.returncode==1,process.stdout+process.stderr
    assert not json.loads(process.stdout)["passed"]
    calls = record.read_text().splitlines() if record.exists() else []
    # The wrong-protocol stub gets the native hello; legacy work never starts.
    assert len(calls) == (1 if fault=="protocol" else 0)
