"""Desktop preflight and download regressions; all providers are mocked."""

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget

from desktop import data_access, widgets
from firesky import web_server as engine


@pytest.fixture(scope="module")
def app(qt_app):
    return qt_app


@pytest.fixture
def config(tmp_path):
    return engine.WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs", ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams", satellite_cache_dir=tmp_path / "hsd",
    )


def point():
    return dict(
        name="杭州", lat=30.2741, lon=120.1551, elevation_m=8,
        timezone_name="Asia/Shanghai", target_date="2026-11-08",
        phenomenon="dusk", model_source="gfs",
    )


def test_point_plan_matches_execution_domain(config):
    context = data_access.model_context(point(), config=config)
    normalized = engine._point_cache_identity_payload(point())
    expected = engine.national_gfs_bbox(
        [{"lat": normalized["lat"], "lon": normalized["lon"]}],
        light_path_padding_km=engine._national_gfs_source_radius_km(
            engine.NATIONAL_ANALYSIS_RADIUS_KM
        ),
    )
    assert context["bbox"] == expected
    assert context["area"] == engine.national_cams_area(expected)
    assert context["terrain_pending"]
    assert context["hours"]
    assert context["common"]["_model_cycle_override"] == context["cycle"].isoformat()


def test_missing_data_is_not_ready(config):
    report = data_access.check_model_data(point(), probe=False, config=config)
    assert not report["ready"]
    assert {row["source"] for row in report["datasets"]} == {"gfs", "cams"}
    assert all(row["missing_count"] > 0 for row in report["datasets"])
    assert all(not row["verified"] for row in report["datasets"])
    assert "未核验" in data_access.status_text(report)


def test_zero_length_files_do_not_count_as_downloaded(config):
    for source, name in (
        (config.gfs_cache_dir, "empty.grib2"),
        (config.ecmwf_cache_dir, "ifs-basic-v2-2026110800-f009.grib2"),
        (config.cams_cache_dir, "cams-optics-v1-2026-11-08-0000-009-global.grib"),
    ):
        source.mkdir(parents=True)
        (source / name).touch()
    assert all(row["file_count"] == 0 for row in data_access.inventory(config))


