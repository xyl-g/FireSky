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
