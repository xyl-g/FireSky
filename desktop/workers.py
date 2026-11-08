"""FireSky 桌面应用后台任务线程。

所有预测/下载都走 ``firesky.web_server`` 的权威路径（与 HTTP API 同一条
装配路径），QThread worker 里不碰任何 UI，只通过 Signal 把进度/结果/
错误回传主线程。全部任务支持 cancel() 协作式取消（当前阶段完成后停止）。
"""

from __future__ import annotations

import threading
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
    cancelled = Signal()  # 取消落地：UI 复位控件，避免界面卡死

    def __init__(self, parent: Any | None = None) -> None:
        super().__init__(parent)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def emit_progress(self, event: dict) -> None:
        if self._cancelled:
            # 与引擎 HTTP 路径 ForecastJob.progress 一致：取消后向权威路径
            # 抛 ForecastCancelled，让引擎在下一个回调检查点停止。
            from firesky.web_server import ForecastCancelled

            raise ForecastCancelled("计算已取消")
        self.progress.emit(event)

    def run(self) -> None:  # noqa: D102 - Qt 命名
        try:
            result = self.execute()
        except CancelledError:
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - 界面层统一兜底
            if self._cancelled:
                # 取消触发的引擎异常（ForecastCancelled 等）按取消收尾，
                # 不作为失败弹窗。
                self.cancelled.emit()
                return
            traceback.print_exc()
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        if self._cancelled:
            self.cancelled.emit()
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

    引擎的 GFS/CAMS 下载都发生在 run_forecast_payload 内部（统一评分先
    预取再评分），阶段一（数据准备）与阶段二（评分）由此通过 progress
    事件的 stage 字段区分；下载期没有事件时由心跳兜底，如实反映
    「正在下载/评分中」，不再出现「卡在 2% 数分钟无反馈」。
    """

    #: 评分阶段占用的进度区间（下载用 2..SCORE_SPAN_START）
    _SCORE_SPAN_START = 62

    def __init__(self, payload: dict, *, nowcast: bool = False) -> None:
        super().__init__()
        self._payload = dict(payload)
        self._nowcast = bool(nowcast)
        self._last_event_at = 0.0
        self._last_percent = 2
        self._last_message = "正在准备预测数据…"
        self._heartbeat_stop = threading.Event()

    def execute(self) -> dict:
        import time

        from firesky.web_server import (
            ForecastCancelled,
            WebServerConfig,
            nowcast_payload,
            run_forecast_payload,
        )

        config = WebServerConfig()
        payload = nowcast_payload(self._payload) if self._nowcast else dict(self._payload)

        # 阶段一提示：引擎会在内部预取 GFS/CAMS（缓存未命中时下载数分钟），
        # 明确告知用户「正在下载」，而不是停在低百分比无反馈。
        self.progress.emit({
            "stage": "prepare",
            "percent": 2,
            "message": "正在准备预测数据（GFS/CAMS 缓存未命中时自动下载）…",
        })

        self._heartbeat_stop.clear()
        self._last_event_at = time.monotonic()

        def heartbeat() -> None:
            # 下载/评分内部超过 8 秒没有任何进度事件时，发一条心跳日志：
            # 如实写「仍在下载/评分」，绝不伪造百分比。
            while not self._heartbeat_stop.wait(8.0):
                if self._cancelled:
                    return
                elapsed = int(time.monotonic() - self._last_event_at)
                downloading = self._last_percent < self._SCORE_SPAN_START
                self.progress.emit({
                    "stage": "download" if downloading else "score",
                    "percent": self._last_percent,
                    "message": (
                        f"仍在下载 {'GFS/CAMS'} 数据…（已进行 {elapsed}s，"
                        "首次运行下载耗时可达数分钟）"
                        if downloading
                        else f"仍在评分计算…（已进行 {elapsed}s）"
                    ),
                })

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = run_forecast_payload(
                payload,
                config,
                progress_callback=self._on_engine_progress,
            )
        except ForecastCancelled:
            raise CancelledError("预测已取消") from None
        finally:
            self._heartbeat_stop.set()
        return result

    def _on_engine_progress(self, event: dict) -> None:
        """引擎进度透传：阶段化 + 缓存命中/下载状态明确日志 + 百分比映射。

        引擎事件：cache(2)/命中(99) → prefetch_gfs(4)/decode_gfs(9)/
        prefetch_cams(12) → tensor(20) → cloud_field(42/54) → frames(55) →
        diagnostics(70) → report(91)/finalize(99)。前段按「数据准备」
        2–60%，评分段 62–100% 映射，与两阶段语义一致。
        """
        import time

        stage = str(event.get("stage") or "")
        raw_percent = int(event.get("percent") or 0)
        message = str(event.get("message") or "")
        if raw_percent >= 99 and stage == "cache":
            # 命中单点持久缓存：明确写日志（阶段二直接开始）
            message = "缓存命中，跳过下载（单点预测持久缓存）· 直接评分"
            percent = 62
        elif stage == "cache":
            # 检查缓存阶段仍在「准备数据」区间
            percent = max(2, raw_percent)
        elif raw_percent <= 20:
            percent = min(60, max(4, int(raw_percent * 3)))
        else:
            percent = max(
                self._SCORE_SPAN_START,
                min(100, raw_percent),
            )
        self._last_percent = percent
        self._last_event_at = time.monotonic()
        self.progress.emit({
            "stage": "download" if percent < self._SCORE_SPAN_START else "score",
            "percent": percent,
            "message": message,
        })


class RegionalForecastWorker(WorkerBase):
    """区域预测：run_national_forecast_payload（regions + spacing_deg 契约）。

    引擎的区域批处理在每点完成/进度回调处检查取消（emit_progress 抛
    ForecastCancelled），多区域长时间批处理也能即时停止。
    """

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self._payload = dict(payload)

    def execute(self) -> dict:
        from firesky.web_server import (
            ForecastCancelled,
            WebServerConfig,
            run_and_cache_national_forecast_payload,
        )

        config = WebServerConfig()
        try:
            return run_and_cache_national_forecast_payload(
                self._payload,
                config,
                progress_callback=self.emit_progress,
            )
        except ForecastCancelled:
            raise CancelledError("区域计算已取消")


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

    默认半径取 900 km，与实况评分读取 HSD 紧凑缓存的默认搜索半径一致
    （web_server nowcast 路径 radius_default=900.0）；半径参与缓存文件名，
    键不匹配会让预热对实况 fail-closed 毫无帮助。
    """

    def __init__(self, lat: float, lon: float, radius_km: float = 900.0) -> None:
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
    """观测点海拔+时区查询：走引擎 fetch_point_metadata 权威路径。

    引擎侧先查 Open-Meteo elevation/timezone（毫秒级），失败自动回退本地
    SRTM 瓦片（高精度 DSM）。返回 (timezone_name, elevation_m)，与引擎
    评分用的海拔口径一致，替代旧的 opentopodata 直连。
    """

    def __init__(self, lat: float, lon: float) -> None:
        super().__init__()
        self._lat = float(lat)
        self._lon = float(lon)

    def execute(self) -> dict:
        from firesky.location import fetch_point_metadata

        if self._cancelled:
            raise CancelledError("海拔查询已取消")
        try:
            timezone_name, elevation_m = fetch_point_metadata(
                self._lat, self._lon, 8
            )
        except Exception as exc:  # noqa: BLE001 - 界面层统一提示
            raise RuntimeError(f"DEM 海拔查询失败：{exc}") from exc
        return {
            "elevation_m": float(elevation_m),
            "timezone_name": str(timezone_name),
            "lat": self._lat,
            "lon": self._lon,
            "dataset": "SRTM/Open-Meteo DEM（引擎权威口径）",
        }


