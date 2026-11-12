"""Desktop data-sync helper contract over the native desktop JSONL bridge.

Covers `docs/DATA_SYNC_HELPER_DESIGN.zh-CN.md` section 13 at the IPC level:
settings persistence across engine restarts, the deterministic due gates,
the three-failure circuit breaker and its two unlock channels, fail-closed
rounds with no heuristic fallback, and the coexistence with manual prepare
sessions in one task history.

Every engine child runs with the ambient proxy pointed at a closed local
port, so publication probes fail instantly and deterministically (`verified
false`) exactly like an upstream outage — no real network is touched and no
download path is exercised here (that is acceptance territory, section 14).
"""

from contextlib import contextmanager
import json
import os
import select
import subprocess
import time

import pytest

from test_rust_migration import engine_binary

NOW = "2026-11-13T15:00:00Z"
STATE_CONTRACT = "rust-data-sync-state-v1"
SETTINGS_CONTRACT = "rust-data-sync-settings-v1"
DEAD_PROXY = "http://127.0.0.1:9"


def offline_env():
    """Force every publication probe to fail fast instead of reaching upstream."""
    env = dict(os.environ)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                 "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        env[name] = DEAD_PROXY if "NO_PROXY" not in name.upper() else ""
    return env


@contextmanager
def sync_session(binary, workspace, *, timeout=60):
    process = subprocess.Popen(
        [str(binary), "desktop", "--workspace", str(workspace)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=offline_env(),
    )
    pending = bytearray()

    def call(request_id, method, params):
        process.stdin.write((json.dumps({
            "id": request_id, "method": method, "params": params,
        }) + "\n").encode())
        process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                response = json.loads(line)
                assert response["id"] == request_id, response
                if response["type"] != "progress":
                    return response
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait(timeout=10)
                pytest.fail(
                    f"{method} timed out; stderr: "
                    f"{process.stderr.read().decode(errors='replace')[-500:]}"
                )
            ready, _, _ = select.select(
                [process.stdout], [], [], min(remaining, 1))
            if ready:
                pending.extend(os.read(process.stdout.fileno(), 65536))

    try:
        yield call
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def cams_only_settings():
    return {"enabled": True, "sources": {"cams": True},
            "interval_minutes": 60, "anchor_region": "china"}


def all_sources_settings():
    return {"enabled": True, "sources": {"cams": True, "gfs": True, "ec": True},
            "interval_minutes": 60, "anchor_region": "china",
            "lead_horizon_hours": 120, "ecmwf_source": "aws"}


def state_path(workspace):
    return workspace / ".firesky" / "state" / "data-sync.json"


def write_state(workspace, sources):
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"contract": STATE_CONTRACT, "sources": sources}))


def read_state_source(workspace, name):
    return json.loads(state_path(workspace).read_text())["sources"][name]


def check_payload():
    return {"name": "sync-contract", "lat": 40.0, "lon": 116.0, "elevation_m": 50.0,
            "timezone_name": "Asia/Shanghai", "target_date": "2026-11-13",
            "phenomenon": "dusk", "model_source": "both"}


def test_settings_default_to_disabled_and_survive_engine_restart(engine_binary, tmp_path):
    with sync_session(engine_binary, tmp_path) as call:
        hello = call("hello", "hello", {})["result"]
        assert hello["runtime_python_used"] is False
        assert "data_sync" in hello["capabilities"]
        status = call("status", "data_sync_settings", {})["result"]
        assert status["contract"] == SETTINGS_CONTRACT
        assert status["configured"] is False
        assert status["settings"]["enabled"] is False
        assert status["retention_configured"] is False
        assert status["runtime_python_used"] is False
        due = call("due", "data_sync_due", {"now_utc": NOW})["result"]
        assert due["contract"] == "rust-data-sync-due-v1"
        assert due["runtime_python_used"] is False
        assert due["enabled"] is False and due["due_for_any"] is False
        assert due["due_sources"] == []
        for name in ("cams", "gfs", "ec"):
            assert due["sources"][name]["reason"] == "disabled"
        saved = call(
            "save", "save_data_sync_settings", {"settings": cams_only_settings()},
        )["result"]
        assert saved["configured"] is True
        assert saved["settings"]["interval_minutes"] == 60
        assert saved["state"]["contract"] == STATE_CONTRACT
    # A restarted engine reads the same settings from the workspace file: the
    # persisted settings file is the single source of truth (design 4.1), the
    # UI only mirrors it.
    with sync_session(engine_binary, tmp_path) as call:
        status = call("status", "data_sync_settings", {})["result"]
        assert status["configured"] is True
        assert status["settings"]["enabled"] is True
        assert status["settings"]["sources"]["cams"] is True
        assert status["settings"]["anchor_region"] == "china"
        assert status["settings"]["sources"]["gfs"] is False
        due = call("due", "data_sync_due", {"now_utc": NOW})["result"]
        assert due["enabled"] is True
        assert due["sources"]["cams"]["reason"] == "publication_unverified"
        assert due["sources"]["gfs"]["reason"] == "disabled"
        assert due["sources"]["ec"]["reason"] == "disabled"


