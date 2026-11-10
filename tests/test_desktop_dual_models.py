"""Dual-source desktop workflows, without downloads or real scoring."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from desktop import data_access, workers, widgets
from desktop.data_panel import ForecastDataPanel
from desktop.ui_point import PointForecastTab
from desktop.ui_regional import RegionalForecastTab
from firesky import web_server as engine


@pytest.mark.parametrize("regional", [False, True])
def test_both_models_use_their_authoritative_paths(monkeypatch, qt_app, regional):
    seen, progress = [], []
    def run(payload, config, *, progress_callback):
        seen.append(dict(payload))
        progress_callback({"percent": 50, "message": "scoring"})
        return {"peak_quality_index": 0.4 if payload["model_source"] == "gfs" else 0.9}
    monkeypatch.setattr(
        engine, "run_and_cache_national_forecast_payload" if regional else "run_forecast_payload",
        run,
    )
    worker = (workers.RegionalForecastWorker if regional else workers.ForecastWorker)({
        "model_source": "both", "_gfs_cycle_override": "gfs-cycle",
        "_ec_cycle_override": "ec-cycle",
    })
    worker.progress.connect(progress.append)
    outcome = worker.execute()
    assert [p["model_source"] for p in seen] == ["gfs", "ec"]
    assert seen[0]["_gfs_cycle_override"] == "gfs-cycle"
    assert seen[1]["_ec_cycle_override"] == "ec-cycle"
    assert outcome["model_results"]["gfs"]["peak_quality_index"] == 0.4
    assert outcome["model_results"]["ec"]["peak_quality_index"] == 0.9
    assert not outcome["model_errors"]
    percentages = [p["percent"] for p in progress]
    assert percentages == sorted(percentages)


def test_one_source_failure_is_not_a_fake_second_result():
    def run(payload, progress):
        if payload["model_source"] == "ec":
            raise RuntimeError("EC unavailable")
        return {"peak_quality_index": 0.4}
    outcome = workers._run_model_pair({"model_source": "both"}, run, lambda _: None)
    assert set(outcome["model_results"]) == {"gfs"}
    assert outcome["model_errors"] == {"ec": "EC unavailable"}


def test_cancel_prevents_the_second_model():
    seen = []
    def run(payload, progress):
        seen.append(payload["model_source"])
        raise engine.ForecastCancelled("cancel")
    with pytest.raises(engine.ForecastCancelled):
        workers._run_model_pair({"model_source": "both"}, run, lambda _: None)
    assert seen == ["gfs"]


def test_both_failures_are_reported():
    def run(payload, progress):
        raise RuntimeError(payload["model_source"])
    with pytest.raises(RuntimeError, match="GFS.*EC"):
        workers._run_model_pair({"model_source": "both"}, run, lambda _: None)


def test_dual_preflight_pins_both_cycles(qt_app, monkeypatch):
    panel = ForecastDataPanel()
    seen = []
    monkeypatch.setattr(panel, "_launch", lambda *args: None)
    panel.begin({"model_source": "both"}, callback=seen.append)
    panel._checked({
        "ready": True, "datasets": [{"is_latest": True, "source": "gfs",
            "ready": True, "verified": True, "request_cycle": "a",
            "latest_local_cycle": "a", "latest_available_cycle": "a"}],
        "model_cycles": {"gfs": "a", "ec": "b"},
    })
    assert not seen
    panel._dialog.confirm_button.click()
    assert seen == [{"model_source": "both", "_gfs_cycle_override": "a", "_ec_cycle_override": "b"}]
    panel.close()


def test_pair_preflight_checks_both_sources(tmp_path, monkeypatch):
    from firesky import ecmwf_point
    monkeypatch.setenv("FIRESKY_ECMWF_DATA_BACKEND", "python")
    monkeypatch.setattr(ecmwf_point, "ecmwf_artifact_available", lambda *a, **kw: True)
    config = engine.WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs", ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
    )
    report = data_access.check_model_data({
        "model_source": "both", "name": "杭州", "lat": 30.27, "lon": 120.15,
        "elevation_m": 8, "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-08", "phenomenon": "dusk",
    }, probe=False, config=config)
    assert not report["ready"]
    assert set(report["model_cycles"]) == {"gfs", "ec"}
    assert {row["source"] for row in report["datasets"]} == {"gfs", "ecmwf", "cams"}
    assert len(report["datasets"]) == 3
    cams = next(row for row in report["datasets"] if row["source"] == "cams")
    assert {part["for_model"] for part in cams["requirements"]} == {"gfs", "ec"}


def test_point_result_switching_never_changes_request(qt_app, monkeypatch, tmp_path):
    from desktop import ui_point
    monkeypatch.setattr(ui_point, "FireSkyMap", None)
    monkeypatch.setattr(widgets, "CONFIG_FILE", tmp_path / "config.json")
    page = PointForecastTab(widgets.SpotStore(widgets.DEFAULT_LOCATIONS[0].copy()))
    assert page._payload_for_data()["model_source"] == "both"
    assert not hasattr(page, "model_combo")
    assert page.dawn_btn.text() == "朝霞"
    assert page.dusk_btn.text() == "晚霞"
    page._running_context = page._context()
    page._on_done({"model_results": {
        "gfs": {"peak_quality_index": 0.4, "steps": []},
        "ec": {"peak_quality_index": 0.9, "steps": []},
    }, "model_errors": {}})
    assert page.quality_big.text() == "0.40"
    assert page._peak_window.text() == "推荐窗口未提供"
    assert page._hero_caption.text() == "峰值质量指数 · 置信度未提供"
    page.model_results_bar.tabs.set_index(1)
    assert page.quality_big.text() == "0.90"
    assert page._result_context == page._context()
    page._on_done({"model_results": {
        "gfs": {"peak_quality_index": 0.2, "steps": []},
    }, "model_errors": {"ec": "not available"}})
    assert not page.model_results_bar.tabs._buttons[1].isEnabled()
    assert page._hero_badge.text() == "部分完成"
    assert page.quality_big.text() == "0.20"
    page.shutdown()
    page.close()


def test_region_submits_both_models_on_quarter_degree_grid(qt_app, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from desktop import ui_regional
    class GridMap(QWidget):
        def set_grid(self, *args, **kwargs):
            pass
        def fit_bounds(self, *args):
            pass
    monkeypatch.setattr(ui_regional, "FireSkyMap", GridMap)
    page = RegionalForecastTab()
    assert page._payload_for_data()["model_source"] == "both"
    assert page._payload_for_data()["spacing_deg"] == 0.25
    assert not hasattr(page, "model_combo")
    page.shutdown()
    page.close()
