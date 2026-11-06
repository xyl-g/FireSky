from __future__ import annotations

import math
import os
import gzip
import json
import shutil
import queue
import re
import sqlite3
import fcntl
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from firesky.ecmwf import choose_ecmwf_cycle
from firesky.ecmwf_ifs import EcmwfIfsAdapter
from firesky.gfs import (
    GFS_OPERATIONAL_PADDING_KM,
    GfsNomadsClient,
    canonical_subset_bbox,
    choose_gfs_cycle,
)
from firesky.models import GeoPoint
from firesky.cams_optics import SPECTRAL_OPTICS_CACHE_PREFIX
from firesky.storage import cycle_directory, dated_directory, derived_cache_dir, storage_phenomenon
from firesky.time_utils import china_today


REGION_BBOXES: dict[str, tuple[float, float, float, float]] = {
    "east-asia": (63.0, 155.0, 60.0, 12.0),
    "southeast-asia": (85.0, 155.0, 35.0, -20.0),
    "south-asia": (55.0, 110.0, 45.0, -5.0),
    "europe": (-25.0, 50.0, 75.0, 25.0),
    "north-america": (-155.0, -40.0, 80.0, 5.0),
    "south-america": (-90.0, -25.0, 20.0, -60.0),
    "oceania": (100.0, 180.0, 5.0, -60.0),
    "africa-middle-east": (-25.0, 65.0, 40.0, -40.0),
    "north-asia": (20.0, 180.0, 82.0, 35.0),
    "china": (73.0, 136.0, 54.0, 18.0),
}

COUNTRY_REGION_MAP: dict[str, tuple[str, ...]] = {
    "CN": ("china",),
    "JP": ("east-asia",),
    "KR": ("east-asia",),
    "IN": ("south-asia",),
    "TH": ("southeast-asia",),
    "VN": ("southeast-asia",),
    "MY": ("southeast-asia",),
    "ID": ("southeast-asia",),
    "PH": ("southeast-asia",),
    "AU": ("oceania",),
    "NZ": ("oceania",),
    "RU": ("north-asia",),
    "DE": ("europe",),
    "GB": ("europe",),
    "FR": ("europe",),
    "IT": ("europe",),
    "ES": ("europe",),
    "US": ("north-america",),
    "CA": ("north-america",),
    "MX": ("north-america",),
    "BR": ("south-america",),
    "AR": ("south-america",),
    "CL": ("south-america",),
    "SA": ("africa-middle-east",),
    "EG": ("africa-middle-east",),
    "ZA": ("africa-middle-east",),
}

# Prunable cache artifacts only. `.sqlite3` (the history/calibration
# database with irreplaceable user labels) and `.lock` (live flock files,
# owned by _remove_orphan_locks' safe flow) are deliberately absent.
CACHE_EXTENSIONS = {
    ".grib",
    ".grib2",
    ".idx",
    ".json",
    ".gz",
}


@dataclass
class CacheTaskResult:
    downloaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "downloaded": self.downloaded,
            "failed": self.failed,
            "removed": self.removed,
        }


def daemon_task_results(tasks, worker, max_workers: int):
    """Yield task results without registering non-daemon interpreter exit hooks."""
    pending: queue.Queue = queue.Queue()
    completed: queue.Queue = queue.Queue()
    for task in tasks:
        pending.put(task)

    def run() -> None:
        while True:
            try:
                task = pending.get_nowait()
            except queue.Empty:
                return
            try:
                completed.put((task, worker(task), None))
            except BaseException as exc:
                completed.put((task, None, exc))
            finally:
                pending.task_done()

    worker_count = min(max(1, int(max_workers)), max(1, len(tasks)))
    for index in range(worker_count):
        threading.Thread(
            target=run,
            name=f"firesky-cache-{index + 1}",
            daemon=True,
        ).start()
    for _ in tasks:
        yield completed.get()


def parse_forecast_hours(value: str) -> list[int]:
    hours: set[int] = set()
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError("forecast hour range end must be >= start")
            hours.update(range(start, end + 1))
        else:
            hours.add(int(item))
    if not hours:
        raise ValueError("at least one forecast hour is required")
    return sorted(hours)


