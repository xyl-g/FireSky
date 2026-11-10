"""Experimental Rust cloud optics, vertical structure and connected-body signals."""

from __future__ import annotations

import ctypes
from functools import lru_cache

import numpy as np


@lru_cache(maxsize=1)
def _functions():
    from firesky.native_core import native_library

    library = native_library()
    if library is None:
        raise RuntimeError("Rust cloud backend requires scripts/build_native_variants.sh")
    try:
        optics = library.firesky_cloud_optics
        profiles = library.firesky_cloud_effective_profiles
    except AttributeError as exc:
        raise RuntimeError("Rust cloud symbols missing; run scripts/build_native_variants.sh") from exc
    pointer = ctypes.POINTER(ctypes.c_float)
    for function in (optics, profiles):
        function.argtypes = [pointer, ctypes.c_size_t, pointer]
        function.restype = ctypes.c_int32
    return optics, profiles


def _call(function, rows, output_fields):
    rows = np.ascontiguousarray(rows, dtype=np.float32)
    output = np.empty((rows.shape[0], output_fields), dtype=np.float32)
    pointer = ctypes.POINTER(ctypes.c_float)
    status = function(rows.ctypes.data_as(pointer), rows.shape[0], output.ctypes.data_as(pointer))
    if status != 0:
        raise ValueError("Rust cloud backend rejected array dimensions")
    return output


def optics(rh, fraction, liquid, ice):
    arrays = np.broadcast_arrays(*(np.asarray(value, dtype=np.float32) for value in (rh, fraction, liquid, ice)))
    shape = arrays[0].shape
    rows = np.stack(arrays, axis=-1).reshape(-1, 4)
    output = _call(_functions()[0], rows, 2).reshape((*shape, 2))
    return output[..., 0], output[..., 1]


def effective_profile(profile):
    values = np.asarray(profile, dtype=np.float32)
    if values.ndim == 0 or values.shape[-1] != 24:
        raise ValueError("Rust cloud profiles must have exactly 24 fields")
    shape = values.shape[:-1]
    output = _call(_functions()[1], values.reshape(-1, 24), 5).reshape((*shape, 5))
    return (*[output[..., index] for index in range(4)], output[..., 4].astype(bool))


@lru_cache(maxsize=1)
def _structure_functions():
    from firesky.native_core import native_library

    library = native_library()
    if library is None:
        raise RuntimeError("Rust cloud backend requires scripts/build_native_variants.sh")
    try:
        structure = library.firesky_cloud_structure_v1
        body = library.firesky_cloud_body_risks_v1
        caps = library.firesky_cloud_connected_caps_v1
    except AttributeError as exc:
        raise RuntimeError("Rust cloud structure symbols missing; run scripts/build_native_variants.sh") from exc
    floats = ctypes.POINTER(ctypes.c_float)
    masks = ctypes.POINTER(ctypes.c_uint8)
    size = ctypes.c_size_t
    structure.argtypes = [floats, floats, floats, masks, size, size, ctypes.c_uint8, floats]
    body.argtypes = [floats, floats, floats, masks, floats, size, size, floats]
    caps.argtypes = [floats, size, floats]
    for function in (structure, body, caps):
        function.restype = ctypes.c_int32
    return structure, body, caps


def _pointer(values, dtype=ctypes.c_float):
    return None if values is None else values.ctypes.data_as(ctypes.POINTER(dtype))


def _levels(pressure_levels, count):
    levels = np.ascontiguousarray(pressure_levels, dtype=np.float32)
    if levels.ndim != 1 or levels.size != count or not 1 <= count <= 256 or not np.all(np.isfinite(levels)):
        raise ValueError("Rust cloud pressure levels must be finite, aligned and contain 1..256 levels")
    return levels


def structure(target_amount, pressure_levels, heights, liquid, ice, omega, explicit,
              aggregate=None, *, masks=None, derive_layers=True):
    amount = np.asarray(target_amount, dtype=np.float32)
    if amount.ndim == 0:
        raise ValueError("Rust cloud amounts require a pressure-level axis")
    levels = _levels(pressure_levels, amount.shape[-1])
    shape = amount.shape[:-1]
    cells = int(np.prod(shape, dtype=np.intp))
    arrays = [amount]
    arrays.extend(np.broadcast_to(np.asarray(value, dtype=np.float32), amount.shape)
                  for value in (heights, liquid, ice, omega))
    arrays.append(np.broadcast_to(np.asarray(explicit, dtype=bool), amount.shape))
    rows = np.ascontiguousarray(np.stack(arrays, axis=-1), dtype=np.float32)
    aggregate_values = None if aggregate is None else np.ascontiguousarray(
        np.broadcast_to(np.asarray(aggregate, dtype=np.float32), shape))
    mask_values = None
    if masks is not None:
        if len(masks) != 3:
            raise ValueError("Rust cloud structure requires three family masks")
        mask_values = np.ascontiguousarray(np.stack([
            np.broadcast_to(np.asarray(value, dtype=bool), amount.shape) for value in masks
        ], axis=-1), dtype=np.uint8)
    output = np.empty((*shape, 10), dtype=np.float32)
    status = _structure_functions()[0](
        _pointer(rows), _pointer(levels), _pointer(aggregate_values),
        _pointer(mask_values, ctypes.c_uint8), cells, levels.size,
        bool(derive_layers), _pointer(output))
    if status != 0:
        raise ValueError("Rust cloud structure rejected profile dimensions")
    return tuple(output[..., index].astype(np.uint8) if index == 5 else output[..., index]
                 for index in range(10))


