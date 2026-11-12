"""Verify desktop forecast publication and restore using existing inputs only."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class Session:
    def __init__(self, binary, workspace, evidence, label, env, deny_inputs=False):
        if sys.platform != "darwin":
            raise RuntimeError("This verifier requires macOS sandbox-exec to deny network access")
        self.stderr = (evidence / f"{label}.stderr.log").open("w")
        self.events = (evidence / f"{label}.events.jsonl").open("w")
        policy = "(version 1)(allow default)(deny network*)"
        if deny_inputs:
            for name in ("GFS", "ECMWF", "CAMS", "HSD"):
                directory = json.dumps(env[f"FIRESKY_{name}_CACHE_DIR"])
                policy += f"(deny file-read* (subpath {directory}))"
        self.process = subprocess.Popen(
            ["/usr/bin/sandbox-exec", "-p", policy,
             str(binary), "desktop", "--workspace", str(workspace)],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, text=True, bufsize=1,
        )
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.sequence = 0

    def _read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(None)

    def call(self, method, params, timeout=600):
        self.sequence += 1
        rid = f"offline-{self.sequence}"
        self.process.stdin.write(json.dumps({"id": rid, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{method} exceeded {timeout}s")
            try:
                message = self.messages.get(timeout=min(remaining, 1))
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError(f"engine exited during {method}; inspect stderr evidence")
            if isinstance(message, Exception):
                raise message
            if message.get("id") == rid and message.get("type") in ("result", "error"):
                if message["type"] == "error":
                    raise RuntimeError(f"{method}: {message.get('error')}")
                return message["result"]
            self.events.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.events.flush()

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.reader.join(timeout=2)
        self.process.stdout.close()
        self.stderr.close()
        self.events.close()


def profile_checks(session, result, kind):
    reports = result.get("model_results") or {"satellite": result}
    checks = []
    for model, report in reports.items():
        source = report.get("profile_source") or {}
        if not source.get("available"):
            checks.append({"model": model, "status": "unavailable", "source": source})
            continue
        points = report.get("points") or [report]
        point = points[0]
        observer = ((report.get("cross_section") or {}).get("observer")
                    or report.get("observer") or result.get("observer") or {})
        lat = point.get("lat", observer.get("lat"))
        lon = point.get("lon", observer.get("lon"))
        valid = point.get("best_time_local") or report.get("best_time_local")
        if kind == "nowcast":
            valid = next((s["valid_time_local"] for s in report.get("steps", [])
                          if isinstance(s.get("cross_section"), dict)), valid)
        valid = valid or (report.get("cross_section") or {}).get("valid_time_local")
        if lat is None or lon is None or valid is None:
            raise RuntimeError(f"cannot identify {model} frozen profile coordinates/time")
        queried = session.call("profile_query", {
            "result_id": source["result_id"], "source_identity": source["source_identity"],
            "model_source": source["model_source"], "lat": lat, "lon": lon,
            "valid_time_utc": datetime.fromisoformat(valid).astimezone(timezone.utc).isoformat(),
        })
        if queried.get("status") != "ready":
            raise RuntimeError(f"{model} frozen profile not ready: {queried}")
        checks.append(queried)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT / "engine/target/aarch64-apple-darwin/release/firesky-engine")
    parser.add_argument("--cache-root", type=Path, default=ROOT / ".cache")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--kind", choices=("point", "regional", "nowcast"), default="point")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    evidence = workspace / "evidence"
    evidence.mkdir(exist_ok=True)
    payload = json.loads(args.request.read_text())
    payload["allow_download"] = False
    save(evidence / "request.json", payload)
    env = dict(os.environ, FIRESKY_RESOURCE_DIR=str(ROOT / "resources/app-data"))
    for source in ("gfs", "ecmwf", "cams"):
        env[f"FIRESKY_{source.upper()}_CACHE_DIR"] = str(args.cache_root.resolve() / source)
    env["FIRESKY_HSD_CACHE_DIR"] = str(args.cache_root.resolve() / "himawari-hsd")
    session = Session(args.engine.resolve(), workspace, evidence, "publish", env)
    try:
        hello = session.call("hello", {})
        save(evidence / "hello.json", hello)
        checked = session.call("check", {"kind": args.kind, "payload": payload})
        save(evidence / "check.json", checked)
        print(json.dumps({"ready": checked["report"].get("ready"),
                          "datasets": [{key: row.get(key) for key in
                                        ("source", "ready", "missing_hours", "missing_valid_times")}
                                       for row in checked["report"].get("datasets", [])]},
                         ensure_ascii=False), flush=True)
        if args.check_only:
            return
        if checked["report"].get("ready") is not True:
            raise RuntimeError("cached inputs are incomplete; downloads remain disabled")
        result = session.call("forecast", {"session": checked["session"], "confirmed": True,
                                            "cycle_age_confirmed": True})
        save(evidence / "forecast.json", result)
        if result.get("model_errors"):
            raise RuntimeError(f"forecast model errors: {result['model_errors']}")
        history = session.call("task_history", {"limit": 100})
        save(evidence / "history.json", history)
        task = next(t for t in history["tasks"]
                    if t.get("status") == "done" and t.get("method") == "forecast")
        restored = session.call("result_restore", {"task_id": task["task_id"]})
        save(evidence / "restore.json", restored)
        if restored != result:
            raise RuntimeError("restored result differs from published result")
        query_result = dict(result)
        query_result.setdefault("observer", {"lat": payload.get("lat"), "lon": payload.get("lon")})
        profiles = profile_checks(session, query_result, args.kind)
        save(evidence / "profiles.json", profiles)
        reports = result.get("model_results") or {"satellite": result}
        artifacts = []
        for model, report in reports.items():
            if report.get("html_report_path"):
                path = Path(report["html_report_path"])
                content = path.read_bytes()
                artifacts.append({"model": model, "path": str(path), "bytes": len(content),
                                  "sha256": hashlib.sha256(content).hexdigest()})
        save(evidence / "reports.json", artifacts)
        task_id = task["task_id"]
    finally:
        session.close()
    session = Session(args.engine.resolve(), workspace, evidence, "restart", env, deny_inputs=True)
    try:
        after_restart = session.call("result_restore", {"task_id": task_id})
        save(evidence / "restore-after-restart.json", after_restart)
        if after_restart != result:
            raise RuntimeError("restart changed restored result")
    finally:
        session.close()
    summary = {"network": "denied by macOS sandbox", "downloads": 0,
               "kind": args.kind, "task_id": task_id,
               "models": list(reports), "restore": "identical", "restart_restore": "identical",
               "raw_inputs_on_restart": "file reads denied by macOS sandbox",
               "profiles": len(profiles), "html_reports": len(artifacts)}
    save(evidence / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
