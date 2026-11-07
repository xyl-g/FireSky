"""FireSky 桌面应用（PySide6）· 四标签工作台 v2。

与 Web 服务共用同一个预测核心：桌面端直接调用 ``firesky.web_server``
的权威装配路径（run_forecast_payload / run_and_cache_national_forecast_payload /
nowcast_payload / handle_data_*），与 HTTP API 数值完全一致，不经过任何
HTTP 一跳；引擎层零改动。

标签页：
1. 单点预测 —— 观测点收藏 + 表单 + 峰值档位卡 + 质量曲线 + 台账（desktop/ui_point.py）
2. 地图选点 —— QWebEngineView + Leaflet + QWebChannel 回传经纬度 +
   Copernicus DEM 海拔查询（desktop/ui_map.py；WebEngine 缺失时降级手输）
3. 区域预测 —— 区域多选 + 网格间距 + 区域批处理 + 排名台账（desktop/ui_regional.py）
4. 实况 nowcast —— 卫星实况四指标卡 + 台账（desktop/ui_nowcast.py）
5. 数据源 —— ADS Key / GFS·EC·卫星下载 / 缓存维护（desktop/ui_datasource.py）

主题：暖沙亮色 / 暗色 QSS + ☾/☀ 切换（QSettings 记忆，desktop/theme.py）。

运行：``python desktop/app.py``（需 ``pip install -e ".[dev,cams,nowcast,ec]"``）。
打包：见 ``desktop/FireSky.spec``（PyInstaller，macOS/Windows）。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许 ``python desktop/app.py`` 直接运行（把仓库根目录加入 sys.path）。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from desktop import theme
from desktop.ui_datasource import DataSourceTab
from desktop.ui_map import MapPickerTab
from desktop.ui_nowcast import NowcastTab
from desktop.ui_point import PointForecastTab
from desktop.ui_regional import RegionalForecastTab

WINDOW_TITLE = "FireSky — 火烧云本地预测工作台"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1240, 820)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 6)
        root.setSpacing(6)

        # ── 标题栏（品牌 + 主题切换）──
        title_row = QHBoxLayout()
        sun_badge = QLabel("☀")
        sun_badge.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 #f2a24b, stop:1 #d95d2c); color: #fff;"
            "border-radius: 7px; font-size: 13px;"
            "min-width: 24px; min-height: 24px; max-width: 24px; max-height: 24px;"
            "qproperty-alignment: AlignCenter;"
        )
        title_row.addWidget(sun_badge)
        brand = QLabel("FireSky")
        brand.setStyleSheet("font-weight: 700; font-size: 14px;")
        title_row.addWidget(brand)
        brand_sub = theme_palette_label("本地预测工作台 · 与 Web 共用预测核心")
        title_row.addWidget(brand_sub)
        title_row.addStretch(1)
        self.theme_button = QPushButton("☾ 夜间")
        self.theme_button.setObjectName("themeBtn")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._toggle_theme)
        title_row.addWidget(self.theme_button)
        root.addLayout(title_row)

        # ── 四标签 ──
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self.point_tab = PointForecastTab()
        self.map_tab = MapPickerTab(self.point_tab)
        self.regional_tab = RegionalForecastTab()
        self.nowcast_tab = NowcastTab()
        self.datasource_tab = DataSourceTab(self.point_tab)

        self.tabs.addTab(self.point_tab, "单点预测")
        self.tabs.addTab(self.map_tab, "地图选点")
        self.tabs.addTab(self.regional_tab, "区域预测")
        self.tabs.addTab(self.nowcast_tab, "实况 nowcast")
        self.tabs.addTab(self.datasource_tab, "数据源")

        # 地图选点 → 单点预测联动跳转
        self._bridge_map_to_point()

        # ── 状态栏 ──
        status = QStatusBar()
        status_label = QLabel("数据源：GFS/EC 免 Key · CAMS 需 ADS Key（数据源标签配置）· 实况需 Himawari 帧")
        status.addWidget(status_label)
        self.setStatusBar(status)

        # 快捷键 Ctrl+1..5 切换标签
        for index in range(5):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(
                lambda _=False, i=index: self.tabs.setCurrentIndex(i)
            )
            self.addAction(action)

        self.setCentralWidget(central)

    def _bridge_map_to_point(self) -> None:
        """地图选点完成 → 自动切到单点标签（观测点已落址）。"""
        # MapPickerTab 的选点已直接写入 point_tab；此处仅在状态栏提示。
        bridge = getattr(self.map_tab, "_bridge", None)
        if bridge is not None:
            bridge.mapClicked.connect(self._on_map_picked)

    def _on_map_picked(self, lat: float, lon: float) -> None:
        self.statusBar().showMessage(
            f"已选点 {lat:.4f}, {lon:.4f} → 观测点已创建（单点预测标签）", 6000
        )

    # ── 主题 ──
    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        dark = not theme.is_dark()
        theme.apply_theme(app, dark)
        self.theme_button.setText("☀ 日间" if dark else "☾ 夜间")
        self._repaint_results()

    def _repaint_results(self) -> None:
        """主题切换后重绘自绘控件（曲线用主题色）。"""
        self.point_tab.curve.update()

    def _restore_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        dark = theme.load_theme_preference()
        theme.apply_theme(app, dark)
        self.theme_button.setText("☀ 日间" if dark else "☾ 夜间")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.point_tab.shutdown()
        self.regional_tab.shutdown()
        self.nowcast_tab.shutdown()
        self.datasource_tab.shutdown()
        event.accept()


def theme_palette_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "ink2")
    return label


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FireSky")
    app.setOrganizationName("FireSky")
    window = MainWindow()
    window._restore_theme()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
