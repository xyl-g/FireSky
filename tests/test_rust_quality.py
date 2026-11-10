"""Complete empirical event/instant ledgers, not just the final quality index."""
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from firesky import forward_field_scoring as reference, national_batch as batch
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import inputs, python_reference
from test_rust_migration import engine_binary, rpc, ec_grib_fixture


def rows_fixture(n, seed=42):
    rng = np.random.default_rng(seed)
    rows = np.zeros((n, 18))
    rows[:, 0] = rng.uniform(.05, 1, n)
    rows[:, 1] = rows[:, 0] * rng.uniform(0, 1, n)
    rows[:, 3] = 2.5 * rows[:, 1] * rng.uniform(.3, 1, n)
    rows[:, 4] = rows[:, 3] * rng.uniform(.3, 1, n)
    rows[:, 2] = rows[:, 4] * rng.uniform(.3, 1, n)
    rows[:, 5] = rng.uniform(0, 1, n)
    rows[:, 6] = rng.uniform(0, 1, n)
    rows[:, 7] = rng.uniform(0, 1, n)
    rows[:, 8] = np.arange(n) * 5 - 20
    rows[:, 9] = rng.uniform(0, .5, n)
    rows[:, 10] = rows[:, 0] * .8
    rows[:, 11] = rows[:, 1] * .7
    rows[:, 13] = rows[:, 11] * 2.5 * rng.uniform(.3, 1, n)
    rows[:, 14] = rows[:, 13] * .8
    rows[:, 12] = rows[:, 14] * .7
    rows[:, 15] = 1
    rows[:, 16] = 2.5
    rows[:, 17] = 1
    return rows


def assert_object(actual, expected, path=""):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            assert_object(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, (float, int)) and not isinstance(expected, (bool, np.bool_)):
        assert actual == pytest.approx(expected, rel=3e-11, abs=1e-11), path
    else:
        assert actual == expected, path


def call(binary, rows, **options):
    response = rpc(binary, [dict(id="score", method="score_observer_fields",
                                 params=dict(rows=[r.tolist() for r in rows], **options))])[0]
    assert response["type"] == "result", response
    return response["result"]


def python_event(rows, *, mode="regional", apply_duration=True, local_columns=True,
                 score_ceiling=False, radius_km=200, scalar_aod=None):
    if mode == "scalar":
        aod = float(rows[0, 9]) if len(rows) else 0.0
        if scalar_aod is not None:
            aod = float(np.mean(scalar_aod))
        return reference.score_observer_cloud_field(
            *(rows[:, i] for i in range(8)), rows[:, 8], aod,
            valid_indices=np.flatnonzero(rows[:, 17] >= .5),
            burnable_gate=rows[:, 15] >= .5,
            score_ceiling=rows[:, 16] if score_ceiling else None,
            apply_duration=apply_duration, analysis_radius_km=radius_km,
            **({key: rows[:, i] for key, i in zip(
                ["local_view_amount", "local_burnable_amount", "local_burnable_load",
                 "local_burnable_material_load", "local_burnable_incoming_aerosol_load"], range(10, 15))}
               if local_columns else {}),
        )
    return reference.score_observer_cloud_field_batch(
        *(rows[None, :, i] for i in range(8)), rows[:, 8], rows[None, :, 9],
        rows[None, :, 17] >= .5, apply_duration=apply_duration, analysis_radius_km=radius_km,
        **({key: rows[None, :, i] for key, i in zip(
            ["local_view_amount", "local_burnable_amount", "local_burnable_load",
             "local_burnable_material_load", "local_burnable_incoming_aerosol_load"], range(10, 15))}
           if local_columns else {}),
    )[0]


@pytest.mark.parametrize("mode", ["scalar", "regional"])
@pytest.mark.parametrize("duration", [False, True])
@pytest.mark.parametrize("local", [False, True])
def test_full_event_and_instant_contract(engine_binary, monkeypatch, mode, duration, local):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    events = [rows_fixture(n, n + 41) for n in [1, 2, 7, 20, 37]]
    for rows in events:
        if mode == "scalar":
            rows[:, 9] = .23
            rows[::4, 15] = 0
        rows[::6, 17] = 0
    actual = call(engine_binary, events, mode=mode, apply_duration=duration, local_columns=local)
    for rows, event, instant in zip(events, actual["events"], actual["instant"]):
        assert_object(event, python_event(rows, mode=mode, apply_duration=duration, local_columns=local))
        allowed = (rows[:, 17] >= .5) & (rows[:, 6] >= .05)
        if mode == "scalar":
            allowed &= rows[:, 15] >= .5
        expected = reference.score_instant_observer_cloud_field_batch(
            *(rows[:, i] for i in range(5)), rows[:, 7], rows[:, 9],
            burnable_gate=allowed, incoming_block_risk=rows[:, 5])
        for i, record in enumerate(instant):
            assert_object(record, {key: value[i].item() for key, value in expected.items()})
    assert actual["metadata"]["numpy_blas_tie_parity_proven"] is False


