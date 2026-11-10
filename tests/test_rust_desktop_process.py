"""Persistent preflight transport lifecycle, framing and bounded admission."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from firesky import rust_desktop_process as transport
from firesky.rust_grid import EngineOperationError
from test_rust_migration import engine_binary
from test_rust_desktop_preflight import context, seed_cams, seed_models, config


@pytest.fixture
def stub(tmp_path, monkeypatch):
    if os.name=="nt":
        pytest.skip("executable Python transport fixture requires a POSIX shebang")
    path = tmp_path/"engine"
    path.write_text(f"#!{sys.executable}\n"+'''
import json, os, sys, time
for line in sys.stdin:
    request=json.loads(line)
    params=request["params"]
    mode=params.get("mode","ok")
    if mode=="exit":
        sys.exit(0)
    if mode=="wait":
        while not os.path.exists(params["gate"]):
            time.sleep(.005)
    if mode=="stderr":
        sys.stderr.write("x"*2097152+"DIAGNOSTIC_TAIL")
        sys.stderr.flush()
    if mode=="oversize":
        sys.stdout.write("x"*2097152+"\\n")
    elif mode=="invalid":
        sys.stdout.write("not-json\\n")
    elif mode=="error":
        sys.stdout.write(json.dumps(dict(id=request["id"],type="error",
            error=dict(code="operation_failed",message="fixture failure")))+"\\n")
    else:
        sys.stdout.write(json.dumps(dict(id="wrong" if mode=="identity" else request["id"],
            type="result",result=dict(pid=os.getpid(),mode=mode)))+"\\n")
    sys.stdout.flush()
''')
    path.chmod(0o755)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN",str(path))
    client = transport.Client()
    yield client
    client.close()


def call(client, **params):
    return client.call("check_desktop_data",params)


def alive(pid):
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False


def test_reuses_child_and_reaps_it_on_close(stub):
    first=call(stub)
    assert call(stub)["pid"]==first["pid"]
    stub.close()
    assert not alive(first["pid"])
    assert call(stub)["pid"]!=first["pid"]


@pytest.mark.parametrize("mode",["invalid","identity","oversize","exit"])
def test_transport_failure_fails_current_call_and_next_call_reconnects(stub, mode):
    old=call(stub)["pid"]
    with pytest.raises(RuntimeError,match="Rust desktop preflight"):
        call(stub,mode=mode)
    assert not alive(old)
    assert call(stub)["pid"]!=old


def test_known_operation_failure_keeps_healthy_transport(stub):
    old=call(stub)["pid"]
    with pytest.raises(EngineOperationError,match="fixture failure"):
        call(stub,mode="error")
    assert call(stub)["pid"]==old


def test_external_child_exit_is_not_silently_retried(stub):
    old=call(stub)["pid"]
    os.kill(old,9)
    stub.process.wait(timeout=5)
    with pytest.raises(RuntimeError,match="Rust desktop preflight"):
        call(stub)
    assert call(stub)["pid"]!=old


def test_drains_large_diagnostics_without_blocking_or_unbounded_retention(stub):
    result=call(stub,mode="stderr")
    stub.close()
    assert not alive(result["pid"])
    assert len(stub.stderr)<=8192
    assert stub.stderr.endswith(b"DIAGNOSTIC_TAIL")


def test_environment_change_restarts_before_next_independent_call(stub, monkeypatch):
    old=call(stub)["pid"]
    monkeypatch.setenv("FIRESKY_ECCODES_LIBRARY","/changed/test/library")
    assert call(stub)["pid"]!=old
    assert not alive(old)


def test_replaced_binary_restarts_before_next_independent_call(stub, monkeypatch):
    old=call(stub)["pid"]
    path=Path(os.environ["FIRESKY_RUST_ENGINE_BIN"])
    replacement=path.with_suffix(".replacement")
    replacement.write_text(path.read_text()+"\n# replacement identity\n")
    replacement.chmod(0o755)
    replacement.replace(path)
    assert call(stub)["pid"]!=old
    assert not alive(old)


def test_request_bound_and_json_failure_leave_connection_usable(stub):
    old=call(stub)["pid"]
    with pytest.raises(ValueError,match="1 MiB"):
        call(stub,text="x"*(1024*1024))
    with pytest.raises(ValueError):
        call(stub,value=float("nan"))
    assert call(stub)["pid"]==old


def test_serializes_concurrent_ids_and_bounds_total_active_requests(stub, tmp_path):
    barrier=threading.Barrier(10)
    gate=tmp_path/"release"
    def request():
        barrier.wait(timeout=5)
        return call(stub,mode="wait",gate=str(gate))
    with ThreadPoolExecutor(max_workers=9) as pool:
        futures=[pool.submit(request) for _ in range(9)]
        barrier.wait(timeout=5)
        try:
            completed,_=wait(futures,timeout=5,return_when=FIRST_COMPLETED)
            assert len(completed)==1
            with pytest.raises(RuntimeError,match="too many active"):
                next(iter(completed)).result()
        finally:
            gate.touch()
        values=[future.result(timeout=10) for future in futures if future not in completed]
    assert len(values)==8 and len({value["pid"] for value in values})==1


def test_global_client_closes_child_when_python_parent_exits(stub):
    code = ("import json; from firesky.rust_desktop_process import call_engine;"
            "print(json.dumps(call_engine('check_desktop_data', {})), flush=True)")
    result=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,check=True,
        timeout=15,env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/"src")))
    assert not alive(json.loads(result.stdout)["pid"])


@pytest.mark.skipif(not hasattr(os,"fork"),reason="POSIX fork lifecycle")
def test_fork_does_not_share_native_connection(stub):
    transport.close()
    old=transport.call_engine("check_desktop_data",{})["pid"]
    read_fd,write_fd=os.pipe()
    pid=os.fork()
    if pid==0:
        try:
            os.close(read_fd)
            value=transport.call_engine("check_desktop_data",{})
            transport.close()
            os.write(write_fd,json.dumps(value).encode())
            os.close(write_fd)
            os._exit(0)
        except BaseException:
            os._exit(1)
    os.close(write_fd)
    try:
        with os.fdopen(read_fd) as stream:
            value=json.load(stream)
        _,status=os.waitpid(pid,0)
        assert os.waitstatus_to_exitcode(status)==0
        assert not alive(old) and not alive(value["pid"])
        assert transport.call_engine("check_desktop_data",{})["pid"]!=value["pid"]
    finally:
        try:
            os.waitpid(pid,0)
        except ChildProcessError:
            pass
        transport.close()


def test_real_bridge_reuses_process_but_rescans_changed_cams_files(
        engine_binary, monkeypatch, config):
    from firesky import desktop_data as data
    transport.close()
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN",str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND","rust")
    monkeypatch.setenv("FIRESKY_GFS_DATA_BACKEND","python")
    seed_models(config)
    paths=seed_cams(config,real=True)
    monkeypatch.setattr(data,"model_context",lambda *a, **kw: context("gfs"))
    try:
        assert data.check_model_data(dict(model_source="gfs"),config=config,probe=False)["ready"]
        pid=transport._client.process.pid
        assert data.check_model_data(dict(model_source="gfs"),config=config,probe=False)["ready"]
        assert transport._client.process.pid==pid
        paths[0].write_bytes(b"broken")
        assert not data.check_model_data(dict(model_source="gfs"),config=config,probe=False)["ready"]
        assert transport._client.process.pid==pid
    finally:
        transport.close()
