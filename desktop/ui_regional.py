"""FireSky 桌面应用 · 区域预测标签页。

区域多选（china / east-asia / custom 等与 Web 一致的 region 代码）、
现象/日期/网格间距（spacing_deg，最小 0.25°）→ QThread 调
run_and_cache_national_forecast_payload（权威区域路径，带持久缓存）。
结果：排名台账表（QTableWidget 可排序）+ 档位配色圆点。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.workers import RegionalForecastWorker
from desktop.ui_point import theme_and_level

# 与 Web/REGION_LABELS 一致的区域多选（custom 由手输 bbox 提供）
REGIONS = [
    ("china", "中国（36685 点 · 0.25° 固定）"),
    ("east-asia", "东亚"),
    ("southeast-asia", "东南亚"),
    ("south-asia", "南亚"),
    ("europe", "欧洲"),
    ("north-america", "北美"),
    ("oceania", "大洋洲"),
]


class RegionalForecastTab(QWidget):
    """区域预测工作台。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: RegionalForecastWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ── 左栏：计算区域 + 参数 ──
        rail = QWidget()
        rail.setMaximumWidth(300)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(0, 0, 12, 0)
        rail_l.setSpacing(8)

        rail_l.addWidget(widgets.card_title("计算区域（可多选）"))
        self.region_checks: dict[str, QCheckBox] = {}
        region_box = QFrame()
        region_box.setProperty("cardSunken", True)
        region_l = QVBoxLayout(region_box)
        region_l.setContentsMargins(10, 8, 10, 8)
        region_l.setSpacing(4)
        self.china_only_hint = QLabel("china 区域固定 0.25° 网格（生产契约）")
        self.china_only_hint.setProperty("role", "ink2")
        for code, label in REGIONS:
            check = QCheckBox(label)
            check.setChecked(code == "east-asia")
            self.region_checks[code] = check
            region_l.addWidget(check)
        region_l.addWidget(self.china_only_hint)
        rail_l.addWidget(region_box)

        custom_label = widgets.ink2_label("自定义 bbox（west, south, east, north · 时区）")
        rail_l.addWidget(custom_label)
        custom_frame = QFrame()
        custom_frame.setProperty("cardSunken", True)
        custom_l = QGridLayout(custom_frame)
        custom_l.setContentsMargins(10, 8, 10, 8)
        self.custom_enable = QCheckBox("启用自定义区域")
        custom_l.addWidget(self.custom_enable, 0, 0, 1, 2)
        self.custom_spins: dict[str, QDoubleSpinBox] = {}
        for row, key in enumerate(("west", "south", "east", "north")):
            spin = QDoubleSpinBox()
            spin.setRange(-180.0 if key in {"west", "east"} else -90.0,
                          180.0 if key in {"west", "east"} else 90.0)
            spin.setDecimals(2)
            defaults = {"west": 100.0, "south": 20.0, "east": 125.0, "north": 42.0}
            spin.setValue(defaults[key])
            self.custom_spins[key] = spin
            custom_l.addWidget(QLabel(key), row // 2 + 1, (row % 2) * 2)
            custom_l.addWidget(spin, row // 2 + 1, (row % 2) * 2 + 1)
        self.custom_tz = QComboBox()
        self.custom_tz.setEditable(True)
        self.custom_tz.addItem("Asia/Shanghai")
        self.custom_tz.addItem("Asia/Tokyo")
        self.custom_tz.addItem("UTC")
        custom_l.addWidget(QLabel("时区"), 3, 0)
        custom_l.addWidget(self.custom_tz, 3, 1)
        rail_l.addWidget(custom_frame)

        form = QFormLayout()
        form.setSpacing(5)
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
        form.addRow("现象", seg_row)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(datetime.now().date())
        form.addRow("目标日期", self.date_edit)

        self.model_combo = QComboBox()
        self.model_combo.addItem("GFS", "gfs")
        self.model_combo.addItem("EC", "ec")
        form.addRow("模式", self.model_combo)

        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(0.25, 10.0)
        self.spacing_spin.setSingleStep(0.25)
        self.spacing_spin.setValue(2.0)
        self.spacing_spin.setSuffix(" °")
        form.addRow("网格间距", self.spacing_spin)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        self.workers_spin.setValue(4)
        form.addRow("并行 workers", self.workers_spin)
        rail_l.addLayout(form)

        self.run_button = QPushButton("提交区域计算")
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
        self.progress_label = widgets.ink2_label("就绪")
        rail_l.addWidget(self.progress_label)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        rail_l.addWidget(self.log_view, 1)
        self._log("区域预测走 run_and_cache_national_forecast_payload（持久缓存 + 全国批处理）。")

        splitter.addWidget(rail)

        # ── 右侧：结果 ──
        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(10)

        head = QLabel("区域结果")
        head.setProperty("role", "h1")
        main_l.addWidget(head)
        self.meta_label = widgets.ink2_label("提交后显示：区域 · 点数 · 周期 · 耗时")
        main_l.addWidget(self.meta_label)

        # 图例
        legend = QHBoxLayout()
        legend.setSpacing(14)
        for level in ("典型大烧", "优质大烧", "中等烧到大烧", "中等烧", "小烧", "无明显火烧云"):
            dot = QLabel("●")
            color = theme.level_color(level)
            dot.setStyleSheet(f"color: {color}; font-size: 14px;")
            name = QLabel(level)
            name.setProperty("role", "ink2")
            legend.addWidget(dot)
            legend.addWidget(name)
        legend.addStretch(1)
        main_l.addLayout(legend)

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
        main_l.addWidget(self.table, 1)

        hint = widgets.ink2_label("提示：气泡地图标注为后续增强；台账表点击表头即可排序。")
        main_l.addWidget(hint)

        splitter.addWidget(main)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 880])

    # ── 提交 ──
    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _selected_regions(self) -> list[str]:
        codes = [
            code for code, check in self.region_checks.items() if check.isChecked()
        ]
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
        if "custom" in regions:
            payload["custom_bounds"] = {
                key: float(spin.value()) for key, spin in self.custom_spins.items()
            }
            payload["custom_timezone_name"] = self.custom_tz.currentText().strip()
        # china 生产网格契约：spacing 必须 0.25
        if "china" in regions and len(regions) == 1:
            payload["spacing_deg"] = 0.25
        self._log(
            f"提交区域计算：{regions} {payload['target_date']} "
            f"{payload['phenomenon']} spacing={payload['spacing_deg']}°"
        )
        self._worker = RegionalForecastWorker(payload)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(2)
        self.progress_label.setText("任务已提交…")
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
        self.progress_label.setText("完成")
        if not isinstance(result, dict):
            self._on_failed("区域结果格式异常。")
            return
        self.render_result(result)

    def render_result(self, result: dict) -> None:
        regions = result.get("regions") or []
        meta = (
            f"{(' · '.join(regions)) or '—'} · "
            f"{result.get('phenomenon', '')} · {result.get('target_date', '')} · "
            f"{result.get('point_count', 0)} 观测点 · 成功 {result.get('success_count', 0)}"
            f" · 周期 {str(result.get('cycle_time_utc') or '')[:16]}Z"
        )
        timing = result.get("timing") or {}
        total_seconds = timing.get("total_seconds")
        if total_seconds is not None:
            meta += f" · 耗时 {float(total_seconds):.0f}s"
        self.meta_label.setText(meta)
        self._log(f"区域计算完成：{meta}")

        points = result.get("points") or []
        # 峰值优先排序（表格自身排序也会启用）
        points = sorted(
            points,
            key=lambda p: float(p.get("peak_quality_index") or 0.0),
            reverse=True,
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for point in points[:2000]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            peak = float(point.get("peak_quality_index") or 0.0)
            level = str(
                point.get("peak_quality_level")
                or point.get("quality_level")
                or theme_and_level(peak)
            )
            color = QColor(theme.level_color(level))
            name_item = QTableWidgetItem(str(point.get("name") or "—"))
            peak_item = QTableWidgetItem(f"{peak:.3f}")
            peak_item.setForeground(color)
            level_item = QTableWidgetItem(level)
            level_item.setForeground(color)
            best = widgets.local_time_label(point.get("best_time_local"))
            duration = point.get("burnable_duration_minutes")
            duration_item = QTableWidgetItem(
                f"{float(duration):.0f}" if duration is not None else "—"
            )
            base = widgets.format_agl_meters(
                point.get("dominant_burnable_cloud_base_agl_mean_m")
            )
            components = point.get("quality_components") or {}
            aod = components.get("quality_aod550")
            aod_item = QTableWidgetItem(f"{float(aod):.2f}" if aod is not None else "—")
            for col, cell in enumerate((
                name_item, peak_item, level_item, QTableWidgetItem(best),
                duration_item, QTableWidgetItem(base), aod_item,
            )):
                self.table.setItem(row, col, cell)
        errors = result.get("errors") or []
        if errors:
            self._log(f"失败点 {len(errors)} 个（首个）：{errors[0]}")
        self.table.setSortingEnabled(True)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress_label.setText("失败")
        self._log(f"失败：{message}")
        QMessageBox.warning(self, "区域预测失败", message)

    def shutdown(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
