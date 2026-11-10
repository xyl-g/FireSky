"""Bounded exact basic-tensor cache; raw contract checks stay in the adapter."""
from dataclasses import fields
from datetime import datetime, timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
from types import MappingProxyType
import uuid
import zipfile

import eccodes
import numpy as np

from firesky.bounded_lock import bounded_file_lock
from firesky.ecmwf_ifs import EcmwfIfsInputTensor, ECMWF_IFS_BASIC_CONTRACT, ECMWF_IFS_PRESSURE_LEVELS

SCHEMA = "ecmwf-basic-tensor-v1"
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARRAYS = 128
ENTRY_NAME = re.compile(r"^[0-9a-f]{64}$")


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path, kwargs):
    from firesky.ecmwf_ifs import _ecmwf_decode_backend

    identity = {
        "schema": SCHEMA, "path": str(path.resolve()), "raw_sha256": _sha(path),
        "sidecar_sha256": _sha(path.with_suffix(path.suffix + ".json")),
        "bbox": list(kwargs["bbox"]) if kwargs["bbox"] is not None else None,
        "cycle": kwargs["expected_cycle_utc"].isoformat(),
        "hour": kwargs["expected_forecast_hour"], "resolution": kwargs["resolution"],
        "decoder_source_sha256": _sha(Path(__file__).with_name("ecmwf_ifs.py")),
        "cache_source_sha256": _sha(Path(__file__)),
        "numpy_version": np.__version__, "eccodes_api_version": str(eccodes.codes_get_api_version()),
        "eccodes_python_version": importlib.metadata.version("eccodes"),
    }
    if _ecmwf_decode_backend() == "rust":
        from firesky.rust_grid import engine_binary
        identity.update({
            "decoder_backend": "rust-ec-basic-v1",
            "rust_engine_sha256": _sha(engine_binary()),
            "rust_bridge_sha256": _sha(Path(__file__).with_name("rust_ecmwf.py")),
            "rust_transport_sha256": _sha(Path(__file__).with_name("tensor_bundle.py")),
        })
    return identity


def _pack(tensor):
    arrays, attributes = {}, {}

    def array(value):
        if value.dtype.kind != "f" or value.dtype.itemsize not in (4, 8) or value.ndim not in (1, 2):
            raise ValueError("unsupported tensor array")
        name = f"a{len(arrays):03d}"
        arrays[name] = value
        return {"array": name, "shape": list(value.shape), "dtype": value.dtype.str,
                "writeable": bool(value.flags.writeable)}

    for field in fields(EcmwfIfsInputTensor):
        value = getattr(tensor, field.name)
        if isinstance(value, np.ndarray):
            attributes[field.name] = array(value)
        elif field.name == "raw_fields":
            attributes[field.name] = {"raw_fields": [
                {"key": list(key), "value": array(values)} for key, values in value.items()
            ]}
        elif field.name == "value_diagnostics":
            attributes[field.name] = {"diagnostics": dict(value)}
        elif isinstance(value, datetime):
            attributes[field.name] = {"datetime": value.isoformat()}
        elif isinstance(value, tuple):
            attributes[field.name] = {"tuple": list(value)}
        elif value is None or type(value) in (str, int):
            attributes[field.name] = {"scalar": value}
        else:
            raise ValueError("unsupported tensor attribute")
    if len(arrays) > MAX_ARRAYS:
        raise ValueError("too many tensor arrays")
    return arrays, attributes


