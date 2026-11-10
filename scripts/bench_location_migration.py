"""Pinned full-world timezone parity and cold/resident native location costs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time

import numpy as np
from timezonefinder import TimezoneFinder


def cases(finder, count):
    result = [(31.23,121.47),(43.8256,87.6168),(40.7,-74.),(-33.9,151.2),
              (51.5,-.1),(0.,-30.),(90.,0.),(-90.,0.)]
    result += [(lat,lon) for lat in (-90.,-56.,0.,60.,90.) for lon in (-180.,0.,180.)]
    for polygon in range(len(finder.zone_ids)):
        coords = finder.coords_of(polygon)
        for i in (0,coords.shape[1]//2,-1):
            lon, lat = coords[:,i]/10_000_000
            for delta in (-.0001,0.,.0001):
                result.append((float(np.clip(lat+delta,-90,90)),float(np.clip(lon+delta,-180,180))))
        for hole in finder._holes_of_poly(polygon):
            lon, lat = hole[:,0]/10_000_000
            result.append((float(lat),float(lon)))
    rng = np.random.default_rng(20261109)
    for i in range(480):
        result.append((float(rng.uniform(-90,90)),-180+i*.75))
        result.append((-90+(i%240)*.75,float(rng.uniform(-180,180))))
    while len(result)<count:
        result.append((float(rng.uniform(-90,90)),float(rng.uniform(-180,180))))
    return result


def environment():
    return dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python",
                FIRESKY_ECCODES_LIBRARY="/no/ecCodes/library")


class Engine:
    def __init__(self, binary):
        self.process = subprocess.Popen(["/usr/bin/time","-l",str(binary),"serve"],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,env=environment())
        try:
            self.call("hello",{})
        except BaseException:
            self.process.kill()
            self.process.communicate()
            raise

    def call(self, method, params):
        self.process.stdin.write(json.dumps(dict(id="bench",method=method,params=params))+"\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        if response["type"]!="result":
            raise RuntimeError(response)
        return response["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        stderr = self.process.stderr.read()
        self.process.stdout.close()
        self.process.stderr.close()
        if self.process.returncode:
            raise RuntimeError(stderr)
        rss = re.search(r"(\d+)\s+maximum resident set size",stderr)
        cpu = re.search(r"([\d.]+)\s+real\s+([\d.]+)\s+user\s+([\d.]+)\s+sys",stderr)
        return dict(native_session_peak_rss_bytes=int(rss[1]) if rss else None,
                    native_session_cpu_user_seconds=float(cpu[2]) if cpu else None,
                    native_session_cpu_system_seconds=float(cpu[3]) if cpu else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin",required=True,type=Path)
    parser.add_argument("--timezone-manifest",required=True,type=Path)
    parser.add_argument("--points",type=int,default=20_000)
    parser.add_argument("--trials",type=int,default=3)
    args = parser.parse_args()
    if args.points<20_000 or not 1<=args.trials<=10:
        parser.error("points must be >=20000 and trials 1..10")
    binary = args.engine_bin.resolve()
    manifest = args.timezone_manifest.resolve()
    finder = TimezoneFinder(in_memory=True)
    data = json.loads(manifest.read_text())
    assert data["data_version"]==finder.data_version
    points = cases(finder,args.points)
    expected = [finder.timezone_at(lat=round(a,4),lng=round(b,4)) for a,b in points]
    config = dict(timezone_manifest=str(manifest))
    cold, trials, mismatches = [], [], []
    for _ in range(args.trials):
        engine = Engine(binary)
        started = time.perf_counter()
        try:
            first = engine.call("location",dict(operation="timezone",lat=points[0][0],lon=points[0][1],
                                               config=config,allow_download=False))
            cold.append((time.perf_counter()-started)*1000)
            assert first["result"]["timezone_name"]==expected[0]
            started = time.perf_counter()
            actual = []
            for lat,lon in points:
                value = engine.call("location",dict(operation="timezone",lat=lat,lon=lon,
                                                   config=config,allow_download=False))
                actual.append(value["result"]["timezone_name"])
            native_ms = (time.perf_counter()-started)*1000
            started = time.perf_counter()
            batched = []
            for offset in range(0,len(points),4096):
                value = engine.call("location",dict(operation="timezones",points=points[offset:offset+4096],
                                                   config=config,allow_download=False))
                batched.extend(value["result"]["timezone_names"])
            batch_ms = (time.perf_counter()-started)*1000
            assert batched==actual
            started = time.perf_counter()
            py_result = [finder.timezone_at(lat=round(a,4),lng=round(b,4)) for a,b in points]
            python_ms = (time.perf_counter()-started)*1000
            assert py_result==expected
            for i,(a,b) in enumerate(zip(actual,expected)):
                if a!=b:
                    mismatches.append(dict(index=i,point=points[i],native=a,reference=b))
            trials.append(dict(native_scalar_rpc_ms=native_ms,native_batch_rpc_ms=batch_ms,
                               native_batch_points_per_second=len(points)*1000/batch_ms,
                               python_in_process_ms=python_ms))
        finally:
            resources = engine.close()
        trials[-1].update(resources)
    print(json.dumps(dict(contract="native-location-benchmark-v1",
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        timezone_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        data_version=finder.data_version,points=len(points),polygons=len(finder.zone_ids),
        warm_trials=trials,first_query_load_ms=cold,mismatches=mismatches[:30],
        mismatch_count=len(mismatches),median_first_load_ms=statistics.median(cold),
        caveats=[
            "Cold samples include native dataset verification/loading after hello, not process startup or cold OS buffers.",
            "Native scalar timings include every request/response serialization and manifest lookup; Python stays in-process.",
            "Batched native queries use up to 4096 points per RPC and one verified resident dataset acquisition per batch.",
            "Session CPU/RSS cover initial load, all scalar calls and all batch calls, not independent per-operation resources.",
            "Both use the complete pinned timezonefinder dataset, original overlap ordering and four-decimal coordinate rounding.",
            "Known cities, overlap regions, all polygon/hole samples, grid edges, dateline, poles and seeded global random points are included.",
            "This is a location-data benchmark, not full forecast, remote-provider, P95, throughput or production-client acceptance.",
        ]),indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__=="__main__":
    main()