@pytest.mark.parametrize("mode,case", [
    (mode, case) for mode in ["scalar", "regional"]
    for case in ["empty", "invalid", "inactive", "ceiling", "duplicates", "unsorted", "weak_local_tail", "saturation"]
    if (mode, case) != ("regional", "ceiling")
])
def test_edge_events(engine_binary, monkeypatch, mode, case):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    rows = rows_fixture(20)
    rows[:, 9] = .17
    options = dict(mode=mode)
    if case == "empty":
        rows = np.zeros((0, 18))
    elif case == "invalid":
        rows[:, 17] = 0
    elif case == "inactive":
        rows[:, 1:5] = 0
    elif case == "ceiling":
        rows[:, 16] = np.linspace(0, .7, len(rows))
        options["score_ceiling"] = True
        rows[::4, 15] = 0
    elif case == "duplicates":
        rows[:, 8] = 0
        options["apply_duration"] = False
    elif case == "unsorted":
        rows = rows[np.random.default_rng(77).permutation(len(rows))]
    elif case == "weak_local_tail":
        rows[-1, 11] = .0051
        rows[-1, 12:15] = .0051 * 2.5
    elif case == "saturation":
        rows[:, :2] = 1
        rows[:, 2:5] = 2.5
        rows[:, 5] = 0
        rows[:, 6] = 1
    actual = call(engine_binary, [rows], **options)
    assert_object(actual["events"][0], python_event(rows, **options))


@pytest.mark.parametrize("mode", ["scalar", "regional"])
def test_randomized_complete_component_payloads(engine_binary, monkeypatch, mode):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    rng = np.random.default_rng(20261109)
    events = []
    for i in range(21):
        rows = rows_fixture([1, 2, 4, 7, 19, 37, 90][i % 7], i+211)
        rows[:, 8] = np.cumsum(rng.uniform(.1, 8, len(rows)))
        rows[:, 9] = .17
        rows[:, 17] = (rng.random(len(rows)) > .2)
        if mode == "scalar":
            rows[:, 15] = rng.random(len(rows)) > .1
        rows[0, 1] = [.005, np.nextafter(.005, 0), np.nextafter(.005, 1)][i % 3]
        rows[0, 6] = [.05, np.nextafter(.05, 0), np.nextafter(.05, 1)][i % 3]
        events.append(rows)
    actual = call(engine_binary, events, mode=mode)
    for rows, event in zip(events, actual["events"]):
        assert_object(event, python_event(rows, mode=mode))


@pytest.mark.parametrize("radius", [200.5, 201.5, .25])
def test_radius_text_retains_python_rounding(engine_binary, monkeypatch, radius):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    rows = rows_fixture(7)
    actual = call(engine_binary, [rows], radius_km=radius)
    assert_object(actual["events"][0], python_event(rows, radius_km=radius))


def test_scalar_original_aod_and_partial_selection(engine_binary, monkeypatch):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    rows = rows_fixture(9)
    rows[:, 9] = .2
    rows[::3, 17] = 0
    aod = [0.1, 0.4, 0.8]
    actual = call(engine_binary, [rows], mode="scalar", scalar_aod=[aod])
    assert_object(actual["events"][0], python_event(rows, mode="scalar", scalar_aod=aod))


@pytest.mark.parametrize("fault", [
    "radius", "mode", "unknown", "bad_gate", "bad_valid", "fields", "long_event",
    "regional_ceiling", "regional_aod", "aod_count",
])
def test_invalid_scoring_requests_fail_closed(engine_binary, fault):
    rows = rows_fixture(1).tolist()
    params = dict(rows=[rows])
    if fault == "radius": params["radius_km"] = 0
    elif fault == "mode": params["mode"] = "approximate"
    elif fault == "unknown": params["fallback_python"] = True
    elif fault == "bad_gate": rows[0][15] = 2
    elif fault == "bad_valid": rows[0][17] = .5
    elif fault == "fields": rows[0].pop()
    elif fault == "long_event":
        short = [0] * 18
        short[15:18] = [1, 2.5, 1]
        params["rows"] = [[short] * 4097]
    elif fault == "regional_ceiling": params["score_ceiling"] = True
    elif fault == "regional_aod": params["scalar_aod"] = [[.1]]
    elif fault == "aod_count": params.update(mode="scalar", scalar_aod=[])
    failed, hello = rpc(engine_binary, [
        dict(id="bad", method="score_observer_fields", params=params),
        dict(id="hello", method="hello"),
    ])
    assert failed["type"] == "error"
    assert hello["type"] == "result"


