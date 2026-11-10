"""A compact source checklist, not a dump of the download plan."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

from desktop import widgets
from desktop.data_access import brief_status, grouped_datasets
from desktop.icons import icon, set_icon


class DataSourceRow(QWidget):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.setObjectName("dataSourceRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 15, 0, 15)
        layout.setSpacing(12)
        self.symbol = QLabel()
        self.symbol.setObjectName("dataSourceSymbol")
        self.symbol.setFixedSize(36, 36)
        self.symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.symbol.setPixmap(icon("database").pixmap(19, 19))
        layout.addWidget(self.symbol)
        names = QVBoxLayout()
        names.setSpacing(3)
        self.name = QLabel({"ecmwf": "EC", "hsd": "卫星帧"}.get(source, source.upper()))
        self.name.setObjectName("dataSourceName")
        self.caption = widgets.role_label("检查本地数据", "hint")
        names.addWidget(self.name)
        names.addWidget(self.caption)
        layout.addLayout(names, 1)
        self.status = QLabel("检查中")
        self.status.setObjectName("dataSourceStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setMinimumWidth(74)
        layout.addWidget(self.status)

    def render(self, row, *, pending=False):
        ready = row["ready"]
        old = ready and row.get("verified") and not row.get("is_latest")
        state = "missing" if not ready else "old" if old else "ready"
        self.status.setProperty("state", state)
        self.status.setText("需下载" if not ready else "可更新" if old else "已下载")
        if not ready:
            count = row.get("missing_count", 0)
            self.caption.setText(f"缺少 {count} 个时次或范围" if count else "本地数据不完整")
        elif old:
            self.caption.setText("本地周期较早")
        elif pending:
            self.caption.setText("本地可用 · 正在核验更新")
        elif not row.get("verified"):
            self.caption.setText("本地可用 · 更新未核验")
        else:
            self.caption.setText("已是适用数据")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class ForecastDataDialog(QDialog):
    refreshRequested = Signal()
    updateRequested = Signal()
    confirmed = Signal()

    def __init__(self, payload, parent=None, *, regional=False, nowcast=False, forecast=True):
        super().__init__(parent)
        self.setObjectName("forecastDataDialog")
        self.setWindowTitle("预测准备" if forecast else "数据准备")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(470)
        self.resize(540, 455 if nowcast else 430)
        self._ready = False
        self._busy = False
        self._forecast = forecast
        self._can_update = True
        self._report = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(0)
        top = QHBoxLayout()
        top.addWidget(widgets.role_label("预测准备" if forecast else "数据准备", "h2"), 1)
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("dataRefreshButton")
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setToolTip("重新检查数据")
        self.refresh_button.setAccessibleName("重新检查数据")
        set_icon(self.refresh_button, "refresh-cw")
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        location = str(payload.get("name") or ("所选区域" if regional else "观测点"))
        event = "朝霞" if payload.get("phenomenon") == "dawn" else "晚霞"
        self.request_label = widgets.role_label(
            f"{location} · {payload.get('target_date', '')} · {event}", "hint",
        )
        self.request_label.setWordWrap(True)
        layout.addSpacing(5)
        layout.addWidget(self.request_label)
        layout.addSpacing(22)
        self.state_label = QLabel("正在检查本地数据")
        self.state_label.setObjectName("dataReadiness")
        layout.addWidget(self.state_label)
        self.rows = {}
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(0)
        layout.addSpacing(10)
        layout.addLayout(self.list_layout)
        for source in (("gfs", "cams", "hsd") if nowcast else ("gfs", "ecmwf", "cams")):
            self._add_row(source)
        layout.addStretch(1)
        self.error_label = widgets.role_label("", "hint")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        layout.addSpacing(12)
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setObjectName("dataPrepareProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.percent = widgets.role_label("", "hint")
        self.percent.setFixedWidth(34)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.percent)
        layout.addLayout(progress_row)
        layout.addSpacing(16)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("ghostBtn")
        self.update_button = QPushButton("下载缺失数据")
        self.confirm_button = QPushButton("确定预测" if forecast else "完成")
        self.confirm_button.setObjectName("runBtn")
        set_icon(self.update_button, "download")
        for button in (self.refresh_button, self.update_button, self.cancel_button, self.confirm_button):
            button.setAutoDefault(False)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        actions.addWidget(self.update_button)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)
        self.refresh_button.clicked.connect(self.refreshRequested)
        self.update_button.clicked.connect(self.updateRequested)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(lambda: self.refresh_button.click())
        self.set_busy(True)

    def _add_row(self, source):
        row = DataSourceRow(source, self)
        self.rows[source] = row
        self.list_layout.addWidget(row)
        line = QFrame()
        line.setObjectName("dataSourceSeparator")
        line.setFixedHeight(1)
        self.list_layout.addWidget(line)

    def set_busy(self, busy, message=None):
        self._busy = busy
        self.refresh_button.setEnabled(not busy)
        self.update_button.setEnabled(not busy and self._can_update)
        self.confirm_button.setEnabled(self._ready and not busy)
        self.progress.setRange(0, 0 if busy else 100)
        if not busy:
            self.progress.setValue(0)
            self.percent.clear()
        if message:
            self.state_label.setText(message)

    def show_progress(self, event):
        message = str(event.get("message") or "").upper()
        source = next((name for name in ("CAMS", "GFS", "EC", "HSD") if name in message), None)
        self.state_label.setText(f"正在准备 {source}" if source else "正在准备数据")
        if "percent" in event:
            percent = max(0, min(100, int(event["percent"])))
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
            self.percent.setText(f"{percent}%")

    def show_report(self, report, *, prepared=False):
        self._report = report
        self._ready = bool(report["ready"])
        rows = grouped_datasets(report.get("datasets", []))
        for row in rows:
            if row["source"] not in self.rows:
                self._add_row(row["source"])
            self.rows[row["source"]].render(row, pending=report.get("publication_pending", False))
        if "satellite_frames" in report:
            count = report["satellite_frames"]
            if "hsd" not in self.rows:
                self._add_row("hsd")
            self.rows["hsd"].render(dict(ready=count >= 2, verified=True, is_latest=True, missing_count=max(0, 2-count)))
            self.rows["hsd"].caption.setText(f"相邻扫描帧 {count}/2")
        self.error_label.hide()
        self._can_update = not self._ready or any(row.get("verified") and not row.get("is_latest") for row in rows)
        self.update_button.setVisible(self._can_update)
        self.update_button.setText("下载缺失数据" if not self._ready else "更新数据")
        self.update_button.setObjectName("runBtn" if not self._ready else "ghostBtn")
        self.confirm_button.setObjectName("runBtn" if self._ready else "ghostBtn")
        set_icon(self.update_button, "download", color="#ffffff" if not self._ready else None)
        for button in (self.update_button, self.confirm_button):
            button.style().unpolish(button)
            button.style().polish(button)
        message = "数据已就绪" if self._ready else brief_status(report)
        self.set_busy(False, message)

    def show_error(self, message):
        self._ready = False
        self._can_update = True
        self.update_button.show()
        self.update_button.setText("重试下载")
        self.set_busy(False, "数据准备未完成")
        text = str(message).splitlines()[0]
        self.error_label.setText(text if len(text) <= 65 else text[:65] + "…")
        self.error_label.setToolTip(str(message))
        self.error_label.show()

    def accept(self):
        if self._ready and not self._busy:
            self.confirmed.emit()

    def complete(self):
        super().accept()
