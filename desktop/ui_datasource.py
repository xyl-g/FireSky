"""FireSky 桌面应用 · 数据与缓存页（v4 暮光玻璃）。

- CAMS ADS API Key：保存到 ~/.firesky-desktop/config.json，同步 ~/.cdsapirc
- GFS / EC / Himawari：检查最新周期、下载、卫星预热
- 缓存占用显示与清理

卫星预热取 SpotStore 当前观测点（与单点预测、实况观测共享），
对应设计稿中「卫星足迹随观测点变化」。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
from desktop.widgets import GlassCard, SpotStore, load_app_config, save_app_config
from desktop.workers import (
    DataFetchWorker,
    DataPruneWorker,
    SatelliteWarmupWorker,
    WorkerBase,
)

CDSAPIRC = Path.home() / ".cdsapirc"


class DataSourceTab(QWidget):
    """数据源设置与缓存维护。"""

    # 地图 Key / 底图源变化：由主窗口广播给所有已存在的地图实例
    mapSettingsChanged = Signal()

    def __init__(self, spot_store: SpotStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spot_store = spot_store
        self._workers: list[WorkerBase] = []
        self._build_ui()
        self._load_saved_key()
        self._load_map_key()
        self._refresh_usage()
        self._apply_spot(spot_store.location())
        spot_store.changed.connect(self._apply_spot)

    # ── UI ──
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 26, 24)
        root.setSpacing(14)

        head = QVBoxLayout()
        head.setSpacing(4)
        head.addWidget(widgets.role_label(
            "密钥保存在本机 ~/.firesky-desktop/config.json（同步 ~/.cdsapirc），"
            "不写入仓库；文件为明文本机存储（权限 0600）",
            "hint",
        ))
        root.addLayout(head)

        self.gfs_status = widgets.role_label("本地缓存：.cache/gfs", "hint")
        self.ec_status = widgets.role_label("本地缓存：.cache/ecmwf", "hint")
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.addWidget(self._source_card(
            "G", "GFS · NOMADS", "主数值模式 · 公开数据 · 免密钥",
            self._source_actions_row("gfs"), self.gfs_status, "已就绪", "gfs",
        ), 0, 0)
        grid.addWidget(self._source_card(
            "EC", "ECMWF · Open Data", "第二数值模式 · 公开数据 · 免密钥",
            self._source_actions_row("ec"), self.ec_status, "已就绪", "ec",
        ), 0, 1)
        grid.addWidget(self._cams_card(), 1, 0)
        grid.addWidget(self._satellite_card(), 1, 1)
        root.addLayout(grid)

        root.addWidget(self._map_card())

        cache = GlassCard()
        cache_l = QVBoxLayout(cache)
        cache_l.setContentsMargins(17, 15, 17, 15)
        cache_l.setSpacing(10)
        cache_head = QHBoxLayout()
        cache_titles = QVBoxLayout()
        cache_titles.setSpacing(3)
        cache_titles.addWidget(widgets.section_title("本地缓存"))
        cache_titles.addWidget(widgets.role_label(
            "预测数据仅保存在本机 · 自动清理 2 天前的过期文件", "hint"
        ))
        cache_head.addLayout(cache_titles)
        cache_head.addStretch(1)
        self.cache_usage = widgets.role_label("缓存占用统计中…", "h2")
        cache_head.addWidget(self.cache_usage)
        refresh_btn = QPushButton("刷新占用")
        refresh_btn.setObjectName("ghostBtn")
        refresh_btn.clicked.connect(self._refresh_usage)
        prune_btn = QPushButton("清理过期缓存")
        prune_btn.setObjectName("ghostBtn")
        prune_btn.clicked.connect(self._prune)
        cache_head.addWidget(refresh_btn)
        cache_head.addWidget(prune_btn)
        cache_l.addLayout(cache_head)
        self.cache_progress = QProgressBar()
        self.cache_progress.setRange(0, 0)
        self.cache_progress.setVisible(False)
        cache_l.addWidget(self.cache_progress)
        root.addWidget(cache)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(96)
        self.log_view.setVisible(False)
        root.addWidget(self.log_view)
        root.addStretch(1)
        self._log("数据源操作走 web_server.handle_data_requirements / handle_data_fetch / handle_data_prune。")

    def _source_card(self, badge: str, name: str, desc: str, actions: QWidget,
                     status: QLabel, state: str, _key: str) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(11)
        icon = widgets.role_label(badge, "h2")
        icon.setFixedSize(38, 38)
        icon.setStyleSheet(
            f"background: {theme.rgba('accent', alpha=28)};"
            f" color: {theme.palette()['accent']}; border-radius: 12px;"
            " font-size: 12px; font-weight: 700; qproperty-alignment: AlignCenter;"
        )
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title(name))
        titles.addWidget(widgets.role_label(desc, "hint"))
        head.addLayout(titles, 1)
        state_chip = widgets.chip("●  " + state, "ok")
        head.addWidget(state_chip)
        layout.addLayout(head)
        layout.addWidget(actions)
        layout.addWidget(status)
        return card

    def _cams_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(11)
        icon = widgets.role_label("C", "h2")
        icon.setFixedSize(38, 38)
        icon.setStyleSheet(
            f"background: {theme.rgba('accent', alpha=28)};"
            f" color: {theme.palette()['accent']}; border-radius: 12px;"
            " font-size: 12px; font-weight: 700; qproperty-alignment: AlignCenter;"
        )
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("CAMS · Copernicus ADS"))
        titles.addWidget(widgets.role_label("气溶胶光学厚度 · 决定霞色与通透度", "hint"))
        head.addLayout(titles, 1)
        head.addWidget(widgets.chip("需要密钥", "accent"))
        layout.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("ADS API Key")
        row.addWidget(self.key_edit, 1)
        self.key_show = QPushButton("显示")
        self.key_show.setObjectName("ghostBtn")
        self.key_show.setCheckable(True)
        self.key_show.toggled.connect(self._toggle_key_visibility)
        row.addWidget(self.key_show)
        save_key_btn = QPushButton("保存 Key")
        save_key_btn.setObjectName("ghostBtn")
        save_key_btn.clicked.connect(self._save_key)
        row.addWidget(save_key_btn)
        layout.addLayout(row)
        self.key_status = widgets.role_label("", "hint")
        layout.addWidget(self.key_status)
        return card

    def _map_card(self) -> GlassCard:
        """地图底图服务：天地图 Key + 默认底图源。

        天地图 Key 只写入本机配置，绝不进仓库；保存后立即推给所有地图
        实例重新注入（无需重启）。Esri 免 Key，仅限中国境外使用。
        """
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(11)
        icon = widgets.role_label("M", "h2")
        icon.setFixedSize(38, 38)
        icon.setStyleSheet(
            f"background: {theme.rgba('accent', alpha=28)};"
            f" color: {theme.palette()['accent']}; border-radius: 12px;"
            " font-size: 12px; font-weight: 700; qproperty-alignment: AlignCenter;"
        )
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("地图服务 · 底图选点"))
        titles.addWidget(widgets.role_label(
            "天地图：境内合规底图（需自有 Key）· Esri：境外免 Key", "hint"
        ))
        head.addLayout(titles, 1)
        head.addWidget(widgets.chip("仅用于选点", "ok"))
        layout.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.map_key_edit = QLineEdit()
        self.map_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.map_key_edit.setPlaceholderText("天地图 Key（console.tianditu.gov.cn 申请「浏览器端」应用）")
        self.map_key_edit.returnPressed.connect(self._save_map_key)
        row.addWidget(self.map_key_edit, 1)

        show_btn = QPushButton("显示")
        show_btn.setObjectName("ghostBtn")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(self._toggle_map_key_visibility)
        row.addWidget(show_btn)

        save_btn = QPushButton("保存并刷新地图")
        save_btn.setObjectName("ghostBtn")
        save_btn.clicked.connect(self._save_map_key)
        row.addWidget(save_btn)
        layout.addLayout(row)

        self.map_key_status = widgets.role_label("", "hint")
        layout.addWidget(self.map_key_status)

        src_row = QHBoxLayout()
        src_row.setSpacing(10)
        src_row.addWidget(widgets.role_label("默认底图源", "hint"))
        self.map_source_seg = widgets.SegmentedControl(["天地图", "Esri"])
        self.map_source_seg.setFixedWidth(176)
        self.map_source_seg.valueChanged.connect(self._on_map_source)
        src_row.addWidget(self.map_source_seg)
        self.map_source_hint = widgets.role_label("", "hint")
        src_row.addWidget(self.map_source_hint, 1)
        layout.addLayout(src_row)
        return card

    def _toggle_map_key_visibility(self, checked: bool) -> None:
        self.map_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _load_map_key(self) -> None:
        from desktop import web_map

        key = web_map.tianditu_key()
        if key:
            self.map_key_edit.setText(key)
        source = web_map.tianditu_key_source()
        if source:
            self.map_key_status.setText(f"✓ 已配置 · 来自{source}")
        else:
            self.map_key_status.setText(
                "未配置。地图会显示申请引导；也可先用 Esri 底图（仅限境外）。"
            )
        self.map_source_seg.blockSignals(True)
        self.map_source_seg.set_index(1 if web_map.map_source() == "esri" else 0,
                                     emit=False)
        self.map_source_seg.blockSignals(False)
        self._refresh_map_source_hint()

    def _refresh_map_source_hint(self) -> None:
        if self.map_source_seg.index() == 1:
            self.map_source_hint.setText(
                "境外使用；Esri 境内无审图号，境内请切回天地图。"
            )
        else:
            self.map_source_hint.setText(
                "境内默认；影像 / 矢量 / 地形可选，境外仅影像可用。"
            )

    def _on_map_source(self, index: int, _label: str) -> None:
        from desktop import web_map

        web_map.set_map_source("esri" if index == 1 else "tianditu")
        self._refresh_map_source_hint()
        self._log("地图底图源已切换，所有地图实例重新加载。")
        self.mapSettingsChanged.emit()

    def _save_map_key(self) -> None:
        from desktop import web_map

        raw = self.map_key_edit.text()
        saved = web_map.set_tianditu_key(raw)
        effective = web_map.tianditu_key()
        if raw.strip() and not saved and not effective:
            # Key 格式不合法且被拒绝保存：提示而不是静默「已保存」。
            self.map_key_status.setText(
                "✗ Key 格式不合法：天地图 Key 是 16–64 位十六进制串；"
                "已保留原 Key。请到 console.tianditu.gov.cn 检查。"
            )
            return
        if effective:
            self.map_key_edit.setText(effective)
            source = web_map.tianditu_key_source() or "本机配置"
            self.map_key_status.setText(f"✓ 已保存并生效 · 来自{source}")
        else:
            self.map_key_status.setText(
                "已清除本机 Key。地图会回到「未配置」引导，可先用 Esri 底图。"
            )
        self._log("天地图 Key 已更新，地图实例重新注入。")
        self.mapSettingsChanged.emit()

    def focus_map_key(self) -> None:
        """从地图浮层「配置 Key」跳过来时定位输入框。"""
        self.map_key_edit.setFocus()
        self.map_key_edit.selectAll()

    def _satellite_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(11)
        icon = widgets.role_label("H9", "h2")
        icon.setFixedSize(38, 38)
        icon.setStyleSheet(
            f"background: {theme.rgba('accent', alpha=28)};"
            f" color: {theme.palette()['accent']}; border-radius: 12px;"
            " font-size: 12px; font-weight: 700; qproperty-alignment: AlignCenter;"
        )
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("Himawari 9 · 卫星实况"))
        self._sat_spot_label = widgets.role_label(
            "当前观测点 · 900 km 足迹（与实况评分口径一致）", "hint"
        )
        titles.addWidget(self._sat_spot_label)
        head.addLayout(titles, 1)
        head.addWidget(widgets.chip("免密钥", "ok"))
        layout.addLayout(head)

        warm_btn = QPushButton("预热最新扫描")
        warm_btn.setObjectName("ghostBtn")
        warm_btn.clicked.connect(self._warm_satellite)
        layout.addWidget(warm_btn)
        self.sat_status = widgets.role_label(
            "对当前观测点执行 handle_satellite_cache_warmup。", "hint"
        )
        self.sat_status.setWordWrap(True)
        layout.addWidget(self.sat_status)
        return card

    def _source_actions_row(self, source: str) -> QWidget:
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(8)
        check_btn = QPushButton("检查最新周期")
        check_btn.setObjectName("ghostBtn")
        check_btn.clicked.connect(lambda: self._check_cycles(source))
        fetch_btn = QPushButton("下载当日时效")
        fetch_btn.setObjectName("ghostBtn")
        fetch_btn.clicked.connect(lambda: self._fetch(source))
        status_label = widgets.role_label("", "hint")
        row_l.addWidget(check_btn)
        row_l.addWidget(fetch_btn)
        row_l.addWidget(status_label, 1)
        if source == "gfs":
            self.gfs_action_status = status_label
        else:
            self.ec_action_status = status_label
        return row

    # ── 共享观测点 ──
    def _apply_spot(self, loc) -> None:
        self._sat_spot_label.setText(
            f"{loc.name} · 900 km 足迹（与实况评分口径一致）"
        )

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
        elif CDSAPIRC.is_file():
            self.key_status.setText("检测到已有 ~/.cdsapirc；可保存 Key 以同步桌面端配置。")
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
        # 只替换 url/key 两行，其余行（verify 等用户既有配置）原样保留：
        # cdsapi 会读取 verify 控制证书校验，静默丢弃会改变用户既有行为。
        url_line = (
            f"url: {str(config.get('ads_url') or 'https://ads.atmosphere.copernicus.eu/api')}"
        )
        key_line = f"key: {key}"
        existing = CDSAPIRC.read_text("utf-8") if CDSAPIRC.is_file() else ""
        lines: list[str] = []
        for line in existing.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("url:") or stripped.startswith("key:"):
                continue
            lines.append(line)
        lines.insert(0, url_line)
        lines.insert(1, key_line)
        try:
            # Key 是付费账号凭据：落盘权限收紧到仅本用户可读写。
            CDSAPIRC.write_text("\n".join(lines) + "\n", "utf-8")
            CDSAPIRC.chmod(0o600)
        except OSError as exc:
            QMessageBox.warning(self, "FireSky", f"写 ~/.cdsapirc 失败：{exc}")
            return
        self.key_status.setText(
            "✓ 已保存并同步 ~/.cdsapirc（明文存本机，权限 0600）"
        )

    # ── 检查 / 下载 ──
    def _log(self, message: str) -> None:
        # 日志面板默认隐藏：首次写入（尤其是失败详情）时自动展开，
        # 否则「检查失败/下载失败」的排障信息用户永远看不到。
        if not self.log_view.isVisible():
            self.log_view.setVisible(True)
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
            lines = [
                f"{item.get('source')} 最新周期 {item.get('latest_cycle')} · "
                f"需 {item.get('expected_files')} 文件"
                for item in datasets
            ]
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

        def task() -> dict:
            from firesky.web_server import handle_data_requirements

            return handle_data_requirements({
                "region": "east-asia", "sources": [source], "days": 1,
            })

        worker = WorkerBase()
        worker.execute = task  # type: ignore[method-assign]
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
            self._set_status(source, f"下载中 {percent}% · {event.get('message', '')}")

        worker = DataFetchWorker({"sources": [source], "days": 1})
        self._log(f"开始下载 {source} 当日时效…")
        self._start_worker(worker, done, fail, progress)

    def _warm_satellite(self) -> None:
        loc = self._current_point_location()
        if loc is None:
            QMessageBox.warning(self, "FireSky", "当前尚无观测点。")
            return

        def done(result: object) -> None:
            if isinstance(result, dict):
                status = result.get("status")
                valid = result.get("valid_time_utc") or result.get("reference_time_utc")
                radius = result.get("radius_km")
                self.sat_status.setText(
                    f"预热结果：{status} · {str(valid)[:16]}Z · 半径 {radius or '?'} km"
                )
                self._log(f"卫星预热：{status} {valid}")
            self.cache_progress.setVisible(False)

        def fail(message: str) -> None:
            self.sat_status.setText("预热失败（详见日志）")
            self._log(f"卫星预热失败：{message}")
            self.cache_progress.setVisible(False)

        def progress(event: dict) -> None:
            self._log(str(event.get("message") or "预热中…"))

        self.sat_status.setText("预热中（HSD 下载每帧 10–40 秒）…")
        # 半径必须与实况评分读缓存的默认搜索半径一致（900 km），否则写出的
        # HSD 紧凑缓存键与读取键不匹配，预热对实况 fail-closed 无效。
        worker = SatelliteWarmupWorker(loc.lat, loc.lon, 900.0)
        self._start_worker(worker, done, fail, progress)

    def _current_point_location(self):
        return self._spot_store.location()

    # ── 缓存 ──
    def _usage_task_dirs(self) -> list[str]:
        # 占用统计目录：加上引擎的 .trash 暂存区，保证「清理后占用下降」
        # 与磁盘实际占用一致。
        return ("gfs", "cams", "ecmwf", "himawari-hsd", "point-forecasts",
                "regional-forecasts", "rainviewer", "map-frames", ".trash")

    def _refresh_usage(self) -> None:
        def task() -> dict:
            usage = {}
            total = 0
            for name in self._usage_task_dirs():
                path = Path(".cache") / name
                if not path.exists():
                    continue
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
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
                f"{total / (1024 ** 3):.2f} GB  ·  {' · '.join(parts[:3]) or '缓存为空'}"
            )

        def fail(message: str) -> None:
            self.cache_progress.setVisible(False)
            self.cache_usage.setText(f"占用统计失败：{message}")

        worker = WorkerBase()
        worker.execute = task  # type: ignore[method-assign]
        self.cache_progress.setVisible(True)
        self._start_worker(worker, done, fail)

    def _prune(self) -> None:
        def done(result: object) -> None:
            self.cache_progress.setVisible(False)
            if isinstance(result, dict):
                removed = result.get("removed")
                count = len(removed) if isinstance(removed, list) else 0
                # 引擎把过期文件移动到 .cache/.trash 暂存（防误删策略），
                # 磁盘真正回收需要手动清空该目录，提示里说明去向。
                trash_text = ""
                trash_root = Path(".cache") / ".trash"
                if trash_root.exists():
                    try:
                        trash_bytes = sum(
                            f.stat().st_size
                            for f in trash_root.rglob("*") if f.is_file()
                        )
                    except OSError:
                        trash_bytes = 0
                    trash_text = f" · 暂存于 .cache/.trash（{trash_bytes / (1024 ** 3):.2f} GB，" \
                                 "确认无误后可手动删除该目录回收磁盘）"
                self._log(f"缓存清理完成：移入暂存 {count} 个文件{trash_text}")
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

    def on_theme_changed(self) -> None:
        for card in self.findChildren(GlassCard):
            card.update()

    def shutdown(self) -> None:
        for worker in self._workers:
            if worker.isRunning():
                worker.cancel()
                # 下载/清理线程的 urlopen 无法中断，等它退出再析构窗口：
                # 运行中的 QThread 被 GC 会让 Qt abort 进程。
                worker.wait(300000)