def quality_from_bundle(bundle, index):
    metadata = bundle.manifest["metadata"]["quality"]
    values = dict(zip(metadata["numeric_fields"], bundle.arrays["quality"][index]))
    flags = bundle.arrays["quality_flags"][index]
    if flags[0] < .5:
        return dict(index=0.0, components=dict(scoring_scope=metadata["scoring_scope"]))
    score = values.pop("index")
    if flags[2] < .5:
        values.pop("observation_score_ceiling")
        values.pop("observation_ceiling_adjustment")
    if flags[1] < .5:
        values["observer_column_quality_index"] = values["observer_column_quality_peak"] = None
    values.update(
        scoring_scope=metadata["scoring_scope"], score_formula=metadata["score_formula"],
        quality_limiting_factor=metadata["limiting_factors"][int(flags[3])],
        observer_column_quality_source=metadata["observer_column_quality_source"] if flags[1] >= .5
        else metadata["missing_observer_column_quality_source"],
        quality_coverage_source=metadata["quality_coverage_source"],
    )
    return dict(index=score, components=values)


def assessment_quality_rows(output, samples, phenomenon):
    rows = np.zeros((*output.shape[:2], 18))
    offsets = [batch.NATIONAL_VIEW_AMOUNT_OFFSET, batch.NATIONAL_BURN_AMOUNT_OFFSET,
               batch.NATIONAL_BURN_LOAD_OFFSET, batch.NATIONAL_MATERIAL_LOAD_OFFSET,
               batch.NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET, batch.NATIONAL_INCOMING_BLOCK_OFFSET, 5, 4]
    rows[:, :, :8] = output[:, :, offsets]
    rows[:, :, 8] = batch.national_event_offsets_minutes(phenomenon)
    rows[:, :, 9] = samples[:, :, 5]
    rows[:, :, 10:15] = output[:, :, [
        batch.NATIONAL_LOCAL_VIEW_OFFSET, batch.NATIONAL_LOCAL_BURN_OFFSET, batch.NATIONAL_LOCAL_LOAD_OFFSET,
        batch.NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET, batch.NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET]]
    rows[:, :, 15] = 1
    rows[:, :, 16] = 2.5
    rows[:, :, 17] = output[:, :, 0] >= .5
    return rows


def python_regional_quality(rows, radius=200):
    events = reference.score_observer_cloud_field_batch(
        *(rows[:, :, i] for i in range(8)), rows[0, :, 8], rows[:, :, 9],
        rows[:, :, 17] >= .5, analysis_radius_km=radius,
        **{key: rows[:, :, i] for key, i in zip(
            ["local_view_amount", "local_burnable_amount", "local_burnable_load",
             "local_burnable_material_load", "local_burnable_incoming_aerosol_load"], range(10, 15))},
    )
    instant = reference.score_instant_observer_cloud_field_batch(
        *(rows[:, :, i] for i in range(5)), rows[:, :, 7], rows[:, :, 9],
        burnable_gate=(rows[:, :, 17] >= .5) & (rows[:, :, 6] >= .05),
        incoming_block_risk=rows[:, :, 5],
    )
    return events, instant


def test_native_nowcast_quality_matches_python_adapter(engine_binary, monkeypatch):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    steps = [
        SimpleNamespace(
            valid_time_local=base + timedelta(minutes=index * 5),
            forward_view_cloud_field_amount=0.30 + 0.10 * index,
            forward_burnable_cloud_field_amount=0.15 + 0.06 * index,
            observer_local_cloud_field_amount=0.20,
            forward_burnable_cloud_load=0.25 + 0.08 * index,
            forward_burnable_cloud_material_load=0.35 + 0.08 * index,
            forward_burnable_cloud_incoming_aerosol_load=0.30 + 0.05 * index,
            solar_incoming_block_risk=0.10 * index,
            vividness_score=0.60,
            visibility_score=0.80,
            intensity_score=0.20,
            satellite_retrieval_material_evidence=None,
            top_candidate=None,
            top_boundary=None,
            top_cloud_hole=None,
            selected_observer_path_aod550=None,
            selected_inflow_path_aod550=None,
            selected_aerosol_path_variability_score=0.0,
            observer_return_transmission=None,
        )
        for index in range(5)
    ]
    from firesky import national_batch

    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_NOWCAST_SCORING_BACKEND", "python")
    expected = national_batch.fire_cloud_quality_from_assessments(
        steps,
        0.2,
        event_time_local=base + timedelta(minutes=10),
        core_window_minutes=10,
    )
    monkeypatch.setenv("FIRESKY_NOWCAST_SCORING_BACKEND", "rust")
    actual = national_batch.fire_cloud_quality_from_assessments(
        steps,
        0.2,
        event_time_local=base + timedelta(minutes=10),
        core_window_minutes=10,
    )
    assert actual["quality_index"] == pytest.approx(expected["quality_index"], rel=3e-11, abs=1e-11)
    assert actual["quality_level"] == expected["quality_level"]
    assert actual["best_index"] == expected["best_index"]
    assert actual["duration_status"] == expected["duration_status"]
    assert actual["quality_components"].keys() == expected["quality_components"].keys()
    for key, value in expected["quality_components"].items():
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            assert actual["quality_components"][key] == pytest.approx(value, rel=3e-11, abs=1e-11)
        else:
            assert actual["quality_components"][key] == value
    assert len(actual["steps"]) == len(expected["steps"])
    for actual_step, expected_step in zip(actual["steps"], expected["steps"], strict=True):
        assert actual_step.keys() == expected_step.keys()
        for key, value in expected_step.items():
            if isinstance(value, (float, int)) and not isinstance(value, bool):
                assert actual_step[key] == pytest.approx(value, rel=3e-11, abs=1e-11)
            else:
                assert actual_step[key] == value


