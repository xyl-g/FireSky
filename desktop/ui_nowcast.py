"""FireSky 桌面应用 · 实况观测页（v4 暮光玻璃）。

与单点预测共享 SpotStore 观测点：本页不再维护独立的地点列表，点位条
显示当前观测点并提供「地图选点」入口（与单点预测唤起同一个浮层）。

预测仍走 web_server.nowcast_payload + run_forecast_payload 权威路径；
新鲜卫星帧缺失时引擎 fail-closed，错误展示在日志，属预期行为。
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.data_panel import ForecastDataPanel
from desktop.profile_canvas import SatelliteProfileCanvas
from desktop.ui_point import theme_and_level
from desktop.widgets import (
    GlassCard,
    Location,
    MetricCard,
    MiniMap,
    QualityCurve,
    RunButton,
    SpotStore,
    fmt,
)
from desktop.workers import ForecastWorker


class NowcastTab(QWidget):
    """实况 nowcast 工作台（共享观测点）。"""

    def __init__(self, spot_store: SpotStore, on_pick_map=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = spot_store
        self._on_pick_map = on_pick_map
        self._worker: ForecastWorker | None = None
        self._last_context = None
        self._request_context = None
        self._image_available = False
        self._build_ui()
        self.date_edit.dateChanged.connect(self._update_context_state)
        self._apply_spot(self._store.location())
        self._store.changed.connect(self._on_spot_changed)

    # ── UI ──
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 26, 24)
        root.setSpacing(14)
        root.addWidget(self._build_spot_strip())
        root.addWidget(self._build_filter_bar())
        self.data_panel = ForecastDataPanel(self, nowcast=True)
        self.data_panel.bind(self._payload_for_data)
        self.data_panel.busyChanged.connect(self._on_data_busy)
        root.addWidget(self.data_panel)
        root.addWidget(self.log_view)
        root.addLayout(self._build_metrics())

        middle = QHBoxLayout()
        middle.setSpacing(14)
        chart = GlassCard()
        chart_l = QVBoxLayout(chart)
        chart_l.setContentsMargins(17, 15, 17, 12)
        chart_l.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("实况质量时间轴"))
        self._chart_sub = widgets.role_label("事件前后 30 分钟 · 5 分钟步长", "hint")
        titles.addWidget(self._chart_sub)
        head.addLayout(titles)
        head.addStretch(1)
        head.addWidget(widgets.chip("NOWCAST", "accent"))
        chart_l.addLayout(head)
        self.curve = QualityCurve(190)
        chart_l.addWidget(self.curve)
        middle.addWidget(chart, 1)

        # 雷达与数据源信息卡（与台账并列，字段缺失显示 —）
        middle.addWidget(self._build_radar_card(), 1)
        root.addLayout(middle)

        # 卫星剖面（真数据自绘）：位于四指标卡与台账之间
        profile = GlassCard()
        profile_l = QVBoxLayout(profile)
        profile_l.setContentsMargins(17, 15, 17, 12)
        profile_l.setSpacing(8)
        profile_head = QHBoxLayout()
        profile_titles = QVBoxLayout()
        profile_titles.setSpacing(3)
        profile_titles.addWidget(widgets.section_title("太阳方向实况大气剖面"))
        self._profile_sub = widgets.role_label("校准卫星云柱 + 同周期 GFS 垂直先验", "hint")
        profile_titles.addWidget(self._profile_sub)
        profile_head.addLayout(profile_titles)
        profile_head.addStretch(1)
        self._profile_chip = widgets.chip("等待实况刷新")
        profile_head.addWidget(self._profile_chip)
        profile_l.addLayout(profile_head)
        self._profile = SatelliteProfileCanvas()
        profile_l.addWidget(self._profile)
        root.addWidget(profile)

        table_card = GlassCard()
        table_l = QVBoxLayout(table_card)
        table_l.setContentsMargins(0, 0, 0, 6)
        table_l.setSpacing(0)
        t_head = QHBoxLayout()
        t_head.setContentsMargins(17, 14, 17, 10)
        t_titles = QVBoxLayout()
        t_titles.setSpacing(3)
        t_titles.addWidget(widgets.section_title("实况台账"))
        t_titles.addWidget(widgets.role_label("卫星反演与雷达约束明细", "hint"))
        t_head.addLayout(t_titles)
        t_head.addStretch(1)
        self.meta_label = widgets.role_label("卫星观测时间 · 覆盖率在获取后显示", "hint")
        t_head.addWidget(self.meta_label)
        table_l.addLayout(t_head)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["时刻", "质量", "档位", "可烧", "雷达约束", "云相", "太阳仰角"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(140)
        table_l.addWidget(self.table, 1)
        root.addWidget(table_card)
        root.addStretch(1)

    def _build_spot_strip(self) -> GlassCard:
        strip = GlassCard()
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(13, 11, 13, 11)
        layout.setSpacing(13)

        self._strip_map = MiniMap(compact=True, interactive=False)
        self._strip_map.setFixedSize(104, 66)
        self._strip_map.setCursor(Qt.CursorShape.PointingHandCursor)
        self._strip_map.clicked.connect(self._open_map_picker)
        layout.addWidget(self._strip_map)

        text = QVBoxLayout()
        text.setSpacing(5)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self._strip_title = widgets.role_label("杭州 · 实况观测", "h2")
        title_row.addWidget(self._strip_title)
        title_row.addWidget(widgets.chip("●  与单点预测共用点位", "ok"))
        title_row.addStretch(1)
        text.addLayout(title_row)
        self._strip_sub = widgets.role_label("", "hint")
        text.addWidget(self._strip_sub)
        layout.addLayout(text, 1)

        self._satellite_badge = widgets.chip("卫星帧待获取")
        self._satellite_badge.setFixedHeight(26)
        layout.addWidget(self._satellite_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        pick = QPushButton("⌖  地图选点")
        pick.setObjectName("pickBtn")
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.clicked.connect(self._open_map_picker)
        layout.addWidget(pick)
        self.current_location_button = QPushButton("获取当前位置")
        self.current_location_button.setObjectName("ghostBtn")
        self.current_location_button.clicked.connect(self._request_current_location)
        layout.addWidget(self.current_location_button)
        return strip

    def _build_filter_bar(self) -> GlassCard:
        bar = GlassCard()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(10)
        layout.addWidget(widgets.role_label("现象", "ink3"))
        self.dawn_btn = QPushButton("朝霞")
        self.dusk_btn = QPushButton("晚霞")
        for button in (self.dawn_btn, self.dusk_btn):
            button.setObjectName("chipBtn")
            button.setCheckable(True)
        self.dusk_btn.setChecked(True)
        self.dusk_btn.setProperty("active", "true")
        self.dawn_btn.setProperty("active", "false")
        from PySide6.QtWidgets import QButtonGroup
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.dawn_btn)
        group.addButton(self.dusk_btn)
        self.dawn_btn.clicked.connect(lambda: self._on_phenomenon(0))
        self.dusk_btn.clicked.connect(lambda: self._on_phenomenon(1))
        layout.addWidget(self.dawn_btn)
        layout.addWidget(self.dusk_btn)
        layout.addSpacing(8)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(datetime.now().date())
        self.date_edit.setFixedWidth(130)
        layout.addWidget(self.date_edit)
        layout.addStretch(1)
        self.run_button = RunButton("刷新实况")
        self.run_button.setFixedHeight(32)
        self.run_button.clicked.connect(self._run)
        layout.addWidget(self.run_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("ghostBtn")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress.setFixedWidth(90)
        layout.addWidget(self.progress)
        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(70)
        self.log_view.setVisible(False)
        return bar

    def _build_metrics(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.metric_cards: dict[str, MetricCard] = {}
        self.metric_subs: dict[str, MetricCard] = {}
        for key, title, sub in (
            ("nowcast_peak", "实况峰值", "运行刷新后显示"),
            ("convective_score_ceiling", "对流顶棚", "雷达约束上限"),
            ("calibrated_sample_coverage_ratio", "采样覆盖率", "校准样本"),
            ("geometric_path_blocking_risk", "光路阻塞风险", "太阳入射路径"),
        ):
            card = MetricCard(title)
            card.set_sub(sub)
            row.addWidget(card, 1)
            self.metric_cards[key] = card
            self.metric_subs[key] = card
        return row

    def _build_radar_card(self) -> GlassCard:
        """雷达与数据源信息卡：radar_* 字段只读展示（缺失显示 —）。"""
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("雷达与数据源"))
        titles.addWidget(widgets.role_label("天气雷达约束与实况数据来源明细", "hint"))
        head.addLayout(titles)
        head.addStretch(1)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(5)
        self._radar_rows: dict[str, QLabel] = {}
        items = (
            ("radar_source", "雷达数据源"),
            ("radar_valid_time_utc", "雷达观测时间"),
            ("radar_max_dbz", "最大回波 dBZ"),
            ("radar_local_precip_ratio", "本地降水比例"),
            ("radar_path_precip_ratio", "光路降水比例"),
            ("radar_reliability_score", "雷达可靠度"),
            ("radar_convective_ratio", "对流比例"),
        )
        for index, (key, title) in enumerate(items):
            row, col = index % 4, index // 4
            value = QLabel("—")
            value.setProperty("role", "ink2")
            grid.addWidget(widgets.role_label(title, "ink3"), row, col * 2)
            grid.addWidget(value, row, col * 2 + 1)
            self._radar_rows[key] = value
        layout.addLayout(grid)
        layout.addStretch(1)
        return card

    def _render_radar_card(self, nowcast: dict) -> None:
        """雷达字段：缺失一律显示 —，不臆造口径。"""

        def text_for(key: str) -> str:
            value = nowcast.get(key)
            if value is None or value == "":
                return "—"
            if key == "radar_max_dbz":
                return f"{float(value):.1f} dBZ"
            if key in ("radar_local_precip_ratio", "radar_path_precip_ratio",
                       "radar_convective_ratio", "radar_reliability_score"):
                try:
                    return f"{float(value) * 100:.0f}%"
                except (TypeError, ValueError):
                    return str(value)
            if key == "radar_valid_time_utc":
                return f"{str(value)[:16]}Z"
            return str(value)

        for key, label in self._radar_rows.items():
            label.setText(text_for(key))

    # ── 共享观测点 ──
    def _apply_spot(self, loc: Location) -> None:
        self._strip_title.setText(f"{loc.name} · 实况观测")
        self._strip_sub.setText(
            f"{loc.coord_text()} · 海拔 {widgets.format_elevation_m(loc.elevation_m)} · "
            f"{loc.utc_offset_label().replace('时区 ', '')} · "
            "向日葵 9 号"
        )
        if self._last_context is None:
            self._chart_sub.setText(f"{loc.name} · 事件前后 30 分钟 · 5 分钟步长")
            self._profile_sub.setText(f"卫星云柱 + GFS 垂直先验 · {loc.name} 上空")
        self._strip_map.set_location(loc)
        self._update_context_state()

    def _context(self) -> tuple:
        loc = self._store.location()
        return (loc.name, loc.lat, loc.lon, loc.elevation_m, loc.timezone_name,
                self.date_edit.date().toString("yyyy-MM-dd"),
                "dawn" if self.dawn_btn.isChecked() else "dusk")

    def _update_context_state(self, *_args) -> None:
        current = self._context()
        if self._worker is not None:
            stale = self._request_context != current
            text, tone = ("正在刷新旧参数", "warn") if stale else ("正在刷新", "accent")
        elif self._last_context is not None and current != self._last_context:
            text, tone = "参数已变更 · 请刷新", "warn"
        elif self._last_context is not None:
            text, tone = ("卫星帧已就绪", "ok") if self._image_available else ("无卫星帧数据", "warn")
        else:
            text, tone = "卫星帧待获取", ""
        self._satellite_badge.setText(text)
        self._satellite_badge.setProperty("tone", tone)
        theme.repolish(self._satellite_badge)

    def _on_spot_changed(self, loc: Location) -> None:
        self._apply_spot(loc)
        self._log(f"观测点已同步：{loc.name} {loc.coord_text()}")

    def _open_map_picker(self) -> None:
        if self._on_pick_map is not None:
            self._on_pick_map()

    def _on_phenomenon(self, index: int) -> None:
        for i, button in enumerate((self.dawn_btn, self.dusk_btn)):
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)
        self._update_context_state()

    # ── 预测 ──
    def _request_current_location(self):
        window = self.window()
        if hasattr(window, "request_current_location"):
            window.request_current_location()

    def _on_data_busy(self, busy):
        self.run_button.setEnabled(not busy and self._worker is None)
        self.cancel_button.setVisible(busy or self._worker is not None)
        self.cancel_button.setEnabled(True)

    def _payload_for_data(self):
        loc = self._store.location()
        return {
            "name": loc.name, "lat": loc.lat, "lon": loc.lon,
            "elevation_m": loc.elevation_m, "timezone_name": loc.timezone_name,
            "target_date": self.date_edit.date().toPython().isoformat(),
            "phenomenon": "dawn" if self.dawn_btn.isChecked() else "dusk", "workers": 1,
        }

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        loc = self._store.location()
        target = self.date_edit.date().toPython()
        if not isinstance(target, date_type):
            QMessageBox.warning(self, "FireSky", "事件日期无效。")
            return
        phenomenon = "dawn" if self.dawn_btn.isChecked() else "dusk"
        # 海拔哨兵拦截：内嵌小地图落点后 DEM 尚未回填（=-1）时不提交，
        # 否则 -1 会被引擎当真实海拔参与辐射/云底计算。
        if float(loc.elevation_m) <= widgets.Location.UNQUERIED_ELEVATION_M:
            QMessageBox.warning(
                self, "FireSky",
                f"观测点「{loc.name}」的海拔尚未查询完成（DEM 异步获取中）。\n"
                "请稍候几秒后重试，或在单点页手动填写海拔。")
            return
        payload = {
            "name": loc.name,
            "lat": loc.lat,
            "lon": loc.lon,
            "elevation_m": loc.elevation_m,
            "timezone_name": loc.timezone_name,
            "target_date": target.isoformat(),
            "phenomenon": phenomenon,
            "workers": 1,
        }
        self._log(f"创建实况任务：{loc.name} {target} {phenomenon}（卫星实况）")
        context = self._context()
        self.data_panel.begin(
            payload, callback=self._start_forecast,
            is_current=lambda: self._context() == context,
        )

    def _start_forecast(self, payload):
        self._request_context = self._context()
        self._worker = ForecastWorker(payload, nowcast=True)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(2)
        self._update_context_state()
        self._worker.start()

    def _cancel(self) -> None:
        self.data_panel.cancel()
        if self._worker is not None:
            self._worker.cancel()
            self._log("已请求取消（引擎将在下一个进度检查点停止；取消后本次结果将被忽略）。")
            self.progress.setToolTip("取消中…")

    def _on_progress(self, event: dict) -> None:
        raw_percent = event.get("percent")
        percent = int(raw_percent) if raw_percent is not None else None
        message = str(event.get("message") or "")
        if percent is not None:
            self.progress.setValue(max(2, min(100, percent)))
        if message:
            self._log(message)
            self.progress.setToolTip(message)

    def _on_cancelled(self) -> None:
        """取消落地：复位控件（不弹窗、不当失败处理）。"""
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self._update_context_state()
        self._log("实况刷新已取消。")

    def _on_done(self, result: object) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(100)
        self.progress.setVisible(False)
        if not isinstance(result, dict):
            self._on_failed("实况结果格式异常。")
            return
        self._last_context = self._request_context
        self.render_result(result)
        self._log("实况完成。")

    def render_result(self, result: dict) -> None:
        nowcast = result.get("nowcast") or {}
        level = str(result.get("quality_level") or "无明显火烧云")
        peak = float(result.get("peak_quality_index") or 0.0)
        color = theme.level_color(level)

        peak_card = self.metric_cards["nowcast_peak"]
        peak_card.set_value(f"{peak:.2f}", color)
        best = widgets.local_time_label(result.get("best_time_local"))
        peak_card.set_sub(f"{level} · {best}")
        self._image_available = bool(nowcast.get("image_valid_time_utc"))
        if self._last_context is None:
            self._last_context = self._context()
        context = self._last_context
        caption = f"{context[0]} · {context[5]} {'朝霞' if context[6] == 'dawn' else '晚霞'}"
        self._chart_sub.setText(caption)
        self._profile_sub.setText(f"卫星云柱 + GFS 垂直先验 · {caption}")
        self._update_context_state()

        ceiling = nowcast.get("convective_score_ceiling")
        ceiling_card = self.metric_cards["convective_score_ceiling"]
        if ceiling is None:
            ceiling_card.set_value("—")
        else:
            value = float(ceiling)
            ceiling_card.set_value(f"{value * 100:.0f}" if value <= 1.0 else f"{value:.0f}")
        radar_risk = nowcast.get("radar_convective_risk")
        ceiling_card.set_sub(
            (f"雷达回波风险 {float(radar_risk):.0%} · " if radar_risk is not None else "")
            + "对流风险对评分上限的约束"
        )

        coverage = nowcast.get("calibrated_sample_coverage_ratio")
        coverage_card = self.metric_cards["calibrated_sample_coverage_ratio"]
        if coverage is None:
            coverage_card.set_value("—")
            coverage_card.set_sub("无校准采样数据")
        else:
            ratio = float(coverage)
            # 阈值与引擎对齐：MIN_NOWCAST_CALIBRATED_SAMPLE_COVERAGE=0.80
            # 是校准样本覆盖门槛，低于它评分会被压上限。
            coverage_card.set_value(
                f"{ratio:.0%}",
                theme.palette()["ok"] if ratio >= 0.8 else theme.palette()["warn"],
            )
            coverage_card.set_sub("样本覆盖良好" if ratio >= 0.8
                                  else "覆盖不足（引擎门槛 80%）· 置信度受限")

        blocking = nowcast.get(
            "geometric_path_blocking_risk",
            nowcast.get("effective_path_blocking_risk"),
        )
        blocking_card = self.metric_cards["geometric_path_blocking_risk"]
        samples = nowcast.get("geometric_path_samples")
        if blocking is None:
            blocking_card.set_value("—")
            blocking_card.set_sub("无光路几何样本")
        else:
            risk = float(blocking)
            # 阈值与引擎对齐：path_blocking_risk ≥ 0.62 触发物理可烧否决，
            # 0.18–0.72 为评分 ramp 区间；0.35 只是「尚无约束」的参考位。
            blocking_card.set_value(
                f"{risk * 100:.0f} / 100",
                theme.palette()["ok"] if risk < 0.35 else theme.palette()["warn"],
            )
            blocking_card.set_sub(
                f"{samples or 0} 样本 · "
                + ("挡光风险低" if risk < 0.35
                   else "注意光路遮挡（≥62 会否决可烧）")
            )

        image_time = nowcast.get("image_valid_time_utc")
        source = nowcast.get("source")
        # 缺时间戳时不输出孤零零的「Z」。
        stamp_text = f"{str(image_time)[:16]}Z" if image_time else "—"
        self.meta_label.setText(
            f"数据源 {source or '—'} · 卫星观测 "
            f"{stamp_text} · 覆盖率 {fmt(coverage, '{:.0%}')}"
        )

        steps = result.get("steps") or []
        valid_pairs = [
            (i, float(s.get("instant_quality_index") or 0.0))
            for i, s in enumerate(steps) if s.get("valid", True)
        ]
        best_index = max(valid_pairs, key=lambda pair: pair[1])[0] if valid_pairs else None
        self.curve.set_steps([
            (
                widgets.local_time_label(s.get("valid_time_local")),
                float(s.get("instant_quality_index") or 0.0),
                bool(s.get("valid", True)),
            )
            for s in steps
        ])

        ceiling_text = (
            f"{float(ceiling) * 100:.0f}" if ceiling is not None and float(ceiling) <= 1.0
            else (f"{float(ceiling):.0f}" if ceiling is not None else "—")
        )
        radar_constraint = f"上限 {ceiling_text}/100"
        # 引擎的逐 5 分钟 steps 不含 cloud_phase_proxy（只有顶层
        # dominant_burnable_cloud_phase）：用顶层相态标注整段台账，缺失显示 —。
        top_phase = result.get("dominant_burnable_cloud_phase")
        top_phase_label = (
            top_phase.get("label")
            if isinstance(top_phase, dict)
            else (top_phase if isinstance(top_phase, str) else None)
        )
        phase_cn_top = {
            "ice": "冰晶", "water": "过冷水", "mixed": "冰水混合", "unknown": "—",
        }.get(str(top_phase_label or ""), str(top_phase_label or ""))
        phase_seen = ""
        self.table.setRowCount(0)
        for index, s in enumerate(steps):
            if not s.get("valid", True):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            instant = float(s.get("instant_quality_index") or 0.0)
            step_level = str(s.get("instant_quality_level") or theme_and_level(instant))
            row_color = QColor(theme.level_color(step_level))
            stamp = widgets.local_time_label(s.get("valid_time_local"))
            if index == best_index:
                stamp = f"▶ {stamp}（峰值）"
            phase = s.get("cloud_phase_proxy")
            phase_label = phase.get("label") if isinstance(phase, dict) else phase
            phase_cn = {
                "ice": "冰晶", "water": "过冷水", "mixed": "冰水混合", "unknown": "—",
            }.get(str(phase_label or ""), str(phase_label or "—"))
            if phase_cn == "—" and phase_cn_top:
                phase_cn = phase_cn_top
            if phase_cn != "—" and not phase_seen:
                phase_seen = phase_cn
            row_items = (
                QTableWidgetItem(stamp), QTableWidgetItem(f"{instant:.3f}"),
                QTableWidgetItem(step_level),
                QTableWidgetItem("✓" if s.get("physically_burnable") else "×"),
                QTableWidgetItem(radar_constraint), QTableWidgetItem(phase_cn),
                QTableWidgetItem(fmt(s.get("solar_elevation_deg"), "{:.1f}°")),
            )
            for col, cell in enumerate(row_items):
                if col in (1, 2):
                    cell.setForeground(row_color)
                self.table.setItem(row, col, cell)

        # 卫星剖面 + 雷达与数据源卡（nowcast dict 缺字段时 Canvas/卡片自行显示 —/占位）
        self._profile.set_data(nowcast.get("satellite_vertical_profile"))
        self._render_radar_card(nowcast)
        profile_status = nowcast.get("satellite_vertical_profile")
        if isinstance(profile_status, dict) and profile_status.get("samples"):
            status_text = str(profile_status.get("status") or "")
            self._profile_chip.setText(
                "剖面完整" if status_text == "available"
                else "剖面部分未知" if status_text == "partial_unknown" else "剖面已更新"
            )
            self._profile_chip.setProperty("tone", "ok")
        else:
            self._profile_chip.setText("无剖面数据")
            self._profile_chip.setProperty("tone", "")
        theme.repolish(self._profile_chip)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self._satellite_badge.setText(
            "刷新失败 · 保留上次结果" if self._last_context else "刷新失败"
        )
        self._satellite_badge.setProperty("tone", "warn")
        theme.repolish(self._satellite_badge)
        self._log(f"失败：{message}")
        # 引擎对「事件窗口外」明确提示改用模式预测；该场景预热/重试必然
        # 再失败，只有缺帧类错误才附加卫星预热引导。
        if "4 小时" in message or "窗口" in message:
            self.log_view.append(
                "提示：该目标时刻在实况支持窗口之外；"
                "请改用单点预测页的模式预测（GFS/EC）或调整目标日期。"
            )
        else:
            self.log_view.append(
                "提示：实况依赖新鲜 Himawari HSD 帧（约每 10 分钟一帧）；"
                "缺帧或 GFS 垂直先验未预热时引擎会 fail-closed。"
                "可在本页执行「预热卫星与数据」后重试。"
            )

    def on_theme_changed(self) -> None:
        self._strip_map.update()
        self._profile.on_theme_changed()
        self.curve.update()
        for button in (self.dawn_btn, self.dusk_btn):
            theme.repolish(button)

    def shutdown(self) -> None:
        self.data_panel.shutdown()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            # 必须等线程退出再析构：运行中的 QThread 被销毁会 abort 进程。
            self._worker.wait(600000)
