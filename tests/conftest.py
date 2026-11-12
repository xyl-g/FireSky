import os
import sys
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def no_desktop_publication_network(monkeypatch):
    module = sys.modules.get("desktop.data_panel")
    if module is not None:
        monkeypatch.setattr(module, "published_cycles", lambda sources: {})

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _repair_joined_path_args(args) -> bool:
    """Defence in depth for the gate-runner quoting bug (the primary fix is the
    ``gate_repair`` pytest11 entry-point plugin in
    ``src/firesky/_pytest_gate_repair.py``, which rewrites ``argv`` inside
    ``pytest_load_initial_conftests`` — the only hook that runs before
    ``Config.parse``; this hook is not callable from conftest files).

    Here we can only repair the *pure paths* join (``"tests/a.py tests/b.py"``):
    split it back apart when the joined value does not exist, contains spaces,
    and every component exists as a path (optionally relative to tests/).
    Joined ``-k``/``-m`` expressions must NOT be handled here — ``config.args``
    holds collection paths only; writing an option into it makes pytest resolve
    ``-k`` as a path and fail with the very exit code 4 we are fixing.
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


@pytest.fixture(scope="session")
def qt_app():
    import os
    import gc
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["firesky-ui-tests"])
    yield app
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    app.processEvents()
