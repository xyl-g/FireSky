#!/usr/bin/env python3
"""Cold-cache China input download comparison; never changes application caches."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def emit(event, **values):
    print(json.dumps(dict(event=event, **values)), flush=True)


def plan(args):
    from firesky.desktop_data import model_context
    from firesky.aerosol import cams_profile_bracket_times

    result = dict(
        created_at=datetime.now(timezone.utc).isoformat(),
        target_date=args.date, phenomenon="dusk", proxy=args.proxy,
        cycle=args.cycle, ec_source=args.ec_source,
        scope="China 0.25-degree conservative terrain-pending preflight; raw inputs only",
        local_cache="independent empty directories; remote provider caches uncontrolled",
        concurrency=1, models={},
    )
    for model in ("gfs", "ec"):
        context = model_context(dict(
            regions=["china"], target_date=args.date, phenomenon="dusk",
            model_source=model, workers=1,
            **{f"_{model}_cycle_override": args.cycle},
        ), regional=True, fast=True)
        targets = sorted({
            target for sample in context["times"]
            for target in cams_profile_bracket_times(sample)
        })
        result["models"][model] = dict(
            bbox=context["bbox"], area=context["area"], hours=context["hours"],
            points=len(context["points"]), terrain_pending=context["terrain_pending"],
            cams_targets=[target.isoformat() for target in targets],
        )
    areas = [item["area"] for item in result["models"].values()]
    result["cams_area"] = [
        max(a[0] for a in areas), min(a[1] for a in areas),
        min(a[2] for a in areas), max(a[3] for a in areas),
    ]
    targets = sorted({
        target for model in result["models"].values() for target in model["cams_targets"]
    })
    cycle = datetime.fromisoformat(args.cycle)
    # Resolve the actual available CAMS cycle/lead for each target. The model
    # cycle is not necessarily a valid CAMS publication cycle.
    import requests
    from cdsapi import Client
    from firesky.aerosol import choose_best_cams_selection
    session = requests.Session()
    session.trust_env = False
    process = Client(quiet=True, progress=False, session=session).client.get_process(
        "cams-global-atmospheric-composition-forecasts"
    )
    base = dict(
        variable=["total_aerosol_optical_depth_550nm",
                  "aerosol_extinction_coefficient_532nm"],
        pressure_level=["1000", "950", "925", "900", "850", "800", "700",
                        "600", "500", "400", "300"],
        type=["forecast"], data_format="grib", area=result["cams_area"],
    )
    constrained = process.apply_constraints(base)
    selections = [
        choose_best_cams_selection(
            process, base, constrained, datetime.fromisoformat(value)
        )
        for value in targets
    ]
    if not selections or any(selection is None for selection in selections):
        raise RuntimeError("CAMS availability did not cover the target window")
    run_dates = {selection[0] for selection in selections}
    run_times = {selection[1] for selection in selections}
    if len(run_dates) != 1 or len(run_times) != 1:
        raise RuntimeError("CAMS benchmark target window spans multiple runs")
    run_date, run_time = selections[0][0], selections[0][1]
    result["cams_query"] = dict(
        variable=["total_aerosol_optical_depth_550nm", "aerosol_extinction_coefficient_532nm"],
        pressure_level=["1000", "950", "925", "900", "850", "800", "700", "600", "500", "400", "300"],
        type=["forecast"], data_format="grib", area=result["cams_area"],
        date=[run_date.isoformat()], time=[run_time],
        leadtime_hour=[str(selection[2]) for selection in selections],
    )
    return result


def configure_route(mode, proxy):
    import requests

    for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                "ALL_PROXY", "no_proxy", "NO_PROXY"):
        os.environ.pop(key, None)
    proxies = {"http": proxy, "https": proxy} if mode == "proxy" else {}
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler(proxies)))
    original = requests.Session.request
    stats = dict(requests=0, request_return_seconds=0.0,
                 note="requests only; excludes urllib; return time is not transfer-only")

    def request(session, method, url, **kwargs):
        session.trust_env = False
        kwargs["proxies"] = proxies
        kwargs.setdefault("timeout", (12, 60))
        started = time.monotonic()
        stats["requests"] += 1
        try:
            return original(session, method, url, **kwargs)
        finally:
            stats["request_return_seconds"] += time.monotonic() - started

    requests.Session.request = request
    return stats


def validate(path):
    import eccodes

    count = 0
    with path.open("rb") as handle:
        while True:
            gid = eccodes.codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                if len(eccodes.codes_get_values(gid)) == 0:
                    raise ValueError("empty GRIB field")
                count += 1
            finally:
                eccodes.codes_release(gid)
    if not count:
        raise ValueError("no GRIB messages")
    return count


def cams_download(manifest, directory, result, persist):
    import requests
    from firesky.desktop_credentials import ADS_URL, _path, _read
    from firesky.desktop_network import _api_link, _link, _safe_https
    from firesky.aerosol import _split_cams_bundle_by_lead, _cams_optics_file_valid
    from urllib.parse import urljoin

    key = os.environ.get("CDSAPI_KEY") or _read(_path()).get("key")
    if not key:
        raise ValueError("ADS credential missing")
    session = requests.Session()
    headers = {"PRIVATE-TOKEN": key}

    def document(method, url, **kwargs):
        response = session.request(method, url, headers=headers, allow_redirects=False, **kwargs)
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("unexpected authenticated redirect")
        return response.json()

    process = f"{ADS_URL}/retrieve/v1/processes/cams-global-atmospheric-composition-forecasts"
    started = time.monotonic()
    job = document("POST", process + "/execution", json={"inputs": manifest["cams_query"]})
    monitor = _api_link(process, _link(job, "monitor"))
    result["job_monitor"] = monitor
    result["submission_seconds"] = time.monotonic() - started
    persist()
    previous = None
    while True:
        state = document("GET", monitor)
        status = state.get("status")
        if status != previous:
            emit("cams_job", mode=result["mode"], status=status,
                 elapsed_seconds=round(time.monotonic() - started, 2))
            previous = status
        result["job_status"] = status
        result["job_wait_seconds"] = time.monotonic() - started
        persist()
        if status == "successful":
            break
        if status not in ("accepted", "running"):
            raise ValueError("ADS job failed")
        if time.monotonic() - started > 1100:
            result["job_retained_for_resume"] = True
            raise TimeoutError("ADS job wait exceeded benchmark bound")
        time.sleep(5)
    try:
        result_href = _link(state, "results")
    except Exception:
        result_href = monitor.rstrip("/") + "/results"
    result_url = _api_link(monitor, result_href)
    asset = document("GET", result_url)["asset"]["value"]
    url = _safe_https(urljoin(result_url, asset["href"]))
    expected = int(asset["file:size"])
    if not 20 <= expected <= 2 * 1024**3:
        raise ValueError("unexpected CAMS asset size")
    output = directory / "cams-full.grib"
    started = time.monotonic()
    # Tokens are never sent to the asset host.
    with session.get(url, stream=True, headers={"Accept-Encoding": "identity"}) as response:
        response.raise_for_status()
        with output.open("wb") as handle:
            for chunk in response.iter_content(1024 * 256):
                handle.write(chunk)
    if output.stat().st_size != expected:
        raise ValueError("CAMS length mismatch")
    result["transfer_seconds"] = time.monotonic() - started
    result["downloaded_bytes"] = expected
    result["grib_messages"] = validate(output)
    cycle = datetime.fromisoformat(manifest["cycle"])
    pieces = _split_cams_bundle_by_lead(
        output, directory, cycle.date(), cycle.strftime("%H:%M"), "benchmark",
    )
    if set(pieces) != {int(v) for v in manifest["cams_query"]["leadtime_hour"]}:
        raise ValueError("CAMS missing forecast leads")
    if not all(_cams_optics_file_valid(path) for path in pieces.values()):
        raise ValueError("CAMS profile validation failed")
    with output.open("rb") as handle:
        result["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()


def stage(args):
    manifest = json.loads((args.output / "manifest.json").read_text())
    directory = args.output / args.mode / args.source
    directory.mkdir(parents=True, exist_ok=False)
    result = dict(source=args.source, mode=args.mode, status="running", files=[],
                  started_at=datetime.now(timezone.utc).isoformat())
    result_path = args.output / f"{args.source}-{args.mode}.json"
    persist = lambda: save(result_path, result)
    persist()
    stats = configure_route(args.mode, manifest["proxy"])
    started = time.monotonic()
    try:
        if args.source == "cams":
            cams_download(manifest, directory, result, persist)
        else:
            cycle = datetime.fromisoformat(manifest["cycle"])
            if args.source == "gfs":
                from firesky.gfs import GfsNomadsClient
                client = GfsNomadsClient(cache_dir=directory, region="china",
                                        timeout_seconds=60)
                client.download_attempts = 2
                bbox = tuple(manifest["models"]["gfs"]["bbox"])
                download = lambda hour: client.download(
                    client.build_subset_url(cycle, hour, bbox), cycle, hour, bbox,
                )
            else:
                from firesky.ecmwf_ifs import EcmwfIfsAdapter
                client = EcmwfIfsAdapter(cache_dir=directory, source=manifest["ec_source"])
                download = lambda hour: client.download(cycle, hour)
            for hour in manifest["models"][args.source]["hours"]:
                before = time.monotonic()
                path = download(hour)
                downloaded = time.monotonic() - before
                before = time.monotonic()
                messages = validate(path)
                item = dict(hour=hour, bytes=path.stat().st_size,
                            download_seconds=downloaded, decode_seconds=time.monotonic() - before,
                            messages=messages, path=str(path))
                result["files"].append(item)
                persist()
                emit("file_complete", source=args.source, mode=args.mode, **item)
        result["status"] = "complete"
    except Exception as exc:
        # SDK exceptions can embed API keys, signed URLs or proxy credentials.
        result["status"] = "failed"
        result["error_type"] = type(exc).__name__
        result["http_status"] = getattr(getattr(exc, "response", None), "status_code", None)
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        result["http_stats"] = stats
        if args.source != "cams":
            result["downloaded_bytes"] = sum(item["bytes"] for item in result["files"])
            result["transfer_and_download_seconds"] = sum(
                item["download_seconds"] for item in result["files"]
            )
        persist()
        emit("stage_finished", **result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", default="2026-11-11")
    parser.add_argument("--cycle", default="2026-11-10T00:00:00+00:00")
    parser.add_argument("--proxy", default="http://127.0.0.1:7897")
    parser.add_argument("--ec-source", default="ecmwf", choices=("ecmwf", "aws", "google"))
    parser.add_argument("--source", choices=("gfs", "ec", "cams"))
    parser.add_argument("--mode", choices=("direct", "proxy"))
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.source:
        stage(args)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = plan(args)
    save(args.output / "manifest.json", manifest)
    emit("plan_ready", output=str(args.output), manifest=manifest)
    # Sequential runs avoid local bandwidth contention between comparison arms.
    for source in ("gfs", "ec", "cams"):
        for mode in ("direct", "proxy"):
            emit("stage_started", source=source, mode=mode)
            with (args.output / f"{source}-{mode}.log").open("w") as log:
                try:
                    subprocess.run([
                        sys.executable, str(Path(__file__).resolve()), "--output", str(args.output),
                        "--source", source, "--mode", mode,
                    ], stdout=log, stderr=log, timeout=1500, check=True)
                except subprocess.TimeoutExpired:
                    path = args.output / f"{source}-{mode}.json"
                    value = json.loads(path.read_text()) if path.exists() else {}
                    value.update(status="timeout", deadline_seconds=1500)
                    save(path, value)
                except subprocess.CalledProcessError:
                    emit("stage_process_failed", source=source, mode=mode)
            path = args.output / f"{source}-{mode}.json"
            emit("stage_result", result=json.loads(path.read_text()) if path.exists() else {})
    emit("benchmark_finished", output=str(args.output))


if __name__ == "__main__":
    main()
