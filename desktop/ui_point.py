"""FireSky 桌面应用 · 单点预测页（v4 暮光玻璃）。

布局：左栏「观测点（内嵌选点地图 + 收藏）」+「观测参数」，右栏
「峰值卡 + 四指标 + 质量曲线 + 逐五分钟台账」。

地图选点已并入本页：内嵌小地图点击即写入 SpotStore，大地图浮层由
MainWindow 统一唤起；实况观测订阅同一状态。预测仍走
web_server.run_forecast_payload 权威路径，业务逻辑未改动。
"""

from __future__ import annotations

import math

from datetime import date as date_type
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
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
from desktop.workers import ElevationQueryWorker, ForecastWorker, WorkerBase

# 地图控件首选 QWebEngine + 天地图/Esri 真底图；若 QtWebEngine 未安装，
# 降级到纯自绘示意底图，保证预测主流程不被地图拖死。
try:  # noqa: SIM105 - 需要区分"未安装"与"正常"
    from desktop.web_map import FireSkyMap
except Exception:  # noqa: BLE001
    FireSkyMap = None  # type: ignore[assignment]


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
        self._syncing = False
        self._build_ui()
        self._reload_location_list()
        self._apply_spot(self._store.location())
        self._store.changed.connect(self._on_spot_changed)
        self._log("提示：内嵌地图单击落点，或使用「展开地图选点」精确定位。")

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 22, 26, 24)
        root.setSpacing(16)

        rail = QWidget()
        rail.setFixedWidth(292)
        rail_l = QVBoxLayout(rail)
        rail_l.setContentsMargins(0, 0, 0, 0)
        rail_l.setSpacing(14)
        rail_l.addWidget(self._build_spot_card())
        rail_l.addWidget(self._build_params_card())
        rail_l.addStretch(1)
        root.addWidget(rail)

        results = QWidget()
        results_l = QVBoxLayout(results)
        results_l.setContentsMargins(0, 0, 0, 0)
        results_l.setSpacing(14)
        results_l.addWidget(self._build_hero())
        results_l.addLayout(self._build_metrics())
        results_l.addWidget(self._build_chart_card())
        results_l.addWidget(self._build_table_card())
        results_l.addStretch(1)
        root.addWidget(results, 1)

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

        self._spot_views = QStackedWidget()
        map_page = QWidget()
        map_l = QVBoxLayout(map_page)
        map_l.setContentsMargins(0, 0, 0, 0)
        self._mini_map = FireSkyMap(mini=True) if FireSkyMap is not None else MiniMap()
        self._mini_map.setFixedHeight(148)
        self._mini_map.picked.connect(self._on_mini_map_picked)
        map_l.addWidget(self._mini_map)
        self._spot_views.addWidget(map_page)

        fav_page = QWidget()
        fav_l = QVBoxLayout(fav_page)
        fav_l.setContentsMargins(0, 0, 0, 0)
        self.location_list = QListWidget()
        self.location_list.setFixedHeight(148)
        self.location_list.itemClicked.connect(self._on_location_clicked)
        fav_l.addWidget(self.location_list)
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
        self._spot_name_label = widgets.role_label("杭州", "h2")
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
        foot.addWidget(widgets.chip("●  与实况观测共用", "ok"))
        foot.addStretch(1)
        expand = QPushButton("⤢  展开地图选点")
        expand.setObjectName("ghostBtn")
        expand.setCursor(Qt.CursorShape.PointingHandCursor)
        expand.clicked.connect(self._open_map_picker)
        foot.addWidget(expand)
        layout.addLayout(foot)
        return card

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
        self.dawn_btn = QPushButton("☀  朝霞\n日出前后")
        self.dusk_btn = QPushButton("☾  晚霞\n日落前后")
        self.dusk_btn.setChecked(True)
        self.dusk_btn.setProperty("active", "true")
        self.dawn_btn.setProperty("active", "false")
        theme.repolish(self.dawn_btn)
        theme.repolish(self.dusk_btn)
        for button in (self.dawn_btn, self.dusk_btn):
            button.setObjectName("chipBtn")
            button.setCheckable(True)
            button.setFixedHeight(46)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
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

        layout.addWidget(widgets.role_label("数值模式", "ink3"))
        layout.addSpacing(6)
        self._model_tabs = SegmentedControl(["GFS · 主模式", "ECMWF"])
        self._model_tabs.valueChanged.connect(self._on_model_tab)
        layout.addWidget(self._model_tabs)
        self.model_combo = QComboBox(self)
        self.model_combo.addItem("GFS（主模式）", "gfs")
        self.model_combo.addItem("ECMWF IFS", "ec")
        self.model_combo.setVisible(False)
        layout.addSpacing(9)

        context = QFrame()
        context.setFrameShape(QFrame.Shape.NoFrame)
        context.setProperty("plain", "true")
        context_l = QVBoxLayout(context)
        context_l.setContentsMargins(9, 7, 9, 7)
        context_l.setSpacing(5)
        self._context_label = widgets.role_label("", "hint")
        self._context_label.setWordWrap(True)
        context_l.addWidget(self._context_label)
        toggles = QHBoxLayout()
        edit_btn = QPushButton("✎  编辑坐标")
        edit_btn.setObjectName("ghostBtn")
        edit_btn.clicked.connect(self._toggle_editor)
        log_btn = QPushButton("▤  运行日志")
        log_btn.setObjectName("ghostBtn")
        log_btn.clicked.connect(self._toggle_log)
        toggles.addWidget(edit_btn)
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

        self.run_button = RunButton("开始预测  →")
        self.run_button.setFixedHeight(41)
        self.run_button.clicked.connect(self._run_forecast)
        layout.addSpacing(11)
        layout.addWidget(self.run_button)

        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setObjectName("ghostBtn")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_button)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setProperty("role", "log")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(96)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        actions = QHBoxLayout()
        star_btn = QPushButton("★ 收藏当前点")
        star_btn.setObjectName("ghostBtn")
        star_btn.clicked.connect(self._star_current)
        remove_btn = QPushButton("删除收藏")
        remove_btn.setObjectName("ghostBtn")
        remove_btn.clicked.connect(self._remove_current)
        actions.addWidget(star_btn)
        actions.addWidget(remove_btn)
        actions.addStretch(1)
        editor_l.addLayout(actions, 7, 0, 1, 2)
        return card

    def _build_hero(self) -> GlassCard:
        card = GlassCard()
        card.setMinimumHeight(176)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(24)

        left = QVBoxLayout()
        left.setSpacing(6)
        kicker_row = QHBoxLayout()
        kicker_row.setSpacing(8)
        self._hero_kicker = widgets.role_label("杭州 · 今日晚霞", "ink3")
        self._hero_badge = widgets.chip("等待预测")
        kicker_row.addWidget(self._hero_kicker)
        kicker_row.addWidget(self._hero_badge)
        kicker_row.addStretch(1)
        left.addLayout(kicker_row)

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
        layout.addLayout(left, 1)

        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {theme.palette()['border']};")
        self._hero_divider = divider
        layout.addWidget(divider)

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
        sunline.addWidget(widgets.role_label("25 min", "hint"))
        right.addLayout(sunline)
        right.addStretch(1)
        layout.addLayout(right, 1)
        return card

    def _build_metrics(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self._metric_cards: dict[str, MetricCard] = {}
        for key, title, sub in (
            ("burnable", "可烧云量", "中高云条件"),
            ("duration", "有效持续", "完整观赏窗口"),
            ("aod", "AOD 550", "大气通透度"),
            ("genus", "主要云属", "云底高度"),
        ):
            card = MetricCard(title)
            card.set_sub(sub)
            row.addWidget(card, 1)
            self._metric_cards[key] = card
        return row

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

    def _build_table_card(self) -> GlassCard:
        card = GlassCard()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(17, 14, 17, 10)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.section_title("逐时详情"))
        titles.addWidget(widgets.role_label("物理指标与霞光质量台账", "hint"))
        head.addLayout(titles)
        head.addStretch(1)
        detail = QPushButton("查看全部 →")
        detail.setObjectName("ghostBtn")
        detail.clicked.connect(self._toggle_log)
        head.addWidget(detail)
        layout.addLayout(head)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["时刻", "质量", "档位", "可烧", "云底 AGL", "AOD", "太阳仰角"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(150)
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
        self._hero_kicker.setText(f"{loc.name} · 今日晚霞")
        self._sync_mini_map(loc)
        for index in range(self.location_list.count()):
            item = self.location_list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            item.setSelected(
                isinstance(data, Location) and data.name == loc.name
                and abs(data.lat - loc.lat) < 1e-4
            )

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

    def _on_location_clicked(self, item: QListWidgetItem) -> None:
        loc = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(loc, Location):
            self._store.set_location(loc)

    def _on_spot_tab(self, index: int, _label: str) -> None:
        self._spot_views.setCurrentIndex(index)

    def _on_date_tab(self, index: int, _label: str) -> None:
        today = datetime.now().date()
        self.date_edit.setDate(today + timedelta(days=1) if index == 1 else today)
        self.date_edit.setVisible(index == 2)

    def _on_phenomenon(self, index: int) -> None:
        for i, button in enumerate((self.dawn_btn, self.dusk_btn)):
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)

    def _on_model_tab(self, index: int, _label: str) -> None:
        self.model_combo.setCurrentIndex(index)

    def _reset_params(self) -> None:
        self._date_tabs.set_index(0)
        self._model_tabs.set_index(0)
        self.dusk_btn.setChecked(True)
        self._on_phenomenon(1)

    def _toggle_editor(self) -> None:
        self._editor.setVisible(not self._editor.isVisible())

    def _toggle_log(self) -> None:
        self.log_view.setVisible(not self.log_view.isVisible())

    def _apply_editor(self) -> None:
        self._store.set_location(self._current_location())

    def _open_map_picker(self) -> None:
        if self._on_pick_map is not None:
            self._on_pick_map()

    def _sync_mini_map(self, loc: Location) -> None:
        """共享观测点 → 内嵌地图；兼容 MiniMap 降级实现。"""
        if FireSkyMap is not None and isinstance(self._mini_map, FireSkyMap):
            self._mini_map.set_view(loc.lat, loc.lon, 8, loc.name)
        else:
            self._mini_map.set_location(loc)  # type: ignore[attr-defined]

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
            # 旧 worker 的 urlopen 无法中断，先等它退出再替换，
            # 否则被丢弃的 QThread 在退出时析构运行中线程会崩进程。
            self._elev_worker.cancel()
            self._elev_worker.wait(30000)
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
        if elevation <= 0.0:
            # 海平面点 / 数据集未收录（null）时不要用 0 覆盖已有海拔；
            # 0 既是「未查询」哨兵也是真实值，只在没有更可信来源时采用。
            if current.elevation_source == "dem" and current.elevation_m > 0.0:
                self._log(f"DEM 返回 {elevation:.0f} m，保留已有海拔 "
                          f"{current.elevation_m:.0f} m")
                return
            self._log("DEM 返回 0 m（海洋或未收录区域），海拔保持待填。")
            return
        current.elevation_m = elevation
        current.elevation_source = "dem"
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
        loc = self._current_location()
        loc.starred = True
        for index, existing in enumerate(self._locations):
            if abs(existing.lat - loc.lat) < 1e-4 and abs(existing.lon - loc.lon) < 1e-4:
                self._locations[index] = loc
                break
        else:
            self._locations.append(loc)
        save_locations(self._locations)
        self._reload_location_list()
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
        self._locations = [
            x for x in self._locations
            if not (x.name == loc.name and abs(x.lat - loc.lat) < 1e-6
                    and abs(x.lon - loc.lon) < 1e-6)
        ]
        save_locations(self._locations)
        self._reload_location_list()
        self._log(f"已删除观测点：{loc.name}")

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
        payload = {
            "name": loc.name,
            "lat": loc.lat,
            "lon": loc.lon,
            "elevation_m": loc.elevation_m,
            "timezone_name": loc.timezone_name,
            "target_date": target.isoformat(),
            "phenomenon": self._phenomenon(),
            "model_source": self.model_combo.currentData(),
            "forecast_mode": "model",
            "satellite_source": "off",
            "workers": 1,
        }
        self._start_worker(ForecastWorker(payload))
        self._log(f"创建预测任务：{loc.name} {payload['target_date']} "
                  f"{payload['phenomenon']} {payload['model_source']}")

    def _start_worker(self, worker: ForecastWorker) -> None:
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(2)
        worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._log("已请求取消（引擎将在下一个进度检查点停止）。")

    def _on_cancelled(self) -> None:
        """取消落地：复位控件（不弹窗、不当失败处理）。"""
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self._log("预测已取消。")

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.append(f"[{stamp}] {message}")

    def _on_progress(self, event: dict) -> None:
        percent = int(event.get("percent") or 0)
        message = str(event.get("message") or "")
        self.progress.setValue(max(2, min(100, percent)))
        if message:
            self._log(message)
            self.progress.setToolTip(message)

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
        self.render_result(result)
        self._log(f"完成：周期 {result.get('cycle_time_utc', '')}")

    def render_result(self, result: dict) -> None:
        """把单点扁平 payload 渲染到峰值卡 + 指标 + 曲线 + 台账。"""
        level = str(result.get("quality_level") or "无明显火烧云")
        peak = float(result.get("peak_quality_index") or 0.0)
        color = theme.level_color(level)
        self.quality_big.setText(f"{peak:.2f}")
        self.quality_big.setStyleSheet(f"color: {color};")
        self._has_result = True
        self.quality_level.setText(level)
        self.quality_level.setProperty("tone", "accent")
        theme.repolish(self.quality_level)
        self._hero_badge.setText("预测完成")
        self._hero_badge.setProperty("tone", "accent")
        theme.repolish(self._hero_badge)

        confidence = result.get("confidence_score")
        self._hero_caption.setText(
            f"峰值质量指数 · 置信度 "
            f"{float(confidence):.0%}" if confidence is not None
            else "峰值质量指数 · 运行预测后显示"
        )
        best_time = widgets.local_time_label(result.get("best_time_local"))
        self._peak_time.setText(best_time)
        # 推荐窗口与引擎 timing_window 联动（质量曲线插值窗口），缺省回退 ±25 分钟。
        timing_window = result.get("timing_window") or {}
        window_minutes = timing_window.get("duration_minutes")
        window_text = (
            f"推荐窗口 {widgets.local_time_label(timing_window.get('start_time_local'))}"
            f" – {widgets.local_time_label(timing_window.get('end_time_local'))}"
            if window_minutes
            else f"推荐窗口 {best_time} 前后 25 分钟"
        )
        self._peak_window.setText(window_text)

        genus = result.get("cloud_genus_proxy")
        genus_label = genus.get("label") if isinstance(genus, dict) else genus
        # 引擎把可烧云量 / AOD 放在 quality_components 嵌套层（与 Web 前端
        # MetricsGrid 同口径），顶层无这两个键。
        components = result.get("quality_components") or {}
        burnable = components.get(
            "burnable_cloud_coverage_effective",
            result.get("burnable_cloud_coverage_effective"),
        )
        burnable_text = (
            f"{float(burnable) * 100:.0f}%" if burnable is not None
            and float(burnable) <= 1.0 else fmt(burnable, "{:.0f}%")
        )
        self._metric_cards["burnable"].set_value(burnable_text)
        # 「有效持续」用引擎权威口径：duration_minutes（核心窗口时长），
        # 区域点 payload 键名为 burnable_duration_minutes，不做自造口径。
        duration = result.get("duration_minutes")
        if duration is None:
            duration = components.get("duration_minutes")
        self._metric_cards["duration"].set_value(
            fmt(duration, "{:.0f}"),
        )
        self._metric_cards["duration"].set_sub("核心观赏窗口 · 分钟")
        aod = components.get("quality_aod550", result.get("quality_aod550"))
        self._metric_cards["aod"].set_value(fmt(aod, "{:.2f}"))
        genus_card = self._metric_cards["genus"]
        genus_card.value_label.setText(str(genus_label or "—"))
        genus_card.value_label.setStyleSheet("font-size: 16px;")
        base = widgets.format_agl_meters(
            result.get("dominant_burnable_cloud_base_agl_mean_m")
        )
        genus_card.set_sub(f"云底约 {base}")

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

        # 峰值行与峰值卡同口径：valid 且 physically_burnable 里取最大，
        # 与引擎 best_index 选择一致，避免「质量最高步不可烧」时错位。
        valid_pairs = [
            (i, v) for i, (_, v, ok) in enumerate(curve_steps)
            if ok and steps[i].get("physically_burnable")
        ]
        best_index = max(valid_pairs, key=lambda pair: pair[1])[0] if valid_pairs else None

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
            burnable_item = QTableWidgetItem(
                "✓" if bool(s.get("physically_burnable")) else "×"
            )
            if s.get("physically_burnable"):
                burnable_item.setForeground(QColor(theme.palette()["ok"]))
            for col, cell in enumerate((
                QTableWidgetItem(stamp), quality_item, level_item, burnable_item,
                QTableWidgetItem(widgets.format_agl_meters(
                    s.get("dominant_burnable_cloud_base_agl_mean_m"))),
                QTableWidgetItem(fmt(s.get("quality_aod550"), "{:.2f}")),
                QTableWidgetItem(fmt(s.get("solar_elevation_deg"), "{:.1f}°")),
            )):
                self.table.setItem(row, col, cell)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.progress.setValue(0)
        self.progress.setVisible(False)
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

    def shutdown(self) -> None:
        for worker in (self._worker, self._elev_worker):
            if worker is not None and worker.isRunning():
                worker.cancel()
                # 不带超时等待收尾：QThread 带析构运行中线程会 abort 进程。
                worker.wait(5000)


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
