"""Full event diagnostic composition, including non-scoring metadata."""
import json
import os

import numpy as np
import pytest

from firesky import national_batch as batch
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import inputs, python_reference
from test_rust_migration import ec_grib_fixture, engine_binary, rpc
from test_rust_quality import assert_object, quality_from_bundle, rows_fixture, python_regional_quality, assessment_quality_rows


def ledger(n, seed=71):
    rows = rows_fixture(n, seed)
    values = np.random.default_rng(seed).uniform(0, 1, (n, 82)).astype(np.float32)
    values[:, 0] = 1
    values[:, [43, 44, 45, 46, 62, 60, 5, 4]] = rows[:, :8]
    values[:, [54, 55, 56, 61, 63]] = rows[:, 10:15]
    samples = np.zeros((n, 9), dtype=np.float32)
    samples[:, 5] = rows[:, 9]
    return values, samples, rows[:, 8]


@pytest.mark.parametrize("mode,case", [
    (mode, case) for mode in ["scalar", "regional"]
    for case in ["normal", "empty", "invalid", "gate", "inactive", "cap", "zero_width", "selection", "ceiling"]
    if (mode, case) != ("regional", "ceiling")
])
def test_complete_event_diagnostics(engine_binary, monkeypatch, mode, case):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    values, samples, offsets = ledger(20 if case != "empty" else 0)
    indices = np.arange(len(values))
    gate = None
    ceiling = None
    if case == "invalid":
        indices = np.array([], dtype=int)
    elif case == "gate":
        gate = np.arange(len(values)) % 3 != 0
        values[1::4, 5] = .01
    elif case == "inactive":
        values[:, 44] = 0
    elif case == "cap":
        values[:, 60] = .95
        values[:, 73:77] = [-2, 1.4, 2, -.1]
    elif case == "zero_width":
        offsets[:] = 0
    elif case == "selection":
        indices = np.array([9, 3, 3, 18, 1])
    elif case == "ceiling":
        ceiling = np.linspace(0, 1, len(values))
    event = dict(values=values.tolist(), samples=samples.tolist(), offsets=offsets.tolist(),
                 valid_indices=indices.tolist())
    if gate is not None: event["burnable_gate"] = gate.tolist()
    if ceiling is not None: event["score_ceiling"] = ceiling.tolist()
    precomputed = None
    if mode == "regional" and len(indices):
        rows = assessment_quality_rows(values[None], samples[None], "dusk")[:, indices]
        rows[:, :, 8] = offsets[indices]
        rows[:, :, 17] = 1
        precomputed = python_regional_quality(rows, 1000)[0][0]
    expected = batch._fire_cloud_quality(values, samples, offsets, indices, gate, ceiling,
                                         precomputed_score=precomputed)
    response = rpc(engine_binary, [dict(id="diag", method="diagnose_observer_fields",
        params=dict(events=[event], mode=mode))])[0]
    assert response["type"] == "result", response
    assert_object(response["result"]["events"][0], expected)
    if len(indices):
        assert len(expected["components"]) == (105 if ceiling is not None else 103)


@pytest.mark.parametrize("n", [1, 7, 8, 9, 20, 127, 128, 129, 255])
def test_scalar_selected_aod_retains_float32_mean(engine_binary, monkeypatch, n):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    values, samples, offsets = ledger(n)
    samples[:, 5] = np.random.default_rng(n).uniform(0, 3, n)
    expected = batch._fire_cloud_quality(values, samples, offsets, np.arange(n), apply_duration=False)
    event = dict(values=values.tolist(), samples=samples.tolist(), offsets=offsets.tolist())
    response = rpc(engine_binary, [dict(id="aod", method="diagnose_observer_fields",
        params=dict(events=[event], mode="scalar", apply_duration=False))])[0]
    assert response["type"] == "result", response
    assert_object(response["result"]["events"][0], expected)


