"""Equal-wire complete local inventory benchmark; synthetic or existing caches."""
import argparse
import atexit
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

from bench_desktop_location_migration import Child


def reference_child(path):
    protocol_output=sys.stdout
    sys.stdout=sys.stderr
    from firesky import aerosol, desktop_data, desktop_rpc
    # The owning reader may queue area-index writes for process exit.
    # This read-only benchmark retains its calculations without publishing them.
    atexit.unregister(aerosol._cams_area_sidecar_flush)
    config=SimpleNamespace(**{k:Path(v) for k,v in json.loads(Path(path).read_text())["inventory"].items()})
    class Reference:
        def dispatch(self,method,params,progress):
            if method=="hello": return dict(protocol=1)
            if method=="inventory": return dict(datasets=desktop_data.inventory(config))
            raise ValueError("benchmark only implements inventory")
    desktop_rpc.serve(output_stream=protocol_output,service=Reference())


def production_rust_child(path):
    protocol_output=sys.stdout
    sys.stdout=sys.stderr
    from firesky import desktop_rpc

    config=SimpleNamespace(**{
        k: Path(v) for k, v in json.loads(Path(path).read_text())["inventory"].items()
    })
    desktop_rpc.serve(
        output_stream=protocol_output,
        service=desktop_rpc.DesktopService(config),
    )


def fixture(root,count):
    roots={f"{source}_cache_dir":str(root/source) for source in ("gfs","ecmwf","cams")}
    sidecars={}
    for i in range(count):
        shard=f"part-{i%32:02d}"
        date=f"202611{8+i%3:02d}00"
        hour=(i%21)*3
        payload=b"" if i%17==0 else b"synthetic non-GRIB inventory fixture\n"*4
        for source in ("gfs","ecmwf","cams"):
            directory=Path(roots[f"{source}_cache_dir"])/shard
            directory.mkdir(parents=True,exist_ok=True)
            if source=="gfs": name=f"gfs-{i:06d}.grib2"
            elif source=="ecmwf": name=f"ifs-basic-v2-{date}-f{hour:03d}.grib2"
            else: name=f"cams-optics-v2-{date[:4]}-{date[4:6]}-{date[6:8]}-0000-{hour:03d}-{i:x}.grib"
            path=directory/name
            # EC repeated paths need a unique shard too, retaining exact legacy names.
            if path.exists() and source=="ecmwf":
                directory=directory/f"copy-{i}"
                directory.mkdir()
                path=directory/name
            path.write_bytes(payload)
            if source=="gfs" and i%13:
                path.with_suffix(".grib2.json").write_text(json.dumps(dict(
                    cycle=f"2026-11-{8+i%3:02d}T00:00:00Z",forecast_hour=hour,
                    bbox=[100,104,32,28],**{"global":False})))
            if source=="cams":
                stat=path.stat()
                sidecars.setdefault(directory,{})[path.name]=dict(
                    m=stat.st_mtime_ns,s=stat.st_size,a=[32,100,28,104])
    for directory,entries in sidecars.items():
        (directory/".cams-area-index-v1.json").write_text(json.dumps(entries))
    return roots


def digest(value):
    # Python area_keys values are sets serialized as lists; compare their semantics.
    for dataset in value["datasets"]:
        for cycle in dataset["cycles"].values():
            for key,areas in cycle.get("area_keys",{}).items():
                cycle["area_keys"][key]=sorted(areas)
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(",",":")).encode()).hexdigest()

