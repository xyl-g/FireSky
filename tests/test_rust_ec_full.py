"""Strict offline EC hybrid catalog, distinct from the public/basic proxy."""

from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import os

import numpy as np
import pytest

from firesky import ecmwf_ifs as ec
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_migration import ec_grib_fixture, engine_binary, rpc


@pytest.fixture
def full_grib(ec_grib_fixture):
    def write(path, *, count=4, fault=None, **kwargs):
        codes = pytest.importorskip("eccodes")
        ec_grib_fixture(path, **kwargs)
        records = [("hybrid", level, name) for level in range(1, count + 1)
                   for name in ("cc", "clwc", "ciwc")]
        records += [("hybrid", 0, "lnsp"), ("heightAboveGround", 2, "2t"),
                    ("heightAboveGround", 2, "2d")]
        for name in ("cc", "clwc", "ciwc", "lnsp", "2t", "2d"):
            selected = next(item for item in records if item[2] == name)
            if fault == f"missing_{name}":
                records.remove(selected)
            if fault == f"duplicate_{name}":
                records.append(selected)
        if fault in ("extra", "extra_time"):
            records.append(("hybrid", count + 1, "cc"))
        pv = np.concatenate((np.linspace(0., 1000., count + 1), np.linspace(0., 1., count + 1)))
        with path.open("ab") as output:
            for index, (kind, level, name) in enumerate(records):
                handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                try:
                    west = 179 if kwargs.get("antimeridian") else 100
                    if fault == "grid" and index == 0:
                        west += 1
                    step = 9 if ((fault == "time" and index == 0) or
                                 (fault == "extra_time" and level == count + 1)) else 6
                    for key, value in {
                        "centre": "ecmf", "setLocalDefinition": 1, "localDefinitionNumber": 1,
                        "marsType": "fc", "marsStream": "oper", "Ni": 3, "Nj": 2,
                        "latitudeOfFirstGridPointInDegrees": 1,
                        "latitudeOfLastGridPointInDegrees": 0,
                        "longitudeOfFirstGridPointInDegrees": west,
                        "longitudeOfLastGridPointInDegrees": west + 2,
                        "iDirectionIncrementInDegrees": 1, "jDirectionIncrementInDegrees": 1,
                        "shortName": name, "typeOfLevel": kind, "level": level,
                        "dataDate": 20261108 if fault == "cycle" and index == 0 else 20261107,
                        "dataTime": 1200, "step": step, "packingType": "grid_ieee", "precision": 1,
                    }.items():
                        codes.codes_set(handle, key, value)
                    assert codes.codes_get(handle, "shortName") == name
                    if name in ("cc", "clwc", "ciwc"):
                        has_pv = not (fault == "no_pv" or
                                      (fault == "cc_no_pv" and index == 0) or
                                      (fault == "clwc_no_pv" and name == "clwc"))
                        if has_pv:
                            coords = pv.copy()
                            if ((fault == "pv_conflict" and index == 0) or
                                    (fault == "water_pv_conflict" and index == 1)):
                                coords[1] += 1
                            if fault == "pv_length" and index == 0:
                                coords = coords[:-2]
                            if ((fault == "pv_nonfinite" and index == 0) or
                                    (fault == "water_pv_nonfinite" and index == 1)):
                                coords[1] = np.nan
                            codes.codes_set(handle, "PVPresent", 1)
                            codes.codes_set_array(handle, "pv", coords)
                        values = np.linspace(.1, .9, 6) if name == "cc" else np.arange(6) * 1e-6
                        if fault == "bad_cc" and index == 0:
                            values[1] = 1.01
                        if fault == "negative_cc" and index == 0:
                            values[1] = -.01
                        if fault == f"negative_{name}" and name in ("clwc", "ciwc"):
                            values[1] = -1e-6
                    elif name == "lnsp":
                        values = np.linspace(10.8, 11.5, 6)
                    else:
                        values = np.full(6, 290. if name == "2t" else 285.)
                    if fault == "bitmap" and index == 0:
                        codes.codes_set(handle, "missingValue", 9999.)
                        codes.codes_set(handle, "bitmapPresent", 1)
                        values[0] = 9999.
                    codes.codes_set_values(handle, values)
                    codes.codes_write(handle, output)
                finally:
                    codes.codes_release(handle)
        return path
    return write


