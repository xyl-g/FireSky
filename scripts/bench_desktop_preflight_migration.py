"""Complete resolved-plan cache-check benchmark, including the production bridge."""
import argparse
import atexit
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

from bench_desktop_location_migration import Child

ROOT = Path(__file__).resolve().parents[1]


def reference_child(path):
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    from firesky import aerosol, desktop_data, desktop_rpc
    from firesky.web_server import WebServerConfig
    atexit.unregister(aerosol._cams_area_sidecar_flush)
    if os.environ.get("FIRESKY_PREFLIGHT_BENCH_TRANSIENT") == "1":
        from firesky import rust_desktop_data, rust_grid
        rust_desktop_data.call_engine = rust_grid.call_engine
    class Reference:
        def dispatch(self, method, params, progress):
            if method == "hello":
                return dict(protocol=1)
            if method != "check_desktop_data":
                raise ValueError("benchmark only implements resolved-plan preflight")
            contexts = {}
            for p in params["plans"]:
                contexts[p["model_source"]] = dict(
                    common=dict(model_source=p["model_source"], _compute_region=p["compute_region"]),
                    cycle=datetime.fromisoformat(p["cycle_utc"]),
                    times=[datetime.fromisoformat(t) for t in p["cams_valid_times"]],
                    bbox=tuple(p["bbox"]), area=p["area"], hours=p["hours"],
                    points=[{}]*p["point_count"], terrain_pending=p["terrain_pending"])
            # Isolate cache checks with exactly the already resolved event plans.
            desktop_data.model_context = lambda payload, **kw: contexts[payload["model_source"]]
            config = WebServerConfig(**{k:Path(v) for k,v in params["config"].items()})
            report = desktop_data.check_model_data(
                dict(model_source="both"), config=config, probe=False, fast=params["fast"])
            return dict(report=report)
    desktop_rpc.serve(output_stream=protocol_output, service=Reference())


