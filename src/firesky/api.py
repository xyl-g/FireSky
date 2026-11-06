# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 李晓洋
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 3.

"""FastAPI application for the firesky burning-sunset engine (port 8765).

This module is the HTTP layer only. All forecasting / data-fetch business
logic lives in :mod:`firesky.web_server` and is reused verbatim — we just
re-expose it through FastAPI routes, Server-Sent Events, gzip, and a JSON
logging pipeline.

Run with::

    uvicorn firesky.api:create_app --factory   # reads firesky/config/web_server.yaml
    # or directly:
    python -m firesky.api
"""
from __future__ import annotations

import json
import logging
import mimetypes
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from firesky.config import Settings, load_settings
from firesky.gfs import GfsInputUnavailableError
from firesky.location import fetch_point_metadata, search_location_fast
from firesky.logging_config import setup_logging
from firesky.web_server import (
    BUILD_DIR,
    DATA_FETCH_JOBS,
    FORECAST_JOBS,
    SATELLITE_WARMUP_JOBS,
    HCAI_WARMUP_JOBS,
    STATIC_DIR,
    WebServerConfig,
    diagnostic_bundle_path,
    handle_cache_warmup,
    handle_satellite_cache_warmup,
    handle_hcai_cache_warmup,
    require_satellite_warmup_access,
    handle_data_fetch,
    handle_data_fetch_status,
    handle_data_prune,
    handle_data_requirements,
    handle_data_status,
    handle_refresh_latest_cycles,
    handle_refresh_national_forecast_range,
    location_payload,
    national_cache_payload_from_query,
    nowcast_payload,
    read_map_frame,
    read_ecmwf_point_cross_section,
    read_national_cross_section,
    read_national_forecast_cache,
    read_national_forecast_summary,
    read_regional_diagnostic,
    regional_forecast_contract,
    run_and_cache_national_forecast_public_payload,
    run_forecast_payload,
)

log = logging.getLogger("firesky.api")


# ── App factory ──────────────────────────────────────────────────────────────

def build_web_server_config(settings: Settings) -> WebServerConfig:
    s = settings.server
    return WebServerConfig(
        workers=s.workers,
        report_dir=Path(s.report_dir),
        aerosol_source=s.aerosol_source,
        gfs_cache_dir=Path(s.gfs_cache_dir),
        ecmwf_cache_dir=Path(s.ecmwf_cache_dir),
        cams_cache_dir=Path(s.cams_cache_dir),
        map_cache_dir=Path(s.map_cache_dir),
        regional_cache_dir=Path(s.regional_cache_dir),
        point_cache_dir=Path(s.point_cache_dir),
        satellite_cache_dir=Path(s.satellite_cache_dir),
        satellite_hcai_cache_dir=Path(s.satellite_hcai_cache_dir),
        satellite_warmup_token=s.satellite_warmup_token,
        forecast_cache_seconds=s.forecast_cache_seconds,
        regional_refresh_payload=(
            dict(s.regional_refresh_payload)
            if isinstance(s.regional_refresh_payload, dict)
            else None
        ),
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Logging is configured in create_app() before serving; nothing else needed.
    yield
    listener = getattr(app.state, "log_listener", None)
    if listener is not None:
        listener.stop()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="firesky web", version="0.2.0", lifespan=_lifespan)
    app.add_middleware(GZipMiddleware, minimum_size=16384)
    app.state.settings = settings
    app.state.config = build_web_server_config(settings)
    app.state.log_listener = setup_logging(
        level=settings.logging.level,
        log_dir=settings.logging.dir,
        app_name=settings.logging.app_name,
        when=settings.logging.when,
        backup_count=settings.logging.backup_count,
        console=settings.logging.console,
    )
    _register_routes(app)
    _register_error_handlers(app)
    _register_middleware(app)
    return app


# ── Body helpers ─────────────────────────────────────────────────────────────

async def _read_json(request: Request) -> Any:
    body = await request.body()
    if not body:
        raise ValueError("请求内容为空或过大。")
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("请求体不是合法的 JSON。") from exc


async def _optional_json(request: Request) -> Any:
    body = await request.body()
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("请求体不是合法的 JSON。") from exc


# ── Static / SPA helpers ─────────────────────────────────────────────────────

