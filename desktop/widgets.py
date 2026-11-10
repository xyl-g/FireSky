"""FireSky 桌面应用共享组件 · 暮光玻璃 v4。

包含：
- Location / 收藏持久化 / 应用配置（保持原有 JSON 契约）
- SpotStore：全局唯一观测点状态（单点预测与实况观测共享）
- GlassCard / SegmentedControl / MetricCard / chip / role_label 等 UI 基座
- MiniMap：纯本地自绘的示意选点底图（无第三方瓦片、无国界/行政区要素）
- QualityCurve：逐五分钟质量曲线（渐变面积 + 峰值标注）
- Toast：点位变更等轻提示

地图合规说明：MiniMap 是 UI 示意组件，不承载真实地理边界数据，不接入
在线地图服务。需要真实底图时应改用合规白名单内的服务并自备 Key。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from desktop import theme


class NumericItem(QTableWidgetItem):
    """数值排序表项：按 EditRole 里的浮点值参与列排序。

    QTableWidgetItem 默认排序按 DisplayRole 文本（'100.000' < '25.000'
    字符串序），构造时传入排序键即可数值比较；回退到文本比较兜底。
    """

    def __init__(self, text: str = "", sort_key: float | None = None) -> None:
        super().__init__(text)
        if sort_key is not None:
            self.setData(Qt.ItemDataRole.EditRole, float(sort_key))

    def __lt__(self, other) -> bool:  # noqa: N802 - Qt 命名
        mine = self.data(Qt.ItemDataRole.EditRole)
        theirs = other.data(Qt.ItemDataRole.EditRole)
        try:
            return float(mine) < float(theirs)
        except (TypeError, ValueError):
            return super().__lt__(other)

APP_DIR = Path.home() / ".firesky-desktop"
LOCATIONS_FILE = APP_DIR / "locations.json"
CONFIG_FILE = APP_DIR / "config.json"


# ── 观测点数据 ────────────────────────────────────────────────────────────

@dataclass
class Location:
    """观测点收藏（~/.firesky-desktop/locations.json 持久化）。"""

    # 「DEM 尚未回填」哨兵值：引擎海拔合法域是 -500..9000 m，取域外 -1
    # 避免与真实海平面 0 m 混淆。提交预测前必须已被 DEM 或手动值替换。
    UNQUERIED_ELEVATION_M = -1.0

    name: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    timezone_name: str = "Asia/Shanghai"
    starred: bool = False
    elevation_source: str = "manual"  # manual | dem

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Location":
        return cls(
            name=str(data.get("name") or "观测点"),
            lat=float(data.get("lat") or 0.0),
            lon=float(data.get("lon") or 0.0),
            elevation_m=float(data.get("elevation_m") or 0.0),
            timezone_name=str(data.get("timezone_name") or "Asia/Shanghai"),
            starred=bool(data.get("starred") or False),
            elevation_source=str(data.get("elevation_source") or "manual"),
        )

    def coord_label(self) -> str:
        return f"{self.lat:.2f}, {self.lon:.2f}"

    def coord_text(self) -> str:
        lat_hemisphere = "N" if self.lat >= 0 else "S"
        lon_hemisphere = "E" if self.lon >= 0 else "W"
        return (f"{abs(self.lat):.4f}° {lat_hemisphere}, "
                f"{abs(self.lon):.4f}° {lon_hemisphere}")

    def utc_offset_label(self) -> str:
        """时区 chip 文案：按实际 IANA 时区显示 UTC 偏移（如「时区 UTC+8」）。"""
        offset_minutes = 480
        try:
            from zoneinfo import ZoneInfo

            offset = datetime.now(ZoneInfo(self.timezone_name)).utcoffset()
            if offset is not None:
                offset_minutes = round(offset.total_seconds() / 60.0)
        except Exception:  # noqa: BLE001 - 时区库缺失时回退按经度估算
            offset_minutes = round(self.lon / 15.0) * 60
        sign = "+" if offset_minutes >= 0 else "-"
        hours = abs(offset_minutes) // 60
        minutes = abs(offset_minutes) % 60
        return f"时区 UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")

    def copy(self) -> "Location":
        return Location.from_dict(self.to_dict())


DEFAULT_LOCATIONS: list[Location] = [
    Location("杭州", 30.2741, 120.1551, 8.0, "Asia/Shanghai", True, "manual"),
    Location("北京", 39.9042, 116.4074, 44.0, "Asia/Shanghai"),
    Location("上海", 31.2304, 121.4737, 4.0, "Asia/Shanghai"),
    Location("广州", 23.1291, 113.2644, 11.0, "Asia/Shanghai"),
    Location("成都", 30.5728, 104.0668, 500.0, "Asia/Shanghai"),
    Location("西安", 34.3416, 108.9398, 405.0, "Asia/Shanghai"),
    Location("拉萨", 29.6520, 91.1721, 3650.0, "Asia/Shanghai"),
    Location("哈尔滨", 45.8038, 126.5350, 150.0, "Asia/Shanghai"),
]


def load_locations() -> list[Location]:
    try:
        LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = json.loads(LOCATIONS_FILE.read_text("utf-8"))
        items = raw.get("locations") if isinstance(raw, dict) else raw
        locations = [Location.from_dict(item) for item in items or []]
        return locations or [x.copy() for x in DEFAULT_LOCATIONS]
    except Exception:  # noqa: BLE001 - 损坏文件回退默认收藏
        # 回退前把损坏文件留档：否则下次 save_locations 会覆盖原文件，
        # 用户的自定义收藏将不可恢复。
        try:
            if LOCATIONS_FILE.exists():
                backup = LOCATIONS_FILE.with_suffix(
                    f".corrupt-{int(datetime.now().timestamp())}.json"
                )
                LOCATIONS_FILE.replace(backup)
        except OSError:
            pass
        return [x.copy() for x in DEFAULT_LOCATIONS]


def save_locations(locations: list[Location]) -> None:
    LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATIONS_FILE.write_text(
        json.dumps(
            {"version": 1, "locations": [item.to_dict() for item in locations]},
            ensure_ascii=False,
            indent=2,
        ),
        "utf-8",
    )


def load_app_config() -> dict[str, Any]:
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        return json.loads(CONFIG_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def timezone_for_point(lat: float, lon: float) -> str:
    """新建点位默认时区：优先复用引擎权威 lookup（timezonefinder 精确到
    IANA 名），失败时按经度每 15° 一个时区回退到 Etc/GMT*，避免把境外
    点位按 UTC+8 计算日出/日落锚点。"""
    try:
        from firesky.web_server import point_timezone

        return point_timezone(float(lat), float(lon))
    except Exception:  # noqa: BLE001 - 引擎不可用时本地估算
        offset = round(float(lon) / 15.0)
        if offset == 0:
            return "Etc/GMT-0"
        # IANA Etc/GMT 使用反号：UTC+8 -> Etc/GMT-8。
        sign = "-" if offset >= 0 else "+"
        return f"Etc/GMT{sign}{abs(offset)}"


def save_app_config(config: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 配置文件含 API Key 等敏感内容：收紧到仅本用户可读写。
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), "utf-8"
    )
    try:
        CONFIG_FILE.chmod(0o600)
        CONFIG_FILE.parent.chmod(0o700)
    except OSError:
        pass


class SpotStore(QObject):
    """全局唯一「当前观测点」，单点预测 / 实况观测 / 卫星足迹共享。

    写入口只有 set_location()；页面订阅 changed 信号同步自己的展示，
    避免各页各自维护一份点位副本。
    """

    changed = Signal(object)  # Location

    def __init__(self, initial: Location | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._location = initial or self._restore() or DEFAULT_LOCATIONS[0].copy()

    @staticmethod
    def _restore() -> Location | None:
        raw = load_app_config().get("current_spot")
        return Location.from_dict(raw) if isinstance(raw, dict) else None

    def location(self) -> Location:
        return self._location.copy()

    def set_location(self, loc: Location, *, persist: bool = True) -> None:
        self._location = loc.copy()
        if persist:
            config = load_app_config()
            config["current_spot"] = self._location.to_dict()
            save_app_config(config)
        self.changed.emit(self._location)


# ── 文本 / 卡片基座 ────────────────────────────────────────────────────────

class ElidedLabel(QLabel):
    """单行动态名称按可用宽度省略，完整内容仍可悬停读取。"""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full_text = text
        self.setToolTip(text)
        self._fit_text()

    def _fit_text(self) -> None:
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width())
        ))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._fit_text()


def role_label(text: str = "", role: str = "ink2", *, elide: bool = False) -> QLabel:
    label = ElidedLabel(text) if elide else QLabel(text)
    label.setProperty("role", role)
    return label


def card_title(text: str) -> QLabel:
    return role_label(text, "cardTitle")


def ink2_label(text: str = "") -> QLabel:
    return role_label(text, "ink2")


def section_title(text: str) -> QLabel:
    return role_label(text, "h2")


def chip(text: str, tone: str = "") -> QLabel:
    label = role_label(text, "chip")
    if tone:
        label.setProperty("tone", tone)
    theme.repolish(label)
    return label


def fmt(value: Any, pattern: str = "{}", default: str = "—") -> str:
    if value is None:
        return default
    try:
        return pattern.format(value)
    except (TypeError, ValueError, KeyError):
        return str(value)


def local_time_label(iso: str | None, with_seconds: bool = False) -> str:
    if not iso:
        return "—"
    text = str(iso)
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).strftime(
            "%H:%M:%S" if with_seconds else "%H:%M"
        )
    except ValueError:
        return text


def format_agl_meters(value: Any) -> str:
    if value is None:
        return "—"
    try:
        meters = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{meters / 1000.0:.1f} km" if meters >= 1000.0 else f"{meters:.0f} m"


def format_elevation_m(elevation_m: float) -> str:
    """观测点海拔展示：未查询哨兵显示「—」而不是误导性的「0 m」。"""
    if elevation_m is None or float(elevation_m) <= Location.UNQUERIED_ELEVATION_M:
        return "—"
    return f"{float(elevation_m):.0f} m"


def level_color_for(value: float) -> str:
    if value < 0.001:
        return "#9e9e9e"
    if value < 0.05:
        return "#a8a29e"
    if value < 0.20:
        return "#d6b56a"
    if value < 0.40:
        return "#e09b3d"
    if value < 0.60:
        return "#ea7c2c"
    if value < 0.80:
        return "#e05a1e"
    if value < 1.00:
        return "#d43d1a"
    if value < 1.50:
        return "#c0261b"
    if value < 2.00:
        return "#a31a1f"
    return "#7d0f1e"


class GlassCard(QFrame):
    """Translucent surface with a restrained refractive edge."""

    def __init__(self, *, strong: bool = False, radius: int = 8,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("glass", "true")
        self._strong = bool(strong)
        self._radius = min(8, int(radius))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        base = QColor(p["glass_strong"] if self._strong else p["glass"])
        alpha = p["glass_strong_a"] if self._strong else p["glass_a"]
        base.setAlpha(alpha)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        top = QColor(base)
        top.setAlpha(min(245, alpha + 28))
        bottom = QColor(base)
        bottom.setAlpha(max(60, alpha - 32))
        gradient.setColorAt(0, top)
        gradient.setColorAt(0.55, base)
        gradient.setColorAt(1, bottom)
        painter.setBrush(gradient)

        border = QColor(p["line_rgb"])
        border.setAlpha(p["border_a"])
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(rect, self._radius, self._radius)

        highlight = QColor(p["highlight"])
        highlight.setAlpha(p["highlight_a"])
        painter.setPen(QPen(highlight, 1))
        inset = self._radius * 0.6
        painter.drawLine(
            QPointF(rect.left() + inset, rect.top() + 1),
            QPointF(rect.right() - inset, rect.top() + 1),
        )


class ModelResultsBar(QWidget):
    """Result navigation only; both models are always part of the request."""

    modelChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.tabs = SegmentedControl(["GFS", "EC"])
        self.tabs.setFixedWidth(230)
        self.tabs.valueChanged.connect(
            lambda index, _: self.modelChanged.emit(("gfs", "ec")[index])
        )
        layout.addWidget(self.tabs)
        self.status = role_label("", "ink3", elide=True)
        layout.addWidget(self.status, 1)
        self.hide()

    def set_results(self, results, errors):
        self.show()
        for index, model in enumerate(("gfs", "ec")):
            button = self.tabs._buttons[index]
            result = results.get(model)
            available = result is not None
            button.setEnabled(available)
            score = result.get("peak_quality_index") if available else None
            if score is None and available and "points" in result:
                values = [p.get("peak_quality_index") for p in result["points"]]
                score = max((v for v in values if v is not None), default=None)
            button.setText(
                f"{model.upper()} · {float(score):.2f}" if score is not None
                else f"{model.upper()} · {'已完成' if available else '未完成'}"
            )
            button.setToolTip(errors.get(model) or f"查看 {model.upper()} 结果")
        status = (
            "；".join(f"{model.upper()} 未完成：{message}" for model, message in errors.items())
            if errors else "双模式完成"
        )
        self.status.setText(status)
        self.status.setToolTip(status)
        model = "gfs" if "gfs" in results else "ec"
        self.tabs.set_index(0 if model == "gfs" else 1, emit=False)
        return model


def make_card(sunken: bool = False) -> GlassCard:
    """兼容旧页面：返回玻璃卡片容器。"""
    return GlassCard(strong=not sunken)


class SegmentedControl(QFrame):
    """分段选择器（[地图 | 收藏]、[今天 | 明天] 等）。"""

    valueChanged = Signal(int, str)

    def __init__(self, labels: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("plain", "true")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        self._buttons: list[QPushButton] = []
        self._labels = list(labels)
        self._index = 0
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, text in enumerate(labels):
            button = QPushButton(text)
            button.setObjectName("segmentBtn")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("active", "false")
            button.clicked.connect(lambda _=False, i=index: self.set_index(i))
            group.addButton(button)
            layout.addWidget(button, 1)
            self._buttons.append(button)
        self.set_index(0, emit=False)

    def set_index(self, index: int, *, emit: bool = True) -> None:
        self._index = max(0, min(len(self._buttons) - 1, int(index)))
        for i, button in enumerate(self._buttons):
            active = i == self._index
            button.setChecked(active)
            button.setProperty("active", "true" if active else "false")
            theme.repolish(button)
        if emit:
            self.valueChanged.emit(self._index, self._labels[self._index])

    def index(self) -> int:
        return self._index


class RunButton(QPushButton):
    """跟随主题的主操作按钮，包含悬停、按下与键盘焦点反馈。"""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("runBtn")
        self.setMinimumHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        enabled = self.isEnabled()
        if enabled:
            color = p["accent_pressed"] if self.isDown() else (
                p["accent_hover"] if self.underMouse() else p["accent"]
            )
            painter.setBrush(QColor(color))
        else:
            painter.setBrush(QColor(p["border_strong"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 8, 8)
        if self.hasFocus():
            painter.setPen(QPen(QColor(p["ink"]), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 4, 4)
        if enabled:
            highlight = QColor("#ffffff")
            highlight.setAlpha(80)
            painter.setPen(QPen(highlight, 1))
            painter.drawLine(
                QPointF(rect.left() + 14, rect.top() + 1),
                QPointF(rect.right() - 14, rect.top() + 1),
            )
        font = QFont()
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text_color = p["canvas"] if theme.is_dark() else "#ffffff"
        painter.setPen(QColor(text_color if enabled else p["faint"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())


class MetricCard(GlassCard):
    """指标小卡：标题 + 数值 + 说明。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(radius=14, parent=parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        layout.addWidget(role_label(title, "ink3"))
        self.value_label = role_label("—", "metric")
        self.value_label.setStyleSheet(f"color: {theme.palette()['faint']};")
        layout.addWidget(self.value_label)
        self.sub_label = role_label("", "hint")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.sub_label)
        layout.addStretch(1)

    def set_value(self, text: str, color: str = "") -> None:
        self.value_label.setText(text)
        if color:
            self.value_label.setStyleSheet(f"color: {color};")
        else:
            self.value_label.setStyleSheet("")

    def set_sub(self, text: str) -> None:
        self.sub_label.setText(text)


