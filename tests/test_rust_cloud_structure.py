"""Differential checks for vertical classification and connected cloud bodies."""

import os

import numpy as np
import pytest

from firesky import cloud_native
from firesky import national_batch as batch


@pytest.fixture
def native_structure():
    try:
        cloud_native._structure_functions()
    except RuntimeError as exc:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail(str(exc))
        pytest.skip(str(exc))


def compare_outputs(actual, expected):
    for got, want in zip(actual, expected):
        assert got.dtype == want.dtype
        assert got.shape == want.shape
        if want.dtype == np.uint8:
            np.testing.assert_array_equal(got, want)
        else:
            np.testing.assert_allclose(got, want, rtol=3e-6, atol=3e-7, equal_nan=True)


def compare_structure(monkeypatch, args, **kwargs):
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    # The reference eagerly subtracts missing/infinite bounds before masking.
    with np.errstate(invalid="ignore"):
        expected = batch._vertical_cloud_structure(*args, **kwargs)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    actual = batch._vertical_cloud_structure(*args, **kwargs)
    compare_outputs(actual, expected)
    return actual


@pytest.mark.parametrize("derive_layers", [False, True])
@pytest.mark.parametrize("custom_masks", [False, True])
def test_vertical_structure_randomized_float32(native_structure, monkeypatch, derive_layers, custom_masks):
    rng = np.random.default_rng(202611083)
    thresholds = np.asarray([0, .005, .20, .30, .35, .40, .45, .50, .55, .58, .60, .62, .68, .80, 1], dtype=np.float32)
    candidates = np.concatenate((
        thresholds, np.nextafter(thresholds, np.float32(-np.inf)),
        np.nextafter(thresholds, np.float32(np.inf)), [np.nan, np.inf, -np.inf],
    )).astype(np.float32)
    for levels in ([300], [925, 300, 400, 600], batch.PRESSURE_LEVELS):
        levels = np.asarray(levels, dtype=np.float32)
        shape = (3, 100, len(levels))
        amount = rng.choice(candidates, shape)
        heights = np.broadcast_to(44330 * (1 - (levels / 1013.25) ** .1903), shape).copy()
        heights.reshape(-1)[::31] = np.nan
        heights.reshape(-1)[::43] = np.inf
        liquid = np.power(10.0, rng.uniform(-12, -3, shape)).astype(np.float32)
        ice = np.power(10.0, rng.uniform(-12, -3, shape)).astype(np.float32)
        liquid.reshape(-1)[::7] = np.nan
        ice.reshape(-1)[::13] = np.inf
        omega = rng.uniform(-1, 1, shape).astype(np.float32)
        omega.reshape(-1)[::11] = np.nan
        explicit = rng.random(shape) > .3
        masks = tuple(rng.random(shape) > .55 for _ in range(3)) if custom_masks else None
        for aggregate in (None, rng.choice(candidates, shape[:-1])):
            compare_structure(
                monkeypatch, (amount, levels, heights, liquid, ice, omega, explicit, aggregate),
                _resolved_family_masks=masks, _derive_layer_materials=derive_layers,
            )


def test_vertical_structure_all_material_classes(native_structure, monkeypatch):
    levels = np.asarray([200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925], dtype=np.float32)
    amount = np.zeros((9, len(levels)), dtype=np.float32)
    amount[1, 2] = .8
    amount[2, 4:6] = .8
    amount[3, 2:5] = [.8, 0, .6]
    amount[4, :7] = .8
    amount[4, 7:] = .3
    amount[5, 4] = .5
    amount[6, 9] = .8
    amount[7, 9] = .5
    amount[8] = .9
    heights = np.broadcast_to(np.linspace(12000, 800, len(levels), dtype=np.float32), amount.shape)
    ice = np.full_like(amount, 1e-5)
    result = compare_structure(monkeypatch, (
        amount, levels, heights, np.zeros_like(amount), ice,
        np.full_like(amount, -.5), np.ones_like(amount, dtype=bool),
    ))
    np.testing.assert_array_equal(result[5], np.arange(9, dtype=np.uint8))


