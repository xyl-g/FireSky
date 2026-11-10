"""UI-independent desktop data planning, shared by Qt, SwiftUI and WinUI."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _python_inventory(config=None) -> list[dict]:
    from firesky.web_server import (
        WebServerConfig, _scan_gfs_local, _scan_ecmwf_local, _scan_cams_local,
    )

    config = config or WebServerConfig()
    result = []
    for source, root, scan in (
        ("gfs", config.gfs_cache_dir, _scan_gfs_local),
        ("ecmwf", config.ecmwf_cache_dir, _scan_ecmwf_local),
        ("cams", config.cams_cache_dir, _scan_cams_local),
    ):
        local = scan(root)
        raw_files = [
            path for pattern in ("*.grib", "*.grib2")
            for path in Path(root).rglob(pattern)
        ]
        total_bytes = sum(path.stat().st_size for path in raw_files)
        indexed_count = sum(item["files"] for item in local["cycles"].values())
        area_bounds = {}
        if source == "cams":
            from firesky.aerosol import cams_file_area
            from firesky.web_server import _CAMS_RE
            for path in raw_files:
                match = _CAMS_RE.match(path.name)
                if match and match.group(6) not in area_bounds:
                    try:
                        area_bounds[match.group(6)] = cams_file_area(path)
                    except (OSError, RuntimeError, ValueError):
                        area_bounds[match.group(6)] = None
        result.append({
            "source": source, "path": str(Path(root).resolve()),
            "total_file_count": len(raw_files),
            "total_storage_mb": total_bytes / (1024 * 1024),
            "unindexed_file_count": max(0, len(raw_files) - indexed_count),
            "area_bounds": area_bounds,
            **local,
        })
    return result


def inventory(config=None) -> list[dict]:
    """Read the desktop cache inventory through the selected backend."""
    from firesky.rust_desktop_data import enabled as rust_enabled

    if rust_enabled():
        from firesky.rust_desktop_data import inventory as rust_inventory

        return rust_inventory(config)
    return _python_inventory(config)


def _python_model_context(payload: dict, *, regional: bool = False, config=None, fast=False) -> dict:
    """Resolve the same spatial footprint as execution, without downloading."""
    import firesky.web_server as engine
    from firesky.ecmwf_point import (
        _ecmwf_point_domain_geometry, ecmwf_point_evaluation_times,
        required_ecmwf_forecast_hours,
    )
    from firesky.geometry import bounding_box
    from firesky.service import evaluation_times_utc, required_model_hours
    import numpy as np

    config = config or engine.WebServerConfig()
    payload = dict(payload)
    model = str(payload.get("model_source") or "gfs")
    ec_point = model == "ec" and not regional
    if regional:
        payload["spacing_deg"] = 0.25
        points = engine.national_points_from_payload(payload)
        cycle = engine._national_model_cycle(payload, points)
        compute_region, bbox, grid_region = engine._national_compute_domain(payload, points)
        radius = engine._analysis_radius_from_payload(payload)
    else:
        payload = engine._point_cache_identity_payload(payload)
        request = engine.forecast_request_from_payload(payload)
        radius = request.radius_km
        points = [{
            "name": request.name, "lat": request.point.lat, "lon": request.point.lon,
            "timezone_name": request.timezone_name, "elevation_m": request.point.elevation_m,
            "_elevation_source": (
                "model-grid" if payload.get("_point_grid_mode") else "observer"
            ),
        }]
        compute_region = None
        grid_region = str(payload.get("_grid_region") or "custom")
        if ec_point:
            cycle = (
                engine._point_model_cycle(payload) if fast
                else engine._ecmwf_point_cycle(request, payload, cache_dir=config.ecmwf_cache_dir)
            )
            bbox = bounding_box(request.point, _ecmwf_point_domain_geometry(radius)[2])
        else:
            cycle = engine._point_model_cycle(payload)
            bbox = engine.national_gfs_bbox(
                points, light_path_padding_km=engine._national_gfs_source_radius_km(radius)
            )

    ecmwf_source = str(payload.get("ecmwf_source") or "ecmwf").strip().lower()
    if ecmwf_source not in {"ecmwf", "aws", "google"}:
        raise ValueError("ecmwf_source 必须是 ecmwf、aws 或 google。")
    common = {
        "target_date": payload["target_date"],
        "phenomenon": payload["phenomenon"],
        "model_source": model, "forecast_mode": "model", "satellite_source": "off",
        "radius_km": radius, "compute_backend": "auto", "gfs_resolution": "0p25",
        "ecmwf_source": ecmwf_source,
        "workers": payload.get("workers", 4),
        "_compute_region": compute_region, "_grid_region": grid_region,
        "_model_fixed_bbox": bbox, "_gfs_fixed_bbox": bbox,
        "_model_cycle_override": cycle.isoformat(),
        f"_{model}_cycle_override": cycle.isoformat(),
    }
    terrain_pending = any(p.get("_elevation_source") == "model-grid" for p in points)
    if ec_point:
        times = list(ecmwf_point_evaluation_times(request))
        area_bbox = bbox
    elif model == "ec" or len(points) >= 64:
        _, _, _, valid, epochs, _ = engine.prepare_solar_event_timeline(
            points, date.fromisoformat(payload["target_date"]), payload["phenomenon"]
        )
        times = [
            datetime.fromtimestamp(float(value), timezone.utc)
            for value in np.asarray(epochs)[np.asarray(valid)]
        ]
        area_bbox = (
            engine._external_prepared_bbox(model, bbox, radius) if model == "ec" else bbox
        )
    else:
        times = []
        for point in points:
            req = engine.forecast_request_from_payload({**common, **point})
            anchor = engine.solar_event_time_local(
                req.target_date, req.point, req.timezone_name, req.phenomenon
            )
            times.extend(evaluation_times_utc(req, anchor))
        area_bbox = bbox
    if not times:
        raise ValueError("该区域在所选日期没有可计算的朝霞/晚霞事件。")
    if terrain_pending and not fast:
        try:
            initial_hours = sorted(
                required_ecmwf_forecast_hours(cycle, times) if model == "ec"
                else required_model_hours(model, cycle, times)
            )
            terrain_grid = None
            if model == "gfs":
                client = engine.GfsNomadsClient(
                    cache_dir=config.gfs_cache_dir, resolution="0p25",
                    fixed_bbox=bbox, region=compute_region,
                )
                for hour in initial_hours:
                    try:
                        terrain_grid, _ = client.load_decoded_subset(
                            cycle, hour, bbox, cache_only=True
                        )
                        break
                    except (OSError, RuntimeError, ValueError):
                        continue
            else:
                from firesky.ecmwf_ifs import EcmwfIfsAdapter, decode_ecmwf_ifs_basic_tensor
                for hour in initial_hours:
                    path = EcmwfIfsAdapter.cache_path(config.ecmwf_cache_dir, cycle, hour)
                    if path.is_file() and path.stat().st_size:
                        tensor = decode_ecmwf_ifs_basic_tensor(
                            path, bbox=area_bbox, expected_cycle_utc=cycle,
                            expected_forecast_hour=hour,
                        )
                        terrain_grid, _, _, _ = engine._tensor_to_native_grid(tensor)
                        break
            if terrain_grid is not None:
                engine.apply_decoded_model_surface_elevations(terrain_grid, points)
                if model == "ec" or len(points) >= 64:
                    _, _, _, valid, epochs, _ = engine.prepare_solar_event_timeline(
                        points, date.fromisoformat(payload["target_date"]), payload["phenomenon"]
                    )
                    times = [
                        datetime.fromtimestamp(float(v), timezone.utc)
                        for v in np.asarray(epochs)[np.asarray(valid)]
                    ]
                else:
                    times = []
                    for point in points:
                        req = engine.forecast_request_from_payload({**common, **point})
                        anchor = engine.solar_event_time_local(
                            req.target_date, req.point, req.timezone_name, req.phenomenon
                        )
                        times.extend(evaluation_times_utc(req, anchor))
                terrain_pending = False
        except (OSError, RuntimeError, ValueError):
            # A malformed local terrain file is not a reason to claim readiness.
            # Keep the conservative plan; execution validates the actual bytes.
            pass
    # Model terrain is only known after decoding. A generous preflight envelope
    # avoids presenting sea-level event brackets as a final terrain-aware plan.
    check_times = set(times)
    if terrain_pending:
        check_times.update(t + timedelta(hours=offset) for t in times for offset in (-2, 2))
    check_times = sorted(t for t in check_times if t >= cycle)
    if not check_times:
        raise ValueError("所选模式周期晚于事件窗口，请调整日期或周期。")
    hours = (
        required_ecmwf_forecast_hours(cycle, check_times)
        if model == "ec" else required_model_hours(model, cycle, check_times)
    )
    return {
        "points": points, "common": common, "cycle": cycle,
        "bbox": tuple(bbox), "area": engine.national_cams_area(area_bbox),
        "hours": sorted(hours), "times": sorted(check_times),
        "ec_point": ec_point, "terrain_pending": terrain_pending,
    }


def model_context(payload: dict, *, regional: bool = False, config=None, fast=False) -> dict:
    """Resolve a desktop context through the selected planning backend."""
    from firesky.rust_desktop_data import enabled as rust_enabled, model_context as rust_context
    if rust_enabled():
        return rust_context(payload, regional=regional, config=config, fast=fast)
    return _python_model_context(payload, regional=regional, config=config, fast=fast)


def published_cycles(sources: list[str]) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    from firesky import cycle_availability as cycles

    now = datetime.now(timezone.utc)
    queries = {
        "gfs": cycles.query_latest_gfs_cycle,
        "ecmwf": cycles.query_latest_ecmwf_cycle,
        "cams": cycles.query_latest_cams_cycle,
    }
    def query(source):
        try:
            cycle = queries[source](now, timeout=2)
            verified = cycles.get_cycle_source(source) == "provider"
            return source, {
                "cycle": cycle.isoformat(), "verified": verified,
                "label": cycle.strftime("%Y-%m-%d %HZ"),
            }
        except Exception as exc:
            return source, {"cycle": "", "label": "未知", "verified": False,
                            "error": str(exc)}
    if not sources:
        return {}
    with ThreadPoolExecutor(max_workers=min(3, len(sources))) as executor:
        return dict(executor.map(query, dict.fromkeys(sources)))


def grouped_datasets(datasets: list[dict]) -> list[dict]:
    """Present each physical source once, retaining both models' requirements."""
    groups = {}
    for row in datasets:
        groups.setdefault(row["source"], []).append(row)
    result = []
    for source in ("gfs", "ecmwf", "cams"):
        parts = groups.get(source, [])
        if not parts:
            continue
        combined = dict(parts[0])
        combined.pop("for_model", None)
        combined["ready"] = all(row["ready"] for row in parts)
        combined["verified"] = all(row.get("verified") for row in parts)
        combined["is_latest"] = all(row.get("is_latest") for row in parts)
        combined["request_cycle"] = " / ".join(sorted({
            cycle for row in parts for cycle in row["request_cycle"].split(" / ")
        }))
        keys = {key for row in parts for key in row.get("missing_keys", [])}
        combined["missing_count"] = (
            len(keys) if keys else max(row.get("missing_count", 0) for row in parts)
        )
        combined["missing_hours"] = sorted({hour for row in parts for hour in row.get("missing_hours", [])})
        combined["missing_valid_times"] = sorted({t for row in parts for t in row.get("missing_valid_times", [])})
        combined["requirements"] = parts
        result.append(combined)
    return result


