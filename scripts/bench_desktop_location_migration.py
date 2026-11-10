"""Equal-wire location IPC benchmark against the owning Python desktop service.

Uses a local simulated provider. Never calls credential/forecast endpoints.
The reference child is benchmark-only; no native runtime calls this adapter.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse


def reference_child(path):
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    from firesky import location, desktop_rpc
    config = json.loads(Path(path).read_text())["location"]
    for key, name in [
        ("geocoding_url","GEOCODING_URL"),("nominatim_url","NOMINATIM_URL"),
        ("metadata_url","POINT_METADATA_URL"),("srtm_base_url","SRTM_BASE_URL"),
    ]:
        setattr(location,name,config[key])
    location.LOCATION_CACHE_PATH = Path(config["location_cache_path"])
    location.SRTM_CACHE_DIR = Path(config["srtm_cache_dir"])

    class ReferenceService:
        def __init__(self):
            self.service = desktop_rpc.DesktopService()
        def dispatch(self, method, params, progress):
            # Exclude credential access and region advertisement from this probe.
            if method=="hello":
                return {"protocol":1,"engine":"location-reference"}
            return self.service.dispatch(method,params,progress)
    desktop_rpc.serve(output_stream=protocol_output,service=ReferenceService())


def production_rust_child(path):
    protocol_output = sys.stdout
    sys.stdout = sys.stderr
    from firesky import desktop_rpc, location

    config = json.loads(Path(path).read_text())["location"]
    for key, name in [
        ("geocoding_url", "GEOCODING_URL"),
        ("nominatim_url", "NOMINATIM_URL"),
        ("metadata_url", "POINT_METADATA_URL"),
        ("srtm_base_url", "SRTM_BASE_URL"),
    ]:
        setattr(location, name, config[key])
    location.LOCATION_CACHE_PATH = Path(config["location_cache_path"])
    location.SRTM_CACHE_DIR = Path(config["srtm_cache_dir"])
    desktop_rpc.serve(
        output_stream=protocol_output,
        service=desktop_rpc.DesktopService(),
    )


class Child:
    def __init__(self, command, env):
        self.process = subprocess.Popen(["/usr/bin/time","-l",*map(str,command)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,bufsize=1,env=env)
        self.sequence = 0
    def send(self, method, params):
        self.sequence += 1
        id = str(self.sequence)
        self.process.stdin.write(json.dumps(dict(id=id,method=method,params=params))+"\n")
        self.process.stdin.flush()
        return id
    def read(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("location child exited without a response")
        value = json.loads(line)
        if value["type"]!="result":
            raise RuntimeError(value)
        return value
    def call(self, method, params):
        id = self.send(method,params)
        value = self.read()
        assert value["id"]==id
        return value["result"]
    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=30)
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
        return dict(peak_rss_bytes=int(rss[1]) if rss else None,
                    user_cpu_seconds=float(cpu[2]) if cpu else None,
                    system_cpu_seconds=float(cpu[3]) if cpu else None)


def elapsed(call):
    started = time.perf_counter()
    result = call()
    return (time.perf_counter()-started)*1000,result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin",type=Path,required=True)
    parser.add_argument("--trials",type=int,default=5)
    parser.add_argument("--repeats",type=int,default=100)
    args = parser.parse_args()
    if sys.platform!="darwin" or not 1<=args.trials<=10 or not 10<=args.repeats<=1000:
        parser.error("requires macOS, trials 1..10 and repeats 10..1000")
    binary = args.engine_bin.resolve()
    source = Path(__file__).resolve().parents[1]/"src"
    state = {"delay":0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            path = urlparse(self.path).path
            if path=="/metadata":
                time.sleep(state["delay"])
                payload = dict(timezone="Europe/London",elevation=80)
            elif path=="/nom":
                payload = [dict(name="Town",lat="51.4",lon="-.2",display_name="Town, Country")]
            else:
                raise AssertionError("unexpected provider request")
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    provider = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=provider.serve_forever,daemon=True)
    thread.start()
    traces = {"native":[],"python":[],"production-rust":[]}
    try:
        with tempfile.TemporaryDirectory(prefix="firesky-desktop-location-") as temporary:
            root = Path(temporary)
            base = f"http://127.0.0.1:{provider.server_port}"
            config = root/"config.json"
            config.write_text(json.dumps(dict(location=dict(
                geocoding_url=base+"/geo",nominatim_url=base+"/nom",
                metadata_url=base+"/metadata",srtm_base_url=base+"/srtm",
                location_cache_path=str(root/"locations.json"),srtm_cache_dir=str(root/"srtm"),
                retry_seconds=0,request_attempts=1))))
            for _ in range(args.trials):
                order = ("native", "python", "production-rust")
                if _ % 2:
                    order = tuple(reversed(order))
                for backend in order:
                    state["delay"] = 0
                    if backend=="native":
                        command = [binary,"desktop-location","--config",config]
                        env = dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python",
                                   FIRESKY_ECCODES_LIBRARY="/no/ecCodes/library")
                    elif backend=="python":
                        command = [sys.executable,Path(__file__).resolve(),"--reference-child",config]
                        env = dict(os.environ,PYTHONPATH=str(source),NO_PROXY="*")
                    else:
                        command = [
                            sys.executable, Path(__file__).resolve(),
                            "--production-rust-child", config,
                        ]
                        env = dict(
                            os.environ,
                            PYTHONPATH=str(source),
                            NO_PROXY="*",
                            FIRESKY_DESKTOP_DATA_BACKEND="rust",
                            FIRESKY_RUST_ENGINE_BIN=str(binary),
                        )
                    started = time.perf_counter()
                    child = Child(command,env)
                    try:
                        hello = child.call("hello",{})
                        assert hello["protocol"]==1
                        trace = {"startup_hello_ms":(time.perf_counter()-started)*1000}
                        search = dict(query="City")
                        metadata = dict(lat=51.23445,lon=-.12345)
                        expected_search = dict(name="Town",lat=51.4,lon=-.2,
                            timezone_name="Europe/London",elevation_m=80)
                        expected_metadata = dict(metadata,timezone_name="Europe/London",elevation_m=80)
                        for method,params,expected in (
                            ("search",search,expected_search),("metadata",metadata,expected_metadata),
                        ):
                            duration,value = elapsed(lambda:child.call(method,params))
                            assert value==expected
                            trace[f"first_{method}_ms"] = duration
                            durations = []
                            for _ in range(args.repeats):
                                duration,value = elapsed(lambda:child.call(method,params))
                                assert value==expected
                                durations.append(duration)
                            trace[f"memo_{method}_ms"] = statistics.median(durations)
                        state["delay"] = .05
                        started = time.perf_counter()
                        requests = {}
                        for i in range(8):
                            params = dict(lat=40+i*.1,lon=-2)
                            requests[child.send("metadata",params)] = dict(
                                params,timezone_name="Europe/London",elevation_m=80)
                        for _ in range(8):
                            value = child.read()
                            assert value["result"]==requests.pop(value["id"])
                        trace["eight_uncached_metadata_50ms_provider_ms"] = (time.perf_counter()-started)*1000
                    finally:
                        trace_resources = child.close()
                        state["delay"] = 0
                    trace.update(trace_resources)
                    traces[backend].append(trace)
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join()
    print(json.dumps(dict(contract="desktop-location-wire-benchmark-v1",
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),trials=traces,
        medians={k:{name:statistics.median(t[name] for t in values)
                    for name in values[0]} for k,values in traces.items()},
        caveats=[
            "Both use the actual desktop search/metadata contracts, persistent children and JSON-lines transport.",
            "Python reference delegates to DesktopService, but hello omits credentials and region advertisement.",
            "The direct native child has Python/ecCodes unavailable. The Python reference and production-rust bridge are Python processes; the bridge is a production route, not a Python-free runtime.",
            "Providers are local simulated responses; no live-provider, account, forecast or production GUI acceptance.",
            "Eight cold-coordinate calls include a controlled 50ms provider delay; both servers use four workers.",
            "Memo hits are historical process-local results, not fresh network requests.",
            "No offline timezone resource or SRTM download is exercised; those have separate fixtures and benchmarks.",
            "Process CPU/RSS include startup and all operations, not per-request resource costs.",
        ]),indent=2))


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--reference-child":
        reference_child(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=="--production-rust-child":
        production_rust_child(sys.argv[2])
    else:
        main()
