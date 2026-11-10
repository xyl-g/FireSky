import json
import os
from pathlib import Path
import subprocess

import pytest

from firesky import desktop_rpc


ROOT = Path(__file__).resolve().parents[1]


def engine_binary():
    path = Path(
        os.environ.get(
            "FIRESKY_RUST_ENGINE_BIN",
            ROOT
            / "engine"
            / "target"
            / "debug"
            / ("firesky-engine.exe" if os.name == "nt" else "firesky-engine"),
        )
    )
    if not path.is_file():
        pytest.skip("build the Rust engine before running native session tests")
    return path


def rpc(binary, requests):
    result = subprocess.run(
        [str(binary), "serve"],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_native_session_contract_owns_single_use_and_owner_isolation():
    binary = engine_binary()
    child = subprocess.Popen(
        [str(binary), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        child.stdin.write(
            json.dumps(
                {
                    "id": "create",
                    "method": "desktop_session",
                    "params": {
                        "action": "create",
                        "owner": "pytest-native-session",
                        "kind": "point",
                        "payload": {"model_source": "both"},
                        "report": {"ready": True},
                    },
                }
            )
            + "\n"
        )
        child.stdin.flush()
        created = json.loads(child.stdout.readline())
        assert created["type"] == "result"
        token = created["result"]["session"]
        requests = [
            {
                "id": "other",
                "method": "desktop_session",
                "params": {
                    "action": "get",
                    "owner": "different-owner",
                    "session": token,
                },
            },
            {
                "id": "consume",
                "method": "desktop_session",
                "params": {
                    "action": "consume",
                    "owner": "pytest-native-session",
                    "session": token,
                },
            },
            {
                "id": "stale",
                "method": "desktop_session",
                "params": {
                    "action": "get",
                    "owner": "pytest-native-session",
                    "session": token,
                },
            },
        ]
        messages = []
        for request in requests:
            child.stdin.write(json.dumps(request) + "\n")
            child.stdin.flush()
            messages.append(json.loads(child.stdout.readline()))
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    assert messages[0]["type"] == "error"
    assert messages[1]["type"] == "result"
    assert messages[2]["type"] == "error"
    child = subprocess.Popen(
        [str(binary), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        child.stdin.write(
            json.dumps(
                {
                    "id": "restarted",
                    "method": "desktop_session",
                    "params": {
                        "action": "validate",
                        "owner": "pytest-native-session",
                        "session": token,
                    },
                }
            )
            + "\n"
        )
        child.stdin.flush()
        assert json.loads(child.stdout.readline())["type"] == "error"
    finally:
        child.stdin.close()
        child.wait(timeout=10)


def test_production_service_uses_native_session_authority(monkeypatch):
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    service = desktop_rpc.DesktopService()
    assert service.dispatch("dismiss", {"session": "unknown"}) == {"dismissed": True}
    report = {
        "ready": True,
        "model_cycles": {
            "gfs": "2026-11-08T00:00:00+00:00",
            "ec": "2026-11-08T00:00:00+00:00",
        },
        "datasets": [
            {"source": source, "ready": True, "verified": False}
            for source in ("gfs", "ecmwf", "cams")
        ],
    }
    monkeypatch.setattr(service, "_check", lambda *args, **kwargs: report.copy())
    monkeypatch.setattr(
        service,
        "_forecast",
        lambda kind, payload, progress: {"kind": kind, "model_source": payload["model_source"]},
    )
    token = service.dispatch(
        "check",
        {"kind": "point", "payload": {"lat": 30.25, "lon": 120.25}},
    )["session"]
    assert len(token) == 64
    assert service.dispatch("forecast", {"session": token, "confirmed": True}) == {
        "kind": "point",
        "model_source": "both",
    }
    with pytest.raises(desktop_rpc.RequestError, match="过期"):
        service.dispatch("forecast", {"session": token, "confirmed": True})
