import shutil
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def preparation_checks(tmp_path_factory):
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        pytest.skip("Native preparation checks require the macOS Swift compiler")
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path_factory.mktemp("native-ui") / "preparation-checks"
    subprocess.run(
        [
            "swiftc",
            str(root / "apps/macos/Sources/FireSkyNative/PreparationActions.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/DownloadProbe.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/ObservationDate.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/ForecastComparison.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/MapZoom.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/NowcastPresentation.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/RegionalForecast.swift"),
            str(root / "apps/macos/Sources/FireSkyNative/RegionalMapLayers.swift"),
            str(root / "apps/macos/Tests/PreparationActionsChecks.swift"),
            "-o", str(binary),
        ],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return binary


@pytest.mark.parametrize("state", [
    "missing", "ready", "blocked", "copy", "date", "calendar",
    "comparison-empty", "comparison-pair", "comparison-partial",
    "comparison-curve", "comparison-invalid",
    "aod-shared", "aod-range", "aod-partial", "aod-source",
    "nowcast-curve", "nowcast-aod", "map-zoom", "map-zoom-limits",
    "nowcast-metrics", "nowcast-profile",
    "regional-empty", "regional-models", "regional-missing", "regional-grid",
    "regional-camera", "regional-layers", "download-probe",
])
def test_native_preparation_actions(preparation_checks, state):
    subprocess.run(
        [str(preparation_checks), state],
        check=True, capture_output=True, text=True, timeout=10,
    )


def test_regional_palette_matches_web_renderer(preparation_checks):
    node = shutil.which("node")
    if not node:
        pytest.skip("Web palette parity requires Node.js with TypeScript support")
    root = Path(__file__).resolve().parents[1]
    native = subprocess.run(
        [str(preparation_checks), "regional-web-palette"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    web = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", """
import { burnColor } from './frontend/src/lib/utils/cloud-render.ts';
const colors = Array.from({length: 251}, (_, index) => {
    const score = index / 100 / 2.5;
    const alpha = score <= 0.001 ? 12 : Math.round(42 + 190 * Math.sqrt(Math.min(1, score)));
    return [...burnColor(score), alpha];
});
console.log(JSON.stringify(colors));
"""],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    )
    assert json.loads(native.stdout) == json.loads(web.stdout)