def apply_publications(report: dict, publications: dict) -> dict:
    import copy
    report = copy.deepcopy(report)
    report["publication_pending"] = False
    for row in report["datasets"]:
        publication = publications.get(row["source"])
        if publication is None:
            continue
        row.update(
            latest_available_cycle=publication.get("label", "未核验"),
            latest_available_cycle_utc=publication.get("cycle"),
            verified=bool(publication.get("verified")),
            is_latest=bool(
                publication.get("verified") and row["ready"]
                and set(row["request_cycle"].split(" / ")) == {publication.get("label")}
            ),
        )
    cams = publications.get("cams", {})
    if cams.get("verified"):
        report["cams_update_cycle"] = cams.get("cycle")
    return report


def brief_status(report: dict) -> str:
    missing = [
        {"ecmwf": "EC"}.get(row["source"], row["source"].upper())
        for row in grouped_datasets(report.get("datasets", [])) if not row["ready"]
    ]
    if report.get("satellite_frames", 2) < 2:
        missing.append("卫星帧")
    return "缺少 " + "、".join(missing) if missing else "本次数据已下载"


def check_model_data(
    payload: dict, *, regional=False, probe=True, config=None, refresh=False,
    fast=False, _session=None,
) -> dict:
    from firesky import rust_desktop_data
    if rust_desktop_data.enabled():
        return rust_desktop_data.check_model_data(
            payload, regional=regional, probe=probe, config=config, refresh=refresh,
            fast=fast, session=_session,
        )
    session = _session if _session is not None else {}
    if payload.get("model_source") == "both":
        if probe and "publications" not in session:
            session["publications"] = published_cycles(["gfs", "ecmwf", "cams"])
        reports = {
            model: check_model_data(
                {**payload, "model_source": model},
                regional=regional, probe=probe, config=config, refresh=refresh,
                fast=fast, _session=session,
            )
            for model in ("gfs", "ec")
        }
        return {
            "ready": all(report["ready"] for report in reports.values()),
            "datasets": grouped_datasets([
                {**row, "for_model": model}
                for model, report in reports.items() for row in report["datasets"]
            ]),
            "model_cycles": {model: report["model_cycle"] for model, report in reports.items()},
            "terrain_pending": any(report["terrain_pending"] for report in reports.values()),
            "cams_update_cycle": reports["gfs"].get("cams_update_cycle"),
            "notes": [note for report in reports.values() for note in report.get("notes", [])],
            "model_windows": {model: report["event_start"] for model, report in reports.items()},
        }
    from firesky import web_server as engine
    from firesky.aerosol import cams_profile_bracket_times, find_cached_cams_bracket

    config = config or engine.WebServerConfig()
    context = model_context(payload, regional=regional, config=config, fast=fast)
    source = "ecmwf" if context["common"]["model_source"] == "ec" else "gfs"
    if probe and "publications" not in session:
        session["publications"] = published_cycles([source, "cams"])
    latest = session.get("publications", {})
    context, notes = _refresh_model_context(
        payload, context, latest.get(source, {}), regional=regional, config=config,
        refresh=refresh,
    )
    def scan(key):
        if key not in session:
            session[key] = {
                "gfs": engine._scan_gfs_local,
                "ecmwf": engine._scan_ecmwf_local,
                "cams": engine._scan_cams_local,
            }[key](getattr(config, key + "_cache_dir"))
        return session[key]
    local = scan(source)
    cycle = context["cycle"]
    cycle_label = cycle.strftime("%Y-%m-%d %HZ")
    matched = next((c for c in local["cycles"].values() if c["label"] == cycle_label), None)
    client = engine.GfsNomadsClient(
        cache_dir=config.gfs_cache_dir, resolution="0p25",
        fixed_bbox=context["bbox"], region=context["common"]["_compute_region"],
    ) if source == "gfs" else None
    missing = []
    for hour in context["hours"]:
        if source == "gfs":
            covered = client.find_covering_cache(
                cycle, hour, context["bbox"],
                url=client.build_subset_url(cycle, hour, context["bbox"]),
            ) is not None
        else:
            covered = matched is not None and hour in matched["hours"]
        if not covered:
            missing.append(hour)

    cams = scan("cams")
    if "cams_paths" not in session:
        from firesky.aerosol import CAMS_OPTICS_CACHE_PREFIX
        session["cams_paths"] = list(config.cams_cache_dir.rglob(f"{CAMS_OPTICS_CACHE_PREFIX}-*.grib"))
    target_times = {
        bracket for target in context["times"] for bracket in cams_profile_bracket_times(target)
    }
    brackets = [
        find_cached_cams_bracket(
            config.cams_cache_dir, context["area"], target,
            candidate_paths=session["cams_paths"], validate_contents=not fast,
        )
        for target in sorted(target_times)
    ]
    missing_cams = [target for target, bracket in zip(sorted(target_times), brackets) if bracket is None]
    cams_cycles = {
        selection.run_time_utc.strftime("%Y-%m-%d %HZ")
        for bracket in brackets if bracket is not None
        for selection in (bracket.left, bracket.right)
    }
    datasets = []
    for key, scan, missing_items, requested in (
        (source, local, missing, cycle_label),
        ("cams", cams, missing_cams, " / ".join(sorted(cams_cycles)) or "无可用事件数据"),
    ):
        publication = latest.get(key, {})
        newest = scan["latest_cycle"]
        datasets.append({
            "source": key, "local_files": scan["file_count"],
            "request_cycle": requested, "latest_local_cycle": newest,
            "latest_available_cycle": publication.get("label", "未核验"),
            "latest_available_cycle_utc": publication.get("cycle"),
            "verified": publication.get("verified", False),
            "is_latest": bool(
                publication.get("verified") and not missing_items
                and (
                    cams_cycles == {publication["label"]} if key == "cams"
                    else requested == publication["label"]
                )
            ),
            "missing_count": len(missing_items),
            "missing_hours": missing_items if key != "cams" else [],
            "missing_valid_times": [target.isoformat() for target in missing_cams] if key == "cams" else [],
            "missing_keys": [
                f"{tuple(context['area'])}:{target.isoformat()}" for target in missing_cams
            ] if key == "cams" else [],
            "ready": not missing_items,
        })
    return {
        "ready": all(item["ready"] for item in datasets),
        "datasets": datasets, "checked_at": datetime.now(timezone.utc).isoformat(),
        "point_count": len(context["points"]),
        "terrain_pending": context["terrain_pending"],
        "bbox": list(context["bbox"]),
        "model_cycle": cycle.isoformat(),
        "model_source": context["common"]["model_source"],
        "cams_update_cycle": (
            latest["cams"].get("cycle") if latest.get("cams", {}).get("verified") else None
        ),
        "notes": notes,
        "event_start": min(context["times"]).isoformat(),
    }


