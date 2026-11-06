"""Engine facade contract checks (offline, no network)."""

import pytest

from firesky import FIRESKY_ALGORITHM_VERSION, predict_burn_sky


def test_predict_burn_sky_validates_inputs():
    from datetime import datetime, timezone

    valid_cycle = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="phenomenon"):
        predict_burn_sky(
            [{"name": "x", "lat": 30.0, "lon": 120.0}],
            "2026-11-05",
            "noon",
            cycle_utc=None,
            cams_cache_dir=".cache/cams",
        )
    # cycle_utc is now optional (auto_fetch derives the latest cycle) — the
    # remaining hard validations are phenomenon, points and full point keys.
    with pytest.raises(ValueError, match="观测点"):
        predict_burn_sky([], "2026-11-05", "dusk", cycle_utc=valid_cycle, cams_cache_dir=".cache/cams")


def test_algorithm_version_is_exposed():
    assert FIRESKY_ALGORITHM_VERSION == "2026.11.04.87"
