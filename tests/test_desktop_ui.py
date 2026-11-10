"""Desktop workflow regressions. No network or forecast computation."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QAction, QKeySequence

from desktop import theme, widgets
from desktop import ui_point
from desktop.ui_nowcast import NowcastTab


class FakeGridMap(QWidget):
    def set_grid(self, *args, **kwargs):
        pass
    def fit_bounds(self, *args):
        pass
    def set_theme(self, *args):
        pass


class FakeWorker(QObject):
    progress = Signal(dict)
    finished_ok = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, payload=None):
        super().__init__()
        self.payload = payload
        self.running = False
        self.cancel_requested = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancel_requested = True


@pytest.fixture(scope="module")
def app(qt_app):
    return qt_app


@pytest.fixture
def workspace(app, monkeypatch, tmp_path):
    from desktop import ui_regional
    monkeypatch.setattr(widgets, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(widgets, "LOCATIONS_FILE", tmp_path / "locations.json")
    monkeypatch.setattr(ui_point, "FireSkyMap", None)
    monkeypatch.setattr(ui_regional, "FireSkyMap", FakeGridMap)
    monkeypatch.setattr(ui_point, "load_locations", lambda: [
        item.copy() for item in widgets.DEFAULT_LOCATIONS
    ])
    monkeypatch.setattr(theme, "_current_dark", False)
    app.setStyleSheet(theme._build_qss(theme.palette()))
    store = widgets.SpotStore(widgets.DEFAULT_LOCATIONS[0].copy())
    page = ui_point.PointForecastTab(store)
    page.resize(924, 648)
    page.show()
    app.processEvents()
    yield page, store
    page.shutdown()
    page.close()
    app.processEvents()


def result():
    return {
        "peak_quality_index": 0.4,
        "quality_level": "中等烧",
        "steps": [],
    }


@pytest.mark.parametrize("size", [(924, 648), (1244, 828)])
def test_run_action_stays_visible_when_rail_scrolls(workspace, app, size):
    page, _ = workspace
    page.resize(*size)
    app.processEvents()
    position = page.run_button.mapTo(page, QPoint(0, 0))
    assert page.run_button.isVisibleTo(page)
    assert position.y() + page.run_button.height() <= page.height()
    page.rail_scroll.verticalScrollBar().setValue(
        page.rail_scroll.verticalScrollBar().maximum()
    )
    app.processEvents()
    assert page.run_button.mapTo(page, QPoint(0, 0)) == position


def test_result_sections_remain_available_in_tabs(workspace):
    page, _ = workspace
    assert [page.result_tabs.tabText(i) for i in range(4)] == [
        "概览", "大气剖面", "诊断与时间窗", "逐时台账"
    ]
    assert page.result_tabs.widget(1).isAncestorOf(page.profile_canvas)
    assert page.result_tabs.widget(3).isAncestorOf(page.table)
    assert page.result_tabs.widget(3).isAncestorOf(page.provenance_view)


def test_changed_inputs_do_not_relabel_old_result(workspace):
    page, store = workspace
    page.render_result(result())
    title = page._hero_kicker.text()
    page._date_tabs.set_index(1)
    assert page._hero_badge.property("tone") == "warn"
    assert page._hero_kicker.text() == title
    page._date_tabs.set_index(0)
    assert page._hero_badge.text() == "预测完成"
    loc = store.location()
    loc.name = "另一个观测点"
    loc.lon += 1
    store.set_location(loc, persist=False)
    assert page._hero_badge.property("tone") == "warn"
    assert page._hero_kicker.text() == title
    assert page.quality_big.text() == "0.40"


def test_inflight_result_keeps_original_request_context(workspace):
    page, _ = workspace
    page._start_worker(FakeWorker())
    requested = page._running_context
    page._date_tabs.set_index(1)
    page._on_done(result())
    assert page._result_context == requested
    assert requested[5] in page._hero_kicker.text()
    assert page._hero_badge.property("tone") == "warn"


def test_cancel_has_pending_and_terminal_states(workspace):
    page, _ = workspace
    worker = FakeWorker()
    page._start_worker(worker)
    page._cancel()
    assert worker.cancel_requested
    assert not page.cancel_button.isEnabled()
    assert "正在取消" in page.cancel_button.text()
    page._on_progress({"stage": "score", "percent": 30})
    assert "正在取消" in page.progress_stage.text()
    page._on_cancelled()
    assert page.run_button.isEnabled()
    assert page.cancel_button.isHidden()
    assert page.progress.isHidden()


def test_favorite_action_is_not_hidden_in_coordinate_editor(workspace):
    page, store = workspace
    store.set_location(widgets.Location("新点位", 30, 120, 5), persist=False)
    assert page._editor.isHidden()
    assert page.favorite_button.isVisibleTo(page)
    assert page.favorite_button.isEnabled()
    page.favorite_button.click()
    assert not page.favorite_button.isEnabled()
    assert any(loc.name == "新点位" for loc in widgets.load_locations())


def test_keyboard_selection_updates_shared_location(workspace, app):
    page, store = workspace
    page._spot_tabs.set_index(1)
    page.location_list.setCurrentRow(0)
    QTest.keyClick(page.location_list, Qt.Key.Key_Down)
    app.processEvents()
    selected = page.location_list.currentItem().data(Qt.ItemDataRole.UserRole)
    assert store.location().name == selected.name
    assert page.remove_favorite_button.isEnabled()


def test_run_commits_edited_coordinates_to_shared_location(workspace, monkeypatch):
    page, store = workspace
    monkeypatch.setattr(ui_point, "ForecastWorker", FakeWorker)
    checked = []
    def preflight(payload, *, callback, **kwargs):
        checked.append(payload)
        callback(payload)
    monkeypatch.setattr(page.data_panel, "begin", preflight)
    page.lat_spin.setValue(31.25)
    page._run_forecast()
    assert store.location().lat == 31.25
    assert page._worker.payload["lat"] == 31.25
    assert checked[0]["lat"] == 31.25
    page._on_cancelled()


def test_custom_date_does_not_reset_selected_date(workspace):
    page, _ = workspace
    page._date_tabs.set_index(1)
    tomorrow = page.date_edit.date()
    page._date_tabs.set_index(2)
    assert page.date_edit.date() == tomorrow


def test_nowcast_marks_old_results_without_relabeling_them(workspace, app):
    _, store = workspace
    page = NowcastTab(store)
    page.render_result(result())
    title = page._chart_sub.text()
    assert page._satellite_badge.property("tone") == "warn"
    loc = store.location()
    loc.name = "新的实况点"
    store.set_location(loc, persist=False)
    app.processEvents()
    assert page._chart_sub.text() == title
    assert page._satellite_badge.text() == "参数已变更 · 请刷新"
    page.close()


def test_nowcast_failure_does_not_keep_refreshing_badge(workspace):
    _, store = workspace
    page = NowcastTab(store)
    page._worker = FakeWorker()
    page._on_failed("目标时刻在实况窗口之外")
    assert page._worker is None
    assert page._satellite_badge.text() == "刷新失败"
    assert page.run_button.isEnabled()
    page.close()


def test_native_menu_shortcuts_dispatch_to_current_page(workspace, app, monkeypatch):
    from desktop.app import MainWindow

    window = MainWindow()
    window.show()
    app.processEvents()
    actions = {action.text(): action for action in window.findChildren(QAction)}
    invoked = []
    monkeypatch.setattr(window.point_tab, "_run_forecast", lambda: invoked.append("point"))
    monkeypatch.setattr(window.regional_tab, "_run", lambda: invoked.append("regional"))
    monkeypatch.setattr(window.nowcast_tab, "_run", lambda: invoked.append("nowcast"))
    run = actions["运行当前预测"]
    assert QKeySequence("F5") in run.shortcuts()
    assert QKeySequence("Ctrl+Return") in run.shortcuts()
    for index in range(3):
        window._select_page(index)
        run.trigger()
    assert invoked == ["point", "regional", "nowcast"]
    actions["搜索地点"].trigger()
    app.processEvents()
    assert window.stack.currentIndex() == 0
    assert window.point_tab.search_edit.hasFocus()
    assert window.stack.widget(0) is window.point_tab
    loc = window.spot_store.location()
    loc.name = "一个很长的观测点名称" * 6
    window.spot_store.set_location(loc, persist=False)
    window.resize(1120, 720)
    app.processEvents()
    assert window.width() == 1120
    assert loc.name in window._subtitle.toolTip()
    assert window.point_tab._spot_name_label.toolTip() == loc.name
    window.close()