def _refresh_model_context(payload, context, publication, *, regional, config, refresh):
    notes = []
    source = "ecmwf" if context["common"]["model_source"] == "ec" else "gfs"
    if refresh and publication.get("verified") and publication.get("cycle"):
        candidate = datetime.fromisoformat(publication["cycle"])
        if context["cycle"] < candidate <= min(context["times"]):
            model = context["common"]["model_source"]
            updated = model_context(
                {**payload, f"_{model}_cycle_override": candidate.isoformat()},
                regional=regional, config=config,
            )
            available = True
            if model == "ec":
                from firesky.ecmwf_point import ecmwf_artifact_available
                available = ecmwf_artifact_available(
                    "ecmwf", candidate, max(updated["hours"]), timeout_seconds=8,
                )
            if available:
                context = updated
            else:
                notes.append("EC 最新周期尚未发布本次所需的全部时效，保留适用的较早周期。")
        elif context["cycle"] < candidate:
            notes.append(f"{source.upper()} 最新周期晚于所需事件窗口，保留历史适用周期。")
    return context, notes


def prepare_model_data(payload: dict, *, regional=False, progress=None, config=None) -> dict:
    from firesky import rust_desktop_data
    if rust_desktop_data.enabled():
        return rust_desktop_data.prepare_model_data(
            payload, regional=regional, config=config, progress=progress,
        )
    if payload.get("model_source") == "both":
        results, errors = {}, {}
        for index, model in enumerate(("gfs", "ec")):
            def model_progress(event, index=index, model=model):
                if progress:
                    progress({
                        **event, "percent": index * 50 + int(event.get("percent", 0)) // 2,
                        "message": f"{model.upper()} · {event.get('message', '准备数据')}",
                    })
            # A cancelled operation must not be mistaken for a source failure.
            model_progress({"percent": 0})
            try:
                results[model] = prepare_model_data(
                    {**payload, "model_source": model}, regional=regional,
                    progress=model_progress, config=config,
                )
            except Exception as exc:
                from firesky.web_server import ForecastCancelled
                if isinstance(exc, ForecastCancelled):
                    raise
                errors[model] = str(exc)
        if errors:
            raise RuntimeError(
                "数据准备未全部完成：" +
                "；".join(f"{model.upper()}：{error}" for model, error in errors.items())
            )
        return {"status": "ready", "models": results}
    from firesky import web_server as engine
    from firesky.aerosol import CamsAdsAerosolProvider, cams_profile_bracket_times
    from firesky.ecmwf_ifs import EcmwfIfsAdapter

    config = config or engine.WebServerConfig()
    context = model_context(payload, regional=regional, config=config)
    if progress:
        progress({"stage": "prepare", "percent": 1, "message": "准备本次事件窗口与完整空间范围"})
    if not context["ec_point"]:
        engine._prefetch_national_inputs(
            context["points"], context["common"], config, progress
        )
    else:
        adapter = EcmwfIfsAdapter(
            cache_dir=config.ecmwf_cache_dir,
            source=str(context["common"].get("ecmwf_source") or "ecmwf"),
        )
        for index, hour in enumerate(context["hours"]):
            if progress:
                progress({"percent": int(60 * index / max(1, len(context["hours"]))),
                          "message": f"准备 EC f{hour:03d}"})
            adapter.download(context["cycle"], hour)
        provider = CamsAdsAerosolProvider(
            cache_dir=config.cams_cache_dir, strict=True,
            fixed_area=context["area"], auto_download=True,
        )
        targets = sorted({
            t for sample in context["times"] for t in cams_profile_bracket_times(sample)
        })
        covered = provider.download_targets_batch(
            context["area"], targets, require_exact_valid_time=True
        )
        for target in targets:
            if progress:
                progress({"percent": 75, "message": f"准备 CAMS {target.isoformat()}"})
            if target not in covered:
                provider.download_target(context["area"], target, require_exact_valid_time=True)
    if payload.get("_cams_refresh_cycle"):
        _refresh_cams_inputs(payload, context, config, progress)
    return {"status": "ready", "cycle": context["cycle"].isoformat()}


