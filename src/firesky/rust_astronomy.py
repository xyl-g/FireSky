"""Explicit astronomy/time transport; no numerical Python fallback."""

import ctypes
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import os
from pathlib import Path
from numbers import Real
from zoneinfo import ZoneInfo

from firesky.rust_grid import EngineOperationError, call_engine, engine_binary


def enabled():
    backend = os.environ.get("FIRESKY_ASTRONOMY_BACKEND", "python")
    if backend not in {"python", "rust"}:
        raise ValueError("FIRESKY_ASTRONOMY_BACKEND must be python or rust")
    return backend == "rust"


@lru_cache(maxsize=16)
def _file_sha(path, mtime_ns, ctime_ns, size, inode):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _fingerprint(path):
    path = Path(path).resolve()
    info = path.stat()
    return _file_sha(str(path), info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)


def backend_identity():
    if not enabled():
        return {}
    from firesky.native_core import native_library

    _position_function()
    return {
        "astronomy_backend": "rust-astronomy-v1",
        "astronomy_engine_sha256": _fingerprint(engine_binary()),
        "astronomy_native_sha256": _fingerprint(native_library()._name),
        "astronomy_bridge_sha256": _fingerprint(__file__),
    }


def _point(point):
    return {"lat": point.lat, "lon": point.lon, "elevation_m": point.elevation_m}


def _time(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Rust astronomy requires timezone-aware timestamps")
    return value.astimezone(timezone.utc).isoformat()


def time_operation(operation, **params):
    return call_engine("time_contract", {"operation": operation, **params})


def _call(method, params):
    try:
        return call_engine(method, params)
    except EngineOperationError as exc:
        raise ValueError(str(exc)) from exc


def parse(value, field, operation):
    message = (
        f"{field} must be an ISO calendar date" if operation == "parse_date"
        else f"{field} must be a timezone-aware ISO timestamp"
    )
    if not isinstance(value, str):
        raise ValueError(message)
    try:
        result = time_operation(operation, value=value)["value"]
    except EngineOperationError as exc:
        if operation == "parse_utc" and "must use UTC" in str(exc):
            message = f"{field} must use UTC offset Z or +00:00"
        raise ValueError(message) from exc
    return date.fromisoformat(result) if operation == "parse_date" else datetime.fromisoformat(result)


@lru_cache(maxsize=1)
def _position_function():
    from firesky.native_core import native_library

    library = native_library()
    try:
        function = library.firesky_solar_position_v1
    except AttributeError as exc:
        raise RuntimeError("Rust astronomy requires rebuilt native variants") from exc
    function.argtypes = [
        ctypes.c_double, ctypes.c_double, ctypes.c_int32,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_double),
    ]
    function.restype = ctypes.c_int32
    return function


def position(point, when):
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Rust astronomy requires timezone-aware timestamps")
    when = when.astimezone(timezone.utc)
    output = (ctypes.c_double * 2)()
    status = _position_function()(
        point.lat, point.lon, when.year, when.month, when.day,
        when.hour * 3600 + when.minute * 60 + when.second, output,
    )
    if status != 0:
        raise ValueError("Rust solar position rejected coordinates or calendar")
    return output[0], output[1]


def event(target_date, point, timezone_name, phenomenon):
    result = _call("solar_events", {
        "point": _point(point), "date": target_date.isoformat(),
        "timezone_name": timezone_name, "phenomenon": phenomenon,
    })
    return datetime.fromisoformat(result["anchor_time_utc"]).astimezone(ZoneInfo(timezone_name))


def crossing(point, anchor, target, phenomenon, search):
    output = ctypes.c_int64()
    status = _time_functions()["crossing"](
        point.lat, point.lon, _micros(anchor), target, _rising(phenomenon), search, ctypes.byref(output),
    )
    if status == 1:
        return None
    if status != 0:
        raise ValueError("Rust solar crossing rejected input or calendar bounds")
    return _from_micros(output.value)