def prefetch_cache(
    model_source: str,
    cycle_utc: datetime,
    forecast_hours: Iterable[int],
    regions: Iterable[str],
    gfs_cache_dir: Path | str = ".cache/gfs",
    ecmwf_cache_dir: Path | str = ".cache/ecmwf",
    ecmwf_source: str = "ecmwf",
    progress_callback=None,
    bboxes: Iterable[tuple[float, float, float, float]] | None = None,
    region: str | None = None,
    include_diagnostic_fields: bool = False,
) -> CacheTaskResult:
    """Download only the requested model hours and GFS spatial subsets.

    *region* (when given) is a geographic tag used purely for storage layout:
    GFS subsets are written under ``.cache/gfs/<date>/<HH>Z/<region>/...``.
    ECMWF is downloaded globally (it cannot be spatially clipped),
    so it ignores *region* and shares a single global copy across requests.
    """
    result = CacheTaskResult()
    if model_source not in {"gfs", "ec"}:
        raise ValueError("model_source 必须是 gfs 或 ec。")
    models = [model_source]
    cycle = cycle_utc.astimezone(timezone.utc)
    hours = sorted({int(hour) for hour in forecast_hours})
    requested_bboxes = list(bboxes or [])
    if not requested_bboxes:
        requested_bboxes = [REGION_BBOXES[name] for name in regions if name in REGION_BBOXES]
    gfs_bboxes: list[tuple[float, float, float, float] | None] = requested_bboxes or [None]
    total = sum(len(hours) * len(gfs_bboxes) if m == "gfs" else len(hours) for m in models)
    completed = 0

    if "gfs" in models:
        gfs = GfsNomadsClient(cache_dir=gfs_cache_dir, region=region)
        tasks = [
            (
                cycle,
                hour,
                bbox,
                f"gfs {cycle:%Y-%m-%d %H:%MZ} f{hour:03d} "
                + ("global" if bbox is None else f"bbox={','.join(f'{v:g}' for v in bbox)}"),
            )
            for bbox in gfs_bboxes
            for hour in hours
        ]
        gfs_workers = min(
            8,
            max(1, int(os.environ.get("FIRESKY_GFS_DOWNLOAD_WORKERS", "4"))),
        )
        def fetch_gfs(task):
            if include_diagnostic_fields:
                return gfs.prefetch_diagnostic_subset(task[0], task[1], task[2])
            return gfs.prefetch_subset(task[0], task[1], task[2])

        for task, path, error in daemon_task_results(tasks, fetch_gfs, gfs_workers):
            label = task[3]
            if error is None:
                result.downloaded.append(f"{label} -> {path}")
            else:
                result.failed.append(f"{label}: {error}")
            completed += 1
            if progress_callback:
                progress_callback(completed, total, label)

    if "ec" in models:
        # Keep scheduled/data-assets prefetch on the same strict basic bundle
        # contract used by both EC point and regional prediction.  The legacy
        # client writes ``ifs-v3`` pressure-only files, which the prediction
        # path must reject because they lack TCC and surface geopotential.
        ecmwf = EcmwfIfsAdapter(cache_dir=ecmwf_cache_dir, source=ecmwf_source)
        ec_tasks = [(cycle, hour, f"ec {cycle:%Y-%m-%d %H:%MZ} f{hour:03d}") for hour in hours]
        def fetch_ecmwf(task):
            return ecmwf.download(task[0], task[1])

        for task, path, error in daemon_task_results(ec_tasks, fetch_ecmwf, 4):
            label = task[2]
            if error is None:
                result.downloaded.append(f"{label} -> {path}")
            else:
                result.failed.append(f"{label}: {error}")
            completed += 1
            if progress_callback:
                progress_callback(completed, total, label)

    return result


def prune_cache(cache_root: Path | str = ".cache", max_age_days: float = 2.0) -> CacheTaskResult:
    root = Path(cache_root)
    result = CacheTaskResult()
    if not root.exists():
        return result
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    cutoff_timestamp = cutoff.timestamp()
    # Per the file-protection policy, deletions are moves to a trash staging
    # area, never direct unlinks.
    trash_root = root / ".trash" / f"pruned-{int(datetime.now(timezone.utc).timestamp())}"
    trash_root.mkdir(parents=True, exist_ok=True)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in CACHE_EXTENSIONS:
            continue
        if ".trash" in path.relative_to(root).parts:
            continue
        try:
            if path.stat().st_mtime > cutoff_timestamp:
                continue
            target = trash_root / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            final_target = target
            counter = 1
            while final_target.exists():
                final_target = target.with_name(f"{target.name}.{counter}")
                counter += 1
            shutil.move(str(path), str(final_target))
            result.removed.append(f"{path} -> {final_target}")
        except OSError as exc:
            result.failed.append(f"{path}: {exc}")
    return result


_LEGACY_DERIVED_DIR_NAMES = {"parsed", ".decoded", ".national-layer-profiles"}
_DERIVED_FILE_VERSION_RE = re.compile(r"-v(\d+)-")


