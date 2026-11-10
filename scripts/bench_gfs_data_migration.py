"""Synthetic cache microbenchmark; no remote requests or weather computation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

from firesky import gfs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--rust-example", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path)
    parser.add_argument("--entries", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--find-iterations", type=int, default=300)
    parser.add_argument("--reuse-iterations", type=int, default=10)
    parser.add_argument("--bridge-iterations", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.entries <= 100_000:
        parser.error("entries must be 1..100000")
    os.environ["FIRESKY_GFS_DATA_BACKEND"] = "python"
    root = args.cache_dir.resolve()
    client = gfs.GfsNomadsClient(cache_dir=root)
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    bbox = (100, 102, 1, 0)
    url = client.build_subset_url(cycle, 9, bbox)
    target = root / "2026-11-09/00Z/0p25" / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.grib2"
    # The bytes intentionally are not a decoded meteorological fixture.
    for i in range(args.entries):
        path = target if i == args.entries - 1 else root / f"older/{i // 50:04}/{i:06}.grib2"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GRIB-cache-benchmark-only-7777")
        path.with_suffix(".grib2.json").write_text(json.dumps(dict(
            model="gfs", cycle="2026-11-09T00:00:00Z", forecast_hour=9 if path == target else 6,
            file=path.name, url=url, bbox=bbox)))
    assert client.find_covering_cache(cycle, 9, bbox, url) == target
    output = dict(entries=args.entries, cache_dir=str(root), index_bytes=gfs._gfs_metadata_index_paths(root)[0].stat().st_size,
                  measurement="warm-in-process-cache-only", network=False, modes={})
    for mode, iterations in (("find", args.find_iterations), ("reuse", args.reuse_iterations)):
        def run():
            if mode == "find":
                path = client.find_covering_cache(cycle, 9, bbox, url)
            else:
                path = client.download(url, cycle, 9, bbox)
            assert path == target
        python_times, rust_times, bridge_times = [], [], []
        for _ in range(args.rounds):
            run()
            start = time.perf_counter()
            for _ in range(iterations):
                run()
            python_times.append((time.perf_counter() - start) * 1000 / iterations)
            result = subprocess.run(
                [str(args.rust_example.resolve()), str(root), mode, str(iterations), str(target)],
                text=True, capture_output=True, check=True,
                env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
            row = json.loads(result.stdout)
            assert row["path"] == str(target)
            rust_times.append(row["ms_per_call"])
            if args.engine_bin:
                from firesky import rust_gfs_data
                os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(args.engine_bin.resolve())
                def bridge():
                    path = (rust_gfs_data.find(client, cycle, 9, bbox, url) if mode == "find"
                            else rust_gfs_data.download(client, url, cycle, 9, bbox))
                    assert path == target
                bridge()
                start = time.perf_counter()
                for _ in range(args.bridge_iterations):
                    bridge()
                bridge_times.append((time.perf_counter() - start) * 1000 / args.bridge_iterations)
        output["modes"][mode] = dict(iterations=iterations, python_ms=python_times,
                                    rust_ms=rust_times, python_median_ms=statistics.median(python_times),
                                    rust_median_ms=statistics.median(rust_times))
        if bridge_times:
            output["modes"][mode].update(
                python_bridge_ms=bridge_times, python_bridge_median_ms=statistics.median(bridge_times),
                bridge_iterations=args.bridge_iterations)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
