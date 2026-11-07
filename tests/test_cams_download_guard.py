"""Concurrency contract for the CAMS ADS download guard."""

import threading
import time

from firesky.aerosol import _cams_download_guard, _cams_download_worker_limit


def test_worker_limit_env_clamped(monkeypatch):
    monkeypatch.setenv("FIRESKY_CAMS_DOWNLOAD_WORKERS", "99")
    assert _cams_download_worker_limit() == 4
    monkeypatch.setenv("FIRESKY_CAMS_DOWNLOAD_WORKERS", "0")
    assert _cams_download_worker_limit() == 1
    monkeypatch.setenv("FIRESKY_CAMS_DOWNLOAD_WORKERS", "not-a-number")
    assert _cams_download_worker_limit() == 2
    monkeypatch.setenv("FIRESKY_CAMS_DOWNLOAD_WORKERS", "3")
    assert _cams_download_worker_limit() == 3


def test_guard_bounds_concurrency_and_releases_all_slots():
    limit = _cams_download_worker_limit()
    state = {"active": 0, "max_active": 0}
    counter_lock = threading.Lock()
    completed: list[int] = []

    def worker(index: int) -> None:
        with _cams_download_guard():
            with counter_lock:
                state["active"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.15)
            with counter_lock:
                state["active"] -= 1
                completed.append(index)

    threads = [
        threading.Thread(target=worker, args=(index,))
        for index in range(limit + 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30.0)

    assert sorted(completed) == list(range(limit + 1))
    assert 1 <= state["max_active"] <= limit