def body_risks(target_amount, pressure_levels, aggregate, members, amounts, bases, tops):
    amount = np.asarray(target_amount, dtype=np.float32)
    if amount.ndim != 2:
        raise ValueError("Rust cloud body amounts must use (levels, cells)")
    levels = _levels(pressure_levels, amount.shape[0])
    cells = amount.shape[1]
    if any(len(values) != 3 for values in (members, amounts, bases, tops)):
        raise ValueError("Rust cloud body inputs require three families")
    values = np.ascontiguousarray(amount.T)
    member_values = np.ascontiguousarray(np.stack([
        np.broadcast_to(np.asarray(value, dtype=bool), amount.shape).T for value in members
    ], axis=-1), dtype=np.uint8)
    family_values = np.ascontiguousarray(np.stack([
        np.stack([np.broadcast_to(np.asarray(value, dtype=np.float32), (cells,))
                  for value in family], axis=-1)
        for family in zip(amounts, bases, tops)
    ], axis=1))
    aggregate_values = np.ascontiguousarray(np.broadcast_to(
        np.asarray(aggregate, dtype=np.float32), (cells,)))
    output = np.empty((cells, 3), dtype=np.float32)
    status = _structure_functions()[1](
        _pointer(values), _pointer(levels), _pointer(aggregate_values),
        _pointer(member_values, ctypes.c_uint8), _pointer(family_values),
        cells, levels.size, _pointer(output))
    if status != 0:
        raise ValueError("Rust cloud body risks rejected profile dimensions")
    return tuple(output[:, index] for index in range(3))


def connected_caps(*fields):
    if len(fields) != 14:
        raise ValueError("Rust connected cloud caps require exactly 14 fields")
    arrays = np.broadcast_arrays(*(np.asarray(value, dtype=np.float32) for value in fields))
    shape = arrays[0].shape
    rows = np.ascontiguousarray(np.stack(arrays, axis=-1).reshape(-1, 14))
    output = np.empty((rows.shape[0], 3), dtype=np.float32)
    status = _structure_functions()[2](_pointer(rows), rows.shape[0], _pointer(output))
    if status != 0:
        raise ValueError("Rust connected cloud caps rejected array dimensions")
    output = output.reshape((*shape, 3))
    return tuple(output[..., index] for index in range(3))


@lru_cache(maxsize=1)
def _derivation_function():
    from firesky.native_core import native_library

    library = native_library()
    try:
        function = library.firesky_derive_grid_v1
    except AttributeError as exc:
        raise RuntimeError("Rust grid derivation symbols missing; run scripts/build_native_variants.sh") from exc
    floats = ctypes.POINTER(ctypes.c_float)
    function.argtypes = [
        floats, floats, floats, ctypes.POINTER(ctypes.c_int32),
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.c_size_t, floats, floats,
    ]
    function.restype = ctypes.c_int32
    return function


def derive_grid(grid):
    from firesky import national_batch as batch

    batch._validate_canonical_grid_field_ranges(grid)
    levels = tuple(level for level in batch.PRESSURE_LEVELS
                   if ("isobaricInhPa", level, "gh") in grid.fields
                   and np.any(np.isfinite(batch._field(grid, "isobaricInhPa", level, "gh"))))
    if not levels:
        raise RuntimeError("GFS decoded grid has no supported pressure levels")
    cells = len(grid.latitudes)
    if cells > 500_000 or np.asarray(grid.longitudes).shape != (cells,):
        raise ValueError("Rust grid derivation requires aligned coordinates and at most 500000 cells")
    aliases = (("tcc", "tcdc"), ("r",), ("clwmr",), ("icmr",), ("gh",),
               ("t", "tmp"), ("w", "vvel"), ("u", "ugrd"), ("v", "vgrd"))
    rows = np.ascontiguousarray(np.stack([
        np.stack([batch._field_alias(grid, "isobaricInhPa", level, *names)
                  for names in aliases], axis=-1)
        for level in levels
    ], axis=1), dtype=np.float32)
    aggregates = [batch._layer_fraction_with_provenance(grid, kind, *names)
                  for kind, names in (
                      ("highCloudLayer", ("hcc", "avg_hcc")),
                      ("middleCloudLayer", ("mcc", "avg_mcc")),
                      ("lowCloudLayer", ("lcc", "avg_lcc")),
                  )]
    extras = np.ascontiguousarray(np.stack([
        *[batch._field(grid, "surface", 0, name) for name in ("orog", "vis", "prate")],
        *[values for values, _ in aggregates],
        *[batch._field(grid, "highCloudLayer", 0, name) for name in ("hcdc", "tcc", "tcdc")],
        *[batch._cloud_boundary_pressure(grid, family, boundary)
          for family in ("high", "middle", "low") for boundary in ("bottom", "top")],
    ], axis=-1), dtype=np.float32)
    coordinates = np.ascontiguousarray(np.column_stack((grid.latitudes, grid.longitudes)), dtype=np.float32)
    pressure = np.asarray(levels, dtype=np.int32)
    semantics = np.asarray([{"missing": 0, "instantaneous": 1, "average": 2}[kind]
                            for _, kind in aggregates], dtype=np.uint8)
    columns = np.empty((cells, 36), dtype=np.float32)
    profiles = np.empty((cells, len(levels), 24), dtype=np.float32)
    status = _derivation_function()(
        _pointer(rows), _pointer(extras), _pointer(coordinates), _pointer(pressure, ctypes.c_int32),
        _pointer(semantics, ctypes.c_uint8), cells, len(levels), _pointer(columns), _pointer(profiles))
    if status != 0:
        raise ValueError("Rust grid derivation rejected coordinates, fields or layout")
    return columns, profiles, levels
