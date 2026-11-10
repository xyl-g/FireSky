"""Full core validator/materializer differentials, including rejected tampering."""
import copy
from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
import os

import numpy as np
import pytest

from firesky import geometry, national_batch as batch, national_point_core as reference
from firesky.models import ForecastRequest, GeoPoint
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import inputs, ec_grib_fixture
from test_rust_frames import inline_fixture, assemble_reference, compare, named_points, reference as frame_reference, resolved_points
from test_rust_migration import engine_binary, rpc


@pytest.fixture
def core_case(monkeypatch):
    for flag in ("ASTRONOMY", "CLOUD", "EMPIRICAL"):
        monkeypatch.setenv(f"FIRESKY_{flag}_BACKEND", "python")

    def make(model="gfs", phenomenon="dusk", case="normal", *, location=None, target_date=date(2026, 11, 9)):
        _, prepared, config, args = inline_fixture(case, model, phenomenon, True)
        points, values, samples, epochs, anchors, sources, qualities, profiles = args
        if location is not None:
            points[0].update(location)
        p = points[0]
        req = ForecastRequest(
            target_date=target_date, timezone_name=p["timezone_name"],
            point=GeoPoint(p["lat"], p["lon"], p["elevation_m"]), name=p["name"],
            phenomenon=phenomenon, model_source=model, radius_km=200,
            gfs_cycle_override=datetime.fromisoformat(f"{target_date - timedelta(days=1)}T00:00:00+00:00"))
        anchor = geometry.solar_event_time_local(
            req.target_date, req.point, req.timezone_name, req.phenomenon).timestamp()
        anchors[:] = anchor
        epochs[:] = anchor + np.array(batch.national_event_offsets_minutes(phenomenon)) * 60
        # The standalone assembly fixtures deliberately allow arbitrary ledgers;
        # valid point cores must additionally preserve the loss-chain invariants.
        values[..., 43] = .8
        values[..., 44] = np.minimum(values[..., 44], .6)
        values[..., 77:82] = [.9, .75, .7, .65, .85]
        config["cycle_utc"] = req.gfs_cycle_override.isoformat()
        config["date"] = target_date.isoformat()
        parts = assemble_reference(prepared, points, config, values, samples, epochs,
                                   anchors, sources, qualities, profiles, monkeypatch)
        core = parts["point_core"]
        reference.validate_national_point_core(core, req)
        request = asdict(req)
        request["target_date"] = req.target_date.isoformat()
        request["gfs_cycle_override"] = req.gfs_cycle_override.isoformat()
        return req, request, core
    return make


