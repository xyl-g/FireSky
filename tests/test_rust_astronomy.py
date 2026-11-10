"""Astral/time differential tests, with Python retained only as reference."""

import json
import os
from pathlib import Path
import random
import subprocess
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from firesky import geometry, rust_astronomy, time_contract, time_utils
from firesky.models import GeoPoint

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def backend(monkeypatch):
    binary = Path(os.environ.get("FIRESKY_RUST_ENGINE_BIN", ROOT / "engine/target/debug/firesky-engine"))
    if not binary.is_file():
        message = "build Rust engine before running astronomy differential tests"
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(binary))
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "python")
    return binary


CASES = [
    (31.23,121.47,0,"Asia/Shanghai","2026-11-09"),
    (39.90,116.40,44,"Asia/Shanghai","2026-06-21"),
    (30.0,90.0,4800,"Asia/Shanghai","2026-12-21"),
    (0,0,0,"UTC","2024-02-29"),
    (51.5,-0.1,12,"Europe/London","2026-03-29"),
    (51.5,-0.1,12,"Europe/London","2026-10-25"),
    (40.7,-74,10,"America/New_York","2026-03-08"),
    (40.7,-74,10,"America/New_York","2026-11-01"),
    (-33.9,151.2,33,"Australia/Sydney","2026-10-04"),
    (27.7,85.3,1400,"Asia/Kathmandu","2026-02-01"),
    (1.9,-157.4,0,"Pacific/Kiritimati","2026-01-01"),
    (-13.8,-171.8,0,"Pacific/Apia","2026-12-31"),
    (0,180,-400,"Etc/GMT-12","2026-03-21"),
    (0,-180,0,"Etc/GMT+12","2026-09-21"),
    (31.2,121.5,0,"Asia/Shanghai","1900-01-01"),
]


@pytest.mark.parametrize("lat,lon,height,tz,day", CASES)
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_events_match_astral(monkeypatch, lat, lon, height, tz, day, phenomenon):
    point = GeoPoint(lat,lon,height)
    day = date.fromisoformat(day)
    expected = geometry.solar_event_time_local(day,point,tz,phenomenon)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    actual = geometry.solar_event_time_local(day,point,tz,phenomenon)
    assert abs((actual-expected).total_seconds()) <= 5e-6
    assert actual.date() == day
    assert actual.tzinfo.key == tz
    assert actual.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize("latitude,day", [(89,"2026-06-21"),(-89,"2026-12-21"),(89,"2026-12-21"),(61,"2026-06-21")])
def test_full_sun_contract_rejects_missing_twilight(monkeypatch, latitude, day):
    point = GeoPoint(latitude,0)
    with pytest.raises(ValueError):
        geometry.solar_event_time_local(date.fromisoformat(day),point,"UTC","dawn")
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    with pytest.raises(ValueError):
        geometry.solar_event_time_local(date.fromisoformat(day),point,"UTC","dawn")


def test_positions_randomized_and_subsecond_reference(monkeypatch):
    rng = random.Random(20261109)
    cases = [(GeoPoint(rng.uniform(-90,90),rng.uniform(-180,180),rng.uniform(-400,5000)),
        datetime(1900,1,1,tzinfo=timezone.utc)+timedelta(seconds=rng.randrange(8_000_000_000),microseconds=123456))
        for _ in range(512)]
    cases.extend((GeoPoint(lat,lon),datetime(2026,6,21,12,tzinfo=timezone.utc)) for lat in [-90,0,90] for lon in [-180,0,180])
    expected = [geometry.solar_snapshot(point,time,"Asia/Kathmandu") for point,time in cases]
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    try:
        rust_astronomy._position_function()
    except RuntimeError as exc:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1": pytest.fail(str(exc))
        pytest.skip(str(exc))
    for (point,time), reference in zip(cases,expected):
        actual = geometry.solar_snapshot(point,time,"Asia/Kathmandu")
        assert actual.elevation_deg == pytest.approx(reference.elevation_deg,abs=2e-10)
        assert actual.azimuth_deg == pytest.approx(reference.azimuth_deg,abs=2e-9)
        assert actual.valid_time_utc == reference.valid_time_utc
        assert actual.valid_time_local == reference.valid_time_local
        assert actual.elevation_deg == rust_astronomy.position(point,time.replace(microsecond=0))[0]