def _refresh_cams_inputs(payload, context, config, progress):
    from firesky.aerosol import CamsAdsAerosolProvider, cams_profile_bracket_times

    cycle = datetime.fromisoformat(payload["_cams_refresh_cycle"])
    targets = sorted({t for sample in context["times"] for t in cams_profile_bracket_times(sample)})
    provider = CamsAdsAerosolProvider(
        cache_dir=config.cams_cache_dir, strict=True,
        fixed_area=context["area"], auto_download=True,
    )
    if progress:
        progress({"message": "更新事件窗口内适用的 CAMS 发布周期…"})
    covered = provider.download_targets_batch(
        context["area"], targets, require_exact_valid_time=True, refresh_before_cycle=cycle,
    )
    for target in targets:
        if progress:
            progress({"message": f"校验 CAMS 更新 {target.isoformat()}"})
        if target not in covered:
            provider.download_target(
                context["area"], target, require_exact_valid_time=True, refresh_before_cycle=cycle,
            )


def status_text(report: dict) -> str:
    lines = []
    if "satellite_frames" in report:
        lines.append(
            f"HSD：本地点已缓存 {report['satellite_frames']}/2 帧；"
            f"参考 UTC {report['satellite_reference']}；"
            f"扫描 UTC {', '.join(report['satellite_valid_times']) or '无'}"
        )
    for item in report.get("datasets", []):
        source = {"ecmwf": "EC"}.get(item["source"], item["source"].upper())
        readiness = "覆盖预检窗口" if item["ready"] else f"缺 {item['missing_count']} 个时次/范围"
        latest = (
            ("本次使用最新发布周期" if item["is_latest"] else
             f"最新发布 {item['latest_available_cycle']}")
            if item["verified"] else "最新发布未核验（离线或上游不可达）"
        )
        for_model = " / " + item["for_model"].upper() if item.get("for_model") and item["source"] == "cams" else ""
        lines.append(
            f"{source}{for_model}："
            f"{readiness}；本次 {item['request_cycle']}；"
            f"本地最新 {item['latest_local_cycle'] or '无'}；{latest}"
        )
    if report.get("terrain_pending"):
        lines.append("模式地形尚待解码：预检含时间余量，最终时效与文件内容由引擎校验。")
    lines.extend(report.get("notes", []))
    return "\n".join(lines)


