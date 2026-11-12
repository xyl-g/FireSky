"""Full desktop inventory parity, native-client routing and filesystem limits."""
import hashlib
from collections import OrderedDict
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from firesky import aerosol, desktop_data, desktop_rpc
from test_rust_desktop_location import Wire, swift_location_probe
from test_rust_location import engine_binary, provider, timezone_source


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(aerosol,"_CAMS_AREA_CACHE", OrderedDict())
    monkeypatch.setattr(aerosol,"_CAMS_AREA_SIDECARS", {})
    monkeypatch.setattr(aerosol,"_CAMS_AREA_SIDECAR_DIRTY",set())
    config = dict(gfs_cache_dir=str(tmp_path/"gfs"),ecmwf_cache_dir=str(tmp_path/"ec"),
                  cams_cache_dir=str(tmp_path/"cams"))
    return config


def put(path, data=b"raw", meta=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(data)
    if meta is not None:
        path.with_suffix(".grib2.json").write_text(json.dumps(meta))
    return path


def legacy(config):
    config = SimpleNamespace(**{k:Path(v) for k,v in config.items()})
    return desktop_rpc.json_value(dict(datasets=desktop_data.inventory(config)))


def canonical(value):
    value = json.loads(json.dumps(value))
    for dataset in value["datasets"]:
        for cycle in dataset["cycles"].values():
            for key,areas in cycle.get("area_keys",{}).items():
                cycle["area_keys"][key] = sorted(areas)
    return value


def native(binary,tmp_path,config):
    client = Wire(binary,tmp_path,{},inventory=config)
    try:
        output = client.call("inventory","inventory")
        assert output["type"]=="result",output
        return canonical(output["result"])
    finally:
        client.close()


def tree_identity(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*") if p.is_file()}


def test_missing_roots_original_desktop_payload_and_no_creation(engine_binary,tmp_path,roots):
    expected=legacy(roots)
    assert native(engine_binary,tmp_path,roots)==expected
    assert not any(Path(v).exists() for v in roots.values())


def test_complete_mixed_multicycle_inventory_matches_python(engine_binary,tmp_path,roots):
    gfs,ec,cams=(Path(v) for v in roots.values())
    for folder,hour,size,bbox in [
        ("20261109/00Z/a",0,13,[100,104,32,28]),
        ("20261109/00Z/b",0,17,[99,105,33,27]),
        ("20261109/00Z/a",3,21,None),
        ("20261109/12Z/a",6,32,[100,104,32,28]),
    ]:
        cycle="2026-11-09T12:00:00+00:00" if "12Z" in folder else "2026-11-09T00:00:00Z"
        put(gfs/folder/f"gfs-{hour}.grib2",b"x"*size,
            dict(cycle=cycle,forecast_hour=hour,bbox=bbox,global_=False)
            if bbox is not None else dict(cycle=cycle,forecast_hour=hour,**{"global":True}))
    put(gfs/"unknown.grib2",b"x"*15)
    put(gfs/"empty.grib2",b"",dict(cycle="ignored"))
    put(gfs/"legacy.grib",b"x"*7)
    put(gfs/"incomplete.json",b"{}")
    for folder,name,size in [
        ("a","ifs-basic-v2-2026110900-f003.grib2",20),
        ("b","ifs-basic-v2-2026110900-f003.grib2",30),
        ("b","ifs-basic-v2-2026110912-f006.grib2",42),
        ("b","ifs-v3-2026110912-f006.grib2",10),
        ("b","ifs-basic-v2-2026110912-f009.grib2",0),
        ("b","unrecognized.grib2",11),("b","old.grib",12),
    ]:
        put(ec/folder/name,b"x"*size)
    for folder,name,size in [
        ("a","cams-optics-v2-2026-11-09-0000-003-aa.grib",20),
        ("b","cams-optics-v2-2026-11-09-0000-003-bb.grib",30),
        ("b","cams-optics-v2-2026-11-09-1230-006-aa.grib",45),
        ("b","cams-optics-v2-2026-11-09-0000-000-zero.grib",0),
        ("b","cams-optics-v1-2026-11-09-0000-003-cc.grib",15),
        ("b","other.grib2",16),
    ]:
        put(cams/folder/name,b"x"*size)
    expected=canonical(legacy(roots))
    before=tree_identity(tmp_path)
    actual=native(engine_binary,tmp_path,roots)
    # Wire startup config is owned by the test harness; inventory never writes caches.
    before.pop("location-ipc.json",None)
    after=tree_identity(tmp_path)
    after.pop("location-ipc.json",None)
    assert before==after
    assert actual==expected
    assert actual["datasets"][0]["cycles"]["2026-11-09T00:00:00Z"]["coverage"]["0"]==[
        [100.,104.,32.,28.],[99.,105.,33.,27.]]
    assert actual["datasets"][1]["total_file_count"]==7
    assert actual["datasets"][2]["area_bounds"]==dict(aa=None,bb=None,zero=None)


@pytest.mark.parametrize("cycle", [
    "2026-11-09T12:30:00Z","2026-11-09T12:30:00+08:00","2026-11-09T12:30:00",
    "2026-11-09 12:30:00","2026-11-09T12:30","2026-11-09","20261109",
    "20261109T123000","2026-11-09T12:30:00.123456Z","unparsed-cycle",
])
def test_gfs_cycle_key_label_is_not_converted_to_utc(engine_binary,tmp_path,roots,cycle):
    put(Path(roots["gfs_cache_dir"])/"one.grib2",b"x",dict(cycle=cycle,forecast_hour=3))
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


@pytest.mark.parametrize("meta", [
    {},dict(cycle=""),dict(cycle="cycle"),dict(cycle="cycle",forecast_hour=" 003 "),
    dict(cycle="cycle",forecast_hour=3.9),dict(cycle="cycle",forecast_hour=True),
    dict(cycle="cycle",forecast_hour="-1"),dict(cycle="cycle",forecast_hour=None),
    dict(cycle="cycle",forecast_hour="bad"),dict(cycle="cycle",bbox=None),
    dict(cycle="cycle",bbox=[]),dict(cycle="cycle",bbox=["1","2","3","4"]),
    dict(cycle="cycle",bbox=[1,2,"bad",4]),
    dict(cycle="cycle",bbox=[1,2,3,4],**{"global":True}),
    dict(cycle="cycle",bbox=[1,2,3,4],**{"global":"false"}),
    dict(cycle="cycle",bbox=[1,2,3,4],**{"global":[]}),
])
def test_gfs_sidecar_parsing_and_failed_index_still_count_raw(engine_binary,tmp_path,roots,meta):
    put(Path(roots["gfs_cache_dir"])/"one.grib2",b"x"*100,meta)
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


@pytest.mark.parametrize("name", [
    "ifs-v3-2026110900-f003.grib2","ifs-basic-v2-2026110900-f03.grib2",
    "ifs-basic-v2-2026999944-f999.grib2","ifs-basic-v2-2026110900-f003.grib",
    "ifs-basic-v2-2026110900-f003.grib2.bak","IFS-basic-v2-2026110900-f003.grib2",
])
def test_ec_inventory_name_admission_is_not_full_readiness(engine_binary,tmp_path,roots,name):
    put(Path(roots["ecmwf_cache_dir"])/name)
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


@pytest.mark.parametrize("name", [
    "cams-optics-v2-2026-11-09-1230-003-aa.grib",
    "cams-optics-v2-2026-99-99-9999-999-aa.grib",
    "cams-optics-v2-2026-11-09-0000-003-area-name.grib",
    "cams-optics-v2-2026-11-09-0000-003-AA.grib",
    "cams-optics-v2-2026-11-09-0000-03-aa.grib",
    "cams-spectral-v1-2026-11-09-0000-003-aa.grib",
])
def test_cams_compact_vs_spectral_and_area_key_semantics(engine_binary,tmp_path,roots,name):
    put(Path(roots["cams_cache_dir"])/name)
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


def test_shared_cams_area_sidecar_allows_python_free_eccodes_free_inventory(engine_binary,tmp_path,roots):
    path=put(Path(roots["cams_cache_dir"])/"cams-optics-v2-2026-11-09-0000-003-aa.grib")
    stat=path.stat()
    sidecar=path.parent/".cams-area-index-v1.json"
    sidecar.write_text(json.dumps({path.name:dict(m=stat.st_mtime_ns,s=stat.st_size,a=[32,100,28,104])}))
    expected=legacy(roots)
    before=tree_identity(path.parent)
    assert native(engine_binary,tmp_path,roots)==expected
    assert tree_identity(path.parent)==before
    assert expected["datasets"][2]["area_bounds"]==dict(aa=[32.,100.,28.,104.])


@pytest.mark.parametrize("fault", ["mtime","size","shape","json","missing"])
def test_stale_invalid_area_sidecar_never_fakes_bounds(engine_binary,tmp_path,roots,fault):
    path=put(Path(roots["cams_cache_dir"])/"cams-optics-v2-2026-11-09-0000-003-aa.grib")
    stat=path.stat()
    value=dict(m=stat.st_mtime_ns,s=stat.st_size,a=[32,100,28,104])
    if fault=="mtime": value["m"]-=1
    elif fault=="size": value["s"]+=1
    elif fault=="shape": value["a"]=[1,2,3]
    if fault!="missing":
        (path.parent/".cams-area-index-v1.json").write_text(
            "unowned invalid JSON" if fault=="json" else json.dumps({path.name:value}))
    assert native(engine_binary,tmp_path,roots)==legacy(roots)
    assert legacy(roots)["datasets"][2]["area_bounds"]=={"aa":None}


@pytest.mark.parametrize("first_lon", [100,180,358])
def test_actual_first_grib_area_and_dateline_normalization(engine_binary,tmp_path,roots,first_lon):
    import numpy as np
    codes=pytest.importorskip("eccodes")
    path=Path(roots["cams_cache_dir"])/"cams-optics-v2-2026-11-09-0000-003-aa.grib"
    path.parent.mkdir()
    handle=codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        for key,value in dict(Ni=3,Nj=2,latitudeOfFirstGridPointInDegrees=1,
            latitudeOfLastGridPointInDegrees=0,longitudeOfFirstGridPointInDegrees=first_lon,
            longitudeOfLastGridPointInDegrees=(first_lon+2)%360,
            iDirectionIncrementInDegrees=1,jDirectionIncrementInDegrees=1).items():
            codes.codes_set(handle,key,value)
        codes.codes_set_values(handle,np.arange(6,dtype=float))
        with path.open("wb") as stream:
            codes.codes_write(handle,stream)
            stream.write(b"malformed later data must not affect first-message area")
    finally:
        codes.codes_release(handle)
    expected=legacy(roots)
    # Wire intentionally removes ecCodes. Use the explicit host library for this test.
    settings=tmp_path/"config.json"
    settings.write_text(json.dumps(dict(inventory=roots)))
    process=subprocess.Popen([str(engine_binary),"desktop-location","--config",str(settings)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        env=dict(os.environ,PATH="",PYTHONPATH="",FIRESKY_PYTHON="/no/python",
            FIRESKY_ECCODES_LIBRARY="/opt/homebrew/lib/libeccodes.dylib"))
    try:
        process.stdin.write('{"id":"i","method":"inventory","params":{}}\n')
        process.stdin.flush()
        value=json.loads(process.stdout.readline())
        assert value["type"]=="result",value
        assert canonical(value["result"])==canonical(expected)
        longitudes=[(first_lon+i+180)%360-180 for i in range(3)]
        assert value["result"]["datasets"][2]["area_bounds"]["aa"]==[1.,min(longitudes),0.,max(longitudes)]
    finally:
        process.stdin.close()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
    assert not (path.parent/".cams-area-index-v1.json").exists()


def test_repeat_scan_not_stale_for_new_deleted_or_resized_files(engine_binary,tmp_path,roots):
    root=Path(roots["ecmwf_cache_dir"])
    a=put(root/"ifs-basic-v2-2026110900-f003.grib2",b"x")
    client=Wire(engine_binary,tmp_path,{},inventory=roots)
    try:
        assert canonical(client.call("a","inventory")["result"])==canonical(legacy(roots))
        a.write_bytes(b"x"*1000)
        b=put(root/"ifs-basic-v2-2026110912-f006.grib2",b"x"*1024)
        assert canonical(client.call("b","inventory")["result"])==canonical(legacy(roots))
        a.unlink()
        b.unlink()
        assert canonical(client.call("c","inventory")["result"])==canonical(legacy(roots))
    finally:
        client.close()


def test_nested_duplicate_hours_preserve_current_rglob_last_path_order(engine_binary,tmp_path,roots):
    root=Path(roots["ecmwf_cache_dir"])
    for folder in ["a","b","a/inner","b/inner","a/inner/deep","b/inner/deep","c","c/inner"]:
        put(root/folder/"ifs-basic-v2-2026110900-f003.grib2",b"x"*len(folder))
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


def test_directory_symlinks_not_recursed_and_regular_file_links_counted(engine_binary,tmp_path,roots):
    root=Path(roots["gfs_cache_dir"])
    target=put(root/"real"/"gfs.grib2",b"x"*5,dict(cycle="2026-11-09",forecast_hour=0))
    (root/"loop").symlink_to(root,target_is_directory=True)
    (root/"alias").symlink_to(root/"real",target_is_directory=True)
    (root/"copy.grib2").symlink_to(target)
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


def test_symlink_root_and_missing_root_paths_resolve_like_desktop(engine_binary,tmp_path,roots):
    target=tmp_path/"target"
    target.mkdir()
    link=tmp_path/"alias"
    link.symlink_to(target,target_is_directory=True)
    roots["gfs_cache_dir"]=str(link/"missing")
    roots["ecmwf_cache_dir"]=str(link)
    roots["cams_cache_dir"]=str(link/"missing"/".."/"cams")
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


@pytest.mark.parametrize("ec_root", [".","./ec","./ec//"])
def test_relative_paths_and_current_directory_match_pathlib(
    engine_binary,tmp_path,roots,monkeypatch,ec_root
):
    monkeypatch.chdir(tmp_path)
    roots.update(gfs_cache_dir="./missing-gfs",ecmwf_cache_dir=ec_root,cams_cache_dir="./missing-cams")
    put(Path(ec_root)/"ifs-basic-v2-2026110900-f003.grib2")
    assert native(engine_binary,tmp_path,roots)==legacy(roots)


def test_depth_bound_returns_explicit_error_and_control_plane_survives(engine_binary,tmp_path,roots):
    path=Path(roots["gfs_cache_dir"])
    for _ in range(130): path=path/"a"
    path.mkdir(parents=True)
    client=Wire(engine_binary,tmp_path,{},inventory=roots)
    try:
        value=client.call("a","inventory")
        assert value["type"]=="error" and "depth" in value["error"]["message"]
        assert client.call("h","hello")["type"]=="result"
    finally:
        client.close()


@pytest.mark.parametrize("kind",["fifo","directory"])
def test_special_raw_name_rejected_without_blocking(engine_binary,tmp_path,roots,kind):
    root=Path(roots["cams_cache_dir"])
    root.mkdir()
    path=root/"cams-optics-v2-2026-11-09-0000-003-aa.grib"
    if kind=="fifo": os.mkfifo(path)
    else: path.mkdir()
    client=Wire(engine_binary,tmp_path,{},inventory=roots)
    try:
        value=client.call("a","inventory")
        assert value["type"]=="error" and "regular" in value["error"]["message"]
    finally:
        client.close()


@pytest.mark.parametrize("fault",["symlink","fifo","oversized"])
def test_untrusted_gfs_sidecars_are_ignored_without_mutation(engine_binary,tmp_path,roots,fault):
    path=put(Path(roots["gfs_cache_dir"])/"one.grib2")
    sidecar=path.with_suffix(".grib2.json")
    foreign=tmp_path/"foreign"
    foreign.write_text(json.dumps(dict(cycle="2026-11-09",forecast_hour=0)))
    if fault=="symlink": sidecar.symlink_to(foreign)
    elif fault=="fifo": os.mkfifo(sidecar)
    else: sidecar.write_bytes(b" "* (16*1024*1024+1))
    output=native(engine_binary,tmp_path,roots)
    assert output["datasets"][0]["file_count"]==1
    assert output["datasets"][0]["cycles"]=={}
    assert json.loads(foreign.read_text())["forecast_hour"]==0


@pytest.mark.parametrize("failure",[False,True])
def test_real_swift_data_page_inventory_bypasses_python(
    engine_binary,swift_location_probe,provider,tmp_path,roots,failure
):
    location,_=provider
    put(Path(roots["gfs_cache_dir"])/"one.grib2",b"x"*13,
        dict(cycle="2026-11-09T00:00:00Z",forecast_hour=0))
    if failure:
        os.mkfifo(Path(roots["gfs_cache_dir"])/"unowned.grib2")
    root=Path(__file__).resolve().parents[1]
    stub=tmp_path/"legacy-engine"
    import sys
    stub.write_text(f"#!{sys.executable}\n"+(root/"tests/fixtures/native_location_python_stub.py").read_text())
    stub.chmod(0o755)
    settings,record=tmp_path/"settings.json",tmp_path/"calls.jsonl"
    settings.write_text(json.dumps(dict(location=location,inventory=roots)))
    workspace=tmp_path/"workspace"
    workspace.mkdir()
    process=subprocess.run([str(swift_location_probe),str(workspace),str(engine_binary),
        "inventoryfailure" if failure else "inventory"],
        env=dict(os.environ,FIRESKY_RUST_ENGINE_BIN=str(engine_binary),
            FIRESKY_LOCATION_ENGINE_BIN=str(engine_binary),FIRESKY_PYTHON=str(stub),
            FIRESKY_LOCATION_CONFIG=str(settings),FIRESKY_NATIVE_LOG_DIR=str(tmp_path/"logs"),
            FIRESKY_PROBE_PYTHON_RECORD=str(record)),
        capture_output=True,text=True,timeout=15)
    assert process.returncode==0,process.stdout+process.stderr
    value=json.loads(process.stdout)
    if failure:
        assert "regular" in value["nativeFailure"]
    else:
        assert canonical(value["inventory"])==canonical(legacy(roots))
    assert not record.exists(), "native inventory started the forbidden Python sidecar"


@pytest.mark.parametrize("config", [
    dict(gfs_cache_dir=""),dict(gfs_cache_dir=None),dict(ecmwf_cache_dir=3),
    dict(unknown_cache_dir="/no/file"),dict(cams_cache_dir="x"*4097),
])
def test_invalid_inventory_startup_settings_fail_before_io(engine_binary,tmp_path,config):
    path=tmp_path/"settings.json"
    path.write_text(json.dumps(dict(inventory=config)))
    process=subprocess.run([str(engine_binary),"desktop-location","--config",str(path)],
        input="",capture_output=True,text=True,timeout=3)
    assert process.returncode==1 and not process.stdout


def test_production_desktop_service_inventory_uses_rust_bridge(
    engine_binary, tmp_path, roots, monkeypatch
):
    from firesky.web_server import WebServerConfig
    from firesky.rust_desktop_process import close

    put(
        Path(roots["gfs_cache_dir"]) / "one.grib2",
        b"x" * 13,
        dict(cycle="2026-11-09T00:00:00Z", forecast_hour=0),
    )
    expected = legacy(roots)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setattr(
        desktop_data,
        "_python_inventory",
        lambda *_args, **_kwargs: pytest.fail("production inventory used Python"),
    )
    config = WebServerConfig(
        gfs_cache_dir=Path(roots["gfs_cache_dir"]),
        ecmwf_cache_dir=Path(roots["ecmwf_cache_dir"]),
        cams_cache_dir=Path(roots["cams_cache_dir"]),
    )
    try:
        actual = desktop_rpc.DesktopService(config).dispatch("inventory", {})
    finally:
        close()
    assert actual == expected
