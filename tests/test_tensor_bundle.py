import hashlib
import json

import numpy as np
import pytest

from firesky.tensor_bundle import MAX_BUNDLE_BYTES, load_tensor_bundle


@pytest.fixture
def bundle_files(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    source = tmp_path / "source.grib"
    source.write_bytes(b"pinned source")
    values = np.array([[1, np.nan], [np.inf, -0.0]], dtype="<f4")
    np.save(root / "values.npy", values, allow_pickle=False)
    payload = (root / "values.npy").read_bytes()
    manifest = {
        "schema": "firesky.tensor-bundle.v1", "producer": "firesky-engine/test",
        "kind": "test", "source": {"bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "metadata": {},
        "arrays": {"values": {"file": "values.npy", "dtype": "<f4", "shape": [2, 2],
                              "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}},
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest, source, values


@pytest.mark.parametrize("mmap", [False, True])
def test_bundle_roundtrip_and_owned_mapping(bundle_files, mmap):
    path, _, source, expected = bundle_files
    bundle = load_tensor_bundle(path, kind="test", source_path=source, mmap=mmap)
    actual = bundle.arrays["values"]
    np.testing.assert_array_equal(actual, expected)
    assert np.signbit(actual[1, 1]) and not actual.flags.writeable
    if mmap:
        assert isinstance(actual, np.memmap)
        actual._mmap.close()


@pytest.mark.parametrize("mutation", [
    "kind", "schema", "source", "dtype", "shape", "rank", "path", "size", "hash",
    "fortran", "object", "trailing", "shape_header", "zero", "bool_shape", "limit", "no_arrays",
])
def test_bundle_invalid_contracts_fail_closed(bundle_files, mutation):
    path, manifest, source, _ = bundle_files
    entry = manifest["arrays"]["values"]
    array_path = path.parent / entry["file"]
    if mutation == "kind":
        manifest["kind"] = "different"
    elif mutation == "schema":
        manifest["schema"] = "unknown"
    elif mutation == "source":
        source.write_bytes(b"source changed")
    elif mutation == "dtype":
        entry["dtype"] = "<f8"
    elif mutation == "shape":
        entry["shape"] = [1, 4]
    elif mutation == "rank":
        entry["shape"] = [1, 1, 1, 4]
    elif mutation == "path":
        entry["file"] = "../values.npy"
    elif mutation == "size":
        entry["bytes"] += 1
    elif mutation == "hash":
        entry["sha256"] = "0" * 64
    elif mutation == "fortran":
        np.save(array_path, np.asfortranarray(np.ones((2, 2), dtype="<f4")))
    elif mutation == "object":
        np.save(array_path, np.array([["a", "b"], ["c", "d"]], dtype=object))
    elif mutation == "trailing":
        with array_path.open("ab") as stream:
            stream.write(b"extra")
    elif mutation == "shape_header":
        np.save(array_path, np.ones((1, 4), dtype="<f4"))
    elif mutation == "zero":
        entry["shape"] = [0]
    elif mutation == "bool_shape":
        entry["shape"] = [True, 4]
    elif mutation == "limit":
        entry["bytes"] = MAX_BUNDLE_BYTES + 1
    elif mutation == "no_arrays":
        manifest["arrays"] = {}
    if mutation in {"fortran", "object", "trailing", "shape_header"}:
        payload = array_path.read_bytes()
        entry.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_tensor_bundle(path, kind="test", source_path=source)


@pytest.mark.parametrize("target", ["manifest", "array", "directory"])
def test_bundle_refuses_symlinks(bundle_files, tmp_path, target):
    path, _, _, _ = bundle_files
    if target == "manifest":
        real = path.with_name("manifest-real.json")
        path.rename(real)
        path.symlink_to(real)
    elif target == "array":
        array = path.parent / "values.npy"
        real = array.with_name("real.npy")
        array.rename(real)
        array.symlink_to(real)
    else:
        real = tmp_path / "real-directory"
        path.parent.rename(real)
        path.parent.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        load_tensor_bundle(path)


def test_bundle_refuses_duplicate_manifest_keys(bundle_files):
    path, _, _, _ = bundle_files
    path.write_text('{"arrays":{},"arrays":{}}')
    with pytest.raises(ValueError, match="duplicate"):
        load_tensor_bundle(path)


@pytest.mark.parametrize("mmap", [False, True])
def test_float64_roundtrip_preserves_sub_f32_precision(bundle_files, mmap):
    path, manifest, source, _ = bundle_files
    expected = np.array([[1.00000000001, np.nan], [np.inf, -0.0]], dtype="<f8")
    array = path.parent / "values.npy"
    np.save(array, expected, allow_pickle=False)
    payload = array.read_bytes()
    manifest["arrays"]["values"].update(
        dtype="<f8", bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
    )
    path.write_text(json.dumps(manifest))
    result = load_tensor_bundle(path, source_path=source, mmap=mmap).arrays["values"]
    assert result.dtype == expected.dtype
    np.testing.assert_array_equal(result.view("u8"), expected.view("u8"))
    if mmap:
        result._mmap.close()