@pytest.mark.parametrize("model", ["gfs", "ec"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("case", ["normal", "partial_invalid", "no_burn", "no_family",
                                 "no_profile", "blocked", "ties", "family_wrapping"])
def test_complete_core_validation_and_public_materialization(engine_binary, core_case, model, phenomenon, case):
    req, request, core = core_case(model, phenomenon, case)
    expected = reference.materialize_national_point_core(req, core)
    validated, materialized = rpc(engine_binary, [
        dict(id="validate", method="validate_point_core", params=dict(request=request, core=core)),
        dict(id="materialize", method="materialize_point_core", params=dict(request=request, core=core)),
    ], env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    assert validated["type"] == "result", validated
    compare(validated["result"], core)
    assert materialized["type"] == "result", materialized
    compare(materialized["result"], expected)


def outcomes(binary, req, request, variants):
    wanted = []
    commands = []
    for i, core in enumerate(variants):
        try:
            reference.validate_national_point_core(core, req)
        except (ValueError, TypeError, OverflowError):
            wanted.append(False)
        else:
            wanted.append(True)
        commands.append(dict(id=str(i), method="validate_point_core",
                             params=dict(request=request, core=core)))
    responses = rpc(binary, commands + [dict(id="alive", method="hello")])
    assert responses[-1]["type"] == "result"
    for command, response, valid in zip(commands, responses, wanted):
        assert (response["type"] == "result") == valid, (
            command["id"], response, command["params"]["core"])


@pytest.mark.parametrize("container", ["root", "steps", "quality_components"])
def test_missing_extra_and_wrong_field_types(engine_binary, core_case, container):
    req, request, core = core_case()
    target = core if container == "root" else core["steps"][0] if container == "steps" else core[container]
    variants = []
    for field in list(target):
        value = copy.deepcopy(core)
        t = value if container == "root" else value["steps"][0] if container == "steps" else value[container]
        del t[field]
        variants.append(value)
    extra = copy.deepcopy(core)
    t = extra if container == "root" else extra["steps"][0] if container == "steps" else extra[container]
    t["unsupported"] = 0
    variants.append(extra)
    for field in list(target):
        for bad in [None, True, {}, []]:
            value = copy.deepcopy(core)
            t = value if container == "root" else value["steps"][0] if container == "steps" else value[container]
            t[field] = bad
            variants.append(value)
    outcomes(engine_binary, req, request, variants)


@pytest.mark.parametrize("container,groups", [
    ("quality_components", ["POINT_CORE_COMPONENT_UNIT_FIELDS", "POINT_CORE_COMPONENT_SCORE_FIELDS",
                            "POINT_CORE_COMPONENT_SIGNED_SCORE_FIELDS", "POINT_CORE_COMPONENT_NONNEGATIVE_FIELDS"]),
    ("steps", ["POINT_CORE_STEP_UNIT_FIELDS", "POINT_CORE_STEP_NONNEGATIVE_FIELDS", "POINT_CORE_STEP_SCORE_FIELDS"]),
])
def test_every_numeric_constraint_matches_python(engine_binary, core_case, container, groups):
    req, request, core = core_case()
    variants = []
    for group in groups:
        for field in sorted(getattr(reference, group)):
            for bad in [-3.0, 3.0, .123456789]:
                value = copy.deepcopy(core)
                t = value["steps"][0] if container == "steps" else value[container]
                t[field] = bad
                variants.append(value)
    outcomes(engine_binary, req, request, variants)


@pytest.mark.parametrize("fault", [
    "version", "algorithm", "date", "coordinates", "elevation", "zone", "anchor", "anchor_offset",
    "cycle", "cycle_weight", "geometry", "geometry_without_family", "root_peak", "best_offset",
    "active_count", "overall", "loss_chain", "upper_bound", "color", "color_input",
    "phase", "genus_scoring", "scene", "regime", "nonboolean_valid", "support_float", "invalid_row",
    "no_valid", "step_order", "duplicate_step", "missing_step", "extra_step",
])
def test_inconsistent_or_tampered_cores_reject_materialization(engine_binary, core_case, fault):
    req, request, core = core_case()
    c = copy.deepcopy(core)
    s = c["steps"][0]
    if fault == "version": c["version"] += 1
    elif fault == "algorithm": c["algorithm"] += "-old"
    elif fault == "date": c["target_date"] = "2026-11-08"
    elif fault == "coordinates": c["lon"] += 2e-9
    elif fault == "elevation": c["observer_elevation_asl_m"] += 2e-6
    elif fault == "zone": c["timezone_name"] = "UTC"
    elif fault == "anchor": c["anchor_local"] = (datetime.fromisoformat(c["anchor_local"])
        .replace(microsecond=0) + timedelta(seconds=2)).isoformat()
    elif fault == "anchor_offset": c["anchor_local"] = c["anchor_local"].replace("+08:00", "+09:00")
    elif fault == "cycle": c["cycle_time_utc"] = "2026-11-07T00:00:00+00:00"
    elif fault == "cycle_weight": c["model_weights"]["gfs"] = .9
    elif fault == "geometry": s["dominant_burnable_cloud_top_asl_mean_m"] = 0
    elif fault == "geometry_without_family": s["dominant_burnable_pressure_family"] = None
    elif fault == "root_peak": c["peak_quality_index"] *= .9
    elif fault == "best_offset": c["best_offset_minutes"] += 5
    elif fault == "active_count": c["quality_components"]["physically_burnable_steps"] += 1
    elif fault == "overall": c["overall_score"] += 1e-4
    elif fault == "loss_chain": s["forward_target_eligible_cloud_field_amount"] = 0
    elif fault == "upper_bound": s["forward_unoccluded_burn_potential_upper_bound"] = 0
    elif fault == "color": s["color_diagnosis"]["type"] = "invalid"
    elif fault == "color_input": s["color_diagnosis"]["inputs"]["aod550"] += 1e-8
    elif fault == "phase": s["cloud_phase_proxy"]["label"] = "invalid"
    elif fault == "genus_scoring": s["cloud_genus_proxy"]["scoring_effect"] = True
    elif fault == "scene": s["scene_scores"]["clear"] = .123
    elif fault == "regime": s["selected_regime"] = "clear"
    elif fault == "nonboolean_valid": s["valid"] = 1
    elif fault == "support_float": s["cloud_geometry_support_count"] = 3.0
    elif fault == "invalid_row": s["valid"] = False
    elif fault == "no_valid":
        for row in c["steps"]: row["valid"] = False
    elif fault == "step_order": c["steps"].reverse()
    elif fault == "duplicate_step": c["steps"][1] = copy.deepcopy(s)
    elif fault == "missing_step": c["steps"].pop()
    elif fault == "extra_step": c["steps"].append(copy.deepcopy(s))
    with pytest.raises((ValueError, TypeError, OverflowError)):
        reference.materialize_national_point_core(req, c)
    bad, alive = rpc(engine_binary, [
        dict(id="bad", method="materialize_point_core", params=dict(request=request, core=c)),
        dict(id="alive", method="hello")])
    assert bad["type"] == "error", bad
    assert alive["type"] == "result"


def test_optional_proxies_and_legacy_validation_flexibility(engine_binary, core_case):
    req, request, core = core_case("ec")
    variants = []
    for edit in ["optional", "numeric_version", "unvalidated_ec_metadata", "genus_evidence"]:
        c = copy.deepcopy(core)
        if edit == "optional":
            for row in c["steps"]:
                row.pop("cloud_phase_proxy", None)
                row.pop("cloud_genus_proxy", None)
        elif edit == "numeric_version": c["version"] = 8.0
        elif edit == "unvalidated_ec_metadata":
            c["quality_components"]["cloud_opacity_proxy_source"] = None
        else: c["steps"][0]["cloud_genus_proxy"]["evidence"] = ["legacy"]
        reference.validate_national_point_core(c, req)
        variants.append(c)
    outcomes(engine_binary, req, request, variants)
    for c in variants:
        got = rpc(engine_binary, [dict(id="point", method="materialize_point_core",
            params=dict(request=request, core=c))])[0]
        assert got["type"] == "result", got
        compare(got["result"], reference.materialize_national_point_core(req, c))


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("model_grid", [False, True])
def test_stored_raw_inputs_to_validated_point(engine_binary, inputs, tmp_path, monkeypatch,
                                             model, phenomenon, model_grid):
    import hashlib
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    path, request = inputs(model=model, phenomenon=phenomenon, points=named_points(True))
    if model_grid:
        request["points"][0]["elevation_source"] = "model-grid"
        request["points"][0].pop("elevation_m")
    # Point endpoint must imply the core even when not explicitly requested.
    path.write_text(json.dumps(request))
    prepared, _, parts = frame_reference(dict(request, include_point_core=True), monkeypatch)
    p = resolved_points(prepared, request["points"])[0]
    req = ForecastRequest(
        target_date=date.fromisoformat(request["date"]), timezone_name=p["timezone_name"],
        point=GeoPoint(p["lat"], p["lon"], p["elevation_m"]), name=p["name"],
        phenomenon=phenomenon, model_source="gfs" if model == "gfs" else "ec",
        radius_km=request["radius_km"],
        gfs_cycle_override=datetime.fromisoformat(request["cycle_utc"].replace("Z", "+00:00")))
    output = tmp_path / "point"
    commands = [dict(id="point", method="forecast_stored_point_to_files",
                     params=dict(path=str(path), output_dir=str(output)))]
    response = rpc(engine_binary, commands,
                   env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="point-forecast-v1", source_path=path)
    assert len(bundle.arrays) == 16
    metadata = bundle.manifest["metadata"]
    artifacts = {}
    for key in ("frame_payload", "point_forecast"):
        entry = metadata[key]
        encoded = (output / entry["file"]).read_bytes()
        assert len(encoded) == entry["identity"]["bytes"]
        assert hashlib.sha256(encoded).hexdigest() == entry["identity"]["sha256"]
        artifacts[key] = json.loads(encoded)
        assert entry["point_core_validated"] is True
        assert entry["public_forecast_complete"] is False
    core = artifacts["frame_payload"]["point_core"]
    # Strict materializer parity from the same core; raw-stage float32
    # tolerances are checked independently by frame/assessment tests.
    compare(artifacts["point_forecast"], reference.materialize_national_point_core(req, core))
    original = reference.materialize_national_point_core(req, parts["point_core"])
    if model == "ecmwf-basic":
        # The previously measured EC float32 ground-mean difference belongs to
        # upstream assessment, not materialization of the same point core.
        for key in ("ground_area_burnable_amount_mean", "ground_area_burnable_load_mean"):
            np.testing.assert_allclose(
                artifacts["point_forecast"]["quality_components"][key],
                original["quality_components"][key], rtol=3e-6, atol=3e-8)
            original["quality_components"][key] = artifacts["point_forecast"]["quality_components"][key]
        for actual, wanted in zip(artifacts["point_forecast"]["steps"], original["steps"]):
            for key in ("ground_area_burnable_cloud_field_amount", "ground_area_burnable_cloud_load"):
                np.testing.assert_allclose(actual[key], wanted[key], rtol=3e-6, atol=3e-8)
                wanted[key] = actual[key]
    compare(artifacts["point_forecast"], original)
    assert metadata["point_forecast"]["stored_point_payload_complete"] is True
    assert metadata["point_forecast"]["automatic_input_selection"] is False
    before = (output / "manifest.json").read_bytes()
    assert rpc(engine_binary, commands)[0]["type"] == "error"
    assert (output / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("fault", ["multiple", "unnamed", "late_validation"])
def test_stored_point_failure_does_not_publish(engine_binary, inputs, tmp_path, fault):
    path, request = inputs(points=named_points(fault != "multiple"))
    if fault == "unnamed":
        request["points"][0].pop("name")
    elif fault == "late_validation":
        import eccodes as codes
        # Apparent sunset exists here, but civil dawn/dusk do not. The regional
        # timeline uses sunset alone; scalar validation follows Astral sun(),
        # which requires all four crossings and must fail after tensor work.
        request["points"][0]["lat"] = 61
        request["date"] = "2026-06-21"
        request["cycle_utc"] = "2026-06-21T00:00:00Z"
        for record in request["frames"] + request["cams"]:
            handles = []
            with open(record["path"], "rb") as stream:
                while (handle := codes.codes_grib_new_from_file(stream)) is not None:
                    handles.append(handle)
            try:
                with open(record["path"], "wb") as stream:
                    for handle in handles:
                        codes.codes_set(handle, "dataDate", 20260621)
                        codes.codes_set(handle, "step", codes.codes_get(handle, "step") + 3)
                        for key in ("latitudeOfFirstGridPointInDegrees", "latitudeOfLastGridPointInDegrees"):
                            codes.codes_set(handle, key, codes.codes_get(handle, key) + 60)
                        codes.codes_write(handle, stream)
            finally:
                for handle in handles:
                    codes.codes_release(handle)
            if "valid_time" in record:
                record["valid_time"] = (datetime.fromisoformat(
                    record["valid_time"].replace("2026-11-09", "2026-06-21")) +
                    timedelta(hours=3)).isoformat()
            else:
                record["hour"] += 3
    path.write_text(json.dumps(request))
    if fault == "late_validation":
        assembled = rpc(engine_binary, [
            dict(id="frames", method="build_stored_frames_to_files",
                 params=dict(path=str(path), output_dir=str(tmp_path / "frames-before-validation")))])[0]
        assert assembled["type"] == "result", assembled
    output = tmp_path / "point-failure"
    bad, alive = rpc(engine_binary, [
        dict(id="bad", method="forecast_stored_point_to_files",
             params=dict(path=str(path), output_dir=str(output))),
        dict(id="alive", method="hello")])
    assert bad["type"] == "error", bad
    if fault == "late_validation":
        assert "solar transit does not occur" in bad["error"]["message"], bad
    assert alive["type"] == "result"
    assert not output.exists() or not list(output.iterdir())


@pytest.mark.parametrize("duration", [0.5, 1.5, 2.5, 1e20, 1e100, 1e308])
def test_duration_rounding_and_arbitrary_integer_output(engine_binary, core_case, duration):
    req, request, core = core_case()
    core["burnable_duration_minutes"] = duration
    core["quality_components"]["duration_minutes"] = duration
    expected = reference.materialize_national_point_core(req, core)
    got = rpc(engine_binary, [dict(id="point", method="materialize_point_core",
                                  params=dict(request=request, core=core))])[0]
    assert got["type"] == "result", got
    assert type(got["result"]["duration_minutes"]) is int
    compare(got["result"], expected)


@pytest.mark.parametrize("count", [2**53+1, 2**64, 10**400])
def test_support_counts_keep_python_integer_semantics(engine_binary, core_case, count):
    req, request, core = core_case()
    for row in core["steps"]:
        row["cloud_geometry_support_count"] = count
    core["cloud_geometry_support_count"] = count
    expected = reference.materialize_national_point_core(req, core)
    got = rpc(engine_binary, [dict(id="point", method="materialize_point_core",
                                  params=dict(request=request, core=core))])[0]
    assert got["type"] == "result", got
    compare(got["result"], expected)
    bad = copy.deepcopy(core)
    bad["cloud_geometry_support_count"] = count + 1
    outcomes(engine_binary, req, request, [bad])


@pytest.mark.parametrize("microsecond", [0, 100000, 999999])
def test_anchor_microseconds_and_fixed_offset_times(engine_binary, core_case, microsecond):
    req, request, core = core_case()
    core["anchor_local"] = datetime.fromisoformat(core["anchor_local"]).replace(
        microsecond=microsecond).isoformat()
    expected = reference.materialize_national_point_core(req, core)
    got = rpc(engine_binary, [dict(id="point", method="materialize_point_core",
                                  params=dict(request=request, core=core))])[0]
    assert got["type"] == "result", got
    compare(got["result"], expected)


@pytest.mark.parametrize("target_date", [date(2026, 11, 9), date(2400, 11, 9)])
def test_anchor_one_second_boundary_matches_scalar_reference(engine_binary, core_case, target_date):
    req, request, core = core_case(target_date=target_date)
    anchor = datetime.fromisoformat(core["anchor_local"])
    variants = []
    for microseconds in [-1000001, -1000000, -999999, 999999, 1000000, 1000001]:
        c = copy.deepcopy(core)
        c["anchor_local"] = (anchor + timedelta(microseconds=microseconds)).isoformat()
        variants.append(c)
    outcomes(engine_binary, req, request, variants)


def test_materialized_steps_keep_anchor_offset_across_dst(engine_binary, core_case):
    from zoneinfo import ZoneInfo
    req, request, core = core_case(
        location=dict(lat=.25, lon=175, timezone_name="America/New_York"),
        target_date=date(2026, 11, 1))
    expected = reference.materialize_national_point_core(req, core)
    anchor = datetime.fromisoformat(core["anchor_local"])
    assert any(datetime.fromisoformat(row["valid_time_local"]).utcoffset() !=
               datetime.fromisoformat(row["valid_time_local"]).astimezone(
                   ZoneInfo(req.timezone_name)).utcoffset()
               for row in expected["steps"])
    got = rpc(engine_binary, [dict(id="point", method="materialize_point_core",
                                  params=dict(request=request, core=core))])[0]
    assert got["type"] == "result", got
    compare(got["result"], expected)
    assert all(datetime.fromisoformat(row["valid_time_local"]).utcoffset() == anchor.utcoffset()
               for row in got["result"]["steps"])


@pytest.mark.parametrize("case", ["normal", "no_burn"])
def test_float32_best_key_overflow_and_python_whitespace(engine_binary, core_case, case):
    req, request, core = core_case(case=case)
    variants = []
    for load in [float(np.finfo(np.float32).max), 1e39]:
        c = copy.deepcopy(core)
        for row in c["steps"]:
            row["forward_burnable_cloud_load"] = load
        variants.append(c)
    for whitespace in ["\x1c", "\x1d", "\x1e", "\x1f", "\u0085", "\u2003", " \t\n"]:
        c = copy.deepcopy(core)
        c["confidence_kind"] = whitespace
        variants.append(c)
    outcomes(engine_binary, req, request, variants)