def purge_legacy_derived_caches(
    cache_root: Path | str = ".cache",
    trash_root: Path | str | None = None,
) -> CacheTaskResult:
    """One-shot cleanup of legacy-layout derived caches and old-version files.

    Targets:
      1. Any directory named parsed/.decoded/.national-layer-profiles anywhere
         under {cache_root}/{gfs|ecmwf|cams}/ — these are legacy layout orphans
         now superseded by the {source}/derived/{kind}/ tree.
      2. assessments/ files matching assessment-v{N}-*.pkl.gz where N < current
         version (ASSESSMENT_CACHE_VERSION). Current-version files are kept.
      3. parsed/ files matching features-v{N}-*.pkl.gz where N < current version
         (FEATURE_CACHE_VERSION = 1). Current-version files inside the legacy
         parsed/ dirs are moved along with their parent directory (covered by
         rule 1) — this rule only catches stragglers elsewhere.

    Per the file-protection policy, all deletions are moves to a trash staging
    area (default: {cache_root}/.trash/legacy-derived-{timestamp}/). The user
    can empty it manually once satisfied.
    """
    from firesky.assessment_cache import ASSESSMENT_CACHE_VERSION
    from firesky.feature_cache import FEATURE_CACHE_VERSION

    root = Path(cache_root).resolve()
    result = CacheTaskResult()
    if not root.is_dir():
        return result

    import shutil
    import time as _time
    if trash_root is None:
        trash_root = root / ".trash" / f"legacy-derived-{int(_time.time())}"
    trash_root = Path(trash_root)
    trash_root.mkdir(parents=True, exist_ok=True)

    def _trash(source: Path) -> None:
        if not source.exists():
            return
        target = trash_root / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        # If target already exists, append a counter to avoid collision.
        final_target = target
        counter = 1
        while final_target.exists():
            final_target = target.with_name(f"{target.name}.{counter}")
            counter += 1
        try:
            shutil.move(str(source), str(final_target))
            result.removed.append(f"{source} -> {final_target}")
        except OSError as exc:
            result.failed.append(f"{source}: {exc}")

    # 1. Legacy derived directories under any model cache.
    for source_name in ("gfs", "ecmwf", "cams"):
        source_root = root / source_name
        if not source_root.is_dir():
            continue
        for path in source_root.rglob("*"):
            if not path.is_dir():
                continue
            if path.name in _LEGACY_DERIVED_DIR_NAMES:
                # Skip the new derived/ tree itself (it contains 'parsed'/'decoded'
                # etc. as kind subdirectories — those are NOT legacy).
                if "derived" in path.relative_to(source_root).parts:
                    continue
                _trash(path)

    # 2. Old-version assessment files (current version = ASSESSMENT_CACHE_VERSION).
    assessments_root = root / "gfs" / "assessments"
    if assessments_root.is_dir():
        for path in assessments_root.rglob("assessment-v*-*.pkl.gz"):
            m = _DERIVED_FILE_VERSION_RE.search(path.name)
            if not m:
                continue
            try:
                version = int(m.group(1))
            except ValueError:
                continue
            if version < ASSESSMENT_CACHE_VERSION:
                _trash(path)
                # Also trash the matching lock file if any.
                lock = path.with_suffix(path.suffix + ".lock")
                if lock.exists():
                    _trash(lock)

    # 3. Old-version feature-cache files (current version = FEATURE_CACHE_VERSION).
    for path in root.rglob("features-v*-*.pkl.gz"):
        m = _DERIVED_FILE_VERSION_RE.search(path.name)
        if not m:
            continue
        try:
            version = int(m.group(1))
        except ValueError:
            continue
        if version < FEATURE_CACHE_VERSION:
            _trash(path)

    return result


_EC_CACHE_PATTERN = re.compile(r"^ifs-v\d+-(\d{8})(\d{2})-f\d{3}\.grib2$")
_CAMS_CACHE_PATTERN = re.compile(
    r"^cams-optics-v2-(\d{4}-\d{2}-\d{2})-(\d{2})\d{2}-\d{3}-.+\.grib$"
)
_CAMS_SPECTRAL_CACHE_PATTERN = re.compile(
    rf"^{re.escape(SPECTRAL_OPTICS_CACHE_PREFIX)}-"
    r"(\d{4}-\d{2}-\d{2})-(\d{2})\d{2}-\d{3}-[a-f0-9]+\.grib$"
)


def organize_cache(cache_root: Path | str = ".cache", dry_run: bool = False) -> CacheTaskResult:
    root = Path(cache_root)
    result = CacheTaskResult()
    _organize_gfs(root / "gfs", result, dry_run)
    _organize_named_model_cache(root / "ecmwf", _EC_CACHE_PATTERN, result, dry_run)
    _organize_named_model_cache(root / "cams", _CAMS_CACHE_PATTERN, result, dry_run)
    _organize_named_model_cache(root / "cams", _CAMS_SPECTRAL_CACHE_PATTERN, result, dry_run)
    _organize_json_cache(root / "point-forecasts", "point-forecast-*.json.gz", False, result, dry_run)
    _organize_json_cache(root / "regional-forecasts", "regional-forecast-*.json.gz", True, result, dry_run)
    _organize_map_frames(root / "map-frames", result, dry_run)
    _organize_diagnostics(root / "regional-forecasts" / "diagnostics", root / "regional-forecasts", result, dry_run)
    _remove_stale_temporary_files(root, result, dry_run)
    _remove_orphan_locks(root, result, dry_run)
    return result


def _move_cache_file(source: Path, target: Path, result: CacheTaskResult, dry_run: bool) -> bool:
    if source == target or not source.exists():
        return False
    label = f"{source} -> {target}"
    try:
        if target.exists():
            if source.is_file() and target.is_file() and source.stat().st_size == target.stat().st_size:
                if not dry_run:
                    source.unlink()
                result.downloaded.append(f"duplicate: {label}")
                return True
            result.failed.append(f"目标已存在且内容大小不同: {label}")
            return False
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
        result.downloaded.append(label)
        return True
    except OSError as exc:
        result.failed.append(f"{label}: {exc}")
        return False


def _move_with_sidecars(source: Path, target: Path, result: CacheTaskResult, dry_run: bool) -> None:
    _move_cache_file(source, target, result, dry_run)
    for suffix in (".json", ".lock", ".decode.lock"):
        side_source = Path(str(source) + suffix)
        side_target = Path(str(target) + suffix)
        _move_cache_file(side_source, side_target, result, dry_run)


