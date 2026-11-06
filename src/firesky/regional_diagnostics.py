from __future__ import annotations

import json
import math
import os
import sqlite3
import zlib
from pathlib import Path
from typing import Any, Mapping

from firesky.storage import dated_directory, find_partitioned_file, storage_phenomenon


DIAGNOSTIC_BUNDLE_VERSION = 1
DIAGNOSTIC_BUNDLE_ALGORITHM = "regional-diagnostics-2026.07.17.2"
DIAGNOSTIC_COMPRESSION_LEVEL = max(
    1,
    min(9, int(os.environ.get("FIRESKY_DIAGNOSTIC_COMPRESSION_LEVEL", "3"))),
)


class RegionalDiagnosticBundleWriter:
    """Incrementally stores full point reports without retaining them in memory."""

    def __init__(self, path: str | Path, metadata: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS point_diagnostics (
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                observer_name TEXT NOT NULL,
                payload BLOB NOT NULL,
                uncompressed_bytes INTEGER NOT NULL,
                PRIMARY KEY (lat, lon)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_point_diagnostics_location ON point_diagnostics(lat, lon)"
        )
        base_metadata = {
            "version": DIAGNOSTIC_BUNDLE_VERSION,
            "algorithm": DIAGNOSTIC_BUNDLE_ALGORITHM,
            **(dict(metadata) if metadata is not None else {}),
        }
        self.connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            [
                (str(key), json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                for key, value in base_metadata.items()
            ],
        )
        self.count = 0

    def add_report(self, report, lat: float, lon: float) -> None:
        self.add_payload(report.to_dict(), lat, lon, report.observer_name)

    def add_payload(
        self,
        payload: Mapping[str, Any],
        lat: float,
        lon: float,
        observer_name: str,
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.connection.execute(
            """
            INSERT INTO point_diagnostics(lat, lon, observer_name, payload, uncompressed_bytes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(lat, lon) DO UPDATE SET
                observer_name=excluded.observer_name,
                payload=excluded.payload,
                uncompressed_bytes=excluded.uncompressed_bytes
            """,
            (
                round(float(lat), 4),
                round(float(lon), 4),
                str(observer_name),
                sqlite3.Binary(zlib.compress(encoded, level=DIAGNOSTIC_COMPRESSION_LEVEL)),
                len(encoded),
            ),
        )
        self.count += 1
        if self.count % 64 == 0:
            self.connection.commit()

    def merge(self, source_path: str | Path) -> int:
        source = sqlite3.connect(Path(source_path), timeout=30.0)
        try:
            rows = source.execute(
                "SELECT lat, lon, observer_name, payload, uncompressed_bytes FROM point_diagnostics"
            ).fetchall()
        finally:
            source.close()
        self.connection.executemany(
            """
            INSERT INTO point_diagnostics(lat, lon, observer_name, payload, uncompressed_bytes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(lat, lon) DO UPDATE SET
                observer_name=excluded.observer_name,
                payload=excluded.payload,
                uncompressed_bytes=excluded.uncompressed_bytes
            """,
            rows,
        )
        self.count += len(rows)
        self.connection.commit()
        return len(rows)

    def close(self) -> int:
        actual_count = int(
            self.connection.execute("SELECT COUNT(*) FROM point_diagnostics").fetchone()[0]
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES ('point_count', ?)",
            (json.dumps(actual_count),),
        )
        self.connection.commit()
        self.connection.execute("VACUUM")
        self.connection.close()
        return actual_count

    def __enter__(self) -> RegionalDiagnosticBundleWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.close()
        else:
            self.connection.close()


def diagnostic_bundle_path(
    cache_dir: str | Path,
    token: str,
    target_date: str | None = None,
    phenomenon: str | None = None,
) -> Path:
    safe_token = "".join(character for character in str(token) if character.isalnum() or character in "-_")
    if not safe_token or safe_token != token:
        raise ValueError("invalid regional diagnostics token")
    filename = f"regional-diagnostics-v{DIAGNOSTIC_BUNDLE_VERSION}-{safe_token}.sqlite3"
    legacy = Path(cache_dir) / "diagnostics" / filename
    if target_date is not None:
        return dated_directory(
            cache_dir,
            target_date,
            storage_phenomenon(phenomenon),
            "diagnostics",
        ) / filename
    return find_partitioned_file(cache_dir, f"*/*/diagnostics/{filename}", legacy)


def read_regional_diagnostic(
    path: str | Path,
    lat: float,
    lon: float,
    *,
    max_distance_deg: float = 0.05,
) -> dict[str, Any]:
    target_lat = float(lat)
    target_lon = float(lon)
    with sqlite3.connect(Path(path), timeout=5.0) as connection:
        row = connection.execute(
            """
            SELECT lat, lon, observer_name, payload
            FROM point_diagnostics
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
            ORDER BY ((lat - ?) * (lat - ?)) + ((lon - ?) * (lon - ?))
            LIMIT 1
            """,
            (
                target_lat - max_distance_deg,
                target_lat + max_distance_deg,
                target_lon - max_distance_deg,
                target_lon + max_distance_deg,
                target_lat,
                target_lat,
                target_lon,
                target_lon,
            ),
        ).fetchone()
    if row is None:
        raise FileNotFoundError("该坐标没有区域完整诊断。")
    point_lat, point_lon, observer_name, compressed = row
    if math.hypot(float(point_lat) - target_lat, float(point_lon) - target_lon) > max_distance_deg:
        raise FileNotFoundError("该坐标没有区域完整诊断。")
    payload = json.loads(zlib.decompress(compressed).decode("utf-8"))
    return {
        "lat": float(point_lat),
        "lon": float(point_lon),
        "observer_name": str(observer_name),
        "diagnostic": payload,
    }


def diagnostic_bundle_metadata(path: str | Path) -> dict[str, Any]:
    with sqlite3.connect(Path(path), timeout=5.0) as connection:
        rows = connection.execute("SELECT key, value FROM metadata").fetchall()
    return {str(key): json.loads(value) for key, value in rows}
