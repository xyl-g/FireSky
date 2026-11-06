from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
from pathlib import Path
from typing import Any, List, Optional

from firesky.models import TimeStepAssessment
from firesky.bounded_lock import bounded_file_lock
from firesky.storage import dated_directory, storage_phenomenon


ASSESSMENT_CACHE_VERSION = 12
ASSESSMENT_CACHE_LOCK_TIMEOUT_SECONDS = 300.0


def assessment_cache_path(cache_dir: str | Path, payload: dict[str, Any]) -> Path:
    encoded = json.dumps(
        {"version": ASSESSMENT_CACHE_VERSION, **payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    cycle = str(payload.get("cycle") or "")
    try:
        target_dir = dated_directory(
            Path(cache_dir) / "assessments",
            cycle,
            storage_phenomenon(payload.get("phenomenon")),
        )
    except ValueError:
        target_dir = Path(cache_dir) / "assessments" / "undated"
    return target_dir / f"assessment-v{ASSESSMENT_CACHE_VERSION}-{digest}.pkl.gz"


def read_assessment_cache(path: Path) -> Optional[List[TimeStepAssessment]]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with gzip.open(path, "rb") as handle:
            payload = pickle.load(handle)
        if payload.get("version") != ASSESSMENT_CACHE_VERSION:
            return None
        assessments = payload.get("assessments")
        return assessments if isinstance(assessments, list) else None
    except (OSError, EOFError, pickle.PickleError, AttributeError, TypeError):
        path.unlink(missing_ok=True)
        return None


def write_assessment_cache(path: Path, assessments: List[TimeStepAssessment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as lock_handle:
        with bounded_file_lock(
            lock_handle,
            timeout_seconds=ASSESSMENT_CACHE_LOCK_TIMEOUT_SECONDS,
            description="评估缓存锁",
        ):
            if path.exists() and path.stat().st_size > 0:
                return
            temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
            with gzip.open(temporary, "wb", compresslevel=1) as handle:
                pickle.dump(
                    {"version": ASSESSMENT_CACHE_VERSION, "assessments": assessments},
                    handle,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            temporary.replace(path)