def _serve_spa_index() -> Response:
    # adapter-static prerenders the site home page into index.html and keeps
    # the client-side router shell in 200.html — SPA routes must fall back to
    # the shell, otherwise /forecast renders the pre-rendered home page.
    fallback = BUILD_DIR / "200.html"
    if fallback.is_file():
        return FileResponse(fallback, media_type="text/html; charset=utf-8")
    index = BUILD_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html; charset=utf-8")
    return _serve_static_file("map.html", "text/html; charset=utf-8")


def _serve_static_file(name: str, content_type: str) -> Response:
    target = STATIC_DIR / name
    if not target.is_file():
        raise FileNotFoundError("页面资源不存在。")
    return FileResponse(target, media_type=content_type)


def _serve_build_asset(path: str) -> Response:
    safe = Path(urllib.parse.unquote(path))
    target = (BUILD_DIR / safe).resolve()
    if not target.is_file() or not str(target).startswith(str(BUILD_DIR.resolve())):
        raise FileNotFoundError("静态资源不存在。")
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=content_type)


# ── Routes ───────────────────────────────────────────────────────────────────

def _register_routes(app: FastAPI) -> None:

    @app.get("/")
    async def index() -> Response:
        return _serve_spa_index()

    @app.get("/legacy")
    async def legacy() -> Response:
        return _serve_static_file("map.html", "text/html; charset=utf-8")

    @app.get("/_app/{path:path}")
    async def build_asset(path: str) -> Response:
        return _serve_build_asset(f"_app/{path}")

    @app.get("/api/v1/firesky/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/v1/firesky/locations/search")
    async def location(query: str = "") -> dict:
        candidate = await run_in_threadpool(search_location_fast, query)
        return location_payload(candidate)

    # Legacy-compat aliases: the standalone map.html SPA (served at /legacy and
    # as the SPA fallback) calls the pre-v1 paths. They are thin aliases over
    # the canonical handlers above and add no new behavior.
    @app.get("/api/point")
    async def point_compat(lat: float = 0.0, lon: float = 0.0) -> dict:
        return await point(lat=lat, lon=lon)

    @app.get("/api/location")
    async def location_compat(query: str = "") -> dict:
        return await location(query=query)

    @app.get("/api/map-frame")
    async def map_frame_compat(token: str = "", time: str = "") -> dict:
        return await map_frame(token=token, time=time)

    @app.post("/api/forecast/start")
    async def forecast_start_compat(request: Request) -> dict:
        return await forecast_start(request)

    @app.post("/api/forecast")
    async def forecast_compat(request: Request) -> dict:
        return await forecast(request)

    @app.get("/api/v1/firesky/points/metadata")
    async def point(lat: float = 0.0, lon: float = 0.0) -> dict:
        if not -90.0 <= lat <= 90.0:
            raise ValueError("纬度必须在 -90 到 90 之间。")
        if not -180.0 <= lon <= 180.0:
            raise ValueError("经度必须在 -180 到 180 之间。")
        timezone_name, elevation_m = await run_in_threadpool(
            fetch_point_metadata, lat, lon
        )
        return {"lat": lat, "lon": lon, "timezone_name": timezone_name, "elevation_m": elevation_m}


    @app.get("/api/v1/firesky/map-frames")
    async def map_frame(token: str = "", time: str = "") -> dict:
        return await run_in_threadpool(
            read_map_frame, app.state.config.map_cache_dir, token, time
        )

    @app.get("/api/v1/firesky/forecasts/national/cache")
    async def national_cache(request: Request) -> dict:
        payload = national_cache_payload_from_query(dict(request.query_params))
        payload["_aerosol_source"] = app.state.config.aerosol_source
        return await run_in_threadpool(
            read_national_forecast_cache, payload, app.state.config.regional_cache_dir
        )

    @app.get("/api/v1/firesky/forecasts/national/contract")
    async def national_contract() -> dict:
        return regional_forecast_contract()

    @app.get("/api/v1/firesky/forecasts/national/summary")
    async def national_summary(request: Request) -> dict:
        payload = national_cache_payload_from_query(dict(request.query_params))
        payload["_aerosol_source"] = app.state.config.aerosol_source
        return await run_in_threadpool(
            read_national_forecast_summary, payload, app.state.config.regional_cache_dir
        )

    @app.get("/api/v1/firesky/forecasts/national/cross-section")
    async def national_cross_section(
        request: Request, lat: float = 0.0, lon: float = 0.0, offset_minutes: float = 0.0
    ) -> dict:
        if not -90.0 <= lat <= 90.0:
            raise ValueError("纬度必须在 -90 到 90 之间。")
        if not -180.0 <= lon <= 180.0:
            raise ValueError("经度必须在 -180 到 180 之间。")
        payload = national_cache_payload_from_query(dict(request.query_params))
        payload["_aerosol_source"] = app.state.config.aerosol_source
        if payload.get("model_source") == "ec":
            return await run_in_threadpool(
                read_ecmwf_point_cross_section,
                payload,
                app.state.config.ecmwf_cache_dir,
                lat,
                lon,
                offset_minutes,
            )
        return await run_in_threadpool(
            read_national_cross_section,
            payload,
            app.state.config.regional_cache_dir,
            lat,
            lon,
            offset_minutes,
        )

    @app.get("/api/v1/firesky/forecasts/national/diagnostic")
    async def national_diagnostic(request: Request, token: str = "", lat: float = 0.0, lon: float = 0.0) -> dict:
        def read_diagnostic() -> dict:
            path = diagnostic_bundle_path(app.state.config.regional_cache_dir, token)
            if not path.is_file():
                raise FileNotFoundError("区域完整诊断不存在或已过期。")
            return read_regional_diagnostic(path, lat, lon)

        return await run_in_threadpool(read_diagnostic)

    terminal_events = {"done", "failed", "cancelled"}

    def _job_terminal_event(job) -> dict[str, Any] | None:
        """Synthesize the terminal event for a job that already finished, so a
        late/reconnecting subscriber does not wait on an empty queue forever."""
        with job._lock:
            if job.completed_at is None:
                return None
            if job.status == "cancelled" or job._cancel_requested.is_set():
                return {
                    "event": "cancelled",
                    "stage": "cancelled",
                    "percent": 100,
                    "message": "计算已取消",
                }
            if job.status == "failed":
                unavailable = isinstance(job.error, GfsInputUnavailableError)
                return {
                    "event": "failed",
                    "stage": "failed",
                    "percent": 100,
                    "message": str(job.error),
                    "status": "unavailable" if unavailable else "failed",
                    "error_type": type(job.error).__name__,
                }
            event = {
                "event": "done",
                "stage": "done",
                "percent": 100,
                "message": "预测完成",
            }
            if job.defer_result:
                event["result_ref"] = job.job_id
            elif job.result is not None:
                event["result"] = job.result
            return event

    @app.get("/api/v1/firesky/forecasts/events")
    async def forecast_events(request: Request, job_id: str = "") -> StreamingResponse:
        job = FORECAST_JOBS.get(job_id)
        if job is None:
            raise FileNotFoundError("预测任务不存在或已过期。")
        if job.status == "failed" and isinstance(job.error, GfsInputUnavailableError):
            raise job.error
        initial_terminal = _job_terminal_event(job)

        async def event_stream():
            # A subscriber that connects after the job settled (reconnect after
            # a dropped stream, or a slow poller) must receive the terminal
            # event immediately instead of pinging on an empty queue forever.
            if initial_terminal is not None:
                data = json.dumps(initial_terminal, ensure_ascii=False, separators=(",", ":"))
                yield f"event: {initial_terminal['event']}\ndata: {data}\n\n"
                return
            while True:
                event = await run_in_threadpool(job.next_event, 15.0)
                if event is None:
                    terminal = _job_terminal_event(job)
                    if terminal is not None:
                        data = json.dumps(terminal, ensure_ascii=False, separators=(",", ":"))
                        yield f"event: {terminal['event']}\ndata: {data}\n\n"
                        break
                    yield ": ping\n\n"
                    continue
                name = str(event.get("event") or "progress")
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                yield f"event: {name}\ndata: {data}\n\n"
                if name in terminal_events:
                    break

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/firesky/forecasts/jobs")
    async def forecast_jobs(
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return FORECAST_JOBS.snapshot_list(
            status=status,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )

    @app.get("/api/v1/firesky/forecasts/jobs/{job_id}")
    async def forecast_job_detail(job_id: str) -> dict:
        job = FORECAST_JOBS.get(job_id)
        if job is None:
            raise FileNotFoundError("预测任务不存在或已过期。")
        return {
            "id": job.job_id,
            "status": job.status,
            "progress_percent": job.percent,
            "progress_stage": job.stage,
            "progress_message": job.message,
            "error_message": str(job.error) if job.error is not None else None,
            "request_payload": job.request_payload,
            "started_at": job.started_at,
            "cache_key": job.cache_key,
            "algorithm_version": job.algorithm_version,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        }

    @app.get("/api/v1/firesky/forecasts/results/{job_id}")
    async def forecast_job_result(job_id: str) -> dict:
        job = FORECAST_JOBS.get(job_id)
        if job is None:
            raise FileNotFoundError("预测任务不存在或已过期。")
        if job.status == "failed" and isinstance(job.error, GfsInputUnavailableError):
            raise job.error
        if job.status == "failed":
            raise RuntimeError(str(job.error or "预测任务失败。"))
        if job.status != "done" or job.result is None:
            raise FileNotFoundError("预测结果尚未完成。")
        return job.result

    @app.post("/api/v1/firesky/forecasts/{job_id}/cancel")
    async def forecast_cancel(job_id: str) -> dict:
        if not FORECAST_JOBS.cancel(job_id):
            raise FileNotFoundError("预测任务不存在或已结束。")
        return {"job_id": job_id, "status": "cancelled"}

    @app.get("/api/v1/firesky/data/status")
    async def data_status(request: Request) -> dict:
        query = request.query_params
        payload: dict[str, Any] = {}
        if query.get("region"):
            payload["region"] = query["region"]
        if query.get("region_name"):
            payload["region_name"] = query["region_name"]
        if query.get("timezone"):
            payload["timezone"] = query["timezone"]
        if query.get("bbox"):
            payload["bbox"] = [float(value) for value in query["bbox"].split(",")]
        if query.get("phenomena"):
            payload["phenomena"] = [value for value in query["phenomena"].split(",") if value]
        if query.get("days"):
            payload["days"] = int(query["days"])
        if query.get("spacing_deg"):
            payload["spacing_deg"] = float(query["spacing_deg"])
        # Cache scans and cycle metadata are synchronous filesystem work. Keep
        # them out of the asyncio event loop so a long regional writer cannot
        # stall unrelated API requests.
        return await run_in_threadpool(handle_data_status, app.state.config, payload)

    @app.get("/api/v1/firesky/data/fetch/status")
    async def data_fetch_status(job_id: str = "") -> dict:
        if not job_id.strip():
            raise ValueError("缺少 job_id 参数。")
        return await run_in_threadpool(handle_data_fetch_status, job_id)

    @app.get("/reports/{path:path}")
    async def report_file(path: str) -> Response:
        config = app.state.config
        root = config.report_dir.resolve()
        target = (root / Path(path)).resolve()
        if not target.is_file() or root not in target.parents:
            raise FileNotFoundError("报告不存在。")
        content_type = mimetypes.guess_type(target.name)[0] or "text/html"
        return FileResponse(target, media_type=f"{content_type}; charset=utf-8")

    # ── POST routes ────────────────────────────────────────────────────────

    @app.post("/api/v1/firesky/data/latest/refresh")
    async def data_latest_refresh(request: Request) -> dict:
        payload = await _optional_json(request)
        return await run_in_threadpool(
            handle_refresh_latest_cycles, payload, app.state.config
        )

    @app.post("/api/v1/firesky/data/requirements")
    async def data_requirements(request: Request) -> dict:
        payload = await _optional_json(request)
        return await run_in_threadpool(handle_data_requirements, payload)

    @app.post("/api/v1/firesky/data/fetch")
    async def data_fetch(request: Request) -> dict:
        payload = await _read_json(request)
        return await run_in_threadpool(
            handle_data_fetch, payload, app.state.config
        )

    @app.post("/api/v1/firesky/data/prune")
    async def data_prune(request: Request) -> dict:
        payload = await _read_json(request)
        return await run_in_threadpool(handle_data_prune, payload)

    @app.post("/api/v1/firesky/cache/warmup")
    async def cache_warmup(request: Request) -> dict:
        payload = await _read_json(request)
        return await run_in_threadpool(
            handle_cache_warmup, payload, app.state.config
        )

    @app.post("/api/v1/firesky/satellite/cache/warmup")
    async def satellite_cache_warmup(request: Request) -> dict:
        payload = await _read_json(request)
        require_satellite_warmup_access(
            app.state.config,
            request.headers.get("x-firesky-warmup-token"),
        )
        job = SATELLITE_WARMUP_JOBS.start(payload, app.state.config)
        return {"job_id": job.job_id, "status": "queued"}

    @app.post("/api/v1/firesky/satellite/hcai/cache/warmup")
    async def hcai_cache_warmup(request: Request) -> dict:
        payload = await _read_json(request)
        require_satellite_warmup_access(
            app.state.config,
            request.headers.get("x-firesky-warmup-token"),
        )
        job = HCAI_WARMUP_JOBS.start(payload, app.state.config)
        return {"job_id": job.job_id, "status": "queued", "source": "hcai"}

    @app.get("/api/v1/firesky/satellite/cache/warmup/status")
    async def satellite_cache_warmup_status(request: Request, job_id: str = "") -> dict:
        require_satellite_warmup_access(
            app.state.config,
            request.headers.get("x-firesky-warmup-token"),
        )
        if not job_id.strip():
            raise ValueError("缺少 job_id 参数。")
        job = SATELLITE_WARMUP_JOBS.get(job_id)
        if job is None:
            raise FileNotFoundError("卫星预热任务不存在或已过期。")
        return job.snapshot()

    @app.get("/api/v1/firesky/satellite/hcai/cache/warmup/status")
    async def hcai_cache_warmup_status(request: Request, job_id: str = "") -> dict:
        require_satellite_warmup_access(
            app.state.config,
            request.headers.get("x-firesky-warmup-token"),
        )
        if not job_id.strip():
            raise ValueError("缺少 job_id 参数。")
        job = HCAI_WARMUP_JOBS.get(job_id)
        if job is None:
            raise FileNotFoundError("HCAI 卫星预热任务不存在或已过期。")
        return job.snapshot()

    @app.post("/api/v1/firesky/forecasts/nowcast")
    async def forecast_nowcast_start(request: Request) -> dict:
        payload = await _read_json(request)
        job = FORECAST_JOBS.start(nowcast_payload(payload), app.state.config)
        return {"job_id": job.job_id, "forecast_mode": "nowcast"}

    @app.post("/api/v1/firesky/forecasts/nowcast/compute")
    async def forecast_nowcast(request: Request) -> dict:
        payload = await _read_json(request)
        return await run_in_threadpool(run_forecast_payload, nowcast_payload(payload), app.state.config)

    @app.post("/api/v1/firesky/forecasts/national")
    async def forecast_national_start(request: Request) -> dict:
        payload = await _read_json(request)
        job = FORECAST_JOBS.start(
            payload,
            app.state.config,
            run_and_cache_national_forecast_public_payload,
            defer_result=True,
            regional=True,
        )
        return {"job_id": job.job_id, "scope": "regional"}

    @app.post("/api/v1/firesky/forecasts/national/refresh")
    async def forecast_national_refresh(request: Request) -> dict:
        payload = await _optional_json(request)
        return await run_in_threadpool(
            handle_refresh_national_forecast_range, payload, app.state.config
        )

    @app.post("/api/v1/firesky/forecasts/national/compute")
    async def forecast_national(request: Request) -> dict:
        payload = await _read_json(request)
        return await run_in_threadpool(
            run_and_cache_national_forecast_public_payload, payload, app.state.config
        )

    @app.post("/api/v1/firesky/forecasts")
    async def forecast_start(request: Request) -> dict:
        payload = await _read_json(request)
        job = FORECAST_JOBS.start(payload, app.state.config)
        return {"job_id": job.job_id}

    @app.post("/api/v1/firesky/forecasts/compute")
    async def forecast(request: Request) -> dict:
        payload = await _read_json(request)
        return await run_in_threadpool(run_forecast_payload, payload, app.state.config)

    # ── SPA client-side routing fallback (must be registered last) ──────────

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> Response:
        return _serve_spa_index()


# ── Error handlers ───────────────────────────────────────────────────────────

def _register_error_handlers(app: FastAPI) -> None:

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def not_found_handler(request: Request, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.exception_handler(GfsInputUnavailableError)
    async def gfs_input_unavailable_handler(
        request: Request, exc: GfsInputUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "error": str(exc)},
        )

    @app.exception_handler(Exception)
    async def server_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("未处理的异常：%s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Access-log middleware ─────────────────────────────────────────────────────

def _register_middleware(app: FastAPI) -> None:

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        # Forecast HTML and forecast responses are date/cycle-sensitive. The
        # server already selects the newest matching cache, so a browser or
        # intermediary must not reuse yesterday's document or summary after a
        # China-local midnight.
        path = request.url.path
        if request.method in {"GET", "HEAD"} and (
            path in {"/", "/legacy", "/forecast", "/outlook"}
            or path.startswith("/api/v1/firesky/forecasts/")
        ):
            response.headers["Cache-Control"] = "no-store"
        elapsed_ms = (time.monotonic() - start) * 1000
        log.info(
            "%s %s -> %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "event": "access",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(elapsed_ms, 1),
            },
        )
        return response


# ── Direct execution ─────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn

    settings = load_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
