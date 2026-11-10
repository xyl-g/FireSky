"""FireSky 原生桌面工作台（PySide6）。

三个预测页面共用原有预测核心，设置在独立弹窗中。
单点页采用固定执行区、独立滚动栏与结果标签页；菜单提供运行、
取消、页面切换与地点搜索。中性亮暗主题保留引擎档位的颜色语义。

运行：python desktop/app.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QMessageBox,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.ui_datasource import SettingsPanel, SettingsDialog
from desktop.ui_data import DataSourceTab
from desktop.location_controller import CurrentLocationController
from desktop.ui_map import MapPickerDialog
from desktop.ui_nowcast import NowcastTab
from desktop.ui_point import PointForecastTab
from desktop.ui_regional import RegionalForecastTab
from desktop.widgets import GlassCard, SpotStore, Toast

try:  # 地图控件依赖 QtWebEngine；未安装时仅跳过 Key 刷新广播
    from desktop.web_map import FireSkyMap
except Exception:  # noqa: BLE001
    FireSkyMap = None  # type: ignore[assignment]

WINDOW_TITLE = "FireSky — 火烧云本地预测工作台"

PAGES = [
    ("forecast", "单点预测", "sun"),
    ("regional", "区域预测", "grid"),
    ("nowcast", "实况观测", "target"),
    ("data", "数据与下载", "stack"),
]


class AppShell(QWidget):
    """Broad sky-tinted planes under translucent desktop surfaces."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appShell")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QColor(p["canvas"]))
        background.setColorAt(0.55, QColor(p["canvas_alt"]))
        background.setColorAt(1, QColor("#1d3634" if theme.is_dark() else "#e3f0eb"))
        painter.fillRect(self.rect(), background)
        light = QLinearGradient(0, 0, self.width(), 0)
        light.setColorAt(0, QColor(255, 255, 255, 0))
        light.setColorAt(0.5, QColor(255, 255, 255, 10 if theme.is_dark() else 55))
        light.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillRect(self.rect(), light)


