"""FireSky 桌面应用 · 单点预测页。

布局：
- 左栏「观测点」：地名搜索（回车落点）+ 内嵌选点地图（单击即选点）
  + 收藏列表 + 观测点摘要（海拔/时区 chip）+「⚙ 打开设置」
- 左栏「观测参数」：日期 / 现象 / 模式 / 坐标微调 / 运行日志
- 底部固定执行按钮，左右栏独立滚动
- 右栏「结果」：峰值摘要，以及概览 / 剖面 / 诊断 / 台账标签页

地图选点内嵌在本页固定区域（不再是弹窗展开）；地名搜索走引擎
search_location_fast（Nominatim，带缓存），海拔走引擎 fetch_point_metadata
（Open-Meteo / SRTM，与评分同口径）。预测仍走
web_server.run_forecast_payload 权威路径，业务逻辑未改动。
"""

from __future__ import annotations

import math

from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.data_panel import ForecastDataPanel
from desktop.profile_canvas import (
    FIELD_CLOUD,
    FIELD_RH,
    FIELD_TEMP,
    NationalProfileCanvas,
)
from desktop.theme import (
    BOUNDARY_STABILITY_LABELS,
    COLOR_DIAGNOSIS_LABELS,
    GENUS_LABELS,
    PRESSURE_FAMILY_LABELS,
    REGIME_LABELS,
    TREND_LABELS,
    label_for,
)
from desktop.widgets import (
    GlassCard,
    Location,
    MetricCard,
    MiniMap,
    QualityCurve,
    RunButton,
    SegmentedControl,
    SpotStore,
    fmt,
    load_locations,
    save_locations,
)
from desktop.workers import (
    ElevationQueryWorker,
    ForecastWorker,
    LocationSearchWorker,
    WorkerBase,
)

# 地图控件首选 QWebEngine + OSM/天地图/Esri 真底图；若 QtWebEngine 未安装，
# 降级到纯自绘示意底图，保证预测主流程不被地图拖死。
try:  # noqa: SIM105 - 需要区分"未安装"与"正常"
    from desktop.web_map import FireSkyMap
except Exception:  # noqa: BLE001
    FireSkyMap = None  # type: ignore[assignment]

MAP_PICK_ZOOM = 13  # 内嵌选点地图落点/定位后的缩放级别（街区块可辨识）