def differences(a,b,path="root"):
    if isinstance(a,dict) and isinstance(b,dict):
        for key in sorted(a.keys()|b.keys()):
            if key not in a or key not in b: yield path+"."+key,"missing key"
            else: yield from differences(a[key],b[key],path+"."+key)
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b): yield path,f"length {len(a)} != {len(b)}"
        for i,(left,right) in enumerate(zip(a,b)):
            yield from differences(left,right,f"{path}[{i}]")
    elif a!=b or type(a)!=type(b):
        yield path,f"{a!r} ({type(a).__name__}) != {b!r} ({type(b).__name__})"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin",required=True,type=Path)
    parser.add_argument("--files-per-source",default=4000,type=int)
    parser.add_argument("--trials",default=3,type=int)
    parser.add_argument("--repeats",default=5,type=int)
    parser.add_argument("--existing-workspace",type=Path,
        help="read existing .cache/{gfs,ecmwf,cams} instead of creating synthetic files")
    args=parser.parse_args()
    if sys.platform!="darwin" or not 100<=args.files_per_source<=50000 \
        or not 1<=args.trials<=10 or not 2<=args.repeats<=20:
        parser.error("requires macOS; files/source 100..50000, trials 1..10, repeats 2..20")
    binary=args.engine_bin.resolve()
    traces={"native":[],"python":[],"production-rust":[]}
    expected=None
    expected_value=None
    raw_files=None
    datasets=None
    def snapshot(roots):
        return {str(p):(p.stat().st_size,p.stat().st_mtime_ns)
            for root in roots.values() for p in Path(root).rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="firesky-inventory-") as temporary:
        root=Path(temporary)
        if args.existing_workspace:
            workspace=args.existing_workspace.resolve()
            roots={f"{source}_cache_dir":str(workspace/".cache"/source)
                   for source in ("gfs","ecmwf","cams")}
            before=snapshot(roots)
        else:
            roots=fixture(root,args.files_per_source)
        settings=root/"settings.json"
        settings.write_text(json.dumps(dict(inventory=roots)))
        for trial in range(args.trials):
            # Alternate order to avoid always favoring one backend's filesystem cache.
            order = ("native", "python", "production-rust")
            if trial % 2:
                order = tuple(reversed(order))
            for backend in order:
                env=dict(os.environ,FIRESKY_ECCODES_LIBRARY="/no/ecCodes/library")
                if args.existing_workspace:
                    env.pop("FIRESKY_ECCODES_LIBRARY",None)
                if backend=="native":
                    command=[binary,"desktop-location","--config",settings]
                    env.update(PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python")
                elif backend=="python":
                    command=[sys.executable,Path(__file__).resolve(),"--reference-child",settings]
                    env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src")
                else:
                    command=[
                        sys.executable, Path(__file__).resolve(),
                        "--production-rust-child", settings,
                    ]
                    env.update(
                        PYTHONPATH=str(Path(__file__).resolve().parents[1]/"src"),
                        FIRESKY_DESKTOP_DATA_BACKEND="rust",
                        FIRESKY_RUST_ENGINE_BIN=str(binary),
                    )
                started=time.perf_counter()
                child=Child(command,env)
                try:
                    child.call("hello",{})
                    trace={"startup_hello_ms":(time.perf_counter()-started)*1000,"scans_ms":[]}
                    for _ in range(args.repeats):
                        started=time.perf_counter()
                        value=child.call("inventory",{})
                        trace["scans_ms"].append((time.perf_counter()-started)*1000)
                        actual=digest(value)
                        if expected is None:
                            expected=actual
                            expected_value=value
                            datasets=[dict(source=v["source"],
                                total_file_count=v["total_file_count"],
                                file_count=v["file_count"],
                                unindexed_file_count=v["unindexed_file_count"],
                                cycles=len(v["cycles"]),
                                area_keys=len(v["area_bounds"]),
                                unresolved_areas=sum(a is None for a in v["area_bounds"].values()))
                                for v in value["datasets"]]
                        if actual!=expected:
                            import itertools
                            raise AssertionError(list(itertools.islice(differences(expected_value,value),10)))
                        counts=[v["total_file_count"] for v in value["datasets"]]
                        raw_files=sum(counts)
                        if not args.existing_workspace:
                            assert counts==[args.files_per_source]*3
                    trace["first_scan_ms"]=trace["scans_ms"][0]
                    trace["resident_scan_ms"]=statistics.median(trace["scans_ms"][1:])
                finally:
                    resources=child.close()
                trace.update(resources)
                traces[backend].append(trace)
        if args.existing_workspace:
            assert snapshot(roots)==before,"existing cache directory entries changed during read-only benchmark"
    print(json.dumps(dict(contract="desktop-inventory-benchmark-v1",
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        fixture_kind="existing-workspace" if args.existing_workspace else "synthetic",
        raw_files=raw_files,datasets=datasets,complete_response_sha256=expected,trials=traces,
        medians={k:{name:statistics.median(t[name] for t in ts)
                    for name in ts[0] if name!="scans_ms"} for k,ts in traces.items()},
        caveats=[
            "Complete original inventory outputs are checked, including GFS coverage, EC paths and CAMS areas/keys.",
            "Synthetic mode uses invalid GRIB with matching timestamp/size CAMS area sidecars; existing-workspace mode uses real files and available native ecCodes.",
            "The direct native child has Python unavailable; ecCodes is additionally unavailable in synthetic mode. The Python reference and production-rust bridge are Python processes, so neither is a Python-free application.",
            "Reference area-index exit publication is disabled; existing-workspace mode compares file names/size/mtime before and after, not full raw-content hashes.",
            "Native scans each root once and reuses file metadata; legacy inventory performs separate scanner/raw-count traversals.",
            "All timings include JSON-lines request/response parsing in the client, but exclude full-output comparison.",
            "Filesystem caches are warm after fixture preparation; first-scan samples are not cold storage performance.",
            "Alternating backend order and multiple scans do not establish P95, concurrent throughput or production cache sizes.",
            "Process CPU/RSS cover startup and all scans; no per-file integrity or forecast readiness is claimed.",
            "This optional subset does not remove the client's main Python process or prove application startup/RSS improvement.",
        ]),indent=2))


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--reference-child": reference_child(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=="--production-rust-child":
        production_rust_child(sys.argv[2])
    else: main()
