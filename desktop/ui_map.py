"""FireSky 桌面应用 · 地图选点标签页。

QWebEngineView 加载本地 HTML（内嵌 Leaflet + 瓦片底图）。点击地图 →
QWebChannel 回传经纬度 → 自动创建观测点并查询 Copernicus DEM 海拔
（OpenTopoData REST，免费无 Key），允许在单点标签手动覆盖。

天地图卫星瓦片（t{s}.tianditu.gov.cn）在无 Key 时返回 HTTP 418，因此
默认底图直接使用 OpenStreetMap 兜底；若日后拿到 Key，把 TIANDITU_KEY
填上即可自动切换回卫星影像。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:  # QtWebEngineWidget 可用性探测：失败则降级手输面板
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    WEBENGINE_AVAILABLE = True
except Exception:  # noqa: BLE001 - 环境缺 QtWebEngine
    WEBENGINE_AVAILABLE = False

from desktop import theme
from desktop.ui_point import PointForecastTab
from desktop.widgets import Location, load_locations

# 有 Key 时自动启用天地图卫星底图（img_w + cia_w 注记层）。
TIANDITU_KEY = ""

MAP_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; background: #d8d2c4; }
  .hint { position: absolute; left: 12px; bottom: 12px; z-index: 1000;
          background: rgba(251,247,240,.94); border: 1px solid #e3d6c2;
          border-radius: 8px; padding: 6px 12px; font: 12px "PingFang SC", sans-serif;
          color: #7a6a55; }
  .coordbox { position: absolute; right: 12px; bottom: 12px; z-index: 1000;
              background: rgba(251,247,240,.94); border: 1px solid #e3d6c2;
              border-radius: 8px; padding: 6px 12px; font: 12px monospace;
              color: #2a2118; }
</style>
</head>
<body>
<div id="map"></div>
<div class="hint">点击地图任意位置 → 自动创建观测点（DEM 海拔自动查询）</div>
<div class="coordbox" id="coord">—</div>
<script>
const TIANDITU_KEY = "__TIANDITU_KEY__";
const map = L.map('map', { zoomControl: true }).setView([32.5, 112.0], 5);
let baseLayers = {};
if (TIANDITU_KEY) {
  const img = L.tileLayer(
    'https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=' + TIANDITU_KEY,
    { subdomains: ['0','1','2','3','4','5','6','7'], maxZoom: 18,
      attribution: '© 天地图' });
  const cia = L.tileLayer(
    'https://t{s}.tianditu.gov.cn/cia_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=cia&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=' + TIANDITU_KEY,
    { subdomains: ['0','1','2','3','4','5','6','7'], maxZoom: 18 });
  baseLayers.satellite = L.layerGroup([img, cia]).addTo(map);
}
const osm = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  { maxZoom: 19, attribution: '© OpenStreetMap' });
if (!TIANDITU_KEY) { osm.addTo(map); }
L.control.layers(
  { '天地图卫星': baseLayers.satellite || osm, 'OpenStreetMap': osm },
  {}, { position: 'topright' }).addTo(map);

let marker = null;
function setMarker(lat, lon) {
  if (marker) { map.removeLayer(marker); }
  marker = L.marker([lat, lon],
    { icon: L.divIcon({ className: '', html:
      '<div style="width:14px;height:14px;border-radius:50% 50% 50% 0;' +
      'background:#d95d2c;transform:rotate(-45deg);box-shadow:0 2px 6px rgba(0,0,0,.3);"></div>',
      iconSize: [16, 16], iconAnchor: [10, 16] }) }).addTo(map);
}
function report(lat, lon) {
  setMarker(lat, lon);
  document.getElementById('coord').textContent =
    lat.toFixed(4) + ', ' + lon.toFixed(4);
  if (window.pyBridge && window.pyBridge.onMapClick) {
    window.pyBridge.onMapClick(lat, lon);
  }
}
map.on('click', function (event) {
  report(event.latlng.lat, event.latlng.lng);
});
function showLocation(lat, lon) {
  map.setView([lat, lon], Math.max(map.getZoom(), 8));
  setMarker(lat, lon);
}
// 已收藏观测点
const LOCATIONS = __LOCATIONS_JSON__;
LOCATIONS.forEach(function (loc) {
  L.circleMarker([loc.lat, loc.lon],
    { radius: 5, color: '#d95d2c', weight: 2, fillOpacity: 0.85 })
    .addTo(map)
    .bindTooltip(loc.name + (loc.starred ? ' ★' : ''))
    .on('click', function () { report(loc.lat, loc.lon); });
});
window.showLocation = showLocation;
</script>
</body>
</html>
"""


class MapBridge(QObject):
    """QWebChannel 桥：JS → Python 经纬度回传。"""

    mapClicked = Signal(float, float)

    @Slot(float, float)
    def onMapClick(self, lat: float, lon: float) -> None:
        self.mapClicked.emit(float(lat), float(lon))


