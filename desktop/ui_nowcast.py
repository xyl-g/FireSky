"""FireSky 桌面应用 · 实况 nowcast 标签页。

观测点（复用单点标签收藏）+ 现象 + 事件日期（默认今天）→ QThread 调
web_server.nowcast_payload + run_forecast_payload（同一条权威路径，
forecast_mode=nowcast / model_source=satellite）。结果：四指标卡
（实况峰值 / 对流顶棚 / 采样覆盖率 / 光路阻塞风险）+ 台账。
新鲜卫星帧缺失时引擎会 fail-closed——错误展示在日志，属预期行为。
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
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
from desktop.ui_point import theme_and_level
from desktop.workers import ForecastWorker
from desktop.widgets import Location, fmt, load_locations


class NowcastTab(QWidget):
    """实况 nowcast 工作台。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._locations: list[Location] = load_locations()
        self._worker: ForecastWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(14)

        # ── 左栏 ──
        rail = QWidget()
        rail.setMaximumWidth(300)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(0, 0, 12, 0)
        rail_l.setSpacing(8)

        rail_l.addWidget(widgets.card_title("观测点（与单点标签共用收藏）"))
        self.location_list = QListWidget()
        self.location_list.setMaximumHeight(170)
        self.location_list.itemClicked.connect(self._on_location_clicked)
        rail_l.addWidget(self.location_list)

        seg_row = QHBoxLayout()
        self.dawn_btn = QPushButton("朝霞")
        self.dusk_btn = QPushButton("晚霞")
        for btn in (self.dawn_btn, self.dusk_btn):
            btn.setCheckable(True)
        self.dusk_btn.setChecked(True)
        self._phenomenon_group = QButtonGroup(self)
        self._phenomenon_group.setExclusive(True)
        self._phenomenon_group.addButton(self.dawn_btn)
        self._phenomenon_group.addButton(self.dusk_btn)
        seg_row.addWidget(self.dawn_btn)
        seg_row.addWidget(self.dusk_btn)
        rail_l.addLayout(seg_row)

        rail_l.addWidget(widgets.ink2_label("事件日期（默认今天）"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(datetime.now().date())
        rail_l.addWidget(self.date_edit)

        self.run_button = QPushButton("获取实况预测")
        self.run_button.setObjectName("runBtn")
        self.run_button.setMinimumHeight(40)
        self.run_button.clicked.connect(self._run)
        rail_l.addWidget(self.run_button)

        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        rail_l.addWidget(self.cancel_button)

        self.progress = QProgressBar()
        rail_l.addWidget(self.progress)
        self.progress_label = widgets.ink2_label("实况需要新鲜 Himawari HSD 帧；缺帧时引擎 fail-closed。")
        self.progress_label.setWordWrap(True)
        rail_l.addWidget(self.progress_label)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        rail_l.addWidget(self.log_view, 1)

        root.addWidget(rail)

        # ── 右侧 ──
        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(10)

        head = QLabel("实况 nowcast")
        head.setProperty("role", "h1")
        main_l.addWidget(head)
        self.meta_label = widgets.ink2_label("卫星观测时间 · 覆盖率 · 数据源在获取后显示")
        main_l.addWidget(self.meta_label)

        # 四指标卡
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        self.metric_cards: dict[str, QLabel] = {}
        self.metric_subs: dict[str, QLabel] = {}
        card_defs = (
            ("nowcast_peak", "实况峰值"),
            ("convective_score_ceiling", "对流顶棚"),
            ("calibrated_sample_coverage_ratio", "采样覆盖率"),
            ("geometric_path_blocking_risk", "太阳入射光路"),
        )
        for key, title in card_defs:
            card = widgets.make_card()
            card_l = QVBoxLayout(card)
            card_l.setContentsMargins(14, 10, 14, 10)
            title_label = widgets.card_title(title)
            value = QLabel("—")
            value_font = QFont()
            value_font.setPointSize(22)
            value_font.setBold(True)
            value.setFont(value_font)
            sub = widgets.ink2_label("")
            sub.setWordWrap(True)
            card_l.addWidget(title_label)
            card_l.addWidget(value)
            card_l.addWidget(sub)
            self.metric_cards[key] = value
            self.metric_subs[key] = sub
            cards_row.addWidget(card, 1)
        main_l.addLayout(cards_row)

        # 台账
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["时刻", "质量", "档位", "可烧", "雷达约束", "云相", "太阳仰角"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        main_l.addWidget(self.table, 1)

        root.addWidget(main, 1)
        self._reload_locations(select_first=True)

    def _reload_locations(self, select_first: bool = False) -> None:
        self.location_list.clear()
        for loc in load_locations():
            star = "★ " if loc.starred else ""
            item = QListWidgetItem(f"{star}{loc.name}    {loc.coord_label()}")
            item.setData(Qt.ItemDataRole.UserRole, loc)
            self.location_list.addItem(item)
        if select_first and self.location_list.count():
            self.location_list.setCurrentRow(0)

    def _on_location_clicked(self, item: QListWidgetItem) -> None:
        # 点击只做选中回显，不重建列表
        pass

    def _selected_location(self) -> Location | None:
        item = self.location_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, Location) else None

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        loc = self._selected_location()
        if loc is None:
            QMessageBox.warning(self, "FireSky", "请先在单点标签添加并选择观测点。")
            return
        target = self.date_edit.date().toPython()
        if not isinstance(target, date_type):
            QMessageBox.warning(self, "FireSky", "事件日期无效。")
            return
        phenomenon = "dawn" if self.dawn_btn.isChecked() else "dusk"
        payload = {
            "name": loc.name,
            "lat": loc.lat,
            "lon": loc.lon,
            "elevation_m": loc.elevation_m,
            "timezone_name": loc.timezone_name,
            "target_date": target.isoformat(),
            "phenomenon": phenomenon,
            # nowcast 归一化由 ForecastWorker(nowcast=True) 里的
            # web_server.nowcast_payload 完成（forecast_mode=nowcast /
            # model_source=satellite / satellite_source=auto）。
            "workers": 1,
        }
        self._log(f"创建实况任务：{loc.name} {target} {phenomenon}（卫星实况）")
        self._worker = ForecastWorker(payload, nowcast=True)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(2)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._log("已请求取消（当前阶段完成后停止）。")

    def _on_progress(self, event: dict) -> None:
        percent = int(event.get("percent") or 0)
        message = str(event.get("message") or "")
        self.progress.setValue(max(2, min(100, percent)))
        if message:
            self.progress_label.setText(message)
            self._log(message)

    def _on_done(self, result: object) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(100)
        if not isinstance(result, dict):
            self._on_failed("实况结果格式异常。")
            return
        self.render_result(result)
        self._log("实况完成。")

    def render_result(self, result: dict) -> None:
        nowcast = result.get("nowcast") or {}
        level = str(result.get("quality_level") or "无明显火烧云")
        peak = float(result.get("peak_quality_index") or 0.0)
        color = theme.level_color(level)

        peak_card = self.metric_cards["nowcast_peak"]
        peak_card.setText(f"{peak:.2f}")
        peak_card.setStyleSheet(f"color: {color};")
        best = widgets.local_time_label(result.get("best_time_local"))
        self.metric_subs["nowcast_peak"].setText(f"{level} · {best}")

        ceiling = nowcast.get("convective_score_ceiling")
        ceiling_card = self.metric_cards["convective_score_ceiling"]
        if ceiling is None:
            ceiling_card.setText("—")
        else:
            ceiling_card.setText(f"{float(ceiling) * 100:.0f}" if float(ceiling) <= 1.0
                                 else f"{float(ceiling):.0f}")
        radar_risk = nowcast.get("radar_convective_risk")
        radar_text = (
            f"雷达回波风险 {float(radar_risk):.0%} · "
            if radar_risk is not None else ""
        )
        self.metric_subs["convective_score_ceiling"].setText(
            radar_text + "对流风险对评分上限的约束"
        )

        coverage = nowcast.get("calibrated_sample_coverage_ratio")
        coverage_card = self.metric_cards["calibrated_sample_coverage_ratio"]
        if coverage is None:
            coverage_card.setText("—")
            self.metric_subs["calibrated_sample_coverage_ratio"].setText("无校准采样数据")
        else:
            ratio = float(coverage)
            coverage_card.setText(f"{ratio:.0%}")
            coverage_card.setStyleSheet(
                f"color: {theme.palette()['ok'] if ratio >= 0.5 else theme.palette()['warn']};"
            )
            self.metric_subs["calibrated_sample_coverage_ratio"].setText(
                "样本覆盖良好" if ratio >= 0.5 else "覆盖不足 · 结果置信度受限"
            )

        blocking = nowcast.get(
            "geometric_path_blocking_risk",
            nowcast.get("effective_path_blocking_risk"),
        )
        blocking_card = self.metric_cards["geometric_path_blocking_risk"]
        samples = nowcast.get("geometric_path_samples")
        if blocking is None:
            blocking_card.setText("—")
            self.metric_subs["geometric_path_blocking_risk"].setText("无光路几何样本")
        else:
            risk = float(blocking)
            blocking_card.setText(f"{risk * 100:.0f} / 100")
            blocking_card.setStyleSheet(
                f"color: {theme.palette()['ok'] if risk < 0.35 else theme.palette()['warn']};"
            )
            self.metric_subs["geometric_path_blocking_risk"].setText(
                f"{samples or 0} 样本 · " + ("挡光风险低" if risk < 0.35 else "注意光路遮挡")
            )

        image_time = nowcast.get("image_valid_time_utc")
        source = nowcast.get("source")
        self.meta_label.setText(
            f"数据源 {source or '—'} · 卫星观测 "
            f"{str(image_time or '')[:16]}Z · 覆盖率 "
            f"{fmt(coverage, '{:.0%}')}"
        )

        # 台账：steps（质量时次）+ 雷达约束 + 云相
        steps = result.get("steps") or []
        best_index = None
        valid_pairs = []
        for i, s in enumerate(steps):
            if s.get("valid", True):
                valid_pairs.append(
                    (i, float(s.get("instant_quality_index") or 0.0))
                )
        if valid_pairs:
            best_index = max(valid_pairs, key=lambda pair: pair[1])[0]

        radar_constraint = "上限 {}/100".format(
            f"{float(ceiling) * 100:.0f}" if ceiling is not None and float(ceiling) <= 1.0
            else (f"{float(ceiling):.0f}" if ceiling is not None else "—")
        )
        self.table.setRowCount(0)
        for index, s in enumerate(steps):
            if not s.get("valid", True):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            instant = float(s.get("instant_quality_index") or 0.0)
            step_level = str(
                s.get("instant_quality_level") or theme_and_level(instant)
            )
            row_color = QColor(theme.level_color(step_level))
            stamp = widgets.local_time_label(s.get("valid_time_local"))
            if index == best_index:
                stamp = f"▶ {stamp}（峰值）"
            phase = s.get("cloud_phase_proxy")
            phase_label = phase.get("label") if isinstance(phase, dict) else phase
            phase_cn = {
                "ice": "冰晶", "water": "过冷水", "mixed": "冰水混合",
                "unknown": "—",
            }.get(str(phase_label or ""), str(phase_label or "—"))
            row_items = (
                QTableWidgetItem(stamp),
                QTableWidgetItem(f"{instant:.3f}"),
                QTableWidgetItem(step_level),
                QTableWidgetItem("✓" if s.get("physically_burnable") else "×"),
                QTableWidgetItem(radar_constraint),
                QTableWidgetItem(phase_cn),
                QTableWidgetItem(fmt(s.get("solar_elevation_deg"), "{:.1f}°")),
            )
            for col, cell in enumerate(row_items):
                if col in (1, 2):
                    cell.setForeground(row_color)
                self.table.setItem(row, col, cell)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("失败（详见日志）")
        self._log(f"失败：{message}")
        # 实况无新鲜卫星帧时 fail-closed 是预期行为，仅日志提示，不弹窗打断。
        self.log_view.append(
            "提示：实况依赖新鲜 Himawari HSD 帧（约每 10 分钟一帧）；"
            "缺帧或 GFS 垂直先验未预热时引擎会 fail-closed。"
            "可在数据源标签执行「卫星预热」后重试。"
        )

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