def test_enabled_settings_reject_missing_or_invalid_fields_over_the_bridge(
    engine_binary, tmp_path,
):
    invalid = [
        {"enabled": True, "sources": {}, "interval_minutes": 60,
         "anchor_region": "china"},
        {"enabled": True, "sources": {"cams": True}, "anchor_region": "china"},
        {"enabled": True, "sources": {"cams": True}, "interval_minutes": 60},
        {"enabled": True, "sources": {"gfs": True}, "interval_minutes": 60,
         "anchor_region": "china"},
        {"enabled": True, "sources": {"ec": True}, "interval_minutes": 60,
         "anchor_region": "china", "lead_horizon_hours": 120},
        {"enabled": True, "sources": {"cams": True}, "interval_minutes": 29,
         "anchor_region": "china"},
        {"enabled": True, "sources": {"cams": True}, "interval_minutes": 1441,
         "anchor_region": "china"},
        {"enabled": True, "sources": {"cams": True}, "interval_minutes": 60,
         "anchor_region": "atlantis"},
        {"enabled": True, "sources": {"gfs": True}, "interval_minutes": 60,
         "anchor_region": "china", "lead_horizon_hours": 121},
        {"enabled": True, "sources": {"ec": True}, "interval_minutes": 60,
         "anchor_region": "china", "lead_horizon_hours": 119,
         "ecmwf_source": "noaa"},
        {"enabled": True, "sources": {"cams": True, "bogus": True},
         "interval_minutes": 60, "anchor_region": "china"},
        {"enabled": True, "sources": {"cams": True}, "interval_minutes": 60,
         "anchor_region": "china", "unknown_field": 1},
    ]
    with sync_session(engine_binary, tmp_path) as call:
        call(
            "save", "save_data_sync_settings", {"settings": cams_only_settings()},
        )
        for index, settings in enumerate(invalid):
            response = call(
                f"bad-{index}", "save_data_sync_settings", {"settings": settings},
            )
            assert response["type"] == "error", settings
        status = call("status", "data_sync_settings", {})["result"]
        assert status["configured"] is True
        assert status["settings"] == {
            "enabled": True, "sources": {"cams": True, "gfs": False, "ec": False},
            "interval_minutes": 60, "anchor_region": "china",
            "lead_horizon_hours": None, "ecmwf_source": None,
        }


def test_due_boundaries_stay_fail_closed_without_a_verified_publication(
    engine_binary, tmp_path,
):
    with sync_session(engine_binary, tmp_path) as call:
        call("save", "save_data_sync_settings", {"settings": cams_only_settings()})
        # Fresh source, probe offline: the heuristic cycle is unverified and
        # never schedules a download (design 5.2 step 3, fail-closed).
        due = call("due-fresh", "data_sync_due", {"now_utc": NOW})["result"]
        row = due["sources"]["cams"]
        assert row["reason"] == "publication_unverified"
        assert row["due"] is False
        assert row["publication"]["verified"] is False
        assert row["publication"]["label"] == "2026-11-13 00Z"
        assert due["due_for_any"] is False and due["due_sources"] == []
        # Interval gate runs before the publication gate: one second inside the
        # configured interval short-circuits, exactly at the boundary it does
        # not (now - attempt < interval is the contract, design 5.2 step 2).
        write_state(tmp_path, {"cams": {
            "last_attempt_utc": "2026-11-13T14:00:01+00:00",
        }})
        due = call("due-inside", "data_sync_due", {"now_utc": NOW})["result"]
        assert due["sources"]["cams"]["reason"] == "interval_not_elapsed"
        assert due["sources"]["cams"]["last_attempt_utc"] == \
            "2026-11-13T14:00:01+00:00"
        write_state(tmp_path, {"cams": {
            "last_attempt_utc": "2026-11-13T14:00:00+00:00",
        }})
        due = call("due-exact", "data_sync_due", {"now_utc": NOW})["result"]
        assert due["sources"]["cams"]["reason"] == "publication_unverified"
        # The breaker gate runs before everything else.
        write_state(tmp_path, {"cams": {
            "suspended": True, "consecutive_failures": 3,
            "last_cycle": "2026-11-12T00:00:00+00:00",
        }})
        due = call("due-suspended", "data_sync_due", {"now_utc": NOW})["result"]
        assert due["sources"]["cams"]["reason"] == "suspended_failures"
        assert due["sources"]["cams"]["suspended"] is True
        assert due["due_for_any"] is False
        # A malformed injected reference time is a contract error, not a guess.
        response = call("due-bad-time", "data_sync_due", {"now_utc": "not-a-time"})
        assert response["type"] == "error"