def test_complete_event_dictionary_and_historical_offsets():
    for lat,lon,height,tz,day in CASES:
        point = GeoPoint(lat,lon,height)
        expected = geometry.sun(geometry.observer_for(point),date=date.fromisoformat(day),tzinfo=ZoneInfo(tz))
        actual = rust_astronomy.call_engine("solar_events",{
            "point":rust_astronomy._point(point), "date":day,
            "timezone_name":tz,"phenomenon":"dusk",
        })["events"]
        assert set(actual) == set(expected)
        for name,reference in expected.items():
            got = datetime.fromisoformat(actual[name])
            assert abs((got-reference).total_seconds()) <= 5e-6
            assert got.utcoffset() == reference.utcoffset()


@pytest.mark.parametrize("phenomenon", ["dawn","dusk"])
def test_crossings_windows_and_speed(monkeypatch, phenomenon):
    point = GeoPoint(31.23,121.47,50)
    anchor = geometry.solar_event_time_local(date(2026,11,9),point,"Asia/Shanghai",phenomenon)
    targets = [-30,-10,-5,-2,-.8,0,20,90]
    crossings = [geometry.solar_elevation_crossing_utc(point,anchor,target,phenomenon) for target in targets]
    speed = geometry.solar_shadow_speed_km_per_min(point,anchor)
    functions = [
        (geometry.typical_deck_glow_window, dict(edge_distance_km=80,cloud_base_m=6000)),
        (geometry.typical_deck_glow_window, dict(edge_distance_km=80,cloud_base_m=40)),
        (geometry.typical_deck_glow_window, dict(edge_distance_km=800,cloud_base_m=6000)),
        (geometry.typical_deck_glow_window, dict(edge_distance_km=20,cloud_base_m=3000,observer_elevation_m=1000)),
        (geometry.target_cloud_glow_window, dict(target_distance_km=100,cloud_base_m=4000,cloud_top_m=9000,regime="remote")),
        (geometry.target_cloud_glow_window, dict(target_distance_km=800,cloud_base_m=4000,cloud_top_m=9000,regime="remote")),
        (geometry.target_cloud_glow_window, dict(target_distance_km=1,cloud_base_m=4000,cloud_top_m=0,regime="below-base")),
    ]
    expected = [function(point,anchor,"Asia/Shanghai",phenomenon,**args) for function,args in functions]
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    for target,reference in zip(targets,crossings):
        actual = geometry.solar_elevation_crossing_utc(point,anchor,target,phenomenon)
        assert (actual is None) == (reference is None)
        if actual is not None: assert abs((actual-reference).total_seconds()) <= 2e-6
    assert geometry.solar_shadow_speed_km_per_min(point,anchor) == pytest.approx(speed,abs=1e-9)
    for (function,args),reference in zip(functions,expected):
        actual = function(point,anchor,"Asia/Shanghai",phenomenon,**args)
        assert (actual is None) == (reference is None)
        if actual is not None:
            for key,value in vars(reference).items():
                got = getattr(actual,key)
                if isinstance(value,datetime): assert abs((got-value).total_seconds()) <= 2e-6
                elif isinstance(value,float): assert got == pytest.approx(value,abs=1e-7)
                else: assert got == value


@pytest.mark.parametrize("value", [
    "2024-02-29", "0001-01-01", "9999-12-31", "2026-02-29",
    "2026-1-01", "2026-01-01Z", "２０２６-01-01", 20260101, None,
])
def test_date_contract(monkeypatch,value):
    try: expected = time_contract.parse_iso_date(value,"target")
    except ValueError: expected = None
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    if expected is None:
        with pytest.raises(ValueError,match="target must be an ISO calendar date"):
            time_contract.parse_iso_date(value,"target")
    else: assert time_contract.parse_iso_date(value,"target") == expected


