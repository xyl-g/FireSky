"""Latency and information-density contracts for the source checklist."""

from datetime import datetime, timezone
from threading import Event

import pytest

from desktop import data_access
from firesky import aerosol, web_server as engine


def payload():
    return dict(
        model_source="both", name="杭州", lat=30.2741, lon=120.1551,
        elevation_m=8, timezone_name="Asia/Shanghai",
        target_date="2026-11-08", phenomenon="dusk",
    )


def test_fast_scan_never_waits_for_upstream_or_decodes_terrain(monkeypatch, tmp_path):
    from firesky import ecmwf_ifs
    def forbidden(*a, **kw):
        raise AssertionError("slow operation on local checklist path")
    monkeypatch.setattr(data_access, "published_cycles", forbidden)
    monkeypatch.setattr(engine, "_ecmwf_point_cycle", forbidden)
    monkeypatch.setattr(engine.GfsNomadsClient, "load_decoded_subset", forbidden)
    monkeypatch.setattr(ecmwf_ifs, "decode_ecmwf_ifs_basic_tensor", forbidden)
    config = engine.WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs", ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
    )
    result = data_access.check_model_data(payload(), probe=False, fast=True, config=config)
    assert [row["source"] for row in result["datasets"]] == ["gfs", "ecmwf", "cams"]
    assert not result["ready"]


@pytest.mark.parametrize("same_area", [True, False])
def test_pair_reuses_one_cams_scan_and_combines_required_coverage(monkeypatch, tmp_path, same_area):
    # This unit test injects Python scanner callbacks; native parity is separate.
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "python")
    when = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
    counts, paths = {}, []
    def scan(source):
        counts[source] = counts.get(source, 0) + 1
        return dict(file_count=0, latest_cycle="", cycles={})
    def context(payload, **kw):
        model = payload["model_source"]
        return dict(
            common={"model_source": model, "_compute_region": None},
            cycle=when.replace(hour=0), bbox=(110, 120, 40, 20), hours=[9],
            area=[40, 110, 20, 120] if same_area or model == "gfs" else [41, 109, 19, 121],
            times=[when], points=[{}], terrain_pending=True,
        )
    def bracket(*a, **kw):
        paths.append((id(kw["candidate_paths"]), kw["validate_contents"]))
        return None
    monkeypatch.setattr(data_access, "model_context", context)
    monkeypatch.setattr(engine, "_scan_gfs_local", lambda root: scan("gfs"))
    monkeypatch.setattr(engine, "_scan_ecmwf_local", lambda root: scan("ecmwf"))
    monkeypatch.setattr(engine, "_scan_cams_local", lambda root: scan("cams"))
    monkeypatch.setattr(aerosol, "find_cached_cams_bracket", bracket)
    config = engine.WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs", ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
    )
    result = data_access.check_model_data(payload(), probe=False, fast=True, config=config)
    assert counts == {"gfs": 1, "ecmwf": 1, "cams": 1}
    assert len({identity for identity, _ in paths}) == 1
    assert all(not validate for _, validate in paths)
    cams = result["datasets"][-1]
    assert cams["missing_count"] == (1 if same_area else 2)
    assert not cams["ready"]
    assert len(result["datasets"]) == 3


def test_cams_ignores_other_times_before_reading_geographic_headers(monkeypatch, tmp_path):
    target = datetime(2026, 11, 8, 9, tzinfo=timezone.utc)
    area = [40, 110, 20, 120]
    key = aerosol.cams_area_key(area)
    new = tmp_path / f"cams-optics-v2-2026-11-08-0600-003-{key}.grib"
    old = tmp_path / f"cams-optics-v2-2026-11-08-0000-009-{key}.grib"
    unrelated = tmp_path / "cams-optics-v2-2026-11-07-0000-003-other.grib"
    for path in (new, old, unrelated):
        path.write_bytes(b"fixture")
    inspected = []
    monkeypatch.setattr(
        aerosol, "_cams_optics_file_valid",
        lambda path: inspected.append(path) or path == old,
    )
    def no_unrelated_headers(*a, **kw):
        raise AssertionError("unrelated header should not be read")
    monkeypatch.setattr(aerosol, "cams_file_covers_area", no_unrelated_headers)
    result = aerosol.find_cached_cams_bracket(tmp_path, area, target)
    assert result.left.path == old
    assert inspected == [new, old]  # strict default still rejects malformed newest data
    inspected.clear()
    quick = aerosol.find_cached_cams_bracket(tmp_path, area, target, validate_contents=False)
    assert quick.left.path == new
    assert not inspected


def test_upstream_query_does_not_block_local_result_or_confirm(qt_app, monkeypatch):
    from desktop import data_panel
    from test_desktop_data_dialog import report, wait_for
    release, started = Event(), Event()
    seen = []
    def publication(sources):
        started.set()
        release.wait(3)
        return {"gfs": dict(verified=True, label="2026-11-08 06Z", cycle="2026-11-08T06:00:00Z")}
    monkeypatch.setattr(data_panel, "published_cycles", publication)
    monkeypatch.setattr(data_panel, "check_model_data", lambda *a, **kw: report())
    panel = data_panel.ForecastDataPanel()
    try:
        panel.begin(payload(), callback=seen.append)
        wait_for(lambda: panel._report is not None and started.is_set())
        assert panel._active is None
        assert panel._dialog.confirm_button.isEnabled()
        panel._dialog.confirm_button.click()
        release.set()
        panel.shutdown()
        assert len(seen) == 1
        assert not panel._session_active
    finally:
        release.set()
        panel.shutdown()
        panel.close()


def test_checklist_has_three_rows_and_no_log_dump(qt_app):
    from desktop.data_dialog import ForecastDataDialog
    from test_desktop_data_dialog import report
    dialog = ForecastDataDialog(payload())
    fixture = report(ready=False)
    fixture["datasets"] *= 2
    for source in ("ecmwf", "cams", "cams"):
        row = dict(fixture["datasets"][0], source=source)
        fixture["datasets"].append(row)
    dialog.show_report(fixture)
    assert list(dialog.rows) == ["gfs", "ecmwf", "cams"]
    assert not hasattr(dialog, "table")
    assert not hasattr(dialog, "details")
    dialog.show_progress({"message": "CAMS · lots of internal details f009 2026-11-08 long-path", "percent": 40})
    assert dialog.state_label.text() == "正在准备 CAMS"
    assert dialog.percent.text() == "40%"
    dialog.close()