def _python_nowcast_context(payload: dict, config=None, *, fast=False) -> dict:
    import firesky.web_server as engine
    from firesky.gfs import LIGHTPATH_FETCH_RADIUS_KM, canonical_subset_bbox
    from firesky.geometry import bounding_box
    from firesky.service import resolve_reference_time, satellite_lookup_reference_time

    config = config or engine.WebServerConfig()
    request = engine.forecast_request_from_payload(engine.nowcast_payload(payload))
    anchor = engine.solar_event_time_local(
        request.target_date, request.point, request.timezone_name, request.phenomenon
    ).astimezone(timezone.utc)
    reference = resolve_reference_time(request, anchor)
    if reference <= anchor and (anchor - reference).total_seconds() > 4 * 3600:
        raise ValueError("所选事件距当前超过 4 小时，请使用单点模式预测；实况预热不会生成未来卫星帧。")
    lookup = satellite_lookup_reference_time(reference, anchor)
    if fast and request.gfs_cycle_override is None:
        cycle = engine.choose_gfs_cycle(min(anchor, reference))
    else:
        cycle = engine.resolve_nowcast_gfs_cycle(request, anchor, reference_utc=reference)
    radius = float(request.radius_km)
    bbox = canonical_subset_bbox(
        bounding_box(request.point, max(radius, LIGHTPATH_FETCH_RADIUS_KM) + max(35, 2 * .25 * 111.2)),
        .25,
    )
    times = [anchor + timedelta(minutes=5 * step) for step in range(-9, 10)]
    region = engine.region_for_point(request.point)
    area = engine.national_cams_area(
        engine.REGION_BBOXES[region] if region else engine.EAST_ASIA_OPERATIONAL_BBOX
    )
    return dict(request=request, anchor=anchor, reference=reference, lookup=lookup, cycle=cycle,
                bbox=bbox, times=times, region=region, area=area, config=config)


