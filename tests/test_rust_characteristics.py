"""Differential public characteristic dictionaries and interpolated windows."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from firesky import forecast_characteristics as reference
from test_rust_migration import engine_binary, rpc
from test_rust_quality import assert_object


def compare(binary, operation, cases):
    requests = [dict(id=str(i), method="forecast_characteristics", params=dict(operation=operation, **case))
                for i, case in enumerate(cases)]
    responses = rpc(binary, requests)
    functions = dict(level=reference.fire_cloud_quality_level, scene=reference.scene_diagnosis,
        phase=reference.cloud_phase_diagnosis, genus=reference.cloud_genus_diagnosis,
        genus_proxy=reference.cloud_genus_proxy_diagnosis, color=reference.color_diagnosis,
        evolution=reference.cloud_evolution_diagnosis, window=reference.score_curve_window)
    for response, params in zip(responses, cases):
        assert response["type"] == "result", response
        assert_object(response["result"], functions[operation](**params))


def test_levels_scene_and_phase_boundaries(engine_binary):
    thresholds = [.001, .05, .20, .40, .60, .80, 1, 1.5, 2]
    compare(engine_binary, "level", [dict(index=v) for t in thresholds
            for v in [np.nextafter(t, 0), t, np.nextafter(t, 3)]] + [dict(index=-1), dict(index=3)])
    rng = np.random.default_rng(751)
    scenes = [dict(zip(["local_deck", "distant_target", "remote_boundary", "cloud_hole", "convective_risk"], row))
              for row in rng.uniform(-.2, 1.2, (40, 5))]
    scenes += [dict(local_deck=t, distant_target=t, remote_boundary=t, cloud_hole=t, convective_risk=c)
               for t in [0, .079, .08, .62] for c in [.349, .35, .62]]
    compare(engine_binary, "scene", scenes)
    compare(engine_binary, "phase", [dict(cloud_ice_kg_kg=ice, cloud_liquid_kg_kg=liquid)
        for ice, liquid in [(None,None), (-1,0), (1e-11,1e-11), (1,0), (0,1), (.28,.72), (.72,.28), (.5,.5)]])


@pytest.mark.parametrize("operation", ["genus", "genus_proxy"])
@pytest.mark.parametrize("family", [None, "high", "middle", "low"])
def test_genera_keep_proxy_and_compatible_contracts(engine_binary, operation, family):
    key = "pressure_family" if operation == "genus_proxy" else "dominant_pressure_family"
    cases = []
    for phase in [None, "ice", "water", dict(label="mixed", confidence=.8), dict(label=None)]:
        for base, top in [(None,None), (500,1200), (501,1200), (2000,3999), (2000,4500), (2000,5000),
                          (2000,5500), (3000,2000)]:
            for risk in [.1, .62, 1.2]:
                cases.append({key:family, "cloud_phase":phase, "cloud_base_agl_m":base,
                    "cloud_top_agl_m":top, "convective_risk":risk, "model_source":"ec"})
    compare(engine_binary, operation, cases)


def test_color_thresholds_and_quality_override(engine_binary):
    cases = [dict(quality_score=strength, burn_score=.99, vividness_score=.7,
                  solar_elevation_deg=elevation, aod550=aod, solar_incoming_illumination_score=illum,
                  deep_cloud_risk=deep, cloud_phase=phase)
             for strength in [.079, .08, .8] for elevation in [-1.599, -1.6, -3.8, -5]
             for aod in [.319, .32] for illum, deep in [(.159,.2), (.16,.66), (.9,.661)]
             for phase in ["ice", "water"]]
    compare(engine_binary, "color", cases)
    compare(engine_binary, "color", [dict(burn_score=.5, vividness_score=.3, solar_elevation_deg=-4,
        aod550=-1, solar_incoming_illumination_score=1, deep_cloud_risk=-1, cloud_phase="unknown")])


def steps_fixture(aware=True, quality=True):
    start = datetime(2026,11,9,17,0,0,123456, tzinfo=timezone(timedelta(hours=8)) if aware else None)
    steps = []
    for i, amount in enumerate([0, .02, .2, .6, .3, .004, 0]):
        record = dict(valid_time_local=(start+timedelta(minutes=i*5)).isoformat(),
            burn_score=amount, local_sky_score=amount*.7, remote_sky_score=amount,
            scene_scores=dict(cloud_hole=amount*.6), ignored_public_step_field=True)
        if quality:
            record.update(instant_quality_index=amount*2, physically_burnable=amount>=.003,
                          burnable_cloud_coverage=amount)
        steps.append(record)
    return steps


@pytest.mark.parametrize("aware", [False, True])
@pytest.mark.parametrize("quality", [False, True])
def test_curve_windows_and_evolution(engine_binary, aware, quality):
    steps = steps_fixture(aware, quality)
    permutations = [steps, list(reversed(steps)), [steps[i] for i in [0,3,3,2,1,5,4,6]]]
    compare(engine_binary, "window", [dict(steps=v, threshold=t, regime="forward_cloud_field")
                                     for v in permutations for t in [.02,.25,.8]])
    compare(engine_binary, "evolution", [dict(steps=v, best_index=best) for v in permutations
                                        for best in [None,-5,0,3,100]])


def test_empty_inactive_tied_and_mixed_missing_scores(engine_binary):
    compare(engine_binary, "window", [dict(steps=[]), dict(steps=[dict(valid_time_local="2026-11-09T00:00:00", burn_score=0)])])
    compare(engine_binary, "evolution", [dict(steps=[])])
    steps = steps_fixture()
    for step in steps: step["instant_quality_index"] = .3
    compare(engine_binary, "window", [dict(steps=steps)])
    steps[1]["instant_quality_index"] = None
    steps[3]["physically_burnable"] = False
    compare(engine_binary, "window", [dict(steps=steps)])
    compare(engine_binary, "evolution", [dict(steps=steps)])
    # Python picks the global peak even if it is not itself active; preserve
    # this existing behavior rather than introducing a hidden active-only rule.
    steps[0]["instant_quality_index"] = 2.5
    compare(engine_binary, "window", [dict(steps=steps)])


def test_offsets_and_microsecond_crossings(engine_binary):
    steps = [dict(valid_time_local=t, instant_quality_index=i, physically_burnable=b,
                  burnable_cloud_coverage=c)
             for t,i,b,c in [("2026-11-09T00:00:00.000001Z",0,False,0),
                            ("2026-11-09T08:00:00.000006+08:00",1,True,.006),
                            ("2026-11-09T00:00:00.000011+00:00",0,False,0)]]
    compare(engine_binary, "window", [dict(steps=steps)])
    compare(engine_binary, "evolution", [dict(steps=steps)])


def test_centuries_long_timedelta_uses_exact_float_ratio(engine_binary):
    steps = [dict(valid_time_local=t, instant_quality_index=q, physically_burnable=b,
                  burnable_cloud_coverage=c) for t,q,b,c in [
        ("0001-01-01T00:00:00.000003+00:00",0,False,0),
        ("5000-02-03T04:05:06.000007+00:00",1,True,.007),
        ("9999-12-31T23:59:59.999999+00:00",0,False,0)]]
    compare(engine_binary, "window", [dict(steps=steps)])


def test_trend_and_quality_coverage_thresholds(engine_binary):
    cases = []
    for change in [-.15, -.14, -.05, -.049, 0, .049, .05, .14, .15]:
        steps = steps_fixture()[:3]
        for i, amount in enumerate([.5, .6, .5+change]):
            steps[i].update(remote_sky_score=amount, local_sky_score=0,
                            scene_scores=dict(cloud_hole=0), instant_quality_index=float(i == 1))
        cases.append(dict(steps=steps))
    compare(engine_binary, "evolution", cases)
    cases = []
    for coverage in [np.nextafter(.003, 0), .003, np.nextafter(.003, 1)]:
        steps = steps_fixture()[:3]
        for i, step in enumerate(steps):
            step.update(physically_burnable=True, burnable_cloud_coverage=coverage,
                        instant_quality_index=float(i == 1))
        cases.append(dict(steps=steps))
    compare(engine_binary, "window", cases)


@pytest.mark.parametrize("suffix", ["", "+08:00"])
def test_end_of_day_midnight_normalization(engine_binary, suffix):
    steps = [dict(valid_time_local=t+suffix, burn_score=q) for t,q in [
        ("2026-11-09T23:55:00", 0), ("2026-11-09T24:00:00.000000", .8),
        ("2026-11-10T00:05:00", 0)]]
    compare(engine_binary, "window", [dict(steps=steps)])
    compare(engine_binary, "evolution", [dict(steps=steps)])

@pytest.mark.parametrize("params", [
    dict(operation="genus", dominant_pressure_family="invalid", convective_risk=0, model_source="gfs"),
    dict(operation="window", steps=[dict(valid_time_local="not-time")]),
    dict(operation="window", steps=[dict(valid_time_local="2026-11-09T00:00:00"),
                                  dict(valid_time_local="2026-11-09T00:00:00Z")]),
    dict(operation="phase", cloud_ice_kg_kg=0, unsupported=True),
    dict(operation="unknown"),
    dict(operation="evolution", steps=[{}]*4097),
])
def test_invalid_requests_do_not_stop_engine(engine_binary, params):
    failed, hello = rpc(engine_binary, [dict(id="bad", method="forecast_characteristics", params=params),
                                      dict(id="hello", method="hello")])
    assert failed["type"] == "error"
    assert hello["type"] == "result"
