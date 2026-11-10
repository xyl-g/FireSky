"""Local model inventory and explicit, scoped downloads."""

from __future__ import annotations

from datetime import datetime
import time

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QHeaderView, QMessageBox,
    QProgressBar, QPushButton, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from desktop import widgets
from desktop.icons import set_icon
from desktop.data_access import inventory, published_cycles
from desktop.workers import DataFetchWorker, DataPruneWorker, run_callable_worker


class DataSourceTab(QWidget):
    dataStatusChanged = Signal()

    def __init__(self, spot_store=None, parent=None):
        super().__init__(parent)
        self._workers = []
        self._inventory_worker = None
        self._download_worker = None
        self._published_worker = None
        self._prune_worker = None
        self._items = []
        self._published = {}
        self._published_at = 0.0
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 26, 24)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(widgets.section_title("本地资料库"))
        head.addStretch(1)
        self.refresh_button = QPushButton("刷新本地清单")
        self.refresh_button.clicked.connect(self.refresh)
        set_icon(self.refresh_button, "refresh-cw")
        self.latest_button = QPushButton("核验最新发布")
        self.latest_button.clicked.connect(self.check_published)
        for button in (self.refresh_button, self.latest_button):
            button.setObjectName("ghostBtn")
            head.addWidget(button)
        root.addLayout(head)
        self.summary = widgets.role_label("本地清单尚未读取", "hint")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        sources = QHBoxLayout()
        sources.setSpacing(12)
        self.source_cards = {}
        for source, title in (("gfs", "GFS"), ("ecmwf", "EC"), ("cams", "CAMS")):
            card = widgets.MetricCard(title)
            card.set_value("—")
            card.set_sub("尚未扫描")
            card.setMinimumHeight(92)
            sources.addWidget(card, 1)
            self.source_cards[source] = card
        root.addLayout(sources)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["数据源 / UTC 周期", "文件数", "大小", "预报时效 (h)", "范围", "发布状态"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(260)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in ((0, 165), (1, 60), (2, 80), (4, 160), (5, 125)):
            self.tree.setColumnWidth(column, width)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self._open_folder)
        root.addWidget(self.tree, 1)
        folder = QPushButton("打开选中缓存目录")
        folder.setObjectName("ghostBtn")
        set_icon(folder, "folder-open")
        folder.clicked.connect(lambda: self._open_folder(self.tree.currentItem()))
        root.addWidget(folder, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addSpacing(8)
        download_head = QHBoxLayout()
        download_head.addWidget(widgets.section_title("下载数据"))
        download_head.addStretch(1)
        log_toggle = QPushButton("下载日志")
        log_toggle.setObjectName("ghostBtn")
        log_toggle.clicked.connect(lambda: self.log.setVisible(not self.log.isVisible()))
        download_head.addWidget(log_toggle)
        root.addLayout(download_head)
        scope = QHBoxLayout()
        scope.setSpacing(12)
        self.region_combo = QComboBox()
        for code, label in (
            ("china", "中国"), ("east-asia", "东亚"), ("southeast-asia", "东南亚"),
            ("south-asia", "南亚"), ("europe", "欧洲"),
            ("north-america", "北美"), ("oceania", "大洋洲"),
        ):
            self.region_combo.addItem(label, code)
        scope.addWidget(self.region_combo)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(datetime.now().date())
        scope.addWidget(self.date_edit)
        self.event_combo = QComboBox()
        self.event_combo.addItem("朝霞与晚霞", ["dawn", "dusk"])
        self.event_combo.addItem("朝霞", ["dawn"])
        self.event_combo.addItem("晚霞", ["dusk"])
        scope.addWidget(self.event_combo)
        self.source_checks = {}
        for source, label in (("gfs", "GFS"), ("ecmwf", "EC"), ("cams", "CAMS")):
            check = QCheckBox(label)
            check.setChecked(True)
            self.source_checks[source] = check
            scope.addWidget(check)
        scope.addStretch(1)
        self.download_button = QPushButton("下载所选数据")
        self.download_button.setObjectName("pickBtn")
        set_icon(self.download_button, "download")
        self.download_button.clicked.connect(self._download)
        scope.addWidget(self.download_button)
        root.addLayout(scope)
        self.download_status = widgets.role_label("未开始下载", "hint")
        self.download_status.setWordWrap(True)
        root.addWidget(self.download_status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(90)
        self.log.setProperty("role", "log")
        self.log.hide()
        root.addWidget(self.log)
        foot = QHBoxLayout()
        self.prune_button = QPushButton("移出 2 天前的缓存")
        self.prune_button.setObjectName("ghostBtn")
        self.prune_button.clicked.connect(self._prune)
        foot.addWidget(self.prune_button)
        foot.addStretch(1)
        settings = QPushButton("凭据与地图设置")
        settings.setObjectName("ghostBtn")
        settings.clicked.connect(lambda: self.window().open_settings())
        foot.addWidget(settings)
        root.addLayout(foot)

    def _start(self, worker, done, failed, progress=None):
        worker.setParent(self)
        self._workers.append(worker)
        worker.finished_ok.connect(done)
        worker.failed.connect(failed)
        if progress:
            worker.progress.connect(progress)
        worker.start()

    def refresh(self):
        if self._inventory_worker is not None and self._inventory_worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.summary.setText("扫描本地原始文件、周期、时效及范围…")
        self._inventory_worker = run_callable_worker(inventory)
        self._start(self._inventory_worker, self._inventory_done, self._inventory_failed)

    def _inventory_done(self, items):
        self.refresh_button.setEnabled(True)
        self._items = items
        self._render()
        self.dataStatusChanged.emit()

    def _inventory_failed(self, message):
        self.refresh_button.setEnabled(True)
        self.summary.setText(f"清单读取失败：{message}")

    def _render(self):
        if self._published and time.monotonic() - self._published_at > 300:
            self._published = {}
        self.tree.clear()
        size = sum(item["total_storage_mb"] for item in self._items)
        count = sum(item["total_file_count"] for item in self._items)
        self.summary.setText(
            f"本地原始数据 {count} 个文件 · {size:.1f} MB · "
            "周期/时效索引不等于预测可用，预测前还会核验范围和文件内容。"
        )
        for item in self._items:
            source = item["source"]
            card = self.source_cards[source]
            card.set_value(f"{item['total_storage_mb']:.1f} MB")
            card.set_sub(
                f"{item['total_file_count']} 个文件 · "
                f"{item['latest_cycle'] or '暂无周期'}"
            )
            card.setToolTip(item["path"])
            publication = self._published.get(source, {})
            latest = (
                f"最新发布 {publication['label']}" if publication.get("verified")
                else "最新发布未核验"
            )
            title = {"gfs": "GFS", "ecmwf": "EC", "cams": "CAMS"}[source]
            parent = QTreeWidgetItem([
                title, str(item["total_file_count"]), f"{item['total_storage_mb']:.1f} MB",
                "", "缓存目录", latest,
            ])
            parent.setData(0, Qt.ItemDataRole.UserRole, item["path"])
            parent.setToolTip(4, item["path"])
            self.tree.addTopLevelItem(parent)
            for key, cycle in sorted(item["cycles"].items(), reverse=True):
                hours = ", ".join(f"{h:03d}" for h in cycle["hours"])
                coverage = (
                    sorted({
                        "全球" if box is None else
                        f"W{box[0]:g} E{box[1]:g} N{box[2]:g} S{box[3]:g}"
                        for boxes in cycle.get("coverage", {}).values() for box in boxes
                    })
                    if source == "gfs" else
                    sorted({
                        (f"N{bounds[0]:g} W{bounds[1]:g} S{bounds[2]:g} E{bounds[3]:g}"
                         if (bounds := item["area_bounds"].get(area)) else f"范围未识别 ({area})")
                        for areas in cycle.get("area_keys", {}).values() for area in areas
                    })
                    if source == "cams" else ["全球原始场"]
                )
                state = (
                    "最新已发布" if publication.get("verified") and cycle["label"] == publication["label"]
                    else "较早周期" if publication.get("verified") else "未核验"
                )
                child = QTreeWidgetItem([
                    cycle.get("label", key), str(cycle["files"]), f"{cycle['mb']:.1f} MB",
                    hours, " / ".join(coverage), state,
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, item["path"])
                child.setToolTip(3, hours)
                child.setToolTip(4, "\n".join(coverage))
                parent.addChild(child)
            if item["unindexed_file_count"]:
                parent.addChild(QTreeWidgetItem([
                    "旧格式 / 无索引 / 未完成", str(item["unindexed_file_count"]), "",
                    "", item["path"], "不计入数据就绪",
                ]))
            parent.setExpanded(True)

    def _open_folder(self, item, *_):
        if item is not None:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def check_published(self):
        if self._published_worker is not None and self._published_worker.isRunning():
            return
        self.latest_button.setEnabled(False)
        self.latest_button.setText("核验发布中…")
        self._published_worker = run_callable_worker(
            lambda: published_cycles(["gfs", "ecmwf", "cams"])
        )
        self._start(self._published_worker, self._published_done, self._published_failed)

    def _published_done(self, result):
        self._published = result
        self._published_at = time.monotonic()
        self.latest_button.setEnabled(True)
        self.latest_button.setText("核验最新发布")
        self._render()

    def _published_failed(self, message):
        self.latest_button.setEnabled(True)
        self.latest_button.setText("核验最新发布")
        self.log.append(f"发布核验失败：{message}")

    def download_payload(self):
        return {
            "region": self.region_combo.currentData(),
            "target_dates": [self.date_edit.date().toPython().isoformat()],
            "phenomena": self.event_combo.currentData(),
            "sources": [source for source, check in self.source_checks.items() if check.isChecked()],
            "spacing_deg": 0.25,
        }

    def _download(self):
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        payload = self.download_payload()
        if not payload["sources"]:
            QMessageBox.warning(self, "数据下载", "请至少选择一个数据源。")
            return
        if self.date_edit.date().toPython() < datetime.now().date():
            QMessageBox.warning(
                self, "历史事件数据",
                "此处按最新可用周期下载，不用于历史事件回放。\n"
                "请在单点或区域预测页选定历史日期后点击「准备数据」。",
            )
            return
        answer = QMessageBox.question(
            self, "下载区域数据",
            f"按最新可用周期下载 {self.region_combo.currentText()} · {payload['target_dates'][0]} · "
            f"{' / '.join(payload['sources'])}？\n"
            "区域原始场可能较大。已过去的事件可能需要较早周期，请在预测页准备。\n"
            "下载后仍需按预测窗口检查；CAMS 需要 ADS 凭据。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.download_button.setEnabled(False)
        self.prune_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.download_status.setText("创建下载任务…")
        self.log.append(str({key: value for key, value in payload.items()}))
        self._download_worker = DataFetchWorker(payload)
        self._start(self._download_worker, self._download_done, self._download_failed, self._progress)

    def _progress(self, event):
        self.progress.setRange(0, 100)
        self.progress.setValue(int(event.get("percent") or 0))
        self.download_status.setText(str(event.get("message") or "下载中…"))
        if int(event.get("percent") or 0) == 0:
            self.log.append(str(event.get("message") or ""))

    def _download_done(self, _result):
        self.download_status.setText("下载任务完成；正在重新读取本地清单。")
        self.download_button.setEnabled(True)
        self.prune_button.setEnabled(True)
        self.progress.hide()
        self.refresh()

    def _download_failed(self, message):
        self.download_status.setText(f"下载未完成：{message}")
        self.log.append(str(message))
        self.download_button.setEnabled(True)
        self.prune_button.setEnabled(True)
        self.progress.hide()
        self.refresh()

    def _prune(self):
        answer = QMessageBox.question(
            self, "移出旧缓存", "将 2 天前的缓存移入 .cache/.trash（仍占磁盘空间）。继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.prune_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self._prune_worker = DataPruneWorker(max_age_days=2)
        self._start(self._prune_worker, self._pruned, self._prune_failed)

    def _pruned(self, result):
        self.log.append(str(result))
        self.prune_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self.refresh()

    def _prune_failed(self, message):
        self.log.append(f"移出失败：{message}")
        self.prune_button.setEnabled(True)
        self.download_button.setEnabled(True)

    def shutdown(self):
        for worker in self._workers:
            if worker.isRunning():
                worker.cancel()
                worker.wait()
