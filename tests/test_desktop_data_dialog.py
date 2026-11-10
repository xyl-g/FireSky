"""Data review is explicit, including after asynchronous downloads."""

import copy
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest

from desktop import data_panel


def report(*, ready=True, latest=True, verified=True, satellite=False):
    result = {
        "ready": ready,
        "datasets": [{
            "source": "gfs", "ready": ready, "missing_count": 0 if ready else 2,
            "missing_hours": [] if ready else [6, 9],
            "request_cycle": "2026-11-08 00Z",
            "latest_local_cycle": "2026-11-08 00Z" if ready else "",
            "latest_available_cycle": "2026-11-08 00Z" if latest else "2026-11-08 06Z",
            "latest_available_cycle_utc": "2026-11-08T00:00:00+00:00" if latest else "2026-11-08T06:00:00+00:00",
            "verified": verified, "is_latest": latest and verified and ready,
        }],
        "model_cycle": "2026-11-08T00:00:00+00:00", "model_source": "gfs",
    }
    if satellite:
        result.update(
            satellite_frames=2 if ready else 1, satellite_reference="2026-11-08T09:00:00Z",
            satellite_valid_times=["2026-11-08T09:00:00Z"],
            reference_time_utc="2026-11-08T09:05:00Z",
        )
    return result


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def panel(qt_app):
    panel = data_panel.ForecastDataPanel()
    yield panel
    panel.shutdown()
    panel.close()


@pytest.mark.parametrize("ready,latest,verified", [
    (True, True, True), (True, False, True), (True, False, False), (False, False, True),
])
def test_every_forecast_requires_dialog_confirmation(panel, monkeypatch, ready, latest, verified):
    seen = []
    monkeypatch.setattr(data_panel, "check_model_data", lambda *a, **kw: report(
        ready=ready, latest=latest, verified=verified,
    ))
    panel.begin({"model_source": "gfs"}, callback=seen.append)
    assert panel._dialog.isVisible()
    assert not panel._dialog.confirm_button.isEnabled()
    wait_for(lambda: panel._report is not None)
    assert not seen
    assert panel._dialog.confirm_button.isEnabled() == ready
    panel._dialog.confirm_button.click()
    assert len(seen) == int(ready)
    if ready:
        panel._confirm()
        assert len(seen) == 1


@pytest.mark.parametrize("nowcast,regional", [(False, False), (False, True), (True, False)])
def test_update_rechecks_then_waits_for_explicit_confirmation(qt_app, monkeypatch, nowcast, regional):
    panel = data_panel.ForecastDataPanel(nowcast=nowcast, regional=regional)
    downloaded, checked, seen = [], [], []

    def check(payload, **kw):
        checked.append(copy.deepcopy(payload))
        return report(ready=bool(downloaded), satellite=nowcast)

    def prepare(payload, **kw):
        downloaded.append(copy.deepcopy(payload))
        kw["progress"]({"percent": 100, "message": "下载完成"})
        return {"status": "ready"}

    monkeypatch.setattr(data_panel, "check_nowcast_data" if nowcast else "check_model_data", check)
    monkeypatch.setattr(data_panel, "prepare_nowcast_data" if nowcast else "prepare_model_data", prepare)
    try:
        panel.begin({"model_source": "gfs"}, callback=seen.append)
        wait_for(lambda: panel._report is not None)
        assert not panel._dialog.confirm_button.isEnabled()
        panel._dialog.update_button.click()
        assert not panel._dialog.confirm_button.isEnabled()
        wait_for(lambda: bool(panel._report and panel._report["ready"]))
        assert len(checked) == 2  # reuse initial plan; only recheck after download
        assert downloaded[0]["_gfs_cycle_override"] == report()["model_cycle"]
        assert checked[-1]["_gfs_cycle_override"] == report()["model_cycle"]
        assert not seen
        assert panel._dialog.isVisible()
        assert panel._dialog.state_label.text() == "数据已就绪"
        panel._dialog.confirm_button.click()
        assert len(seen) == 1
        if nowcast:
            assert seen[0]["reference_time_utc"] == "2026-11-08T09:05:00Z"
    finally:
        panel.shutdown()
        panel.close()


@pytest.mark.parametrize("failure", ["download", "incomplete", "recheck"])
def test_unsuccessful_update_never_enables_forecast(panel, monkeypatch, failure):
    downloaded, seen = [], []

    def check(*a, **kw):
        if downloaded and failure == "recheck":
            raise RuntimeError("重新检查失败")
        return report(ready=False)

    def prepare(*a, **kw):
        downloaded.append(True)
        if failure == "download":
            raise RuntimeError("ADS 权限不足")
        return {"status": "ready"}

    monkeypatch.setattr(data_panel, "check_model_data", check)
    monkeypatch.setattr(data_panel, "prepare_model_data", prepare)
    panel.begin({}, callback=seen.append)
    wait_for(lambda: panel._report is not None)
    panel._dialog.update_button.click()
    wait_for(lambda: bool(downloaded) and panel._active is None)
    assert not panel._dialog.confirm_button.isEnabled()
    assert panel._dialog.update_button.isEnabled()
    assert not seen