def test_native_nowcast_quality_batch_projects_identical_ledgers(
    engine_binary, monkeypatch
):
    from firesky.rust_desktop_data import score_nowcast_fields
    from firesky.rust_desktop_process import close as close_engine

    rows = np.zeros((80, 18), dtype=np.float64)
    steps = np.arange(80, dtype=np.float64) * 5.0
    rows[:, 0] = 0.25 + 0.003 * steps
    rows[:, 1] = 0.10 + 0.002 * steps
    rows[:, 2] = 0.20 + 0.004 * steps
    rows[:, 3] = 0.30 + 0.004 * steps
    rows[:, 4] = 0.25 + 0.003 * steps
    rows[:, 5] = 0.002 * steps
    rows[:, 6] = 0.8
    rows[:, 7] = 0.6
    rows[:, 8] = steps
    rows[:, 9] = 0.2
    rows[:, 10] = 0.2
    rows[:, 15] = 1.0
    rows[:, 16] = 2.5
    rows[:, 17] = 1.0
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    close_engine()
    serial = score_nowcast_fields(
        rows.tolist(),
        apply_duration=True,
        local_columns=True,
        score_ceiling=False,
        radius_km=1000.0,
        parallel_instants=False,
    )
    parallel = score_nowcast_fields(
        rows.tolist(),
        apply_duration=True,
        local_columns=True,
        score_ceiling=False,
        radius_km=1000.0,
        parallel_instants=True,
    )
    assert parallel == serial


def test_multirow_regional_grouping(engine_binary, monkeypatch):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    rows = np.stack([rows_fixture(20, seed=i+101) for i in range(24)])
    rows[::3, ::4, 17] = 0
    actual = call(engine_binary, list(rows))
    expected, _ = python_regional_quality(rows)
    for a, b in zip(actual["events"], expected):
        assert_object(a, b)


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_stored_raw_quality_pipeline(engine_binary, inputs, tmp_path, monkeypatch, model, phenomenon):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    path, request = inputs(model=model, phenomenon=phenomenon)
    prepared, expected = python_reference(request, monkeypatch)
    output = tmp_path / "quality"
    response = rpc(engine_binary, [dict(id="quality", method="score_stored_regional_to_files",
        params=dict(path=str(path), output_dir=str(output)))],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-quality-v1", source_path=path)
    np.testing.assert_allclose(bundle.arrays["assessment"], expected[0], rtol=4e-5, atol=5e-4)
    rows = assessment_quality_rows(expected[0], expected[1], phenomenon)
    events, instant = python_regional_quality(rows, request["radius_km"])
    for i, event in enumerate(events):
        assert_object(quality_from_bundle(bundle, i), event)
    for i, field in enumerate(bundle.manifest["metadata"]["quality"]["instant_fields"]):
        np.testing.assert_allclose(bundle.arrays["instant_quality"][:, :, i], instant[field], rtol=3e-11, atol=1e-11)
    assert len(bundle.manifest["metadata"]["profile_bundles"]) == len(request["frames"])
    assert bundle.manifest["metadata"]["public_forecast_complete"] is False
    assert bundle.arrays["instant_quality"].shape == (len(request["points"]), 20, 19)


def test_failed_quality_chain_cleans_profiles(engine_binary, inputs, tmp_path):
    path, request = inputs()
    request["cams"].pop(0)
    path.write_text(json.dumps(request))
    output = tmp_path / "failed-quality"
    response = rpc(engine_binary, [dict(id="q", method="score_stored_regional_to_files",
        params=dict(path=str(path), output_dir=str(output)))])[0]
    assert response["type"] == "error"
    assert not output.exists()
