"""FireSky 桌面应用 · 数据源标签页。

- CAMS ADS API Key：QLineEdit(Password) 保存到 ~/.firesky-desktop/config.json，
  同步写 ~/.cdsapirc（aerosol.py 的 cdsapi.Client 默认读取该文件）。
- GFS / EC / Himawari：「检查最新周期」（handle_data_requirements）与
  「立即下载」（handle_data_fetch → 轮询 job）。
- 缓存占用显示 + 清理（handle_data_prune）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.widgets import load_app_config, save_app_config
from desktop.workers import (
    DataFetchWorker,
    DataPruneWorker,
    SatelliteWarmupWorker,
    WorkerBase,
)

CDSAPIRC = Path.home() / ".cdsapirc"


class DataSourceTab(QWidget):
    """数据源设置与缓存维护。"""

    def __init__(self, point_tab: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._point_tab = point_tab
        self._workers: list[WorkerBase] = []
        self._build_ui()
        self._load_saved_key()

    # ── UI ──
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 16, 26, 12)
        root.setSpacing(12)

        head = QLabel("数据源设置")
        head.setProperty("role", "h1")
        root.addWidget(head)
        note = widgets.ink2_label(
            "密钥保存在本机 ~/.firesky-desktop/config.json（同时同步 ~/.cdsapirc 供 cdsapi 读取），不写入仓库。"
        )
        root.addWidget(note)

        # ── CAMS ADS ──
        cams_card = widgets.make_card()
        cams_l = QVBoxLayout(cams_card)
        cams_l.setContentsMargins(16, 12, 16, 12)
        cams_l.addWidget(widgets.card_title("CAMS · Copernicus ADS（气溶胶 · AOD 必须）"))
        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("ADS API Key（https://ads.atmosphere.copernicus.eu）")
        self.key_show = QPushButton("显示")
        self.key_show.setCheckable(True)
        self.key_show.toggled.connect(self._toggle_key_visibility)
        save_key_btn = QPushButton("保存 Key")
        save_key_btn.clicked.connect(self._save_key)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.key_show)
        key_row.addWidget(save_key_btn)
        cams_l.addLayout(key_row)
        self.key_status = widgets.ink2_label("")
        cams_l.addWidget(self.key_status)
        root.addWidget(cams_card)

        # ── GFS ──
        gfs_card = widgets.make_card()
        gfs_l = QVBoxLayout(gfs_card)
        gfs_l.setContentsMargins(16, 12, 16, 12)
        gfs_l.addWidget(widgets.card_title("GFS · NOMADS（主模式 · 免 Key）"))
        gfs_l.addWidget(self._source_actions_row("gfs"))
        self.gfs_status = widgets.ink2_label("本地缓存：.cache/gfs")
        gfs_l.addWidget(self.gfs_status)
        root.addWidget(gfs_card)

        # ── EC ──
        ec_card = widgets.make_card()
        ec_l = QVBoxLayout(ec_card)
        ec_l.setContentsMargins(16, 12, 16, 12)
        ec_l.addWidget(widgets.card_title("ECMWF · Open Data（第二模式 · 免 Key）"))
        ec_l.addWidget(self._source_actions_row("ec"))
        self.ec_status = widgets.ink2_label("本地缓存：.cache/ecmwf")
        ec_l.addWidget(self.ec_status)
        root.addWidget(ec_card)

        # ── Himawari ──
        sat_card = widgets.make_card()
        sat_l = QVBoxLayout(sat_card)
        sat_l.setContentsMargins(16, 12, 16, 12)
        sat_l.addWidget(widgets.card_title("Himawari HSD（实况标签 · 免 Key · 单足迹预热）"))
        sat_row = QHBoxLayout()
        warm_btn = QPushButton("预热最新扫描（当前观测点 200 km）")
        warm_btn.clicked.connect(self._warm_satellite)
        sat_row.addWidget(warm_btn)
        sat_row.addStretch(1)
        sat_l.addLayout(sat_row)
        self.sat_status = widgets.ink2_label("对单点标签当前观测点执行 handle_satellite_cache_warmup。")
        sat_l.addWidget(self.sat_status)
        root.addWidget(sat_card)

        # ── 缓存维护 ──
        cache_card = widgets.make_card()
        cache_l = QVBoxLayout(cache_card)
        cache_l.setContentsMargins(16, 12, 16, 12)
        cache_l.addWidget(widgets.card_title("缓存维护"))
        cache_row = QHBoxLayout()
        self.cache_usage = widgets.ink2_label("缓存占用统计中…")
        refresh_btn = QPushButton("刷新占用")
        refresh_btn.clicked.connect(self._refresh_usage)
        prune_btn = QPushButton("清理过期缓存（保留 2 天）")
        prune_btn.clicked.connect(self._prune)
        cache_row.addWidget(self.cache_usage, 1)
        cache_row.addWidget(refresh_btn)
        cache_row.addWidget(prune_btn)
        cache_l.addLayout(cache_row)
        self.cache_progress = QProgressBar()
        self.cache_progress.setRange(0, 0)  # 忙碌指示
        self.cache_progress.setVisible(False)
        cache_l.addWidget(self.cache_progress)
        root.addWidget(cache_card, 1)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(150)
        root.addWidget(self.log_view)
        self._log("数据源操作走 web_server.handle_data_requirements / handle_data_fetch / handle_data_prune。")

        self._refresh_usage()

    def _source_actions_row(self, source: str) -> QWidget:
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        check_btn = QPushButton("检查最新周期")
        check_btn.clicked.connect(lambda: self._check_cycles(source))
        fetch_btn = QPushButton("立即下载（当日时效）")
        fetch_btn.clicked.connect(lambda: self._fetch(source))
        status_label = widgets.ink2_label("")
        row_l.addWidget(check_btn)
        row_l.addWidget(fetch_btn)
        row_l.addWidget(status_label, 1)
        if source == "gfs":
            self.gfs_action_status = status_label
        else:
            self.ec_action_status = status_label
        return row

    # ── ADS Key ──
    def _toggle_key_visibility(self, checked: bool) -> None:
        self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.key_show.setText("隐藏" if checked else "显示")

    def _load_saved_key(self) -> None:
        config = load_app_config()
        key = str(config.get("ads_api_key") or "")
        if key:
            self.key_edit.setText(key)
            self.key_status.setText("✓ 已保存（~/.firesky-desktop/config.json + ~/.cdsapirc）")
        else:
            # ~/.cdsapirc 已有配置（引擎默认路径）时提示即可
            if CDSAPIRC.is_file():
                self.key_status.setText(
                    "检测到已有 ~/.cdsapirc；可在上方保存 Key 以同步桌面端配置。"
                )
            else:
                self.key_status.setText("未配置。CAMS AOD 缺失时预测仍可运行（AOD 质量降级）。")

    def _save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "FireSky", "请输入 ADS API Key。")
            return
        config = load_app_config()
        config["ads_api_key"] = key
        save_app_config(config)
        # 同步 ~/.cdsapirc（aerosol.py 的 cdsapi.Client 默认读取）
        lines = [
            f"url: {str(config.get('ads_url') or 'https://ads.atmosphere.copernicus.eu/api')}",
            f"key: {key}",
        ]
        existing = CDSAPIRC.read_text("utf-8") if CDSAPIRC.is_file() else ""
        for line in existing.splitlines():
            if line.strip().lower().startswith("#"):
                lines.append(line)
        try:
            CDSAPIRC.write_text("\n".join(lines) + "\n", "utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "FireSky", f"写 ~/.cdsapirc 失败：{exc}")
            return
        self.key_status.setText("✓ 已保存并同步 ~/.cdsapirc")

    # ── 检查 / 下载 ──
    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _set_status(self, source: str, text: str) -> None:
        label = self.gfs_action_status if source == "gfs" else self.ec_action_status
        label.setText(text)

    def _start_worker(self, worker: WorkerBase, on_done, on_fail, on_progress=None) -> None:
        worker.setParent(self)
        worker.finished_ok.connect(on_done)
        worker.failed.connect(on_fail)
        if on_progress is not None:
            worker.progress.connect(on_progress)
        worker.start()
        self._workers.append(worker)

    def _check_cycles(self, source: str) -> None:
        def done(result: object) -> None:
            self._set_status(source, "检查完成")
            if not isinstance(result, dict):
                return
            datasets = result.get("datasets") or []
            lines = []
            for item in datasets:
                lines.append(
                    f"{item.get('source')} 最新周期 {item.get('latest_cycle')} · "
                    f"需 {item.get('expected_files')} 文件"
                )
            text = " · ".join(lines) or "无可用计划"
            self._log(f"最新周期：{text}")
            if source == "gfs":
                self.gfs_status.setText(text)
            else:
                self.ec_status.setText(text)

        def fail(message: str) -> None:
            self._set_status(source, "检查失败")
            self._log(f"检查最新周期失败：{message}")

        def progress(event: dict) -> None:
            self._set_status(source, str(event.get("message") or "检查中…"))

        from firesky.cache_tasks import REGION_BBOXES  # noqa: F401  (契约参照)

        def task() -> dict:
            from firesky.web_server import handle_data_requirements

            return handle_data_requirements({
                "region": "east-asia",
                "sources": [source],
                "days": 1,
            })

        worker = WorkerBase()
        worker.execute = task  # type: ignore[method-assign]  # 动态任务体
        self._set_status(source, "检查中…")
        self._start_worker(worker, done, fail, progress)

    def _fetch(self, source: str) -> None:
        def done(result: object) -> None:
            self._set_status(source, "下载完成")
            if isinstance(result, dict):
                errors = result.get("errors") or []
                if errors:
                    self._log(f"下载完成（含错误）：{errors[0]}")
                else:
                    self._log(f"下载完成：{result.get('completed', 0)}/{result.get('total', 0)} 文件")
            self._refresh_usage()

        def fail(message: str) -> None:
            self._set_status(source, "下载失败")
            self._log(f"下载失败：{message}")

        def progress(event: dict) -> None:
            percent = int(event.get("percent") or 0)
            self._set_status(
                source,
                f"下载中 {percent}% · {event.get('message', '')}",
            )

        worker = DataFetchWorker({"sources": [source], "days": 1})
        self._log(f"开始下载 {source} 当日时效…")
        self._start_worker(worker, done, fail, progress)

    def _warm_satellite(self) -> None:
        loc = self._current_point_location()
        if loc is None:
            QMessageBox.warning(self, "FireSky", "单点标签尚无观测点。")
            return

        def done(result: object) -> None:
            if isinstance(result, dict):
                status = result.get("status")
                valid = result.get("valid_time_utc") or result.get("reference_time_utc")
                self.sat_status.setText(f"预热结果：{status} · {str(valid)[:16]}Z")
                self._log(f"卫星预热：{status} {valid}")
            self.cache_progress.setVisible(False)

        def fail(message: str) -> None:
            self.sat_status.setText("预热失败（详见日志）")
            self._log(f"卫星预热失败：{message}")
            self.cache_progress.setVisible(False)

        def progress(event: dict) -> None:
            self._log(str(event.get("message") or "预热中…"))

        self.sat_status.setText("预热中（HSD 下载每帧 10–40 秒）…")
        worker = SatelliteWarmupWorker(loc.lat, loc.lon, 200.0)
        self._start_worker(worker, done, fail, progress)

    def _current_point_location(self):
        editor_name = getattr(self._point_tab, "name_edit", None)
        if editor_name is None or not editor_name.text().strip():
            return None
        from desktop.widgets import Location

        return Location(
            name=editor_name.text().strip(),
            lat=float(self._point_tab.lat_spin.value()),
            lon=float(self._point_tab.lon_spin.value()),
            elevation_m=float(self._point_tab.elev_spin.value()),
            timezone_name=self._point_tab.tz_edit.text().strip() or "Asia/Shanghai",
        )

    # ── 缓存 ──
    def _refresh_usage(self) -> None:
        def task() -> dict:
            usage = {}
            total = 0
            for name in ("gfs", "cams", "ecmwf", "himawari-hsd", "point-forecasts",
                         "regional-forecasts", "rainviewer", "map-frames"):
                path = Path(".cache") / name
                if not path.exists():
                    continue
                size = sum(
                    f.stat().st_size for f in path.rglob("*") if f.is_file()
                )
                usage[name] = size
                total += size
            return {"usage": usage, "total": total}

        def done(result: object) -> None:
            self.cache_progress.setVisible(False)
            if not isinstance(result, dict):
                return
            total = int(result.get("total") or 0)
            usage = result.get("usage") or {}
            parts = [
                f"{name} {size / (1024 ** 2):.0f}M"
                for name, size in sorted(usage.items(), key=lambda kv: -kv[1])
            ]
            self.cache_usage.setText(
                f"总占用 {total / (1024 ** 3):.2f} GB（{' · '.join(parts) or '空'}）"
            )

        def fail(message: str) -> None:
            self.cache_progress.setVisible(False)
            self.cache_usage.setText(f"占用统计失败：{message}")

        worker = WorkerBase()
        worker.execute = task  # type: ignore[method-assign]  # 动态任务体
        self.cache_progress.setVisible(True)
        self._start_worker(worker, done, fail)

    def _prune(self) -> None:
        def done(result: object) -> None:
            self.cache_progress.setVisible(False)
            if isinstance(result, dict):
                removed = result.get("removed_files", result.get("files_removed", "?"))
                freed = result.get("freed_bytes")
                freed_text = (
                    f"释放 {float(freed) / (1024 ** 2):.0f}M" if freed else ""
                )
                self._log(f"缓存清理完成：{removed} 文件 {freed_text}")
            self._refresh_usage()

        def fail(message: str) -> None:
            self.cache_progress.setVisible(False)
            self._log(f"缓存清理失败：{message}")

        confirm = QMessageBox.question(
            self, "FireSky", "清理 2 天前的过期缓存文件？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        worker = DataPruneWorker(max_age_days=2)
        self.cache_progress.setVisible(True)
        self._start_worker(worker, done, fail)

    def shutdown(self) -> None:
        for worker in self._workers:
            if worker.isRunning():
                worker.cancel()
                worker.wait(1500)