class MapPickerTab(QWidget):
    """地图选点标签页（WebEngine 不可用时降级为手输 + 常用城市）。"""

    def __init__(self, point_tab: PointForecastTab, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._point_tab = point_tab
        self._bridge = MapBridge() if WEBENGINE_AVAILABLE else None
        self._channel = None
        self._build_ui()
        if WEBENGINE_AVAILABLE:
            self._setup_webengine()

    # ── UI ──
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("地图选点 — 点击地图任意位置自动创建观测点，海拔自动查 Copernicus DEM")
        title.setProperty("role", "ink2")
        head.addWidget(title)
        head.addStretch(1)
        self.status_label = QLabel(
            "" if WEBENGINE_AVAILABLE else "地图组件不可用（QtWebEngine 加载失败）：使用经纬度手输 + 常用城市"
        )
        if not WEBENGINE_AVAILABLE:
            self.status_label.setStyleSheet("color: #b07020; font-weight: 600;")
        head.addWidget(self.status_label)
        root.addLayout(head)

        if WEBENGINE_AVAILABLE:
            self.web_view = QWebEngineView(self)
            root.addWidget(self.web_view, 1)
        else:
            root.addWidget(self._build_fallback(), 1)

    def _build_fallback(self) -> QWidget:
        from PySide6.QtWidgets import (
            QDoubleSpinBox,
            QFormLayout,
            QFrame,
            QComboBox,
        )

        panel = QFrame()
        panel.setProperty("card", True)
        form = QFormLayout(panel)
        self.fb_lat = QDoubleSpinBox()
        self.fb_lat.setRange(-90.0, 90.0)
        self.fb_lat.setDecimals(4)
        self.fb_lon = QDoubleSpinBox()
        self.fb_lon.setRange(-180.0, 180.0)
        self.fb_lon.setDecimals(4)
        self.fb_city = QComboBox()
        for loc in load_locations():
            self.fb_city.addItem(f"{loc.name} {loc.coord_label()}", loc)
        self.fb_city.currentIndexChanged.connect(self._on_fb_city)
        pick_btn = QPushButton("创建观测点（DEM 海拔自动查询）")
        pick_btn.setObjectName("runBtn")
        pick_btn.clicked.connect(
            lambda: self._on_map_click(self.fb_lat.value(), self.fb_lon.value())
        )
        form.addRow("常用城市", self.fb_city)
        form.addRow("纬度", self.fb_lat)
        form.addRow("经度", self.fb_lon)
        form.addRow(pick_btn)
        return panel

    def _on_fb_city(self, index: int) -> None:
        loc = self.fb_city.itemData(index)
        if isinstance(loc, Location):
            self.fb_lat.setValue(loc.lat)
            self.fb_lon.setValue(loc.lon)

    # ── WebEngine ──
    def _setup_webengine(self) -> None:
        settings = self.web_view.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        locations_json = json.dumps(
            [
                {"name": loc.name, "lat": loc.lat, "lon": loc.lon,
                 "starred": loc.starred}
                for loc in load_locations()
            ],
            ensure_ascii=False,
        )
        html = (
            MAP_HTML
            .replace("__TIANDITU_KEY__", TIANDITU_KEY)
            .replace("__LOCATIONS_JSON__", locations_json)
        )
        self._bridge.mapClicked.connect(self._on_map_click)
        # QWebChannel：页面加载前注入 qwebchannel.js + 桥注册脚本
        self._channel = QWebChannel(self)
        self._channel.registerObject("pyBridge", self._bridge)
        self.web_view.page().setWebChannel(self._channel)
        from PySide6.QtCore import QIODevice
        from PySide6.QtCore import QFile as _QFile

        qwebchannel_js = _QFile(":/qtwebchannel/qwebchannel.js")
        if qwebchannel_js.open(QIODevice.OpenModeFlag.ReadOnly):
            source = bytes(qwebchannel_js.readAll()).decode("utf-8")
            qwebchannel_js.close()
        else:
            source = (
                "function QWebChannel(channel, cb) { cb({objects:{}}); }"
            )
        script = QWebEngineScript()
        script.setSourceCode(source + """
new QWebChannel(qt.webChannelTransport, function (channel) {
    window.pyBridge = channel.objects.pyBridge;
});
""")
        script.setName("fsBridge")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        self.web_view.page().profile().scripts().insert(script)
        self.web_view.setHtml(html, QUrl("http://fsdesktop.local/map.html"))

    # ── 选点处理 ──
    def _on_map_click(self, lat: float, lon: float) -> None:
        """点击回传 → 创建观测点 + 异步 DEM 海拔查询。"""
        from desktop.workers import ElevationQueryWorker

        lat = round(float(lat), 4)
        lon = round(float(lon), 4)
        # 先以 0 m 占位创建，海拔查询完成后回填
        loc = self._point_tab.accept_map_pick(lat, lon, None, None)
        self.status_label.setText(
            f"已选点 {lat:.4f}, {lon:.4f} · 正在查询 Copernicus DEM 海拔…"
        )
        worker = ElevationQueryWorker(lat, lon)
        worker.setParent(self)
        worker.finished_ok.connect(
            lambda result: self._on_elevation(loc, result)
        )
        worker.failed.connect(
            lambda msg: self._on_elevation_failed(loc, msg)
        )
        worker.start()
        self._elev_worker = worker

    def _on_elevation(self, loc: Location, result: object) -> None:
        if not isinstance(result, dict):
            return
        elevation = float(result.get("elevation_m") or 0.0)
        dataset = str(result.get("dataset") or "dem")
        loc.elevation_m = elevation
        loc.elevation_source = "dem"
        self._point_tab.apply_elevation(elevation, dataset)
        self.status_label.setText(
            f"已选点 {loc.lat:.4f}, {loc.lon:.4f} · 海拔 {elevation:.0f} m（{dataset}）"
        )

    def _on_elevation_failed(self, loc: Location, message: str) -> None:
        self.status_label.setText(
            f"已选点 {loc.lat:.4f}, {loc.lon:.4f} · DEM 查询失败，可手动填海拔：{message}"
        )

    def show_location(self, lat: float, lon: float) -> None:
        """外部跳转定位（单点标签点击收藏时联动）。"""
        if WEBENGINE_AVAILABLE:
            js = f"window.showLocation && window.showLocation({lat}, {lon});"
            self.web_view.page().runJavaScript(js)
