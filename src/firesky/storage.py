from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


DATE_KEY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def storage_date_key(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    if not DATE_KEY_PATTERN.fullmatch(text):
        raise ValueError(f"invalid storage date: {value!r}")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid storage date: {value!r}") from exc


def storage_phenomenon(value: str | None) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in {"dawn", "dusk"} else "unknown"


def dated_directory(root: str | Path, value: date | datetime | str, *parts: str, region: str | None = None) -> Path:
    """Build ``root/<date>/[region/]<parts...>``.

    When *region* is provided it is inserted immediately after the date segment
    (concretely, after the ``<HH>Z`` cycle hour for cycle directories) so that
    downloaded data can be organised by geographic region without changing the
    relative path semantics used by the derived-cache mirror.
    """
    segs: list = [storage_date_key(value)]
    if parts:
        segs.append(parts[0])
        if region:
            segs.append(region)
        segs.extend(parts[1:])
    elif region:
        segs.append(region)
    return Path(root) / Path(*segs)


def cycle_directory(root: str | Path, cycle_utc: datetime, *parts: str, region: str | None = None) -> Path:
    cycle = cycle_utc.astimezone(timezone.utc)
    return dated_directory(root, cycle, f"{cycle:%H}Z", *parts, region=region)


def derived_cache_dir(grib_path: Path, kind: str) -> Path:
    """Compute the derived-cache directory for a grib file, separate from the
    raw data tree. kind is one of 'parsed' | 'decoded' | 'national-layer-profiles'.

    The grib file is expected to live at:
        {source_root}/{YYYY-MM-DD}/{HH}Z/[resolution/]filename.grib2

    The derived directory mirrors the relative cycle path under
        {source_root}/derived/{kind}/{YYYY-MM-DD}/{HH}Z/[resolution/]/

    If no date-named ancestor is found (e.g. undated test fixtures), falls back
    to {grib_path.parent}/{kind}/ — the legacy layout.
    """
    resolved = Path(grib_path).resolve()
    date_dir: Path | None = None
    for ancestor in resolved.parents:
        if DATE_KEY_PATTERN.fullmatch(ancestor.name):
            date_dir = ancestor
            break
    if date_dir is None:
        return resolved.parent / kind
    source_root = date_dir.parent
    # Relative path from date_dir to the grib's parent = "{HH}Z/[resolution/]"
    relative = resolved.parent.relative_to(date_dir)
    return source_root / "derived" / kind / relative


def first_existing(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def find_partitioned_file(root: str | Path, relative_pattern: str, legacy: Path) -> Path:
    if legacy.is_file():
        return legacy
    matches = sorted(Path(root).glob(relative_pattern), reverse=True)
    return matches[0] if matches else legacy