class LocationSearchWorker(WorkerBase):
    """地名搜索：firesky.location.search_location_fast（Nominatim，带缓存）。

    返回 LocationCandidate（name / lat / lon / display_name，可能附带
    timezone_name / elevation_m）。海拔元数据缺失时由 UI 侧再用
    ElevationQueryWorker 补齐。
    """

    def __init__(self, query: str, timeout_seconds: int = 6) -> None:
        super().__init__()
        self._query = str(query)
        self._timeout = int(timeout_seconds)

    def execute(self) -> dict:
        from firesky.location import search_location_fast

        if self._cancelled:
            raise CancelledError("地名搜索已取消")
        candidate = search_location_fast(self._query, self._timeout)
        return {
            "name": candidate.name,
            "lat": float(candidate.lat),
            "lon": float(candidate.lon),
            "display_name": candidate.display_name,
            "source": candidate.source,
            "timezone_name": candidate.timezone_name,
            "elevation_m": candidate.elevation_m,
        }


def format_level(level: str | None) -> str:
    return str(level or "无明显火烧云")


def run_callable_worker(fn: Callable[[], Any]) -> WorkerBase:
    """把任意函数包成可运行 worker（用于数据源检查等一次性调用）。"""
    worker = WorkerBase()
    worker.execute = fn  # type: ignore[method-assign]  # 动态任务体
    return worker