@pytest.mark.parametrize("utc_only", [False,True])
@pytest.mark.parametrize("value", [
    "2026-11-09T02:03:04Z", "2026-11-09T02:03:04.000001+00:00",
    "2026-11-09T02:03:04.123456+14:00", "2026-11-09T02:03:04-14:00",
    "2026-11-09T02:03:04-00:00", "2026-11-09T02:03:04+14:01",
    "2026-11-09T02:03:04+00:60", "2026-11-09T02:03:60Z",
    "2026-11-09T24:00:00Z", "2026-11-09T02:03:04.1234567Z",
    "2026-11-09T24:00:00.000000Z", "2026-11-09T24:00:00.000001Z",
    "2026-11-09T24:00:01Z", "2026-11-09T24:01:00Z", "9999-12-31T24:00:00Z",
    "2026-11-09T02:03:04.Z", "2026-11-09 02:03:04Z",
    "2026-11-09T02:03:04", "0000-01-01T00:00:00Z",
    "0001-01-01T00:00:00Z", "9999-12-31T23:59:59.999999Z", None,
])
def test_timestamp_contract(monkeypatch,utc_only,value):
    function = time_contract.parse_utc_iso_datetime if utc_only else time_contract.parse_zoned_iso_datetime
    try: expected = function(value,"sample")
    except ValueError: expected = None
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    if expected is None:
        with pytest.raises(ValueError): function(value,"sample")
    else: assert function(value,"sample") == expected


def test_cycle_rounding_and_china_date(monkeypatch):
    init = datetime(2026,11,8,18,tzinfo=timezone.utc)
    times = [init+timedelta(hours=h,microseconds=us) for h in [-2.5,-.5,.5,1.5,2.5,3.5] for us in [-1,0,1]]
    offsets = geometry.nearest_hour_offsets(init,times)
    references = [init+timedelta(minutes=i) for i in [-1439,-1,0,359,360,720,1439]]
    cycles = [geometry.choose_cycle(value,(0,6,12,18),5) for value in references]
    dates = [time_utils.china_today(value) for value in references]
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    assert geometry.nearest_hour_offsets(init,iter(times)) == offsets
    assert [geometry.choose_cycle(value,(0,6,12,18),5) for value in references] == cycles
    assert [time_utils.china_today(value) for value in references] == dates
    assert time_utils.china_today(datetime(2026,11,8,16)) == date(2026,11,9)


def test_c_abi_and_standalone_window_are_identical(monkeypatch):
    point = GeoPoint(31.23,121.47,50)
    anchor = geometry.solar_event_time_local(date(2026,11,9),point,"Asia/Shanghai","dusk")
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    args = {"kind":"target","target_distance_km":100,"cloud_base_m":4000,"cloud_top_m":9000,"regime":"remote"}
    actual = rust_astronomy.glow_window(point,anchor,"Asia/Shanghai","dusk",**args)
    expected = rust_astronomy.call_engine("solar_glow_window",{
        "point":rust_astronomy._point(point),"anchor":anchor.isoformat(),
        "timezone_name":"Asia/Shanghai","phenomenon":"dusk","window":args,
    })["window"]
    assert actual.to_dict() == expected
    def forbidden(*args,**kwargs): raise AssertionError("Python solar helper called")
    monkeypatch.setattr(geometry,"_solar_elevation_deg",forbidden)
    monkeypatch.setattr(geometry,"solar_geometric_elevation_for_illumination_deg",forbidden)
    assert rust_astronomy.glow_window(point,anchor,"Asia/Shanghai","dusk",**args) == actual


def test_result_cache_identities_are_backend_and_artifact_specific(monkeypatch,tmp_path):
    from firesky import web_server
    payload = {"lat":31.23,"lon":121.47,"elevation_m":0,
        "timezone_name":"Asia/Shanghai","target_date":"2026-11-09","phenomenon":"dusk","model_source":"gfs"}
    monkeypatch.setattr(web_server,"_point_model_cycle",lambda _: datetime(2026,11,9,tzinfo=timezone.utc))
    monkeypatch.setattr(web_server,"_national_model_cycle",lambda *args: datetime(2026,11,9,tzinfo=timezone.utc))
    python_point = web_server.point_forecast_cache_key(payload)
    python_region = web_server.national_forecast_cache_identity(payload)
    assert rust_astronomy.backend_identity() == {}
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    rust_point = web_server.point_forecast_cache_key(payload)
    rust_region = web_server.national_forecast_cache_identity(payload)
    assert rust_point != python_point
    assert rust_region != python_region
    identity = rust_astronomy.backend_identity()
    assert identity["astronomy_backend"] == "rust-astronomy-v1"
    assert len(identity["astronomy_engine_sha256"]) == 64
    for key in identity: rust_region.pop(key)
    assert rust_region == python_region
    replacement = tmp_path / "engine"
    replacement.write_bytes(b"changed artifact")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN",str(replacement))
    first = rust_astronomy.backend_identity()["astronomy_engine_sha256"]
    assert first != identity["astronomy_engine_sha256"]
    replacement.write_bytes(b"second artifact")
    assert rust_astronomy.backend_identity()["astronomy_engine_sha256"] != first


