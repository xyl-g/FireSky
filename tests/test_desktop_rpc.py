import io
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from firesky import desktop_data as data
from firesky import desktop_rpc as rpc
from firesky import web_server as engine


def report(ready=True):
    return {
        "ready": ready,
        "model_cycles": {"gfs": "2026-11-08T00:00:00+00:00",
                         "ec": "2026-11-08T00:00:00+00:00"},
        "datasets": [
            {"source": source, "ready": ready, "missing_count": 0 if ready else 2,
             "request_cycle": "2026-11-08 00Z", "verified": False, "is_latest": False}
            for source in ("gfs", "ecmwf", "cams")
        ],
    }


@pytest.fixture
def service(monkeypatch):
    service = rpc.DesktopService()
    monkeypatch.setattr(service, "_check", lambda *a, **kw: report())
    return service


def check(service, kind="point", **payload):
    return service.dispatch("check", {"kind": kind, "payload": payload})["session"]


def test_legacy_module_is_same_headless_module():
    from desktop import data_access
    assert data_access is data


def test_normalized_snapshot_and_confirmation(service, monkeypatch):
    original = {"name": "before", "model_source": "gfs", "spacing_deg": 2}
    token = check(service, "regional", **original)
    session = service.sessions[token]
    assert session.payload["model_source"] == "both"
    assert session.payload["spacing_deg"] == .25
    assert session.payload["_ec_cycle_override"] == "2026-11-08T00:00:00+00:00"
    original["name"] = "after"
    assert session.payload["name"] == "before"
    calls = []
    monkeypatch.setattr(service, "_forecast", lambda kind, payload, progress: calls.append(payload) or {"ok": True})
    with pytest.raises(rpc.RequestError, match="先确认"):
        service.dispatch("forecast", {"session": token})
    assert not calls
    assert service.dispatch("forecast", {"session": token, "confirmed": True}) == {"ok": True}
    assert len(calls) == 1
    with pytest.raises(rpc.RequestError, match="过期"):
        service.dispatch("forecast", {"session": token, "confirmed": True})


def test_missing_and_strict_validation_never_predict(service, monkeypatch):
    token = check(service)
    monkeypatch.setattr(service, "_check", lambda *a, **kw: report(False))
    monkeypatch.setattr(service, "_forecast", lambda *a: pytest.fail("must not predict"))
    with pytest.raises(rpc.RequestError, match="校验"):
        service.dispatch("forecast", {"session": token, "confirmed": True})
    with pytest.raises(rpc.RequestError, match="先下载"):
        service.dispatch("forecast", {"session": token, "confirmed": True})


def test_prepare_strictly_rechecks_without_forecast(service, monkeypatch):
    token = check(service)
    calls = []
    monkeypatch.setattr(data, "prepare_model_data", lambda payload, **kw: calls.append(("prepare", payload.copy())))
    monkeypatch.setattr(service, "_check", lambda p, k, **kw: calls.append(("check", kw)) or report())
    monkeypatch.setattr(service, "_forecast", lambda *a: pytest.fail("no automatic forecast"))
    response = service.dispatch("prepare", {"session": token})
    assert response["report"]["ready"]
    assert calls[0][0] == "prepare"
    assert calls[1] == ("check", {"fast": False})
    assert not service.sessions[token].consumed


def test_failed_prepare_invalidates_previous_readiness(service, monkeypatch):
    token = check(service)
    def fail(*a, **kw):
        raise RuntimeError("partial download")
    monkeypatch.setattr(data, "prepare_model_data", fail)
    with pytest.raises(RuntimeError, match="partial"):
        service.dispatch("prepare", {"session": token})
    assert not service.sessions[token].report["ready"]
    assert not service.compute_lock.locked()


def test_background_publication_cannot_block_confirm_or_overwrite(service, monkeypatch):
    token = check(service)
    started, release = threading.Event(), threading.Event()
    def publication(_sources):
        started.set()
        assert release.wait(5)
        return {}
    monkeypatch.setattr(data, "published_cycles", publication)
    monkeypatch.setattr(service, "_forecast", lambda *a: {"ok": True})
    with ThreadPoolExecutor() as executor:
        future = executor.submit(service.dispatch, "publications", {"session": token})
        try:
            assert started.wait(2)
            assert service.dispatch("forecast", {"session": token, "confirmed": True}) == {"ok": True}
        finally:
            release.set()
        future.result()
    assert service.sessions[token].consumed


@pytest.mark.parametrize("action", ["dismiss", "expire"])
def test_stale_tokens_cannot_start(service, action):
    token = check(service)
    if action == "dismiss":
        service.dispatch("dismiss", {"session": token})
    else:
        service.sessions[token].created -= rpc.SESSION_SECONDS + 1
    with pytest.raises(rpc.RequestError, match="过期"):
        service.dispatch("forecast", {"session": token, "confirmed": True})


def test_pair_independent_results_and_partial_failure(service, monkeypatch):
    calls = []
    def run(payload, config, progress_callback):
        calls.append(payload["model_source"])
        if payload["model_source"] == "ec":
            raise ValueError("EC unavailable")
        return {"quality_index": 1.25}
    monkeypatch.setattr(engine, "run_forecast_payload", run)
    result = service._forecast("point", {}, lambda e: None)
    assert calls == ["gfs", "ec"]
    assert result == {"model_results": {"gfs": {"quality_index": 1.25}},
                      "model_errors": {"ec": "EC unavailable"}}


