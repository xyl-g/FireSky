"""Native HSD calibration contracts; Python is used only as the test oracle."""
from __future__ import annotations

import bz2
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import time
import hashlib
from datetime import datetime, timezone

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine():
    path = Path(os.environ.get("FIRESKY_RUST_ENGINE_BIN", ROOT / "engine/target/release/firesky-engine"))
    if not path.is_file():
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail("build the current Rust engine before running the strict gate")
        pytest.skip("Rust engine is not built")
    return path


def rpc(engine, method, params):
    process = subprocess.run(
        [str(engine), "serve"],
        input=json.dumps({"id": "hsd-test", "method": method, "params": params}) + "\n",
        text=True, capture_output=True, check=True,
    )
    return json.loads(process.stdout)


def raw_file(path, band, *, multi_stream=False):
    lengths = [282, 50, 127, 139, 147, 259, 47, 61, 45, 47, 259]
    blocks = []
    for index, length in enumerate(lengths, 1):
        block = bytearray(length)
        block[0] = index
        struct.pack_into("<I" if index == 10 else "<H", block, 1, length)
        blocks.append(block)
    basic, data, projection, _, calibration, _, segment = blocks[:7]
    struct.pack_into("<H", basic, 3, 11)
    basic[6:16] = b"Himawari-9"
    basic[38:42] = b"FLDK"
    struct.pack_into("<H", basic, 44, 540)
    instant = datetime(2026, 11, 8, 5, 40, tzinfo=timezone.utc)
    struct.pack_into("<d", basic, 46, instant.timestamp() / 86400 + 40587)
    struct.pack_into("<II", basic, 70, sum(lengths), 5500 * 550 * 2)
    struct.pack_into("<HHH", data, 3, 16, 5500, 550)
    struct.pack_into("<dIIffddd", projection, 3, 140.7, 20466275, 20466275,
                     2750.5, 2750.5, 42164, 6378.137, 6356.7523)
    struct.pack_into("<HdHHHdd", calibration, 3, band, 10.4, 12, 65535, 65534, 0.005, 0.0)
    struct.pack_into("<ddddddddd", calibration, 35, 0, 1, 0, 0, 1, 0,
                     299792458, 6.62607015e-34, 1.380649e-23)
    segment[3:5] = bytes([10, 6])
    struct.pack_into("<H", segment, 5, 2751)
    pixels = np.full((550, 5500), 1000, dtype="<u2").tobytes()
    header = b"".join(blocks)
    path.write_bytes(
        bz2.compress(header) + bz2.compress(pixels) if multi_stream
        else bz2.compress(header + pixels)
    )
    return path


def request_file(tmp_path, files, **overrides):
    payload = {
        "raw_files": [str(path) for path in files],
        "scan_time_utc": "2026-11-08T05:40:00Z",
        "lat": 0.0, "lon": 140.7, "radius_km": 100.0,
        "rows": [2750, 2752], "cols": [2750, 2752],
        **overrides,
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload))
    return path


@pytest.mark.parametrize("multi_stream", [False, True])
def test_raw_calibration_is_native_and_kelvin(engine, tmp_path, multi_stream):
    files = [raw_file(tmp_path / f"B{band}.bz2", band, multi_stream=multi_stream)
             for band in [7, 13, 15]]
    request = request_file(tmp_path, files)
    cache_dir = tmp_path / "satellite"
    response = rpc(engine, "decode_raw_hsd_to_files", {
        "path": str(request), "output_dir": str(tmp_path), "cache_dir": str(cache_dir),
    })
    assert response["type"] == "result", response
    result = response["result"]
    assert result["runtime_python_used"] is False
    assert result["shape"] == [2, 2]
    assert result["valid_time_utc"] == "2026-11-08T05:45:00+00:00"
    manifest = Path(result["manifest_path"])
    wavelength = 10.4e-6
    expected = (6.62607015e-34 * 299792458 / (1.380649e-23 * wavelength)) / math.log(
        2 * 6.62607015e-34 * 299792458**2 / (5 * 1e6 * wavelength**5) + 1
    )
    for band in [7, 13, 15]:
        values = np.load(manifest.parent / f"band_b{band:02}.npy")
        np.testing.assert_allclose(values, expected, rtol=0, atol=3e-5)
    assert np.isfinite(np.load(manifest.parent / "latitudes.npy")).all()
    assert np.isfinite(np.load(manifest.parent / "longitudes.npy")).all()
    assert Path(result["compact_path"]).is_file()
    cached = rpc(engine, "desktop_nowcast_hsd_cache", {
        "cache_dir": str(cache_dir), "reference_time_utc": "2026-11-08T05:50:00Z",
        "lat": 0.0, "lon": 140.7, "radius_km": 100.0, "count": 1,
    })
    assert cached["type"] == "result", cached
    assert cached["result"]["frames"][0]["source"] == result["source"]


