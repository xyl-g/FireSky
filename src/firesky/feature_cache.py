from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
from pathlib import Path
from typing import Callable, List

from firesky.models import GeoPoint, GridCellFeature
from firesky.bounded_lock import bounded_file_lock
from firesky.storage import derived_cache_dir


FEATURE_CACHE_VERSION = 2
FEATURE_CACHE_LOCK_TIMEOUT_SECONDS = 300.0


def load_or_build_feature_cache(
    grib_path: Path,
    observer: GeoPoint,
    radius_km: float,
    builder: Callable[[], List[GridCellFeature]],
) -> List[GridCellFeature]:
    cache_path = feature_cache_path(grib_path, observer, radius_km)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cache_path.with_suffix(cache_path.suffix + ".lock")
    with lock_path.open("a+b") as lock_handle:
        with bounded_file_lock(
            lock_handle,
            timeout_seconds=FEATURE_CACHE_LOCK_TIMEOUT_SECONDS,
            description="GFS 特征缓存锁",
        ):
            cached = read_feature_cache(cache_path)
            if cached is not None:
                return cached
            features = builder()
            temporary = cache_path.with_suffix(cache_path.suffix + f".tmp-{os.getpid()}")
            with gzip.open(temporary, "wb", compresslevel=1) as handle:
                pickle.dump(
                    {"version": FEATURE_CACHE_VERSION, "features": features},
                    handle,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            temporary.replace(cache_path)
            return features


def feature_cache_path(grib_path: Path, observer: GeoPoint, radius_km: float) -> Path:
    stat = grib_path.stat()
    payload = {
        "version": FEATURE_CACHE_VERSION,
        "grib_name": grib_path.name,
        "grib_size": stat.st_size,
        "grib_mtime_ns": stat.st_mtime_ns,
        "lat": round(observer.lat, 5),
        "lon": round(observer.lon, 5),
        "radius_km": round(radius_km, 1),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    return derived_cache_dir(grib_path, "parsed") / f"features-v{FEATURE_CACHE_VERSION}-{digest}.pkl.gz"


def read_feature_cache(path: Path) -> List[GridCellFeature] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with gzip.open(path, "rb") as handle:
            payload = pickle.load(handle)
        if payload.get("version") != FEATURE_CACHE_VERSION:
            return None
        features = payload.get("features")
        if not isinstance(features, list):
            return None
        return features
    except (OSError, EOFError, pickle.PickleError, AttributeError, TypeError):
        path.unlink(missing_ok=True)
        return None