def assert_tensor_equal(actual, expected):
    for attribute in fields(ec.EcmwfIfsInputTensor):
        got, want = getattr(actual, attribute.name), getattr(expected, attribute.name)
        if isinstance(want, np.ndarray):
            assert got.dtype == want.dtype == np.float32
            np.testing.assert_array_equal(got.view("u4"), want.view("u4"), err_msg=attribute.name)
            assert got.flags.writeable == want.flags.writeable, attribute.name
        elif attribute.name == "raw_fields":
            assert got.keys() == want.keys()
            for key in want:
                np.testing.assert_array_equal(got[key].view("u4"), want[key].view("u4"))
                assert not got[key].flags.writeable
        else:
            assert got == want, attribute.name


@pytest.mark.parametrize("count,pressure_z,antimeridian,clipped", [
    (1, False, False, False), (2, False, False, True), (4, True, False, False),
    (4, False, True, True), (137, True, False, True), (138, False, False, False),
])
def test_full_catalog_exact_tensor_and_no_python_decode(
    engine_binary, full_grib, tmp_path, monkeypatch, count, pressure_z, antimeridian, clipped,
):
    path = full_grib(tmp_path / "full.grib", count=count, pressure_z=pressure_z,
                     antimeridian=antimeridian, clipped=clipped)
    bbox = (179, -179, 1, 0) if antimeridian else (100.5, 102, 1, 0)
    kwargs = dict(bbox=bbox, model_levels=tuple(range(1, count + 1)),
                  expected_cycle_utc=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
                  expected_forecast_hour=6)
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    expected = ec.decode_ecmwf_ifs_full_catalog_tensor(path, **kwargs)
    assert expected.model_cloud_fraction.shape == (count, 4 if not antimeridian else 6)
    assert expected.surface_temperature_k is not None and expected.surface_dewpoint_k is not None
    assert ("hybrid", 0, "lnsp") in expected.raw_fields
    assert ("heightAboveGround", 2, "2t") in expected.raw_fields
    assert expected.cloud_fraction_total_percent is None
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))

    def forbidden(*args, **kwargs):
        pytest.fail("Python full-catalog metadata/numerical decoder was invoked")

    for name in ("require_ecmwf_ifs_full_catalog_capability", "_read_records", "_convert_values",
                 "_validate_common_values", "_metadata_for_message"):
        monkeypatch.setattr(ec, name, forbidden)
    actual = ec.decode_ecmwf_ifs_full_catalog_tensor(path, **kwargs)
    assert_tensor_equal(actual, expected)


@pytest.mark.parametrize("fault", [
    "missing_cc", "missing_clwc", "missing_ciwc", "missing_lnsp", "missing_2t", "missing_2d",
    "duplicate_cc", "duplicate_clwc", "duplicate_lnsp", "duplicate_2t",
    "no_pv", "cc_no_pv", "pv_length", "pv_conflict", "water_pv_conflict", "pv_nonfinite",
    "bad_cc", "negative_cc", "negative_clwc", "negative_ciwc", "bitmap", "time", "cycle", "grid", "extra_time",
])
def test_full_bad_catalog_rejected_in_both_backends(engine_binary, full_grib, tmp_path, monkeypatch, fault):
    path = full_grib(tmp_path / "bad.grib", fault=fault)
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    with pytest.raises(ec.EcmwfIfsCapabilityError):
        ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=(1, 2, 3, 4), bbox=(101, 102, 1, 0))
    output = tmp_path / "rejected"
    result = rpc(engine_binary, [{"id": "bad", "method": "decode_ecmwf_full_to_files", "params": {
        "path": str(path), "output_dir": str(output), "model_levels": [1, 2, 3, 4], "bbox": [101, 102, 1, 0],
    }}])[0]
    assert result["type"] == "error", (fault, result)
    assert not output.exists()


@pytest.mark.parametrize("fault", ["clwc_no_pv", "extra"])
def test_reference_allows_shared_pv_and_ignores_extra_values(
    engine_binary, full_grib, tmp_path, monkeypatch, fault,
):
    path = full_grib(tmp_path / "extra.grib", fault=fault)
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    expected = ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=(1, 2, 3, 4))
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    assert_tensor_equal(ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=(1, 2, 3, 4)), expected)