# ── 本地示意选点底图 ──────────────────────────────────────────────────────
# 不绘制国界 / 行政区划 / 真实海岸线，仅为 UI 选点提供空间参照。
# u = 横向 0..1（经度方向），v = 纵向 0..1（纬度自上而下）。

LAT_MIN, LAT_MAX = 18.0, 54.0
LON_MIN, LON_MAX = 73.0, 135.0

_LAND_SHAPES: list[list[tuple[float, float]]] = [
    [(0.00, 0.00), (0.70, 0.00), (0.76, 0.10), (0.68, 0.21), (0.79, 0.33),
     (0.73, 0.50), (0.82, 0.64), (0.70, 0.76), (0.62, 0.96), (0.00, 0.96)],
    [(0.84, 0.09), (0.91, 0.07), (0.93, 0.20), (0.87, 0.27)],
    [(0.75, 0.70), (0.79, 0.69), (0.78, 0.84), (0.75, 0.87)],
    [(0.36, 0.94), (0.42, 0.92), (0.44, 0.98), (0.37, 1.00)],
]
_RIVERS: list[list[tuple[float, float]]] = [
    [(0.08, 0.34), (0.25, 0.39), (0.41, 0.35), (0.57, 0.45), (0.73, 0.43)],
    [(0.34, 0.02), (0.41, 0.28), (0.37, 0.57), (0.49, 0.88)],
]
_ROADS: list[list[tuple[float, float]]] = [
    [(0.05, 0.20), (0.30, 0.16), (0.55, 0.24), (0.79, 0.17)],
    [(0.12, 0.68), (0.36, 0.62), (0.60, 0.70), (0.82, 0.60)],
    [(0.46, 0.04), (0.50, 0.34), (0.46, 0.64), (0.55, 0.94)],
]
_CITY_LABELS: list[tuple[str, float, float]] = [
    ("北京", 39.9042, 116.4074),
    ("上海", 31.2304, 121.4737),
    ("杭州", 30.2741, 120.1551),
    ("广州", 23.1291, 113.2644),
    ("成都", 30.5728, 104.0668),
]


