from __future__ import annotations

import errno
import fcntl
import time
from contextlib import contextmanager
from typing import Iterator, TextIO


def acquire_file_lock(
    handle: TextIO,
    *,
    timeout_seconds: float,
    description: str,
) -> None:
    """Acquire an inter-process lock with a finite wait."""
    timeout = max(0.0, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待 {description} 超时。") from exc
            time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))


@contextmanager
def bounded_file_lock(
    handle: TextIO,
    *,
    timeout_seconds: float,
    description: str,
) -> Iterator[None]:
    acquire_file_lock(
        handle,
        timeout_seconds=timeout_seconds,
        description=description,
    )
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
