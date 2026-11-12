"""Offline ADS v1 contract, raw-cache, bridge and forecast integration tests."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.parse import urlparse

import numpy as np
import pytest

from firesky import aerosol, rust_cams_data
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_cams import write_cams
from test_rust_input_plan import CYCLE, recipe, planned, call, seed_gfs, seed_cams
from test_rust_migration import engine_binary, rpc

DATASET = "cams-global-atmospheric-composition-forecasts"
PROCESS = "/api/retrieve/v1/processes/" + DATASET
TOKEN = "synthetic-test-private-token"
AREA = [1., 100., 0., 102.]


def optical_bytes(path, run, hour, mutation=None, range_aod=False):
    import eccodes as codes
    write_cams(path, hour=hour, mutation=mutation)
    messages = []
    with path.open("rb") as stream:
        while (handle := codes.codes_grib_new_from_file(stream)) is not None:
            try:
                codes.codes_set(handle, "dataDate", int(run.strftime("%Y%m%d")))
                codes.codes_set(handle, "dataTime", int(run.strftime("%H%M")))
                if range_aod and codes.codes_get(handle,"shortName") == "aod550":
                    codes.codes_set(handle,"stepType","avg")
                    codes.codes_set(handle,"stepRange",f"0-{hour}")
                messages.append(codes.codes_get_message(handle))
            finally:
                codes.codes_release(handle)
    return b"".join(messages)


@pytest.fixture(autouse=True)
def reference_and_credentials(monkeypatch, tmp_path):
    for name in ("CAMS", "CAMS_DATA", "ASTRONOMY", "GFS_DATA", "GFS_DECODE", "CLOUD", "EMPIRICAL"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CDSAPI_RC", str(tmp_path/"missing-credentials"))
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    monkeypatch.delenv("CDSAPI_URL", raising=False)


@pytest.fixture
def ads_server(tmp_path, monkeypatch):
    state = SimpleNamespace(
        constraints={"date": ["2026-11-08/2026-11-09"], "time": ["00:00", "12:00"],
                     "leadtime_hour": [str(h) for h in range(0, 73, 3)]},
        day_constraints={}, jobs={}, assets={}, calls=Counter(), requests=[], headers=[],
        bad_leads={}, omit_leads=set(), statuses={}, job_statuses=["accepted", "running", "successful"],
        api_redirect=None, monitor_override=None, result_override=None, asset_override=None,
        invalid_bundle=False, asset_size_delta=0, transient={}, response_delay=0.,
        constraint_delay=0., wrong_clock=False,
        prebuilt={}, range_aod=False, monitor_delay=0.,
    )
    lock = threading.Lock()

    class Assets(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            with lock:
                state.calls["asset"] += 1
                state.headers.append(dict(self.headers))
            data = state.assets.get(urlparse(self.path).path, b"not-a-grib")
            transient = state.transient.get("asset",[])
            status = transient.pop(0) if transient else state.statuses.get("asset",200)
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    assets = ThreadingHTTPServer(("127.0.0.1", 0), Assets)
    asset_thread = threading.Thread(target=assets.serve_forever, daemon=True)
    asset_thread.start()

    class API(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, stage, value):
            state.calls[stage] += 1
            transient = state.transient.get(stage, [])
            status = transient.pop(0) if transient else state.statuses.get(stage, 200)
            if status >= 400:
                value = {"detail": TOKEN, "signed_url": "https://example.invalid/?token="+TOKEN}
            payload = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except BrokenPipeError:
                # The deadline tests intentionally close timed-out requests.
                pass
        def do_GET(self):
            path = urlparse(self.path).path
            state.requests.append(("GET", path, None, dict(self.headers)))
            if self.headers.get("PRIVATE-TOKEN") != TOKEN:
                self.reply("unauthorized", {"detail": TOKEN})
                return
            if path == "/api/catalogue/v1/messages":
                self.reply("catalogue", {"messages":[]})
            elif path == PROCESS:
                if state.api_redirect:
                    self.send_response(302)
                    self.send_header("Location", state.api_redirect)
                    self.end_headers()
                    return
                self.reply("process", {"id": DATASET})
            elif path.endswith("/results"):
                number = path.split("/")[-2]
                data = state.jobs[number]
                href = state.asset_override or f"http://127.0.0.1:{assets.server_port}/asset/{number}"
                self.reply("results", {"asset": {"value": {
                    "href": href, "file:size": len(data["bytes"])+state.asset_size_delta,
                    "type": "application/x-grib"}}})
            else:
                import time
                time.sleep(state.monitor_delay)
                number = path.rsplit("/", 1)[-1]
                job = state.jobs[number]
                index = min(job["polls"], len(state.job_statuses)-1)
                status = state.job_statuses[index]
                job["polls"] += 1
                self.reply("monitor", {"status": status, "links": [{
                    "rel": "results", "href": state.result_override or
                    f"http://127.0.0.1:{self.server.server_port}"+path+"/results"}]})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(("POST", self.path, body, dict(self.headers)))
            if self.path.endswith("/constraints"):
                import time
                time.sleep(state.constraint_delay)
                query = body["inputs"]
                value = state.constraints if "date" not in query else state.day_constraints.get(
                    query["date"][0], {k: v for k, v in state.constraints.items() if k != "date"})
                self.reply("constraints", value)
            elif self.path.endswith("/execution"):
                import time
                time.sleep(state.response_delay)
                query = body["inputs"]
                number = str(len(state.jobs)+1)
                run = datetime.fromisoformat(query["date"][0]+"T"+query["time"][0]+"+00:00")
                if state.wrong_clock:
                    run += timedelta(hours=3)
                payload = b"".join(
                    (state.prebuilt[(run.isoformat(),int(hour))]
                     if (run.isoformat(),int(hour)) in state.prebuilt else
                     optical_bytes(tmp_path/f"asset-{number}-{hour}.grib", run, int(hour),
                                   state.bad_leads.get(int(hour)),state.range_aod))
                    for hour in query["leadtime_hour"] if int(hour) not in state.omit_leads)
                if state.invalid_bundle:
                    payload = b"<html>not GRIB</html>"*10
                state.jobs[number] = dict(query=query, bytes=payload, polls=0)
                state.assets[f"/asset/{number}"] = payload
                self.reply("execution", {"links": [{"rel": "monitor",
                    "href": state.monitor_override or
                    f"http://127.0.0.1:{self.server.server_port}/api/retrieve/v1/jobs/{number}"}]})
            else:
                self.reply("other", {"detail": TOKEN})

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/api"
    monkeypatch.setenv("CDSAPI_URL", endpoint)
    monkeypatch.setenv("CDSAPI_KEY", TOKEN)
    yield endpoint, state
    server.shutdown()
    assets.shutdown()
    thread.join(timeout=5)
    asset_thread.join(timeout=5)
    server.server_close()
    assets.server_close()


def config(endpoint):
    return dict(api_url=endpoint, timeout_seconds=3., wait_timeout_seconds=10.,
                poll_initial_seconds=0., poll_max_seconds=0., request_attempts=1,
                lock_timeout_seconds=2.)


def params(tmp_path, endpoint=None, *, operation="resolve", hours=(9, 12), **kwargs):
    result = dict(operation=operation, cache_dir=str(tmp_path/"cams"), area=AREA,
                  target_utcs=[(CYCLE+timedelta(hours=h)).isoformat() for h in hours],
                  allow_download=endpoint is not None, require_exact_valid_time=True)
    if endpoint:
        result["config"] = config(endpoint)
    result.update(kwargs)
    return result


def invoke(binary, request):
    return rpc(binary, [dict(id="cams", method="cams_data", params=request)],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]


def result(binary, request):
    response = invoke(binary, request)
    assert response["type"] == "result", response
    assert response["result"]["contract"] == "rust-cams-data-v1"
    return response["result"]


def no_temps(root):
    assert not [p for p in root.rglob("*") if ".firesky-cams-" in p.name]


def test_batch_run_selection_assets_auth_hashes_and_python_interop(engine_binary, tmp_path, ads_server):
    endpoint, state = ads_server
    request = params(tmp_path, endpoint, hours=(6, 9, 12))
    got = result(engine_binary, request)
    assert got["missing_valid_times"] == []
    assert got["groups"] == 2 and got["execution_requests"] == 2
    assert got["constraints_requests"] == 3  # base + unique days, not per target
    assert sorted(job["query"]["leadtime_hour"] for job in state.jobs.values()) == [["0"], ["6", "9"]]
    for row in got["files"]:
        path = Path(row["path"])
        data = path.read_bytes()
        assert row["identity"] == dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        target = datetime.fromisoformat(row["requested_valid_time"])
        selection = aerosol.find_cached_cams_bracket(Path(request["cache_dir"]), AREA, target)
        assert selection.left.path == selection.right.path == path
        assert aerosol.load_cams_optics_grid(path).valid_time == target
    assert state.headers and all("private-token" not in {k.lower():v for k,v in h.items()} for h in state.headers)
    assert all({k.lower():v for k,v in h.items()}["private-token"] == TOKEN for _,_,_,h in state.requests)
    assert TOKEN not in json.dumps(got)
    no_temps(tmp_path)


@pytest.mark.parametrize("target_hour", [0, 1, 5, 8, 12, 13, 23, 36])
def test_selection_matches_actual_python_helper(engine_binary, tmp_path, ads_server, target_hour):
    endpoint, state = ads_server
    target = CYCLE+timedelta(hours=target_hour, minutes=31)
    queries = []
    class Process:
        def apply_constraints(self, query):
            queries.append(query)
            return {k: v for k,v in state.constraints.items() if k != "date"}
    base = dict(variable=["total_aerosol_optical_depth_550nm", "aerosol_extinction_coefficient_532nm"])
    expected = aerosol.choose_best_cams_selection(Process(), base, state.constraints, target)
    got = result(engine_binary, params(tmp_path, endpoint, operation="download", hours=(),
        target_utc=target.isoformat(), require_exact_valid_time=False))
    path = Path(got["files"][0]["path"])
    day, clock, lead, _ = aerosol.parse_cams_cache_name(path)
    assert (day, clock, lead) == expected
    assert all(q["variable"] == ["aerosol_extinction_coefficient_532nm"] for q in queries)
    for _,url,body,_ in state.requests:
        if body and url.endswith("constraints") and "date" in body["inputs"]:
            assert body["inputs"]["variable"] == ["aerosol_extinction_coefficient_532nm"]


@pytest.mark.parametrize("area", [
    [1, 100, 0, 102], [1., 100., 0., 102.], [1., 100, 0, 102.],
    [1., -1e-5, -0., 2.], [1e-7, -1., -1., 1.], [1e-4, -1., -1., 1.],
])
def test_numeric_area_cache_hash_matches_python(engine_binary, tmp_path, ads_server, area):
    endpoint, _ = ads_server
    got = result(engine_binary, params(tmp_path, endpoint, operation="download", hours=(9,), area=area))
    assert Path(got["files"][0]["path"]).name.endswith("-"+aerosol.cams_area_key(area)+".grib")


@pytest.mark.parametrize("batch", [False, True])
def test_cache_only_hit_does_not_require_credentials_or_network(engine_binary, tmp_path, batch):
    path = seed_cams(tmp_path/"cams", [CYCLE+timedelta(hours=9)], AREA)[0]
    request = params(tmp_path, hours=(9,), operation="batch" if batch else "download")
    request["allow_download"] = True
    got = result(engine_binary, request)
    assert got["cache_hits"] == 1 and got["execution_requests"] == 0
    assert got["files"][0]["path"] == str(path)


@pytest.mark.parametrize("refresh_hour,target_hour,network", [(6,9,True),(0,9,False),(12,9,False)])
def test_refresh_preserves_old_files_and_historical_cache(engine_binary, tmp_path, ads_server,
                                                         refresh_hour, target_hour, network):
    endpoint, state = ads_server
    path = seed_cams(tmp_path/"cams", [CYCLE+timedelta(hours=target_hour)], AREA)[0]
    before = path.read_bytes()
    got = result(engine_binary, params(tmp_path, endpoint, hours=(target_hour,),
        refresh_before_cycle=(CYCLE+timedelta(hours=refresh_hour)).isoformat()))
    assert bool(state.calls["process"]) is network
    assert got["cache_hits"] == (0 if network else 1)
    assert path.read_bytes() == before


def test_single_bad_profile_uses_explicit_neighbour_fallback(engine_binary, tmp_path, ads_server):
    endpoint, state = ads_server
    state.bad_leads[9] = "zero"
    got = result(engine_binary, params(tmp_path, endpoint, operation="download", hours=(9,)))
    assert [job["query"]["leadtime_hour"] for job in state.jobs.values()] == [["9"], ["12"]]
    assert got["files"][0]["lead_hours"] == 12 and got["files"][0]["exact"] is False
    assert not list((tmp_path/"cams").rglob("*-009-*.grib"))
    no_temps(tmp_path)


def test_batch_bad_profile_returns_only_valid_steps(engine_binary, tmp_path, ads_server):
    endpoint, state = ads_server
    state.bad_leads[9] = "zero"
    got = result(engine_binary, params(tmp_path, endpoint, operation="batch", hours=(6,9)))
    assert [row["lead_hours"] for row in got["files"]] == [6]
    assert got["missing_valid_times"] == [(CYCLE+timedelta(hours=9)).isoformat()]
    assert state.calls["execution"] == 1
    no_temps(tmp_path)


@pytest.mark.parametrize("status", ["failed", "rejected", "dismissed", "deleted", "mystery"])
def test_terminal_job_errors_never_publish_or_expose_provider_body(engine_binary, tmp_path, ads_server, status):
    endpoint, state = ads_server
    state.job_statuses = [status]
    response = invoke(engine_binary, params(tmp_path, endpoint, operation="download", hours=(9,)))
    assert response["type"] == "error"
    assert TOKEN not in json.dumps(response)
    assert not list((tmp_path/"cams").rglob("*.grib"))
    no_temps(tmp_path)


@pytest.mark.parametrize("fault", ["execution-500","process-401","api-redirect","monitor-origin",
                                  "results-origin","asset-credentials","asset-size","bad-grib"])
def test_bad_protocol_transfer_and_links_fail_closed(engine_binary, tmp_path, ads_server, fault):
    endpoint, state = ads_server
    if fault == "execution-500": state.statuses["execution"] = 500
    elif fault == "process-401": state.statuses["process"] = 401
    elif fault == "api-redirect": state.api_redirect = endpoint+"/stolen"
    elif fault == "monitor-origin": state.monitor_override = "http://127.0.0.1:9/stolen"
    elif fault == "results-origin": state.result_override = "http://127.0.0.1:9/stolen"
    elif fault == "asset-credentials": state.asset_override = "https://secret@example.org/private"
    elif fault == "asset-size": state.asset_size_delta = 1
    elif fault == "bad-grib": state.invalid_bundle = True
    request = params(tmp_path, endpoint, operation="download", hours=(9,))
    request["config"]["request_attempts"] = 3
    response = invoke(engine_binary, request)
    assert response["type"] == "error", response
    assert TOKEN not in json.dumps(response)
    if fault == "execution-500": assert state.calls["execution"] == 1
    assert not list((tmp_path/"cams").rglob("*.grib"))
    no_temps(tmp_path)


def test_retry_constraints_and_asset_without_resubmitting_job(engine_binary, tmp_path, ads_server):
    endpoint, state = ads_server
    state.transient["constraints"] = [503]
    state.transient["asset"] = [503]
    got = result(engine_binary, params(tmp_path, endpoint, operation="download", hours=(9,),
        config=dict(config(endpoint), request_attempts=2)))
    assert got["execution_requests"] == 1
    assert state.calls["constraints"] == 4  # retried base + two dates
    assert state.calls["asset"] == 2


def test_job_wait_deadline_cleans_owned_temp(engine_binary, tmp_path, ads_server):
    endpoint, state = ads_server
    state.job_statuses = ["running"]
    response = invoke(engine_binary, params(tmp_path, endpoint, operation="download", hours=(9,),
        config=dict(config(endpoint), wait_timeout_seconds=.15, poll_initial_seconds=.02, poll_max_seconds=.03)))
    assert response["type"] == "error" and "deadline" in response["error"]["message"]
    no_temps(tmp_path)


@pytest.mark.parametrize("deadline_limited", [True, False])
def test_inflight_monitor_timeout_distinguishes_operation_budget(
        engine_binary, tmp_path, ads_server, deadline_limited):
    endpoint, state = ads_server
    state.job_statuses = ["running"]
    state.monitor_delay = 2.
    state.prebuilt[(CYCLE.isoformat(), 9)] = optical_bytes(tmp_path/"prebuilt.grib", CYCLE, 9)
    limits = dict(config(endpoint), wait_timeout_seconds=1. if deadline_limited else 5.,
                  timeout_seconds=3. if deadline_limited else .4)
    response = invoke(engine_binary, params(
        tmp_path, endpoint, operation="download", hours=(9,), config=limits))
    assert response["type"] == "error", response
    message = response["error"]["message"]
    assert ("deadline" in message) == deadline_limited, message
    if not deadline_limited:
        assert message == "ADS HTTP request failed (URL/body redacted)"
    assert state.calls["execution"] == 1
    assert any(method == "GET" and "/jobs/" in path and not path.endswith("/results")
               for method, path, _, _ in state.requests)
    assert TOKEN not in json.dumps(response)
    assert not list((tmp_path/"cams").rglob("*.grib"))
    no_temps(tmp_path)


@pytest.mark.parametrize("area", [
    [1., 179., 0., -179.],
    [1., 179., 0., 181.],
    [1., -181., 0., -179.],
])
def test_single_rectangle_resolver_rejects_unsplit_areas_before_network(
        engine_binary, tmp_path, ads_server, area):
    endpoint, state = ads_server
    response = invoke(engine_binary, params(tmp_path, endpoint, area=area))
    assert response["type"] == "error", response
    assert "ordinary rectangle" in response["error"]["message"]
    assert not state.requests
    assert not (tmp_path / "cams").exists()


@pytest.mark.parametrize("fault", ["area","time","naive","exact","unknown","attempts","timeout","tls"])
def test_invalid_configuration_rejects_before_network(engine_binary, tmp_path, ads_server, fault):
    endpoint, state = ads_server
    request = params(tmp_path, endpoint, operation="download", hours=(9,))
    if fault == "area": request["area"] = [0,100,1,102]
    elif fault == "time": request["target_utcs"] = ["bad"]
    elif fault == "naive": request["target_utcs"] = ["2026-11-09T09:00:00"]
    elif fault == "exact": request["target_utcs"] = ["2026-11-09T09:01:00+00:00"]
    elif fault == "unknown": request["secret_key"] = TOKEN
    elif fault == "attempts": request["config"]["request_attempts"] = 0
    elif fault == "timeout": request["config"]["wait_timeout_seconds"] = -1
    elif fault == "tls": request["config"]["api_url"] = "http://example.org/api"
    response = invoke(engine_binary, request)
    assert response["type"] == "error"
    assert not state.requests
    assert TOKEN not in json.dumps(response)


@pytest.mark.parametrize("batch", [False,True])
def test_opt_in_provider_bridge_calls_no_python_sdk(engine_binary, tmp_path, ads_server, monkeypatch, batch):
    import cdsapi
    endpoint, state = ads_server
    monkeypatch.setenv("FIRESKY_CAMS_DATA_BACKEND","rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(cdsapi,"Client",lambda **kw: pytest.fail("Python ADS client called"))
    provider = aerosol.CamsAdsAerosolProvider(cache_dir=tmp_path/"cams")
    target = CYCLE+timedelta(hours=9)
    if batch:
        paths = provider.download_targets_batch(AREA,[target],require_exact_valid_time=True)
        path = paths[target]
    else:
        path = provider.download_target(AREA,target,require_exact_valid_time=True)
    assert path.is_file() and state.calls["execution"] == 1


@pytest.mark.parametrize("phenomenon", ["dawn","dusk"])
def test_online_cams_planner_produces_full_point_without_python(engine_binary, tmp_path, ads_server,
                                                              monkeypatch, phenomenon):
    endpoint, state = ads_server
    request = recipe(tmp_path, phenomenon=phenomenon)
    plan = planned(engine_binary,tmp_path,request)
    seed_gfs(Path(request["gfs"]["cache_dir"]),plan["forecast_hours"])
    request["cams_download"] = config(endpoint)
    output = tmp_path/"forecast"
    response = call(engine_binary,tmp_path,request,
        method="forecast_cached_gfs_point_to_files", output=output)
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output/"manifest.json",kind="point-forecast-v1",
                                source_path=tmp_path/"plan-request.json")
    plan = json.loads((output/"input_plan.json").read_text())
    assert plan["inputs_located"] and plan["cams_download_enabled"]
    assert plan["cams_download"]["asset_downloads"] > 0
    assert bundle.manifest["metadata"]["point_forecast"]["automatic_input_selection_scope"] == "GFS-event-hours-and-CAMS-ADS"
    assert len(bundle.arrays) == 16
    assert json.loads((output/"point_forecast.json").read_text())["model_source"] == "gfs"
    # Independent Python scorer/materializer, with the same selected raw files.
    from test_rust_frames import reference as frame_reference, resolved_points, compare
    from firesky.models import ForecastRequest, GeoPoint
    from firesky.national_point_core import materialize_national_point_core
    from datetime import date
    frozen = dict(model="gfs",date=request["date"],phenomenon=phenomenon,
        cycle_utc=request["cycle_utc"],points=request["points"],frames=plan["model_files"],
        cams=plan["cams_files"],radius_km=request["radius_km"],bbox=plan["prepared_bbox"],
        include_point_core=True)
    frozen_path = tmp_path/"frozen.json"
    frozen_path.write_text(json.dumps(frozen))
    frozen_output = tmp_path/"frozen-output"
    response = rpc(engine_binary,[dict(id="frozen",method="forecast_stored_point_to_files",
        params=dict(path=str(frozen_path),output_dir=str(frozen_output)))],
        env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    other = load_tensor_bundle(frozen_output/"manifest.json",kind="point-forecast-v1",
                               source_path=frozen_path)
    assert set(bundle.arrays) == set(other.arrays)
    for key in bundle.arrays:
        np.testing.assert_array_equal(bundle.arrays[key],other.arrays[key],err_msg=key)
    compare(json.loads((output/"point_forecast.json").read_text()),
            json.loads((frozen_output/"point_forecast.json").read_text()))
    prepared,_,expected = frame_reference(frozen,monkeypatch)
    p = resolved_points(prepared,frozen["points"])[0]
    req = ForecastRequest(target_date=date.fromisoformat(request["date"]),phenomenon=phenomenon,
        point=GeoPoint(p["lat"],p["lon"],p["elevation_m"]),timezone_name=p["timezone_name"],
        name=p["name"],model_source="gfs",radius_km=request["radius_km"],gfs_cycle_override=CYCLE)
    compare(json.loads((output/"point_forecast.json").read_text()),
            materialize_national_point_core(req,expected["point_core"]))


@pytest.mark.parametrize("hour", [0,1,2,3,4,5,6,7,8,9,10,11,12])
def test_nonexact_legacy_cache_choice_matches_python(engine_binary, tmp_path, hour):
    seed_cams(tmp_path/"cams",[CYCLE+timedelta(hours=h) for h in (0,3,6,9,12)],AREA)
    target = CYCLE+timedelta(hours=hour,minutes=25)
    expected = aerosol.find_cached_cams_file(tmp_path/"cams",AREA,target)
    got = result(engine_binary,params(tmp_path,hours=(),target_utc=target.isoformat(),
                                      require_exact_valid_time=False))
    assert got["files"][0]["path"] == str(expected[0])


def test_nonexact_exact_area_legacy_hit_is_not_falsely_declared_decoded(engine_binary, tmp_path):
    path = seed_cams(tmp_path/"cams",[CYCLE+timedelta(hours=9)],AREA)[0]
    path.write_bytes(b"legacy nonempty but invalid contents")
    got = result(engine_binary,params(tmp_path,hours=(9,),require_exact_valid_time=False))
    assert got["cache_hits"] == 1
    assert got["cache_hit_grib_time_verified"] is False
    assert got["files"][0]["path"] == str(path)


def test_real_installed_python_sdk_accepts_same_offline_ads_contract(tmp_path, ads_server, monkeypatch):
    _, state = ads_server
    state.job_statuses = ["successful"]
    provider = aerosol.CamsAdsAerosolProvider(cache_dir=tmp_path/"python-sdk")
    targets = [CYCLE+timedelta(hours=h) for h in (6,9,12)]
    errors = []
    original = aerosol.retrieve_cams_file
    def capture(*args,**kwargs):
        try:
            return original(*args,**kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    monkeypatch.setattr(aerosol,"retrieve_cams_file",capture)
    original_split = aerosol._split_cams_bundle_by_lead
    def capture_split(*args,**kwargs):
        try:
            return original_split(*args,**kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    monkeypatch.setattr(aerosol,"_split_cams_bundle_by_lead",capture_split)
    got = provider.download_targets_batch(AREA,targets,require_exact_valid_time=True)
    assert not errors, errors
    assert set(got) == set(targets),dict(state.calls)
    assert state.calls["execution"] == state.calls["asset"] == 2
    assert state.calls["constraints"] == 7  # base + two days for every target


def test_wrong_clock_downloads_never_become_canonical_cache_files(engine_binary, tmp_path, ads_server):
    endpoint, state = ads_server
    state.wrong_clock = True
    response = invoke(engine_binary, params(tmp_path,endpoint,operation="download",hours=(9,)))
    assert response["type"] == "error" and "strict validation" in response["error"]["message"]
    assert not list((tmp_path/"cams").rglob("*.grib"))
    no_temps(tmp_path)


def test_broken_destination_is_preserved_on_failure_and_replaced_only_after_validation(
        engine_binary,tmp_path,ads_server):
    endpoint,state = ads_server
    path = seed_cams(tmp_path/"cams",[CYCLE+timedelta(hours=9)],AREA)[0]
    # Use the canonical dated output, rather than a legacy root cache file.
    canonical = path.parent/"2026-11-09"/"00Z"/path.name
    canonical.parent.mkdir(parents=True)
    path.rename(canonical)
    original = b"existing broken cache must survive failed replacement"
    canonical.write_bytes(original)
    state.invalid_bundle = True
    response = invoke(engine_binary,params(tmp_path,endpoint,operation="download",hours=(9,)))
    assert response["type"] == "error" and canonical.read_bytes() == original
    state.invalid_bundle = False
    got = result(engine_binary,params(tmp_path,endpoint,operation="download",hours=(9,)))
    assert got["files"][0]["path"] == str(canonical)
    assert canonical.read_bytes() != original


def test_proxy_environment_is_used_for_api_and_asset(engine_binary,tmp_path,ads_server,monkeypatch):
    # ADS honors the ambient proxy environment like the GFS/EC clients: with every
    # proxy variable pointed at a dead port the download must fail, and it must
    # succeed again once NO_PROXY exempts the test server host.
    endpoint,_ = ads_server
    for name in ("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy"):
        monkeypatch.setenv(name,"http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY","")
    monkeypatch.setenv("no_proxy","")
    response = invoke(engine_binary,params(tmp_path,endpoint,operation="download",hours=(9,)))
    assert response["type"] == "error"
    monkeypatch.setenv("NO_PROXY","127.0.0.1")
    assert result(engine_binary,params(tmp_path,endpoint,operation="download",hours=(9,)))["files"]


def test_full_cycle_download_fetches_every_reported_lead_in_one_execution(engine_binary,tmp_path,ads_server):
    endpoint,state = ads_server
    got = result(engine_binary,params(tmp_path,endpoint,operation="resolve",hours=(9,),all_leads=True))
    assert got["all_leads"] is True and got["execution_requests"] == 1
    assert got["missing_valid_times"] == []
    queries = [job["query"] for job in state.jobs.values()]
    assert len(queries) == 1
    assert queries[0]["leadtime_hour"] == [str(h) for h in range(0, 73, 3)]
    published = sorted(path.name for path in (tmp_path/"cams").rglob("*.grib"))
    keys = {name.rsplit("-",1)[-1] for name in published}
    assert len(keys) == 1
    leads = sorted(int(name.split("-")[-2]) for name in published)
    assert leads == [h for h in range(0, 73, 3)]


def test_full_cycle_cache_serves_later_targets_without_new_executions(engine_binary,tmp_path,ads_server):
    endpoint,state = ads_server
    result(engine_binary,params(tmp_path,endpoint,operation="resolve",hours=(9,),all_leads=True))
    assert state.calls["execution"] == 1
    got = result(engine_binary,params(tmp_path,endpoint,operation="resolve",hours=(12,)))
    assert got["all_leads"] is False and got["cache_hits"] == 1
    assert got["execution_requests"] == 0 and got["missing_valid_times"] == []
    assert state.calls["execution"] == 1


def test_credentials_file_and_env_precedence_without_exposing_key(engine_binary,tmp_path,ads_server,monkeypatch):
    endpoint,_ = ads_server
    credentials = tmp_path/"ads.rc"
    credentials.write_text(f"url: {endpoint}\nkey: {TOKEN}\nverify: 1\n")
    monkeypatch.delenv("CDSAPI_KEY")
    monkeypatch.delenv("CDSAPI_URL")
    request = params(tmp_path,endpoint,operation="download",hours=(9,),
                     config=dict(config(endpoint),credentials_file=str(credentials)))
    got = result(engine_binary,request)
    assert TOKEN not in json.dumps(got)


def test_two_processes_share_run_lock_and_reuse_one_download(engine_binary,tmp_path,ads_server):
    from concurrent.futures import ThreadPoolExecutor
    endpoint,state = ads_server
    state.response_delay = .15
    request = params(tmp_path,endpoint,operation="download",hours=(9,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:result(engine_binary,request), range(2)))
    assert state.calls["execution"] == 1
    assert results[0]["files"][0]["identity"] == results[1]["files"][0]["identity"]
    no_temps(tmp_path)


@pytest.mark.parametrize("mutation",[None,"zero"])
def test_nonexact_covering_cache_uses_header_not_full_profile_policy(engine_binary,tmp_path,mutation):
    subset = [.8,100.2,.2,101.8]
    path = seed_cams(tmp_path/"cams",[CYCLE+timedelta(hours=9)],AREA,key="other-area",mutation=mutation)[0]
    expected = aerosol.find_cached_cams_file(tmp_path/"cams",subset,CYCLE+timedelta(hours=9))
    got = result(engine_binary,params(tmp_path,hours=(9,),area=subset,require_exact_valid_time=False))
    assert got["files"][0]["path"] == str(expected[0]) == str(path)


def test_step_range_grouping_retains_complete_profile(engine_binary,tmp_path,ads_server):
    endpoint,state = ads_server
    state.range_aod = True
    got = result(engine_binary,params(tmp_path,endpoint,operation="batch",hours=(3,6)))
    assert [row["lead_hours"] for row in got["files"]] == [3,6]
    for row in got["files"]:
        optics = aerosol.load_cams_optics_grid(Path(row["path"]))
        assert optics.valid_time == datetime.fromisoformat(row["valid_time"])


def test_symlink_destination_never_overwrites_unowned_file(engine_binary,tmp_path,ads_server):
    endpoint,_ = ads_server
    directory = tmp_path/"cams"/"2026-11-09"/"00Z"
    directory.mkdir(parents=True)
    guard = tmp_path/"unowned"
    guard.write_bytes(b"leave unchanged")
    target = directory/f"cams-optics-v2-2026-11-09-0000-009-{aerosol.cams_area_key(AREA)}.grib"
    target.symlink_to(guard)
    response = invoke(engine_binary,params(tmp_path,endpoint,operation="download",hours=(9,)))
    assert response["type"] == "error"
    assert target.is_symlink() and guard.read_bytes() == b"leave unchanged"
    no_temps(tmp_path)


def test_invalid_bridge_backend_and_empty_batch_do_not_call_sdk(tmp_path,monkeypatch):
    monkeypatch.setenv("FIRESKY_CAMS_DATA_BACKEND","invalid")
    with pytest.raises(ValueError,match="CAMS_DATA_BACKEND"):
        rust_cams_data.enabled()
    monkeypatch.setenv("FIRESKY_CAMS_DATA_BACKEND","rust")
    provider = aerosol.CamsAdsAerosolProvider(cache_dir=tmp_path/"cams")
    assert provider.download_targets_batch(AREA,[]) == {}


def test_python_batch_split_retains_all_messages_for_each_lead(tmp_path):
    original = {hour:optical_bytes(tmp_path/f"source-{hour}.grib",CYCLE,hour) for hour in (3,6)}
    bundle = tmp_path/"bundle.grib"
    bundle.write_bytes(b"".join(original.values()))
    output = tmp_path/"split"
    output.mkdir()
    written = aerosol._split_cams_bundle_by_lead(bundle,output,CYCLE.date(),"00:00",
                                                aerosol.cams_area_key(AREA))
    assert set(written) == set(original)
    for hour,path in written.items():
        assert path.read_bytes() == original[hour]
        assert aerosol.load_cams_optics_grid(path).valid_time == CYCLE+timedelta(hours=hour)


@pytest.mark.parametrize("fold",[0,1])
def test_bridge_preserves_original_ambiguous_timezone_keys(tmp_path,monkeypatch,fold):
    from zoneinfo import ZoneInfo
    target = datetime(2026,11,1,1,0,tzinfo=ZoneInfo("America/New_York"),fold=fold)
    path = tmp_path/"selected.grib"
    path.write_bytes(b"selected cache")
    monkeypatch.setattr(rust_cams_data,"call_engine",lambda *a,**kw:dict(
        contract="rust-cams-data-v1",files=[dict(path=str(path),
            requested_valid_time=target.astimezone(timezone.utc).isoformat())]))
    provider = aerosol.CamsAdsAerosolProvider(cache_dir=tmp_path/"cams")
    got = rust_cams_data.download(provider,AREA,[target],batch=True)
    assert next(iter(got)) is target and got[target] == path
