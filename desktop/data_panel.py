"""Shared check / prepare / run workflow for native forecast pages."""

from __future__ import annotations

import copy

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from desktop import widgets
from desktop.data_dialog import ForecastDataDialog
from desktop.icons import set_icon
from desktop.data_access import (
    apply_publications, brief_status, check_model_data, prepare_model_data,
    check_nowcast_data, prepare_nowcast_data, published_cycles,
)
from desktop.workers import run_callable_worker


class ForecastDataPanel(QWidget):
    busyChanged = Signal(bool)
    dataChanged = Signal()

    def __init__(self, parent=None, *, regional=False, nowcast=False, compact=False):
        super().__init__(parent)
        self.regional = regional
        self.nowcast = nowcast
        self._workers = []
        self._active = None
        self._payload = None
        self._get_payload = None
        self._dialog = None
        self._session_active = False
        self._report = None
        self._callback = None
        self._revision = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.summary = widgets.role_label("本次数据尚未检查", "hint")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.check_button = QPushButton("检查数据")
        self.prepare_button = QPushButton("准备数据")
        if nowcast:
            self.prepare_button.setText("预热卫星与数据")
        self.inventory_button = QPushButton("数据与下载")
        set_icon(self.check_button, "circle-check")
        set_icon(self.prepare_button, "download")
        set_icon(self.inventory_button, "database")
        for button in (self.check_button, self.prepare_button):
            button.setObjectName("ghostBtn")
            row.addWidget(button)
        self.inventory_button.setObjectName("ghostBtn")
        if not compact:
            row.addWidget(self.inventory_button)
        row.addStretch(1)
        layout.addLayout(row)
        if compact:
            layout.addWidget(self.inventory_button)
        self.check_button.clicked.connect(lambda: self._action(False))
        self.prepare_button.clicked.connect(lambda: self._action(True))
        self.inventory_button.clicked.connect(self._open_data)

    def bind(self, get_payload):
        self._get_payload = get_payload

    def _open_data(self):
        window = self.window()
        if hasattr(window, "open_data"):
            window.open_data()

    def _action(self, prepare):
        if not self._get_payload:
            return
        payload = self._get_payload()
        if payload:
            self.begin(payload, prepare=prepare)

    def begin(self, payload, *, callback=None, prepare=False, is_current=None):
        if self._active is not None:
            return
        if self._dialog and self._session_active:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        if self._dialog:
            self._dialog.deleteLater()
        payload = copy.deepcopy(payload)
        self._payload = payload
        self._request_payload = copy.deepcopy(payload)
        original = copy.deepcopy(payload)
        self._current = is_current or (
            lambda: self._get_payload is None or self._get_payload() == original
        )
        self._callback = callback
        self._report = None
        self._session_active = True
        self._dialog = ForecastDataDialog(
            payload, self.window(), regional=self.regional, nowcast=self.nowcast,
            forecast=callback is not None,
        )
        self._dialog.refreshRequested.connect(self._check)
        self._dialog.updateRequested.connect(self._prepare)
        self._dialog.confirmed.connect(self._confirm)
        self._dialog.rejected.connect(self._dismissed)
        self._dialog.open()
        if prepare:
            self._dialog.update_button.setFocus()
        self._check()

    def _check(self, *, prepared=False):
        if self._active is not None or not self._session_active:
            return
        self._report = None
        self._revision += 1
        # A refresh resolves the event's applicable cycles again. Post-download
        # validation instead checks exactly the plan that was just downloaded.
        if not prepared:
            self._payload = copy.deepcopy(self._request_payload)
        payload = copy.deepcopy(self._payload)
        message = "正在检查下载结果" if prepared else "正在检查本地数据"
        self.summary.setText(message)
        self._dialog.set_busy(True, message)
        self._launch(
            lambda: check_nowcast_data(payload, probe=False, fast=not prepared) if self.nowcast
            else check_model_data(payload, regional=self.regional, probe=False, fast=not prepared),
            lambda report: self._checked(report, prepared=prepared),
        )

    def _launch(self, fn, done):
        worker = run_callable_worker(fn)
        worker.setParent(self)
        self._workers.append(worker)
        self._active = worker
        self.check_button.setEnabled(False)
        self.prepare_button.setEnabled(False)
        self.busyChanged.emit(True)
        worker.progress.connect(self._progress)
        worker.finished_ok.connect(lambda result: self._finish(done, result))
        worker.failed.connect(lambda message: self._finish(self._failed, message))
        worker.cancelled.connect(lambda: self._finish(self._failed, "数据操作已取消"))
        worker.start()

    def _progress(self, event):
        if not self._session_active:
            return
        self._dialog.show_progress(event)
        self.summary.setText(self._dialog.state_label.text())

    def _finish(self, done, result):
        self._active = None
        self.check_button.setEnabled(True)
        self.prepare_button.setEnabled(True)
        self.busyChanged.emit(False)
        try:
            current = self._current()
        except (ValueError, AttributeError, RuntimeError):
            current = False
        if not current:
            self.summary.setText("参数已变更；上次数据操作不属于当前请求，请重新检查。")
            if self._dialog:
                self._dialog.show_error("参数已变更，请关闭窗口后重新发起检查。")
                self._dialog.update_button.setEnabled(False)
                self._dialog.refresh_button.setEnabled(False)
            self.dataChanged.emit()
            return
        if self._session_active:
            done(result)

    def _checked(self, report, *, prepared=False):
        report = dict(report)
        report["publication_pending"] = True
        self._report = report
        self.summary.setText(brief_status(report))
        if report.get("model_cycle"):
            self._payload[f"_{report['model_source']}_cycle_override"] = report["model_cycle"]
        for model, cycle in report.get("model_cycles", {}).items():
            self._payload[f"_{model}_cycle_override"] = cycle
        if report.get("reference_time_utc"):
            self._payload["reference_time_utc"] = report["reference_time_utc"]
        if self._dialog:
            self._dialog.show_report(report, prepared=prepared)
        self._start_publication_check()

    def _start_publication_check(self):
        if not self._session_active:
            return
        revision = self._revision
        sources = [row["source"] for row in self._report["datasets"]]
        worker = run_callable_worker(lambda: published_cycles(sources))
        worker.setParent(self)
        self._workers.append(worker)
        worker.finished_ok.connect(lambda result: self._published(revision, result))
        worker.failed.connect(lambda _message: self._published(revision, {}))
        worker.start()

    def _published(self, revision, publications):
        if (
            revision != self._revision or not self._session_active
            or self._report is None or self._active is not None
        ):
            return
        self._report = apply_publications(self._report, publications)
        self._dialog.show_report(self._report)

    def _prepare(self):
        if self._active is not None or not self._session_active:
            return
        try:
            current = self._current()
        except (ValueError, AttributeError, RuntimeError):
            current = False
        if not current:
            self._failed("参数已变更，请重新检查。")
            return
        report = self._report
        self._revision += 1
        # Filling a checked plan needs no second scan. Re-plan only when a
        # verified newer NWP cycle might change the files being requested.
        if report is not None and not any(
            row["source"] != "cams" and row.get("verified")
            and row.get("latest_available_cycle_utc")
            and row.get("latest_available_cycle") not in row["request_cycle"].split(" / ")
            for row in report["datasets"]
        ):
            self._prepare_checked(report)
            return
        self._report = None
        self._payload = copy.deepcopy(self._request_payload)
        self._dialog.set_busy(True, "正在准备更新")
        payload = copy.deepcopy(self._payload)
        self._launch(
            lambda: check_nowcast_data(payload) if self.nowcast
            else check_model_data(payload, regional=self.regional, refresh=True),
            self._prepare_checked,
        )

    def _prepare_checked(self, report):
        if report.get("model_cycle"):
            self._payload[f"_{report['model_source']}_cycle_override"] = report["model_cycle"]
        for model, cycle in report.get("model_cycles", {}).items():
            self._payload[f"_{model}_cycle_override"] = cycle
        if report.get("reference_time_utc"):
            self._payload["reference_time_utc"] = report["reference_time_utc"]
        self._report = None
        if report.get("cams_update_cycle"):
            self._payload["_cams_refresh_cycle"] = report["cams_update_cycle"]
        message = "正在准备数据"
        self.summary.setText(message)
        self._dialog.set_busy(True, message)
        payload = copy.deepcopy(self._payload)

        def prepare():
            if self.nowcast:
                return prepare_nowcast_data(payload, progress=self._active.emit_progress)
            return prepare_model_data(
                payload, regional=self.regional,
                progress=self._active.emit_progress,
            )

        self._launch(prepare, self._prepared)

    def _prepared(self, _result):
        self.dataChanged.emit()
        self._check(prepared=True)

    def _confirm(self):
        if (
            not self._session_active or self._active is not None
            or not self._report or not self._report["ready"]
        ):
            return
        try:
            current = self._current()
        except (ValueError, AttributeError, RuntimeError):
            current = False
        if not current:
            self._report = None
            self._failed("参数已变更，请关闭窗口后重新检查。")
            return
        callback, payload = self._callback, copy.deepcopy(self._payload)
        payload.pop("_cams_refresh_cycle", None)
        self._callback = None
        self._session_active = False
        self._dialog.complete()
        if callback:
            callback(payload)

    def _dismissed(self):
        self._session_active = False
        self._callback = None
        self._report = None
        self.summary.setText("已取消数据检查，未开始预测。")
        if self._active is not None:
            self._active.cancel()

    def _failed(self, message):
        self._report = None
        self.summary.setText("数据未就绪")
        self.summary.setToolTip(str(message))
        if self._dialog and self._session_active:
            self._dialog.show_error(message)

    def cancel(self):
        if self._dialog and self._session_active:
            self._dialog.reject()
        if self._active is not None:
            self._active.cancel()

    def shutdown(self):
        self.cancel()
        for worker in self._workers:
            if worker.isRunning():
                worker.cancel()
                worker.wait()