def fixture(root, count, strict):
    sys.path.insert(0, str(ROOT/"tests"))
    from test_rust_cams import write_cams
    from firesky.gfs import GfsNomadsClient, _load_or_build_gfs_metadata_index
    cycle = datetime(2026,11,9,tzinfo=timezone.utc)
    bbox = [100.,102.,1.,0.]
    area = [1.,100.,0.,102.]
    roots = {f"{s}_cache_dir":str(root/s) for s in ("gfs","ecmwf","cams")}
    for i in range(count):
        for source in ("gfs","ecmwf","cams"):
            directory = Path(roots[f"{source}_cache_dir"])/f"part-{i%32:02d}"
            directory.mkdir(parents=True,exist_ok=True)
            hour = (i%21)*3
            day = 1+i%3
            if source=="gfs":
                path = directory/f"gfs-{i}.grib2"
                path.write_bytes(b"background metadata fixture")
                path.with_suffix(".grib2.json").write_text(json.dumps(dict(
                    cycle=f"2026-10-{day:02d}T00:00:00Z", forecast_hour=hour, bbox=bbox)))
            elif source=="ecmwf":
                directory = directory/f"copy-{i}"
                directory.mkdir()
                (directory/f"ifs-basic-v2-202610{day:02d}00-f{hour:03}.grib2").write_bytes(b"inventory fixture")
            else:
                (directory/f"cams-optics-v2-2026-10-{day:02d}-0000-{hour:03}-{i:x}.grib").write_bytes(b"unrelated target")
    client = GfsNomadsClient(cache_dir=Path(roots["gfs_cache_dir"]),resolution="0p25")
    from firesky.aerosol import cams_area_key
    for hour in (6,9):
        path = Path(roots["gfs_cache_dir"])/f"f{hour:03}.grib2"
        path.write_bytes(b"GRIB"+b"metadata coverage fixture"*100+b"7777")
        path.with_suffix(".grib2.json").write_text(json.dumps(dict(model="gfs",
            cycle=cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),forecast_hour=hour,
            file=path.name,bbox=bbox,url=client.build_subset_url(cycle,hour,bbox))))
        (Path(roots["ecmwf_cache_dir"])/f"ifs-basic-v2-2026110900-f{hour:03}.grib2").write_bytes(b"EC filename admission")
        path = Path(roots["cams_cache_dir"])/f"cams-optics-v2-2026-11-09-0000-{hour:03}-{cams_area_key(area)}.grib"
        if strict:
            write_cams(path,hour=hour)
        else:
            path.write_bytes(b"fast exact-area filename admission")
    # Give all measured processes the same prebuilt interoperable index.
    _load_or_build_gfs_metadata_index(Path(roots["gfs_cache_dir"]))
    plans = [dict(model_source=m, cycle_utc=cycle.isoformat(),
        event_start=(cycle+timedelta(hours=6)).isoformat(),bbox=bbox,area=area,
        hours=[6,9],cams_valid_times=[(cycle+timedelta(hours=h)).isoformat() for h in (6,9)],
        point_count=1,terrain_pending=False,compute_region=None,notes=[]) for m in ("gfs","ec")]
    return dict(plans=plans,config=roots,fast=not strict)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin",required=True,type=Path)
    parser.add_argument("--files-per-source",type=int,default=4000)
    parser.add_argument("--trials",type=int,default=3)
    parser.add_argument("--repeats",type=int,default=5)
    parser.add_argument("--strict",action="store_true")
    args = parser.parse_args()
    if sys.platform!="darwin" or not 100<=args.files_per_source<=20000 \
        or not 1<=args.trials<=10 or not 2<=args.repeats<=20:
        parser.error("requires macOS; files/source 100..20000, trials 1..10, repeats 2..20")
    binary = args.engine_bin.resolve()
    for name in ("DESKTOP_DATA","GFS_DATA","ECMWF_DATA","CAMS_DATA","CAMS","ASTRONOMY"):
        os.environ[f"FIRESKY_{name}_BACKEND"]="python"
    traces = {k:[] for k in ("native","python","bridge","transient_bridge")}
    expected = None
    with tempfile.TemporaryDirectory(prefix="firesky-preflight-bench-") as temporary:
        root = Path(temporary)
        params = fixture(root,args.files_per_source,args.strict)
        settings = root/"request.json"
        settings.write_text(json.dumps(params))
        for trial in range(args.trials):
            backends = list(traces)
            backends = backends[trial%len(backends):]+backends[:trial%len(backends)]
            for backend in backends:
                env = dict(os.environ,PYTHONPATH=str(ROOT/"src"),
                    FIRESKY_RUST_ENGINE_BIN=str(binary))
                if not args.strict:
                    env["FIRESKY_ECCODES_LIBRARY"]="/no/native/library"
                if backend=="native":
                    command=[binary,"serve"]
                    env.update(PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python")
                else:
                    command=[sys.executable,Path(__file__).resolve(),"--reference-child",settings]
                    env["FIRESKY_DESKTOP_DATA_BACKEND"]="python" if backend=="python" else "rust"
                    if backend=="transient_bridge":
                        env["FIRESKY_PREFLIGHT_BENCH_TRANSIENT"]="1"
                started=time.perf_counter()
                child=Child(command,env)
                try:
                    child.call("hello",{})
                    trace=dict(startup_hello_ms=(time.perf_counter()-started)*1000,scans_ms=[])
                    for _ in range(args.repeats):
                        started=time.perf_counter()
                        report=child.call("check_desktop_data",params)["report"]
                        trace["scans_ms"].append((time.perf_counter()-started)*1000)
                        assert report["ready"],report
                        report.pop("checked_at",None)
                        encoded=json.dumps(report,sort_keys=True,allow_nan=False,separators=(",",":"))
                        actual=hashlib.sha256(encoded.encode()).hexdigest()
                        if expected is None:
                            expected=actual
                        assert actual==expected,"complete resolved-plan report changed"
                    trace["first_scan_ms"]=trace["scans_ms"][0]
                    trace["resident_scan_ms"]=statistics.median(trace["scans_ms"][1:])
                finally:
                    resources=child.close()
                trace.update(resources)
                traces[backend].append(trace)
    print(json.dumps(dict(contract="desktop-preflight-benchmark-v1",
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),strict=args.strict,
        raw_files=3*(args.files_per_source+2),complete_report_sha256=expected,
        trials=traces,medians={k:{n:statistics.median(t[n] for t in ts)
            for n in ts[0] if n!="scans_ms"} for k,ts in traces.items()},
        caveats=[
            "Complete original dual-model reports are compared; event plans are frozen before timing.",
            "This excludes Python model_context planning, publication I/O, sessions, confirmation and forecast execution.",
            "The bridge is the optimized production opt-in adapter and reuses one native engine; transient_bridge recreates the original per-check subprocess behavior only inside this benchmark.",
            "Native uses a persistent child with Python unavailable; fast mode also makes native ecCodes unavailable.",
            "Only strict mode has real target CAMS GRIB; background and GFS/EC files exercise legacy metadata admission.",
            "Ready means this cache-check contract, not proof of GFS/EC header validity or forecast readiness.",
            "Only two CAMS targets are decoded; unrelated background timestamps are filtered before headers.",
            "Timings include complete wire transfer and client parsing, excluding report comparison.",
            "Filesystem caches are warm after fixture creation; first scans include per-process initialization.",
            "The common GFS metadata index is built before timing; index creation/rebuild cost is excluded for every backend.",
            "Child RSS/CPU cover startup and all scans. Bridge child RSS does not aggregate transient Rust-child RSS.",
            "This is not whole-application startup, memory, P95 or production acceptance.",
        ]),indent=2))


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--reference-child":
        reference_child(sys.argv[2])
    else:
        main()
