"""Refresh applicable cycles without deleting or future-dating caches."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from desktop import data_access
from firesky import aerosol, web_server as engine


def utc(hour):
    return datetime(2026, 11, 8, hour, tzinfo=timezone.utc)


@pytest.mark.parametrize("source,event_hour,new_hour,available,expected_hour", [
    ("gfs", 12, 6, True, 6),
    ("gfs", 6, 12, True, 0),
    ("ec", 18, 12, True, 12),
    ("ec", 18, 12, False, 0),
])
def test_refresh_uses_new_cycle_only_when_event_steps_are_applicable(
    monkeypatch, tmp_path, source, event_hour, new_hour, available, expected_hour,
):
    from firesky import ecmwf_point
    config = engine.WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs", ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
    )

    def context(payload, **kw):
        cycle = datetime.fromisoformat(payload[f"_{source}_cycle_override"]) if payload.get(f"_{source}_cycle_override") else utc(0)
        return dict(
            cycle=cycle, times=[utc(event_hour)], hours=[event_hour - cycle.hour],
            common={"model_source": source, "_compute_region": None},
            bbox=(110, 120, 40, 20), area=[40, 110, 20, 120],
            points=[{}], terrain_pending=False,
        )

    monkeypatch.setattr(data_access, "model_context", context)
    monkeypatch.setattr(data_access, "published_cycles", lambda sources: {
        key: dict(cycle=utc(new_hour).isoformat(), label=f"2026-11-08 {new_hour:02}Z", verified=True)
        for key in sources
    })
    monkeypatch.setattr(ecmwf_point, "ecmwf_artifact_available", lambda *a, **kw: available)
    result = data_access.check_model_data({"model_source": source}, config=config, refresh=True)
    assert result["model_cycle"] == utc(expected_hour).isoformat()
    assert result["cams_update_cycle"] == utc(new_hour).isoformat()
    assert not result["ready"]  # no local files, even when a release is verified
    if expected_hour == 0:
        assert result["notes"]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("case", ["old", "current", "historical"])
def test_cams_refresh_bypasses_only_applicable_old_cache(monkeypatch, tmp_path, batch, case):
    import cdsapi
    monkeypatch.setenv("FIRESKY_CAMS_DATA_BACKEND", "python")
    target = utc(9) if case != "historical" else utc(3)
    cache_cycle = utc(0) if case != "current" else utc(6)
    path = tmp_path / "old.grib"
    path.touch()
    selection = SimpleNamespace(
        path=path, run_date=cache_cycle.date(), run_time=cache_cycle.strftime("%H:%M"),
        lead_hours=int((target - cache_cycle).total_seconds() / 3600), run_time_utc=cache_cycle,
    )
    bracket = SimpleNamespace(left=selection, right=selection)
    monkeypatch.setattr(aerosol, "find_cached_cams_bracket", lambda *a, **kw: bracket)
    reached_network = []

    def client(**kw):
        reached_network.append(True)
        raise RuntimeError("mock download reached")

    monkeypatch.setattr(cdsapi, "Client", client)
    provider = aerosol.CamsAdsAerosolProvider(cache_dir=tmp_path)

    def download():
        if batch:
            return provider.download_targets_batch(
                [40, 110, 20, 120], [target], require_exact_valid_time=True,
                refresh_before_cycle=utc(6),
            )
        return provider.download_target(
            [40, 110, 20, 120], target, require_exact_valid_time=True,
            refresh_before_cycle=utc(6),
        )

    if case == "old":
        with pytest.raises(RuntimeError, match="mock download"):
            download()
        assert reached_network
    else:
        outcome = download()
        assert outcome == ({target: path} if batch else path)
        assert not reached_network
    assert path.exists()


def test_cams_refresh_is_forwarded_to_batch_and_fallback(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(
        aerosol.CamsAdsAerosolProvider, "download_targets_batch",
        lambda self, area, targets, **kw: seen.append(kw) or {},
    )
    monkeypatch.setattr(
        aerosol.CamsAdsAerosolProvider, "download_target",
        lambda self, area, target, **kw: seen.append(kw) or tmp_path / "new.grib",
    )
    data_access._refresh_cams_inputs(
        {"_cams_refresh_cycle": utc(6).isoformat()},
        {"times": [utc(9)], "area": [40, 110, 20, 120]},
        SimpleNamespace(cams_cache_dir=tmp_path), None,
    )
    assert len(seen) == 2
    assert all(kw["refresh_before_cycle"] == utc(6) for kw in seen)