def test_close_during_worker_ignores_late_success(panel, monkeypatch):
    from threading import Event
    release = Event()
    seen = []

    def check(*a, **kw):
        release.wait(2)
        return report()

    monkeypatch.setattr(data_panel, "check_model_data", check)
    panel.begin({}, callback=seen.append)
    panel._dialog.reject()
    release.set()
    wait_for(lambda: panel._active is None)
    panel._confirm()
    assert not seen
    assert not panel._session_active
    assert not panel._dialog.isVisible()


def test_close_during_download_never_rechecks_or_forecasts(panel, monkeypatch):
    from threading import Event
    started, release = Event(), Event()
    checks, seen = [], []

    def check(*a, **kw):
        checks.append(True)
        return report(ready=False)

    def prepare(*a, **kw):
        started.set()
        release.wait(2)
        return {"status": "ready"}

    monkeypatch.setattr(data_panel, "check_model_data", check)
    monkeypatch.setattr(data_panel, "prepare_model_data", prepare)
    panel.begin({}, callback=seen.append)
    wait_for(lambda: panel._report is not None)
    panel._dialog.update_button.click()
    wait_for(started.is_set)
    panel._dialog.reject()
    release.set()
    wait_for(lambda: panel._active is None)
    assert len(checks) == 1
    assert not seen


def test_final_confirmation_uses_updated_cycles_and_strips_download_options(panel, monkeypatch):
    seen, downloaded = [], []
    updated = "2026-11-08T06:00:00+00:00"

    def check(payload, **kw):
        result = report(latest=False)
        if kw.get("refresh") or payload.get("_gfs_cycle_override") == updated:
            result["model_cycle"] = updated
            result["cams_update_cycle"] = updated
            result["datasets"][0].update(
                request_cycle="2026-11-08 06Z", is_latest=True,
            )
        return result

    monkeypatch.setattr(data_panel, "check_model_data", check)
    monkeypatch.setattr(
        data_panel, "prepare_model_data",
        lambda payload, **kw: downloaded.append(payload) or {"status": "ready"},
    )
    panel.begin({}, callback=seen.append)
    wait_for(lambda: panel._report is not None)
    panel._dialog.update_button.click()
    wait_for(lambda: bool(downloaded) and panel._report is not None)
    assert not seen
    assert downloaded[0]["_gfs_cycle_override"] == updated
    assert downloaded[0]["_cams_refresh_cycle"] == updated
    panel._dialog.confirm_button.click()
    assert seen[0]["_gfs_cycle_override"] == updated
    assert "_cams_refresh_cycle" not in seen[0]


def test_prepare_shortcut_reviews_requirements_before_downloading(panel, monkeypatch):
    downloaded = []
    monkeypatch.setattr(data_panel, "check_model_data", lambda *a, **kw: report(ready=False))
    monkeypatch.setattr(data_panel, "prepare_model_data", lambda *a, **kw: downloaded.append(True))
    panel.begin({}, prepare=True)
    wait_for(lambda: panel._report is not None)
    assert not downloaded
    assert panel._dialog.isVisible()
    assert panel._dialog.confirm_button.text() == "完成"


def test_changed_parameters_prevent_confirmation(panel, monkeypatch):
    monkeypatch.setattr(data_panel, "check_model_data", lambda *a, **kw: report())
    current = [True]
    seen = []
    panel.begin({}, callback=seen.append, is_current=lambda: current[0])
    wait_for(lambda: panel._report is not None)
    current[0] = False
    panel._dialog.confirm_button.click()
    assert not seen
    assert not panel._dialog.confirm_button.isEnabled()
    assert "参数已变更" in panel._dialog.error_label.text()


def test_dialog_shows_only_source_status_not_raw_plan(panel, monkeypatch):
    monkeypatch.setattr(data_panel, "check_model_data", lambda *a, **kw: report(ready=False))
    panel.begin({})
    wait_for(lambda: panel._report is not None)
    row = panel._dialog.rows["gfs"]
    assert row.name.text() == "GFS"
    assert "缺少 2" in row.caption.text()
    assert row.status.text() == "需下载"
    assert not hasattr(panel._dialog, "table")
    assert not hasattr(panel._dialog, "details")
    panel._dialog.reject()