def test_pair_preserves_cancellation(service, monkeypatch):
    def run(*a, **kw):
        raise engine.ForecastCancelled("cancel")
    monkeypatch.setattr(engine, "run_forecast_payload", run)
    with pytest.raises(engine.ForecastCancelled):
        service._forecast("point", {}, lambda e: None)

def test_nowcast_prepares_frames_and_requires_separate_confirmation(service, monkeypatch):
    available = False
    calls = []

    def nowcast_check(payload, kind, **kwargs):
        assert kind == "nowcast"
        return {
            "ready": available, "satellite_frames": 2 if available else 0,
            "datasets": [{"source": source, "ready": available} for source in ("gfs", "cams")],
        }

    def prepare(payload, **kwargs):
        nonlocal available
        calls.append(("prepare", payload["lat"], payload["lon"]))
        available = True

    monkeypatch.setattr(service, "_check", nowcast_check)
    monkeypatch.setattr(data, "prepare_nowcast_data", prepare)
    monkeypatch.setattr(data, "prepare_model_data", lambda *a, **kw: pytest.fail("wrong preparation"))
    monkeypatch.setattr(service, "_forecast", lambda kind, payload, progress: calls.append(("forecast", kind)) or {"ok": True})
    response = service.dispatch("check", {"kind": "nowcast", "payload": {"lat": 30.25, "lon": 120.25}})
    token = response["session"]
    assert [row["source"] for row in response["report"]["datasets"]] == ["gfs", "cams", "hsd"]
    with pytest.raises(rpc.RequestError, match="先下载"):
        service.dispatch("forecast", {"session": token, "confirmed": True})
    prepared = service.dispatch("prepare", {"session": token})
    assert prepared["report"]["ready"] and prepared["report"]["datasets"][-1]["ready"]
    assert calls == [("prepare", 30.25, 120.25)]
    assert not service.sessions[token].consumed
    assert service.dispatch("forecast", {"session": token, "confirmed": True}) == {"ok": True}
    assert calls[-1] == ("forecast", "nowcast")


def test_nowcast_returns_one_satellite_result_not_a_model_pair(service, monkeypatch):
    calls = []

    def run(payload, config, progress_callback):
        calls.append(payload)
        return {"peak_quality_index": 1.25, "nowcast": {"source": "test-satellite"}}

    monkeypatch.setattr(engine, "run_forecast_payload", run)
    result = service._forecast("nowcast", {"model_source": "both", "satellite_source": "off"}, lambda e: None)
    assert len(calls) == 1
    assert calls[0]["model_source"] == "satellite"
    assert calls[0]["forecast_mode"] == "nowcast" and calls[0]["satellite_source"] == "auto"
    assert result["nowcast"]["source"] == "test-satellite"
    assert "model_results" not in result


def test_json_normalizes_nonfinite_and_numpy():
    import numpy as np
    value = rpc.json_value({"array": np.array([1.0, float("nan")]),
                            "time": datetime(2026, 11, 8, tzinfo=timezone.utc)})
    assert value["array"] == [1, None]
    json.dumps(value, allow_nan=False)


def test_wire_cancel_and_progress_redaction():
    started = threading.Event()
    class Service:
        def dispatch(self, method, params, progress):
            if method == "long":
                started.set()
                assert cancelled.wait(3)
                progress({"message": "/secret/token CAMS download", "percent": 25})
            return {}
    cancelled = threading.Event()
    class Input:
        def __init__(self):
            self.index = 0
        def readline(self, _limit):
            self.index += 1
            if self.index == 1:
                return json.dumps({"id": "a", "method": "long"}) + "\n"
            if self.index == 2:
                assert started.wait(3)
                return json.dumps({"id": "c", "method": "cancel", "params": {"request_id": "a"}}) + "\n"
            cancelled.set()
            return ""
    output = io.StringIO()
    rpc.serve(Input(), output, Service())
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert next(m for m in messages if m["id"] == "c")["result"]["requested"]
    assert next(m for m in messages if m["id"] == "a")["error"]["code"] == "cancelled"
    assert "/secret" not in output.getvalue()


def test_real_child_handshake_is_json_only_and_qt_free():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    child = subprocess.Popen(
        [sys.executable, "-u", "-m", "firesky.desktop_rpc"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=root, env=env,
    )
    try:
        child.stdin.write('{"id":"hello","method":"hello"}\n')
        child.stdin.flush()
        reply = json.loads(child.stdout.readline())
        assert reply["type"] == "result"
        assert reply["result"]["protocol"] == 1
        from firesky.web_server import REGIONAL_FORECAST_AREAS
        areas = {item["code"]: item for item in reply["result"]["regions"]}
        assert set(areas) == set(REGIONAL_FORECAST_AREAS)
        assert areas["east-asia"]["bounds"] == list(REGIONAL_FORECAST_AREAS["east-asia"]["bounds"])
        child.stdin.close()
        child.wait(timeout=10)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_native_sources_do_not_import_qt():
    import ast
    for module in (rpc, data):
        tree = ast.parse(Path(module.__file__).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name and name.startswith(("PySide", "PyQt", "desktop.")) for name in imports)
