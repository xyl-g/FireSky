"""地图选点控件（QWebEngine + 本地 Leaflet）—— 桌面端仅用于「选点」。

职责边界
--------
本控件只提供底图（影像 / 矢量 / 地形）、观测点标记、点击落点与标记拖动，
**不叠加云场热力、采样格点或任何预报结果**。结果展示由 Qt 原生页面承担，
因此代码里不存在 field / cloud / cells 之类的数据叠加接口。

底图双源
--------
``tianditu``（默认）
    **天地图**（国家基础地理信息中心 / NASG）。境内公开地图服务的合规底图，
    需要使用者自行申请 Key。境外区域仅有影像且分辨率有限（欧洲约 z14、
    北美约 z16），因此按地图中心是否落在中国范围内动态设置
    ``maxNativeZoom``，用低级别瓦片放大而不是请求空白占位瓦片。

``esri``
    Esri ArcGIS Online 基础底图（World Imagery / Street / Topo），免 Key。
    **仅限中国境外使用**：《地图管理条例》要求境内公开使用的电子地图使用
    有审图号的合规底图，Esri 等境外源在境内不具备该资格。本项目以开源、
    非商用方式分发，Esri 侧按其条款需保留 ``Powered by Esri`` 署名。

天地图为 CGCS2000、Esri 为 WGS84，两者与业务层经纬度基本一致，切换无偏移。

Key 规则
--------
本模块与 ``assets/map.html`` **不包含任何有效 Key**。天地图 Key 由使用者
自行申请，注入顺序（前者优先）：

1. 环境变量 ``FIRESKY_TIANDITU_KEY``（适合 CI / 临时调试）
2. 本机配置 ``~/.firesky-desktop/config.json`` 的 ``tianditu_key``
   （桌面端「数据与缓存 → 地图服务」写入的位置，也是正式途径）
3. 开发树里 Web 前端的 ``frontend/src/lib/utils/constants.ts`` —— 仅在
   直接从源码运行、且前端 Key 恰好存在时作为便利回退；打包后的应用里
   该文件不存在，回退自动失效。

未配置时地图展示申请引导，并可一键切到 Esri 源继续选点（免 Key）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# QtWebEngine 的启动前置条件（macOS 沙箱开关、打包后 framework 路径修复）统一
# 放在 desktop/__init__.py，保证先于本模块的 QtWebEngine 导入执行。

from PySide6.QtCore import QTimer, QUrl, Qt, Signal  # noqa: E402
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

from desktop import theme  # noqa: E402
from desktop.widgets import load_app_config, save_app_config  # noqa: E402

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
MAP_HTML = ASSETS_DIR / "map.html"
_REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_CONSTANTS = _REPO_ROOT / "frontend" / "src" / "lib" / "utils" / "constants.ts"

ENV_KEY = "FIRESKY_TIANDITU_KEY"
CONFIG_KEY = "tianditu_key"
CONFIG_SOURCE = "map_source"
CONFIG_BASE = "map_base"

SOURCE_TIANDITU = "tianditu"
SOURCE_ESRI = "esri"
SOURCES = (SOURCE_TIANDITU, SOURCE_ESRI)
DEFAULT_SOURCE = SOURCE_TIANDITU

BASEMAPS = ("imagery", "vector", "terrain")
BASEMAP_LABELS = {"imagery": "影像", "vector": "矢量", "terrain": "地形"}
SOURCE_LABELS = {SOURCE_TIANDITU: "天地图", SOURCE_ESRI: "Esri"}

# 显式占位符，仅用于 UI 提示；绝不写入可用 Key
KEY_PLACEHOLDER = "请到天地图开放平台申请并替换此占位符"

# 天地图 Key 是 32 位十六进制串（部分应用类型带短横线）
_KEY_PATTERN = re.compile(r"[0-9a-fA-F]{16,64}")


def _key_from_web_constants() -> str:
    """开发期回退：复用 Web 前端已配置的天地图 Key（打包版无此文件）。"""
    try:
        text = WEB_CONSTANTS.read_text("utf-8")
    except OSError:
        return ""
    match = re.search(r"TIANDITU_KEY\s*=\s*['\"]([0-9a-zA-Z]{16,})['\"]", text)
    return match.group(1) if match else ""


def tianditu_key() -> str:
    """读取天地图 Key：环境变量 → 本机配置 → 开发树 Web 常量。"""
    env = (os.environ.get(ENV_KEY) or "").strip()
    if env:
        return env
    value = str(load_app_config().get(CONFIG_KEY) or "").strip()
    if value and value != KEY_PLACEHOLDER:
        return value
    return _key_from_web_constants()


def tianditu_key_source() -> str:
    """Key 的来源描述，用于设置页状态文案；未配置返回空串。"""
    if (os.environ.get(ENV_KEY) or "").strip():
        return f"环境变量 {ENV_KEY}"
    value = str(load_app_config().get(CONFIG_KEY) or "").strip()
    if value and value != KEY_PLACEHOLDER:
        return "本机配置 ~/.firesky-desktop/config.json"
    if _key_from_web_constants():
        return "Web 前端常量（开发期回退，打包版不含）"
    return ""


def mask_key(key: str) -> str:
    """脱敏展示：只留首尾 4 位。"""
    key = (key or "").strip()
    if not key:
        return "未配置"
    if len(key) <= 10:
        return key[:2] + "•" * max(0, len(key) - 2)
    return f"{key[:4]}••••••{key[-4:]}"


def set_tianditu_key(key: str) -> None:
    """把用户自有的 Key 保存到本机配置（不进入代码仓库）。

    Key 是 32 位十六进制串：明显不合法的输入直接拒绝保存并返回 False，
    避免垃圾字符串被存进配置后瓦片一直 403 还显示「已配置」。
    """
    config = load_app_config()
    key = (key or "").strip()
    if key and key != KEY_PLACEHOLDER:
        if not _KEY_PATTERN.fullmatch(key):
            return False
        config[CONFIG_KEY] = key
    else:
        config.pop(CONFIG_KEY, None)
    save_app_config(config)
    return True


def map_source() -> str:
    """读取上次使用的底图源，缺省为天地图。"""
    value = str(load_app_config().get(CONFIG_SOURCE) or "").strip()
    return value if value in SOURCES else DEFAULT_SOURCE


def set_map_source(name: str) -> None:
    """记住用户选择的底图源（只写本机配置，不写仓库）。"""
    if name not in SOURCES:
        return
    config = load_app_config()
    config[CONFIG_SOURCE] = name
    save_app_config(config)


def map_basemap() -> str:
    value = str(load_app_config().get(CONFIG_BASE) or "").strip()
    return value if value in BASEMAPS else "imagery"


def set_map_basemap(name: str) -> None:
    if name not in BASEMAPS:
        return
    config = load_app_config()
    config[CONFIG_BASE] = name
    save_app_config(config)


class _BridgePage(QWebEnginePage):
    """JS → Python 的 console 桥（避免引入额外 qwebchannel.js 依赖）。"""

    pointPicked = Signal(float, float)
    mapReady = Signal()
    baseChanged = Signal(str)
    sourceChanged = Signal(str)
    keyGateShown = Signal()
    tileError = Signal(int)
    jsError = Signal(str)  # 页面 JS 未捕获异常（window.onerror 兜底上报）

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceId):  # noqa: N802
        text = str(message)
        if text.startswith("FIRESKY_PICK:"):
            try:
                data = json.loads(text[len("FIRESKY_PICK:"):])
                self.pointPicked.emit(float(data["lat"]), float(data["lon"]))
            except (ValueError, KeyError, TypeError):
                pass
        elif text == "FIRESKY_READY":
            self.mapReady.emit()
        elif text.startswith("FIRESKY_BASE:"):
            self.baseChanged.emit(text.split(":", 1)[1])
        elif text.startswith("FIRESKY_SOURCE:"):
            self.sourceChanged.emit(text.split(":", 1)[1])
        elif text.startswith("FIRESKY_JSERROR:"):
            self.jsError.emit(text.split(":", 1)[1])
        elif text.startswith("FIRESKY_TILEERROR:"):
            try:
                self.tileError.emit(int(text.split(":", 1)[1]))
            except (ValueError, TypeError):
                pass
        super().javaScriptConsoleMessage(level, message, lineNumber, sourceId)


class FireSkyMap(QWebEngineView):
    """可嵌入 Qt 布局的地图选点控件。

    Signals:
        picked(lat, lon)   单击地图 / 拖动标记后落点
        ready()            HTML 与 Leaflet 初始化完成
        baseChanged(name)  用户切换底图类型（imagery / vector / terrain）
        sourceChanged(name) 用户切换底图源（tianditu / esri）

    ``mini=True`` 用于单点预测页的内嵌小卡：隐藏浮层面板与缩放按钮、
    关闭滚轮缩放（滚轮让给外层页面滚动），保留点击落点与拖动标记。
    """

    picked = Signal(float, float)
    ready = Signal()
    loadFailed = Signal()
    baseChanged = Signal(str)
    sourceChanged = Signal(str)
    tileError = Signal(int)  # 瓦片连续失败（Key 无效 / 网络断开提示）
    jsError = Signal(str)  # 页面 JS 未捕获异常（window.onerror 兜底上报）

    def __init__(self, *, dark: bool | None = None, source: str | None = None,
                 mini: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._dark = theme.is_dark() if dark is None else bool(dark)
        self._source = source if source in SOURCES else map_source()
        self._mini = bool(mini)
        self._loaded = False
        self._load_failed = False
        self._pending: list[str] = []
        self._last_view: tuple[float, float, int, str] | None = None

        page = _BridgePage(self)
        self.setPage(page)
        page.pointPicked.connect(self.picked)
        page.baseChanged.connect(self._on_base_changed)
        page.sourceChanged.connect(self._on_source_changed)
        page.tileError.connect(self.tileError)
        page.jsError.connect(self.jsError)

        settings = self.settings()
        # file:// 页面加载同目录下的 leaflet.js / leaflet.css 必须显式允许，
        # 否则页面脚本静默不执行（表现为 "L is not defined"）。
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        # 瓦片是 https 远程资源，需允许本地页面访问远程内容。
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # 容器尺寸变化后 Leaflet 不会自动重算视口（它只监听 window resize），
        # 必须显式 invalidateSize，否则瓦片只铺满旧尺寸、边缘露底色。
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self.invalidate)
        self.loadFinished.connect(self._on_load_finished)
        self.load(QUrl.fromLocalFile(str(MAP_HTML)))

    # ── 生命周期 ────────────────────────────────────────────────────────
    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            # 加载失败（离线等）：保留 _loaded=False 状态以便下次注入前重放，
            # 但队列设上限防止无限堆积；通知外层展示可重试的错误态。
            self._load_failed = True
            self._pending = self._pending[-32:]
            self.loadFailed.emit()
            return
        self._loaded = True
        self._load_failed = False
        self._inject()
        if self._last_view:
            lat, lon, zoom, name = self._last_view
            self.page().runJavaScript(
                f"window.fsSetView({lat:.6f},{lon:.6f},{int(self._clamped_zoom(zoom))},"
                f"{json.dumps(name)});"
            )
        for script in self._pending:
            self.page().runJavaScript(script)
        self._pending.clear()
        self.ready.emit()

    def reload(self) -> None:
        """加载失败后的手动重试入口（保留待注入队列与视野）。"""
        self._load_failed = False
        self.load(QUrl.fromLocalFile(str(MAP_HTML)))

    def _inject(self) -> None:
        """把当前 Key / 主题 / 源 / 小地图模式推给页面，必要时重建图层。"""
        key = tianditu_key()
        script = (
            f"window.FIRESKY_TDT_KEY={json.dumps(key)};"
            f"window.fsInit({json.dumps(key)}, {json.dumps(self._dark)},"
            f"{json.dumps(self._source)}, {json.dumps(self._mini)});"
        )
        self._js(script)

    def _js(self, script: str) -> None:
        if self._loaded:
            self.page().runJavaScript(script)
        elif len(self._pending) < 64:
            # 未加载完成时排队；加载失败/长期不 ready 时封顶，避免无限堆积。
            self._pending.append(script)

    @staticmethod
    def _clamped_zoom(zoom: int) -> int:
        """Leaflet 支持的 zoom 范围钳制：越界值会让 map.setView 抛 RangeError。"""
        return max(0, min(18, int(zoom)))

    def _on_source_changed(self, name: str) -> None:
        if name not in SOURCES:
            return
        self._source = name
        set_map_source(name)
        self.sourceChanged.emit(name)

    def _on_base_changed(self, name: str) -> None:
        if name in BASEMAPS:
            set_map_basemap(name)
        self.baseChanged.emit(name)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        # 从隐藏状态恢复时 WebEngine 需要重新测量视口，否则渲染区域留白。
        QTimer.singleShot(90, self.invalidate)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._resize_timer.start(120)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if self._mini:
            # 小地图嵌在可滚动页面里：忽略滚轮，让事件冒泡给外层 QScrollArea，
            # 否则鼠标停在地图上方就滚不动页面。
            event.ignore()
            return
        super().wheelEvent(event)

    def on_theme_changed(self) -> None:
        """主窗口切换主题时由 AppShell 统一回调。"""
        self.set_theme(theme.is_dark())

    # ── Python → JS ────────────────────────────────────────────────────
    def set_view(self, lat: float, lon: float, zoom: int = 9,
                 name: str = "") -> None:
        self._last_view = (float(lat), float(lon), self._clamped_zoom(zoom), name)
        self._js(
            f"window.fsSetView({float(lat):.6f},{float(lon):.6f},"
            f"{self._clamped_zoom(zoom)},{json.dumps(name)});"
        )

    def set_name(self, name: str, status: str = "") -> None:
        self._js(f"window.fsSetName({json.dumps(name)},{json.dumps(status)});")

    def set_theme(self, dark: bool) -> None:
        self._dark = bool(dark)
        self._js(f"window.fsSetTheme({json.dumps(self._dark)});")

    def set_basemap(self, name: str) -> None:
        self._js(f"window.fsSetBase({json.dumps(name)});")

    def set_source(self, name: str) -> None:
        """切换底图源：tianditu（境内，需 Key）/ esri（境外，免 Key）。"""
        if name not in SOURCES:
            return
        self._source = name
        self._js(f"window.fsSetSource({json.dumps(name)});")

    def set_mini(self, on: bool) -> None:
        self._mini = bool(on)
        self._js(f"window.fsSetMini({json.dumps(self._mini)});")

    def source(self) -> str:
        return self._source

    def is_mini(self) -> bool:
        return self._mini

    def fit_bounds(self, west: float, south: float,
                   east: float, north: float) -> None:
        self._js(
            f"window.fsFitBounds({west:.5f},{south:.5f},{east:.5f},{north:.5f});"
        )

    def invalidate(self) -> None:
        self._js("window.fsInvalidate();")

    def reload_with_key(self, key: str) -> bool:
        """用户在设置里填入 Key 后立即生效（无需重启应用）。

        Key 格式不合法时拒绝保存并返回 False，由调用方提示，避免把垃圾
        字符串存进配置导致瓦片 403 还显示「已配置」。
        """
        if not set_tianditu_key(key):
            return False
        self._inject()
        return True

    def refresh_key(self) -> None:
        """按当前生效的 Key 重新注入（环境变量或配置变化后调用）。"""
        self._inject()


# 兼容旧引用
TianDiTuMap = FireSkyMap
