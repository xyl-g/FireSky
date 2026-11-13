import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _repair_joined_path_args(args) -> bool:
    """Split pure-path joined collection args (``"tests/a.py tests/b.py"``)
    back apart when the joined value does not exist, contains spaces, and
    every component exists as a path (optionally relative to tests/).
    Joined ``-k``/``-m`` expressions must NOT be handled here — ``config.args``
    holds collection paths only; writing an option into it makes pytest resolve
    ``-k`` as a path and fail with exit code 4.
    """
    repaired = []
    changed = False
    for arg in args:
        candidate = None
        if " " in arg and not arg.startswith("-") and not os.path.exists(arg):
            parts = [part for part in arg.split(" ") if part]
            if parts and all(os.path.exists(part) for part in parts):
                candidate = parts
            else:
                prefixed = [os.path.join("tests", part) for part in parts]
                if parts and all(os.path.exists(part) for part in prefixed):
                    candidate = prefixed
        if candidate is None:
            repaired.append(arg)
        else:
            repaired.extend(candidate)
            changed = True
    if changed:
        args[:] = repaired
    return changed


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    """Split pure-path joined args before the session reads config.args."""
    _repair_joined_path_args(session.config.args)
    yield


def unavailable(message):
    if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture
def engine_binary():
    path = Path(os.environ.get(
        "FIRESKY_RUST_ENGINE_BIN",
        ROOT / "engine" / "target" / "debug" / ("firesky-engine.exe" if os.name == "nt" else "firesky-engine"),
    ))
    if not path.is_file():
        unavailable("build the Rust engine with cargo build --manifest-path engine/Cargo.toml")
    return path