def test_heuristic_publication_is_never_labelled_verified(monkeypatch):
    from firesky import cycle_availability as cycles
    when = datetime(2026, 11, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(cycles, "query_latest_gfs_cycle", lambda now, **kw: when)
    monkeypatch.setattr(cycles, "get_cycle_source", lambda source: "heuristic")
    result = data_access.published_cycles(["gfs"])
    assert result["gfs"]["cycle"] == when.isoformat()
    assert not result["gfs"]["verified"]


def test_cycle_overrides_are_used_per_source(monkeypatch):
    class Cycles(dict):
        cycle_sources = {}
    monkeypatch.setattr(engine, "_cached_latest_cycles", lambda now: Cycles({
        source: datetime(2026, 11, 7, tzinfo=timezone.utc)
        for source in ("gfs", "ecmwf", "cams")
    }))
    plan = engine.handle_data_requirements({
        "region": "china", "target_dates": ["2026-11-09"],
        "sources": ["gfs", "cams"], "phenomena": ["dusk"],
        "cycle_overrides": {"gfs": "2026-11-08T06:00:00Z", "cams": "2026-11-08T00:00:00Z"},
    })
    sources = {d["source"]: d for d in plan["datasets"]}
    assert sources["gfs"]["latest_cycle"] == "2026-11-08 06Z"
    assert sources["cams"]["latest_cycle"] == "2026-11-08 00Z"


def test_preparation_uses_engine_prefetch_not_scoring(monkeypatch, config):
    seen = []
    monkeypatch.setattr(
        engine, "_prefetch_national_inputs",
        lambda points, common, cfg, progress: seen.append((points, common, cfg)),
    )
    outcome = data_access.prepare_model_data(point(), config=config)
    assert outcome["status"] == "ready"
    assert len(seen) == 1
    assert len(seen[0][0]) == 1
    assert seen[0][2] is config


def test_changed_request_cannot_trigger_old_callback(app):
    from desktop.data_panel import ForecastDataPanel
    panel = ForecastDataPanel()
    invoked = []
    panel._current = lambda: False
    panel._finish(lambda result: invoked.append(result), {})
    assert not invoked
    assert "参数已变更" in panel.summary.text()
    panel.close()


def test_failed_check_does_not_run_forecast(app):
    from desktop.data_panel import ForecastDataPanel
    panel = ForecastDataPanel()
    invoked = []
    panel._callback = lambda: invoked.append(True)
    panel._failed("离线")
    assert not invoked
    assert "未就绪" in panel.summary.text()
    panel.close()


def test_missing_data_confirmation_can_be_cancelled(app, monkeypatch):
    from desktop.data_panel import ForecastDataPanel
    panel = ForecastDataPanel()
    invoked = []
    monkeypatch.setattr(panel, "_launch", lambda *args: None)
    panel.begin(point(), callback=invoked.append)
    monkeypatch.setattr(panel, "_prepare", lambda: invoked.append("prepare"))
    row = dict(
        source="gfs", ready=False, missing_count=3, request_cycle="2026-11-08 00Z",
        verified=False, is_latest=False, latest_available_cycle="未核验",
        latest_local_cycle="",
    )
    panel._checked({"ready": False, "datasets": [row]})
    assert not panel._dialog.confirm_button.isEnabled()
    panel._dialog.accept()
    panel._dialog.reject()
    assert not invoked
    panel.close()


def test_checked_cycle_is_pinned_for_actual_execution(app, monkeypatch):
    from desktop.data_panel import ForecastDataPanel
    panel = ForecastDataPanel()
    captured = []
    monkeypatch.setattr(panel, "_launch", lambda *args: None)
    panel.begin(point(), callback=captured.append)
    row = dict(
        source="gfs", ready=True, missing_count=0, request_cycle="2026-11-08 00Z",
        verified=True, is_latest=True, latest_available_cycle="2026-11-08 00Z",
        latest_local_cycle="2026-11-08 00Z",
    )
    panel._checked(dict(
        ready=True, datasets=[row], model_cycle="2026-11-08T00:00:00+00:00", model_source="gfs",
    ))
    assert not captured
    assert panel._dialog.confirm_button.isEnabled()
    panel._dialog.confirm_button.click()
    assert captured[0]["_gfs_cycle_override"] == "2026-11-08T00:00:00+00:00"
    panel.close()


def test_current_location_uses_system_coordinate_and_accuracy(app):
    from PySide6.QtCore import QDateTime
    from PySide6.QtPositioning import QGeoCoordinate, QGeoPositionInfo
    from desktop.location_controller import CurrentLocationController
    controller = CurrentLocationController()
    captured = []
    controller.located.connect(lambda lat, lon, accuracy: captured.append((lat, lon, accuracy)))
    position = QGeoPositionInfo(QGeoCoordinate(31.25, 121.5), QDateTime.currentDateTimeUtc())
    position.setAttribute(QGeoPositionInfo.Attribute.HorizontalAccuracy, 42)
    controller._busy = True
    controller._position(position)
    assert captured == [(31.25, 121.5, 42)]
    assert not controller._busy


def test_expired_system_location_is_rejected(app):
    from PySide6.QtCore import QDateTime
    from PySide6.QtPositioning import QGeoCoordinate, QGeoPositionInfo
    from desktop.location_controller import CurrentLocationController
    controller = CurrentLocationController()
    captured, errors = [], []
    controller.located.connect(lambda *args: captured.append(args))
    controller.failed.connect(errors.append)
    controller._busy = True
    controller._position(QGeoPositionInfo(
        QGeoCoordinate(31, 121), QDateTime.currentDateTimeUtc().addSecs(-600)
    ))
    assert not captured
    assert "过期" in errors[0]


def test_settings_are_separate_from_download_page(app, monkeypatch, tmp_path):
    from desktop import ui_datasource
    from desktop.ui_data import DataSourceTab
    monkeypatch.setattr(widgets, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(ui_datasource, "CDSAPIRC", tmp_path / ".cdsapirc")
    settings = ui_datasource.SettingsPanel()
    data = DataSourceTab()
    assert settings.findChildren(type(settings.key_edit))
    assert not any(
        field.echoMode() == field.EchoMode.Password
        for field in data.findChildren(type(settings.key_edit))
    )
    assert not hasattr(settings, "download_button")
    data.date_edit.setDate(datetime(2026, 11, 9).date())
    payload = data.download_payload()
    assert payload["target_dates"] == ["2026-11-09"]
    assert "cams" in payload["sources"]
    assert payload["spacing_deg"] == .25
    settings.close()
    data.close()


def test_ads_key_save_preserves_other_settings(app, monkeypatch, tmp_path):
    from desktop import ui_datasource
    rc = tmp_path / ".cdsapirc"
    rc.write_text("url: old\nkey: old\nverify: false\n# keep\n")
    monkeypatch.setattr(widgets, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(ui_datasource, "CDSAPIRC", rc)
    settings = ui_datasource.SettingsPanel()
    settings.key_edit.setText("test-key")
    settings._save_key()
    assert "verify: false" in rc.read_text()
    assert "# keep" in rc.read_text()
    assert rc.stat().st_mode & 0o777 == 0o600
    settings.close()


class GridMap(QWidget):
    def __init__(self):
        super().__init__()
        self.points = []
    def set_grid(self, points, spacing=.25):
        self.points = points
        self.spacing = spacing
    def fit_bounds(self, *args):
        pass
    def focus_grid_cell(self, *args):
        pass


def test_regional_uses_quarter_degree_grid_and_all_map_points(app, monkeypatch):
    from desktop import ui_regional
    monkeypatch.setattr(ui_regional, "FireSkyMap", GridMap)
    page = ui_regional.RegionalForecastTab()
    captured = []
    monkeypatch.setattr(page.data_panel, "begin", lambda payload, **kw: captured.append(payload))
    page.region_checks["china"].setChecked(True)
    page._run()
    assert captured[0]["spacing_deg"] == .25
    assert {"china", "east-asia"}.issubset(captured[0]["regions"])
    points = [
        dict(name=f"point-{i}", lat=30 + (i // 100) * .25, lon=100 + (i % 100) * .25,
             peak_quality_index=.1)
        for i in range(2100)
    ]
    page.render_result(dict(
        points=points, point_count=2101, success_count=2100,
        errors=[dict(name="missing", lat=40, lon=120, error="missing source")],
    ))
    assert len(page._regional_map.points) == 2101
    assert page._regional_map.points[-1]["peak_quality_index"] is None
    assert page.table.rowCount() == 2000
    assert page.spacing_spin.value() == .25
    page.close()


def test_download_failure_is_not_a_success(monkeypatch):
    from desktop.workers import DataFetchWorker
    monkeypatch.setattr(data_access, "published_cycles", lambda sources: {})
    monkeypatch.setattr(engine, "handle_data_fetch", lambda payload, cfg: {"job_id": "test"})
    monkeypatch.setattr(engine, "handle_data_fetch_status", lambda job: {
        "status": "failed", "errors": ["provider unavailable"],
    })
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        DataFetchWorker({"sources": ["gfs"]}).execute()


def test_warmup_always_checks_prior_even_on_satellite_cache_hit(monkeypatch, config):
    from firesky import satellite_cloud_column as satellite
    from firesky import aerosol
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "python")
    request = SimpleNamespace(point=SimpleNamespace(lat=30, lon=120), radius_km=900)
    when = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
    frames = [SimpleNamespace(valid_time_utc=when), SimpleNamespace(valid_time_utc=when - __import__("datetime").timedelta(minutes=10))]
    monkeypatch.setattr(data_access, "nowcast_context", lambda payload, cfg: dict(
        request=request, config=config, lookup=when, times=[when], cycle=when,
        region="china", area=[40, 110, 20, 130],
    ))
    monkeypatch.setattr(satellite.AutoHsdCompactCacheProvider, "prepare_series", lambda *a, **kw: frames)
    called = []
    monkeypatch.setattr(engine, "_warm_nowcast_gfs_prior", lambda *a, **kw: (
        called.append(kw) or {"errors": [], "warmed": ["f000"]}
    ))
    monkeypatch.setattr(aerosol.CamsAdsAerosolProvider, "download_targets_batch", lambda self, area, targets, **kw: set(targets))
    result = data_access.prepare_nowcast_data({}, config=config)
    assert called[0]["cycle_override"] == when
    assert len(result["valid_times"]) == 2


def test_rust_nowcast_preparation_uses_native_provider_boundary(monkeypatch, config):
    from firesky import desktop_data, rust_desktop_data, satellite_cloud_column

    request = SimpleNamespace(
        point=SimpleNamespace(lat=30, lon=120), radius_km=900,
    )
    when = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
    frames = [
        SimpleNamespace(valid_time_utc=when),
        SimpleNamespace(valid_time_utc=when - __import__("datetime").timedelta(minutes=10)),
    ]
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setattr(desktop_data, "nowcast_context", lambda payload, cfg: dict(
        request=request, config=config, lookup=when, times=[when],
        cycle=when, region="china", area=[40, 110, 20, 130],
        cams_valid_times=[when],
    ))
    monkeypatch.setattr(
        satellite_cloud_column.AutoHsdCompactCacheProvider,
        "prepare_series",
        lambda *a, **kw: frames,
    )
    native_calls = []
    monkeypatch.setattr(
        rust_desktop_data,
        "prepare_nowcast_data",
        lambda payload, **kwargs: (
            native_calls.append((payload, kwargs))
            or {
                "contract": "rust-desktop-nowcast-prepare-v1",
                "runtime_python_used": False,
                "status": "ready",
                "hsd": {"frames": [{}, {}]},
                "cams": {"missing_valid_times": []},
            }
        ),
    )
    result = desktop_data.prepare_nowcast_data({}, config=config)
    assert native_calls
    assert native_calls[0][1]["config"] is config
    assert result["native"]["runtime_python_used"] is False
    assert result["valid_times"] == [item.valid_time_utc.isoformat() for item in frames]