def shadow_speed(point, when):
    output = ctypes.c_double()
    if _time_functions()["shadow_speed"](point.lat, point.lon, _micros(when), ctypes.byref(output)) != 0:
        raise ValueError("Rust solar shadow speed rejected input or calendar bounds")
    return output.value


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _micros(when):
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("Rust astronomy requires timezone-aware timestamps")
    delta = when.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def _from_micros(value):
    return EPOCH + timedelta(microseconds=value)


def _rising(phenomenon):
    if phenomenon not in {"dawn", "dusk"}:
        raise ValueError("phenomenon must be dawn or dusk")
    return int(phenomenon == "dawn")


@lru_cache(maxsize=1)
def _time_functions():
    from firesky.native_core import native_library

    _position_function()
    library = native_library()
    try:
        functions = {name: getattr(library, f"firesky_solar_{name}_v1")
                     for name in ("crossing", "shadow_speed", "glow_window")}
    except AttributeError as exc:
        raise RuntimeError("Rust astronomy requires rebuilt native time symbols") from exc
    double, integer, mode = ctypes.c_double, ctypes.c_int64, ctypes.c_uint32
    doubles, integers = ctypes.POINTER(double), ctypes.POINTER(integer)
    functions["crossing"].argtypes = [double, double, integer, double, mode, double, integers]
    functions["shadow_speed"].argtypes = [double, double, integer, doubles]
    functions["glow_window"].argtypes = [doubles, integer, mode, mode, doubles, integers, doubles]
    for function in functions.values():
        function.restype = ctypes.c_int32
    return functions


def glow_window(point, anchor, timezone_name, phenomenon, **window):
    from firesky.models import GlowGeometryWindow

    kind = window["kind"]
    if kind == "typical":
        distance = window["edge_distance_km"]
        regime = "typical_local_deck"
        observer = window["observer_elevation_m"]
        values = (distance, window["cloud_base_m"], 0.0, point.elevation_m if observer is None else observer)
    else:
        distance = window["target_distance_km"]
        regime = window["regime"]
        if len(regime.encode("utf-8")) > 128:
            raise ValueError("window regime exceeds 128 bytes")
        values = (distance, window["cloud_base_m"], window["cloud_top_m"], 0.0)
    coordinates = (ctypes.c_double * 3)(point.lat, point.lon, point.elevation_m)
    args = (ctypes.c_double * 4)(*values)
    times = (ctypes.c_int64 * 3)()
    metrics = (ctypes.c_double * 3)()
    status = _time_functions()["glow_window"](
        coordinates, _micros(anchor), _rising(phenomenon), int(kind != "typical"), args, times, metrics,
    )
    if status == 1:
        return None
    if status != 0:
        raise ValueError("Rust solar glow window rejected input or calendar bounds")
    result = {
        "regime": regime, "duration_minutes": metrics[0], "shadow_speed_km_per_min": metrics[1],
        "geometric_max_distance_km": metrics[2], "edge_distance_km": distance,
        "cloud_base_m": window["cloud_base_m"],
    }
    tzinfo = ZoneInfo(timezone_name)
    for name, value in zip(("start", "midpoint", "end"), times):
        utc = _from_micros(value)
        result[f"{name}_time_utc"] = utc
        result[f"{name}_time_local"] = utc.astimezone(tzinfo)
    return GlowGeometryWindow(**result)


class _RegionalOutputs(ctypes.Structure):
    _fields_ = [
        ("observers", ctypes.POINTER(ctypes.c_float)),
        ("elevation", ctypes.POINTER(ctypes.c_double)),
        ("azimuth", ctypes.POINTER(ctypes.c_double)),
        ("valid", ctypes.POINTER(ctypes.c_uint8)),
        ("evaluation", ctypes.POINTER(ctypes.c_double)),
        ("anchors", ctypes.POINTER(ctypes.c_double)),
        ("samples", ctypes.POINTER(ctypes.c_float)),
    ]


