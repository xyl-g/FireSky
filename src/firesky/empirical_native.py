"""Experimental Rust empirical backend; Python remains the reference/default."""

from __future__ import annotations

import ctypes
from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def _functions():
    from firesky.native_core import native_library

    library = native_library()
    if library is None:
        raise RuntimeError("Rust empirical backend requires scripts/build_native_variants.sh")
    try:
        window = library.firesky_empirical_window_weights
        response = library.firesky_empirical_response
        duration = library.firesky_empirical_duration_factor
    except AttributeError as exc:
        raise RuntimeError(
            "Rust empirical symbols are missing; run scripts/build_native_variants.sh"
        ) from exc
    double_ptr = ctypes.POINTER(ctypes.c_double)
    window.argtypes = [
        double_ptr, ctypes.POINTER(ctypes.c_uint8), double_ptr, double_ptr,
        ctypes.c_size_t, double_ptr, double_ptr,
    ]
    window.restype = ctypes.c_int32
    response.argtypes = [double_ptr, ctypes.c_size_t, ctypes.c_uint32, double_ptr]
    response.restype = ctypes.c_int32
    duration.argtypes = [ctypes.c_double]
    duration.restype = ctypes.c_double
    return window, response, duration


def _double_ptr(array):
    return array.ctypes.data_as(ctypes.POINTER(ctypes.c_double))


def response(value, *, inverse=False):
    array = np.asarray(value, dtype=np.float64)
    flat = np.ascontiguousarray(array.reshape(-1))
    output = np.empty_like(flat)
    status = _functions()[1](
        _double_ptr(flat), flat.size, int(inverse), _double_ptr(output),
    )
    if status != 0:
        raise ValueError("Rust empirical response rejected the input")
    result = output.reshape(array.shape)
    return float(result) if result.ndim == 0 else result


def duration_factor(minutes):
    return float(_functions()[2](float(minutes)))


def event_window_weights(values, active, widths, offsets=None):
    values = np.ascontiguousarray(values, dtype=np.float64)
    active = np.ascontiguousarray(active, dtype=np.bool_).astype(np.uint8)
    widths = np.ascontiguousarray(widths, dtype=np.float64)
    offsets = None if offsets is None else np.ascontiguousarray(offsets, dtype=np.float64)
    if (
        values.ndim != 1 or active.shape != values.shape or widths.shape != values.shape
        or (offsets is not None and offsets.shape != values.shape)
    ):
        raise ValueError("values, active, widths and offsets must be aligned 1D arrays")
    weights = np.empty_like(values)
    minutes = ctypes.c_double()
    status = _functions()[0](
        _double_ptr(values), active.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        _double_ptr(widths), None if offsets is None else _double_ptr(offsets),
        values.size, _double_ptr(weights), ctypes.byref(minutes),
    )
    if status != 0:
        raise ValueError("Rust event window requires finite inputs and at most 4096 samples")
    return weights, float(minutes.value)


def event_window_weights_batch(values, active, widths, offsets=None):
    values = np.asarray(values, dtype=np.float64)
    active = np.asarray(active, dtype=bool)
    widths = np.asarray(widths, dtype=np.float64)
    if values.ndim != 2 or active.shape != values.shape or widths.shape != (values.shape[1],):
        raise ValueError("values and active must be aligned 2D arrays with shared 1D widths")
    if offsets is not None and np.asarray(offsets).shape != widths.shape:
        raise ValueError("offsets must be aligned with widths")
    weights = np.zeros_like(values)
    minutes = np.zeros(values.shape[0], dtype=np.float64)
    for index in range(values.shape[0]):
        weights[index], minutes[index] = event_window_weights(values[index], active[index], widths, offsets)
    return weights, minutes
