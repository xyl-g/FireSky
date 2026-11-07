"""FireSky 桌面应用（PySide6）。

与 Web 服务共用同一个预测核心：桌面端直接调用
``firesky.web_server.run_forecast_payload``（与 HTTP API / Python API
同一条权威装配路径，数值完全一致），不经过任何 HTTP 一跳。

界面层（PySide6）与引擎层（firesky）完全解耦：
- ForecastWorker(QThread)：后台线程执行预测，progress/error/done 信号
  驱动界面刷新；窗口关闭即取消线程。
- MainWindow：观测点表单 + 常用城市 + 预测按钮 + 进度/日志 + 结果
  （峰值档位卡片 + 逐五分钟质量台账表 + 质量曲线）。

运行：``python desktop/app.py``（需先 ``make install`` 或
``pip install -e ".[dev,cams,nowcast,ec]"``）。
打包：见 ``desktop/FireSky.spec``（PyInstaller，macOS/Windows）。
"""

from __future__ import annotations

import sys
import traceback
from datetime import date as date_type
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
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

LEVEL_COLORS = {
    "无明显火烧云": "#9e9e9e",
    "微微烧": "#a8a29e",
    "小烧": "#d6b56a",
    "小烧到中等烧": "#e09b3d",
    "中等烧": "#ea7c2c",
    "中等烧到大烧": "#e05a1e",
    "不完美大烧": "#d43d1a",
    "典型大烧": "#c0261b",
    "优质大烧": "#a31a1f",
    "世纪大烧": "#7d0f1e",
}

CITIES = [
    ("杭州", 30.2741, 120.1551, 8.0, "Asia/Shanghai"),
    ("北京", 39.9042, 116.4074, 44.0, "Asia/Shanghai"),
    ("上海", 31.2304, 121.4737, 4.0, "Asia/Shanghai"),
    ("广州", 23.1291, 113.2644, 11.0, "Asia/Shanghai"),
    ("成都", 30.5728, 104.0668, 500.0, "Asia/Shanghai"),
    ("西安", 34.3416, 108.9398, 405.0, "Asia/Shanghai"),
    ("拉萨", 29.6520, 91.1721, 3650.0, "Asia/Shanghai"),
    ("哈尔滨", 45.8038, 126.5350, 150.0, "Asia/Shanghai"),
]


class ForecastWorker(QThread):
    """后台预测线程：与 HTTP API 共用 run_forecast_payload 权威路径。"""

    progress = Signal(dict)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, payload: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _on_progress(self, event: dict) -> None:
        if not self._cancelled:
            self.progress.emit(event)

    def run(self) -> None:
        from firesky.web_server import run_forecast_payload, WebServerConfig

        try:
            from pathlib import Path

            config = WebServerConfig()
            result = run_forecast_payload(
                dict(self._payload),
                config,
                progress_callback=self._on_progress,
            )
            if self._cancelled:
                return
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001 - 界面层统一兜底
            if self._cancelled:
                return
            traceback.print_exc()
            self.failed.emit(str(exc))