def latlon_to_uv(lat: float, lon: float) -> tuple[float, float]:
    u = (float(lon) - LON_MIN) / (LON_MAX - LON_MIN)
    v = (LAT_MAX - float(lat)) / (LAT_MAX - LAT_MIN)
    return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))


def uv_to_latlon(u: float, v: float) -> tuple[float, float]:
    lon = LON_MIN + min(1.0, max(0.0, u)) * (LON_MAX - LON_MIN)
    lat = LAT_MAX - min(1.0, max(0.0, v)) * (LAT_MAX - LAT_MIN)
    return round(lat, 4), round(lon, 4)


class MiniMap(QFrame):
    """可点击落点的本地示意底图。

    - picked(lat, lon)：点击后回传经纬度（按示意范围线性映射）
    - set_location()：把 pin 定位到观测点，必要时平移视野
    - compact=True：缩略图模式（省略标签/提示/缩放按钮）
    - 支持 +/− 缩放（改变视野范围，坐标映射同步变化）
    """

    picked = Signal(float, float)
    clicked = Signal()  # 非交互（缩略图）模式下的点击入口

    def __init__(self, *, compact: bool = False, interactive: bool = True,
                 show_pin: bool = True, show_controls: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("glass", "true")
        self._compact = bool(compact)
        self._interactive = bool(interactive)
        self._show_pin = bool(show_pin)
        self._show_controls = bool(show_controls)
        self._view = [0.0, 0.0, 1.0, 1.0]  # u0, v0, uw, vh
        self._pin: tuple[float, float] = latlon_to_uv(30.2741, 120.1551)
        self._markers: list[tuple[float, float, float]] = []  # lat, lon, score
        self._zoom_rects: list[tuple[QRectF, float]] = []
        self.setMinimumHeight(64 if compact else 150)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor
        )

    # ── 视图 ──
    def _project(self, u: float, v: float) -> QPointF:
        u0, v0, uw, vh = self._view
        return QPointF(
            (u - u0) / uw * self.width(),
            (v - v0) / vh * self.height(),
        )

    def _unproject(self, point: QPointF) -> tuple[float, float]:
        u0, v0, uw, vh = self._view
        return (
            min(1.0, max(0.0, u0 + point.x() / max(1, self.width()) * uw)),
            min(1.0, max(0.0, v0 + point.y() / max(1, self.height()) * vh)),
        )

    def _zoom(self, factor: float) -> None:
        u0, v0, uw, vh = self._view
        new_w, new_h = min(1.0, max(0.22, uw / factor)), min(1.0, max(0.22, vh / factor))
        cu, cv = u0 + uw / 2, v0 + vh / 2
        nu0 = min(1 - new_w, max(0, cu - new_w / 2))
        nv0 = min(1 - new_h, max(0, cv - new_h / 2))
        self._view = [nu0, nv0, new_w, new_h]
        self.update()

    def set_location(self, loc: Location) -> None:
        u, v = latlon_to_uv(loc.lat, loc.lon)
        self._pin = (u, v)
        u0, v0, uw, vh = self._view
        if not (u0 <= u <= u0 + uw and v0 <= v <= v0 + vh):
            nu0 = min(1 - uw, max(0, u - uw / 2))
            nv0 = min(1 - vh, max(0, v - vh / 2))
            self._view = [nu0, nv0, uw, vh]
        self.update()

    def set_markers(self, items: list[tuple[float, float, float]]) -> None:
        """区域结果气泡点（纬度、经度、峰值指数）。

        底图只覆盖中国范围：境外点位直接丢弃（不再钳位堆到边缘），由
        调用方统计越界数量提示用户。返回落在范围内的点数。
        """
        kept: list[tuple[float, float, float]] = []
        for lat, lon, score in items:
            if LAT_MIN <= float(lat) <= LAT_MAX and LON_MIN <= float(lon) <= LON_MAX:
                kept.append((float(lat), float(lon), float(score)))
        self._markers = kept
        self.update()
        return len(kept)

    def reset_view(self) -> None:
        self._view = [0.0, 0.0, 1.0, 1.0]
        self.update()

    # ── 交互 ──
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._interactive:
            self.clicked.emit()
            return
        pos = QPointF(event.position())
        for rect, factor in self._zoom_rects:
            if rect.contains(pos):
                self._zoom(factor)
                return
        u, v = self._unproject(pos)
        self._pin = (u, v)
        self.update()
        lat, lon = uv_to_latlon(u, v)
        self.picked.emit(lat, lon)

    # ── 绘制 ──
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = 10 if self._compact else 13
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setClipPath(clip)

        # 水面背景
        water = QColor(p["map_water"])
        painter.fillRect(self.rect(), water)

        land = QColor(p["map_land"])
        land_edge = QColor(p["line_rgb"])
        land_edge.setAlpha(p["border_a"])
        painter.setPen(QPen(land_edge, 1))
        painter.setBrush(land)
        for shape in _LAND_SHAPES:
            painter.drawPolygon(QPolygonF([self._project(u, v) for u, v in shape]))

        # 示意河流 / 路网
        river_pen = QPen(water.lighter(112), 4 if not self._compact else 2.5)
        river_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(river_pen)
        for line in _RIVERS:
            pts = [self._project(u, v) for u, v in line]
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)
        road = QColor(p["line_rgb"])
        road.setAlpha(p["border_strong_a"])
        painter.setPen(QPen(road, 1))
        for line in _ROADS:
            pts = [self._project(u, v) for u, v in line]
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)

        # 经纬网格（虚线）
        grid = QColor(p["line_rgb"])
        grid.setAlpha(max(12, p["border_a"]))
        grid_pen = QPen(grid, 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        for fraction in (0.25, 0.5, 0.75):
            x = self._project(fraction, 0).x()
            y = self._project(0, fraction).y()
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            painter.drawLine(QPointF(0, y), QPointF(self.width(), y))

        # 收藏城市标签（缩略图省略）
        if not self._compact:
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            label_color = QColor(p["ink3"])
            label_color.setAlpha(210)
            painter.setPen(label_color)
            for name, lat, lon in _CITY_LABELS:
                u, v = latlon_to_uv(lat, lon)
                point = self._project(u, v)
                if not (0 <= point.x() <= self.width() and 0 <= point.y() <= self.height()):
                    continue
                painter.setBrush(label_color)
                painter.drawEllipse(point, 2.0, 2.0)
                painter.drawText(point + QPointF(5, -4), name)

        # 区域结果气泡
        for lat, lon, score in self._markers:
            point = self._project(*latlon_to_uv(lat, lon))
            if not (0 <= point.x() <= self.width() and 0 <= point.y() <= self.height()):
                continue
            bubble = QColor(p["gold"] if score < 1.5 else p["accent"])
            bubble.setAlpha(200)
            painter.setBrush(bubble)
            edge = QColor(p["glass_strong"])
            painter.setPen(QPen(edge, 1))
            radius = min(8.0, 3.0 + float(score) * 1.8)
            painter.drawEllipse(point, radius, radius)

        # pin 光圈 + 水滴
        if not self._show_pin:
            painter.setClipping(False)
            self._zoom_rects = []
            return
        pin_point = self._project(*self._pin)
        pulse = QColor(p["accent"])
        pulse.setAlpha(42 if not self._compact else 34)
        painter.setBrush(pulse)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(pin_point, 15 if not self._compact else 9,
                            15 if not self._compact else 9)

        size = 13 if not self._compact else 8
        px, py = pin_point.x(), pin_point.y()
        top = py - size * 1.9
        path = QPainterPath()
        path.moveTo(px, py)
        path.cubicTo(
            QPointF(px - size * 0.25, top + size * 1.7),
            QPointF(px - size, top + size * 0.8),
            QPointF(px, top),
        )
        path.cubicTo(
            QPointF(px + size, top + size * 0.8),
            QPointF(px + size * 0.25, top + size * 1.7),
            QPointF(px, py),
        )
        path.closeSubpath()
        painter.setBrush(QColor(p["accent"]))
        edge = QColor(p["glass_strong"])
        painter.setPen(QPen(edge, 1.4))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(
            QPointF(pin_point.x(), top + size * 0.92), size * 0.34, size * 0.34
        )

        painter.setClipping(False)

        # 左下提示 / 右下缩放
        self._zoom_rects = []
        if not self._compact and self._show_controls:
            hint_color = QColor(p["glass_strong"])
            hint_color.setAlpha(p["glass_strong_a"])
            hint_rect = QRectF(9, self.height() - 31, 108, 22)
            painter.setBrush(hint_color)
            border = QColor(p["line_rgb"])
            border.setAlpha(p["border_a"])
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(hint_rect, 7, 7)
            painter.setPen(QColor(p["ink3"]))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(
                hint_rect.adjusted(8, 0, 0, 0),
                Qt.AlignmentFlag.AlignVCenter,
                "＋ 单击地图落点" if self._interactive else "当前观测点",
            )
            for index, (glyph, factor) in enumerate((("＋", 1.4), ("－", 1 / 1.4))):
                rect = QRectF(self.width() - 32, self.height() - 54 + index * 26, 23, 23)
                painter.setBrush(hint_color)
                painter.setPen(QPen(border, 1))
                painter.drawRoundedRect(rect, 7, 7)
                painter.setPen(QColor(p["ink2"]))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
                self._zoom_rects.append((rect, factor))


# ── 质量曲线 ──────────────────────────────────────────────────────────────

class QualityCurve(QFrame):
    """逐五分钟质量指数曲线：渐变面积 + 细网格 + 峰值标注。"""

    MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 38, 22, 14, 24

    def __init__(self, height: int = 164, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(height)
        self._steps: list[tuple[str, float, bool]] = []

    def set_steps(self, steps: list[tuple[str, float, bool]]) -> None:
        self._steps = list(steps)
        self.update()

    def has_data(self) -> bool:
        return bool(self._steps)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        p = theme.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(
            self.MARGIN_L, self.MARGIN_T, -self.MARGIN_R, -self.MARGIN_B
        )

        grid = QColor(p["chart_grid"])
        grid.setAlpha(p["chart_grid_a"])
        painter.setPen(QPen(grid, 1, Qt.PenStyle.DashLine))
        axis = QColor(p["faint"])
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        for level in (0.0, 1.25, 2.5):
            y = rect.bottom() - (level / 2.5) * rect.height()
            painter.setPen(QPen(grid, 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(axis)
            painter.drawText(QPointF(4, y + 3), f"{level:g}")

        if not self._steps:
            painter.setPen(QColor(p["faint"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "运行预测后显示质量曲线")
            return

        n = len(self._steps)
        step_w = rect.width() / max(1, n - 1)
        points = [
            QPointF(
                rect.left() + i * step_w,
                rect.bottom() - (min(max(value, 0.0), 2.5) / 2.5) * rect.height(),
            )
            for i, (_, value, _) in enumerate(self._steps)
        ]

        valid = [(i, v) for i, (_, v, ok) in enumerate(self._steps) if ok]
        best = max(valid, key=lambda pair: pair[1])[0] if valid else None

        # 最佳窗口高亮
        if best is not None and n > 1:
            start = max(0, best - 2)
            end = min(n - 1, best + 2)
            window = QRectF(
                QPointF(points[start].x(), rect.top()),
                QPointF(points[end].x(), rect.bottom()),
            )
            band = QColor(p["gold"])
            band.setAlpha(18)
            painter.setBrush(band)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(window, 7, 7)

        # 平滑曲线 + 渐变面积
        path = QPainterPath()
        path.moveTo(points[0])
        for i in range(1, len(points)):
            prev, cur = points[i - 1], points[i]
            path.quadTo(prev, QPointF((prev.x() + cur.x()) / 2, (prev.y() + cur.y()) / 2))
        path.lineTo(points[-1])

        area = QPainterPath(path)
        area.lineTo(points[-1].x(), rect.bottom())
        area.lineTo(points[0].x(), rect.bottom())
        area.closeSubpath()
        fill = QLinearGradient(0, rect.top(), 0, rect.bottom())
        accent = QColor(p["accent"])
        top_color = QColor(accent)
        top_color.setAlpha(70)
        bottom_color = QColor(accent)
        bottom_color.setAlpha(2)
        fill.setColorAt(0, top_color)
        fill.setColorAt(1, bottom_color)
        painter.setBrush(fill)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(area)

        line = QLinearGradient(rect.left(), 0, rect.right(), 0)
        line.setColorAt(0, QColor(p["gold"]))
        line.setColorAt(0.6, QColor(p["accent"]))
        line.setColorAt(1, QColor(p["red"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(line, 2.4))
        painter.drawPath(path)

        # 峰值点与标签
        if best is not None:
            bp = points[best]
            painter.setBrush(QColor(p["glass_strong"]))
            painter.setPen(QPen(QColor(p["accent"]), 2))
            painter.drawEllipse(bp, 5, 5)
            text = f"{self._steps[best][1]:.2f} · {self._steps[best][0]}"
            badge_w, badge_h = 84.0, 18.0
            # 徽章中心跟随峰值点但左右钳制在绘图区内，避免两端被裁剪。
            badge_x = min(
                rect.right() - badge_w,
                max(rect.left(), bp.x() - badge_w / 2),
            )
            badge = QRectF(badge_x, max(0.0, bp.y() - 26.0), badge_w, badge_h)
            painter.setBrush(QColor(p["glass_strong"]))
            edge = QColor(p["accent"])
            edge.setAlpha(90)
            painter.setPen(QPen(edge, 1))
            painter.drawRoundedRect(badge, 7, 7)
            painter.setPen(QColor(p["accent"]))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

        # 时间轴
        painter.setPen(QColor(p["faint"]))
        painter.drawText(QPointF(rect.left(), self.height() - 7), self._steps[0][0])
        if n > 1:
            painter.drawText(
                QPointF(rect.right() - 34, self.height() - 7), self._steps[-1][0]
            )


# ── 轻提示 ────────────────────────────────────────────────────────────────

class Toast(GlassCard):
    """右下角玻璃提示条，2.6 秒自动淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(strong=True, radius=12, parent=parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(9)
        dot = role_label("●", "hint")
        dot.setStyleSheet(f"color: {theme.palette()['ok']}; font-size: 10px;")
        layout.addWidget(dot)
        self._label = role_label("", "ink2")
        layout.addWidget(self._label)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def show_message(self, text: str) -> None:
        self._label.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move(
                max(8, parent.width() - self.width() - 26),
                max(8, parent.height() - self.height() - 24),
            )
        self._effect.setOpacity(0.0)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(2600)


def draw_pin(painter: QPainter, x: float, y: float, color: str) -> None:
    """小圆点 pin（兼容旧调用）。"""
    painter.setPen(QPen(QColor(color)))
    painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(x, y), 4, 4)
