"""Offline public/basic EC transport, SDK/raw-cache parity and native integration."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from firesky import ecmwf_ifs as ec, rust_ecmwf_data
from test_rust_migration import engine_binary, ec_grib_fixture, rpc
from test_rust_assessment import inputs

CYCLE = datetime(2026, 11, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def python_reference(monkeypatch):
    monkeypatch.setenv("FIRESKY_ECMWF_DATA_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


@pytest.fixture
def ec_server(tmp_path, ec_grib_fixture):
    import eccodes as codes
    template = ec_grib_fixture(tmp_path/"template.grib2", fault="optional", clipped=False)
    state = SimpleNamespace(
        calls=Counter(), ranges=[], assets={}, indexes={}, heads={}, status={},
        bad_range=False, force_200=False, truncated=False, bad_clock=False,
        mutation=None, extra_index_rows=0, index_delay=0., transient={},
        index_override=None, data_delay=0., index_length=None, truncated_ranges=0,
    )
    base_messages = []
    with template.open("rb") as stream:
        while (handle := codes.codes_grib_new_from_file(stream)) is not None:
            try:
                base_messages.append(codes.codes_get_message(handle))
                if codes.codes_get(handle,"shortName") == "2t":
                    codes.codes_set(handle,"shortName","2d")
                    base_messages.append(codes.codes_get_message(handle))
            finally:
                codes.codes_release(handle)

    def asset(cycle, hour):
        key = (cycle, hour)
        if key in state.assets:
            return state.assets[key], state.indexes[key]
        data, rows = bytearray(), []
        for encoded in base_messages:
            handle = codes.codes_new_from_message(encoded)
            try:
                name = codes.codes_get(handle,"shortName")
                level_type = codes.codes_get(handle,"typeOfLevel")
                level = codes.codes_get(handle,"level")
                if state.mutation == "missing" and name == "r" and level == 300:
                    continue
                codes.codes_set(handle,"dataDate",int(cycle.strftime("%Y%m%d")))
                codes.codes_set(handle,"dataTime",cycle.hour*100)
                step = 0 if level_type == "surface" and name == "z" else hour
                codes.codes_set(handle,"step",step + (3 if state.bad_clock else 0))
                if state.mutation == "type" and name == "r" and level == 300:
                    codes.codes_set(handle,"marsType","an")
                message = codes.codes_get_message(handle)
                rows.append(dict(param=name,type="fc",step=str(hour),
                                 levtype="pl" if level_type == "isobaricInhPa" else "sfc",
                                 levelist=str(level),_offset=len(data),_length=len(message)))
                data.extend(message)
            finally:
                codes.codes_release(handle)
        for _ in range(state.extra_index_rows):
            rows.append(dict(param="unselected",type="fc",step=str(hour),levtype="pl",
                             levelist="100",_offset=0,_length=1))
        state.assets[key] = bytes(data)
        state.indexes[key] = rows
        return bytes(data), rows

    state.asset = asset

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        def log_message(self, *args):
            pass
        def parse_path(self):
            match = re.search(r"/(\d{14})-(\d+)h-(oper|scda)-fc\.(index|grib2)$", self.path)
            assert match, self.path
            return (datetime.strptime(match[1],"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc),
                    int(match[2]),match[4])
        def reply(self, status, data=b"", **headers):
            self.send_response(status)
            self.send_header("Content-Length",str(len(data)))
            self.send_header("Content-Type","application/octet-stream")
            for key,value in headers.items():
                self.send_header(key.replace("_","-"),value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        def do_HEAD(self):
            cycle,hour,_ = self.parse_path()
            state.calls["head"] += 1
            state.calls[("head",cycle,hour)] += 1
            self.reply(state.heads.get((cycle,hour),200))
        def do_GET(self):
            cycle,hour,kind = self.parse_path()
            stage = "index" if kind == "index" else "range"
            state.calls[stage] += 1
            transient = state.transient.get(stage,[])
            status = transient.pop(0) if transient else state.status.get(stage,200)
            if status >= 400:
                self.reply(status,b"synthetic error")
                return
            data,rows = asset(cycle,hour)
            if kind == "index":
                time.sleep(state.index_delay)
                if state.index_length is not None:
                    self.send_response(200)
                    self.send_header("Content-Length",str(state.index_length))
                    self.send_header("Connection","close")
                    self.end_headers()
                    self.close_connection=True
                    return
                encoded = state.index_override
                if encoded is None:
                    encoded = b"".join(json.dumps(row).encode()+b"\n" for row in rows)
                self.reply(200,encoded)
                return
            time.sleep(state.data_delay)
            value = self.headers.get("Range","")
            state.ranges.append(value)
            match = re.fullmatch(r"bytes=(\d+)-(\d+)",value)
            if match is None:
                self.reply(200,data)
                return
            left,right = map(int,match.groups())
            payload = data[left:right+1]
            header = f"bytes {left+int(state.bad_range)}-{right}/{len(data)}"
            truncated=state.truncated or state.truncated_ranges>0
            state.truncated_ranges=max(0,state.truncated_ranges-1)
            if truncated:
                self.send_response(206)
                self.send_header("Content-Range",header)
                self.send_header("Content-Length",str(len(payload)))
                self.send_header("Content-Type","application/octet-stream")
                self.send_header("Connection","close")
                self.end_headers()
                self.wfile.write(payload[:-1])
                self.close_connection=True
            else:
                self.reply(200 if state.force_200 else 206,payload,Content_Range=header)

    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}",state
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def config(tmp_path, endpoint=None):
    return dict(cache_dir=str(tmp_path/"ec"),source="ecmwf",base_url=endpoint,
                timeout_seconds=2.,lock_timeout_seconds=2.,request_attempts=1,retry_seconds=0.)


def params(tmp_path, endpoint=None, *, operation="resolve", hours=(6,), **kwargs):
    return dict(operation=operation,config=config(tmp_path,endpoint),
                cycle_utc=CYCLE.isoformat(),forecast_hours=list(hours),
                allow_download=endpoint is not None,**kwargs)


def invoke(binary, request):
    return rpc(binary,[dict(id="ec",method="ecmwf_data",params=request)],
               env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python"))[0]


def result(binary, request):
    response = invoke(binary,request)
    assert response["type"] == "result",response
    assert response["result"]["contract"] == "rust-ecmwf-data-v1"
    return response["result"]


def no_temps(root):
    assert not [p for p in root.rglob("*") if p.name.startswith(".firesky-ec-")]


def install_bridge_endpoint(monkeypatch, endpoint):
    original = rust_ecmwf_data._config
    monkeypatch.setattr(rust_ecmwf_data,"_config",
        lambda *args,**kw:dict(original(*args,**kw),base_url=endpoint,
                             request_attempts=1,retry_seconds=0.))


def test_three_parts_shared_metadata_and_strict_cache_hit(engine_binary,tmp_path,ec_server):
    endpoint,state = ec_server
    request = params(tmp_path,endpoint,hours=(6,9))
    got = result(engine_binary,request)
    assert got["forecast_hours"] == [6,9]
    assert got["index_requests"] == state.calls["index"] == 3
    adapter = ec.EcmwfIfsAdapter(tmp_path/"ec")
    for row in got["files"]:
        path = Path(row["path"])
        hour = row["forecast_hour"]
        assert path == adapter.cache_path(adapter.cache_dir,CYCLE,hour)
        assert json.loads(path.with_name(path.name+".json").read_text()) == adapter._metadata(CYCLE,hour)
        assert adapter.probe(path,CYCLE,hour).supported
        assert adapter.download(CYCLE,hour)==path
        assert row["probe"]["numeric_values_validated"] is False
        assert row["identity"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    before = state.calls.copy()
    cached = result(engine_binary,params(tmp_path,hours=(6,9)))
    assert all(row["cache_hit"] for row in cached["files"])
    assert state.calls == before
    no_temps(tmp_path)


@pytest.mark.parametrize("hour",[0,6,144,150,360])
def test_real_python_sdk_bundle_bytes_match_rust(engine_binary,tmp_path,ec_server,monkeypatch,hour):
    from ecmwf.opendata import Client as ActualClient
    import ecmwf.opendata
    endpoint,state = ec_server
    monkeypatch.setattr(ecmwf.opendata,"Client",lambda **kw: ActualClient(
        source=endpoint,model=kw["model"],resol=kw["resol"],verify=True,
        source_accept_ranges=True,source_accept_multiple_ranges=False))
    python = ec.EcmwfIfsAdapter(tmp_path/"python")
    expected = python.download(CYCLE,hour)
    native = result(engine_binary,params(tmp_path,endpoint,hours=(hour,)))
    actual = Path(native["files"][0]["path"])
    assert actual.read_bytes() == expected.read_bytes()
    assert len(native["files"]) == 1
    assert ec.probe_ecmwf_ifs_basic_capability(actual,expected_cycle_utc=CYCLE,
                                             expected_forecast_hour=hour).supported
    calls=state.calls.copy()
    request=params(tmp_path,hours=(hour,))
    request["config"]["cache_dir"]=str(tmp_path/"python")
    assert result(engine_binary,request)["files"][0]["cache_hit"]
    assert state.calls==calls


@pytest.mark.parametrize("fault",["missing","wrong-clock","type","200","range","truncated",
                                  "json","index-status","range-status","overlap","overflow"])
def test_invalid_transfer_never_publishes(engine_binary,tmp_path,ec_server,fault):
    endpoint,state = ec_server
    if fault in ("missing","type"):
        state.mutation=fault
    elif fault=="wrong-clock":
        state.bad_clock=True
    elif fault=="200":
        state.force_200=True
    elif fault=="range":
        state.bad_range=True
    elif fault=="truncated":
        state.truncated=True
    elif fault=="json":
        state.index_override=b"not JSON\n"
    elif fault.endswith("-status"):
        state.status[fault.split("-")[0]]=403
    else:
        _,rows=state.asset(CYCLE,6)
        selected=next(row for row in rows if row["levtype"]=="pl")
        if fault=="overlap":
            rows.append(dict(selected))
        else:
            selected["_offset"]=2**64-1
    response=invoke(engine_binary,params(tmp_path,endpoint))
    assert response["type"]=="error",response
    assert not list((tmp_path/"ec").rglob("*.grib2"))
    no_temps(tmp_path)


@pytest.mark.parametrize("fault",["missing-sidecar","wrong-source","bad-json","wrong-hour","raw-clock"])
def test_invalid_existing_cache_fails_closed_and_is_preserved(engine_binary,tmp_path,ec_server,fault):
    endpoint,state=ec_server
    got=result(engine_binary,params(tmp_path,endpoint))
    path=Path(got["files"][0]["path"])
    metadata=path.with_name(path.name+".json")
    if fault=="missing-sidecar":
        metadata.unlink()
    elif fault=="bad-json":
        metadata.write_text("{} broken")
    elif fault=="raw-clock":
        state.assets.clear();state.indexes.clear();state.bad_clock=True
        path.write_bytes(state.asset(CYCLE,6)[0])
    else:
        data=json.loads(metadata.read_text())
        data["source" if fault=="wrong-source" else "forecast_hour"]="aws" if fault=="wrong-source" else 9
        metadata.write_text(json.dumps(data))
    before=path.read_bytes()
    calls=state.calls.copy()
    response=invoke(engine_binary,params(tmp_path,endpoint))
    assert response["type"]=="error"
    assert path.read_bytes()==before and state.calls==calls
    no_temps(tmp_path)


@pytest.mark.parametrize("status,available",[(200,True),(204,True),(404,False),(403,None),(500,None)])
def test_publication_only_404_means_missing(engine_binary,tmp_path,ec_server,status,available):
    endpoint,state=ec_server
    state.heads[(CYCLE,6)]=status
    response=invoke(engine_binary,params(tmp_path,endpoint,operation="probe"))
    if available is None:
        assert response["type"]=="error"
    else:
        assert response["result"]["files"][0]["available"] is available
    assert state.calls["index"]==state.calls["range"]==0


@pytest.mark.parametrize("hour",[0,1,2.5,3,143,144,144.5,147,150,359,360,361])
def test_source_brackets_match_existing_point_contract(engine_binary,tmp_path,hour):
    from firesky.ecmwf_point import ecmwf_source_bracket
    target=CYCLE+timedelta(hours=hour)
    request=params(tmp_path,operation="plan",hours=(),evaluation_utcs=[target.isoformat()])
    got=result(engine_binary,request)
    row=got["brackets"][0]
    assert (row["lower"],row["upper"],row["weight"]) == ecmwf_source_bracket(CYCLE,target)


def test_automatic_cycle_404_fallback_and_cache_bypass_match_python(engine_binary,tmp_path,ec_server,monkeypatch):
    import firesky.ecmwf_point as point
    endpoint,state=ec_server
    reference=CYCLE+timedelta(hours=20)
    evaluations=[reference-timedelta(hours=1),reference+timedelta(hours=1)]
    newest=CYCLE+timedelta(hours=12)
    state.heads[(newest,9)]=404
    monkeypatch.setattr(point,"ecmwf_artifact_available",
        lambda source,cycle,hour,**kw:state.heads.get((cycle,hour),200)!=404)
    wanted=point.choose_ecmwf_ifs_cycle_for_event(reference,evaluations,
        cache_dir=tmp_path/"ec",available_utc=reference)
    request=dict(operation="plan",config=config(tmp_path,endpoint),reference_utc=reference.isoformat(),
                 now_utc=reference.isoformat(),evaluation_utcs=[v.isoformat() for v in evaluations])
    got=result(engine_binary,request)
    assert datetime.fromisoformat(got["cycle_utc"].replace("Z","+00:00"))==wanted==CYCLE
    assert state.calls[("head",newest,9)]==1
    for hour in (6,9):
        path=ec.EcmwfIfsAdapter.cache_path(tmp_path/"ec",newest,hour)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"unvalidated-cache-admission")
    before=state.calls.copy()
    got=result(engine_binary,request)
    assert datetime.fromisoformat(got["cycle_utc"].replace("Z","+00:00"))==newest
    assert state.calls==before


@pytest.mark.parametrize("fault",["source","resolution","timeout","attempts","url","url-query",
                                  "hour","cycle","full","empty"])
def test_invalid_request_rejects_before_network(engine_binary,tmp_path,ec_server,fault):
    endpoint,state=ec_server
    request=params(tmp_path,endpoint)
    if fault=="source":request["config"]["source"]="not-source"
    elif fault=="resolution":request["config"]["resolution"]="0p1"
    elif fault=="timeout":request["config"]["timeout_seconds"]=0
    elif fault=="attempts":request["config"]["request_attempts"]=0
    elif fault=="url":request["config"]["base_url"]="http://example.invalid"
    elif fault=="url-query":request["config"]["base_url"]=endpoint+"?secret=synthetic"
    elif fault=="hour":request["forecast_hours"]=[147]
    elif fault=="cycle":request["cycle_utc"]="2026-11-09T00:01:00Z"
    elif fault=="full":request["contract"]="full-catalog/offline-bundle"
    elif fault=="empty":request["forecast_hours"]=[]
    assert invoke(engine_binary,request)["type"]=="error"
    assert not state.calls


@pytest.mark.parametrize("fault",["large-body","large-line","nonobject","too-many"])
def test_index_limits_reject_before_ranges(engine_binary,tmp_path,ec_server,fault):
    endpoint,state=ec_server
    if fault=="large-body":state.index_length=32*1024*1024+1
    elif fault=="large-line":state.index_override=json.dumps(dict(ignored="x"*(64*1024))).encode()
    elif fault=="nonobject":state.index_override=b"[]\n"
    else:state.index_override=b"{}\n"*100001
    assert invoke(engine_binary,params(tmp_path,endpoint))["type"]=="error"
    assert state.calls["range"]==0
    no_temps(tmp_path)


@pytest.mark.parametrize("fault",["index-503","range-503","range-truncated"])
def test_retry_resets_partial_output_and_preserves_final_bytes(engine_binary,tmp_path,ec_server,fault):
    endpoint,state=ec_server
    if fault=="range-truncated":state.truncated_ranges=1
    else:state.transient[fault.split("-")[0]]=[503]
    request=params(tmp_path,endpoint)
    request["config"]["request_attempts"]=2
    got=result(engine_binary,request)
    path=Path(got["files"][0]["path"])
    assert ec.EcmwfIfsAdapter(tmp_path/"ec").probe(path,CYCLE,6).supported
    if fault=="index-503":
        assert state.calls["index"]==3
    else:
        assert state.calls["range"]==5
    # A clean independent retry-free transfer must be byte-identical.
    other=result(engine_binary,params(tmp_path/"other",endpoint))
    assert path.read_bytes()==Path(other["files"][0]["path"]).read_bytes()
    no_temps(tmp_path)


def test_two_processes_share_python_compatible_file_lock(engine_binary,tmp_path,ec_server):
    endpoint,state=ec_server
    state.data_delay=.02
    request=params(tmp_path,endpoint)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=list(pool.map(lambda _:result(engine_binary,request),range(2)))
    assert sorted([first["files"][0]["cache_hit"],second["files"][0]["cache_hit"]])==[False,True]
    assert state.calls["index"]==2 and state.calls["range"]==4
    no_temps(tmp_path)


def test_python_lock_timeout_has_no_network_or_file_publication(engine_binary,tmp_path,ec_server):
    fcntl=pytest.importorskip("fcntl")
    endpoint,state=ec_server
    target=ec.EcmwfIfsAdapter.cache_path(tmp_path/"ec",CYCLE,6)
    target.parent.mkdir(parents=True,exist_ok=True)
    request=params(tmp_path,endpoint)
    request["config"]["lock_timeout_seconds"]=0
    with target.with_suffix(target.suffix+".lock").open("a+b") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        response=invoke(engine_binary,request)
    assert response["type"]=="error"
    assert "EC cache lock timed out"==response["error"]["message"]
    assert not state.calls and not target.exists()


@pytest.mark.parametrize("destination",["raw","metadata","lock"])
def test_symlink_destinations_preserve_unowned_files(engine_binary,tmp_path,ec_server,destination):
    endpoint,state=ec_server
    target=ec.EcmwfIfsAdapter.cache_path(tmp_path/"ec",CYCLE,6)
    target.parent.mkdir(parents=True,exist_ok=True)
    unowned=tmp_path/"unowned"
    unowned.write_bytes(b"preserve")
    path={"raw":target,"metadata":target.with_name(target.name+".json"),
          "lock":target.with_suffix(target.suffix+".lock")}[destination]
    path.symlink_to(unowned)
    assert invoke(engine_binary,params(tmp_path,endpoint))["type"]=="error"
    assert unowned.read_bytes()==b"preserve" and not state.calls


@pytest.mark.parametrize("cycle",[
    datetime(2026,5,11,6,tzinfo=timezone.utc),datetime(2026,5,13,6,tzinfo=timezone.utc),
    datetime(2026,5,11,18,tzinfo=timezone.utc),datetime(2026,11,9,12,tzinfo=timezone.utc)])
def test_index_urls_match_installed_sdk_historical_stream_boundary(engine_binary,tmp_path,ec_server,cycle):
    from ecmwf.opendata import Client
    endpoint,state=ec_server
    request=params(tmp_path,endpoint,operation="plan")
    request["cycle_utc"]=cycle.isoformat()
    got=result(engine_binary,request)
    client=Client(source=endpoint,model="ifs",resol="0p25")
    url=client._get_urls(ec.EcmwfIfsAdapter(tmp_path/"ec").build_pressure_request(cycle,6)).urls[0]
    assert got["files"][0]["data_url"]==url
    assert got["files"][0]["index_url"]==url.rsplit(".",1)[0]+".index"
    assert not state.calls


def test_cycle_bridge_accepts_aware_local_time_and_rejects_naive(monkeypatch,tmp_path):
    from zoneinfo import ZoneInfo
    seen=[]
    monkeypatch.setattr(rust_ecmwf_data,"_call",lambda request:seen.append(request) or
                        dict(cycle_utc=CYCLE.isoformat()))
    local=CYCLE.astimezone(ZoneInfo("Asia/Shanghai"))
    assert rust_ecmwf_data.choose_cycle(local,[local],cache_dir=tmp_path,available=local,
        source="ecmwf",timeout=2,max_cycles=8)==CYCLE
    assert seen[0]["reference_utc"]==CYCLE.isoformat()
    with pytest.raises(ValueError,match="timezone-aware"):
        rust_ecmwf_data.choose_cycle(local.replace(tzinfo=None),[local],cache_dir=tmp_path,
            available=local,source="ecmwf",timeout=2,max_cycles=8)


@pytest.mark.parametrize("reference_hour,now_hour",[(120,20),(6,100),(20,20)])
def test_automatic_cycle_caps_future_and_respects_past_event(engine_binary,tmp_path,ec_server,
                                                           monkeypatch,reference_hour,now_hour):
    from firesky import ecmwf_point
    endpoint,state=ec_server
    reference=CYCLE+timedelta(hours=reference_hour)
    now=CYCLE+timedelta(hours=now_hour)
    evaluations=[reference-timedelta(hours=1),reference+timedelta(hours=1)]
    monkeypatch.setattr(ecmwf_point,"ecmwf_artifact_available",lambda *a,**kw:True)
    expected=ecmwf_point.choose_ecmwf_ifs_cycle_for_event(reference,evaluations,
        available_utc=now,cache_dir=tmp_path/"ec")
    got=result(engine_binary,dict(operation="plan",config=config(tmp_path,endpoint),
        reference_utc=reference.isoformat(),now_utc=now.isoformat(),
        evaluation_utcs=[value.isoformat() for value in evaluations]))
    assert datetime.fromisoformat(got["cycle_utc"].replace("Z","+00:00"))==expected


@pytest.mark.parametrize("status",[403,404])
def test_cycle_outage_never_masquerades_as_older_available_data(engine_binary,tmp_path,ec_server,status):
    endpoint,state=ec_server
    reference=CYCLE+timedelta(hours=20)
    for cycle in (CYCLE+timedelta(hours=12),CYCLE):
        for hour in (6,9,18,21):state.heads[(cycle,hour)]=status
    request=dict(operation="plan",config=config(tmp_path,endpoint),max_cycles=2,
        reference_utc=reference.isoformat(),now_utc=reference.isoformat(),
        evaluation_utcs=[reference.isoformat()])
    response=invoke(engine_binary,request)
    assert response["type"]=="error"
    assert state.calls["head"]==(1 if status==403 else 2)


def test_full_catalog_cache_is_never_used_as_a_basic_raw_hit(engine_binary,tmp_path):
    path=ec.full_catalog_cache_path(tmp_path/"ec",CYCLE,6)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b"not a public/basic bundle")
    response=invoke(engine_binary,params(tmp_path))
    assert response["type"]=="error"
    assert path.read_bytes()==b"not a public/basic bundle"


def test_adapter_bridge_download_and_probe_call_no_python_sdk(engine_binary,tmp_path,ec_server,monkeypatch):
    import ecmwf.opendata
    from firesky.ecmwf import ecmwf_artifact_available,EcmwfArtifactProbeError
    endpoint,state=ec_server
    install_bridge_endpoint(monkeypatch,endpoint)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN",str(engine_binary))
    monkeypatch.setenv("FIRESKY_ECMWF_DATA_BACKEND","rust")
    monkeypatch.setattr(ecmwf.opendata,"Client",lambda **kw:pytest.fail("Python EC SDK invoked"))
    adapter=ec.EcmwfIfsAdapter(tmp_path/"ec")
    path=adapter.download(CYCLE,6)
    assert adapter.probe(path,CYCLE,6).supported
    assert ecmwf_artifact_available("ecmwf",CYCLE,6)
    state.heads[(CYCLE,6)]=403
    with pytest.raises(EcmwfArtifactProbeError,match="403"):
        ecmwf_artifact_available("ecmwf",CYCLE,6)


def test_cycle_bridge_does_not_invoke_python_probe(engine_binary,tmp_path,ec_server,monkeypatch):
    import firesky.ecmwf_point as point
    endpoint,state=ec_server
    install_bridge_endpoint(monkeypatch,endpoint)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN",str(engine_binary))
    monkeypatch.setenv("FIRESKY_ECMWF_DATA_BACKEND","rust")
    monkeypatch.setattr(point,"ecmwf_artifact_available",lambda *a,**kw:pytest.fail("Python cycle probe invoked"))
    reference=CYCLE+timedelta(hours=20)
    got=point.choose_ecmwf_ifs_cycle_for_event(reference,[reference],
        cache_dir=tmp_path/"ec",available_utc=reference)
    assert got==CYCLE+timedelta(hours=12) and state.calls["head"]==1


@pytest.mark.parametrize("phenomenon",["dawn","dusk"])
def test_downloaded_ec_inputs_feed_complete_native_point(engine_binary,tmp_path,ec_server,inputs,
                                                        monkeypatch,phenomenon):
    from test_rust_frames import named_points,reference,compare
    from firesky.models import ForecastRequest,GeoPoint
    from firesky.national_point_core import materialize_national_point_core
    endpoint,state=ec_server
    recipe_path,request=inputs(model="ecmwf-basic",phenomenon=phenomenon,points=named_points(True))
    original_frames=request["frames"]
    got=result(engine_binary,params(tmp_path,endpoint,hours=[v["hour"] for v in request["frames"]]))
    request["frames"]=[dict(hour=row["forecast_hour"],path=row["path"]) for row in got["files"]]
    request["include_point_core"]=True
    recipe_path.write_text(json.dumps(request))
    _,_,expected=reference(request,monkeypatch)
    output=tmp_path/"point"
    response=rpc(engine_binary,[dict(id="point",method="forecast_stored_point_to_files",
        params=dict(path=str(recipe_path),output_dir=str(output)))],
        env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"]=="result",response
    from firesky.tensor_bundle import load_tensor_bundle
    bundle=load_tensor_bundle(output/"manifest.json",kind="point-forecast-v1",source_path=recipe_path)
    assert len(bundle.arrays)==16
    frozen_path=tmp_path/"original-inputs.json"
    frozen_path.write_text(json.dumps(dict(request,frames=original_frames)))
    frozen_output=tmp_path/"original-point"
    response=rpc(engine_binary,[dict(id="original",method="forecast_stored_point_to_files",
        params=dict(path=str(frozen_path),output_dir=str(frozen_output)))],
        env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"]=="result",response
    original_bundle=load_tensor_bundle(frozen_output/"manifest.json",kind="point-forecast-v1",
                                      source_path=frozen_path)
    assert set(bundle.arrays)==set(original_bundle.arrays)
    for key in bundle.arrays:
        np.testing.assert_array_equal(bundle.arrays[key],original_bundle.arrays[key],err_msg=key)
    point=request["points"][0]
    req=ForecastRequest(target_date=datetime.fromisoformat(request["date"]).date(),phenomenon=phenomenon,
        point=GeoPoint(point["lat"],point["lon"],point["elevation_m"]),timezone_name=point["timezone_name"],
        name=point["name"],model_source="ec",radius_km=request["radius_km"],gfs_cycle_override=CYCLE)
    actual=json.loads((output/"point_forecast.json").read_text())
    frames=json.loads((output/bundle.manifest["metadata"]["frame_payload"]["file"]).read_text())
    compare(actual,materialize_national_point_core(req,frames["point_core"]))
    wanted=materialize_national_point_core(req,expected["point_core"])
    # Retain the established upstream EC float32 limits from Stage 16;
    # same-core materialization and transport invariance remain strict above.
    for key in ("ground_area_burnable_amount_mean","ground_area_burnable_load_mean"):
        np.testing.assert_allclose(actual["quality_components"][key],wanted["quality_components"][key],
                                   rtol=3e-6,atol=3e-8)
        wanted["quality_components"][key]=actual["quality_components"][key]
    for row,expected_row in zip(actual["steps"],wanted["steps"]):
        for key in ("ground_area_burnable_cloud_field_amount","ground_area_burnable_cloud_load"):
            np.testing.assert_allclose(row[key],expected_row[key],rtol=3e-6,atol=3e-8)
            expected_row[key]=row[key]
    compare(actual,wanted)
    assert all(row["probe"]["supported"] for row in got["files"])
