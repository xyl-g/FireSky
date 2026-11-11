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
    """Repair invocations where several test paths arrive joined into ONE
    argument (e.g. ``"tests/a.py tests/b.py"`` from a quoting bug in a CI
    runner).  The join is only undone when the joined value does not exist,
    contains spaces, and every space-separated component exists (optionally
    relative to tests/).  Options such as ``-k`` expressions never qualify
    because their words are not existing paths.  Returns True when repaired.
    """
    repaired = []
    changed = False
    for arg in args:
        candidate = None
        if " " in arg and not os.path.exists(arg):
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
    """Repair joined path args before the session reads config.args."""
    args = getattr(getattr(session, "config", None), "args", None)
    if args is not None:
        _repair_joined_path_args(args)
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
