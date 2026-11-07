"""FireSky 桌面应用后台任务线程。

所有预测/下载都走 ``firesky.web_server`` 的权威路径（与 HTTP API 同一条
装配路径），QThread worker 里不碰任何 UI，只通过 Signal 把进度/结果/
错误回传主线程。全部任务支持 cancel() 协作式取消（当前阶段完成后停止）。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from desktop.theme import LEVEL_COLORS  # noqa: F401  (re-export convenience)


class CancelledError(RuntimeError):
    """任务被用户取消（协作式：由各 worker 自行抛出）。"""


class WorkerBase(QThread):
    """通用后台任务骨架：cancel 标志 + progress/error/done 信号。"""

    progress = Signal(dict)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def emit_progress(self, event: dict) -> None:
        if not self._cancelled:
            self.progress.emit(event)

    def run(self) -> None:  # noqa: D102 - Qt 命名
        try:
            result = self.execute()
        except CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - 界面层统一兜底
            if self._cancelled:
                return
            traceback.print_exc()
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        if self._cancelled:
            return
        self.finished_ok.emit(result)

    def execute(self) -> Any:
        """子类实现：真正的工作内容（运行在子线程）。"""
        raise NotImplementedError


class ForecastWorker(WorkerBase):
    """单点/实况预测：共用 run_forecast_payload 权威路径。

    nowcast=True 时先把 payload 交给 web_server.nowcast_payload 归一化
    （forecast_mode=nowcast / model_source=satellite / satellite_source=auto），
    之后仍走同一条 run_forecast_payload 装配路径。
    """

    def __init__(self, payload: dict, *, nowcast: bool = False) -> None:
        super().__init__()
        self._payload = dict(payload)
        self._nowcast = bool(nowcast)

    def execute(self) -> dict:
        from firesky.web_server import WebServerConfig, nowcast_payload, run_forecast_payload

        config = WebServerConfig()
        payload = nowcast_payload(self._payload) if self._nowcast else dict(self._payload)
        return run_forecast_payload(
            payload,
            config,
            progress_callback=self.emit_progress,
        )


class RegionalForecastWorker(WorkerBase):
    """区域预测：run_national_forecast_payload（regions + spacing_deg 契约）。"""

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self._payload = dict(payload)

    def execute(self) -> dict:
        from firesky.web_server import WebServerConfig, run_and_cache_national_forecast_payload

        config = WebServerConfig()
        return run_and_cache_national_forecast_payload(
            self._payload,
            config,
            progress_callback=self.emit_progress,
        )


class DataFetchWorker(WorkerBase):
    """数据下载：handle_data_fetch 起 job，轮询 handle_data_fetch_status。

    payload: {"sources": ["gfs"|"ec"|"cams", ...], "target_dates": [...], ...}
    """

    def __init__(self, payload: dict, *, poll_seconds: float = 2.0) -> None:
        super().__init__()
        self._payload = dict(payload)
        self._poll_seconds = max(0.5, float(poll_seconds))

    def execute(self) -> dict:
        import time

        from firesky.web_server import (
            WebServerConfig,
            handle_data_fetch,
            handle_data_fetch_status,
        )

        config = WebServerConfig()
        job = handle_data_fetch(dict(self._payload), config)
        job_id = str(job.get("job_id") or "")
        deadline = time.monotonic() + 2400.0
        while True:
            if self._cancelled:
                raise CancelledError("下载已取消")
            time.sleep(self._poll_seconds)
            snapshot = handle_data_fetch_status(job_id)
            completed = int(snapshot.get("completed") or 0)
            total = int(snapshot.get("total") or 0)
            percent = int(100 * completed / total) if total else 0
            self.emit_progress({
                "percent": percent,
                "message": f"{snapshot.get('current') or snapshot.get('status')} "
                           f"({completed}/{total})",
                "snapshot": snapshot,
            })
            status = str(snapshot.get("status") or "")
            if status in {"done", "failed"}:
                return snapshot
            if time.monotonic() > deadline:
                return {**snapshot, "status": "timeout"}


class SatelliteWarmupWorker(WorkerBase):
    """Himawari HSD 单足迹预热：直接调 handle_satellite_cache_warmup。

    桌面端本地可信：WebServerConfig(satellite_warmup_token=...) 仅是 Web
    API 的管理令牌校验入口，直接函数调用不经过 require_satellite_warmup_access。
    """

    def __init__(self, lat: float, lon: float, radius_km: float = 200.0) -> None:
        super().__init__()
        self._lat = float(lat)
        self._lon = float(lon)
        self._radius_km = float(radius_km)

    def execute(self) -> dict:
        from datetime import datetime, timedelta, timezone

        from firesky.web_server import (
            WebServerConfig,
            handle_satellite_cache_warmup,
        )

        config = WebServerConfig(satellite_warmup_token="desktop-local")
        reference = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        # 找最近一个完整扫描：Himawari 每 10 分钟一帧，回退到上一个整 10 分钟。
        reference -= timedelta(minutes=reference.minute % 10)
        return handle_satellite_cache_warmup(
            {
                "lat": self._lat,
                "lon": self._lon,
                "radius_km": self._radius_km,
                "reference_time_utc": reference.isoformat(),
            },
            config,
        )


class DataPruneWorker(WorkerBase):
    """缓存清理：handle_data_prune（prune_cache 同路径）。"""

    def __init__(self, max_age_days: int = 2, cache_root: str = ".cache") -> None:
        super().__init__()
        self._max_age_days = int(max_age_days)
        self._cache_root = str(cache_root)

    def execute(self) -> dict:
        from firesky.web_server import handle_data_prune

        return handle_data_prune({
            "cache_root": self._cache_root,
            "max_age_days": self._max_age_days,
        })


class ElevationQueryWorker(WorkerBase):
    """Copernicus DEM 海拔查询（OpenTopoData REST，免费无 Key）。

    首选 copernicus_glo_90_dsm；该公共实例当前未收录该数据集时自动回退
    srtm90m（同为 90m DSM），并在结果里标注实际数据集。
    """

    _DATASETS = ("copernicus_glo_90_dsm", "srtm90m")

    def __init__(self, lat: float, lon: float) -> None:
        super().__init__()
        self._lat = float(lat)
        self._lon = float(lon)

    def execute(self) -> dict:
        import json
        from urllib.parse import quote
        from urllib.request import Request, urlopen

        last_error = ""
        for dataset in self._DATASETS:
            if self._cancelled:
                raise CancelledError("海拔查询已取消")
            url = (
                "https://api.opentopodata.org/v1/"
                f"{dataset}?locations={quote(f'{self._lat},{self._lon}')}"
            )
            request = Request(url, headers={"User-Agent": "FireSky-Desktop/2.0"})
            try:
                with urlopen(request, timeout=15) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if body.get("status") == "OK":
                    results = body.get("results") or []
                    elevation = results[0].get("elevation") if results else None
                    if elevation is not None:
                        return {
                            "elevation_m": float(elevation),
                            "dataset": dataset,
                            "lat": self._lat,
                            "lon": self._lon,
                        }
                last_error = str(body.get("error") or body.get("status"))
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        raise RuntimeError(f"Copernicus DEM 海拔查询失败：{last_error}")


def format_level(level: str | None) -> str:
    return str(level or "无明显火烧云")


def run_callable_worker(fn: Callable[[], Any]) -> WorkerBase:
    """把任意函数包成可运行 worker（用于数据源检查等一次性调用）。"""
    worker = WorkerBase()
    worker.execute = fn  # type: ignore[method-assign]  # 动态任务体
    return worker