def test_three_failed_rounds_trip_the_breaker_and_manual_channels_unlock(
    engine_binary, tmp_path,
):
    with sync_session(engine_binary, tmp_path) as call:
        call("save", "save_data_sync_settings", {"settings": cams_only_settings()})
        for index in range(3):
            round_ = call(
                f"round-{index}", "data_sync_run", {"sources": ["cams"]},
            )["result"]
            row = round_["sources"]["cams"]
            assert row["status"] == "failed"
            assert row["publication_verified"] is False
            state = read_state_source(tmp_path, "cams")
            assert state["consecutive_failures"] == index + 1
            assert state["last_cycle"] is None
        assert read_state_source(tmp_path, "cams")["suspended"] is True
        due = call("due", "data_sync_due", {"now_utc": NOW})["result"]
        assert due["sources"]["cams"]["reason"] == "suspended_failures"
        assert due["due_for_any"] is False
        # Unlock channel one (design 8.3/16-1): re-saving settings clears the
        # breaker but keeps last_cycle/last_result bookkeeping intact.
        saved = call(
            "resave", "save_data_sync_settings", {"settings": cams_only_settings()},
        )["result"]
        state = saved["state"]["sources"]["cams"]
        assert state["suspended"] is False
        assert state["consecutive_failures"] == 0
        assert state["last_cycle"] is None
        assert state["last_result"]["status"] == "failed"
        # Unlock channel two (design 16-6): a manual round bypasses the trip
        # and starts a fresh failure budget; the round still fails fail-closed.
        round_ = call("manual", "data_sync_run", {"sources": ["cams"]})["result"]
        assert round_["sources"]["cams"]["status"] == "failed"
        state = read_state_source(tmp_path, "cams")
        assert state["suspended"] is False
        assert state["consecutive_failures"] == 1
        assert state["last_cycle"] is None


def test_failed_round_refuses_fallback_and_preserves_existing_cache(
    engine_binary, tmp_path,
):
    cached = tmp_path / ".cache" / "cams" / "existing-cycle.grib"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"existing accepted cache bytes")
    with sync_session(engine_binary, tmp_path) as call:
        call("save", "save_data_sync_settings",
             {"settings": all_sources_settings()})
        round_ = call(
            "round", "data_sync_run", {"sources": ["cams", "gfs", "ec"]},
        )["result"]
        assert round_["contract"] == "rust-data-sync-v1"
        assert round_["runtime_python_used"] is False
        # Sources fail independently and every one of them refuses to fall
        # back to the heuristic cycle (design 6.1/8.2, fail-closed).
        assert set(round_["sources"]) == {"cams", "gfs", "ec"}
        for name, row in round_["sources"].items():
            assert row["status"] == "failed", (name, row)
            assert row["publication_verified"] is False
            assert "refusing to download" in row["error"]
            assert row["retention"] is None
            assert row["task_id"]
        # A failed round deletes nothing and never advances last_cycle.
        assert cached.read_bytes() == b"existing accepted cache bytes"
        for name in ("cams", "gfs", "ec"):
            assert read_state_source(tmp_path, name)["last_cycle"] is None


def test_sync_rounds_share_history_without_touching_manual_sessions(
    engine_binary, tmp_path,
):
    with sync_session(engine_binary, tmp_path) as call:
        call("save", "save_data_sync_settings", {"settings": cams_only_settings()})
        checked = call(
            "check", "check", {"kind": "point", "payload": check_payload()},
        )
        assert checked["type"] == "result", checked
        session = checked["result"]["session"]
        assert session
        call("round", "data_sync_run", {"sources": ["cams"]})
        history = call("history", "task_history", {"limit": 20})["result"]
        methods = {task["method"]: task for task in history["tasks"]}
        # The sync round lands in the same history under the design's
        # `data_sync` label (design 7.2), distinguishable from manual tasks.
        # Per-source failures do not fail the round itself (design 8.2: every
        # source succeeds or fails independently), so the completed round is
        # `done` while the failure stays in the round result and state file.
        assert "data_sync" in methods
        assert methods["data_sync"]["status"] == "done"
        assert "check" in methods
        # The failed round never created, consumed or invalidated the manual
        # session: confirmation is still the next contract step.
        denied = call("forecast", "forecast", {"session": session})
        assert denied["type"] == "error"
        assert "confirmation" in denied["error"]["message"]
        # The cancelled-path contract stays reachable over the wire too.
        cancelled = call(
            "cancel", "cancel", {"request_id": "no-such-request"},
        )["result"]
        assert cancelled["requested"] is False