def _organize_gfs(root: Path, result: CacheTaskResult, dry_run: bool) -> None:
    if not root.is_dir():
        return
    metadata_files = list(root.glob("*.grib2.json"))
    known: set[Path] = set()
    for metadata_path in metadata_files:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            cycle = datetime.fromisoformat(str(metadata["cycle"]).replace("Z", "+00:00"))
            source = root / str(metadata["file"])
            url = str(metadata.get("url") or "")
            resolution_match = re.search(r"(?:pgrb2\.|gfs_)(0p25|0p50)", url)
            resolution = resolution_match.group(1) if resolution_match else "unknown"
            target = cycle_directory(root, cycle, resolution) / source.name
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            result.failed.append(f"无法识别 GFS 元数据 {metadata_path}: {exc}")
            continue
        known.add(source)
        _move_with_sidecars(source, target, result, dry_run)
        _move_gfs_decoded_cache(root, source.stem, target, result, dry_run)
    for decoded in list((root / ".decoded").glob("*")) if (root / ".decoded").is_dir() else ():
        source_stem = decoded.name.split("-", 1)[0]
        matches = list(root.glob(f"*/*/*/{source_stem}.grib2"))
        if len(matches) == 1:
            _move_cache_directory(
                decoded,
                derived_cache_dir(matches[0], "decoded") / decoded.name,
                result,
                dry_run,
            )
    orphan_count = sum(1 for path in root.glob("*.grib2") if path not in known)
    if orphan_count:
        result.failed.append(f"保留 {orphan_count} 个缺少元数据的 GFS 文件，无法可靠判断日期。")


def _move_gfs_decoded_cache(
    root: Path,
    source_stem: str,
    target_grib_path: Path,
    result: CacheTaskResult,
    dry_run: bool,
) -> None:
    """Move orphan .decoded entries (root-level .decoded/) to the derived/decoded/
    tree computed from the grib's new location."""
    decoded_root = root / ".decoded"
    if not decoded_root.is_dir():
        return
    target_decoded_dir = derived_cache_dir(target_grib_path, "decoded")
    for source in decoded_root.glob(f"{source_stem}-*"):
        _move_cache_directory(source, target_decoded_dir / source.name, result, dry_run)


def _move_cache_directory(source: Path, target: Path, result: CacheTaskResult, dry_run: bool) -> None:
    if not source.is_dir() or source == target:
        return
    label = f"{source} -> {target}"
    try:
        if target.exists():
            result.failed.append(f"目录目标已存在，保留源目录: {label}")
            return
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
        result.downloaded.append(label)
    except OSError as exc:
        result.failed.append(f"{label}: {exc}")


def _organize_named_model_cache(
    root: Path,
    pattern: re.Pattern[str],
    result: CacheTaskResult,
    dry_run: bool,
) -> None:
    if not root.is_dir():
        return
    for source in list(root.iterdir()):
        if not source.is_file():
            continue
        match = pattern.fullmatch(source.name)
        if match is None:
            continue
        target_date, cycle_hour = match.groups()
        if len(target_date) == 8:
            target_date = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
        target = dated_directory(root, target_date, f"{cycle_hour}Z") / source.name
        _move_with_sidecars(source, target, result, dry_run)