def test_raw_calibration_rejects_missing_band(engine, tmp_path):
    files = [raw_file(tmp_path / f"B{band}.bz2", band) for band in [7, 13]]
    request = request_file(tmp_path, files)
    response = rpc(engine, "decode_raw_hsd_to_files", {"path": str(request), "output_dir": str(tmp_path)})
    assert response["type"] == "error"
    assert "missing required" in response["error"]["message"]


def test_real_raw_hsd_matches_satpy(engine, tmp_path):
    raw_root = ROOT / ".cache/himawari-hsd/raw/20261108T0540Z"
    files = sorted(raw_root.glob("*.DAT.bz2"))
    if len(files) != 6:
        pytest.skip("pinned six-segment real HSD fixture is unavailable")
    satpy = pytest.importorskip("satpy")
    from firesky.satellite_cloud_column import SegmentedHimawariHsdFrameProvider
    from types import SimpleNamespace

    point = SimpleNamespace(lat=30.25, lon=120.17)
    rows, cols = SegmentedHimawariHsdFrameProvider._region_indices(point, 1000.0)
    request = request_file(tmp_path, files, lat=point.lat, lon=point.lon,
                           radius_km=1000.0, rows=rows, cols=cols)
    started = time.perf_counter()
    response = rpc(engine, "decode_raw_hsd_to_files", {"path": str(request), "output_dir": str(tmp_path)})
    native_seconds = time.perf_counter() - started
    assert response["type"] == "result", response
    root = Path(response["result"]["manifest_path"]).parent
    scene = satpy.Scene(filenames=[str(path) for path in files], reader="ahi_hsd")
    scene.load(["B07", "B13", "B15"])
    slices = (slice(*rows), slice(*cols))
    maximum_errors = {}
    masks_equal = {}
    for name in ["B07", "B13", "B15"]:
        expected = np.asarray(scene[name].data[slices].compute(), dtype=np.float32)
        expected = np.where((expected < 100) | (expected > 400), np.nan, expected)
        actual = np.load(root / f"band_{name.lower()}.npy")
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-4, equal_nan=True)
        maximum_errors[name] = float(np.nanmax(np.abs(actual - expected)))
        masks_equal[name] = bool(np.array_equal(np.isnan(actual), np.isnan(expected)))
    lon, lat = scene["B13"].attrs["area"].get_lonlats(data_slice=slices, dtype=np.float32)
    np.testing.assert_allclose(np.load(root / "latitudes.npy"), lat, rtol=0, atol=1e-5)
    np.testing.assert_allclose(np.load(root / "longitudes.npy"), lon, rtol=0, atol=2e-5)
    print(json.dumps({
        "fixture": str(raw_root), "rows": rows, "cols": cols,
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        "max_temperature_error_k": maximum_errors, "nan_masks_equal": masks_equal,
        "max_lat_error_deg": float(np.max(np.abs(np.load(root / "latitudes.npy") - lat))),
        "max_lon_error_deg": float(np.max(np.abs(np.load(root / "longitudes.npy") - lon))),
        "native_decode_wall_seconds": native_seconds,
    }, sort_keys=True))
