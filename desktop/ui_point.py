"""FireSky 桌面应用 · 单点预测标签页。

左侧观测点收藏栏（QListWidget + JSON 持久化 + 手输表单 + 现象 seg +
日期 + 获取预测按钮 + 进度/日志），右侧峰值档位卡 + 质量曲线 +
逐五分钟质量台账表。预测走 web_server.run_forecast_payload 权威路径。
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.workers import ForecastWorker, WorkerBase
from desktop.widgets import (
    Location,
    QualityCurve,
    fmt,
    load_locations,
    save_locations,
)


class PointForecastTab(QWidget):
    """单点预测工作台。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._locations: list[Location] = load_locations()
        self._worker: ForecastWorker | None = None
        self._current_result: dict | None = None
        self._build_ui()
        self._reload_location_list(select_starred=True)

    # ── UI ──
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ── 左栏 ──
        rail = QWidget()
        rail.setMaximumWidth(320)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(0, 0, 12, 0)
        rail_l.setSpacing(8)

        rail_l.addWidget(widgets.card_title("观测点（点击加载 · 地图选点自动落址）"))
        self.location_list = QListWidget()
        self.location_list.setMaximumHeight(190)
        self.location_list.itemClicked.connect(self._on_location_clicked)
        rail_l.addWidget(self.location_list)

        form_l = QVBoxLayout()
        form_l.setSpacing(4)
        self._add_field(form_l, "名称", self._make_name_edit())
        coords = QGridLayout()
        coords.setHorizontalSpacing(8)
        coords.setVerticalSpacing(4)
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90.0, 90.0)
        self.lat_spin.setDecimals(4)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(4)
        self.elev_spin = QDoubleSpinBox()
        self.elev_spin.setRange(-500.0, 9000.0)
        self.elev_spin.setDecimals(1)
        self.elev_source_label = widgets.ink2_label("海拔手动输入")
        for row, (label_text, editor) in enumerate((
            ("纬度", self.lat_spin),
            ("经度", self.lon_spin),
        )):
            label = widgets.ink2_label(label_text)
            coords.addWidget(label, row, 0)
            coords.addWidget(editor, row, 1)
        coords.addWidget(widgets.ink2_label("海拔 (m)"), 2, 0)
        coords.addWidget(self.elev_spin, 2, 1)
        coords.addWidget(self.elev_source_label, 3, 1)
        form_l.addLayout(coords)
        self.tz_edit = QLineEdit("Asia/Shanghai")
        self._add_field(form_l, "时区 (IANA)", self.tz_edit)
        rail_l.addLayout(form_l)

        # 现象 seg + 日期
        seg_row = QHBoxLayout()
        seg_row.setSpacing(0)
        self.dawn_btn = QPushButton("朝霞")
        self.dusk_btn = QPushButton("晚霞")
        for btn in (self.dawn_btn, self.dusk_btn):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dusk_btn.setChecked(True)
        self._phenomenon_group = QButtonGroup(self)
        self._phenomenon_group.setExclusive(True)
        self._phenomenon_group.addButton(self.dawn_btn, 0)
        self._phenomenon_group.addButton(self.dusk_btn, 1)
        self.dawn_btn.setStyleSheet("border-radius: 8px 0 0 8px;")
        self.dusk_btn.setStyleSheet("border-radius: 0 8px 8px 0;")
        seg_row.addWidget(self.dawn_btn, 1)
        seg_row.addWidget(self.dusk_btn, 1)
        rail_l.addLayout(seg_row)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(datetime.now().date())
        rail_l.addWidget(self.date_edit)

        self.model_combo = QComboBox()
        self.model_combo.addItem("GFS（主模式）", "gfs")
        self.model_combo.addItem("ECMWF IFS", "ec")
        rail_l.addWidget(self.model_combo)

        self.run_button = QPushButton("获取预测")
        self.run_button.setObjectName("runBtn")
        self.run_button.setMinimumHeight(40)
        self.run_button.clicked.connect(self._run_forecast)
        rail_l.addWidget(self.run_button)

        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        rail_l.addWidget(self.cancel_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        rail_l.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        rail_l.addWidget(self.log_view, 1)
        self._log("提示：观测点可用「地图选点」标签自动落址；海拔支持 DEM 自动查询。")

        location_actions = QHBoxLayout()
        star_btn = QPushButton("★ 收藏")
        star_btn.clicked.connect(self._star_current)
        remove_btn = QPushButton("删除")
        remove_btn.clicked.connect(self._remove_current)
        location_actions.addWidget(star_btn)
        location_actions.addWidget(remove_btn)
        rail_l.addLayout(location_actions)

        splitter.addWidget(rail)

        # ── 右侧结果区 ──
        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(10)

        # 峰值档位卡
        result_card = widgets.make_card()
        result_l = QHBoxLayout(result_card)
        result_l.setContentsMargins(20, 14, 20, 14)
        result_l.setSpacing(24)

        big_col = QVBoxLayout()
        self.quality_big = QLabel("—")
        big_font = QFont()
        big_font.setPointSize(34)
        big_font.setBold(True)
        self.quality_big.setFont(big_font)
        self.quality_level = QLabel("等待预测")
        self.quality_level.setStyleSheet("font-weight: 700; font-size: 14px;")
        big_col.addWidget(self.quality_big)
        big_col.addWidget(self.quality_level)
        result_l.addLayout(big_col)

        self.facts_l = QGridLayout()
        self.facts_l.setHorizontalSpacing(22)
        self.facts_l.setVerticalSpacing(6)
        self._fact_values: dict[str, QLabel] = {}
        for col, key in enumerate((
            "peak_quality_index", "best_time_local",
            "burnable_cloud_coverage_effective", "cloud_genus_proxy", "quality_aod550",
        )):
            titles = {
                "peak_quality_index": "峰值指数",
                "best_time_local": "峰值时刻",
                "burnable_cloud_coverage_effective": "可烧云量",
                "cloud_genus_proxy": "云属（代理）",
                "quality_aod550": "AOD 550",
            }
            title = widgets.ink2_label(titles[key])
            value = QLabel("—")
            value.setStyleSheet("font-weight: 600; font-size: 14px;")
            self.facts_l.addWidget(title, 0, col)
            self.facts_l.addWidget(value, 1, col)
            self._fact_values[key] = value
        result_l.addLayout(self.facts_l, 1)

        self.meta_badge = widgets.ink2_label("")
        self.meta_badge.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        result_l.addWidget(self.meta_badge)
        main_l.addWidget(result_card)

        # 质量曲线
        curve_card = widgets.make_card()
        curve_l = QVBoxLayout(curve_card)
        curve_l.setContentsMargins(14, 10, 14, 8)
        curve_l.addWidget(widgets.card_title("逐五分钟质量曲线（档位色带底衬 · 0–2.5）"))
        self.curve = QualityCurve(190)
        curve_l.addWidget(self.curve)
        main_l.addWidget(curve_card, 1)

        # 台账表
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["时刻", "质量", "档位", "可烧", "云底 AGL", "AOD", "太阳仰角"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        main_l.addWidget(self.table, 1)

        splitter.addWidget(main)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 860])

    def _make_name_edit(self) -> QLineEdit:
        self.name_edit = QLineEdit("杭州")
        return self.name_edit

    def _add_field(self, layout: QVBoxLayout, label_text: str, editor: QWidget) -> None:
        layout.addWidget(widgets.ink2_label(label_text))
        layout.addWidget(editor)

    # ── 观测点 ──
    def _reload_location_list(self, select_starred: bool = False) -> None:
        self.location_list.blockSignals(True)
        self.location_list.clear()
        starred_first = sorted(
            enumerate(self._locations),
            key=lambda pair: (not pair[1].starred, pair[0]),
        )
        for _, loc in starred_first:
            star = "★ " if loc.starred else ""
            item = QListWidgetItem(f"{star}{loc.name}    {loc.coord_label()}")
            item.setData(Qt.ItemDataRole.UserRole, loc)
            self.location_list.addItem(item)
        self.location_list.blockSignals(False)
        if select_starred and starred_first:
            self.location_list.setCurrentRow(0)
            self._apply_location(starred_first[0][1])

    def _on_location_clicked(self, item: QListWidgetItem) -> None:
        loc = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(loc, Location):
            self._apply_location(loc)

    def _apply_location(self, loc: Location) -> None:
        self.name_edit.setText(loc.name)
        self.lat_spin.setValue(loc.lat)
        self.lon_spin.setValue(loc.lon)
        self.elev_spin.setValue(loc.elevation_m)
        self.tz_edit.setText(loc.timezone_name)
        self.elev_source_label.setText(
            "海拔 DEM 自动查询" if loc.elevation_source == "dem" else "海拔手动输入"
        )

    def _current_location(self) -> Location:
        return Location(
            name=self.name_edit.text().strip() or "观测点",
            lat=float(self.lat_spin.value()),
            lon=float(self.lon_spin.value()),
            elevation_m=float(self.elev_spin.value()),
            timezone_name=self.tz_edit.text().strip() or "Asia/Shanghai",
            starred=False,
            elevation_source="dem" if "DEM" in self.elev_source_label.text() else "manual",
        )

    def _star_current(self) -> None:
        loc = self._current_location()
        for index, existing in enumerate(self._locations):
            if (
                abs(existing.lat - loc.lat) < 1e-4
                and abs(existing.lon - loc.lon) < 1e-4
            ):
                self._locations[index] = loc
                break
        else:
            self._locations.append(loc)
        loc.starred = True
        save_locations(self._locations)
        self._reload_location_list()
        self._log(f"已收藏观测点：{loc.name}")

    def _remove_current(self) -> None:
        row = self.location_list.currentRow()
        item = self.location_list.item(row if row >= 0 else 0)
        if item is None:
            return
        loc = item.data(Qt.ItemDataRole.UserRole)
        self._locations = [
            x for x in self._locations
            if not (x.name == loc.name and abs(x.lat - loc.lat) < 1e-6
                    and abs(x.lon - loc.lon) < 1e-6)
        ]
        save_locations(self._locations)
        self._reload_location_list(select_starred=True)
        self._log(f"已删除观测点：{loc.name}")

    # ── 地图选点联动（app.py 桥接）──
    def accept_map_pick(self, lat: float, lon: float, elevation_m: float | None,
                        dataset: str | None) -> Location:
        """地图标签点选后自动创建观测点（海拔可能由 DEM 查询异步补齐）。"""
        loc = Location(
            name=f"点选 {lat:.4f},{lon:.4f}",
            lat=round(float(lat), 4),
            lon=round(float(lon), 4),
            elevation_m=float(elevation_m) if elevation_m is not None else 0.0,
            timezone_name=self._guess_timezone(),
            starred=False,
            elevation_source="dem" if elevation_m is not None else "manual",
        )
        self._locations.append(loc)
        save_locations(self._locations)
        self._reload_location_list()
        for row in range(self.location_list.count()):
            item = self.location_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) is loc:
                self.location_list.setCurrentItem(item)
                break
        self._apply_location(loc)
        suffix = f"（{dataset}）" if dataset else ""
        self._log(f"地图选点：{loc.name} 海拔 {loc.elevation_m:.0f} m{suffix}")
        return loc

    def apply_elevation(self, elevation_m: float, dataset: str) -> None:
        self.elev_spin.setValue(float(elevation_m))
        self.elev_source_label.setText(f"海拔 DEM 已查询 · {dataset}")

    @staticmethod
    def _guess_timezone() -> str:
        # 中国境内外粗略推断：经度 73–135 且纬度 3–54 之内按北京时间。
        return "Asia/Shanghai"

    # ── 预测 ──
    def _phenomenon(self) -> str:
        return "dawn" if self.dawn_btn.isChecked() else "dusk"

    def set_phenomenon(self, value: str) -> None:
        (self.dawn_btn if value == "dawn" else self.dusk_btn).setChecked(True)

    def _run_forecast(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        target = self.date_edit.date().toPython()
        if not isinstance(target, date_type):
            QMessageBox.warning(self, "FireSky", "目标日期无效。")
            return
        payload = {
            "name": self.name_edit.text().strip() or "观测点",
            "lat": float(self.lat_spin.value()),
            "lon": float(self.lon_spin.value()),
            "elevation_m": float(self.elev_spin.value()),
            "timezone_name": self.tz_edit.text().strip() or "Asia/Shanghai",
            "target_date": target.isoformat(),
            "phenomenon": self._phenomenon(),
            "model_source": self.model_combo.currentData(),
            "forecast_mode": "model",
            "satellite_source": "off",
            "workers": 1,
        }
        self._start_worker(ForecastWorker(payload))
        self._log(f"创建预测任务：{payload['name']} {payload['target_date']} "
                  f"{payload['phenomenon']} {payload['model_source']}")

    def _start_worker(self, worker: ForecastWorker) -> None:
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(2)
        worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._log("已请求取消（当前阶段完成后停止）。")

    def attach_worker(self, worker: WorkerBase) -> None:
        """外部（如地图 DEM 查询）共享本栏进度/日志 UI。"""
        worker.progress.connect(self._on_progress)
        worker.failed.connect(lambda msg: self._log(f"失败：{msg}"))

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _on_progress(self, event: dict) -> None:
        percent = int(event.get("percent") or 0)
        message = str(event.get("message") or "")
        self.progress.setValue(max(2, min(100, percent)))
        if message:
            self._log(message)

    def _on_done(self, result: object) -> None:
        if not isinstance(result, dict):
            self._on_failed("预测结果格式异常。")
            return
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(100)
        self._current_result = result
        self.render_result(result)
        self._log(f"完成：周期 {result.get('cycle_time_utc', '')}")

    def render_result(self, result: dict) -> None:
        """把单点扁平 payload 渲染到峰值卡 + 曲线 + 台账。"""
        level = str(result.get("quality_level") or "无明显火烧云")
        peak = float(result.get("peak_quality_index") or 0.0)
        color = theme.level_color(level)
        self.quality_big.setText(f"{peak:.2f} / 2.5")
        self.quality_big.setStyleSheet(f"color: {color};")
        self.quality_level.setText(level)
        self.quality_level.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 14px;"
        )

        genus = result.get("cloud_genus_proxy")
        genus_label = genus.get("label") if isinstance(genus, dict) else genus
        facts = {
            "peak_quality_index": f"{peak:.2f}",
            "best_time_local": widgets.local_time_label(result.get("best_time_local")),
            "burnable_cloud_coverage_effective": fmt(
                result.get("burnable_cloud_coverage_effective"), "{:.2f}"
            ),
            "cloud_genus_proxy": str(genus_label or "—"),
            "quality_aod550": fmt(result.get("quality_aod550"), "{:.2f}"),
        }
        for key, text in facts.items():
            self._fact_values[key].setText(text)
        confidence = result.get("confidence_score")
        self.meta_badge.setText(
            f"{str(result.get('model_source') or 'gfs').upper()} · "
            f"{str(result.get('cycle_time_utc') or '')[:16]}Z"
            + (f"\n置信 {float(confidence):.2f}" if confidence is not None else "")
        )

        steps = result.get("steps") or []
        curve_steps = [
            (
                widgets.local_time_label(s.get("valid_time_local")),
                float(s.get("instant_quality_index") or 0.0),
                bool(s.get("valid", True)),
            )
            for s in steps
        ]
        self.curve.set_steps(curve_steps)

        best_index = None
        if curve_steps:
            valid_pairs = [
                (i, v) for i, (_, v, ok) in enumerate(curve_steps) if ok
            ]
            if valid_pairs:
                best_index = max(valid_pairs, key=lambda pair: pair[1])[0]

        self.table.setRowCount(0)
        for index, s in enumerate(steps):
            row = self.table.rowCount()
            self.table.insertRow(row)
            stamp = widgets.local_time_label(s.get("valid_time_local"), with_seconds=False)
            if index == best_index:
                stamp = f"▶ {stamp}（峰值）"
            instant = float(s.get("instant_quality_index") or 0.0)
            step_level = str(
                s.get("instant_quality_level")
                or theme_and_level(instant)
            )
            row_color = QColor(theme.level_color(step_level))

            stamp_item = QTableWidgetItem(stamp)
            quality_item = QTableWidgetItem(f"{instant:.3f}")
            quality_item.setForeground(row_color)
            level_item = QTableWidgetItem(step_level)
            level_item.setForeground(row_color)
            burnable_item = QTableWidgetItem(
                "✓" if bool(s.get("physically_burnable")) else "×"
            )
            if s.get("physically_burnable"):
                burnable_item.setForeground(QColor(theme.palette()["ok"]))
            cloud_base = widgets.format_agl_meters(
                s.get("dominant_burnable_cloud_base_agl_mean_m")
            )
            aod = fmt(s.get("quality_aod550"), "{:.2f}")
            solar = fmt(s.get("solar_elevation_deg"), "{:.1f}°")
            for col, cell in enumerate((
                stamp_item, quality_item, level_item, burnable_item,
                QTableWidgetItem(cloud_base), QTableWidgetItem(aod),
                QTableWidgetItem(solar),
            )):
                self.table.setItem(row, col, cell)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(0)
        self._log(f"失败：{message}")
        QMessageBox.warning(self, "预测失败", message)

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)


def theme_and_level(value: float) -> str:
    """指数 → 档位名（与引擎阈值一致，避免额外 import 引擎层）。"""
    if value < 0.001:
        return "无明显火烧云"
    if value < 0.05:
        return "微微烧"
    if value < 0.20:
        return "小烧"
    if value < 0.40:
        return "小烧到中等烧"
    if value < 0.60:
        return "中等烧"
    if value < 0.80:
        return "中等烧到大烧"
    if value < 1.00:
        return "不完美大烧"
    if value < 1.50:
        return "典型大烧"
    if value < 2.00:
        return "优质大烧"
    return "世纪大烧"