def _validate_npz(path, specs):
    # Inspect bounded NPY headers before NumPy may allocate from their shapes.
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != len(specs) or len(members) > MAX_ARRAYS:
            raise ValueError("invalid tensor member count")
        if {item.filename for item in members} != {name + ".npy" for name in specs}:
            raise ValueError("invalid tensor members")
        for member in members:
            if member.compress_type != zipfile.ZIP_STORED or member.file_size > MAX_ENTRY_BYTES:
                raise ValueError("invalid tensor member storage")
            with archive.open(member) as stream:
                version = np.lib.format.read_magic(stream)
                if version != (1, 0):
                    raise ValueError("unsupported tensor header")
                shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=4096)
                spec = specs[member.filename[:-4]]
                if list(shape) != spec["shape"] or dtype.str != spec["dtype"]:
                    raise ValueError("tensor header disagrees with manifest")
                if dtype.kind != "f" or dtype.itemsize not in (4, 8) or len(shape) not in (1, 2):
                    raise ValueError("invalid tensor dtype or dimensions")
                count = 1
                for size in shape:
                    if size < 0 or size > MAX_ENTRY_BYTES:
                        raise ValueError("invalid tensor extent")
                    count *= size
                size = count * dtype.itemsize
                if size > MAX_ENTRY_BYTES or stream.tell() + size != member.file_size:
                    raise ValueError("invalid tensor byte extent")


def _read(entry, identity):
    metadata_path, payload_path = entry / "manifest.json", entry / "arrays.npz"
    if entry.is_symlink() or metadata_path.is_symlink() or payload_path.is_symlink():
        raise ValueError("tensor cache symlink")
    if metadata_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("tensor manifest is too large")
    metadata = json.loads(metadata_path.read_text())
    if metadata["identity"] != identity or metadata["schema"] != SCHEMA:
        raise ValueError("tensor identity changed")
    if payload_path.stat().st_size != metadata["payload_bytes"] or metadata["payload_bytes"] > MAX_ENTRY_BYTES:
        raise ValueError("tensor payload size changed")
    if _sha(payload_path) != metadata["payload_sha256"]:
        raise ValueError("tensor payload checksum changed")
    attributes = metadata["attributes"]
    if not isinstance(attributes, dict) or set(attributes) != {field.name for field in fields(EcmwfIfsInputTensor)}:
        raise ValueError("tensor attribute contract changed")
    specs = {}
    for value in attributes.values():
        selected = [value] if "array" in value else [
            item["value"] for item in value.get("raw_fields", [])
        ]
        for spec in selected:
            name = spec["array"]
            if name in specs or not re.fullmatch(r"a\d{3}", name) or type(spec["writeable"]) is not bool:
                raise ValueError("invalid tensor array manifest")
            specs[name] = spec
    _validate_npz(payload_path, specs)
    with np.load(payload_path, allow_pickle=False) as payload:
        values = {}

        def array(spec):
            value = payload[spec["array"]]
            value.setflags(write=spec["writeable"])
            return value

        for name, value in attributes.items():
            if "array" in value:
                values[name] = array(value)
            elif "raw_fields" in value:
                mapping = {}
                for item in value["raw_fields"]:
                    key = tuple(item["key"])
                    if len(key) != 3 or type(key[0]) is not str or type(key[1]) is not int or type(key[2]) is not str or key in mapping:
                        raise ValueError("invalid raw field key")
                    mapping[key] = array(item["value"])
                values[name] = MappingProxyType(mapping)
            elif "diagnostics" in value:
                if not all(type(key) is str and type(count) is int for key, count in value["diagnostics"].items()):
                    raise ValueError("invalid tensor diagnostics")
                values[name] = MappingProxyType(value["diagnostics"])
            elif "datetime" in value:
                values[name] = datetime.fromisoformat(value["datetime"])
            elif "tuple" in value:
                values[name] = tuple(value["tuple"])
            elif set(value) == {"scalar"}:
                values[name] = value["scalar"]
            else:
                raise ValueError("invalid tensor attribute encoding")
    result = EcmwfIfsInputTensor(**values)
    if result.contract != ECMWF_IFS_BASIC_CONTRACT or result.source_file != identity["path"]:
        raise ValueError("tensor source contract changed")
    if result.cycle_utc.isoformat() != identity["cycle"] or result.forecast_hour != identity["hour"]:
        raise ValueError("tensor time contract changed")
    if result.resolution != identity["resolution"]:
        raise ValueError("tensor resolution changed")
    if result.valid_time_utc != result.cycle_utc + timedelta(hours=result.forecast_hour):
        raise ValueError("tensor valid time changed")
    if result.pressure_levels_hpa != ECMWF_IFS_PRESSURE_LEVELS or result.model_level_numbers:
        raise ValueError("tensor pressure/model levels changed")
    count = result.latitudes.size
    if result.latitudes.shape != (count,) or result.longitudes.shape != (count,):
        raise ValueError("tensor coordinate shape changed")
    for name in ("geopotential_height_m", "relative_humidity_percent", "temperature_k",
                 "omega_pa_s", "u_ms", "v_ms"):
        if getattr(result, name).shape != (len(ECMWF_IFS_PRESSURE_LEVELS), count):
            raise ValueError("tensor pressure array shape changed")
    for name in ("cloud_fraction_total_percent", "surface_geopotential_m2_s2", "terrain_elevation_m"):
        if getattr(result, name).shape != (count,):
            raise ValueError("tensor surface array shape changed")
    for name in ("cloud_fraction_high_percent", "cloud_fraction_middle_percent", "cloud_fraction_low_percent",
                 "model_cloud_fraction", "model_cloud_liquid_kg_kg", "model_cloud_ice_kg_kg",
                 "hybrid_a_pa", "hybrid_b", "lnsp"):
        if getattr(result, name) is not None:
            raise ValueError("tensor basic optional field changed")
    return result


