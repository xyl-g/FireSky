"""Offline full cross-section: warm Python mmap vs fresh verified Rust process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"tests"))
from firesky import national_batch as batch
from test_rust_cross_section import fixture, compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=7)
    parser.add_argument("--side", type=int, default=128)
    args = parser.parse_args()
    if not 1 <= args.trials <= 30 or not 32 <= args.side <= 256:
        parser.error("trials must be 1..30 and side 32..256")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    binary = args.engine_bin.resolve()
    os.environ["FIRESKY_ASTRONOMY_BACKEND"] = "python"
    os.environ["FIRESKY_CLOUD_BACKEND"] = "python"
    source, reference, params = fixture(root, rows=args.side, cols=args.side, spacing=24/(args.side-1))
    trials = []
    canonical = None
    for trial in range(args.trials):
        for backend in ("python", "rust") if trial % 2 == 0 else ("rust", "python"):
            started = time.perf_counter()
            peak_rss = None
            if backend == "python":
                result = batch.build_national_cross_section(reference, params["point"], 0)
                result = json.loads(json.dumps(result, allow_nan=False))
            else:
                completed = subprocess.run(
                    ["/usr/bin/time", "-l", str(binary), "serve"],
                    input=json.dumps(dict(id="bench", method="build_stored_cross_section", params=params))+"\n",
                    capture_output=True, text=True, check=True, timeout=120,
                    env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"),
                )
                response = json.loads(completed.stdout)
                if response["type"] != "result":
                    raise RuntimeError(response)
                result = response["result"]
                match = re.search(r"(\d+)\s+maximum resident set size", completed.stderr)
                peak_rss = int(match[1]) if match else None
            elapsed = (time.perf_counter()-started)*1000
            if canonical is None:
                canonical = result
            compare(result, canonical)
            trials.append(dict(trial=trial, backend=backend, elapsed_ms=elapsed,
                               process_peak_rss_bytes=peak_rss))
    canonical_bytes = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    print(json.dumps(dict(
        contract="cross-section-benchmark-v1", trials=trials,
        median_ms={backend: statistics.median(r["elapsed_ms"] for r in trials if r["backend"] == backend)
                   for backend in ("python", "rust")},
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        grid_cells=args.side**2, pressure_levels=len(source["pressure_levels"]), source_frames=4,
        sampled_frames=2, distances=41, radius_km=1000,
        numerical_output_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        scope="warm-OS-cache; warm Python process+mmap; fresh Rust child+full checksum verification; complete JSON output",
        excludes="fixture generation; raw GRIB decode; live download; whole app; Python process RSS",
    ), indent=2))


if __name__ == "__main__":
    main()
