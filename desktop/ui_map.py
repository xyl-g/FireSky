"""FireSky 桌面应用 · 共用地图选点浮层（v5）。

地图选点不再是独立标签页：本浮层由「单点预测」与「实况观测」共同唤起，
确认后的点位写入 SpotStore，两个页面同步更新。

底图
----
使用 QWebEngine 内嵌本地 Leaflet（``assets/map.html``），双源：

* **天地图**（默认）—— 境内合规底图，影像 / 矢量 / 地形三种类型，
  需要使用者自有的 Key（环境变量或「数据与缓存 → 地图服务」里填写）。
* **Esri** —— 免 Key，仅供中国境外区域；境外天地图无矢量/地形数据，
  地图内部会自动锁定影像底图。

底图源与类型的切换控件由地图页面自身提供（玻璃浮层），本浮层只负责
搜索、收藏、待确认点位的编辑与 DEM 海拔补齐。地图仅用于选点，不叠加
任何预报结果。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from desktop import theme, widgets
from desktop.widgets import (
    GlassCard, Location, MiniMap, RunButton, SpotStore, load_locations,
    timezone_for_point,
)
from desktop.workers import ElevationQueryWorker

try:  # 与 ui_point 同一套降级策略：QtWebEngine 缺失时退回自绘底图
    from desktop.web_map import (
        FireSkyMap, SOURCE_LABELS, map_source, tianditu_key, tianditu_key_source,
    )
except Exception:  # noqa: BLE001
    FireSkyMap = None  # type: ignore[assignment]
    SOURCE_LABELS = {}  # type: ignore[assignment]

    def map_source() -> str:  # type: ignore[misc]
        return ""

    def tianditu_key() -> str:  # type: ignore[misc]
        return ""

    def tianditu_key_source() -> str:  # type: ignore[misc]
        return ""


class MapPickerDialog(QDialog):
    """共享地图选点浮层：落点为待确认状态，确认后才写入 SpotStore。"""

    spotConfirmed = Signal(object)  # Location
    configureKeyRequested = Signal()  # 未配置天地图 Key 时请求打开设置

    def __init__(self, spot_store: SpotStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("FireSky · 选择观测点")
        self.setModal(True)
        self.resize(1060, 700)
        self.setMinimumSize(820, 560)
        self._store = spot_store
        self._pending: Location | None = None
        self._elev_worker: ElevationQueryWorker | None = None
        # DEM 查询防抖：公共 API 约限速 1 req/s，连续落点/拖动只在停顿后查询。
        self._dem_timer = QTimer(self)
        self._dem_timer.setSingleShot(True)
        self._dem_timer.setInterval(700)
        self._dem_timer.timeout.connect(self._start_dem_query)
        self._build_ui()

    # ── UI ──
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(66)
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(20, 12, 16, 10)
        header_l.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(widgets.role_label("选择观测点", "h2"))
        titles.addWidget(widgets.role_label(
            "落点将同步到单点预测与实况观测，并自动补齐海拔", "hint"
        ))
        header_l.addLayout(titles)
        header_l.addStretch(1)
        self._shared = widgets.chip("●  两页共用点位", "ok")
        header_l.addWidget(self._shared)
        close_btn = QPushButton("×")
        close_btn.setObjectName("iconBtn")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.hide)
        header_l.addWidget(close_btn)
        root.addWidget(header)

        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_body(), 1)
        root.addWidget(self._build_footer())

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(20, 2, 20, 8)
        bar_l.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索收藏点，或输入 纬度,经度（如 30.2741,120.1551）")
        self._search.setMinimumWidth(280)
        self._search.returnPressed.connect(self._on_search)
        bar_l.addWidget(self._search, 1)

        fav_btn = QPushButton("★ 收藏点")
        fav_btn.setObjectName("ghostBtn")
        fav_btn.clicked.connect(self._show_favourites)
        bar_l.addWidget(fav_btn)

        reset_btn = QPushButton("重置视野")
        reset_btn.setObjectName("ghostBtn")
        reset_btn.clicked.connect(self._reset_view)
        bar_l.addWidget(reset_btn)

        # 底图源状态 + Key 引导（未配置时变成可点击按钮）
        self._source_chip = widgets.chip("")
        bar_l.addWidget(self._source_chip)

        self._key_btn = QPushButton("配置天地图 Key")
        self._key_btn.setObjectName("ghostBtn")
        self._key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._key_btn.clicked.connect(self._request_key_config)
        self._key_btn.setVisible(False)
        bar_l.addWidget(self._key_btn)
        return bar

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setContentsMargins(20, 0, 20, 12)
        body.setSpacing(14)

        if FireSkyMap is not None:
            self._map = FireSkyMap(source=map_source())
            # 页面加载失败（离线等）：提示可重试，不无限堆积静默失败。
            self._map.loadFailed.connect(
                lambda: self._hint.setText(
                    "底图加载失败（网络不可用？）；可点击「重置视野」重试或先用收藏点"
                )
            )
            # 瓦片持续 403（Key 无效）时在提示条可见地暴露，别只显示「已配置」。
            self._map.tileError.connect(self._on_tile_error)
            self._map.jsError.connect(self._on_js_error)
        else:
            self._map = MiniMap(show_controls=False)
        self._map.setMinimumHeight(380)
        self._map.picked.connect(self._on_map_picked)
        body.addWidget(self._map, 1)

        card = GlassCard(strong=True, radius=16)
        card.setFixedWidth(272)
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(16, 15, 16, 15)
        card_l.setSpacing(9)

        card_l.addWidget(widgets.section_title("待确认点位"))
        self._coord = widgets.role_label("—", "ink3")
        card_l.addWidget(self._coord)

        self._name = QLineEdit()
        self._name.setPlaceholderText("观测点名称")
        card_l.addWidget(self._name)

        stats = QHBoxLayout()
        stats.setSpacing(7)
        self._elev_label = widgets.role_label("海拔 —", "chip")
        self._tz_label = widgets.role_label("时区 —", "chip")
        stats.addWidget(self._elev_label)
        stats.addWidget(self._tz_label)
        card_l.addLayout(stats)

        self._hint = widgets.role_label("单击地图放置观测点", "hint")
        self._hint.setWordWrap(True)
        card_l.addWidget(self._hint)
        card_l.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghostBtn")
        cancel.clicked.connect(self.hide)
        confirm = RunButton("使用此观测点")
        confirm.clicked.connect(self._confirm)
        actions.addWidget(cancel)
        actions.addWidget(confirm, 1)
        card_l.addLayout(actions)

        self._card = card
        body.addWidget(card)
        return body

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(34)
        footer_l = QHBoxLayout(footer)
        footer_l.setContentsMargins(19, 0, 19, 0)
        footer_l.addWidget(widgets.role_label(
            "底图切换在左上角面板内 · 仅用于选点，不叠加预报数据", "hint"
        ))
        footer_l.addStretch(1)
        self._attribution = widgets.role_label(
            "境内用天地图（CGCS2000）· 境外用 Esri（Powered by Esri）", "hint"
        )
        footer_l.addWidget(self._attribution)
        return footer

    # ── 底图状态 ──
    def _refresh_source_chip(self) -> None:
        source = self._map.source() if FireSkyMap is not None else ""
        has_key = bool(tianditu_key()) if FireSkyMap is not None else False
        if FireSkyMap is None:
            self._source_chip.setText("本地示意底图")
            self._source_chip.setProperty("tone", "")
            theme.repolish(self._source_chip)
            self._key_btn.setVisible(False)
            return
        label = SOURCE_LABELS.get(source, source)
        self._source_chip.setText(
            f"●  {label} 底图" if has_key or source == "esri"
            else "●  天地图 · 未配置 Key"
        )
        self._source_chip.setProperty(
            "tone", "ok" if (has_key or source == "esri") else "accent"
        )
        theme.repolish(self._source_chip)
        self._key_btn.setVisible(not has_key and source != "esri")
        # tooltip 与按钮可见性同步设置：可见时引导申请，隐藏时说明来源。
        if has_key:
            source_desc = tianditu_key_source() or "本机配置"
            self._key_btn.setToolTip(f"Key 来自 {source_desc}")
        else:
            self._key_btn.setToolTip("到 console.tianditu.gov.cn 申请「浏览器端」Key"
                                     "，在「数据与缓存 → 地图服务」里填写")

    def _on_js_error(self, message: str) -> None:
        """页面脚本未捕获异常：提示条可见地暴露，避免静默丢错。"""
        self._hint.setText(f"地图脚本异常：{message[:80]}（功能可能受影响，可重置视野重试）")

    def _on_tile_error(self, count: int) -> None:
        """瓦片连续加载失败：提示 Key 可能无效（配了 Key 但 403 时可见）。"""
        if count >= 3:
            self._hint.setText(
                f"底图瓦片加载失败（已失败 {count} 次）："
                "若已配置天地图 Key，请到「数据与缓存 → 地图服务」检查 Key 是否有效；"
                "境内可切 Esri 前请确认点位在境外。"
            )

    def _request_key_config(self) -> None:
        self.configureKeyRequested.emit()

    def on_key_changed(self) -> None:
        """设置页改了 Key / 底图源：刷新本浮层的状态芯片。"""
        self._refresh_source_chip()

    # ── 打开 / 待确认点位 ──
    def open_for(self, loc: Location) -> None:
        self._pending = loc.copy()
        self._name.setText(self._pending.name)
        self._sync_map(self._pending)
        self._refresh_source_chip()
        self._update_card()
        self.show()
        self.raise_()
        self.activateWindow()
        if FireSkyMap is not None:
            self._map.invalidate()

    def _sync_map(self, loc: Location) -> None:
        if FireSkyMap is not None and isinstance(self._map, FireSkyMap):
            self._map.set_view(loc.lat, loc.lon, 9, loc.name)
        else:
            self._map.set_location(loc)  # type: ignore[attr-defined]

    def _update_card(self) -> None:
        if self._pending is None:
            return
        self._coord.setText(self._pending.coord_text())
        self._elev_label.setText(
            f"海拔 {widgets.format_elevation_m(self._pending.elevation_m)}"
            + (" · DEM" if self._pending.elevation_source == "dem" else "")
        )
        self._tz_label.setText(self._pending.utc_offset_label())

    def _set_pending(self, loc: Location, *, query_dem: bool) -> None:
        self._pending = loc
        self._name.setText(loc.name)
        self._sync_map(loc)
        self._update_card()
        if query_dem:
            self._query_elevation(loc.lat, loc.lon)

    def _on_map_picked(self, lat: float, lon: float) -> None:
        self._hint.setText(f"已落点 {lat:.4f}, {lon:.4f} · 正在查询 DEM 海拔…")
        self._set_pending(
            Location(
                name="自定义观测点",
                lat=lat,
                lon=lon,
                # -1 m 是「DEM 未回填」哨兵：引擎海拔合法域为 -500..9000 m，
                # 用真实海平面值 0 当哨兵会与真实海洋点混淆。
                elevation_m=Location.UNQUERIED_ELEVATION_M,
                timezone_name=timezone_for_point(lat, lon),
                elevation_source="manual",
            ),
            query_dem=False,
        )
        self._dem_timer.start()  # 防抖后查询，避开公共 API 限速

    def _start_dem_query(self) -> None:
        if self._pending is not None:
            self._query_elevation(self._pending.lat, self._pending.lon)

    def _on_search(self) -> None:
        text = self._search.text().strip()
        if not text:
            return
        for loc in load_locations():
            if text in loc.name or text in loc.coord_label():
                self._hint.setText(f"已定位收藏点 {loc.name}")
                self._set_pending(loc.copy(), query_dem=False)
                return
        try:
            # 只把全角逗号/中文逗号归一成分隔符；保留空格（「30.27, 120.16」
            # 是 coord_label 的原生格式），不能把空格当分隔符。
            parts = text.replace("，", ",").replace("；", ",").split(",")
            lat, lon = float(parts[0].strip()), float(parts[1].strip())
        except (ValueError, IndexError):
            self._hint.setText("未找到收藏点；请输入「纬度,经度」，例如 30.2741,120.1551")
            return
        # 与引擎 bounded_float 同口径的范围校验，别把越界坐标拖到引擎才报错。
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            self._hint.setText(
                f"纬度需在 -90 到 90、经度需在 -180 到 180 之间；当前输入 lat={lat}, lon={lon}"
            )
            return
        self._hint.setText("已按输入经纬度定位")
        self._set_pending(
            Location(name="自定义观测点", lat=round(lat, 4), lon=round(lon, 4),
                     elevation_m=Location.UNQUERIED_ELEVATION_M,
                     timezone_name=timezone_for_point(lat, lon)),
            query_dem=True,
        )

    def _show_favourites(self) -> None:
        menu = QMenu(self)
        for loc in load_locations():
            action = menu.addAction(f"{'★ ' if loc.starred else ''}{loc.name}  {loc.coord_label()}")
            action.setData(loc)
            action.triggered.connect(
                lambda _=False, item=action: self._on_favourite(item.data())
            )
        menu.exec(self._search.mapToGlobal(self._search.rect().bottomLeft()))

    def _on_favourite(self, data: object) -> None:
        if isinstance(data, Location):
            self._hint.setText(f"已定位收藏点 {data.name}")
            self._set_pending(data.copy(), query_dem=False)

    def _reset_view(self) -> None:
        if self._pending is not None:
            self._sync_map(self._pending)

    # ── DEM 海拔 ──
    def _query_elevation(self, lat: float, lon: float) -> None:
        if self._elev_worker is not None and self._elev_worker.isRunning():
            # 旧 worker 的 urlopen 无法中断，先等它退出再替换，否则被丢弃的
            # QThread 在退出时析构运行中线程会崩进程；连续落点由等待串行化。
            self._elev_worker.cancel()
            self._elev_worker.wait(30000)
        worker = ElevationQueryWorker(lat, lon)
        worker.setParent(self)
        worker.finished_ok.connect(self._on_elevation)
        worker.failed.connect(self._on_elevation_failed)
        worker.start()
        self._elev_worker = worker

    def _on_elevation(self, result: object) -> None:
        if not isinstance(result, dict) or self._pending is None:
            return
        if (abs(float(result.get("lat") or 0) - self._pending.lat) > 1e-4
                or abs(float(result.get("lon") or 0) - self._pending.lon) > 1e-4):
            return
        elevation = float(result.get("elevation_m"))
        if elevation <= 0.0:
            # 海平面点 / 数据集未收录（null）时保持「未查询」哨兵，提示手动填。
            self._hint.setText(
                "DEM 返回 0 m（海洋或未收录区域）；请手动确认海拔后再预测"
            )
            return
        self._pending.elevation_m = elevation
        self._pending.elevation_source = "dem"
        self._update_card()
        self._hint.setText(
            f"海拔 {self._pending.elevation_m:.0f} m · {result.get('dataset') or 'DEM'}"
        )

    def _on_elevation_failed(self, message: str) -> None:
        dataset = "DEM"
        if "：" in message and "查询失败" in message:
            dataset = message.split("：", 1)[0] or dataset
        self._hint.setText(f"{dataset} 查询失败（{message[-48:]}）；可手动填写海拔")
        self._hint.setToolTip(message)

    # ── 确认 ──
    def _confirm(self) -> None:
        if self._pending is None:
            return
        if self._pending.elevation_m <= Location.UNQUERIED_ELEVATION_M:
            # DEM 未回填（查询失败/超时/海洋点）：提交 -1 会被引擎当真实
            # 海拔参与辐射/云底计算，必须先补齐。
            QMessageBox.warning(
                self, "FireSky",
                "尚未获取到该点位海拔（DEM 查询未完成或失败）。\n"
                "请在「海拔」框手动填写后重试；真实海平面观测点可填 0。",
            )
            return
        self._pending.name = self._name.text().strip() or "观测点"
        loc = self._pending.copy()
        self.hide()
        self.spotConfirmed.emit(loc)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def on_theme_changed(self) -> None:
        self._card.update()
        theme.repolish(self._shared)
        theme.repolish(self._source_chip)
        if FireSkyMap is not None and isinstance(self._map, FireSkyMap):
            self._map.set_theme(theme.is_dark())
        else:
            self._map.update()  # type: ignore[attr-defined]

    def shutdown(self) -> None:
        if self._elev_worker is not None and self._elev_worker.isRunning():
            self._elev_worker.cancel()
            # 不带超时的收尾等待：QThread 带线程析构会 abort 进程。
            self._elev_worker.wait(30000)