class PointForecastTab(QWidget):
    """单点预测工作台（共享 SpotStore 观测点）。"""

    def __init__(self, spot_store: SpotStore, on_pick_map=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = spot_store
        self._on_pick_map = on_pick_map
        self._locations: list[Location] = load_locations()
        self._worker: ForecastWorker | None = None
        self._elev_worker: ElevationQueryWorker | None = None
        self._search_worker: LocationSearchWorker | None = None
        self._syncing = False
        self._result_context = None
        self._running_context = None
        self._model_results = {}
        self._model_errors = {}
        self._build_ui()
        self.date_edit.dateChanged.connect(self._refresh_result_state)
        self._reload_location_list()
        self._apply_spot(self._store.location())
        self._store.changed.connect(self._on_spot_changed)
        self._log("提示：地图上单击落点，或在上方搜索地名（如「西湖」）直接定位。")

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 14, 20, 18)
        root.setSpacing(16)

        rail = QWidget()
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(0, 0, 0, 0)
        rail_l.setSpacing(14)
        rail_l.addWidget(self._build_spot_card())
        rail_l.addWidget(self._build_params_card())
        rail_l.addWidget(self._build_source_summary())
        rail_l.addStretch(1)
        self.rail_scroll = QScrollArea()
        self.rail_scroll.setWidgetResizable(True)
        self.rail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rail_scroll.setWidget(rail)
        rail_column = QVBoxLayout()
        rail_column.setSpacing(10)
        rail_column.addWidget(self.rail_scroll, 1)
        for control in (self.run_button, self.cancel_button, self.progress,
                        self.progress_stage):
            rail_column.addWidget(control)
        rail_host = QWidget()
        rail_host.setFixedWidth(292)
        rail_host.setLayout(rail_column)
        rail_column.setContentsMargins(0, 0, 0, 0)
        root.addWidget(rail_host)

        results_host = QWidget()
        results_l = QVBoxLayout(results_host)
        results_l.setContentsMargins(0, 0, 0, 0)
        results_l.setSpacing(12)
        self.model_results_bar = widgets.ModelResultsBar()
        self.model_results_bar.modelChanged.connect(self._show_model_result)
        results_l.addWidget(self.model_results_bar)
        results_l.addWidget(self._build_hero())
        self.result_tabs = QTabWidget()
        overview = QVBoxLayout()
        overview.addLayout(self._build_metrics())
        overview.addWidget(self._build_chart_card())
        pages = (
            ("概览", overview),
            ("大气剖面", (self._build_profile_card(),)),
            ("诊断与时间窗", (self._build_genus_card(), self._build_boundary_card())),
            ("逐时台账", (self._build_table_card(), self._build_provenance_card())),
        )
        for title, content in pages:
            page = QWidget()
            layout = content if isinstance(content, QVBoxLayout) else QVBoxLayout()
            if not isinstance(content, QVBoxLayout):
                for section in content:
                    layout.addWidget(section)
            layout.setContentsMargins(0, 12, 0, 0)
            layout.setSpacing(12)
            layout.addStretch(1)
            page.setLayout(layout)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.result_tabs.addTab(scroll, title)
        results_l.addWidget(self.result_tabs, 1)
        root.addWidget(results_host, 1)

    def _build_spot_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(widgets.section_title("观测点"))
        head.addStretch(1)
        self._spot_tabs = SegmentedControl(["地图", "收藏"])
        self._spot_tabs.setFixedWidth(128)
        self._spot_tabs.valueChanged.connect(self._on_spot_tab)
        head.addWidget(self._spot_tabs)
        layout.addLayout(head)
        layout.addSpacing(9)

        # 地名搜索框：回车 / 按钮 → 引擎 search_location_fast → 直接落点
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索地名，如「西湖」或「杭州 西湖」")
        self.search_edit.returnPressed.connect(self._on_search)
        self.search_edit.clearButtonEnabled = True
        search_row.addWidget(self.search_edit, 1)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("pickBtn")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)
        self.current_location_button = QPushButton("获取当前位置")
        self.current_location_button.setObjectName("ghostBtn")
        self.current_location_button.clicked.connect(self._request_current_location)
        layout.addWidget(self.current_location_button)
        self._search_status = widgets.role_label("", "hint")
        self._search_status.setWordWrap(True)
        self._search_status.setVisible(False)
        layout.addWidget(self._search_status)
        layout.addSpacing(8)

        self._spot_views = QStackedWidget()
        self._spot_views.setFixedHeight(152)
        map_page = QWidget()
        map_l = QVBoxLayout(map_page)
        map_l.setContentsMargins(0, 0, 0, 0)
        map_l.setSpacing(4)
        self._mini_map = FireSkyMap(mini=True) if FireSkyMap is not None else MiniMap()
        self._mini_map.setFixedHeight(152)
        self._mini_map.picked.connect(self._on_mini_map_picked)
        map_l.addWidget(self._mini_map)
        self._spot_views.addWidget(map_page)

        fav_page = QWidget()
        fav_l = QVBoxLayout(fav_page)
        fav_l.setContentsMargins(0, 0, 0, 0)
        self.location_list = QListWidget()
        self.location_list.setFixedHeight(114)
        self.location_list.currentItemChanged.connect(
            lambda item, _previous: self._on_location_clicked(item)
        )
        fav_l.addWidget(self.location_list)
        self.remove_favorite_button = QPushButton("删除选中收藏")
        self.remove_favorite_button.setObjectName("ghostBtn")
        self.remove_favorite_button.clicked.connect(self._remove_current)
        fav_l.addWidget(self.remove_favorite_button)
        self._spot_views.addWidget(fav_page)
        layout.addWidget(self._spot_views)
        layout.addSpacing(10)

        summary = QHBoxLayout()
        summary.setSpacing(9)
        dot = widgets.role_label("●", "hint")
        dot.setStyleSheet(f"color: {theme.palette()['accent']}; font-size: 11px;")
        summary.addWidget(dot)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._spot_name_label = widgets.role_label("杭州", "h2", elide=True)
        self._spot_coord_label = widgets.role_label("", "ink3")
        text_col.addWidget(self._spot_name_label)
        text_col.addWidget(self._spot_coord_label)
        summary.addLayout(text_col, 1)
        layout.addLayout(summary)
        layout.addSpacing(11)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._elev_chip = widgets.chip("海拔 —")
        self._tz_chip = widgets.chip("时区 UTC+8")
        self._dem_chip = widgets.chip("DEM 自动补齐")
        chips.addWidget(self._elev_chip)
        chips.addWidget(self._tz_chip)
        chips.addWidget(self._dem_chip)
        chips.addStretch(1)
        layout.addLayout(chips)
        layout.addSpacing(9)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.favorite_button = QPushButton("收藏当前点")
        self.favorite_button.setObjectName("ghostBtn")
        self.favorite_button.clicked.connect(self._star_current)
        foot.addWidget(self.favorite_button)
        foot.addStretch(1)
        if self._on_pick_map is not None:
            expand = QPushButton("大地图")
            expand.setObjectName("ghostBtn")
            expand.clicked.connect(self._open_map_picker)
            foot.addWidget(expand)
        layout.addLayout(foot)
        return card

    def _build_source_summary(self) -> QWidget:
        card = GlassCard(radius=8)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(13, 11, 13, 11)
        layout.setSpacing(7)

        head = QHBoxLayout()
        head.setSpacing(7)
        dot = widgets.role_label("●", "hint")
        dot.setStyleSheet(f"color: {theme.palette()['ok']}; font-size: 11px;")
        head.addWidget(dot)
        head.addWidget(widgets.role_label("数据源", "h2"))
        head.addStretch(1)
        layout.addLayout(head)

        self.data_panel = ForecastDataPanel(self, compact=True)
        self.data_panel.bind(self._payload_for_data)
        self.data_panel.busyChanged.connect(self._on_data_busy)
        layout.addWidget(self.data_panel)
        return card

    def _request_current_location(self) -> None:
        window = self.window()
        if hasattr(window, "request_current_location"):
            window.request_current_location()

    def _on_data_busy(self, busy: bool) -> None:
        self.run_button.setEnabled(not busy and self._worker is None)
        self.cancel_button.setVisible(busy or self._worker is not None)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("取消任务")
        if busy:
            self.rail_scroll.ensureWidgetVisible(self.data_panel)

    def _emit_open_settings(self) -> None:
        window = self.window()
        if window is not None and hasattr(window, "open_settings"):
            window.open_settings()

    def refresh_source_summary(self, status: dict | None = None) -> None:
        if self.data_panel._active is None:
            self.data_panel.summary.setText("本地数据可能已变化；运行前将按本次参数检查。")

    def _build_params_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 13, 15, 13)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.addWidget(widgets.section_title("观测参数"))
        head.addStretch(1)
        reset = QPushButton("重置")
        reset.setObjectName("ghostBtn")
        reset.clicked.connect(self._reset_params)
        head.addWidget(reset)
        layout.addLayout(head)
        layout.addSpacing(9)

        layout.addWidget(widgets.role_label("目标日期", "ink3"))
        layout.addSpacing(6)
        self._date_tabs = SegmentedControl(["今天", "明天", "自定义"])
        self._date_tabs.valueChanged.connect(self._on_date_tab)
        layout.addWidget(self._date_tabs)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(datetime.now().date())
        self.date_edit.setVisible(False)
        layout.addWidget(self.date_edit)
        layout.addSpacing(9)

        layout.addWidget(widgets.role_label("观测现象", "ink3"))
        layout.addSpacing(6)
        phen = QHBoxLayout()
        phen.setSpacing(8)
        self.dawn_btn = QPushButton("朝霞")
        self.dusk_btn = QPushButton("晚霞")
        self.dusk_btn.setChecked(True)
        self.dusk_btn.setProperty("active", "true")
        self.dawn_btn.setProperty("active", "false")
        theme.repolish(self.dawn_btn)
        theme.repolish(self.dusk_btn)
        for button in (self.dawn_btn, self.dusk_btn):
            button.setObjectName("chipBtn")
            button.setCheckable(True)
            button.setFixedHeight(36)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dusk_btn.setChecked(True)
        self._phenomenon_group = QButtonGroup(self)
        self._phenomenon_group.setExclusive(True)
        self._phenomenon_group.addButton(self.dawn_btn, 0)
        self._phenomenon_group.addButton(self.dusk_btn, 1)
        self.dawn_btn.clicked.connect(lambda: self._on_phenomenon(0))
        self.dusk_btn.clicked.connect(lambda: self._on_phenomenon(1))
        phen.addWidget(self.dawn_btn, 1)
        phen.addWidget(self.dusk_btn, 1)
        layout.addLayout(phen)
        layout.addSpacing(9)

        context = QFrame()
        context.setFrameShape(QFrame.Shape.NoFrame)
        context.setProperty("plain", "true")
        context_l = QVBoxLayout(context)
        context_l.setContentsMargins(9, 7, 9, 7)
        context_l.setSpacing(5)
        self._context_label = widgets.role_label("", "hint")
        self._context_label.setParent(card)
        self._context_label.setWordWrap(True)
        self._context_label.hide()
        toggles = QHBoxLayout()
        self.edit_button = QPushButton("编辑坐标")
        self.edit_button.setObjectName("ghostBtn")
        self.edit_button.clicked.connect(self._toggle_editor)
        log_btn = QPushButton("▤  运行日志")
        log_btn.setObjectName("ghostBtn")
        log_btn.clicked.connect(self._toggle_log)
        toggles.addWidget(self.edit_button)
        toggles.addWidget(log_btn)
        toggles.addStretch(1)
        context_l.addLayout(toggles)
        layout.addWidget(context)

        # 坐标微调（默认折叠；保留原有字段供数据源等模块读取）
        self._editor = QWidget()
        editor_l = QGridLayout(self._editor)
        editor_l.setContentsMargins(0, 8, 0, 0)
        editor_l.setSpacing(5)
        self.name_edit = QLineEdit("杭州")
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90.0, 90.0)
        self.lat_spin.setDecimals(4)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180.0, 180.0)
        self.lon_spin.setDecimals(4)
        self.elev_spin = QDoubleSpinBox()
        self.elev_spin.setRange(-500.0, 9000.0)
        self.elev_spin.setDecimals(1)
        self.tz_edit = QLineEdit("Asia/Shanghai")
        self.elev_source_label = widgets.role_label("海拔手动输入", "hint")
        for row, (text, editor) in enumerate((
            ("名称", self.name_edit), ("纬度", self.lat_spin),
            ("经度", self.lon_spin), ("海拔 (m)", self.elev_spin),
            ("时区 (IANA)", self.tz_edit),
        )):
            editor_l.addWidget(widgets.role_label(text, "hint"), row, 0)
            editor_l.addWidget(editor, row, 1)
        editor_l.addWidget(self.elev_source_label, 5, 1)
        apply_btn = QPushButton("应用坐标到两页")
        apply_btn.setObjectName("ghostBtn")
        apply_btn.clicked.connect(self._apply_editor)
        editor_l.addWidget(apply_btn, 6, 0, 1, 2)
        self._editor.setVisible(False)
        layout.addWidget(self._editor)

        self.run_button = RunButton("开始预测")
        self.run_button.setFixedHeight(41)
        self.run_button.clicked.connect(self._run_forecast)

        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setObjectName("ghostBtn")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)

        self.progress_stage = widgets.role_label("", "hint")
        self.progress_stage.setWordWrap(True)
        self.progress_stage.setVisible(False)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(96)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        return card

    def _build_hero(self) -> GlassCard:
        card = GlassCard()
        card.setMinimumHeight(150)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(6)
        kicker_row = QHBoxLayout()
        kicker_row.setSpacing(8)
        self._hero_kicker = widgets.role_label("杭州 · 今日晚霞", "ink3")
        self._hero_kicker.setWordWrap(True)
        self._hero_badge = widgets.chip("等待预测")
        kicker_row.addWidget(self._hero_kicker, 1)
        kicker_row.addWidget(self._hero_badge)
        layout.addLayout(kicker_row)

        score_row = QHBoxLayout()
        score_row.setSpacing(10)
        self.quality_big = widgets.role_label("—", "heroScore")
        self.quality_big.setStyleSheet(f"color: {theme.palette()['faint']};")
        self._has_result = False
        score_row.addWidget(self.quality_big)
        maximum = widgets.role_label("/ 2.5", "subtitle")
        score_row.addWidget(maximum)
        self.quality_level = widgets.chip("等待预测")
        score_row.addWidget(self.quality_level)
        score_row.addStretch(1)
        left.addLayout(score_row)
        self._hero_caption = widgets.role_label("峰值质量指数 · 运行预测后显示", "hint")
        left.addWidget(self._hero_caption)
        left.addStretch(1)
        body.addLayout(left, 1)

        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {theme.palette()['border']};")
        self._hero_divider = divider
        body.addWidget(divider)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(widgets.role_label("最佳观赏时刻", "ink3"))
        self._peak_time = widgets.role_label("—", "heroTime")
        right.addWidget(self._peak_time)
        self._peak_window = widgets.role_label("推荐窗口 —", "ink2")
        right.addWidget(self._peak_window)
        sunline = QHBoxLayout()
        sunline.setSpacing(4)
        self._sunline_inactive: list[QWidget] = []
        for index in range(6):
            bar = QWidget()
            bar.setFixedHeight(3)
            bar.setMinimumWidth(8)
            if index < 4:
                bar.setStyleSheet(
                    "background: " + ("#f3ad4e" if index == 0 else
                                      "#ed7745" if index == 1 else
                                      "#e2653f" if index == 2 else "#df5242")
                    + "; border-radius: 2px;"
                )
            else:
                bar.setStyleSheet(
                    f"background: {theme.palette()['border_strong']}; border-radius: 2px;"
                )
                self._sunline_inactive.append(bar)
            sunline.addWidget(bar, 1)
        self._sunline_label = widgets.role_label("", "hint")
        sunline.addWidget(self._sunline_label)
        right.addLayout(sunline)

        # HTML 完整报告入口：file:// 优先（桌面端不起 HTTP 服务），见
        # _open_html_report；仅当 payload 带可用报告路径时显示按钮。
        self._report_button = QPushButton("📄  完整报告")
        self._report_button.setObjectName("chipBtn")
        self._report_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._report_button.setVisible(False)
        self._report_button.clicked.connect(self._open_html_report)
        report_row = QHBoxLayout()
        report_row.addWidget(self._report_button)
        report_row.addStretch(1)
        right.addLayout(report_row)
        right.addStretch(1)
        body.addLayout(right, 1)
        layout.addLayout(body)
        return card

    def _build_metrics(self) -> QGridLayout:
        """关键指标网格：三列，避免末行孤立指标。"""
        grid = QGridLayout()
        grid.setSpacing(10)
        self._metric_cards: dict[str, MetricCard] = {}
        metrics = (
            ("burnable", "有效可烧云量", "前向可烧云占视野比例"),
            ("material", "云质厚度", "云体材质负载（光学厚度代理）"),
            ("brightness", "亮度色彩", "霞光亮度与色彩评分"),
            ("aod", "AOD 550", "气溶胶光学厚度 · 通透度"),
            ("incoming", "入射受光", "太阳入射照亮云体的因子"),
            ("contrast", "返程对比度", "观测者视线大气对比因子"),
            ("duration", "持续时长", "核心观赏窗口 · 分钟"),
            ("confidence", "置信度", "引擎结果置信评分"),
            ("disagreement", "模式分歧", "多模式结论分歧度（>0.3 警示）"),
        )
        for index, (key, title, sub) in enumerate(metrics):
            card = MetricCard(title)
            card.set_sub(sub)
            grid.addWidget(card, index // 3, index % 3)
            self._metric_cards[key] = card
        return grid

    def _build_chart_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("霞光质量变化"))
        titles.addWidget(widgets.role_label("每 5 分钟更新 · 峰值区间已高亮", "hint"))
        head.addLayout(titles)
        head.addStretch(1)
        head.addWidget(widgets.chip("●  质量指数", "accent"))
        head.addWidget(widgets.chip("●  最佳窗口"))
        layout.addLayout(head)
        self.curve = QualityCurve(164)
        layout.addWidget(self.curve)
        return card

    def _build_profile_card(self) -> GlassCard:
        """模式大气剖面卡：太阳方向压力层剖面 + 显示字段切换。"""
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("太阳方向大气剖面"))
        titles.addWidget(widgets.role_label(
            "峰值时刻 · 压力层几何高度 · 云量/湿度/温度着色", "hint"
        ))
        head.addLayout(titles)
        head.addStretch(1)
        self._profile_field_combo = QComboBox()
        self._profile_field_combo.addItem("云量", FIELD_CLOUD)
        self._profile_field_combo.addItem("相对湿度", FIELD_RH)
        self._profile_field_combo.addItem("温度", FIELD_TEMP)
        self._profile_field_combo.setFixedWidth(110)
        self._profile_field_combo.currentIndexChanged.connect(
            self._on_profile_field_changed
        )
        head.addWidget(self._profile_field_combo)
        layout.addLayout(head)
        self.profile_canvas = NationalProfileCanvas()
        layout.addWidget(self.profile_canvas)
        return card

    def _on_profile_field_changed(self, _index: int) -> None:
        self.profile_canvas.set_display_field(
            self._profile_field_combo.currentData() or FIELD_CLOUD
        )

    def _build_genus_card(self) -> GlassCard:
        """云属与色彩诊断卡。"""
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("云属与色彩诊断"))
        titles.addWidget(widgets.role_label(
            "压力族 + 相态 + 几何代理的云属推断 · 色彩诊断不改变质量分", "hint"
        ))
        head.addLayout(titles)
        head.addStretch(1)
        self._genus_status_chip = widgets.chip("预测后显示")
        head.addWidget(self._genus_status_chip)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)
        self._genus_main = widgets.role_label("—", "metric")
        self._genus_main.setStyleSheet(f"color: {theme.palette()['faint']};")
        self._genus_affinity = widgets.role_label("", "hint")
        self._genus_affinity.setWordWrap(True)
        self._genus_confidence = widgets.role_label("", "ink3")
        self._genus_candidates = widgets.role_label("", "ink2")
        self._genus_candidates.setWordWrap(True)
        self._color_value = widgets.role_label("—", "metric")
        self._color_value.setStyleSheet(f"color: {theme.palette()['faint']};")
        self._color_confidence = widgets.role_label("", "ink3")
        self._regime_value = widgets.role_label("—", "metric")
        self._regime_value.setStyleSheet(f"color: {theme.palette()['faint']};")
        self._family_value = widgets.role_label("", "ink2")
        self._phase_value = widgets.role_label("", "ink2")

        grid.addWidget(widgets.role_label("主要云属（模型代理）", "ink3"), 0, 0)
        grid.addWidget(self._genus_main, 1, 0)
        grid.addWidget(self._genus_confidence, 2, 0)
        grid.addWidget(self._genus_candidates, 3, 0)
        grid.addWidget(self._genus_affinity, 4, 0)

        grid.addWidget(widgets.role_label("预期霞光颜色", "ink3"), 0, 1)
        grid.addWidget(self._color_value, 1, 1)
        grid.addWidget(self._color_confidence, 2, 1)
        grid.addWidget(self._phase_value, 3, 1)

        grid.addWidget(widgets.role_label("主导云场结构", "ink3"), 0, 2)
        grid.addWidget(self._regime_value, 1, 2)
        grid.addWidget(self._family_value, 2, 2)
        layout.addLayout(grid)
        return card

    def _build_boundary_card(self) -> GlassCard:
        """边界与时间窗卡：boundary_uncertainty + timing_window + 演变趋势。"""
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("边界与时间窗"))
        titles.addWidget(widgets.role_label(
            "目标云边界位置敏感度 · 质量曲线插值时间窗 · 云演变趋势", "hint"
        ))
        head.addLayout(titles)
        head.addStretch(1)
        self._boundary_stability_chip = widgets.chip("预测后显示")
        head.addWidget(self._boundary_stability_chip)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(7)
        self._boundary_rows: dict[str, list[QWidget]] = {}
        boundary_items = (
            ("range", "可能位置"),
            ("width", "位置跨度"),
            ("impact", "判断影响"),
        )
        for col, (key, title) in enumerate(boundary_items):
            value = widgets.role_label("—", "metric")
            value.setStyleSheet(f"color: {theme.palette()['faint']};")
            note = widgets.role_label("", "ink2")
            note.setWordWrap(True)
            grid.addWidget(widgets.role_label(title, "ink3"), 0, col)
            grid.addWidget(value, 1, col)
            grid.addWidget(note, 2, col)
            self._boundary_rows[key] = [value, note]

        time_items = (
            ("start", "窗口开始"),
            ("peak", "窗口峰值"),
            ("end", "窗口结束"),
            ("duration", "窗口时长"),
        )
        self._timing_rows: dict[str, QLabel] = {}
        for col, (key, title) in enumerate(time_items):
            label = QLabel("—")
            label.setProperty("role", "ink2")
            grid.addWidget(widgets.role_label(title, "ink3"), 3, col)
            grid.addWidget(label, 4, col)
            self._timing_rows[key] = label
        layout.addLayout(grid)

        trend_row = QHBoxLayout()
        trend_row.setSpacing(10)
        self._trend_value = widgets.role_label("—", "ink2")
        self._trend_note = widgets.role_label("", "hint")
        trend_row.addWidget(widgets.role_label("云演变趋势", "ink3"))
        trend_row.addWidget(self._trend_value)
        trend_row.addWidget(self._trend_note, 1)
        layout.addLayout(trend_row)
        return card

    def _build_provenance_card(self) -> GlassCard:
        """数据溯源折叠卡：周期 / 时效清单 / CAMS 文件列表（只读展示）。

        字段以实际 payload 为准：顶层 data_provenance（cycle_utc / files /
        cams_files / cams_valid_times_utc 等）+ model_cycles +
        cross_section_request。Web 契约里的 input_manifest 只出现在区域
        payload，单点 payload 没有。
        """
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 15, 17, 15)
        layout.setSpacing(8)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("数据溯源"))
        titles.addWidget(widgets.role_label(
            "预测输入清单 · GFS/CAMS 缓存命中与文件列表（只读）", "hint"
        ))
        head.addLayout(titles)
        head.addStretch(1)
        self._provenance_toggle = QPushButton("展开")
        self._provenance_toggle.setObjectName("ghostBtn")
        self._provenance_toggle.clicked.connect(self._toggle_provenance)
        head.addWidget(self._provenance_toggle)
        layout.addLayout(head)

        self.provenance_view = QTextEdit()
        self.provenance_view.setProperty("role", "log")
        self.provenance_view.setReadOnly(True)
        self.provenance_view.setFixedHeight(180)
        self.provenance_view.setVisible(False)
        layout.addWidget(self.provenance_view)
        return card

    def _toggle_provenance(self) -> None:
        visible = not self.provenance_view.isVisible()
        self.provenance_view.setVisible(visible)
        self._provenance_toggle.setText("收起" if visible else "展开")

    def _build_table_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(17, 14, 17, 10)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("逐五分钟台账"))
        titles.addWidget(widgets.role_label(
            "物理指标与霞光质量 · 双向滚动查看全部时刻", "hint"
        ))
        head.addLayout(titles)
        head.addStretch(1)
        detail = QPushButton("查看运行日志 →")
        detail.setObjectName("ghostBtn")
        detail.clicked.connect(self._toggle_log)
        head.addWidget(detail)
        layout.addLayout(head)

        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(
            ["时刻", "质量", "档位", "强度", "范围", "鲜艳度", "可见度",
             "可烧云量", "太阳仰角", "太阳方位", "AOD", "云属"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(220)
        self.table.setMaximumHeight(320)
        layout.addWidget(self.table, 1)
        return card

    # ── 观测点（共享状态）────────────────────────────────────────────────
    def _apply_spot(self, loc: Location) -> None:
        """SpotStore → 本页所有展示与表单字段。"""
        self._syncing = True
        self.name_edit.setText(loc.name)
        self.lat_spin.setValue(loc.lat)
        self.lon_spin.setValue(loc.lon)
        self.elev_spin.setValue(loc.elevation_m)
        self.tz_edit.setText(loc.timezone_name)
        self.elev_source_label.setText(
            "海拔 DEM 自动查询" if loc.elevation_source == "dem" else "海拔手动输入"
        )
        self._syncing = False

        self._spot_name_label.setText(loc.name)
        self._spot_coord_label.setText(loc.coord_text())
        self._elev_chip.setText(f"海拔 {widgets.format_elevation_m(loc.elevation_m)}")
        self._tz_chip.setText(loc.utc_offset_label())
        self._dem_chip.setText("DEM 已补齐" if loc.elevation_source == "dem"
                               else "DEM 自动补齐")
        self._context_label.setText(
            f"当前点位 {loc.name} · {loc.coord_text()} · "
            f"海拔 {widgets.format_elevation_m(loc.elevation_m)}"
        )
        self._sync_mini_map(loc)
        self.location_list.blockSignals(True)
        for index in range(self.location_list.count()):
            item = self.location_list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            item.setSelected(
                isinstance(data, Location) and data.name == loc.name
                and abs(data.lat - loc.lat) < 1e-4
                and abs(data.lon - loc.lon) < 1e-4
            )
        self.location_list.blockSignals(False)
        self._refresh_favorite_state()
        self._refresh_result_state()

    def _on_spot_changed(self, loc: Location) -> None:
        self._apply_spot(loc)

    def _reload_location_list(self) -> None:
        self.location_list.blockSignals(True)
        self.location_list.clear()
        starred_first = sorted(
            enumerate(self._locations), key=lambda pair: (not pair[1].starred, pair[0])
        )
        for _, loc in starred_first:
            item = QListWidgetItem(f"{'★ ' if loc.starred else ''}{loc.name}"
                                   f"    {loc.coord_label()}")
            item.setData(Qt.ItemDataRole.UserRole, loc)
            self.location_list.addItem(item)
        self.location_list.blockSignals(False)

    def _on_location_clicked(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        loc = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(loc, Location):
            self._store.set_location(loc)

    def _on_spot_tab(self, index: int, _label: str) -> None:
        self._spot_views.setCurrentIndex(index)

    def _on_date_tab(self, index: int, _label: str) -> None:
        today = datetime.now().date()
        if index != 2:
            self.date_edit.setDate(today + timedelta(days=index))
        self.date_edit.setVisible(index == 2)
        self._refresh_result_state()

    def _on_phenomenon(self, index: int) -> None:
        for i, button in enumerate((self.dawn_btn, self.dusk_btn)):
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)
        self._refresh_result_state()

    def _reset_params(self) -> None:
        self._date_tabs.set_index(0)
        self.dusk_btn.setChecked(True)
        self._on_phenomenon(1)

    def _toggle_editor(self) -> None:
        expanded = self._editor.isHidden()
        self._editor.setVisible(expanded)
        self.edit_button.setText("收起坐标" if expanded else "编辑坐标")

    def _toggle_log(self) -> None:
        self.log_view.setVisible(not self.log_view.isVisible())

    def _apply_editor(self) -> None:
        self._store.set_location(self._current_location())
        self._editor.hide()
        self.edit_button.setText("编辑坐标")

    def _context(self) -> tuple:
        loc = self._store.location()
        return (loc.name, loc.lat, loc.lon, loc.elevation_m, loc.timezone_name,
                self.date_edit.date().toString("yyyy-MM-dd"), self._phenomenon(),
                "both")

    @staticmethod
    def _context_title(context: tuple) -> str:
        return (f"{context[0]} · {context[5]} "
                f"{'朝霞' if context[6] == 'dawn' else '晚霞'}")

    def _refresh_result_state(self, *_args) -> None:
        current = self._context()
        if not self._has_result:
            self._hero_kicker.setText(self._context_title(current))
        if self._worker is not None:
            text, tone = "正在预测", "accent"
        elif self._has_result:
            stale = self._result_context != current
            text, tone = ("参数已变更 · 待重算", "warn") if stale else ("预测完成", "ok")
            if not stale and self._model_errors:
                text, tone = "部分完成", "warn"
        else:
            text, tone = "等待预测", ""
        self._hero_badge.setText(text)
        self._hero_badge.setProperty("tone", tone)
        theme.repolish(self._hero_badge)

    def _open_map_picker(self) -> None:
        if self._on_pick_map is not None:
            self._on_pick_map()

    def _sync_mini_map(self, loc: Location) -> None:
        """共享观测点 → 内嵌地图；兼容 MiniMap 降级实现。"""
        if FireSkyMap is not None and isinstance(self._mini_map, FireSkyMap):
            self._mini_map.set_view(loc.lat, loc.lon, MAP_PICK_ZOOM, loc.name)
        else:
            self._mini_map.set_location(loc)  # type: ignore[attr-defined]

    # ── 地名搜索 ──────────────────────────────────────────────────────────
    def _on_search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            return
        if self._search_worker is not None and self._search_worker.isRunning():
            self._search_worker.cancel()
        self.search_btn.setEnabled(False)
        self._search_status.setVisible(True)
        self._search_status.setText(f"正在搜索「{query}」…")
        worker = LocationSearchWorker(query)
        worker.setParent(self)
        worker.finished_ok.connect(self._on_search_done)
        worker.failed.connect(self._on_search_failed)
        worker.start()
        self._search_worker = worker

    def _on_search_done(self, result: object) -> None:
        self.search_btn.setEnabled(True)
        if not isinstance(result, dict):
            return
        lat = float(result.get("lat") or 0.0)
        lon = float(result.get("lon") or 0.0)
        display = str(result.get("display_name") or result.get("name") or "观测点")
        name = str(result.get("name") or display.split(",")[0].strip() or "观测点")
        elevation = result.get("elevation_m")
        timezone_name = result.get("timezone_name")
        loc = Location(
            name=name[:60],
            lat=round(lat, 4),
            lon=round(lon, 4),
            elevation_m=(
                float(elevation) if elevation is not None
                else Location.UNQUERIED_ELEVATION_M
            ),
            timezone_name=str(timezone_name) if timezone_name
            else widgets.timezone_for_point(lat, lon),
            elevation_source="dem" if elevation is not None else "manual",
        )
        self._store.set_location(loc)
        self._spot_tabs.set_index(0, emit=False)
        self._spot_views.setCurrentIndex(0)
        if loc.elevation_m <= Location.UNQUERIED_ELEVATION_M:
            self._search_status.setText(f"已定位 {display} · 正在补齐 DEM 海拔…")
            self._log(f"地名搜索命中：{display} → {loc.coord_text()} · 正在查询海拔…")
            self._query_elevation(lat, lon)
        else:
            self._search_status.setText(
                f"已定位 {display} · 海拔 {loc.elevation_m:.0f} m"
            )
            self._log(f"地名搜索命中：{display} → {loc.coord_text()} · "
                      f"海拔 {loc.elevation_m:.0f} m（含元数据）")

    def _on_search_failed(self, message: str) -> None:
        self.search_btn.setEnabled(True)
        self._search_status.setText(f"未找到该地名：{message[:120]}")

    # ── 地图落点 + 海拔 ───────────────────────────────────────────────────
    def _on_mini_map_picked(self, lat: float, lon: float) -> None:
        loc = Location(
            name="自定义观测点", lat=lat, lon=lon,
            elevation_m=Location.UNQUERIED_ELEVATION_M,
            timezone_name=widgets.timezone_for_point(lat, lon),
            elevation_source="manual",
        )
        self._store.set_location(loc)
        self._log(f"地图落点 {lat:.4f}, {lon:.4f} · 正在查询 DEM 海拔…")
        self._query_elevation(lat, lon)

    def _query_elevation(self, lat: float, lon: float) -> None:
        if self._elev_worker is not None and self._elev_worker.isRunning():
            self._elev_worker.cancel()
        worker = ElevationQueryWorker(lat, lon)
        worker.setParent(self)
        worker.finished_ok.connect(self._on_elevation)
        worker.failed.connect(
            lambda msg: self._log("DEM 查询失败，可手动填写海拔")
        )
        worker.start()
        self._elev_worker = worker

    def _on_elevation(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        current = self._store.location()
        if (abs(float(result.get("lat") or 0) - current.lat) > 1e-4
                or abs(float(result.get("lon") or 0) - current.lon) > 1e-4):
            return
        elevation = float(result.get("elevation_m"))
        if not math.isfinite(elevation) or not -500 <= elevation <= 9000:
            self._log("DEM 返回无效海拔，请手动填写。")
            return
        current.elevation_m = elevation
        current.elevation_source = "dem"
        tz = str(result.get("timezone_name") or "").strip()
        if tz and current.timezone_name != tz:
            current.timezone_name = tz
            self._log(f"时区按引擎权威查询更新为 {tz}")
        self._store.set_location(current)
        self._log(f"海拔已补齐 {current.elevation_m:.0f} m"
                  f"（{result.get('dataset') or 'DEM'}）")

    # ── 收藏 ──
    def _current_location(self) -> Location:
        return Location(
            name=self.name_edit.text().strip() or "观测点",
            lat=float(self.lat_spin.value()),
            lon=float(self.lon_spin.value()),
            elevation_m=float(self.elev_spin.value()),
            timezone_name=self.tz_edit.text().strip() or "Asia/Shanghai",
            starred=False,
            elevation_source="dem" if "DEM" in self.elev_source_label.text() else "manual",
        )

    def _star_current(self) -> None:
        loc = self._store.location()
        loc.starred = True
        for index, existing in enumerate(self._locations):
            if abs(existing.lat - loc.lat) < 1e-4 and abs(existing.lon - loc.lon) < 1e-4:
                self._locations[index] = loc
                break
        else:
            self._locations.append(loc)
        save_locations(self._locations)
        self._reload_location_list()
        self._refresh_favorite_state()
        self._log(f"已收藏观测点：{loc.name}")

    def _remove_current(self) -> None:
        row = self.location_list.currentRow()
        if row < 0:
            self._log("请先在收藏列表中选中要删除的观测点。")
            return
        item = self.location_list.item(row)
        loc = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(loc, Location):
            return
        if QMessageBox.question(
            self, "删除收藏", f"从收藏中删除「{loc.name}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._locations = [
            x for x in self._locations
            if not (x.name == loc.name and abs(x.lat - loc.lat) < 1e-6
                    and abs(x.lon - loc.lon) < 1e-6)
        ]
        save_locations(self._locations)
        self._reload_location_list()
        self._refresh_favorite_state()
        self._log(f"已删除观测点：{loc.name}")

    def _refresh_favorite_state(self) -> None:
        loc = self._store.location()
        saved = any(
            item.starred and abs(item.lat - loc.lat) < 1e-4
            and abs(item.lon - loc.lon) < 1e-4
            for item in self._locations
        )
        self.favorite_button.setText("已收藏" if saved else "收藏当前点")
        self.favorite_button.setEnabled(not saved)
        self.remove_favorite_button.setEnabled(self.location_list.currentRow() >= 0)

    # ── 兼容旧接口 ──
    def accept_map_pick(self, lat: float, lon: float, elevation_m: float | None,
                        dataset: str | None) -> Location:
        loc = Location(
            name=f"点选 {lat:.4f},{lon:.4f}", lat=round(float(lat), 4),
            lon=round(float(lon), 4),
            elevation_m=(
                float(elevation_m)
                if elevation_m is not None
                else widgets.Location.UNQUERIED_ELEVATION_M
            ),
            timezone_name=widgets.timezone_for_point(lat, lon),
            elevation_source="dem" if elevation_m is not None else "unqueried",
        )
        self._store.set_location(loc)
        return loc

    def apply_elevation(self, elevation_m: float, dataset: str) -> None:
        loc = self._store.location()
        loc.elevation_m = float(elevation_m)
        loc.elevation_source = "dem"
        self._store.set_location(loc)
        self._log(f"海拔已更新 {elevation_m:.0f} m（{dataset}）")

    def attach_worker(self, worker: WorkerBase) -> None:
        worker.progress.connect(self._on_progress)
        worker.failed.connect(lambda msg: self._log(f"失败：{msg}"))

    def set_phenomenon(self, value: str) -> None:
        (self.dawn_btn if value == "dawn" else self.dusk_btn).click()

    def _phenomenon(self) -> str:
        return "dawn" if self.dawn_btn.isChecked() else "dusk"

    # ── 预测 ──────────────────────────────────────────────────────────────
    def _payload_for_data(self) -> dict:
        loc = self._current_location()
        return {
            "name": loc.name, "lat": loc.lat, "lon": loc.lon,
            "elevation_m": loc.elevation_m, "timezone_name": loc.timezone_name,
            "target_date": self.date_edit.date().toPython().isoformat(),
            "phenomenon": self._phenomenon(),
            "model_source": "both",
            "forecast_mode": "model", "satellite_source": "off", "workers": 1,
        }

    def _run_forecast(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        target = self.date_edit.date().toPython()
        if not isinstance(target, date_type):
            QMessageBox.warning(self, "FireSky", "目标日期无效。")
            return
        loc = self._current_location()
        if float(loc.elevation_m) <= Location.UNQUERIED_ELEVATION_M:
            # -1 是「DEM 未回填」哨兵：提交 -1 会被引擎当真实海拔参与
            # 辐射/云底计算，必须先补齐。
            QMessageBox.warning(
                self, "FireSky",
                "该点位海拔尚未获取（DEM 查询未完成或失败）。\n"
                "请展开「编辑坐标」手动填写海拔后重试；真实海平面观测点可填 0。",
            )
            return
        current = self._store.location()
        if any(getattr(current, key) != getattr(loc, key) for key in (
            "name", "lat", "lon", "elevation_m", "timezone_name", "elevation_source"
        )):
            self._store.set_location(loc)
        payload = {
            "name": loc.name,
            "lat": loc.lat,
            "lon": loc.lon,
            "elevation_m": loc.elevation_m,
            "timezone_name": loc.timezone_name,
            "target_date": target.isoformat(),
            "phenomenon": self._phenomenon(),
            "model_source": "both",
            "forecast_mode": "model",
            "satellite_source": "off",
            "workers": 1,
        }
        context = self._context()
        self.data_panel.begin(
            payload, callback=lambda prepared: self._start_worker(ForecastWorker(prepared)),
            is_current=lambda: self._context() == context,
        )
        self._log(f"创建预测任务：{loc.name} {payload['target_date']} "
                  f"{payload['phenomenon']} {payload['model_source']} · "
                  f"数据未缓存时将准备 GFS / EC / CAMS")

    def _start_worker(self, worker: ForecastWorker) -> None:
        self._running_context = self._context()
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("取消任务")
        self.cancel_button.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(2)
        self.progress_stage.setVisible(True)
        self.progress_stage.setText("正在计算 GFS / EC…")
        self._refresh_result_state()
        worker.start()

    def _cancel(self) -> None:
        self.data_panel.cancel()
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("正在取消…")
            self.progress_stage.setText("正在取消 · 等待引擎到达安全检查点")
            self._log("已请求取消（引擎将在下一个进度检查点停止）。")

    def _on_cancelled(self) -> None:
        """取消落地：复位控件（不弹窗、不当失败处理）。"""
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress_stage.setVisible(False)
        self._refresh_result_state()
        self._log("预测已取消。")

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    _STAGE_LABELS = {
        "download": "阶段一 · 准备数据",
        "score": "阶段二 · 评分计算",
        "prepare": "阶段一 · 准备数据",
        "cache": "阶段二 · 评分计算",
    }

    def _on_progress(self, event: dict) -> None:
        percent = int(event.get("percent") or 0)
        message = str(event.get("message") or "")
        stage = str(event.get("stage") or "")
        self.progress.setValue(max(2, min(100, percent)))
        if message:
            self._log(message)
            self.progress.setToolTip(message)
        stage_label = self._STAGE_LABELS.get(stage)
        if stage_label and self.progress_stage.isVisible() and self.cancel_button.isEnabled():
            model = str(event.get("model_source") or "").upper()
            self.progress_stage.setText(f"{model + ' · ' if model else ''}{stage_label} · {percent}%")

    def _on_done(self, result: object) -> None:
        if not isinstance(result, dict):
            self._on_failed("预测结果格式异常。")
            return
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(100)
        self.progress.setVisible(False)
        self.progress_stage.setVisible(False)
        self._result_context = self._running_context
        self._model_results = result.get("model_results", {})
        self._model_errors = result.get("model_errors", {})
        if self._model_results:
            model = self.model_results_bar.set_results(self._model_results, self._model_errors)
            self._show_model_result(model)
        else:
            self.model_results_bar.hide()
            self.render_result(result)
        for model, message in self._model_errors.items():
            self._log(f"{model.upper()} 未完成：{message}")
        self._log(f"完成：周期 {result.get('cycle_time_utc', '')}")

    def _show_model_result(self, model):
        if model in self._model_results:
            self.render_result(self._model_results[model])
            title = self._context_title(self._result_context) + f" · {model.upper()}"
            self._hero_kicker.setText(title)
            self._hero_kicker.setToolTip(title)

    # ── 结果渲染 ──────────────────────────────────────────────────────────
    def render_result(self, result: dict) -> None:
        """把单点扁平 payload 渲染到峰值卡 + 指标网格 + 曲线 + 诊断卡 + 台账。"""
        level = str(result.get("quality_level") or "无明显火烧云")
        peak = float(result.get("peak_quality_index") or 0.0)
        color = theme.level_color(level)
        self.quality_big.setText(f"{peak:.2f}")
        self.quality_big.setStyleSheet(f"color: {color};")
        self._has_result = True
        if self._result_context is None:
            self._result_context = self._context()
        self._hero_kicker.setText(self._context_title(self._result_context))
        self._hero_kicker.setToolTip(self._context_title(self._result_context))
        self.quality_level.setText(level)
        self.quality_level.setProperty("tone", "accent")
        theme.repolish(self.quality_level)
        self._hero_badge.setText("预测完成")
        self._hero_badge.setProperty("tone", "accent")
        theme.repolish(self._hero_badge)

        components = result.get("quality_components") or {}
        confidence = result.get("confidence_score")
        self._hero_caption.setText(
            f"峰值质量指数 · 置信度 {float(confidence):.0%}"
            if confidence is not None
            else "峰值质量指数 · 置信度未提供"
        )
        best_time = widgets.local_time_label(result.get("best_time_local"))
        self._peak_time.setText(best_time)

        timing_window = result.get("timing_window") or {}
        window_minutes = timing_window.get("duration_minutes")
        window_text = (
            f"推荐窗口 {widgets.local_time_label(timing_window.get('start_time_local'))}"
            f" – {widgets.local_time_label(timing_window.get('end_time_local'))}"
            if window_minutes
            else "推荐窗口未提供"
        )
        self._peak_window.setText(window_text)

        # 关键指标网格（Web MetricsGrid 同口径）
        def pct(value) -> str:
            if value is None:
                return "—"
            try:
                return f"{float(value) * 100:.0f}%"
            except (TypeError, ValueError):
                return str(value)

        def frac(value) -> str:
            if value is None:
                return "—"
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return str(value)

        burnable = components.get(
            "burnable_cloud_coverage_effective",
            result.get("burnable_cloud_coverage_effective"),
        )
        self._metric_cards["burnable"].set_value(pct(burnable))
        self._metric_cards["material"].set_value(frac(components.get("cloud_material")))
        self._metric_cards["brightness"].set_value(frac(components.get("brightness_color")))
        aod = components.get("quality_aod550", result.get("quality_aod550"))
        self._metric_cards["aod"].set_value(fmt(aod, "{:.2f}"))
        self._metric_cards["incoming"].set_value(
            frac(components.get("incoming_aod_illumination_factor"))
        )
        self._metric_cards["contrast"].set_value(
            frac(components.get("observer_aod_contrast_factor"))
        )
        duration = result.get("duration_minutes")
        if duration is None:
            duration = components.get("duration_minutes")
        self._metric_cards["duration"].set_value(fmt(duration, "{:.0f}"))
        self._metric_cards["confidence"].set_value(
            pct(confidence) if confidence is not None else "—"
        )

        # 曲线 + 峰值行
        steps = result.get("steps") or []
        curve_steps = [
            (
                widgets.local_time_label(s.get("valid_time_local")),
                float(s.get("instant_quality_index") or 0.0),
                bool(s.get("valid", True)),
            )
            for s in steps
        ]
        self.curve.set_steps(curve_steps)
        valid_pairs = [
            (i, v) for i, (_, v, ok) in enumerate(curve_steps)
            if ok and steps[i].get("physically_burnable")
        ]
        best_index = max(valid_pairs, key=lambda pair: pair[1])[0] if valid_pairs else None
        if best_index is not None and timing_window.get("duration_minutes"):
            self._sunline_label.setText(f"{float(timing_window['duration_minutes']):.0f} min")

        self._render_genus_card(result, best_index)
        self._render_boundary_card(result)
        self._render_profile(result)
        self._render_report_entry(result)
        self._render_disagreement(result)
        self._render_provenance(result)
        self._render_table(steps, best_index)
        self._refresh_result_state()

    def _render_profile(self, result: dict) -> None:
        """模式大气剖面：payload 的 cross_section 是峰值时刻剖面，直接用。"""
        self.profile_canvas.set_data(result.get("cross_section"))

    def _render_report_entry(self, result: dict) -> None:
        """📄 完整报告按钮：file:// 优先打开本地报告文件。

        契约核查（与引擎代码一致）：
        - html_report_url 是 Web 相对路径（/reports/...），需要 HTTP 服务；
          桌面端预测引擎不起服务，单点 GFS 路径返回空串。
        - html_report_path 是报告的绝对/相对文件路径（EC 与 nowcast 路径
          会写入；GFS 单点路径为 None）。
        桌面端用 QDesktopServices.openUrl(file://) 直接打开本地文件：
        url 缺失时从 html_report_path 解析，两者都缺则隐藏按钮。
        """
        import os

        url = str(result.get("html_report_url") or "").strip()
        path = str(result.get("html_report_path") or "").strip()
        file_path = ""
        if path:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            if candidate.is_file():
                file_path = str(candidate.resolve())
        if not file_path and url.startswith("/reports/"):
            # url 形如 /reports/<date>/<phenomenon>/<file>.html，落盘在
            # WebServerConfig.report_dir（默认 reports/web，见引擎契约）。
            candidate = Path.cwd() / "reports" / "web" / url[len("/reports/"):]
            if candidate.is_file():
                file_path = str(candidate.resolve())
        self._report_file_path = file_path
        self._report_button.setVisible(bool(file_path))
        if not file_path:
            self._log("提示：本次预测未生成 HTML 报告（GFS 单点路径不写报告文件）。")

    def _open_html_report(self) -> None:
        path = getattr(self, "_report_file_path", "")
        if not path:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _render_disagreement(self, result: dict) -> None:
        """模式分歧卡：model_disagreement_score > 0.3 橙色警示。"""
        card = self._metric_cards.get("disagreement")
        if card is None:
            return
        score = result.get("model_disagreement_score")
        if score is None:
            card.set_value("—")
            card.set_sub("引擎未返回分歧评分")
            return
        try:
            value = float(score)
        except (TypeError, ValueError):
            card.set_value("—")
            card.set_sub("分歧评分格式异常")
            return
        card.set_value(f"{value:.2f}")
        if value > 0.3:
            card.set_value(f"{value:.2f}", theme.palette()["warn"])
            card.set_sub("分歧偏高 · 结论对模式选择敏感")
        else:
            card.set_sub("分歧较低 · 结论稳定")

    def _render_provenance(self, result: dict) -> None:
        """数据溯源：周期 / 时效清单 / CAMS 文件列表（只读）。"""
        lines: list[str] = []

        def add(label: str, value) -> None:
            if value not in (None, ""):
                lines.append(f"{label}：{value}")

        provenance = result.get("data_provenance")
        if isinstance(provenance, dict) and provenance:
            add("模式", provenance.get("model_source"))
            add("数据周期（cycle_utc）", provenance.get("cycle_utc"))
            gfs_files = provenance.get("files") or []
            add("GFS 文件数", len(gfs_files) if gfs_files else None)
            add("GFS 缓存命中", "是" if provenance.get("gfs_cache_used") else "否")
            add("CAMS 缓存命中", "是" if provenance.get("cams_cache_used") else "否")
            cams_files = provenance.get("cams_files") or []
            add("CAMS 文件数", len(cams_files) if cams_files else None)
            cams_times = provenance.get("cams_valid_times_utc") or []
            add("CAMS 时效数", len(cams_times) if cams_times else None)
            if gfs_files:
                lines.append("GFS 文件清单：")
                lines.extend(f"  · {item}" for item in gfs_files[:12])
                if len(gfs_files) > 12:
                    lines.append(f"  · …共 {len(gfs_files)} 个文件")
            if cams_files:
                lines.append("CAMS 文件清单：")
                lines.extend(f"  · {item}" for item in cams_files[:12])
                if len(cams_files) > 12:
                    lines.append(f"  · …共 {len(cams_files)} 个文件")
            if cams_times:
                lines.append(f"CAMS 时效（UTC）：{', '.join(str(t) for t in cams_times[:10])}"
                             + ("…" if len(cams_times) > 10 else ""))
        model_cycles = result.get("model_cycles")
        if isinstance(model_cycles, dict) and model_cycles:
            add("模式周期", "；".join(
                f"{model} {cycle}" for model, cycle in model_cycles.items()
            ))
        request = result.get("cross_section_request")
        if isinstance(request, dict) and request:
            add("剖面请求区域", request.get("regions"))
            add("剖面网格距", f"{request.get('spacing_deg')}°"
                if request.get("spacing_deg") is not None else None)
        if not lines:
            lines.append("本次预测未返回输入溯源信息。")
        self.provenance_view.setPlainText("\n".join(lines))

    def _render_genus_card(self, result: dict, best_index: int | None) -> None:
        """云属与色彩诊断卡。"""
        genus = result.get("cloud_genus_proxy")
        if isinstance(genus, dict) and genus:
            genus_label = label_for(GENUS_LABELS, genus.get("label"), "—")
            self._genus_main.setText(genus_label)
            self._genus_main.setStyleSheet("")
            status = str(genus.get("status") or "")
            confidence = genus.get("confidence")
            conf_text = (
                f"置信度 {float(confidence):.0%}" if confidence is not None else ""
            )
            self._genus_confidence.setText(
                " · ".join(filter(None, (conf_text, status)))
                or "推断完成"
            )
            candidates = genus.get("candidate_scores") or {}
            if candidates:
                ranked = sorted(
                    candidates.items(), key=lambda kv: -float(kv[1] or 0.0)
                )[:3]
                text = "候选：" + " · ".join(
                    f"{label_for(GENUS_LABELS, key)} {float(value):.0%}"
                    for key, value in ranked
                )
                self._genus_candidates.setText(text)
            else:
                self._genus_candidates.setText("")
            compatible = genus.get("compatible_genera") or []
            if compatible:
                self._genus_candidates.setText(
                    (self._genus_candidates.text() + " ｜ 兼容："
                     + " / ".join(label_for(GENUS_LABELS, g) for g in compatible)).strip(" ｜")
                )
            self._genus_affinity.setText(
                theme.GENUS_BURN_AFFINITY.get(str(genus.get("label") or ""), "")
            )
            self._genus_status_chip.setText(
                "云属推断 · 代理" if status == "proxy" else "云属推断"
            )
            self._genus_status_chip.setProperty("tone", "accent")
            theme.repolish(self._genus_status_chip)
        else:
            self._genus_main.setText("—")
            self._genus_confidence.setText("")
            self._genus_candidates.setText("")
            self._genus_affinity.setText("")

        color_diag = result.get("color_diagnosis")
        steps_list = result.get("steps") or []
        step_color = (
            steps_list[best_index].get("color_diagnosis")
            if best_index is not None and best_index < len(steps_list) else None
        )
        color_payload = (
            color_diag if isinstance(color_diag, dict) and color_diag
            else step_color if isinstance(step_color, dict) and step_color
            else None
        )
        if isinstance(color_payload, dict) and color_payload:
            self._color_value.setText(
                label_for(COLOR_DIAGNOSIS_LABELS, color_payload.get("type"), "未知")
            )
            self._color_value.setStyleSheet("")
            color_confidence = color_payload.get("confidence")
            self._color_confidence.setText(
                f"置信度 {float(color_confidence):.0%}"
                if color_confidence is not None else ""
            )
            phase = color_payload.get("cloud_phase") or result.get(
                "dominant_burnable_cloud_phase"
            )
            self._phase_value.setText(
                "云相态：" + label_for(theme.CLOUD_PHASE_LABELS, phase, "—")
            )
        else:
            self._color_value.setText("—")
            self._color_confidence.setText("")
            self._phase_value.setText("")

        regime = result.get("selected_regime")
        self._regime_value.setText(
            label_for(REGIME_LABELS, regime, "无可烧目标云")
            if result.get("dominant_burnable_pressure_family")
            else "无可烧目标云"
        )
        self._regime_value.setStyleSheet("")
        self._family_value.setText(
            "压力族：" + label_for(
                PRESSURE_FAMILY_LABELS, result.get("dominant_burnable_pressure_family"), "—"
            ) if result.get("dominant_burnable_pressure_family") else ""
        )

    def _render_boundary_card(self, result: dict) -> None:
        """边界与时间窗卡。"""
        boundary = result.get("boundary_uncertainty")
        if isinstance(boundary, dict) and boundary:
            lower = boundary.get("lower_distance_km")
            upper = boundary.get("upper_distance_km")
            width = boundary.get("width_km")
            rows = self._boundary_rows
            if lower is not None and upper is not None:
                rows["range"][0].setText(f"{float(lower):.0f}–{float(upper):.0f} km")
                rows["range"][0].setStyleSheet("")
            if width is not None:
                rows["width"][0].setText(f"{float(width):.0f} km")
                rows["width"][0].setStyleSheet("")
            stable = bool(boundary.get("classification_stable"))
            rows["impact"][0].setText(
                "边界移动不改变结论" if stable else "边界移动可能改变结论"
            )
            rows["impact"][0].setStyleSheet("")
            flip = boundary.get("nearest_flip_displacement_km")
            extra = (
                f"移动约 {float(flip):.0f} km 可改变结论" if flip is not None else ""
            )
            rows["impact"][1].setText(extra)
            source = str(boundary.get("source") or "")
            rows["range"][1].setText(
                "多个候选目标给出的边界位置范围"
                if source == "target-candidate-envelope"
                else "模式从有云到晴空的过渡位置范围"
            )
            self._boundary_stability_chip.setText(
                label_for(BOUNDARY_STABILITY_LABELS, stable)
            )
            self._boundary_stability_chip.setProperty(
                "tone", "ok" if stable else "accent"
            )
            theme.repolish(self._boundary_stability_chip)
        else:
            for key in ("range", "width", "impact"):
                self._boundary_rows[key][0].setText("—")
                self._boundary_rows[key][1].setText("")
            self._boundary_stability_chip.setText("无边界敏感度数据")
            self._boundary_stability_chip.setProperty("tone", "")
            theme.repolish(self._boundary_stability_chip)

        timing = result.get("timing_window")
        if isinstance(timing, dict) and timing:
            self._timing_rows["start"].setText(
                widgets.local_time_label(timing.get("start_time_local"), True)
            )
            self._timing_rows["peak"].setText(
                widgets.local_time_label(timing.get("peak_time_local"), True)
            )
            self._timing_rows["end"].setText(
                widgets.local_time_label(timing.get("end_time_local"), True)
            )
            duration = timing.get("duration_minutes")
            self._timing_rows["duration"].setText(
                f"{float(duration):.0f} 分钟" if duration is not None else "—"
            )
        else:
            for label in self._timing_rows.values():
                label.setText("—")

        evolution = result.get("cloud_evolution")
        if isinstance(evolution, dict) and evolution:
            trend = str(evolution.get("trend") or "unknown")
            self._trend_value.setText(label_for(TREND_LABELS, trend, "演变未知"))
            change = evolution.get("target_score_change")
            note = (
                f"目标评分变化 {float(change) * 100:+.1f}%"
                if change is not None else ""
            )
            self._trend_note.setText(note)
        else:
            self._trend_value.setText("—")
            self._trend_note.setText("")

    def _render_table(self, steps: list, best_index: int | None) -> None:
        """12 列台账：时刻/质量/档位/强度/范围/鲜艳度/可见度/可烧云量/
        太阳仰角/太阳方位/AOD/云属——字段全部来自 steps。"""
        self.table.setRowCount(0)
        for index, s in enumerate(steps):
            row = self.table.rowCount()
            self.table.insertRow(row)
            stamp = widgets.local_time_label(s.get("valid_time_local"))
            if index == best_index:
                stamp = f"▶ {stamp}（峰值）"
            instant = float(s.get("instant_quality_index") or 0.0)
            step_level = str(
                s.get("instant_quality_level") or theme_and_level(instant)
            )
            row_color = QColor(theme.level_color(step_level))
            quality_item = QTableWidgetItem(f"{instant:.3f}")
            quality_item.setForeground(row_color)
            level_item = QTableWidgetItem(step_level)
            level_item.setForeground(row_color)
            burnable_cov = s.get("burnable_cloud_coverage")
            burnable_text = (
                f"{float(burnable_cov) * 100:.0f}%"
                if burnable_cov is not None else "—"
            )
            burnable_item = QTableWidgetItem(burnable_text)
            if s.get("physically_burnable"):
                burnable_item.setForeground(QColor(theme.palette()["ok"]))
            else:
                burnable_item.setForeground(QColor(theme.palette()["faint"]))
            genus_step = s.get("cloud_genus_proxy")
            genus_text = (
                label_for(GENUS_LABELS, genus_step.get("label"), "—")
                if isinstance(genus_step, dict) and genus_step.get("label")
                else label_for(GENUS_LABELS, s.get("dominant_burnable_pressure_family"),
                               "—") if s.get("dominant_burnable_pressure_family") else "—"
            )
            cells = (
                QTableWidgetItem(stamp),
                quality_item,
                level_item,
                QTableWidgetItem(fmt(s.get("intensity_score"), "{:.2f}")),
                QTableWidgetItem(fmt(s.get("extent_score"), "{:.2f}")),
                QTableWidgetItem(fmt(s.get("vividness_score"), "{:.2f}")),
                QTableWidgetItem(fmt(s.get("visibility_score"), "{:.2f}")),
                burnable_item,
                QTableWidgetItem(fmt(s.get("solar_elevation_deg"), "{:.1f}°")),
                QTableWidgetItem(fmt(s.get("solar_azimuth_deg"), "{:.1f}°")),
                QTableWidgetItem(fmt(s.get("quality_aod550"), "{:.2f}")),
                QTableWidgetItem(genus_text),
            )
            for col, cell in enumerate(cells):
                self.table.setItem(row, col, cell)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.progress_stage.setVisible(False)
        self._refresh_result_state()
        self._log(f"失败：{message}")
        QMessageBox.warning(self, "预测失败", message)

    def on_theme_changed(self) -> None:
        if not self._has_result:
            self.quality_big.setStyleSheet(f"color: {theme.palette()['faint']};")
        self._hero_divider.setStyleSheet(
            f"background: {theme.palette()['border']};"
        )
        for bar in self._sunline_inactive:
            bar.setStyleSheet(
                f"background: {theme.palette()['border_strong']}; border-radius: 2px;"
            )
        self._mini_map.update()
        self.curve.update()
        self.profile_canvas.on_theme_changed()

    def shutdown(self) -> None:
        self.data_panel.shutdown()
        for worker in self.findChildren(ElevationQueryWorker) + self.findChildren(LocationSearchWorker):
            if worker.isRunning():
                worker.cancel()
                worker.wait()
        for worker in (self._worker, self._elev_worker, self._search_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
                # 不带超时等待收尾：QThread 带析构运行中线程会 abort 进程。
                worker.wait()


def theme_and_level(value: float) -> str:
    """指数 → 档位名（与引擎阈值一致）。"""
    # 与引擎 fire_cloud_quality_level 相同的前置守卫：非有限值视为无现象。
    if not math.isfinite(value):
        return "无明显火烧云"
    if value < 0.001:
        return "无明显火烧云"
    if value < 0.05:
        return "微微烧"
    if value < 0.20:
        return "小烧"
    if value < 0.40:
        return "小烧到中等烧"
    if value < 0.60:
        return "中等烧"
    if value < 0.80:
        return "中等烧到大烧"
    if value < 1.00:
        return "不完美大烧"
    if value < 1.50:
        return "典型大烧"
    if value < 2.00:
        return "优质大烧"
    return "世纪大烧"