@lru_cache(maxsize=1)
def _regional_function():
    from firesky.native_core import native_library

    _position_function()
    try:
        function = native_library().firesky_regional_timeline_v1
    except AttributeError as exc:
        raise RuntimeError("Rust astronomy requires rebuilt regional timeline symbols") from exc
    function.argtypes = [
        ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t,
        ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_int64, ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
        ctypes.POINTER(_RegionalOutputs), ctypes.POINTER(ctypes.c_char), ctypes.c_size_t,
    ]
    function.restype = ctypes.c_int32
    return function


def _regional(points, target_date, phenomenon, cycle=None, hours=None):
    import numpy as np

    count = len(points)
    if count > 500000:
        raise ValueError("regional point limit exceeded")
    coordinates = []
    zones = []
    for point in points:
        elevation = point["elevation_m"]
        if isinstance(elevation, bool) or not isinstance(elevation, Real):
            raise ValueError("solar event point elevation_m must be a finite number")
        coordinates.append((point["lat"], point["lon"], elevation))
        zone = str(point["timezone_name"]).encode("utf-8")
        if b"\0" in zone or len(zone) > 128:
            raise ValueError("regional timezone name is invalid or too long")
        zones.append(zone)
    coordinates = np.ascontiguousarray(coordinates, dtype=np.float64).reshape(count, 3)
    zone_names = (ctypes.c_char_p * count)(*zones)
    if cycle is None:
        if hours is not None:
            raise ValueError("cycle and model hours must be supplied together")
        hour_values = np.empty(0, dtype=np.float64)
        cycle_micros = 0
    else:
        hour_values = np.asarray(hours, dtype=np.float64)
        if hour_values.ndim != 1 or not 1 <= hour_values.size <= 4096:
            raise ValueError("model hours must be a bounded nonempty one-dimensional vector")
        hour_values = np.ascontiguousarray(hour_values)
        cycle_micros = _micros(cycle)
    observers = np.empty((count, 3), dtype=np.float32)
    elevation = np.empty((count, 20), dtype=np.float64)
    azimuth = np.empty((count, 20), dtype=np.float64)
    valid = np.empty((count, 20), dtype=np.bool_)
    evaluation = np.empty((count, 20), dtype=np.float64)
    anchors = np.empty(count, dtype=np.float64)
    samples = None if cycle is None else np.empty((count, 20, 9), dtype=np.float32)
    def pointer(array, scalar):
        return array.ctypes.data_as(ctypes.POINTER(scalar))
    outputs = _RegionalOutputs(
        pointer(observers, ctypes.c_float), pointer(elevation, ctypes.c_double),
        pointer(azimuth, ctypes.c_double), pointer(valid, ctypes.c_uint8),
        pointer(evaluation, ctypes.c_double), pointer(anchors, ctypes.c_double),
        None if samples is None else pointer(samples, ctypes.c_float),
    )
    error = ctypes.create_string_buffer(1024)
    status = _regional_function()(
        pointer(coordinates, ctypes.c_double), zone_names, count,
        target_date.year, target_date.month, target_date.day, _rising(phenomenon),
        cycle_micros, pointer(hour_values, ctypes.c_double), hour_values.size,
        ctypes.byref(outputs), error, len(error),
    )
    if status != 0:
        message = error.value.decode("utf-8", errors="replace") or "Rust regional timeline rejected input"
        if status == -2:
            raise RuntimeError(message)
        raise ValueError(message)
    if samples is None:
        return observers, elevation, azimuth, valid, evaluation, anchors
    return observers, samples, evaluation, anchors


def regional_timeline(points, target_date, phenomenon):
    return _regional(points, target_date, phenomenon)


def regional_samples(points, target_date, phenomenon, cycle, hours):
    return _regional(points, target_date, phenomenon, cycle, hours)
