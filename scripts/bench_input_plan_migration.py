"""Build real synthetic GRIB caches outside the Rust planner timer."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_rust_input_plan import recipe, reference, seed_gfs, seed_cams
from pytest import MonkeyPatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--rust-example", type=Path, required=True)
    parser.add_argument("--points", type=int, default=1)
    parser.add_argument("--model-grid", action="store_true")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    request = recipe(root, count=args.points)
    os.environ["FIRESKY_GFS_DATA_BACKEND"] = "python"
    with MonkeyPatch.context() as patch:
        for name in ("ASTRONOMY", "GFS_DATA", "GFS_DECODE", "CAMS", "CLOUD", "EMPIRICAL"):
            patch.setenv(f"FIRESKY_{name}_BACKEND", "python")
        expected = reference(request, patch)
    hours = sorted({h+d for h in expected["hours"] for d in (-1,0,1) if h+d >= 0}) if args.model_grid else expected["hours"]
    grids = seed_gfs(Path(request["gfs"]["cache_dir"]), hours)
    if args.model_grid:
        for point in request["points"]:
            point["elevation_source"] = "model-grid"
            point.pop("elevation_m")
        with MonkeyPatch.context() as patch:
            patch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
            patch.setenv("FIRESKY_ASTRONOMY_BACKEND", "python")
            expected = reference(request, patch, grids=grids)
    seed_cams(Path(request["cams_cache_dir"]), expected["targets"], expected["area"])
    path = root/"request.json"
    path.write_text(json.dumps(request))
    trials = []
    for _ in range(args.trials):
        result = subprocess.run([str(args.rust_example.resolve()), str(path), str(args.iterations)],
            text=True, capture_output=True, check=True,
            env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python"))
        trials.append(json.loads(result.stdout))
    assert all(row["plan_identity"] == trials[0]["plan_identity"] for row in trials)
    print(json.dumps(dict(point_count=args.points, model_grid=args.model_grid, trials=trials,
        median_ms=statistics.median(row["ms_per_plan"] for row in trials)), indent=2))


if __name__ == "__main__":
    main()