def diagnostics_from_bundle(bundle, point):
    result = quality_from_bundle(bundle, point)
    metadata = bundle.manifest["metadata"]["diagnostics"]
    if not bundle.arrays["quality_flags"][point, 0]:
        return dict(index=0.0, level=batch.fire_cloud_quality_level(0), components={})
    result["level"] = batch.fire_cloud_quality_level(result["index"])
    result["components"].update(metadata["cloud_optics_components"])
    result["components"]["observer_return_model"] = metadata["observer_return_model"]
    result["components"].update(zip(metadata["numeric_fields"], bundle.arrays["quality_diagnostics"][point]))
    for key in metadata["quality_text_replacement_fields"]:
        for old, new in metadata["quality_text_replacements"].items():
            result["components"][key] = result["components"][key].replace(old, new)
    return result


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_stored_complete_diagnostics(engine_binary, inputs, tmp_path, monkeypatch, model, phenomenon):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    path, request = inputs(model=model, phenomenon=phenomenon)
    prepared, expected = python_reference(request, monkeypatch)
    values, samples = expected[:2]
    rows = assessment_quality_rows(values, samples, phenomenon)
    scores, _ = python_regional_quality(rows, request["radius_km"])
    output = tmp_path / "diagnostics"
    response = rpc(engine_binary, [dict(id="diag", method="diagnose_stored_regional_to_files",
        params=dict(path=str(path), output_dir=str(output)))],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-diagnostics-v1", source_path=path)
    np.testing.assert_allclose(bundle.arrays["assessment"], values, rtol=4e-5, atol=5e-4)
    for i, score in enumerate(scores):
        wanted = batch._fire_cloud_quality(values[i], samples[i], rows[i, :, 8],
            np.flatnonzero(values[i, :, 0] >= .5), precomputed_score=score)
        if model != "gfs" and wanted["components"]:
            wanted["components"].update(batch.cloud_optics_metadata_for_model("ec"))
            for key in ["quality_coverage_source", "observer_column_quality_source"]:
                wanted["components"][key] = wanted["components"][key].replace("GFS", "EC public TCC/RH proxy")
        assert_object(diagnostics_from_bundle(bundle, i), wanted)
    assert bundle.manifest["metadata"]["public_forecast_complete"] is False
    assert len(bundle.manifest["metadata"]["profile_bundles"]) == len(request["frames"])


@pytest.mark.parametrize("fault", ["shape", "samples", "offsets", "index", "gate", "ceiling", "mode", "unknown"])
def test_malformed_diagnostics_fail_without_stopping_engine(engine_binary, fault):
    values, samples, offsets = ledger(2)
    event = dict(values=values.tolist(), samples=samples.tolist(), offsets=offsets.tolist())
    params = dict(events=[event])
    if fault == "shape": event["values"][0].pop()
    elif fault == "samples": event["samples"].pop()
    elif fault == "offsets": event["offsets"].pop()
    elif fault == "index": event["valid_indices"] = [2]
    elif fault == "gate": event["burnable_gate"] = [True]
    elif fault == "ceiling": event["score_ceiling"] = [1]
    elif fault == "mode": params["mode"] = "proxy"
    elif fault == "unknown": params["fallback_python"] = True
    bad, hello = rpc(engine_binary, [dict(id="bad", method="diagnose_observer_fields", params=params),
                                   dict(id="hello", method="hello")])
    assert bad["type"] == "error"
    assert hello["type"] == "result"


def test_failed_diagnostic_chain_removes_provisional_profiles(engine_binary, inputs, tmp_path):
    path, request = inputs()
    request["cams"].pop(0)
    path.write_text(json.dumps(request))
    output = tmp_path / "failed-diagnostics"
    response = rpc(engine_binary, [dict(id="diag", method="diagnose_stored_regional_to_files",
        params=dict(path=str(path), output_dir=str(output)))])[0]
    assert response["type"] == "error"
    assert not output.exists()
