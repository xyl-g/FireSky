"""Contract tests for the display-only cloud-genus proxy (v2 evidence)."""

from firesky.forecast_characteristics import cloud_genus_proxy_diagnosis


def _proxy(**overrides):
    kwargs = {
        "pressure_family": "high",
        "cloud_phase": "ice",
        "cloud_base_agl_m": 9000.0,
        "cloud_top_agl_m": 9800.0,
        "convective_risk": 0.1,
        "model_source": "gfs",
    }
    kwargs.update(overrides)
    return cloud_genus_proxy_diagnosis(**kwargs)


def _assert_proxy_contract(proxy, expected_candidates):
    assert proxy is not None
    assert proxy["status"] == "proxy"
    assert proxy["scoring_effect"] is False
    assert set(proxy["candidate_scores"]) == set(expected_candidates)
    assert all(0.0 <= score <= 1.0 for score in proxy["candidate_scores"].values())
    assert 0.0 <= proxy["confidence"] <= 1.0
    assert list(proxy["compatible_genera"]) == list(proxy["candidate_scores"])
    assert proxy["source"].endswith("proxy-v2")


def test_thin_high_ice_veil_prefers_cirrus():
    proxy = _proxy(cloud_base_agl_m=9000.0, cloud_top_agl_m=9800.0)
    _assert_proxy_contract(proxy, {"cirrus", "cirrostratus", "cirrocumulus"})
    assert proxy["label"] == "cirrus"


def test_thick_high_ice_veil_prefers_cirrostratus():
    proxy = _proxy(cloud_base_agl_m=8500.0, cloud_top_agl_m=11500.0)
    assert proxy["label"] == "cirrostratus"


def test_thin_mid_water_layer_prefers_altocumulus():
    proxy = _proxy(
        pressure_family="middle",
        cloud_phase="water",
        cloud_base_agl_m=3000.0,
        cloud_top_agl_m=3800.0,
    )
    assert proxy["label"] == "altocumulus"


def test_thick_mid_ice_layer_prefers_altostratus():
    proxy = _proxy(
        pressure_family="middle",
        cloud_phase="ice",
        cloud_base_agl_m=2500.0,
        cloud_top_agl_m=5600.0,
    )
    assert proxy["label"] == "altostratus"


def test_deep_low_convective_column_prefers_cumulonimbus():
    proxy = _proxy(
        pressure_family="low",
        cloud_phase="mixed",
        cloud_base_agl_m=600.0,
        cloud_top_agl_m=9000.0,
        convective_risk=0.9,
    )
    assert proxy["label"] == "cumulonimbus"


def test_shallow_low_water_layer_prefers_stratus():
    proxy = _proxy(
        pressure_family="low",
        cloud_phase="water",
        cloud_base_agl_m=300.0,
        cloud_top_agl_m=700.0,
        convective_risk=0.1,
    )
    assert proxy["label"] in {"stratus", "stratocumulus"}


def test_phase_signal_raises_confidence_and_cirrus_score():
    unknown_phase = _proxy(cloud_phase="unknown")
    ice_phase = _proxy(cloud_phase="ice")
    assert ice_phase["candidate_scores"]["cirrus"] > unknown_phase["candidate_scores"]["cirrus"]
    assert ice_phase["confidence"] > unknown_phase["confidence"]


def test_missing_family_returns_none_and_unknown_phase_is_tolerated():
    assert cloud_genus_proxy_diagnosis(
        pressure_family=None,
        cloud_phase=None,
        cloud_base_agl_m=None,
        cloud_top_agl_m=None,
        convective_risk=0.0,
        model_source="gfs",
    ) is None
    fallback = _proxy(cloud_phase="unknown", cloud_base_agl_m=None, cloud_top_agl_m=None)
    _assert_proxy_contract(
        fallback, {"cirrus", "cirrostratus", "cirrocumulus"}
    )
