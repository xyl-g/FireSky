"""Theme-aware rendering of the bundled Lucide icons (see assets/icons/LICENSE)."""

from pathlib import Path
from xml.etree import ElementTree

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


def icon(name, color=None):
    from desktop import theme

    root = ElementTree.parse(Path(__file__).parent / "assets" / "icons" / f"{name}.svg").getroot()
    root.set("stroke", color or theme.palette()["ink3"])
    renderer = QSvgRenderer(QByteArray(ElementTree.tostring(root)))
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def set_icon(button, name, *, color=None):
    if color is not None:
        button.setProperty("glyphColor", color)
    elif button.objectName() != "runBtn":
        button.setProperty("glyphColor", None)
    button.setIcon(icon(name, color or button.property("glyphColor")))
    button.setIconSize(QSize(16, 16))
    button.setProperty("glyph", name)
