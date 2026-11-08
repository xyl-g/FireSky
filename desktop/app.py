"""FireSky 桌面应用（PySide6）· 暮光玻璃 v5。

与 Web 服务共用同一个预测核心：直接调用 firesky.web_server 的权威装配路径
（run_forecast_payload / run_and_cache_national_forecast_payload /
nowcast_payload / handle_data_*），与 HTTP API 数值一致，引擎层零改动。

v5 信息架构：
- 一级导航 4 项：单点预测 / 区域预测 / 实况观测 / 设置
- 单点预测：内嵌选点地图（单击落点）+ 地名搜索（search_location_fast），
  结果页对齐 Web 端信息量（关键指标网格 / 云属色彩诊断 / 边界与时间窗 /
  12 列台账）；预测前数据未缓存时自动下载（引擎预取透传 + 心跳日志）
- 设置页：ADS Key / 地图供应商（OSM 默认 · 天地图卫星需 Key）/ 数据下载
  与缓存，独立标签；主界面只保留一行数据源就绪态 pill + ⚙ 打开设置
- 亮色暖白 / 暗色深暮蓝双主题，半透明玻璃卡片 + 背景霞光晕染

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
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
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
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.ui_datasource import DataSourceTab, SettingsDialog
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
    ("forecast", "单点预测", "在地图上选一个地点，找到霞光最盛的时刻。", "01", "sun"),
    ("regional", "区域预测", "比较不同区域的火烧云潜力，发现最佳观测目的地。", "02", "grid"),
    ("nowcast", "实况观测", "沿用当前观测点，融合卫星与雷达校准正在发生的天空。", "03", "target"),
]


class AppShell(QWidget):
    """主画布：渐变底色 + 两处霞光晕染（玻璃质感的背景层）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("appShell")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QLinearGradient(0, 0, self.width(), self.height())
        base.setColorAt(0, QColor(p["canvas"]))
        base.setColorAt(1, QColor(p["canvas_alt"]))
        painter.fillRect(self.rect(), base)

        for cx, cy, radius, key, alpha_key in (
            (self.width() * 0.02, -self.height() * 0.18, max(self.width(), 700) * 0.62,
             "glow", "glow_a"),
            (self.width() * 1.02, self.height() * 1.12, max(self.width(), 700) * 0.66,
             "glow_alt", "glow_alt_a"),
        ):
            color = QColor(p[key])
            color.setAlpha(p[alpha_key])
            transparent = QColor(color)
            transparent.setAlpha(0)
            glow = QRadialGradient(QPointF(cx, cy), radius)
            glow.setColorAt(0, color)
            glow.setColorAt(1, transparent)
            painter.fillRect(self.rect(), glow)


class BrandMark(QFrame):
    """品牌标记：朝霞渐变圆角方块 + 太阳与地平线。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(39, 39)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0, QColor("#ffc066"))
        grad.setColorAt(0.52, QColor("#ef7b48"))
        grad.setColorAt(1, QColor("#d94e43"))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 13, 13)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(19.5, 17.0), 5.0, 5.0)
        horizon_pen = QPen(QColor("#ffffff"), 1.6)
        horizon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(horizon_pen)
        painter.drawLine(QPointF(11, 26), QPointF(28, 26))
        painter.drawLine(QPointF(14, 30), QPointF(25, 30))
        for x, y in ((19.5, 7), (8, 11), (31, 11)):
            painter.drawLine(QPointF(x, y), QPointF(x, y + 2.2))


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
            painter.drawRoundedRect(rect, 12, 12)
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
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
        self.datasource_tab.mapSettingsChanged.connect(self._on_map_settings_changed)
        self.datasource_tab.dataStatusChanged.connect(
            self.point_tab.refresh_source_summary
        )
        for page in (self.point_tab, self.regional_tab, self.nowcast_tab):
            self.stack.addWidget(self._wrap_page(page))
        # 设置 = 独立弹窗（macOS 菜单栏「设置… ⌘,」），不占主导航
        self._settings_dialog: SettingsDialog | None = None

        self._build_menu()

        self._toast = Toast(shell)
        self._select_page(0)
        self.spot_store.changed.connect(self._on_spot_changed)
        self.point_tab.refresh_source_summary()

        # 快捷键 Ctrl+1..3
        for index in range(len(PAGES)):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(
                lambda _=False, i=index: self._select_page(i)
            )
            self.addAction(action)

    # ── 外壳 ──
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(224)
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
        edition.setStyleSheet("letter-spacing: 2px;")
        brand_text.addWidget(edition)
        brand_row.addLayout(brand_text)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        layout.addSpacing(30)

        label = widgets.role_label("观测工作台", "hint")
        label.setStyleSheet("letter-spacing: 2px; font-size: 10px; font-weight: 650;")
        layout.addWidget(label)
        layout.addSpacing(9)
        for index, (_, title, _, _, icon) in enumerate(PAGES):
            button = NavButton(icon, title, f"⌘{index + 1}")
            button.clicked.connect(lambda _=False, i=index: self._select_page(i))
            layout.addWidget(button)
            self._nav_buttons.append(button)
        layout.addStretch(1)

        engine = GlassCard(radius=15)
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
        engine_l.addWidget(widgets.role_label("已连接 · 数据不离开本机", "hint"))
        engine_l.addWidget(widgets.role_label("Engine 2026.11.04", "hint"))
        layout.addWidget(engine)
        layout.addSpacing(12)

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
        bar.setFixedHeight(105)
        root = QHBoxLayout(bar)
        root.setContentsMargins(30, 20, 30, 16)
        root.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(4)
        self._eyebrow = widgets.role_label("", "eyebrow")
        self._title = widgets.role_label("", "h1")
        self._subtitle = widgets.role_label("", "subtitle")
        left.addWidget(self._eyebrow)
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
        key, title, subtitle, number, _ = PAGES[index]
        self._eyebrow.setText(f"ATMOSPHERIC OBSERVATORY / {number}")
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        for i, button in enumerate(self._nav_buttons):
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)
        area = self.stack.widget(index)
        if isinstance(area, QScrollArea):
            area.verticalScrollBar().setValue(0)

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
            self._settings_dialog = SettingsDialog(self.datasource_tab, self)
        self._settings_dialog.open()

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
        self.datasource_tab.focus_map_key()

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
    raise SystemExit(main())
