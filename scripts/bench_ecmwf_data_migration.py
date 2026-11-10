"""Sequential offline public/basic EC transport benchmark against the owning SDK."""
import argparse
from collections import Counter
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
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"tests"))
from pytest import MonkeyPatch
import ecmwf.opendata
from ecmwf.opendata import Client as ActualClient
from firesky import ecmwf_ifs as ec
from test_rust_ecmwf_data import ec_server,ec_grib_fixture,params,result,CYCLE


def snapshot(paths):
    return {p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in paths}


def native(binary,request,measure_rss):
    if not measure_rss:
        return result(binary,request)
    completed=subprocess.run(["/usr/bin/time","-l",str(binary),"serve"],
        input=json.dumps(dict(id="ec",method="ecmwf_data",params=request))+"\n",
        capture_output=True,text=True,check=True,timeout=30,
        env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python"))
    response=json.loads(completed.stdout)
    assert response["type"]=="result",response
    match=re.search(r"^\s*(\d+)\s+maximum resident set size\s*$",completed.stderr,re.M)
    assert match,completed.stderr
    response["result"]["peak_rss_bytes"]=int(match[1])
    return response["result"]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir",type=Path,required=True)
    parser.add_argument("--engine-bin",type=Path,required=True)
    parser.add_argument("--trials",type=int,default=5)
    parser.add_argument("--extra-index-rows",type=int,default=10000)
    parser.add_argument("--index-delay",type=float,default=.01)
    parser.add_argument("--single",action="store_true")
    parser.add_argument("--measure-rss",action="store_true")
    args=parser.parse_args()
    if not 1<=args.trials<=20 or not 0<=args.extra_index_rows<=90000 or not 0<=args.index_delay<=1:
        parser.error("trials must be 1..20, rows 0..90000, delay 0..1")
    if args.measure_rss and sys.platform!="darwin":
        parser.error("--measure-rss currently uses macOS /usr/bin/time -l")
    root=args.cache_dir.resolve()
    root.mkdir(parents=True,exist_ok=True)
    hours=(6,9,12,15,18,21,24)
    trials=[]
    fixture=ec_server.__wrapped__(root,ec_grib_fixture.__wrapped__())
    endpoint,state=next(fixture)
    try:
        state.extra_index_rows=args.extra_index_rows
        state.index_delay=args.index_delay
        for hour in (0,*hours):
            state.asset(CYCLE,hour)
        expected=None
        with MonkeyPatch.context() as patch:
            patch.setenv("FIRESKY_ECMWF_DATA_BACKEND","python")
            patch.setenv("FIRESKY_ECMWF_DECODE_BACKEND","python")
            patch.setenv("NO_PROXY","*")
            patch.setenv("no_proxy","*")
            patch.setattr(ecmwf.opendata,"Client",lambda **kw: ActualClient(
                source=endpoint,model=kw["model"],resol=kw["resol"],verify=True,
                source_accept_ranges=True,source_accept_multiple_ranges=False))
            for trial in range(args.trials):
                row={}
                for backend in ("python","rust"):
                    directory=root/f"trial-{trial}-{backend}"
                    if directory.exists():
                        raise RuntimeError(f"benchmark cache must be new: {directory}")
                    state.calls=Counter()
                    state.ranges.clear()
                    started=time.perf_counter()
                    if backend=="python":
                        adapter=ec.EcmwfIfsAdapter(directory/"ec")
                        paths=[adapter.download(CYCLE,hour) for hour in hours]
                    else:
                        requests = [(hour,) for hour in hours] if args.single else [hours]
                        responses=[native(args.engine_bin.resolve(),params(directory,endpoint,hours=request),
                                          args.measure_rss)
                                   for request in requests]
                        paths=[Path(v["path"]) for got in responses for v in got["files"]]
                        got=dict(elapsed_ms=sum(got["elapsed_ms"] for got in responses),
                                 timings_ms={key:sum(got.get("timings_ms",{}).get(key,0) for got in responses)
                                             for key in ("index","range","header_probe")})
                    elapsed=(time.perf_counter()-started)*1000
                    current=snapshot(paths)
                    assert len(current)==len(hours)
                    if expected is None:expected=current
                    assert current==expected
                    row[backend]=dict(total_ms=elapsed,requests={
                        k:v for k,v in state.calls.items() if isinstance(k,str)})
                    if backend=="rust":
                        row[backend]["engine_elapsed_ms"]=got["elapsed_ms"]
                        if "timings_ms" in got:row[backend]["timings_ms"]=got["timings_ms"]
                        if args.measure_rss:
                            row[backend]["max_child_peak_rss_bytes"]=max(v["peak_rss_bytes"] for v in responses)
                        if all("index_cache" in v for v in responses):
                            row[backend]["max_reported_final_index_bytes"]=max(
                                v["index_cache"]["retained_bytes_estimate"] for v in responses)
                trials.append(row)
    finally:
        next(fixture,None)
    encoded=json.dumps(expected,sort_keys=True,separators=(",",":")).encode()
    print(json.dumps(dict(scope="offline-EC-basic-cache-miss-to-validated-raw-publication",
        live_provider=False,index_delay_seconds=args.index_delay,extra_index_rows=args.extra_index_rows,
        request_mode="one-native-process-per-hour" if args.single else "one-native-process-per-batch",
        native_rss_measured=args.measure_rss,
        cached_hours=list(hours),trials=trials,
        medians_ms={backend:statistics.median(row[backend]["total_ms"] for row in trials)
                    for backend in ("python","rust")},
        stable_files_identity=dict(bytes=len(encoded),sha256=hashlib.sha256(encoded).hexdigest())),indent=2))


if __name__=="__main__":
    main()
