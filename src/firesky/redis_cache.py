"""Thin Redis helper for caching infrequently-changing reference data.

The /data/status endpoint used to perform slow upstream HEAD probes (NOMADS /
ECMWF opendata / DWD / CDS) on every request to discover the latest published
model cycle. To keep that API fast, a background scheduler (Go) calls the
Python ``/api/v1/firesky/data/latest/refresh`` endpoint every ~10 minutes; that
endpoint performs the probes once and persists the result here. The status
endpoint then reads straight from Redis.

All failures degrade gracefully: if Redis is unavailable the callers fall back
to live queries, so this module never blocks the main request path.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import redis

# Key holding the latest published cycle per source, e.g.
# {"gfs": "2026-07-19 00Z", "ecmwf": ..., "cams": ...,
#  "refreshed_at": "2026-07-19T05:10:00+00:00"}
LATEST_CYCLES_KEY = "firesky:latest_cycles"

_client: Optional["redis.Redis"] = None


def _build_client() -> Optional["redis.Redis"]:
    """Build a Redis client and verify connectivity. Returns None if Redis is
    unreachable so callers can fall back to live queries."""
    addr = os.environ.get("FIRESKY_REDIS_ADDR", "127.0.0.1:6379")
    host = addr.rsplit(":", 1)[0] if ":" in addr else addr
    port = int(addr.rsplit(":", 1)[1]) if ":" in addr else 6379
    password = os.environ.get("FIRESKY_REDIS_PASSWORD") or None
    db = int(os.environ.get("FIRESKY_REDIS_DB", "0"))
    try:
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            db=db,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        return client
    except Exception:
        return None


def get_redis() -> Optional["redis.Redis"]:
    """Return a lazily-initialised, connection-checked Redis client (or None)."""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def redis_available() -> bool:
    return get_redis() is not None


def get_latest_cycles() -> Optional[dict]:
    """Return the cached latest-cycle map, or None on miss / error."""
    client = get_redis()
    if client is None:
        return None
    try:
        raw = client.get(LATEST_CYCLES_KEY)
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def set_latest_cycles(cycles: dict, ttl: Optional[int] = None) -> bool:
    """Persist the latest-cycle map. Returns True on success.

    TTL defaults to 20 min (2x the 10-min refresh interval) so a missed refresh
    still leaves usable (slightly stale) data rather than an empty cache.
    """
    client = get_redis()
    if client is None:
        return False
    if ttl is None:
        ttl = int(os.environ.get("FIRESKY_REDIS_TTL_SECONDS", "1200"))
    try:
        client.set(LATEST_CYCLES_KEY, json.dumps(cycles), ex=ttl)
        return True
    except Exception:
        return False