def nowcast_context(payload: dict, config=None, *, fast=False) -> dict:
    """Resolve nowcast geometry and time state through the selected backend."""
    from firesky.rust_desktop_data import enabled as rust_enabled, nowcast_context as rust_context
    if rust_enabled():
        return rust_context(payload, config=config, fast=fast)
    return _python_nowcast_context(payload, config=config, fast=fast)


def check_nowcast_data(payload: dict, *, config=None, probe=True, fast=False) -> dict:
    from firesky import rust_desktop_data
    if rust_desktop_data.enabled():
        return rust_desktop_data.check_nowcast_data(
            payload, config=config, probe=probe, fast=fast,
        )
    import math
    from firesky import web_server as engine
    from firesky.aerosol import cams_profile_bracket_times, find_cached_cams_bracket
    from firesky.satellite_cloud_column import AutoHsdCompactCacheProvider

    ctx = nowcast_context(payload, config, fast=fast)
    config = ctx["config"]
    request = ctx["request"]
    frames = AutoHsdCompactCacheProvider(
        config.satellite_cache_dir, allow_foreground=False
    ).fetch_cached_series(ctx["lookup"], request.point, radius_km=request.radius_km, count=2)
    needed_hours = set()
    for target in ctx["times"]:
        offset = (target - ctx["cycle"]).total_seconds() / 3600
        if offset < 0:
            continue
        needed_hours.update((math.floor(offset), math.ceil(offset)))
    missing = []
    client = engine.GfsNomadsClient(
        cache_dir=config.gfs_cache_dir, region=ctx["region"],
        feature_cache_enabled=False, shared_decode_enabled=True, resolution="0p25",
    )
    for hour in sorted(needed_hours):
        try:
            if fast:
                if client.find_covering_cache(
                    ctx["cycle"], hour, ctx["bbox"],
                    url=client.build_subset_url(ctx["cycle"], hour, ctx["bbox"]),
                ) is None:
                    missing.append(hour)
            else:
                client.load_decoded_subset(ctx["cycle"], hour, ctx["bbox"], cache_only=True)
        except (OSError, RuntimeError, ValueError):
            missing.append(hour)
    cams = engine._scan_cams_local(config.cams_cache_dir)
    targets = {t for sample in ctx["times"] for t in cams_profile_bracket_times(sample)}
    from firesky.aerosol import CAMS_OPTICS_CACHE_PREFIX
    paths = list(config.cams_cache_dir.rglob(f"{CAMS_OPTICS_CACHE_PREFIX}-*.grib"))
    brackets = [
        find_cached_cams_bracket(
            config.cams_cache_dir, ctx["area"], target,
            candidate_paths=paths, validate_contents=not fast,
        )
        for target in sorted(targets)
    ]
    missing_cams = [target for target, bracket in zip(sorted(targets), brackets) if bracket is None]
    cams_cycles = {
        selection.run_time_utc.strftime("%Y-%m-%d %HZ")
        for bracket in brackets if bracket is not None
        for selection in (bracket.left, bracket.right)
    }
    latest = published_cycles(["gfs", "cams"]) if probe else {}
    gfs = engine._scan_gfs_local(config.gfs_cache_dir)
    datasets = []
    for source, scan, request_cycle, missing_count in (
        ("gfs", gfs, ctx["cycle"].strftime("%Y-%m-%d %HZ"), len(missing)),
        ("cams", cams, " / ".join(sorted(cams_cycles)) or "无可用事件数据", len(missing_cams)),
    ):
        publication = latest.get(source, {})
        datasets.append(dict(
            source=source, local_files=scan["file_count"], request_cycle=request_cycle,
            latest_local_cycle=scan["latest_cycle"],
            latest_available_cycle=publication.get("label", "未核验"),
            latest_available_cycle_utc=publication.get("cycle"),
            verified=publication.get("verified", False),
            is_latest=bool(
                publication.get("verified") and not missing_count
                and (
                    cams_cycles == {publication["label"]} if source == "cams"
                    else request_cycle == publication["label"]
                )
            ),
            ready=not missing_count, missing_count=missing_count,
            missing_hours=missing if source == "gfs" else [],
            missing_valid_times=[target.isoformat() for target in missing_cams] if source == "cams" else [],
        ))
    return {
        "ready": len(frames) >= 2 and all(d["ready"] for d in datasets),
        "datasets": datasets, "satellite_frames": len(frames),
        "satellite_reference": ctx["lookup"].isoformat(),
        "satellite_valid_times": [f.valid_time_utc.isoformat() for f in frames],
        "model_cycle": ctx["cycle"].isoformat(), "model_source": "gfs",
        "reference_time_utc": ctx["reference"].isoformat(),
        "cams_update_cycle": (
            latest["cams"].get("cycle") if latest.get("cams", {}).get("verified") else None
        ),
    }


