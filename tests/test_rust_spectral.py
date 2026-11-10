"""Expanded CAMS schema, diagnostics, bridge and standalone differentials."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

import numpy as np
import pytest

from firesky import cams_optics as reference
from firesky import rust_spectral
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_migration import engine_binary, rpc, unavailable

LEVELS = reference.SPECTRAL_PRESSURE_LEVELS_HPA


@pytest.fixture
def spectral_native():
    try:
        rust_spectral._functions()
    except RuntimeError as exc:
        unavailable(str(exc))


def write_spectral(path, *, mutation=None, shuffled=False, minute=0):
    codes = pytest.importorskip("eccodes")
    records = [(f"aod{wave}", 0) for wave in reference.SPECTRAL_AOD_WAVELENGTHS_NM]
    records += [(f"aerext{wave}", p) for wave in reference.SPECTRAL_EXTINCTION_WAVELENGTHS_NM for p in LEVELS]
    records += [("ssa550", 0), ("asymmetry550", 0)]
    for name, position in (("aod", 0), ("ext", 5), ("ssa", 29), ("asymmetry", 30)):
        if mutation == f"missing_{name}":
            records.pop(position)
        if mutation == f"duplicate_{name}":
            records.append(records[position])
    if mutation == "noise":
        records.insert(0, ("t", 0))
    if shuffled:
        records = [records[i] for i in np.random.default_rng(117).permutation(len(records))]
    with path.open("wb") as output:
        for index, (name, level) in enumerate(records):
            handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                for key, value in {
                    "centre": "ecmf", "shortName": name,
                    "typeOfLevel": "isobaricInhPa" if name.startswith("aerext") else "surface",
                    "level": level, "Ni": 3, "Nj": 2,
                    "latitudeOfFirstGridPointInDegrees": 1,
                    "longitudeOfFirstGridPointInDegrees": 179,
                    "latitudeOfLastGridPointInDegrees": 0,
                    "longitudeOfLastGridPointInDegrees": 181,
                    "iDirectionIncrementInDegrees": 1, "jDirectionIncrementInDegrees": 1,
                    "dataDate": 20261109, "dataTime": minute, "forecastTime": 0,
                    "packingType": "grid_ieee", "precision": 2,
                }.items():
                    codes.codes_set(handle, key, value)
                if name.startswith("aod"):
                    values = 0.1234567890123 * (550/int(name[3:])) ** 1.3 + np.arange(6) / 1000
                elif name.startswith("aerext"):
                    height = reference.standard_pressure_height_m(level)
                    values = (0.0001 * np.exp(-height/1700)) * (532/int(name[6:])) ** 1.2 + np.arange(6) * 1e-8
                elif name == "ssa550":
                    values = np.linspace(.8, .9, 6)
                elif name == "asymmetry550":
                    values = np.linspace(.5, .7, 6)
                else:
                    values = np.full(6, 250.)
                    codes.codes_set(handle, "forecastTime", 9)
                if mutation == "time" and index == 8:
                    codes.codes_set(handle, "forecastTime", 3)
                if mutation == "grid" and index == 8:
                    codes.codes_set(handle, "longitudeOfFirstGridPointInDegrees", 178)
                if mutation == "ext_level" and index == 5:
                    codes.codes_set(handle, "level", 950)
                if mutation == "ext_type" and index == 5:
                    codes.codes_set(handle, "typeOfLevel", "surface")
                if mutation == "aod_level" and index == 0:
                    codes.codes_set(handle, "typeOfLevel", "isobaricInhPa")
                    codes.codes_set(handle, "level", 1000)
                if mutation == "bad_aod" and index == 0:
                    values[0] = 10.01
                if mutation == "negative_ext" and index == 5:
                    values[0] = -1e-8
                if mutation == "bad_ssa" and name == "ssa550":
                    values[0] = 1.01
                if mutation == "bad_asymmetry" and name == "asymmetry550":
                    values[0] = -1.01
                if mutation == "bitmap" and index == 5:
                    codes.codes_set(handle, "missingValue", 9999)
                    codes.codes_set(handle, "bitmapPresent", 1)
                    values[0] = 9999
                if mutation == "clear" and name.startswith(("aod", "aerext")):
                    values[:] = 0
                codes.codes_set_values(handle, values)
                codes.codes_write(handle, output)
            finally:
                codes.codes_release(handle)
    return path


def assert_optics_equal(actual, expected):
    assert actual.valid_time == expected.valid_time
    assert actual.pressure_levels_hpa == expected.pressure_levels_hpa
    assert list(actual.aod) == list(expected.aod)
    assert list(actual.extinction) == list(expected.extinction)
    pairs = [(actual.latitudes, expected.latitudes), (actual.longitudes, expected.longitudes),
             (actual.single_scattering_albedo_550, expected.single_scattering_albedo_550),
             (actual.asymmetry_factor_550, expected.asymmetry_factor_550)]
    pairs += [(actual.aod[w], v) for w, v in expected.aod.items()]
    pairs += [(actual.extinction[w], v) for w, v in expected.extinction.items()]
    for a, b in pairs:
        assert a.dtype == b.dtype == np.float64
        assert a.flags.c_contiguous and a.flags.writeable == b.flags.writeable
        np.testing.assert_array_equal(a.view("u8"), b.view("u8"))


@pytest.mark.parametrize("mutation,shuffled,minute", [(None, False, 0), ("noise", False, 6), (None, True, 0), ("clear", False, 0)])
def test_expanded_full_schema_exact_double_decode(engine_binary, tmp_path, monkeypatch, mutation, shuffled, minute):
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    path = write_spectral(tmp_path / "expanded.grib", mutation=mutation, shuffled=shuffled, minute=minute)
    expected = reference.load_cams_spectral_optics(path)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    actual = reference.load_cams_spectral_optics(path)
    assert_optics_equal(actual, expected)
    assert actual.valid_time == datetime(2026, 11, 9, 0, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize("mutation", [
    "missing_aod", "missing_ext", "missing_ssa", "missing_asymmetry",
    "duplicate_aod", "duplicate_ext", "duplicate_ssa", "duplicate_asymmetry",
    "time", "grid", "ext_level", "ext_type", "aod_level", "bad_aod",
    "negative_ext", "bad_ssa", "bad_asymmetry", "bitmap",
])
def test_bad_expanded_schema_rejects_without_output(engine_binary, tmp_path, monkeypatch, mutation):
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    path = write_spectral(tmp_path / "expanded.grib", mutation=mutation)
    if mutation == "bitmap":
        # Reference accepts the finite missing-extinction sentinel. Native
        # decoding intentionally masks it and rejects the missing field value.
        assert reference.load_cams_spectral_optics(path).extinction[355][0, 0] == 9999
    else:
        with pytest.raises(ValueError):
            reference.load_cams_spectral_optics(path)
    output = tmp_path / "out"
    result = rpc(engine_binary, [{"id": "x", "method": "decode_cams_spectral_to_files", "params": {
        "path": str(path), "output_dir": str(output),
    }}])[0]
    assert result["type"] == "error", result
    assert not output.exists()


def test_expanded_expected_time_limits_collision_and_compact_rejection(engine_binary, tmp_path, monkeypatch):
    from test_rust_cams import write_cams
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    path = write_spectral(tmp_path / "expanded.grib")
    compact = write_cams(tmp_path / "compact.grib")
    for index, extra in enumerate((
        {"expected_valid_time": "2026-11-09T01:00:00Z"},
        {"max_points": 1}, {"max_points": 0}, {"max_messages": 30},
        {"max_messages": 0}, {"path": str(compact)},
    )):
        output = tmp_path / f"bad{index}"
        result = rpc(engine_binary, [{"id": "x", "method": "decode_cams_spectral_to_files",
            "params": {"path": str(path), "output_dir": str(output), **extra}}])[0]
        assert result["type"] == "error"
        assert not output.exists()
    output = tmp_path / "good"
    params = {"path": str(path), "output_dir": str(output),
              "expected_valid_time": "2026-11-09T08:00:00+08:00"}
    result = rpc(engine_binary, [{"id": "x", "method": "decode_cams_spectral_to_files", "params": params}])[0]
    assert result["type"] == "result", result
    manifest = output / "manifest.json"
    before = manifest.read_bytes()
    result = rpc(engine_binary, [{"id": "x", "method": "decode_cams_spectral_to_files", "params": params}])[0]
    assert result["type"] == "error"
    assert manifest.read_bytes() == before
    bundle = load_tensor_bundle(manifest, kind="cams-spectral-v1", source_path=path)
    assert bundle.manifest["metadata"]["score_consumed"] is False


def test_spectral_scalar_differentials_nonfinite_and_thresholds(spectral_native, monkeypatch):
    cases = []
    for elevation in [-np.inf, np.nan, -6, -5, np.nextafter(-5., 0), -.1, 0., 1., 10., 90., 100., np.inf]:
        cases.append((reference.relative_airmass, (elevation,)))
        cases.extend((reference.direct_aerosol_transmission, (depth, elevation))
                     for depth in [np.nan, -np.inf, -1., 0., .1, 10., np.inf])
    for g in [-np.inf, -1., -.7, 0., .7, 1., np.inf, np.nan]:
        cases.extend((reference.henyey_greenstein_phase_function, (g, a)) for a in [0., 30., 90., 180., np.nan])
    cases.extend((reference.standard_pressure_height_m, (p,)) for p in [1., 300., 500., 1000., 1013.25])
    for function, args in cases:
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
        expected = function(*args)
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
        actual = function(*args)
        np.testing.assert_allclose(actual, expected, rtol=3e-12, atol=3e-12, equal_nan=True)
    for p in [-1., 0., np.nan, np.inf, 1013.26]:
        with pytest.raises(ValueError):
            reference.standard_pressure_height_m(p)
    with pytest.raises(ValueError):
        reference.henyey_greenstein_phase_function(.7, np.inf)


def test_log_interpolation_and_angstrom_differentials(spectral_native, monkeypatch):
    rng = np.random.default_rng(528)
    for _ in range(50):
        aod = {w: float(v) for w, v in zip(reference.SPECTRAL_AOD_WAVELENGTHS_NM, rng.uniform(.001, 3, 5))}
        for wavelength in [355, 440, 550, 670, 865, float(rng.uniform(355, 865))]:
            monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
            expected = reference.interpolate_log_aod(aod, wavelength)
            exponent = reference.angstrom_exponent(aod)
            monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
            assert reference.interpolate_log_aod(aod, wavelength) == pytest.approx(expected, rel=3e-12, abs=3e-12)
            assert reference.angstrom_exponent(aod) == pytest.approx(exponent, rel=3e-12, abs=3e-12)
    for aod, wavelength in [({355: 0., 550: .2, 865: .1}, 355), ({550: .2}, 550),
                            ({355: np.nan, 865: .1}, 550), ({355: .1, 865: .2}, np.nan)]:
        with pytest.raises(ValueError):
            reference.interpolate_log_aod(aod, wavelength)
    with pytest.raises(ValueError):
        reference.angstrom_exponent({0: .1, 440: .2}, 440, 0)


@pytest.mark.parametrize("size", [8, 19, 256])
def test_profile_differentials_arbitrary_order_missing_and_duplicate_heights(spectral_native, monkeypatch, size):
    rng = np.random.default_rng(417 + size)
    for _ in range(30):
        levels = np.linspace(300., 1000., size)
        values = rng.uniform(0., .0001, size)
        values[rng.random(size) < .1] = np.nan
        order = rng.permutation(size)
        levels, values = levels[order], values[order]
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
        expected = reference.integrate_pressure_level_extinction(values, levels)
        try:
            middle = reference.middle_layer_extinction_diagnostics(values, levels)
        except ValueError:
            middle = None
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
        assert reference.integrate_pressure_level_extinction(values, levels) == pytest.approx(expected, rel=3e-12, abs=3e-12)
        if middle is None:
            with pytest.raises(ValueError):
                reference.middle_layer_extinction_diagnostics(values, levels)
        else:
            actual = reference.middle_layer_extinction_diagnostics(values, levels)
            assert actual.level_count == middle.level_count
            for name in ("peak_extinction_km_inv", "optical_depth", "seasoning_level_fraction", "overloaded_level_fraction"):
                assert getattr(actual, name) == pytest.approx(getattr(middle, name), rel=3e-12, abs=3e-12)
    # Duplicate levels retain zero-height integration intervals; test the
    # actual eight-level schema rather than assuming stable NumPy sort ties.
    levels = np.array(LEVELS, dtype=float)
    levels[2] = levels[1]
    values = np.arange(8) * 1e-5
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    expected = reference.integrate_pressure_level_extinction(values, levels)
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    assert reference.integrate_pressure_level_extinction(values, levels) == pytest.approx(expected, rel=3e-12)


def test_profile_failures_boundaries_and_units(spectral_native, monkeypatch):
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    values = np.full(8, .00002)
    result = reference.middle_layer_extinction_diagnostics(values, LEVELS)
    assert result.optical_depth == pytest.approx(.12)
    assert result.peak_extinction_km_inv == .02
    assert result.seasoning_level_fraction == 1.
    assert result.overloaded_level_fraction == 0.
    for values, levels in [([1.], [1000.]), ([np.nan]*8, LEVELS), ([1., 2.], [1000., 0.]),
                           ([1., 2.], [1000.]), (np.ones((2, 4)), np.ones((2, 4)))]:
        with pytest.raises(ValueError):
            reference.integrate_pressure_level_extinction(values, levels)
    for lower, upper in [(-1., 8000.), (8000., 2000.), (0., np.inf), (0., 1.)]:
        with pytest.raises(ValueError):
            reference.middle_layer_extinction_diagnostics(np.ones(8), LEVELS,
                lower_height_m=lower, upper_height_m=upper)


def test_nearest_and_normalization_match_scan_order_and_nan(spectral_native, monkeypatch):
    optics = reference.CamsSpectralOptics(
        latitudes=np.array([0., 0., 1.]), longitudes=np.array([179., -179., 0.]),
        aod={550: np.array([.1, .2, .3])},
        extinction={532: np.arange(24).reshape(8, 3)},
        single_scattering_albedo_550=np.array([.9, .8, .7]),
        asymmetry_factor_550=np.array([.5, .6, .7]),
    )
    for lat, lon in [(0., 180.), (0., 181.), (np.nan, 180.), (1., np.inf)]:
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
        with np.errstate(all="ignore"):
            expected = reference.nearest_cams_spectral_point(optics, lat, lon)
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
        assert reference.nearest_cams_spectral_point(optics, lat, lon) == expected
    for values in [1., np.empty((0, 2)), np.array([-540., -180., 0., 180., 360., np.inf, np.nan])]:
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
        with np.errstate(all="ignore"):
            expected = reference.normalize_longitudes(values)
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
        np.testing.assert_array_equal(reference.normalize_longitudes(values), expected)


def test_standalone_full_diagnostics_without_python(engine_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    path = write_spectral(tmp_path / "expanded.grib", shuffled=True)
    optics = reference.load_cams_spectral_optics(path)
    point = reference.nearest_cams_spectral_point(optics, .5, 180.)
    params = {"path": str(path), "observer": [.5, 180.], "wavelengths_nm": [355, 500, 550, 865, 1000],
              "solar_elevation_deg": 5., "scattering_angle_deg": 30.,
              "expected_valid_time": "2026-11-09T00:00:00Z"}
    env = {**os.environ, "PATH": "", "PYTHONPATH": "", "FIRESKY_PYTHON": "/no-python"}
    result = rpc(engine_binary, [{"id": "p", "method": "diagnose_cams_spectral", "params": params}], env=env)[0]
    assert result["type"] == "result", result
    actual = result["result"]
    assert actual["score_consumed"] is False
    assert actual["source"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual["nearest"]["lat"] == point.lat and actual["nearest"]["lon"] == point.lon
    assert actual["nearest"]["aod"] == {str(w): v for w, v in point.aod.items()}
    assert actual["nearest"]["extinction"] == {str(w): list(v) for w, v in point.extinction.items()}
    for item in actual["interpolated"][:-1]:
        expected = point.aod_at(item["wavelength_nm"])
        assert item["aod"] == pytest.approx(expected, rel=3e-12)
        assert item["direct_transmission"] == pytest.approx(reference.direct_aerosol_transmission(expected, 5.), rel=3e-12)
    assert actual["interpolated"][-1]["error"] is not None
    assert actual["angstrom_exponent_440_865"]["value"] == pytest.approx(reference.angstrom_exponent(point.aod), rel=3e-12)
    for wave in reference.SPECTRAL_EXTINCTION_WAVELENGTHS_NM:
        assert actual["profile_optical_depth"][str(wave)] == pytest.approx(point.profile_optical_depth(wave), rel=3e-12)
    expected = reference.middle_layer_extinction_diagnostics(point.extinction[532], LEVELS)
    assert actual["middle_layer_532"]["level_count"] == expected.level_count
    assert actual["middle_layer_532"]["optical_depth"] == pytest.approx(expected.optical_depth, rel=3e-12)
    assert actual["henyey_greenstein_phase_550"] == pytest.approx(reference.henyey_greenstein_phase_function(point.asymmetry_factor_550, 30.), rel=3e-12)


def test_clear_spectral_diagnostics_do_not_invent_positive_aod(engine_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    path = write_spectral(tmp_path / "clear.grib", mutation="clear")
    result = rpc(engine_binary, [{"id": "p", "method": "diagnose_cams_spectral", "params": {
        "path": str(path), "observer": [0., 180.], "wavelengths_nm": [550],
        "solar_elevation_deg": -6., "scattering_angle_deg": 90.,
    }}])[0]
    assert result["type"] == "result"
    actual = result["result"]
    assert actual["interpolated"][0]["aod"] is None and actual["interpolated"][0]["error"]
    assert actual["angstrom_exponent_440_865"]["value"] is None
    assert actual["relative_airmass"] is None and actual["relative_airmass_finite"] is False
    assert actual["middle_layer_532"]["optical_depth"] == 0.


def test_spectral_cache_sidecar_still_validates_before_rust_decode(engine_binary, tmp_path, monkeypatch):
    identity = reference.spectral_optics_cache_identity(
        datetime(2026, 11, 9, tzinfo=timezone.utc), 0, [1., 179., 0., 180.])
    path = tmp_path / reference.spectral_optics_cache_filename(
        datetime(2026, 11, 9, tzinfo=timezone.utc), 0, identity["area"])
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    write_spectral(path)
    reference.write_spectral_optics_cache_metadata(path, identity)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    assert reference.load_spectral_optics_cache(path, identity).valid_time == datetime(2026, 11, 9, tzinfo=timezone.utc)
    path.write_bytes(path.read_bytes() + b"corrupted")
    monkeypatch.setattr(rust_spectral, "decode_spectral",
        lambda *args: pytest.fail("invalid content reached the native decoder"))
    with pytest.raises(ValueError):
        reference.load_spectral_optics_cache(path, identity)
