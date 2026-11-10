"""Full stored-point output: uncached vs verified native derived-cache reuse.

Uses pinned local raw GRIB/CAMS fixtures, without downloads or Python inside
the engine. Each timed request is a fresh native process and retains all
source-domain cells, diagnostics, tensors, pressure children and report output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"src"), str(ROOT/"tests"), str(ROOT/"scripts")]
from bench_assessment_migration import pinned
from bench_ecmwf_service_migration import invoke
from firesky.tensor_bundle import load_tensor_bundle


def digest_outputs(output, path):
    bundle = load_tensor_bundle(output/"manifest.json", source_path=path)
    result = {}
    for name, array in bundle.arrays.items():
        result[name] = hashlib.sha256(array.tobytes()).hexdigest()
    for i, child in enumerate(bundle.manifest["metadata"]["profile_bundles"]):
        profile = load_tensor_bundle(output/child["manifest"], kind="regional-profiles-v1")
        result[f"profiles_{i}"] = hashlib.sha256(profile.arrays["profiles"].tobytes()).hexdigest()
    return bundle, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--model", choices=["gfs", "ecmwf-basic"], default="gfs")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("trials must be 1..20")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    request = json.loads(args.request.read_text()) if args.request else pinned(args.model)
    if args.request:
        for record in request["frames"]+request["cams"]:
            record["path"] = str((args.request.parent/record["path"]).resolve())
    for i, point in enumerate(request["points"]):
        point.setdefault("name", f"Observer {i}")
    if len(request["points"]) != 1:
        parser.error("stored point benchmark requires one observer")
    request["include_point_core"] = True
    path = root/"request.json"
    binary = args.engine_bin.resolve()
    records, canonical = [], None
    schedule = [("derived-cold", 0)]
    for trial in range(args.trials):
        backends = ("uncached", "derived-warm") if trial % 2 == 0 else ("derived-warm", "uncached")
        schedule.extend((backend, trial) for backend in backends)
    geometry = None
    for backend, trial in schedule:
        request["derived_cache"] = None if backend == "uncached" else dict(directory=str(root/"derived"))
        path.write_text(json.dumps(request))
        output = root/f"{backend}-{trial}"
        _, elapsed, rss = invoke(binary, "forecast_stored_point_to_files",
                                 dict(path=str(path), output_dir=str(output)))
        bundle, digests = digest_outputs(output, path)
        metadata = bundle.manifest["metadata"]
        if canonical is None:
            canonical = digests
            geometry = dict(metadata["geometry"], frames=len(request["frames"]),
                            pressure_levels=metadata["pressure_levels"])
        assert digests == canonical, backend
        cache = metadata["derived_cache"]
        statuses = {frame["status"] for frame in cache["frames"]}
        assert statuses == {{"uncached": "disabled", "derived-cold": "miss", "derived-warm": "hit"}[backend]}
        records.append(dict(backend=backend, trial=trial, total_ms=elapsed,
                            peak_rss_bytes=rss, timings_ms=metadata["timings_ms"], derived_cache=cache))
    print(json.dumps(dict(
        contract="native-derived-cache-benchmark-v1", model=request["model"], geometry=geometry,
        trials=records,
        medians={backend:dict(
            total_ms=statistics.median(r["total_ms"] for r in records if r["backend"] == backend),
            peak_rss_bytes=statistics.median(r["peak_rss_bytes"] for r in records if r["backend"] == backend),
        ) for backend in ("uncached", "derived-warm")},
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        all_tensor_and_profile_digests=canonical,
        caveats=[
            "Stored raw-input point forecast, not production HTTP or independent input planning.",
            "Fresh native processes, OS buffers may be warm; one cache-miss sample is not a cold latency distribution.",
            "Complete source-domain tensors and independently copied profile children are durably written every time.",
            "All main/profile float32 arrays are compared bitwise after the timed acknowledgement.",
            "Resource fingerprints and every cache array checksum remain enabled.",
            "No Python implementation performance ratio, national/P95, live-provider or deployment claim.",
        ],
    ), indent=2))


if __name__ == "__main__":
    main()