class BrandMark(QFrame):
    """FireSky's sun cut by two flowing cloud ribbons."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 42, self.height() / 42)
        grad = QLinearGradient(9, 4, 32, 37)
        grad.setColorAt(0, QColor("#f5b76e"))
        grad.setColorAt(0.5, QColor("#ec7859"))
        grad.setColorAt(1, QColor("#c3474b"))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        sun = QPainterPath()
        sun.addEllipse(QRectF(8, 4, 28, 28))
        upper = QPainterPath()
        upper.moveTo(2, 24)
        upper.cubicTo(11, 17, 24, 32, 40, 20)
        upper.lineTo(42, 24)
        upper.cubicTo(25, 38, 10, 23, 1, 29)
        upper.closeSubpath()
        painter.drawPath(sun.subtracted(upper))
        ribbon = QPainterPath()
        ribbon.moveTo(5, 33)
        ribbon.cubicTo(15, 27, 26, 40, 36, 31)
        painter.setPen(QPen(QColor("#438e89"), 2.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(ribbon)


def _draw_nav_icon(painter: QPainter, key: str, cx: float, cy: float,
                   color: QColor) -> None:
    pen = QPen(color, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if key == "sun":
        painter.drawEllipse(QPointF(cx, cy), 4.2, 4.2)
        for dx, dy in ((0, -8), (0, 8), (-8, 0), (8, 0),
                       (-6, -6), (6, -6), (-6, 6), (6, 6)):
            painter.drawLine(QPointF(cx + dx * 0.72, cy + dy * 0.72),
                             QPointF(cx + dx, cy + dy))
    elif key == "grid":
        painter.drawRoundedRect(QRectF(cx - 8, cy - 7, 16, 14), 2, 2)
        painter.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 7))
        painter.drawLine(QPointF(cx - 8, cy), QPointF(cx + 8, cy))
    elif key == "target":
        painter.drawEllipse(QPointF(cx, cy), 7.6, 7.6)
        painter.drawEllipse(QPointF(cx, cy), 2.8, 2.8)
        painter.drawLine(QPointF(cx - 10, cy), QPointF(cx - 7, cy))
        painter.drawLine(QPointF(cx + 7, cy), QPointF(cx + 10, cy))
    else:  # stack
        for offset in (-5, 0, 5):
            painter.drawLine(QPointF(cx - 8, cy + offset),
                             QPointF(cx + 8, cy + offset))
        painter.drawEllipse(QPointF(cx, cy - 5), 8, 3)


class NavButton(QPushButton):
    """自绘侧栏导航项：图标 + 标题 + 快捷键 + 选中高亮条。"""

    def __init__(self, icon: str, text: str, shortcut: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("navBtn")
        self._icon = icon
        self._label = text
        self._shortcut = shortcut
        self.setFixedHeight(43)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setProperty("active", "false")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.property("active") == "true"
        hovered = self.underMouse()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if active or hovered:
            fill = QColor(p["accent"] if active else p["glass"])
            fill.setAlpha(30 if active else 110)
            painter.setBrush(fill)
            edge = QColor(p["accent"] if active else p["line_rgb"])
            edge.setAlpha(56 if active else p["border_a"])
            painter.setPen(QPen(edge, 1))
            painter.drawRoundedRect(rect, 8, 8)
        if active:
            painter.setBrush(QColor(p["accent"]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(0, self.height() / 2 - 10, 3, 20), 2, 2)

        color = QColor(p["accent"] if active else p["ink3"])
        _draw_nav_icon(painter, self._icon, 26, self.height() / 2, color)
        font = QFont()
        font.setPointSize(12)
        font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(p["accent"] if active else (p["ink"] if hovered else p["ink3"])))
        painter.drawText(QRectF(44, 0, self.width() - 90, self.height()),
                         Qt.AlignmentFlag.AlignVCenter, self._label)
        small = QFont()
        small.setPointSize(9)
        painter.setFont(small)
        painter.setPen(QColor(p["faint"]))
        painter.drawText(QRectF(self.width() - 44, 0, 34, self.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         self._shortcut)
        if self.hasFocus():
            painter.setPen(QPen(QColor(p["accent"]), 1, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 5, 5)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "firesky.png")))
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)

        self.spot_store = SpotStore()
        self._map_dialog: MapPickerDialog | None = None
        self._nav_buttons: list[NavButton] = []
        self._theme_buttons: dict[str, QPushButton] = {}

        shell = AppShell()
        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        main = QWidget()
        main_l = QVBoxLayout(main)
        main_l.setContentsMargins(0, 0, 0, 0)
        main_l.setSpacing(0)
        main_l.addWidget(self._build_topbar())

        self.stack = QStackedWidget()
        self.stack.setFrameShape(QFrame.Shape.NoFrame)
        main_l.addWidget(self.stack, 1)
        root.addWidget(main, 1)
        self.setCentralWidget(shell)
        self._shell = shell

        # ── 四个工作页面 ──
        self.point_tab = PointForecastTab(
            self.spot_store, on_pick_map=self.open_map_picker
        )
        self.regional_tab = RegionalForecastTab()
        self.nowcast_tab = NowcastTab(
            self.spot_store, on_pick_map=self.open_map_picker
        )
        self.datasource_tab = DataSourceTab(self.spot_store)
        self.settings_panel = SettingsPanel()
        self.settings_panel.mapSettingsChanged.connect(self._on_map_settings_changed)
        self.datasource_tab.dataStatusChanged.connect(
            self.point_tab.refresh_source_summary
        )
        self.stack.addWidget(self.point_tab)
        for page in (self.regional_tab, self.nowcast_tab, self.datasource_tab):
            self.stack.addWidget(self._wrap_page(page))
        # 设置 = 独立弹窗（macOS 菜单栏「设置… ⌘,」），不占主导航
        self._settings_dialog: SettingsDialog | None = None

        self._build_menu()

        self._toast = Toast(shell)
        self.location_controller = CurrentLocationController(self)
        self.location_controller.busyChanged.connect(self._location_busy)
        self.location_controller.located.connect(self._location_received)
        self.location_controller.failed.connect(
            lambda message: QMessageBox.warning(self, "当前位置", message)
        )
        self._select_page(0)
        self.spot_store.changed.connect(self._on_spot_changed)
        self.point_tab.refresh_source_summary()

    # ── 外壳 ──
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(196)
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 24, 15, 16)
        layout.setSpacing(0)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(11)
        brand_row.addWidget(BrandMark())
        brand_text = QVBoxLayout()
        brand_text.setSpacing(1)
        brand_text.addWidget(widgets.role_label("FireSky", "h2"))
        edition = widgets.role_label("DESKTOP", "hint")
        brand_text.addWidget(edition)
        brand_row.addLayout(brand_text)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        layout.addSpacing(30)

        label = widgets.role_label("观测工作台", "hint")
        label.setStyleSheet("font-size: 10px; font-weight: 650;")
        layout.addWidget(label)
        layout.addSpacing(9)
        for index, (_, title, icon) in enumerate(PAGES):
            shortcut = QKeySequence(f"Ctrl+{index + 1}").toString(
                QKeySequence.SequenceFormat.NativeText
            )
            button = NavButton(icon, title, shortcut)
            button.clicked.connect(lambda _=False, i=index: self._select_page(i))
            layout.addWidget(button)
            self._nav_buttons.append(button)
        layout.addStretch(1)

        engine = GlassCard(radius=8)
        engine_l = QVBoxLayout(engine)
        engine_l.setContentsMargins(13, 12, 13, 12)
        engine_l.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(7)
        dot = widgets.role_label("●", "hint")
        dot.setStyleSheet(f"color: {theme.palette()['ok']}; font-size: 11px;")
        head.addWidget(dot)
        head.addWidget(widgets.role_label("本地预测引擎", "h2"))
        head.addStretch(1)
        engine_l.addLayout(head)
        engine_l.addWidget(widgets.role_label("本地计算 · 按需下载数据", "hint"))
        from firesky.version import FIRESKY_ALGORITHM_VERSION
        engine_l.addWidget(widgets.role_label(FIRESKY_ALGORITHM_VERSION, "hint"))
        layout.addWidget(engine)
        layout.addSpacing(12)
        settings = QPushButton("设置")
        settings.setObjectName("ghostBtn")
        settings.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        settings.clicked.connect(self.open_settings)
        layout.addWidget(settings)
        layout.addSpacing(8)

        switch = QFrame()
        switch.setFrameShape(QFrame.Shape.NoFrame)
        switch.setProperty("plain", "true")
        switch_l = QHBoxLayout(switch)
        switch_l.setContentsMargins(4, 4, 4, 4)
        switch_l.setSpacing(3)
        for value, text in (("light", "☀  浅色"), ("dark", "☾  深色")):
            button = QPushButton(text)
            button.setObjectName("themeBtn")
            button.setProperty("active", "false")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: self._set_theme(v == "dark"))
            switch_l.addWidget(button, 1)
            self._theme_buttons[value] = button
        layout.addWidget(switch)
        return sidebar

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(72)
        root = QHBoxLayout(bar)
        root.setContentsMargins(20, 12, 20, 10)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)
        self._title = widgets.role_label("", "h1")
        self._subtitle = widgets.role_label("", "subtitle", elide=True)
        left.addWidget(self._title)
        left.addWidget(self._subtitle)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(0)
        pills = QHBoxLayout()
        pills.setSpacing(10)
        self._local_pill = widgets.chip("●  本地计算", "ok")
        weekday_cn = "一二三四五六日"[datetime.now().weekday()]
        self._date_pill = widgets.chip(
            f"{datetime.now().strftime('%m月%d日')} · 周{weekday_cn}"
        )
        pills.addWidget(self._local_pill)
        pills.addWidget(self._date_pill)
        right.addLayout(pills)
        right.addStretch(1)
        root.addLayout(right)
        return bar

    def _wrap_page(self, page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setStyleSheet("QScrollArea { background: transparent; }")
        area.viewport().setAutoFillBackground(False)
        area.viewport().setStyleSheet("background: transparent;")
        area.setWidget(page)
        return area

    # ── 页面切换 ──
    def _select_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        key, title, _ = PAGES[index]
        self._title.setText(title)
        loc = self.spot_store.location()
        self._subtitle.setText(
            f"{loc.name} · {loc.coord_text()}" if key in {"forecast", "nowcast"}
            else "本地原始数据 · 周期与时效 · 按需下载" if key == "data"
            else datetime.now().strftime("%Y-%m-%d")
        )
        if key == "data":
            self.datasource_tab.refresh()
        for i, button in enumerate(self._nav_buttons):
            button.setChecked(i == index)
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)

    # ── 共享观测点 ──
    def _build_menu(self) -> None:
        """macOS 顶部菜单栏：应用菜单含「设置… ⌘,」（Qt 自动归并到应用菜单）。"""
        menu = self.menuBar()
        app_menu = menu.addMenu("FireSky")
        settings_action = app_menu.addAction("设置…")
        settings_action.setMenuRole(QAction.MenuRole.PreferencesRole)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.open_settings)
        about_action = app_menu.addAction("关于 FireSky")
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        about_action.triggered.connect(self._show_about)
        view_menu = menu.addMenu("视图")
        for index, (_, title, _) in enumerate(PAGES):
            action = view_menu.addAction(title)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(lambda _=False, i=index: self._select_page(i))
        task_menu = menu.addMenu("任务")
        run = task_menu.addAction("运行当前预测")
        run.setShortcuts([QKeySequence("F5"), QKeySequence("Ctrl+Return")])
        run.triggered.connect(self._run_current_page)
        cancel = task_menu.addAction("取消当前任务")
        cancel.setShortcut(QKeySequence("Esc"))
        cancel.triggered.connect(self._cancel_current_page)
        search = task_menu.addAction("搜索地点")
        search.setShortcut(QKeySequence("Ctrl+K"))
        search.triggered.connect(self._focus_location_search)

    def _run_current_page(self) -> None:
        index = self.stack.currentIndex()
        if index == 0:
            self.point_tab._run_forecast()
        elif index in (1, 2):
            (self.regional_tab if index == 1 else self.nowcast_tab)._run()

    def _cancel_current_page(self) -> None:
        index = self.stack.currentIndex()
        if index < 3:
            (self.point_tab, self.regional_tab, self.nowcast_tab)[index]._cancel()

    def _focus_location_search(self) -> None:
        self._select_page(0)
        self.point_tab.rail_scroll.ensureWidgetVisible(self.point_tab.search_edit)
        self.point_tab.search_edit.setFocus()
        self.point_tab.search_edit.selectAll()

    def _show_about(self) -> None:
        from firesky.version import FIRESKY_ALGORITHM_VERSION

        QMessageBox.about(
            self,
            "关于 FireSky",
            "<b>FireSky 桌面工作台</b><br>火烧云本地预测<br><br>"
            f"算法版本 {FIRESKY_ALGORITHM_VERSION}<br>"
            "与 Web 服务共用同一预测核心。",
        )

    def open_settings(self) -> None:
        """打开独立设置弹窗（单点页「⚙ 打开设置」/ 菜单栏「设置…⌘,」）。"""
        if self._settings_dialog is None:
            self._settings_dialog = SettingsDialog(self.settings_panel, self)
        self._settings_dialog.open()

    def open_data(self) -> None:
        self._select_page(3)

    def request_current_location(self) -> None:
        location = self.spot_store.location()
        self._location_origin = (location.lat, location.lon)
        self.location_controller.request()

    def _location_busy(self, busy: bool) -> None:
        for button in (self.point_tab.current_location_button,
                       self.nowcast_tab.current_location_button):
            button.setEnabled(not busy)
            button.setText("定位中…" if busy else "获取当前位置")

    def _location_received(self, lat: float, lon: float, accuracy: float) -> None:
        import math

        location = self.spot_store.location()
        if (location.lat, location.lon) != getattr(self, "_location_origin", None):
            self._toast.show_message("定位期间已手动更改点位；本次定位结果未覆盖新点位。")
            return
        self.point_tab._on_mini_map_picked(lat, lon)
        location = self.spot_store.location()
        location.name = "当前位置"
        self.spot_store.set_location(location)
        accuracy_text = f"系统水平精度约 ±{accuracy:.0f} m" if math.isfinite(accuracy) else "系统未提供定位精度"
        self.point_tab._search_status.setText(f"已获取当前位置；{accuracy_text}，正在查询海拔与时区。")
        self.point_tab._search_status.show()
        self._toast.show_message(f"当前位置已同步；{accuracy_text}")

    def open_map_picker(self) -> None:
        if self._map_dialog is None:
            self._map_dialog = MapPickerDialog(self.spot_store, self)
            self._map_dialog.spotConfirmed.connect(self._set_spot)
            self._map_dialog.configureKeyRequested.connect(self._open_map_settings)
        self._map_dialog.open_for(self.spot_store.location())

    def _open_map_settings(self) -> None:
        """浮层里点「配置天地图 Key」→ 收起浮层并跳到设置页地图供应商组。"""
        if self._map_dialog is not None:
            self._map_dialog.hide()
        self.open_settings()
        self.settings_panel.focus_map_key()

    def _on_map_settings_changed(self) -> None:
        """设置页改了 Key / 底图源：所有已存在的地图实例立即生效。"""
        if FireSkyMap is not None:
            for view in self.findChildren(FireSkyMap):
                view.refresh_key()
        if self._map_dialog is not None:
            self._map_dialog.on_key_changed()

    def _set_spot(self, loc) -> None:
        # changed 信号统一驱动各页面同步与 Toast，避免重复提示
        self.spot_store.set_location(loc)

    def _on_spot_changed(self, loc) -> None:
        if self.stack.currentIndex() in (0, 2):
            self._subtitle.setText(f"{loc.name} · {loc.coord_text()}")
        self._toast.show_message(
            f"观测点已更新为「{loc.name}」· 单点预测与实况观测已同步"
        )

    # ── 主题 ──
    def _set_theme(self, dark: bool) -> None:
        app = QApplication.instance()
        if app is None:
            return
        theme.apply_theme(app, dark)
        for value, button in self._theme_buttons.items():
            active = (value == "dark") == dark
            button.setProperty("active", "true" if active else "false")
            theme.repolish(button)
        for button in self._nav_buttons:
            theme.repolish(button)
        for widget in self.findChildren(QWidget):
            if isinstance(widget, (GlassCard, widgets.MiniMap, widgets.QualityCurve)):
                widget.update()
            if hasattr(widget, "on_theme_changed"):
                widget.on_theme_changed()
        self._shell.update()
        self._toast.update()
        self._local_pill.setProperty("tone", "ok")
        theme.repolish(self._local_pill)
        theme.repolish(self._date_pill)

    def _restore_theme(self) -> None:
        self._set_theme(theme.load_theme_preference())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self._toast.isVisible():
            self._toast.move(
                max(8, self._shell.width() - self._toast.width() - 26),
                max(8, self._shell.height() - self._toast.height() - 24),
            )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        download = self.datasource_tab._download_worker
        if download is not None and download.isRunning():
            answer = QMessageBox.question(
                self, "下载仍在运行",
                "关闭窗口会停止进度监控，后台下载可能仍在继续。确认关闭？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        for tab in (self.point_tab, self.regional_tab, self.nowcast_tab,
                    self.datasource_tab):
            tab.shutdown()
        if self._map_dialog is not None:
            self._map_dialog.shutdown()
        event.accept()


def _ensure_writable_cwd() -> None:
    """保证工作目录可写。

    从 Finder 双击 .app 启动时，进程 cwd 是 ``/``（macOS 只读根卷），而预测
    核心与缓存层大量使用相对路径 ``.cache/...``，任何写入都会抛
    ``[Errno 30] Read-only file system: '.cache'``。从终端启动则继承终端
    目录、恰好可写，所以开发时不会暴露。

    这里先用探针文件实测当前目录能否写入：能写就保持原样（开发态行为不变、
    已有项目内缓存继续命中），不能写就切到配置目录，缓存自然落到
    ``~/.firesky-desktop/.cache``。
    """
    fallback = Path.home() / ".firesky-desktop"
    try:
        cwd = Path.cwd()
        probe = cwd / f".firesky-write-probe-{os.getpid()}"
        probe.touch()
        probe.unlink()
        return
    except OSError:
        pass
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        os.chdir(fallback)
    except OSError:
        pass


def main() -> int:
    _ensure_writable_cwd()
    app = QApplication(sys.argv)
    app.setApplicationName("FireSky")
    app.setOrganizationName("FireSky")
    window = MainWindow()
    window._restore_theme()
    window.show()

    # 打包自检：设置 FIRESKY_SNAPSHOT=<png 路径> 时，等界面（含 WebEngine 地图）
    # 渲染完成后抓图并退出，用于验证 .app 产物里的地图是否真的加载出来。
    snapshot = (os.environ.get("FIRESKY_SNAPSHOT") or "").strip()
    if snapshot:
        def _capture() -> None:
            try:
                window.grab().save(snapshot)
            finally:
                app.quit()

        QTimer.singleShot(9000, _capture)

    return app.exec()


if __name__ == "__main__":
    # Frozen data-download subprocesses must enter their worker, not another GUI.
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
