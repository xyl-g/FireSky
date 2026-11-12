"""The native radar metrics use the existing NumPy implementation as an oracle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest

from firesky.radar import radar_image_metrics

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine():
    path = Path(os.environ.get("FIRESKY_RUST_ENGINE_BIN", ROOT / "engine/target/release/firesky-engine"))
    if not path.is_file():
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail("build the current Rust engine before running the strict gate")
        pytest.skip("Rust engine is not built")
    return path


def rpc(engine, params):
    process = subprocess.run(
        [str(engine), "serve"],
        input=json.dumps({"id": "radar-test", "method": "assess_radar_images", "params": params}) + "\n",
        text=True, capture_output=True, check=True,
    )
    return json.loads(process.stdout)


@pytest.mark.parametrize(
    ("lat", "lon", "azimuth"),
    [(30.25, 120.17, 270.0), (65.0, 120.0, 90.0), (-40.0, -120.0, 15.0), (0.0, 179.8, 270.0)],
)
def test_radar_metrics_match_numpy(engine, tmp_path, lat, lon, azimuth):
    colors = np.asarray([
        [0, 0, 0, 0], [1, 2, 3, 255], [12, 34, 56, 255],
        [56, 78, 90, 255], [98, 76, 54, 255],
    ], dtype=np.uint8)
    dbz = [-32.0, 5.0, 20.0, 40.0, 55.0]
    palette = {tuple(color): value for color, value in zip(colors, dbz)}
    rng = np.random.default_rng(20261111)
    radar = Image.fromarray(colors[rng.integers(0, len(colors), size=(256, 256))])
    coverage_values = np.zeros((256, 256, 4), dtype=np.uint8)
    coverage_values[:, :, 3] = (rng.random((256, 256)) < 0.17).astype(np.uint8) * 255
    coverage = Image.fromarray(coverage_values)
    radar_path, coverage_path = tmp_path / "radar.png", tmp_path / "coverage.png"
    palette_path = tmp_path / "palette.csv"
    radar.save(radar_path)
    coverage.save(coverage_path)
    palette_path.write_text(
        "\ufeffdBZ,Universal Blue\n" + "".join(
            f"{value},#{''.join(f'{channel:02x}' for channel in color)}\n"
            for color, value in zip(colors, dbz)
        )
    )
    expected = radar_image_metrics(radar, coverage, palette, lat, lon, azimuth, 6)
    response = rpc(engine, {
        "radar_path": str(radar_path), "coverage_path": str(coverage_path),
        "palette_path": str(palette_path), "lat": lat, "lon": lon,
        "path_azimuth_deg": azimuth, "zoom": 6,
    })
    assert response["type"] == "result", response
    for key, value in expected.items():
        assert response["result"][key] == pytest.approx(value, abs=1e-12), key


def test_radar_rejects_mismatched_coverage_image(engine, tmp_path):
    radar, coverage = tmp_path / "radar.png", tmp_path / "coverage.png"
    palette = tmp_path / "palette.csv"
    Image.new("RGBA", (256, 256)).save(radar)
    Image.new("RGBA", (32, 32)).save(coverage)
    palette.write_text("dBZ,Universal Blue\n-32,#00000000\n")
    response = rpc(engine, {
        "radar_path": str(radar), "coverage_path": str(coverage),
        "palette_path": str(palette), "lat": 30.0, "lon": 120.0,
        "path_azimuth_deg": 270.0,
    })
    assert response["type"] == "error", response
    assert "invalid radar image metrics request" in response["error"]["message"]