def test_engine_batch_standalone_and_no_astral_fallback(monkeypatch,backend):
    point = GeoPoint(31.2,121.5)
    times = [datetime(2026,11,9,8,tzinfo=timezone.utc)+timedelta(microseconds=i) for i in range(10)]
    expected = [geometry.solar_snapshot(point,time,"America/New_York") for time in times]
    request = {"id":"solar","method":"solar_snapshots","params":{
        "point": {"lat":point.lat,"lon":point.lon}, "times":[time.isoformat() for time in times],
        "timezone_name":"America/New_York",
    }}
    env = dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/nonexistent/python")
    result = subprocess.run([str(backend),"serve"],input=json.dumps(request)+"\n",
        capture_output=True,text=True,check=True,env=env,timeout=10)
    message = json.loads(result.stdout)
    assert message["type"] == "result", message
    for actual,reference in zip(message["result"]["snapshots"],expected):
        assert actual["elevation_deg"] == pytest.approx(reference.elevation_deg,abs=2e-10)
        assert datetime.fromisoformat(actual["valid_time_local"]) == reference.valid_time_local
    def forbidden(*args,**kwargs): raise AssertionError("Python astronomical calculation used")
    monkeypatch.setattr(geometry,"sun",forbidden)
    monkeypatch.setattr(geometry,"elevation",forbidden)
    monkeypatch.setattr(geometry,"azimuth",forbidden)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    geometry.solar_event_time_local(date(2026,11,9),point,"Asia/Shanghai","dusk")
    geometry.solar_snapshot(point,times[0],"Asia/Shanghai")


@pytest.mark.parametrize("method,params", [
    ("solar_events", {"point":{"lat":91,"lon":0},"date":"2026-11-09","timezone_name":"UTC","phenomenon":"dusk"}),
    ("solar_events", {"point":{"lat":0,"lon":0},"date":"2026-11-09","timezone_name":"bad/zone","phenomenon":"dusk"}),
    ("solar_events", {"point":{"lat":0,"lon":0},"date":"2026-11-09","timezone_name":"UTC","phenomenon":"sunrise"}),
    ("solar_crossing", {"point":{"lat":0,"lon":0},"anchor":"2026-11-09T00:00:00Z","target_elevation_deg":0,"phenomenon":"dawn","search_minutes":1441}),
    ("time_contract", {"operation":"choose_cycle","reference":"2026-11-09T00:00:00Z","cycle_hours":[],"availability_lag_hours":0}),
    ("time_contract", {"operation":"choose_cycle","reference":"2026-11-09T00:00:00Z","cycle_hours":[12,0],"availability_lag_hours":0}),
    ("time_contract", {"operation":"parse_date","value":"2026-11-09","extra":1}),
    ("solar_snapshots", {"point":{"lat":0,"lon":0},"times":["2026-11-09T00:00:00Z"]*4097,"timezone_name":"UTC"}),
    ("solar_crossing", {"point":{"lat":0,"lon":0},"anchor":"0001-01-01T00:00:00Z","target_elevation_deg":0,"phenomenon":"dawn"}),
])
def test_typed_limits_and_errors(backend,method,params):
    request = {"id":"bad","method":method,"params":params}
    result = subprocess.run([str(backend)],input=json.dumps(request)+"\n",
        capture_output=True,text=True,check=True,timeout=10)
    assert json.loads(result.stdout)["type"] == "error"


def test_unavailable_engine_and_invalid_backend_fail_closed(monkeypatch):
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN","/nonexistent/firesky-engine")
    with pytest.raises(RuntimeError,match="unavailable"):
        geometry.solar_event_time_local(date(2026,11,9),GeoPoint(31,121),"UTC","dusk")
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND","other")
    with pytest.raises(ValueError,match="FIRESKY_ASTRONOMY_BACKEND"):
        geometry.solar_snapshot(GeoPoint(0,0),datetime.now(timezone.utc),"UTC")