def _warm_nowcast_gfs_prior_from_rust_plan(
    config, context, valid_times: list[datetime]
) -> dict:
    from firesky.gfs import GfsNomadsClient
    from firesky.rust_desktop_data import nowcast_gfs_plan

    plan = nowcast_gfs_plan(context, valid_times)
    client = GfsNomadsClient(
        cache_dir=config.gfs_cache_dir,
        feature_cache_enabled=False,
        shared_decode_enabled=True,
        resolution="0p25",
        region=context["region"],
    )
    warmed: list[str] = []
    errors: list[str] = []
    for hour in plan["hours"]:
        try:
            client.load_decoded_subset(plan["cycle"], hour, plan["bbox"])
            warmed.append(f"gfs {plan['cycle']:%Y-%m-%dT%HZ} f{hour:03d}")
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    return {
        "warmed": warmed,
        "errors": errors,
        "bbox": [round(float(value), 4) for value in plan["bbox"]],
    }


def prepare_nowcast_data(payload: dict, *, config=None, progress=None) -> dict:
    from firesky.rust_desktop_data import enabled as rust_enabled
    if rust_enabled():
        from firesky.satellite_cloud_column import AutoHsdCompactCacheProvider
        from firesky import rust_desktop_data

        ctx = nowcast_context(payload, config)
        request, config = ctx["request"], ctx["config"]
        if progress:
            progress({"stage": "prepare", "percent": 2,
                      "message": "预热选定坐标的相邻 HSD 扫描帧…"})
        frames = AutoHsdCompactCacheProvider(config.satellite_cache_dir).prepare_series(
            ctx["lookup"], request.point, radius_km=request.radius_km, count=2
        )
        if len(frames) < 2:
            raise RuntimeError(
                f"仅获取到 {len(frames)} 帧；相邻卫星扫描尚不完整。请稍后重试，或检查所选位置是否在卫星覆盖范围。"
            )
        if progress:
            progress({"stage": "prepare", "percent": 35,
                      "message": "HSD 已缓存，Rust 正在准备 GFS 与 CAMS…"})
        native = rust_desktop_data.prepare_nowcast_data(payload, config=config)
        if progress:
            progress({"stage": "prepare", "percent": 100,
                      "message": "Rust 已准备实况模型与 CAMS 数据"})
        return {
            "status": "ready",
            "point": {"lat": request.point.lat, "lon": request.point.lon},
            "valid_times": [frame.valid_time_utc.isoformat() for frame in frames],
            "radius_km": request.radius_km,
            "native": native,
        }

    from firesky import web_server as engine
    from firesky.aerosol import CamsAdsAerosolProvider, cams_profile_bracket_times
    from firesky.satellite_cloud_column import AutoHsdCompactCacheProvider

    ctx = nowcast_context(payload, config)
    request, config = ctx["request"], ctx["config"]
    if progress:
        progress({"percent": 2, "message": "预热选定坐标的相邻 HSD 扫描帧…"})
    frames = AutoHsdCompactCacheProvider(config.satellite_cache_dir).prepare_series(
        ctx["lookup"], request.point, radius_km=request.radius_km, count=2
    )
    if len(frames) < 2:
        raise RuntimeError(
            f"仅获取到 {len(frames)} 帧；相邻卫星扫描尚不完整。请稍后重试，或检查所选位置是否在卫星覆盖范围。"
        )
    if progress:
        progress({"percent": 35, "message": "卫星帧已缓存，预热同周期 GFS 垂直背景…"})
    valid_times = ctx["times"] + [frame.valid_time_utc for frame in frames]
    if rust_enabled():
        prior = _warm_nowcast_gfs_prior_from_rust_plan(config, ctx, valid_times)
    else:
        prior = engine._warm_nowcast_gfs_prior(
            config, request.point, request.radius_km, valid_times,
            cycle_override=ctx["cycle"], region=ctx["region"],
        )
    if prior["errors"]:
        raise RuntimeError("卫星帧已缓存，但 GFS 背景不完整：" + "; ".join(prior["errors"]))
    if progress:
        progress({"percent": 70, "message": "准备实况事件窗口的 CAMS 气溶胶数据…"})
    provider = CamsAdsAerosolProvider(
        cache_dir=config.cams_cache_dir, strict=True, fixed_area=ctx["area"], auto_download=True,
    )
    if rust_enabled() and ctx.get("cams_valid_times"):
        targets = sorted(ctx["cams_valid_times"])
    else:
        targets = sorted({
            t for sample in ctx["times"] for t in cams_profile_bracket_times(sample)
        })
    covered = provider.download_targets_batch(ctx["area"], targets, require_exact_valid_time=True)
    for target in targets:
        if progress:
            progress({"percent": 85, "message": f"校验 CAMS {target.isoformat()}"})
        if target not in covered:
            provider.download_target(ctx["area"], target, require_exact_valid_time=True)
    if payload.get("_cams_refresh_cycle"):
        _refresh_cams_inputs(payload, ctx, config, progress)
    return {
        "status": "ready", "point": {"lat": request.point.lat, "lon": request.point.lon},
        "valid_times": [frame.valid_time_utc.isoformat() for frame in frames],
        "radius_km": request.radius_km,
    }