def test_vertical_structure_scalar_empty_noncontiguous_and_masks(native_structure, monkeypatch):
    levels = np.asarray([250, 300, 400, 500, 850], dtype=np.float32)
    for shape in ((5,), (0, 5), (2, 0, 5), (3, 5)):
        amount = np.full(shape, .5, dtype=np.float32)
        kwargs = {"_resolved_family_masks": (levels <= 400, (levels > 400) & (levels <= 700), levels > 700)}
        compare_structure(monkeypatch, (
            amount, levels, np.broadcast_to(np.arange(5, dtype=np.float32) * 2000, shape),
            np.full(shape, np.nan, dtype=np.float32),
            np.full(shape, 1e-5, dtype=np.float32),
            np.zeros(shape, dtype=np.float32), np.zeros(shape, dtype=bool),
        ), **kwargs)
    amount = np.full((4, 10), .5, dtype=np.float32)[:, ::2]
    assert not amount.flags.c_contiguous
    compare_structure(monkeypatch, (
        amount, levels, np.full_like(amount, 5000), np.zeros_like(amount),
        np.full_like(amount, 1e-5), np.zeros_like(amount), np.ones_like(amount, dtype=bool),
    ))


def test_connected_family_body_risks_match_python(native_structure, monkeypatch):
    rng = np.random.default_rng(202611084)
    for cells in (0, 1, 511):
        levels = np.asarray(batch.PRESSURE_LEVELS, dtype=np.float32)
        amount = rng.uniform(0, 1, (len(levels), cells)).astype(np.float32)
        amount.reshape(-1)[::23] = np.nan
        members = tuple(rng.random(amount.shape) > .5 for _ in range(3))
        family_amount = tuple(rng.choice(np.asarray([0, .099, .10, .3, 1, np.nan], dtype=np.float32), cells) for _ in range(3))
        bases = tuple(rng.uniform(-500, 8000, cells).astype(np.float32) for _ in range(3))
        tops = tuple(base + rng.uniform(0, 6000, cells).astype(np.float32) for base in bases)
        if cells:
            bases[0][::13] = np.nan
        aggregate = rng.uniform(0, 1, cells).astype(np.float32)
        args = (amount, levels, aggregate, members, family_amount, bases, tops)
        monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
        expected = batch._resolved_family_body_deep_risks(*args)
        monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
        compare_outputs(batch._resolved_family_body_deep_risks(*args), expected)


def test_connected_caps_boundary_and_nonfinite_geometry(native_structure, monkeypatch):
    rng = np.random.default_rng(202611085)
    count = 500
    fields = [
        rng.integers(0, 9, count).astype(np.uint8), rng.uniform(0, 1, count).astype(np.float32),
    ]
    for base in (6000, 2000, -500):
        fields.extend([
            rng.choice(np.asarray([.099, .10, .5, 1], dtype=np.float32), count),
            np.full(count, base, dtype=np.float32),
            np.full(count, base + 3100, dtype=np.float32),
        ])
    fields.extend(rng.uniform(0, 1, count).astype(np.float32) for _ in range(3))
    fields[4][::17] = np.nan
    fields[7][::19] = np.inf
    fields[12][::23] = np.nan
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    expected = batch._apply_connected_deep_material_cap(*fields)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    compare_outputs(batch._apply_connected_deep_material_cap(*fields), expected)
    # All three families are exactly 900 m apart; the tolerance is inclusive.
    assert cloud_native.connected_caps(8, .2, .1, 6000, 9000, .1, 2000, 5100,
                                       .1, -500, 1100, .8, .7, .6) == (.2, .2, .2)
    assert cloud_native.connected_caps(8, .2, .1, 6001, 9000, .1, 2000, 5100,
                                       .1, -500, 1100, .8, .7, .6) == (.8, .7, .6)


def test_structure_rejects_invalid_inputs_without_fallback(native_structure, monkeypatch):
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    for levels, amount in (([], []), ([np.nan], [1]), ([300, 500], [1]), ([300] * 257, [1] * 257)):
        with pytest.raises(ValueError):
            batch._vertical_cloud_structure(np.asarray(amount, dtype=np.float32), levels, 0, 0, 0, 0, False)
    with pytest.raises(ValueError):
        cloud_native.body_risks(np.zeros(11), batch.PRESSURE_LEVELS, 0, (), (), (), ())
    with pytest.raises(ValueError):
        cloud_native.connected_caps(1, 2, 3)


def test_rust_structure_does_not_run_python_classifier(native_structure, monkeypatch):
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")

    def forbidden(*args, **kwargs):
        raise AssertionError("Python classifier must not run with the Rust backend")

    monkeypatch.setattr(batch, "_vertical_cloud_deep_structure", forbidden)
    monkeypatch.setattr(batch, "_vertical_cloud_material", forbidden)
    amount = np.asarray([[0, .5, 0]], dtype=np.float32)
    result = batch._vertical_cloud_structure(amount, [300, 500, 850], 5000, 0, 0, 0, False)
    assert result[5][0] == 5
