"""Sequential offline ADS retrieval benchmark against the actual Python SDK."""
import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"tests"))
from pytest import MonkeyPatch
from firesky import aerosol
from test_rust_cams_data import ads_server, optical_bytes, params, result, AREA, CYCLE


def snapshot(rows):
    return {path.name: dict(bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for path in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir",type=Path,required=True)
    parser.add_argument("--engine-bin",type=Path,required=True)
    parser.add_argument("--trials",type=int,default=5)
    parser.add_argument("--constraint-delay",type=float,default=.01)
    args = parser.parse_args()
    if not 1<=args.trials<=20 or not 0<=args.constraint_delay<=1:
        parser.error("trials must be 1..20, constraint-delay must be 0..1")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True,exist_ok=True)
    targets = [CYCLE+timedelta(hours=h) for h in (3,6,9,12,15,18,21)]
    trials = []
    with MonkeyPatch.context() as patch:
        for name in ("CAMS","CAMS_DATA"):
            patch.setenv(f"FIRESKY_{name}_BACKEND","python")
        fixture = ads_server.__wrapped__(root,patch)
        endpoint,state = next(fixture)
        try:
            state.job_statuses = ["successful"]
            state.constraint_delay = args.constraint_delay
            for target in targets:
                run = CYCLE if target.hour<12 else CYCLE+timedelta(hours=12)
                lead = int((target-run).total_seconds()/3600)
                state.prebuilt[(run.isoformat(),lead)] = optical_bytes(root/f"source-{target.hour}.grib",run,lead)
            expected = None
            for trial in range(args.trials):
                row = {}
                for backend in ("python","rust"):
                    directory = root/f"trial-{trial}-{backend}"
                    if directory.exists():
                        raise RuntimeError(f"benchmark cache must be new: {directory}")
                    state.calls = Counter()
                    state.jobs.clear()
                    state.assets.clear()
                    state.requests.clear()
                    state.headers.clear()
                    started = time.perf_counter()
                    if backend=="python":
                        provider = aerosol.CamsAdsAerosolProvider(cache_dir=directory/"cams")
                        paths = list(provider.download_targets_batch(
                            AREA,targets,require_exact_valid_time=True).values())
                    else:
                        got = result(args.engine_bin.resolve(),params(
                            directory,endpoint,hours=(3,6,9,12,15,18,21),operation="batch"))
                        assert not got["missing_valid_times"]
                        paths = [Path(item["path"]) for item in got["files"]]
                    elapsed = (time.perf_counter()-started)*1000
                    current = snapshot(paths)
                    assert len(current)==len(targets)
                    if expected is None:
                        expected = current
                    assert current==expected
                    row[backend] = dict(total_ms=elapsed,requests=dict(state.calls))
                    if backend=="rust":
                        row[backend]["timings_ms"] = got["timings_ms"]
                trials.append(row)
        finally:
            next(fixture,None)
    encoded = json.dumps(expected,sort_keys=True,separators=(",",":")).encode()
    print(json.dumps(dict(scope="offline-ADS-cache-miss-through-validated-raw-publication",
        live_provider=False,constraint_delay_seconds=args.constraint_delay,
        cached_valid_times=len(targets),trials=trials,
        medians_ms={backend:statistics.median(row[backend]["total_ms"] for row in trials)
                    for backend in ("python","rust")},
        stable_files_identity=dict(bytes=len(encoded),sha256=hashlib.sha256(encoded).hexdigest())),indent=2))


if __name__=="__main__":
    main()