def _organize_json_cache(
    root: Path,
    pattern: str,
    include_results_dir: bool,
    result: CacheTaskResult,
    dry_run: bool,
) -> None:
    if not root.is_dir():
        return
    for source in list(root.glob(pattern)):
        try:
            with gzip.open(source, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            target_date = payload["target_date"]
            phenomenon = storage_phenomenon(payload.get("phenomenon"))
            parts = (phenomenon, "results") if include_results_dir else (phenomenon,)
            target = dated_directory(root, target_date, *parts) / source.name
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            result.failed.append(f"无法识别结果缓存 {source}: {exc}")
            continue
        _move_with_sidecars(source, target, result, dry_run)


def _organize_map_frames(root: Path, result: CacheTaskResult, dry_run: bool) -> None:
    if not root.is_dir():
        return
    for source in list(root.glob("map-frame-*.json.gz")):
        try:
            with gzip.open(source, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
            target_date = payload["frames"][0]["valid_time_local"]
            target = dated_directory(root, target_date) / source.name
        except (OSError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
            result.failed.append(f"无法识别地图帧 {source}: {exc}")
            continue
        _move_with_sidecars(source, target, result, dry_run)


def _organize_diagnostics(source_root: Path, cache_root: Path, result: CacheTaskResult, dry_run: bool) -> None:
    if not source_root.is_dir():
        return
    for source in list(source_root.glob("regional-diagnostics-*.sqlite3")):
        empty_bundle = False
        try:
            with sqlite3.connect(source) as connection:
                metadata = {
                    str(key): json.loads(value)
                    for key, value in connection.execute("SELECT key, value FROM metadata")
                }
                if not metadata.get("target_date"):
                    row = connection.execute(
                        "SELECT payload FROM point_diagnostics LIMIT 1"
                    ).fetchone()
                    if row is None:
                        empty_bundle = True
                    else:
                        point_payload = json.loads(zlib.decompress(row[0]).decode("utf-8"))
                        metadata["target_date"] = point_payload.get("target_date")
                        metadata["phenomenon"] = point_payload.get("phenomenon")
            if empty_bundle:
                if not dry_run:
                    source.unlink(missing_ok=True)
                result.removed.append(str(source))
                continue
            target = dated_directory(
                cache_root,
                metadata["target_date"],
                storage_phenomenon(metadata.get("phenomenon")),
                "diagnostics",
            ) / source.name
        except (OSError, KeyError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
            result.failed.append(f"无法识别诊断缓存 {source}: {exc}")
            continue
        _move_with_sidecars(source, target, result, dry_run)


def _remove_stale_temporary_files(root: Path, result: CacheTaskResult, dry_run: bool) -> None:
    now = datetime.now(timezone.utc).timestamp()
    for source in root.rglob("*"):
        if not source.is_file() or not re.search(r"(?:\.tmp(?:-|$)|\.part$|\.download$)", source.name):
            continue
        match = re.search(r"\.tmp-(\d+)(?:-|$)", source.name)
        if match and _pid_is_running(int(match.group(1))):
            continue
        if match is None and now - source.stat().st_mtime < 3600:
            continue
        try:
            if not dry_run:
                source.unlink()
            result.removed.append(str(source))
        except OSError as exc:
            result.failed.append(f"{source}: {exc}")


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_orphan_locks(root: Path, result: CacheTaskResult, dry_run: bool) -> None:
    for source in root.rglob("*.lock"):
        data_path = Path(str(source).removesuffix(".lock"))
        if data_path.exists():
            continue
        try:
            with source.open("a+b") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                if not dry_run:
                    source.unlink(missing_ok=True)
            result.removed.append(str(source))
        except OSError as exc:
            result.failed.append(f"{source}: {exc}")


def describe_regions() -> dict[str, tuple[float, float, float, float]]:
    return {name: canonical_subset_bbox(bbox) for name, bbox in REGION_BBOXES.items()}


# ── Country / region registry ─────────────────────────────────────────

@dataclass(frozen=True)
class CountryConfig:
    code: str
    name: str
    bounds: tuple[float, float, float, float]   # (leftlon, rightlon, toplat, bottomlat)
    timezone: str
    regions: tuple[str, ...]
    center_point: tuple[float, float]            # (lat, lon)
    is_builtin: bool = True


_BUILTIN_COUNTRIES: dict[str, CountryConfig] = {
    "CN": CountryConfig(
        code="CN", name="中国",
        bounds=(73.0, 136.0, 54.0, 18.0),
        timezone="Asia/Shanghai",
        regions=("east-asia",),
        center_point=(35.0, 105.0),
    ),
    "JP": CountryConfig(
        code="JP", name="日本",
        bounds=(128.0, 146.0, 46.0, 30.0),
        timezone="Asia/Tokyo",
        regions=("east-asia",),
        center_point=(36.0, 138.0),
    ),
    "KR": CountryConfig(
        code="KR", name="韩国",
        bounds=(125.0, 130.0, 39.0, 33.0),
        timezone="Asia/Seoul",
        regions=("east-asia",),
        center_point=(36.0, 128.0),
    ),
    "IN": CountryConfig(
        code="IN", name="印度",
        bounds=(68.0, 98.0, 36.0, 6.0),
        timezone="Asia/Kolkata",
        regions=("south-asia",),
        center_point=(22.0, 80.0),
    ),
    "TH": CountryConfig(
        code="TH", name="泰国",
        bounds=(97.0, 106.0, 21.0, 5.0),
        timezone="Asia/Bangkok",
        regions=("southeast-asia",),
        center_point=(14.0, 101.0),
    ),
    "AU": CountryConfig(
        code="AU", name="澳大利亚",
        bounds=(112.0, 155.0, -9.0, -45.0),
        timezone="Australia/Sydney",
        regions=("oceania",),
        center_point=(-26.0, 134.0),
    ),
    "RU": CountryConfig(
        code="RU", name="俄罗斯",
        bounds=(27.0, 180.0, 78.0, 41.0),
        timezone="Europe/Moscow",
        regions=("north-asia",),
        center_point=(56.0, 100.0),
    ),
    "DE": CountryConfig(
        code="DE", name="德国",
        bounds=(5.0, 16.0, 55.0, 47.0),
        timezone="Europe/Berlin",
        regions=("europe",),
        center_point=(51.0, 10.0),
    ),
    "GB": CountryConfig(
        code="GB", name="英国",
        bounds=(-8.0, 2.0, 60.0, 49.0),
        timezone="Europe/London",
        regions=("europe",),
        center_point=(54.0, -2.0),
    ),
    "US": CountryConfig(
        code="US", name="美国",
        bounds=(-125.0, -66.0, 50.0, 24.0),
        timezone="America/New_York",
        regions=("north-america",),
        center_point=(39.0, -98.0),
    ),
    "NZ": CountryConfig(
        code="NZ", name="新西兰",
        bounds=(165.0, 179.0, -34.0, -48.0),
        timezone="Pacific/Auckland",
        regions=("oceania",),
        center_point=(-41.0, 174.0),
    ),
}

# Mutable registry: builtins + custom countries loaded from disk.
COUNTRY_REGISTRY: dict[str, CountryConfig] = dict(_BUILTIN_COUNTRIES)

_CUSTOM_COUNTRIES_PATH = Path(".config/countries.json")


def _country_to_dict(c: CountryConfig) -> dict:
    return {
        "code": c.code,
        "name": c.name,
        "bounds": list(c.bounds),
        "timezone": c.timezone,
        "regions": list(c.regions),
        "center_point": list(c.center_point),
        "is_builtin": c.is_builtin,
    }


def _country_from_dict(d: dict) -> CountryConfig:
    return CountryConfig(
        code=d["code"].upper(),
        name=d["name"],
        bounds=tuple(d["bounds"]),
        timezone=d["timezone"],
        regions=tuple(d["regions"]),
        center_point=tuple(d["center_point"]),
        is_builtin=False,
    )


def _load_custom_countries() -> None:
    """Load custom countries from JSON and merge into COUNTRY_REGISTRY."""
    if not _CUSTOM_COUNTRIES_PATH.is_file():
        return
    try:
        import json as _json
        data = _json.loads(_CUSTOM_COUNTRIES_PATH.read_text(encoding="utf-8"))
        for item in data:
            c = _country_from_dict(item)
            COUNTRY_REGISTRY[c.code] = c
    except (OSError, ValueError, KeyError):
        pass


# Load custom areas at import time for dataset resolution.
_load_custom_countries()


def get_country(code: str) -> Optional[CountryConfig]:
    return COUNTRY_REGISTRY.get(code.upper())


def resolve_regions_for_countries(
    country_codes: Iterable[str],
) -> tuple[list[str], tuple[float, float, float, float]]:
    """Return (regions, overall_bbox) covering all selected countries."""
    regions: list[str] = []
    seen: set[str] = set()
    all_boxes: list[tuple[float, float, float, float]] = []
    for code in country_codes:
        country = get_country(code)
        if country is None:
            continue
        all_boxes.append(country.bounds)
        for r in country.regions:
            if r not in seen:
                seen.add(r)
                regions.append(r)
    if not all_boxes:
        return ["east-asia"], REGION_BBOXES["east-asia"]
    overall = (
        min(b[0] for b in all_boxes),
        max(b[1] for b in all_boxes),
        max(b[2] for b in all_boxes),
        min(b[3] for b in all_boxes),
    )
    return regions or ["east-asia"], overall


# ── Smart dataset resolution ──────────────────────────────────────────

@dataclass
class DatasetSpec:
    model_source: str
    forecast_hours: list[int]
    regions: list[str]
    cams_lead_hours: list[int] = field(default_factory=list)
    cams_area: list[float] = field(default_factory=list)
    summary: str = ""
    cycle_utc: Optional[datetime] = None
    bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    cams_areas: list[list[float]] = field(default_factory=list)


def _cams_area_from_bbox(
    bbox: tuple[float, float, float, float],
) -> list[float]:
    """Convert (leftlon, rightlon, toplat, bottomlat) → CAMS [N, W, S, E]."""
    leftlon, rightlon, toplat, bottomlat = bbox
    return [
        min(90.0, toplat + 2.0),
        max(-180.0, leftlon - 2.0),
        max(-90.0, bottomlat - 2.0),
        min(180.0, rightlon + 2.0),
    ]


def _country_configs(
    country_codes: list[str],
    area_overrides: list[dict] | None,
) -> list[CountryConfig]:
    overrides: dict[str, CountryConfig] = {}
    for item in area_overrides or []:
        try:
            code = str(item.get("code") or "CUSTOM").upper()
            raw_bounds = item.get("bounds")
            if not isinstance(raw_bounds, (list, tuple)) or len(raw_bounds) != 4:
                continue
            bounds = tuple(float(value) for value in raw_bounds)
            center = item.get("center_point")
            if not isinstance(center, (list, tuple)) or len(center) != 2:
                center = (
                    (bounds[2] + bounds[3]) / 2.0,
                    (bounds[0] + bounds[1]) / 2.0,
                )
            overrides[code] = CountryConfig(
                code=code,
                name=str(item.get("name") or code),
                bounds=bounds,
                timezone=str(item.get("timezone") or "UTC"),
                regions=tuple(str(value) for value in item.get("regions") or ()),
                center_point=(float(center[0]), float(center[1])),
                is_builtin=False,
            )
        except (TypeError, ValueError):
            continue

    configs: list[CountryConfig] = []
    for code in country_codes:
        normalized = str(code).upper()
        country = overrides.get(normalized) or get_country(normalized)
        if country is not None:
            fixed_regions = COUNTRY_REGION_MAP.get(normalized)
            if fixed_regions is not None and country.regions != fixed_regions:
                country = CountryConfig(
                    code=country.code,
                    name=country.name,
                    bounds=country.bounds,
                    timezone=country.timezone,
                    regions=fixed_regions,
                    center_point=country.center_point,
                    is_builtin=country.is_builtin,
                )
            configs.append(country)
    if not configs:
        fallback = get_country("CN")
        if fallback is not None:
            configs.append(fallback)
    return configs


def _axis_samples(start: float, end: float, spacing: float) -> list[float]:
    values = [start]
    current = math.ceil(start / spacing) * spacing
    while current < end:
        if current > start:
            values.append(current)
        current += spacing
    if end > start:
        values.append(end)
    return sorted(set(round(value, 6) for value in values))


def _coverage_points(
    countries: Sequence[CountryConfig],
    spacing_deg: float,
) -> list[tuple[GeoPoint, str]]:
    points: list[tuple[GeoPoint, str]] = []
    seen: set[tuple[float, float, str]] = set()
    spacing = max(0.5, min(10.0, float(spacing_deg)))
    for country in countries:
        west, east, north, south = country.bounds
        if east < west or north < south:
            continue
        for lat in _axis_samples(south, north, spacing):
            for lon in _axis_samples(west, east, spacing):
                key = (lat, lon, country.timezone)
                if key in seen:
                    continue
                seen.add(key)
                points.append((GeoPoint(lat=lat, lon=lon), country.timezone))
    return points


_SOLAR_SCHEDULE_CACHE_LIMIT = 32
_SOLAR_SCHEDULE_CACHE: OrderedDict[tuple, tuple[tuple[float, ...], tuple[float, ...]]] = OrderedDict()
_SOLAR_SCHEDULE_LOCK = threading.Lock()
# Dataset planning does not yet have the model orography available.  The two
# supported elevation endpoints bound the continuous solar-event time without
# adding a terrain download to the planning path.  Astral treats non-positive
# elevations as sea level, so 0 m also covers the supported -500..0 m range.
_SOLAR_PREFETCH_ELEVATIONS_M = (0.0, 9000.0)


def _hourly_envelope_epochs(lower: float, upper: float) -> tuple[float, ...]:
    """Return envelope endpoints and every UTC-hour boundary between them."""
    start, end = sorted((float(lower), float(upper)))
    first_hour = int(math.ceil(start / 3600.0))
    last_hour = int(math.floor(end / 3600.0))
    return (
        start,
        *(hour * 3600.0 for hour in range(first_hour, last_hour + 1)),
        end,
    )


def _solar_anchor_epochs(
    payloads: Sequence[dict],
    target_date: date,
    phenomenon: str,
) -> list[float | None]:
    """Resolve anchors while isolating polar points without this event."""
    from firesky.national_batch import prepare_solar_event_timeline

    try:
        *_, anchors = prepare_solar_event_timeline(
            payloads,
            target_date,
            phenomenon,
        )
    except ValueError as exc:
        if "Unable to find a" not in str(exc):
            raise
        if len(payloads) == 1:
            return [None]
        middle = len(payloads) // 2
        return [
            *_solar_anchor_epochs(payloads[:middle], target_date, phenomenon),
            *_solar_anchor_epochs(payloads[middle:], target_date, phenomenon),
        ]
    return [float(value) for value in anchors]


def _precomputed_solar_schedule(
    countries: Sequence[CountryConfig],
    points: Sequence[tuple[GeoPoint, str]],
    target_dates: Sequence[str],
    phenomena: Sequence[str],
    spacing_deg: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return cached valid-sample and event-anchor epochs for one coverage plan."""
    key = (
        tuple((country.code, country.bounds, country.timezone) for country in countries),
        tuple(target_dates),
        tuple(phenomena),
        round(float(spacing_deg), 4),
    )
    with _SOLAR_SCHEDULE_LOCK:
        cached = _SOLAR_SCHEDULE_CACHE.get(key)
        if cached is not None:
            _SOLAR_SCHEDULE_CACHE.move_to_end(key)
            return cached

    from firesky.national_batch import national_event_offsets_minutes

    sample_epochs: set[float] = set()
    anchor_epochs: set[float] = set()
    endpoint_anchors: list[list[float | None]] = []
    for elevation_m in _SOLAR_PREFETCH_ELEVATIONS_M:
        payloads = [
            {
                "name": f"schedule-{index}",
                "lat": point.lat,
                "lon": point.lon,
                "elevation_m": elevation_m,
                "timezone_name": timezone_name,
            }
            for index, (point, timezone_name) in enumerate(points)
        ]
        anchors_for_elevation: list[float | None] = []
        for date_text in target_dates:
            target_date = date.fromisoformat(date_text)
            for phenomenon in phenomena:
                anchors = _solar_anchor_epochs(
                    payloads,
                    target_date,
                    phenomenon,
                )
                anchors_for_elevation.extend(anchors)
        endpoint_anchors.append(anchors_for_elevation)

    event_index = 0
    for _date_text in target_dates:
        for phenomenon in phenomena:
            offsets = national_event_offsets_minutes(phenomenon)
            minimum_offset_seconds = float(min(offsets) * 60)
            maximum_offset_seconds = float(max(offsets) * 60)
            for point_index in range(len(points)):
                endpoint_values = [
                    anchors[event_index * len(points) + point_index]
                    for anchors in endpoint_anchors
                ]
                available_anchors = [
                    value for value in endpoint_values if value is not None
                ]
                if not available_anchors:
                    continue
                lower_anchor = min(available_anchors)
                upper_anchor = max(available_anchors)
                anchor_epochs.update(
                    _hourly_envelope_epochs(lower_anchor, upper_anchor)
                )
                sample_epochs.update(
                    _hourly_envelope_epochs(
                        min(lower_anchor, upper_anchor) + minimum_offset_seconds,
                        max(lower_anchor, upper_anchor) + maximum_offset_seconds,
                    )
                )
            event_index += 1

    result = (tuple(sorted(sample_epochs)), tuple(sorted(anchor_epochs)))
    with _SOLAR_SCHEDULE_LOCK:
        _SOLAR_SCHEDULE_CACHE[key] = result
        _SOLAR_SCHEDULE_CACHE.move_to_end(key)
        while len(_SOLAR_SCHEDULE_CACHE) > _SOLAR_SCHEDULE_CACHE_LIMIT:
            _SOLAR_SCHEDULE_CACHE.popitem(last=False)
    return result


def _download_bboxes(countries: Sequence[CountryConfig]) -> tuple[list[str], list[tuple[float, float, float, float]]]:
    """Resolve one reusable spatial package per fixed region."""
    regions: list[str] = []
    bboxes: list[tuple[float, float, float, float]] = []
    for country in countries:
        matched_region = False
        for region in country.regions:
            bbox = REGION_BBOXES.get(region)
            if bbox is None:
                continue
            matched_region = True
            if region not in regions:
                regions.append(region)
            if bbox not in bboxes:
                bboxes.append(bbox)
        if not matched_region:
            west, east, north, south = country.bounds
            lat_pad = GFS_OPERATIONAL_PADDING_KM / 111.0
            mid_lat = (north + south) / 2.0
            lon_scale = max(0.2, math.cos(math.radians(mid_lat)))
            lon_pad = GFS_OPERATIONAL_PADDING_KM / (111.0 * lon_scale)
            region = f"country-{country.code.lower()}"
            bbox = (
                max(-180.0, west - lon_pad),
                min(180.0, east + lon_pad),
                min(90.0, north + lat_pad),
                max(-90.0, south - lat_pad),
            )
            regions.append(region)
            bboxes.append(bbox)

    return regions, bboxes


def resolve_datasets_for_areas(
    area_codes: list[str],
    target_dates: list[str] | None = None,
    phenomena: list[str] | None = None,
    model_source: str = "gfs",
    *,
    cycle_overrides: dict[str, datetime] | None = None,
    area_overrides: list[dict] | None = None,
    spacing_deg: float = 2.0,
) -> list[DatasetSpec]:
    """Compute exact datasets for geographic coverage areas and dates.

    Returns a list of ``DatasetSpec`` objects describing which GFS/EC
    forecast hours and CAMS lead hours to download — only the data
    actually needed for sunrise/sunset prediction.
    """
    phenomena = phenomena or ["dawn", "dusk"]
    if target_dates is None:
        today = china_today()
        target_dates = [(today + timedelta(days=i)).isoformat() for i in range(1, 4)]

    coverage_areas = _country_configs(area_codes, area_overrides)
    regions, bboxes = _download_bboxes(coverage_areas)
    cams_areas = [_cams_area_from_bbox(bbox) for bbox in bboxes]
    points = _coverage_points(coverage_areas, spacing_deg)

    if model_source == "all":
        models = ["gfs", "ec"]
    elif model_source in {"gfs", "ec"}:
        models = [model_source]
    else:
        raise ValueError("model_source 必须是 gfs 或 ec。")

    gfs_hours: set[int] = set()
    ec_hours: set[int] = set()
    cams_leads: set[int] = set()

    now_utc = datetime.now(timezone.utc)
    cycles = cycle_overrides or {}
    gfs_cycle = cycles.get("gfs") or choose_gfs_cycle(now_utc)
    ec_cycle = cycles.get("ec") or cycles.get("ecmwf") or choose_ecmwf_cycle(now_utc)
    cams_cycle = cycles.get("cams")
    if cams_cycle is None:
        published = now_utc - timedelta(hours=10)
        cams_cycle = published.replace(
            hour=(published.hour // 12) * 12,
            minute=0,
            second=0,
            microsecond=0,
        )

    sample_epochs, _anchor_epochs = _precomputed_solar_schedule(
        coverage_areas,
        points,
        target_dates,
        phenomena,
        spacing_deg,
    )

    def add_hours(target: set[int], epochs: Sequence[float], cycle: datetime, cadence) -> None:
        cycle_epoch = cycle.timestamp()
        for epoch in epochs:
            offset = max(0.0, (epoch - cycle_epoch) / 3600.0)
            step = cadence(offset) if callable(cadence) else cadence
            target.add(max(0, int(math.floor(offset / step) * step)))
            target.add(max(0, int(math.ceil(offset / step) * step)))

    if "gfs" in models:
        add_hours(gfs_hours, sample_epochs, gfs_cycle, lambda offset: 3 if offset > 120.0 else 1)
    if "ec" in models:
        add_hours(ec_hours, sample_epochs, ec_cycle, 3)
    if cams_cycle is not None:
        add_hours(cams_leads, sample_epochs, cams_cycle, 3)

    specs: list[DatasetSpec] = []

    if "gfs" in models and gfs_hours:
        specs.append(DatasetSpec(
            model_source="gfs",
            forecast_hours=sorted(gfs_hours),
            regions=regions,
            summary=f"GFS {len(gfs_hours)} 个时效",
            cycle_utc=gfs_cycle,
            bboxes=bboxes,
        ))

    if "ec" in models and ec_hours:
        specs.append(DatasetSpec(
            model_source="ec",
            forecast_hours=sorted(ec_hours),
            regions=regions,
            summary=f"EC {len(ec_hours)} 个时效",
            cycle_utc=ec_cycle,
        ))

    if cams_leads:
        specs.append(DatasetSpec(
            model_source="cams",
            forecast_hours=[],
            regions=regions,
            cams_lead_hours=sorted(cams_leads),
            cams_area=cams_areas[0] if len(cams_areas) == 1 else [],
            summary=f"CAMS {len(cams_leads)} 个 lead hours",
            cycle_utc=cams_cycle,
            bboxes=bboxes,
            cams_areas=cams_areas,
        ))

    return specs