def _owned_entries(root):
    return [path for path in root.iterdir()
            if path.is_dir() and not path.is_symlink() and ENTRY_NAME.fullmatch(path.name)]


def _usage(root):
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("tensor cache pool contains a symlink")
        if path.is_file():
            size += path.stat().st_size
    return size


def _trim(root, limit, keep=None):
    entries = sorted(_owned_entries(root), key=lambda path: path.stat().st_mtime_ns)
    for entry in entries:
        if _usage(root) <= limit:
            break
        if entry != keep:
            shutil.rmtree(entry)
    return _usage(root) <= limit


def load_or_decode_basic_tensor(path, *, cache_root, cache_bytes, decoder, **kwargs):
    if cache_bytes == 0:
        return decoder(path, **kwargs)
    path, root = Path(path), Path(cache_root)
    try:
        identity = _identity(path, kwargs)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if root.is_symlink():
            raise ValueError("tensor cache pool symlink")
        root.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return decoder(path, **kwargs)
    decoded_started = False
    try:
        with (root / ".lock").open("a+b") as handle:
            with bounded_file_lock(handle, timeout_seconds=300.0, description="EC tensor cache"):
                if not _trim(root, cache_bytes):
                    decoded_started = True
                    return decoder(path, **kwargs)
                entry = root / key
                try:
                    result = _read(entry, identity)
                    if _identity(path, kwargs) != identity:
                        raise RuntimeError("EC source changed during tensor cache read")
                    os.utime(entry, None)
                    return result
                except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile):
                    pass
                decoded_started = True
                result = decoder(path, **kwargs)
                if _identity(path, kwargs) != identity:
                    raise RuntimeError("EC source changed during tensor decode")
                try:
                    arrays, attributes = _pack(result)
                    reserve = sum(value.nbytes for value in arrays.values()) + 4096 * len(arrays) + MAX_MANIFEST_BYTES
                    if reserve > min(cache_bytes, MAX_ENTRY_BYTES) or not _trim(root, cache_bytes - reserve):
                        return result
                    temporary = root / f".tmp-{os.getpid()}-{uuid.uuid4().hex}"
                    temporary.mkdir()
                    try:
                        payload = temporary / "arrays.npz"
                        np.savez(payload, **arrays)
                        metadata = {
                            "schema": SCHEMA, "identity": identity, "attributes": attributes,
                            "payload_bytes": payload.stat().st_size, "payload_sha256": _sha(payload),
                        }
                        text = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
                        if len(text.encode()) > MAX_MANIFEST_BYTES or _usage(root) + len(text.encode()) > cache_bytes:
                            return result
                        (temporary / "manifest.json").write_text(text)
                        if entry.exists():
                            shutil.rmtree(entry)
                        os.replace(temporary, entry)
                    finally:
                        if temporary.exists():
                            shutil.rmtree(temporary)
                except (OSError, ValueError, TypeError):
                    return result
                return result
    except (OSError, TimeoutError, ValueError):
        if decoded_started:
            raise
        return decoder(path, **kwargs)
