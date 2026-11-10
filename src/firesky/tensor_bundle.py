"""Validated Rust tensor transport. This is not a forecast cache identity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import numpy as np

MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
_NAME = re.compile(r"[a-z0-9_]{1,64}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class TensorBundle:
    manifest: dict
    arrays: dict[str, np.ndarray]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate tensor manifest key: {key}")
        result[key] = value
    return result


def _regular_file(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"tensor file is not a regular file: {path.name}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    stream = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError("tensor file changed type during opening")
    return stream


def _digest(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(64 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _integer(value, minimum=0, maximum=MAX_BUNDLE_BYTES):
    return type(value) is int and minimum <= value <= maximum


def load_tensor_bundle(manifest_path, *, kind=None, source_path=None, mmap=False):
    """Validate bounded NPY headers and whole-file hashes before loading arrays.

    Mappings use the already validated file descriptor, never a reopened path.
    Successful arrays are read-only, and the caller owns their mapping lifetime.
    """
    path = Path(manifest_path).absolute()
    if path.name != "manifest.json" or not stat.S_ISDIR(path.parent.lstat().st_mode):
        raise ValueError("invalid tensor manifest location")
    with _regular_file(path) as stream:
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("tensor manifest exceeds size limit")
    manifest = json.loads(payload, object_pairs_hook=_object)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "producer", "kind", "source", "metadata", "arrays"}
        or manifest["schema"] != "firesky.tensor-bundle.v1"
        or not isinstance(manifest["producer"], str)
        or not manifest["producer"].startswith("firesky-engine/")
        or not isinstance(manifest["kind"], str)
        or (kind is not None and manifest["kind"] != kind)
        or not isinstance(manifest["metadata"], dict)
    ):
        raise ValueError("incompatible tensor manifest")
    source = manifest["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"bytes", "sha256"}
        or not _integer(source["bytes"], maximum=2**64 - 1)
        or not isinstance(source["sha256"], str)
        or not _SHA256.fullmatch(source["sha256"])
    ):
        raise ValueError("invalid tensor source identity")
    if source_path is not None:
        with open(source_path, "rb") as stream:
            if os.fstat(stream.fileno()).st_size != source["bytes"] or _digest(stream) != source["sha256"]:
                raise ValueError("tensor source identity mismatch")
    entries = manifest["arrays"]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 128:
        raise ValueError("invalid tensor array count")
    total_bytes = 0
    for name, entry in entries.items():
        if (
            not _NAME.fullmatch(name)
            or not isinstance(entry, dict)
            or set(entry) != {"file", "dtype", "shape", "bytes", "sha256"}
            or entry["file"] != f"{name}.npy"
            or entry["dtype"] not in ("<f4", "<f8")
            or not isinstance(entry["shape"], list)
            or not 1 <= len(entry["shape"]) <= 3
            or not all(_integer(size, 1, 5_000_000) for size in entry["shape"])
            or not _integer(entry["bytes"], 1)
            or not isinstance(entry["sha256"], str)
            or not _SHA256.fullmatch(entry["sha256"])
        ):
            raise ValueError("invalid tensor array descriptor")
        count = 1
        for size in entry["shape"]:
            count *= size
        if count * np.dtype(entry["dtype"]).itemsize > entry["bytes"]:
            raise ValueError("tensor shape exceeds declared file size")
        total_bytes += entry["bytes"]
        if total_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("tensor bundle exceeds size limit")
    arrays = {}
    try:
        for name, entry in entries.items():
            with _regular_file(path.parent / entry["file"]) as stream:
                if os.fstat(stream.fileno()).st_size != entry["bytes"] or _digest(stream) != entry["sha256"]:
                    raise ValueError(f"tensor checksum or size mismatch: {name}")
                stream.seek(0)
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=4096)
                elif version == (2, 0):
                    shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream, max_header_size=4096)
                else:
                    raise ValueError("unsupported tensor NPY version")
                offset = stream.tell()
                if (
                    list(shape) != entry["shape"] or fortran or dtype.str != entry["dtype"]
                    or offset + int(np.prod(shape, dtype=np.int64)) * dtype.itemsize != entry["bytes"]
                ):
                    raise ValueError(f"tensor NPY header mismatch: {name}")
                if mmap:
                    array = np.memmap(stream, dtype=dtype, mode="r", offset=offset, shape=shape, order="C")
                else:
                    stream.seek(0)
                    array = np.load(stream, allow_pickle=False, max_header_size=4096)
                array.setflags(write=False)
                arrays[name] = array
        return TensorBundle(manifest, arrays)
    except Exception:
        for array in arrays.values():
            mapping = getattr(array, "_mmap", None)
            if mapping is not None:
                mapping.close()
        raise