def test_default_l137_file_bundle_without_python_and_no_overwrite(
    engine_binary, full_grib, tmp_path, monkeypatch,
):
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    path = full_grib(tmp_path / "l137.grib", count=137)
    params = {"path": str(path), "output_dir": str(tmp_path / "out")}
    env = {**os.environ, "PATH": "", "PYTHONPATH": "", "FIRESKY_PYTHON": "/no-python"}
    result = rpc(engine_binary, [{"id": "full", "method": "decode_ecmwf_full_to_files", "params": params}], env=env)[0]
    assert result["type"] == "result", result
    bundle = load_tensor_bundle(result["result"]["manifest_path"], kind="ecmwf-full-v1", source_path=path)
    assert bundle.manifest["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(bundle.arrays) == 21
    assert bundle.arrays["raw_fields"].shape == (469, 6)
    assert bundle.arrays["model_cc"].shape == (137, 6)
    assert "total_cloud" not in bundle.arrays and bundle.manifest["metadata"]["unsupported_reasons"] == []
    second = rpc(engine_binary, [{"id": "full", "method": "decode_ecmwf_full_to_files", "params": params}], env=env)[0]
    assert second["type"] == "error"
    assert load_tensor_bundle(result["result"]["manifest_path"], kind="ecmwf-full-v1").manifest == bundle.manifest


def test_full_options_and_absence_fail_explicitly(engine_binary, full_grib, tmp_path, monkeypatch):
    path = full_grib(tmp_path / "full.grib")
    for index, extra in enumerate((
        {"model_levels": []}, {"model_levels": [2]}, {"model_levels": [1, 3]},
        {"model_levels": [1, True]}, {"model_levels": [1, 2.0]},
        {"model_levels": list(range(1, 1348))}, {"max_points": 5}, {"max_messages": 1},
        {"expected_forecast_hour": 9}, {"expected_forecast_hour": 147},
        {"expected_cycle_utc": "2026-11-07T12:00:00+08:00"},
    )):
        output = tmp_path / f"bad{index}"
        response = rpc(engine_binary, [{"id": "bad", "method": "decode_ecmwf_full_to_files", "params": {
            "path": str(path), "output_dir": str(output), "model_levels": [1, 2, 3, 4], **extra,
        }}])[0]
        assert response["type"] == "error", (extra, response)
        assert not output.exists()
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", "/nonexistent-engine")
    with pytest.raises(ec.EcmwfIfsCapabilityError, match="unavailable"):
        ec.decode_ecmwf_ifs_full_catalog_tensor(path)
    for levels in ((), (2,), (1, 3), (True,), (1.0,), tuple(range(1, 1348))):
        with pytest.raises(ValueError):
            ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=levels)


def test_basic_and_full_contracts_remain_separate(engine_binary, full_grib, ec_grib_fixture, tmp_path, monkeypatch):
    full = full_grib(tmp_path / "full.grib")
    basic = ec_grib_fixture(tmp_path / "basic.grib")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    expected = ec.decode_ecmwf_ifs_basic_tensor(full)
    for backend in ("python", "rust"):
        monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", backend)
        actual = ec.decode_ecmwf_ifs_basic_tensor(full)
        assert_tensor_equal(actual, expected)
        assert actual.model_level_numbers == () and actual.surface_temperature_k is None
        with pytest.raises(ec.EcmwfIfsCapabilityError):
            ec.decode_ecmwf_ifs_full_catalog_tensor(basic, model_levels=(1, 2, 3, 4))


def test_corrupt_water_pv_rejected_with_codec_normalization_accounted_for(
    engine_binary, full_grib, tmp_path, monkeypatch,
):
    path = full_grib(tmp_path / "bad-water-pv.grib", fault="water_pv_nonfinite")
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    codes = pytest.importorskip("eccodes")
    with path.open("rb") as stream:
        while (handle := codes.codes_grib_new_from_file(stream)) is not None:
            try:
                if codes.codes_get(handle, "shortName") == "clwc" and codes.codes_get(handle, "level") == 1:
                    decoded_pv = codes.codes_get_array(handle, "pv")
                    break
            finally:
                codes.codes_release(handle)
        else:
            pytest.fail("missing corrupt water message")
    if np.all(np.isfinite(decoded_pv)):
        # This ecCodes codec normalizes the NaN to zero. That tests a PV
        # conflict, not a nonfinite value reaching the native guard.
        assert decoded_pv[1] != 250.
        with pytest.raises(ec.EcmwfIfsCapabilityError):
            ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=(1, 2, 3, 4))
    else:
        # A codec preserving NaN exercises the reference optional reader's
        # ignored PV versus Rust's fail-closed metadata contract.
        assert ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=(1, 2, 3, 4)).hybrid_a_pa is not None
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    with pytest.raises(ec.EcmwfIfsCapabilityError):
        ec.decode_ecmwf_ifs_full_catalog_tensor(path, model_levels=(1, 2, 3, 4))