class QualityCurve(QWidget):
    """逐五分钟质量指数曲线（轻量 QPainter，无第三方绘图依赖）。"""

    def __init__(self) -> None:
        super().__init__()
        self._steps: list[tuple[str, float]] = []
        self.setMinimumHeight(160)

    def set_steps(self, steps: list[tuple[str, float]]) -> None:
        self._steps = steps
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(46, 12, -12, -26)
        painter.fillRect(rect, QColor("#1b1d24" if self._is_dark() else "#faf6ef"))

        if not self._steps:
            painter.setPen(QColor("#888"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        max_v = max(2.5, max((v for _, v in self._steps), default=0.0) * 1.15)
        n = len(self._steps)
        step_w = rect.width() / max(1, n - 1) if n > 1 else rect.width()

        # 网格线（0 / 1 / 2 三档参考线）
        pen = QPen(QColor(128, 128, 128, 60))
        painter.setPen(pen)
        for level in (0.0, 1.0, 2.0):
            y = rect.bottom() - (level / max_v) * rect.height()
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            painter.setPen(QColor("#999"))
            f = painter.font()
            f.setPointSize(8)
            painter.setFont(f)
            painter.drawText(rect.left() - 40, int(y) + 4, f"{level:g}")
            painter.setPen(pen)

        # 曲线
        pen = QPen(QColor("#e05a1e"))
        pen.setWidth(2)
        painter.setPen(pen)
        prev = None
        for i, (_, v) in enumerate(self._steps):
            x = rect.left() + i * step_w
            y = rect.bottom() - (min(v, max_v) / max_v) * rect.height()
            point = (int(x), int(y))
            if prev is not None:
                painter.drawLine(prev[0], prev[1], point[0], point[1])
            prev = point

        # 峰值标注
        if self._steps:
            best_i = max(range(n), key=lambda i: self._steps[i][1])
            bx = rect.left() + best_i * step_w
            by = rect.bottom() - (min(self._steps[best_i][1], max_v) / max_v) * rect.height()
            painter.setBrush(QColor("#e05a1e"))
            painter.drawEllipse(int(bx) - 4, int(by) - 4, 8, 8)
            painter.setPen(QColor("#333"))
            painter.drawText(int(bx) - 20, int(by) - 10, f"峰值 {self._steps[best_i][1]:.2f}")

    def _is_dark(self) -> bool:
        palette = self.palette()
        return palette.color(self.backgroundRole()).lightness() < 128


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FireSky — 火烧云本地预测")
        self.resize(880, 720)
        self._worker: ForecastWorker | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ── 表单 ──
        form_box = QGroupBox("观测点与预测设置")
        form = QFormLayout(form_box)

        self.city_combo = QComboBox()
        for name, *_ in CITIES:
            self.city_combo.addItem(name)
        self.city_combo.addItem("自定义")
        self.city_combo.currentIndexChanged.connect(self._on_city)

        self.name_edit = QLineEdit("杭州")
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90.0, 90.0)
        self.lat_spin.setDecimals(4)
        self.lat_spin.setValue(30.2741)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(4)
        self.lon_spin.setValue(120.1551)
        self.elev_spin = QDoubleSpinBox()
        self.elev_spin.setRange(-500.0, 9000.0)
        self.elev_spin.setValue(8.0)
        self.tz_edit = QLineEdit("Asia/Shanghai")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(datetime.now().date())
        self.phenomenon_combo = QComboBox()
        self.phenomenon_combo.addItem("晚霞 (dusk)", "dusk")
        self.phenomenon_combo.addItem("朝霞 (dawn)", "dawn")
        self.model_combo = QComboBox()
        self.model_combo.addItem("GFS", "gfs")
        self.model_combo.addItem("ECMWF IFS", "ec")

        form.addRow("常用城市", self.city_combo)
        form.addRow("名称", self.name_edit)
        form.addRow("纬度", self.lat_spin)
        form.addRow("经度", self.lon_spin)
        form.addRow("海拔 (m)", self.elev_spin)
        form.addRow("时区", self.tz_edit)
        form.addRow("目标日期", self.date_edit)
        form.addRow("现象", self.phenomenon_combo)
        form.addRow("数值模式", self.model_combo)

        root.addWidget(form_box)

        # ── 按钮 + 进度 ──
        button_row = QHBoxLayout()
        self.run_button = QPushButton("获取预测")
        self.run_button.setMinimumHeight(38)
        self.run_button.clicked.connect(self._run)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        button_row.addWidget(self.run_button, 1)
        button_row.addWidget(self.cancel_button)
        root.addLayout(button_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(110)
        self.log_view.setPlaceholderText("进度日志…")
        root.addWidget(self.log_view)

        # ── 结果 ──
        self.peak_label = QLabel("—")
        peak_font = QFont()
        peak_font.setPointSize(20)
        peak_font.setBold(True)
        self.peak_label.setFont(peak_font)
        self.peak_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.peak_label)

        self.curve = QualityCurve()
        root.addWidget(self.curve)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["时刻", "质量指数", "可烧"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)

        self._on_city(0)

    # ── 交互 ──
    def _on_city(self, index: int) -> None:
        if index < 0 or index >= len(CITIES):
            return
        name, lat, lon, elev, tz = CITIES[index]
        self.name_edit.setText(name)
        self.lat_spin.setValue(lat)
        self.lon_spin.setValue(lon)
        self.elev_spin.setValue(elev)
        self.tz_edit.setText(tz)

    def _log(self, message: str) -> None:
        self.log_view.append(message)

    def _run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        target = self.date_edit.date().toPyDate()
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
            "phenomenon": self.phenomenon_combo.currentData(),
            "model_source": self.model_combo.currentData(),
            "forecast_mode": "model",
            "satellite_source": "off",
            "workers": 1,
        }
        self.log_view.clear()
        self._log("创建预测任务…")
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(2)

        self._worker = ForecastWorker(payload)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
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
            self._log(message)

    def _on_done(self, result: dict) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(100)
        # 单点 payload 是扁平结构：顶层 quality_index/quality_level/steps
        # （每步的数值键是 instant_quality_index）。
        level = str(result.get("quality_level") or "无明显火烧云")
        index_value = float(result.get("peak_quality_index") or 0.0)
        color = LEVEL_COLORS.get(level, "#555")
        self.peak_label.setText(
            f"{level}  ·  峰值 {index_value:.2f} / 2.5"
        )
        self.peak_label.setStyleSheet(f"color: {color};")
        self._log(f"完成：周期 {result.get('cycle_time_utc', '')}")

        steps = [
            (
                str(s.get("valid_time_local") or ""),
                float(s.get("instant_quality_index") or 0.0),
            )
            for s in (result.get("steps") or [])
            if s.get("valid", True)
        ]
        self.curve.set_steps(steps)
        self.table.setRowCount(0)
        for stamp, value in steps:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(stamp))
            item = QTableWidgetItem(f"{value:.3f}")
            item.setForeground(QColor(LEVEL_COLORS.get("中等烧", "#e05a1e")) if value >= 1.0 else QColor("#666"))
            self.table.setItem(row, 1, item)
            self.table.setItem(row, 2, QTableWidgetItem("是" if value >= 0.02 else "否"))

    def _on_failed(self, message: str) -> None:
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(0)
        self._log(f"失败：{message}")
        QMessageBox.warning(self, "预测失败", message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FireSky")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
