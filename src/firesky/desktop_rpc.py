"""Private JSON-lines IPC for native desktop clients. No listening network port."""

from __future__ import annotations

import copy
import json
import math
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024
SESSION_SECONDS = 1800


class RequestError(ValueError):
    pass


def json_value(value):
    """Keep engine output safe for Foundation/System.Text.Json, including NaNs."""
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date, Path)):
        return str(value)
    if hasattr(value, "tolist"):
        return json_value(value.tolist())
    return value


def client_report(report):
    result = copy.deepcopy(report)
    if "satellite_frames" in report:
        count = report["satellite_frames"]
        result["datasets"].append({
            "source": "hsd", "ready": count >= 2, "verified": True,
            "is_latest": True, "missing_count": max(0, 2 - count),
        })
    return result


@dataclass
class Session:
    payload: dict
    kind: str
    report: dict
    created: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)
    consumed: bool = False
    dismissed: bool = False


class DesktopService:
    """Own request snapshots and operation locks, not frontend widgets."""

    def __init__(self, config=None):
        from firesky.web_server import WebServerConfig
        self.config = config or WebServerConfig()
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.compute_lock = threading.Lock()
        self.network_probe_lock = threading.Lock()
        self._session_owner = uuid.uuid4().hex

    @staticmethod
    def _native_sessions_enabled():
        from firesky.rust_desktop_data import enabled as rust_enabled

        return rust_enabled()

    def _native_session_call(
        self,
        action,
        *,
        token=None,
        kind=None,
        payload=None,
        report=None,
    ):
        from firesky.rust_desktop_data import session as rust_session

        return rust_session(
            action,
            self._session_owner,
            token=token,
            kind=kind,
            payload=payload,
            report=report,
        )

    def _check(self, payload, kind, *, fast=True, refresh=False):
        from firesky import desktop_data as data
        if kind == "nowcast":
            return data.check_nowcast_data(
                payload, config=self.config, probe=refresh, fast=fast,
            )
        return data.check_model_data(
            payload, config=self.config, regional=kind == "regional",
            probe=refresh, refresh=refresh, fast=fast,
        )

    @staticmethod
    def _pin(session):
        report, payload = session.report, session.payload
        if report.get("model_cycle"):
            payload[f"_{report['model_source']}_cycle_override"] = report["model_cycle"]
        for model, cycle in report.get("model_cycles", {}).items():
            payload[f"_{model}_cycle_override"] = cycle
        if report.get("reference_time_utc"):
            payload["reference_time_utc"] = report["reference_time_utc"]
        if report.get("cams_update_cycle"):
            payload["_cams_refresh_cycle"] = report["cams_update_cycle"]

    def _session(self, params):
        token = str(params.get("session") or "")
        with self.lock:
            session = self.sessions.get(token)
        if session is None or session.dismissed or session.consumed:
            raise RequestError("数据检查已过期，请重新检查。")
        if self._native_sessions_enabled():
            from firesky.rust_grid import EngineOperationError

            try:
                native = self._native_session_call("validate", token=token)
            except EngineOperationError as exc:
                raise RequestError("数据检查已过期，请重新检查。") from exc
            if native["kind"] != session.kind:
                raise RequestError("数据检查已过期，请重新检查。")
        elif time.monotonic() - session.created > SESSION_SECONDS:
            raise RequestError("数据检查已过期，请重新检查。")
        return session

    def dispatch(self, method, params, progress=lambda event: None):
        from firesky import desktop_data as data
        if method == "hello":
            from firesky.desktop_credentials import cams_status
            from firesky.web_server import REGIONAL_FORECAST_AREAS
            return {"protocol": PROTOCOL_VERSION, "engine": "firesky",
                    "capabilities": ["point", "regional", "nowcast", "inventory", "network_probe"],
                    "cams_credentials": cams_status(),
                    "workspace": str(Path.cwd()),
                    "regions": [{"code": code, **definition}
                                for code, definition in REGIONAL_FORECAST_AREAS.items()]}
        if method == "cams_credentials":
            from firesky.desktop_credentials import cams_status
            return cams_status()
        if method == "save_cams_key":
            from firesky.desktop_credentials import save_cams_key
            return save_cams_key(params.get("key"))
        if method == "network_probe":
            from firesky.desktop_network import probe_downloads
            if not self.network_probe_lock.acquire(blocking=False):
                raise RequestError("下载探测正在运行，请稍候。")
            try:
                source = str(params.get("ecmwf_source") or "ecmwf")
                return probe_downloads(progress, ecmwf_source=source)
            finally:
                self.network_probe_lock.release()
        if method == "inventory":
            return {"datasets": data.inventory(self.config)}
        if method == "search":
            from firesky.rust_desktop_data import enabled as rust_enabled
            if rust_enabled():
                from firesky.rust_desktop_data import location as rust_location

                return rust_location(method, params)
            from firesky.location import search_location_fast, fetch_point_metadata
            query = str(params.get("query") or "").strip()
            if not query or len(query) > 200:
                raise RequestError("请输入有效地名。")
            candidate = search_location_fast(query, 6)
            tz, elevation = fetch_point_metadata(candidate.lat, candidate.lon, 8)
            return {"name": candidate.name, "lat": candidate.lat, "lon": candidate.lon,
                    "timezone_name": tz, "elevation_m": elevation}
        if method == "metadata":
            from firesky.rust_desktop_data import enabled as rust_enabled
            if rust_enabled():
                from firesky.rust_desktop_data import location as rust_location

                return rust_location(method, params)
            from firesky.location import fetch_point_metadata
            lat, lon = float(params["lat"]), float(params["lon"])
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise RequestError("坐标超出范围。")
            tz, elevation = fetch_point_metadata(lat, lon, 8)
            return {"lat": lat, "lon": lon, "timezone_name": tz, "elevation_m": elevation}
        if method == "check":
            kind = params.get("kind", "point")
            if kind not in {"point", "regional", "nowcast"}:
                raise RequestError("未知预测类型。")
            payload = copy.deepcopy(params.get("payload"))
            if not isinstance(payload, dict):
                raise RequestError("预测参数必须是对象。")
            payload["model_source"] = "both"
            payload["workers"] = 1
            if kind == "regional":
                payload["spacing_deg"] = 0.25
            report = self._check(payload, kind)
            progress({"percent": 100, "stage": "check"})
            session = Session(payload, kind, report)
            self._pin(session)
            native_sessions = self._native_sessions_enabled()
            with self.lock:
                self.sessions = {
                    key: item for key, item in self.sessions.items()
                    if not item.dismissed and not item.consumed
                    and time.monotonic() - item.created <= SESSION_SECONDS
                }
                if len(self.sessions) >= 32:
                    raise RequestError("检查窗口过多，请关闭后重试。")
                if native_sessions:
                    token = self._native_session_call(
                        "create",
                        kind=kind,
                        payload=session.payload,
                        report=session.report,
                    )["session"]
                else:
                    token = uuid.uuid4().hex
                self.sessions[token] = session
            return {"session": token, "report": client_report(report)}
        if method == "dismiss":
            token = str(params.get("session") or "")
            with self.lock:
                session = self.sessions.get(token)
            if session and self._native_sessions_enabled():
                self._native_session_call("dismiss", token=token)
            with self.lock:
                if session:
                    session.dismissed = True
            return {"dismissed": True}
        if method not in {"publications", "prepare", "forecast"}:
            raise RequestError("未知操作。")
        session = self._session(params)
        if method == "publications":
            with session.lock:
                report = session.report
                sources = [r["source"] for r in report["datasets"]]
            if self._native_sessions_enabled():
                from firesky.rust_desktop_data import publications as rust_publications

                publications = rust_publications(sources)
            else:
                publications = data.published_cycles(sources)
            # Publication I/O must never hold the session's operation lock.
            # Ignore late replies once preparation or forecasting has begun.
            if session.lock.acquire(blocking=False):
                try:
                    if session.report is report and not session.dismissed and not session.consumed:
                        next_report = data.apply_publications(report, publications)
                        if self._native_sessions_enabled():
                            self._native_session_call(
                                "update",
                                token=params["session"],
                                report=next_report,
                            )
                        session.report = next_report
                    report = session.report
                finally:
                    session.lock.release()
            return {"session": params["session"], "report": client_report(report)}
        if not session.lock.acquire(blocking=False):
            raise RequestError("当前数据检查仍在处理，请稍候。")
        try:
            if session.dismissed or session.consumed:
                raise RequestError("数据检查已关闭，请重新检查。")
            if method == "prepare":
                if not self.compute_lock.acquire(blocking=False):
                    raise RequestError("已有下载或预测任务正在运行。")
                try:
                    if any(
                        row["source"] != "cams" and row.get("verified")
                        and row.get("latest_available_cycle_utc")
                        and row.get("latest_available_cycle") not in
                        row["request_cycle"].split(" / ")
                        for row in session.report["datasets"]
                    ):
                        payload = copy.deepcopy(session.payload)
                        for key in ("_gfs_cycle_override", "_ec_cycle_override"):
                            payload.pop(key, None)
                        session.report = self._check(payload, session.kind, refresh=True)
                        session.payload = payload
                    self._pin(session)
                    session.report = {**session.report, "ready": False}
                    if self._native_sessions_enabled():
                        self._native_session_call(
                            "update",
                            token=params["session"],
                            payload=session.payload,
                            report=session.report,
                        )
                    if session.kind == "nowcast":
                        data.prepare_nowcast_data(
                            session.payload, config=self.config, progress=progress,
                        )
                    else:
                        data.prepare_model_data(
                            session.payload, regional=session.kind == "regional",
                            config=self.config, progress=progress,
                        )
                    session.report = self._check(session.payload, session.kind, fast=False)
                    self._pin(session)
                    if self._native_sessions_enabled():
                        self._native_session_call(
                            "update",
                            token=params["session"],
                            payload=session.payload,
                            report=session.report,
                        )
                    return {"session": params["session"], "report": client_report(session.report)}
                finally:
                    self.compute_lock.release()
            if params.get("confirmed") is not True:
                raise RequestError("请先确认数据，再开始预测。")
            if not session.report.get("ready"):
                raise RequestError("数据有缺失，请先下载。")
            if not self.compute_lock.acquire(blocking=False):
                raise RequestError("已有下载或预测任务正在运行。")
            try:
                # Metadata-only checks do not prove GRIB integrity.
                session.report = self._check(session.payload, session.kind, fast=False)
                if not session.report.get("ready"):
                    raise RequestError("数据校验未通过，请重新检查并下载。")
                if self._native_sessions_enabled():
                    self._native_session_call(
                        "update",
                        token=params["session"],
                        payload=session.payload,
                        report=session.report,
                    )
                progress({"stage": "score", "percent": 0})
                if session.dismissed:
                    raise RequestError("预测已取消。")
                if self._native_sessions_enabled():
                    self._native_session_call("consume", token=params["session"])
                session.consumed = True
                payload = copy.deepcopy(session.payload)
                payload.pop("_cams_refresh_cycle", None)
                return self._forecast(session.kind, payload, progress)
            finally:
                self.compute_lock.release()
        finally:
            session.lock.release()

    def _forecast(self, kind, payload, progress):
        from firesky import web_server as engine
        if kind == "nowcast":
            return engine.run_forecast_payload(
                engine.nowcast_payload(payload), self.config, progress_callback=progress,
            )
        if kind in {"point", "regional"} and self._native_sessions_enabled():
            from firesky import rust_desktop_data

            results, errors = {}, {}
            for index, model in enumerate(("gfs", "ec")):
                def update(event, index=index, model=model):
                    progress({**event, "model_source": model,
                              "percent": index * 50 + min(100, max(0, int(
                                  event.get("percent") or 0))) // 2})
                update({"percent": 0})
                try:
                    if kind == "point":
                        results[model] = rust_desktop_data.forecast_point(
                            {**payload, "model_source": model},
                            model=model,
                            config=self.config,
                            progress=update,
                        )
                    else:
                        results[model] = rust_desktop_data.forecast_regional(
                            {**payload, "model_source": model},
                            model=model,
                            config=self.config,
                            progress=update,
                        )
                except engine.ForecastCancelled:
                    raise
                except Exception as exc:
                    errors[model] = str(exc)
            if not results:
                raise RuntimeError("；".join(f"{m.upper()}: {e}" for m, e in errors.items()))
            return {"model_results": results, "model_errors": errors}
        run = (
            engine.run_and_cache_national_forecast_payload if kind == "regional"
            else engine.run_forecast_payload
        )
        results, errors = {}, {}
        for index, model in enumerate(("gfs", "ec")):
            def update(event, index=index, model=model):
                progress({**event, "model_source": model,
                          "percent": index * 50 + min(100, max(0, int(
                              event.get("percent") or 0))) // 2})
            update({"percent": 0})
            try:
                results[model] = run(
                    {**payload, "model_source": model}, self.config,
                    progress_callback=update,
                )
            except engine.ForecastCancelled:
                raise
            except Exception as exc:
                errors[model] = str(exc)
        if not results:
            raise RuntimeError("；".join(f"{m.upper()}: {e}" for m, e in errors.items()))
        return {"model_results": results, "model_errors": errors}


def serve(input_stream=None, output_stream=None, service=None):
    """One child per app; cancellation is cooperative through engine callbacks."""
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    service = service or DesktopService()
    write_lock = threading.Lock()
    active_lock = threading.Lock()
    active: dict[str, threading.Event] = {}

    def send(message):
        text = json.dumps(json_value(message), ensure_ascii=False, allow_nan=False)
        with write_lock:
            output_stream.write(text + "\n")
            output_stream.flush()

    def execute(request, cancel):
        request_id = request["id"]
        def progress(event):
            if cancel.is_set():
                from firesky.web_server import ForecastCancelled
                raise ForecastCancelled("任务已取消")
            source = str(event.get("model_source") or "")
            message = str(event.get("message") or "").upper()
            dataset = next((s for s in ("CAMS", "GFS", "EC", "HSD")
                            if s in message), source.upper())
            safe_event = {"percent": event.get("percent", 0),
                          "stage": event.get("stage", ""), "source": dataset}
            if isinstance(event.get("message"), str):
                safe_event["message"] = event["message"][:1024]
            if isinstance(event.get("cache_hit"), bool):
                safe_event["cache_hit"] = event["cache_hit"]
            if request["method"] == "network_probe" and isinstance(event.get("probe"), dict):
                safe_event["probe"] = {
                    key: event["probe"][key] for key in
                    ("source", "title", "host", "state", "detail", "bytes_received", "elapsed_ms")
                    if key in event["probe"]
                }
            send({"id": request_id, "type": "progress", "event": safe_event})
        try:
            result = service.dispatch(request["method"], request.get("params") or {}, progress)
            if cancel.is_set():
                raise RequestError("任务已取消")
            send({"id": request_id, "type": "result", "result": result})
        except Exception as exc:
            send({"id": request_id, "type": "error",
                  "error": {"code": "cancelled" if cancel.is_set() else "operation_failed",
                            "message": str(exc) or type(exc).__name__}})
        finally:
            with active_lock:
                active.pop(request_id, None)

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="desktop") as executor:
        while True:
            line = input_stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            request_id = None
            try:
                if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                    raise RequestError("请求过大。")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise RequestError("无效请求。")
                request_id = request.get("id")
                if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                    raise RequestError("无效请求编号。")
                if not isinstance(request.get("params", {}), dict):
                    raise RequestError("无效参数。")
                with active_lock:
                    if request_id in active:
                        raise RequestError("重复请求编号。")
                    if request.get("method") == "cancel":
                        target = active.get(str(request["params"].get("request_id")))
                        if target:
                            target.set()
                        send({"id": request_id, "type": "result",
                              "result": {"requested": bool(target)}})
                        continue
                    if len(active) >= 16:
                        raise RequestError("任务过多，请稍候。")
                    cancel = threading.Event()
                    active[request_id] = cancel
                executor.submit(execute, request, cancel)
            except (ValueError, TypeError, KeyError) as exc:
                send({"id": request_id, "type": "error",
                      "error": {"code": "invalid_request", "message": str(exc)}})
        with active_lock:
            for cancel in active.values():
                cancel.set()


def main():
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    serve(output_stream=protocol_output)


if __name__ == "__main__":
    main()
