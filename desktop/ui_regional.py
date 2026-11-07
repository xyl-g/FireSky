"""FireSky 桌面应用 · 区域预测页（v4 暮光玻璃）。

区域多选（china / east-asia / custom 等与 Web 一致的 region 代码）、
现象/日期/网格间距 → QThread 调 run_and_cache_national_forecast_payload
（权威区域路径，带持久缓存）。结果：概览指标 + 示意气泡地图 + 排名台账。
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.ui_point import theme_and_level
from desktop.widgets import GlassCard, MetricCard, MiniMap, NumericItem, RunButton
from desktop.workers import RegionalForecastWorker

REGIONS = [
    ("china", "中国"),
    ("east-asia", "东亚"),
    ("southeast-asia", "东南亚"),
    ("south-asia", "南亚"),
    ("europe", "欧洲"),
    ("north-america", "北美"),
    ("oceania", "大洋洲"),
]


def _china_today() -> datetime.date:
    """与引擎 china_today 相同口径（UTC+8 日历日），不引入引擎导入。"""
    try:
        return datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
    except Exception:  # noqa: BLE001 - 时区库缺失时退回本地日期
        return datetime.now().date()


class RegionalForecastTab(QWidget):
    """区域预测工作台。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: RegionalForecastWorker | None = None
        self._cache_hit = False
        self._build_ui()

    # ── UI ──
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 26, 24)
        root.setSpacing(14)
        root.addWidget(self._build_region_card())
        root.addWidget(self._build_filter_bar())
        root.addLayout(self._build_metrics())

        middle = QHBoxLayout()
        middle.setSpacing(14)
        map_card = GlassCard()
        map_l = QVBoxLayout(map_card)
        map_l.setContentsMargins(12, 12, 12, 12)
        map_l.setSpacing(8)
        map_head = QHBoxLayout()
        map_head.addWidget(widgets.section_title("区域质量分布"))
        map_head.addStretch(1)
        map_head.addWidget(widgets.role_label("本地示意底图 · 不承载地理边界", "hint"))
        map_l.addLayout(map_head)
        self._regional_map = MiniMap(interactive=False, show_pin=False)
        self._regional_map.setMinimumHeight(300)
        map_l.addWidget(self._regional_map, 1)
        legend = QHBoxLayout()
        legend.setSpacing(12)
        for text, color in (("无明显", "#9e9e9e"), ("小烧", "#d6b56a"),
                            ("中等烧", "#ea7c2c"), ("大烧", "#c0261b")):
            dot = widgets.role_label("●", "hint")
            dot.setStyleSheet(f"color: {color}; font-size: 12px;")
            legend.addWidget(dot)
            legend.addWidget(widgets.role_label(text, "hint"))
        legend.addStretch(1)
        map_l.addLayout(legend)
        middle.addWidget(map_card, 1)

        rank = GlassCard()
        rank_l = QVBoxLayout(rank)
        rank_l.setContentsMargins(0, 0, 0, 6)
        rank_l.setSpacing(0)
        rank_head = QHBoxLayout()
        rank_head.setContentsMargins(17, 14, 17, 10)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("观测点排行榜"))
        self.meta_label = widgets.role_label("提交后显示区域 · 点数 · 周期 · 耗时", "hint")
        self.meta_label.setWordWrap(True)
        titles.addWidget(self.meta_label)
        rank_head.addLayout(titles)
        rank_head.addStretch(1)
        detail = QPushButton("运行日志")
        detail.setObjectName("ghostBtn")
        detail.clicked.connect(self._toggle_log)
        rank_head.addWidget(detail)
        rank_l.addLayout(rank_head)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["观测点", "峰值", "档位", "峰值时刻", "时长(min)", "云底 AGL", "AOD"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(300)
        rank_l.addWidget(self.table, 1)
        middle.addWidget(rank, 1)
        root.addLayout(middle)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(90)
        self.log_view.setVisible(False)
        root.addWidget(self.log_view)
        root.addStretch(1)
        self._log("区域预测走 run_and_cache_national_forecast_payload（持久缓存 + 全国批处理）。")

    def _build_region_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(widgets.section_title("计算区域（可多选）"))
        head.addStretch(1)
        head.addWidget(widgets.role_label("china 区域固定 0.25° 网格", "hint"))
        custom_toggle = QPushButton("✎  自定义 bbox")
        custom_toggle.setObjectName("ghostBtn")
        custom_toggle.clicked.connect(self._toggle_custom)
        head.addWidget(custom_toggle)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setSpacing(8)
        self.region_checks: dict[str, QCheckBox] = {}
        for index, (code, label) in enumerate(REGIONS):
            check = QCheckBox(label)
            check.setChecked(code == "east-asia")
            self.region_checks[code] = check
            grid.addWidget(check, index // 4, index % 4)
        custom_toggle_row = QCheckBox("启用自定义区域")
        custom_toggle_row.toggled.connect(self._on_custom_toggled)
        self.custom_enable = custom_toggle_row
        grid.addWidget(custom_toggle_row, 1, 3)
        layout.addLayout(grid)

        self._custom_frame = QWidget()
        custom_l = QGridLayout(self._custom_frame)
        custom_l.setContentsMargins(0, 0, 0, 0)
        custom_l.setSpacing(6)
        self.custom_spins: dict[str, QDoubleSpinBox] = {}
        for row, key in enumerate(("west", "south", "east", "north")):
            spin = QDoubleSpinBox()
            spin.setRange(-180.0 if key in {"west", "east"} else -90.0,
                          180.0 if key in {"west", "east"} else 90.0)
            spin.setDecimals(2)
            spin.setValue({"west": 100.0, "south": 20.0,
                           "east": 125.0, "north": 42.0}[key])
            self.custom_spins[key] = spin
            custom_l.addWidget(widgets.role_label(key, "hint"), row // 2, (row % 2) * 2)
            custom_l.addWidget(spin, row // 2, (row % 2) * 2 + 1)
        # 引擎要求东界大于西界（不支持跨国际日期变更线圈域），提前告知。
        self._custom_bounds_hint = widgets.role_label(
            "东界必须大于西界（暂不支持跨 ±180° 经线圈域）", "hint"
        )
        self._custom_bounds_hint.setWordWrap(True)
        custom_l.addWidget(self._custom_bounds_hint, 2, 2, 1, 2)
        self.custom_tz = QComboBox()
        self.custom_tz.setEditable(True)
        self.custom_tz.addItems(("Asia/Shanghai", "Asia/Tokyo", "UTC"))
        custom_l.addWidget(widgets.role_label("时区", "hint"), 2, 0)
        custom_l.addWidget(self.custom_tz, 2, 1)
        self._custom_frame.setVisible(False)
        layout.addWidget(self._custom_frame)
        return card

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
        self._phenomenon_group = QButtonGroup(self)
        self._phenomenon_group.setExclusive(True)
        self._phenomenon_group.addButton(self.dawn_btn)
        self._phenomenon_group.addButton(self.dusk_btn)
        self.dawn_btn.clicked.connect(lambda: self._on_phenomenon(0))
        self.dusk_btn.clicked.connect(lambda: self._on_phenomenon(1))
        layout.addWidget(self.dawn_btn)
        layout.addWidget(self.dusk_btn)
        layout.addSpacing(8)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        # 与引擎 target_date 默认口径一致（china_today = UTC+8 日历日），
        # 本地时区在美洲等地区会比中国晚一天。
        self.date_edit.setDate(_china_today())
        self.date_edit.setFixedWidth(128)
        layout.addWidget(self.date_edit)

        self.model_combo = QComboBox()
        self.model_combo.addItem("GFS", "gfs")
        self.model_combo.addItem("EC", "ec")
        self.model_combo.setFixedWidth(74)
        layout.addWidget(self.model_combo)

        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(0.25, 10.0)
        self.spacing_spin.setSingleStep(0.25)
        self.spacing_spin.setValue(2.0)
        self.spacing_spin.setSuffix(" °")
        self.spacing_spin.setFixedWidth(80)
        layout.addWidget(self.spacing_spin)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        self.workers_spin.setValue(4)
        self.workers_spin.setPrefix("×")
        self.workers_spin.setFixedWidth(62)
        layout.addWidget(self.workers_spin)
        layout.addStretch(1)

        self.run_button = RunButton("开始区域计算")
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
        self.progress_label = widgets.role_label("就绪", "hint")
        self.progress_label.setWordWrap(False)
        layout.addWidget(self.progress_label)  # 之前从未加入布局，进度文案不可见
        return bar

    def _build_metrics(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self._metric_cards: dict[str, MetricCard] = {}
        for key, title, sub in (
            ("points", "观测点", "提交后显示"),
            ("high", "高质量观测点", "指数 ≥ 1.5"),
            ("peak", "区域峰值", "最佳观测地"),
            ("timing", "计算耗时", "本地缓存命中"),
        ):
            card = MetricCard(title)
            card.set_sub(sub)
            row.addWidget(card, 1)
            self._metric_cards[key] = card
        return row

    def _toggle_custom(self) -> None:
        # setVisible 会触发 custom_enable 的 toggled → _on_custom_toggled，
        # 两处入口（✎ 按钮与 checkbox）共享同一可见性状态。
        self.custom_enable.setChecked(not self._custom_frame.isVisible())

    def _on_custom_toggled(self, checked: bool) -> None:
        if self._custom_frame.isVisible() == checked:
            return
        self._custom_frame.setVisible(checked)

    def _toggle_log(self) -> None:
        self.log_view.setVisible(not self.log_view.isVisible())

    def _on_phenomenon(self, index: int) -> None:
        for i, button in enumerate((self.dawn_btn, self.dusk_btn)):
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)

    # ── 提交 ──
    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _selected_regions(self) -> list[str]:
        codes = [code for code, check in self.region_checks.items() if check.isChecked()]
        if self.custom_enable.isChecked():
            codes.append("custom")
        return codes

    def _run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        regions = self._selected_regions()
        if not regions:
            QMessageBox.warning(self, "FireSky", "请至少选择一个计算区域。")
            return
        payload: dict = {
            "regions": regions,
            "target_date": self.date_edit.date().toPython().isoformat(),
            "phenomenon": "dawn" if self.dawn_btn.isChecked() else "dusk",
            "model_source": self.model_combo.currentData(),
            "forecast_mode": "model",
            "satellite_source": "off",
            "spacing_deg": float(self.spacing_spin.value()),
            "workers": int(self.workers_spin.value()),
        }
        # ── 桌面侧预校验（与引擎 national_coverage_from_payload 同口径）──
        known = {code for code, _ in REGIONS}
        unknown = [code for code in regions if code != "custom" and code not in known]
        if unknown:
            QMessageBox.warning(
                self, "FireSky",
                f"未知区域码 {unknown}；可选值为 {sorted(known)} 与 custom。",
            )
            return
        if "custom" in regions:
            bounds = {key: float(spin.value()) for key, spin in self.custom_spins.items()}
            if bounds["east"] <= bounds["west"] or bounds["north"] <= bounds["south"]:
                QMessageBox.warning(
                    self, "FireSky",
                    "自定义范围必须满足东界大于西界、北界大于南界。\n"
                    "暂不支持跨国际日期变更线（±180°）的圈选。",
                )
                return
            payload["custom_bounds"] = bounds
            payload["custom_timezone_name"] = self.custom_tz.currentText().strip()
        if "china" in regions and len(regions) > 1:
            # china 是 0.25° 生产网格：引擎只对 china 单选放行该密度
            # （36,685 点契约），与其他区域混选时 china 段会被当前 spacing
            # 粗采样，同一「中国」在不同组合下密度差 60 倍 —— 直接拦截。
            QMessageBox.warning(
                self, "FireSky",
                "「中国」区域按 0.25° 生产网格计算，不能与其他区域混选；\n"
                "请单独勾选「中国」，或取消勾选「中国」后选择其它区域组合。",
            )
            return
        if regions == ["china"]:
            payload["spacing_deg"] = 0.25
        self._log(
            f"提交区域计算：{regions} {payload['target_date']} "
            f"{payload['phenomenon']} spacing={payload['spacing_deg']}°"
        )
        self._cache_hit = False
        self._worker = RegionalForecastWorker(payload)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(2)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._log("已请求取消（引擎将在下一个进度检查点停止）。")
            self.progress_label.setText("取消中…")

    def _on_progress(self, event: dict) -> None:
        # percent=0 与缺字段是两回事：区分「尚未上报」与「进度回到 0」。
        raw_percent = event.get("percent")
        percent = int(raw_percent) if raw_percent is not None else None
        message = str(event.get("message") or "")
        if str(event.get("stage") or "") == "cache" and percent == 99:
            # 引擎缓存命中只发一个 stage=cache/percent=99 事件：
            # 明确告诉用户「这是秒级缓存命中，不是卡住」。
            self._cache_hit = True
            self.progress.setValue(100)
            self.progress_label.setText("命中全国预测持久缓存")
            if message:
                self._log(message)
            return
        if percent is not None:
            self.progress.setValue(max(2, min(100, percent)))
        if message:
            self.progress_label.setText(message)
            self._log(message)

    def _on_cancelled(self) -> None:
        """取消落地：复位控件（区域批处理取消也要能立刻再跑下一次）。"""
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress_label.setText("已取消")
        self._log("区域计算已取消。")

    def _on_done(self, result: object) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(100)
        self.progress.setVisible(False)
        self.progress_label.setText("完成")
        if not isinstance(result, dict):
            self._on_failed("区域结果格式异常。")
            return
        self.render_result(result)

    def render_result(self, result: dict) -> None:
        regions = result.get("regions") or []
        meta = (
            f"{(' · '.join(regions)) or '—'} · {result.get('phenomenon', '')} · "
            f"{result.get('target_date', '')} · {result.get('point_count', 0)} 观测点 · "
            f"成功 {result.get('success_count', 0)} · "
            f"周期 {str(result.get('cycle_time_utc') or '')[:16]}Z"
        )
        if getattr(self, "_cache_hit", False):
            meta += " · 命中持久缓存（未重算）"
        timing = result.get("timing") or {}
        total_seconds = timing.get("total_seconds")
        if total_seconds is not None:
            meta += f" · 耗时 {float(total_seconds):.0f}s"
        self.meta_label.setText(meta)
        self._log(f"区域计算完成：{meta}")

        points = result.get("points") or []
        points = sorted(
            points, key=lambda p: float(p.get("peak_quality_index") or 0.0),
            reverse=True,
        )
        peaks = [float(p.get("peak_quality_index") or 0.0) for p in points]
        self._metric_cards["points"].set_value(f"{len(points)}")
        self._metric_cards["points"].set_sub(f"成功 {result.get('success_count', 0)}")
        self._metric_cards["high"].set_value(
            f"{sum(1 for v in peaks if v >= 1.5)}", theme.palette()["accent"]
        )
        self._metric_cards["peak"].set_value(
            f"{max(peaks):.2f}" if peaks else "—", theme.palette()["accent"]
        )
        if points:
            self._metric_cards["peak"].set_sub(str(points[0].get("name") or "—"))
        self._metric_cards["timing"].set_value(
            f"{float(total_seconds):.0f} s" if total_seconds is not None else "—"
        )

        markers = [
            (float(p.get("lat") or 0.0), float(p.get("lon") or 0.0),
             float(p.get("peak_quality_index") or 0.0))
            for p in points[:60]
            if p.get("lat") is not None and p.get("lon") is not None
        ]
        shown = self._regional_map.set_markers(markers)
        if shown < len(markers):
            # 示意底图只覆盖中国：境外点位不绘制（原来会被钳位堆到边缘，
            # 用户会误把边缘气泡当真实位置）。
            self._log(
                f"示意底图仅覆盖中国范围：{len(markers) - shown} 个境外观测点"
                "未在地图上绘制（排行表数值不受影响）。"
            )

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for point in points[:2000]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            peak = float(point.get("peak_quality_index") or 0.0)
            level = str(
                point.get("peak_quality_level") or point.get("quality_level")
                or theme_and_level(peak)
            )
            color = QColor(theme.level_color(level))
            peak_item = NumericItem(f"{peak:.3f}", sort_key=peak)
            peak_item.setForeground(color)
            level_item = QTableWidgetItem(level)
            level_item.setForeground(color)
            duration = point.get("burnable_duration_minutes")
            components = point.get("quality_components") or {}
            aod = components.get("quality_aod550")
            duration_item = (
                NumericItem(f"{float(duration):.0f}", sort_key=float(duration))
                if duration is not None else QTableWidgetItem("—")
            )
            aod_item = (
                NumericItem(f"{float(aod):.2f}", sort_key=float(aod))
                if aod is not None else QTableWidgetItem("—")
            )
            for col, cell in enumerate((
                QTableWidgetItem(str(point.get("name") or "—")),
                peak_item, level_item,
                QTableWidgetItem(widgets.local_time_label(point.get("best_time_local"))),
                duration_item,
                QTableWidgetItem(widgets.format_agl_meters(
                    point.get("dominant_burnable_cloud_base_agl_mean_m"))),
                aod_item,
            )):
                self.table.setItem(row, col, cell)
        errors = result.get("errors") or []
        if errors:
            self._log(f"失败点 {len(errors)} 个；明细已写入运行日志。")
            for item in errors[:20]:
                self._log(f"失败点 {item.get('name')}: {item.get('error')}")
            if len(errors) > 20:
                self._log(f"… 其余 {len(errors) - 20} 个失败点见引擎 errors 字段。")
        self.table.setSortingEnabled(True)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress_label.setText("失败")
        self._log(f"失败：{message}")
        QMessageBox.warning(self, "区域预测失败", message)

    def on_theme_changed(self) -> None:
        self._regional_map.update()
        for button in (self.dawn_btn, self.dusk_btn):
            theme.repolish(button)

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            # 长批处理可能远超 2 秒：必须等线程真正退出再销毁窗口，
            # 否则运行中的 QThread 被析构会让 Qt 直接 abort 进程。
            self._worker.wait(600000)
