"""FireSky 桌面应用共享工具：观测点持久化 + 小部件。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QLabel

APP_DIR = Path.home() / ".firesky-desktop"
LOCATIONS_FILE = APP_DIR / "locations.json"
CONFIG_FILE = APP_DIR / "config.json"


@dataclass
class Location:
    """观测点收藏（~/.firesky-desktop/locations.json 持久化）。"""

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
        return locations or [Location.from_dict(x.to_dict()) for x in DEFAULT_LOCATIONS]
    except Exception:  # noqa: BLE001 - 损坏文件回退默认收藏
        return [Location.from_dict(x.to_dict()) for x in DEFAULT_LOCATIONS]


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


def save_app_config(config: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), "utf-8"
    )


def make_card(sunken: bool = False) -> QFrame:
    """设计稿风格的圆角卡片容器。"""
    frame = QFrame()
    frame.setProperty("card", not sunken)
    frame.setProperty("cardSunken", sunken)
    return frame


def card_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "cardTitle")
    return label


def ink2_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "ink2")
    return label


def fmt(value: Any, pattern: str = "{}", default: str = "—") -> str:
    if value is None:
        return default
    try:
        return pattern.format(value)
    except (TypeError, ValueError, KeyError):
        return str(value)


def local_time_label(iso: str | None, with_seconds: bool = False) -> str:
    """ISO 本地时刻 → HH:MM（或 HH:MM:SS）。"""
    if not iso:
        return "—"
    text = str(iso)
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(text)
        return parsed.strftime("%H:%M:%S" if with_seconds else "%H:%M")
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


def level_color_for(value: float) -> str:
    """质量指数 → 档位色（与引擎 fire_cloud_quality_level 阈值一致）。"""
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


class QualityCurve(QFrame):
    """逐五分钟质量指数曲线（QPainter 自绘，带档位色带底衬 + 峰值标注）。"""

    MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 46, 22, 14, 26

    def __init__(self, height: int = 180) -> None:
        super().__init__()
        self._steps: list[tuple[str, float, bool]] = []  # (时间, 指数, 有效)
        self.setMinimumHeight(height)

    def set_steps(self, steps: list[tuple[str, float, bool]]) -> None:
        self._steps = list(steps)
        self.update()

    def has_data(self) -> bool:
        return bool(self._steps)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        from desktop.theme import palette

        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(
            self.MARGIN_L, self.MARGIN_T, -self.MARGIN_R, -self.MARGIN_B
        )
        painter.setPen(QColor(p["border"]))
        painter.setBrush(QColor(p["card_sunken"]))
        painter.drawRoundedRect(rect, 8, 8)

        if not self._steps:
            painter.setPen(QColor(p["ink3"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        valid_steps = [(t, v, ok) for t, v, ok in self._steps if ok]
        max_v = 2.5
        n = len(self._steps)
        step_w = rect.width() / max(1, n - 1) if n > 1 else 0.0

        def to_xy(index: int, value: float) -> tuple[float, float]:
            x = rect.left() + index * step_w
            y = rect.bottom() - (min(max(value, 0.0), max_v) / max_v) * rect.height()
            return x, y

        # 档位色带底衬（0.001/0.05/0.2/0.4/0.6/0.8/1.0/1.5/2.0 阈值带，
        # 低不透明度铺底，曲线在上层仍清晰可读）
        bands = [
            (0.001, "#9e9e9e"), (0.05, "#a8a29e"), (0.20, "#d6b56a"),
            (0.40, "#e09b3d"), (0.60, "#ea7c2c"), (0.80, "#e05a1e"),
            (1.00, "#d43d1a"), (1.50, "#c0261b"), (2.00, "#a31a1f"),
            (2.51, "#7d0f1e"),
        ]
        painter.save()
        painter.setClipRect(rect)
        lower = 0.0
        for upper, color in bands:
            top = rect.bottom() - (min(upper, max_v) / max_v) * rect.height()
            bottom = rect.bottom() - (lower / max_v) * rect.height()
            band_color = QColor(color)
            band_color.setAlpha(38)
            painter.fillRect(
                rect.left(), int(top),
                rect.width(), max(1, int(bottom - top)),
                band_color,
            )
            lower = upper
        painter.restore()

        # 参考线
        grid_color = QColor(128, 120, 108, 70)
        grid_pen = QPen(grid_color)
        painter.setPen(grid_pen)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for level in (0.0, 1.0, 2.0):
            y = rect.bottom() - (level / max_v) * rect.height()
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            painter.setPen(QColor(p["ink3"]))
            painter.drawText(rect.left() - 36, int(y) + 4, f"{level:g}")
            painter.setPen(grid_pen)

        # 有效区间阴影连接
        points = [to_xy(i, v) for i, (_, v, ok) in enumerate(self._steps)]
        line_color = QColor(p["accent"])
        pen = QPen(line_color, 2)
        painter.setPen(pen)
        prev: tuple[float, float] | None = None
        for i, (_, _, ok) in enumerate(self._steps):
            point = points[i]
            if prev is not None and ok:
                painter.drawLine(int(prev[0]), int(prev[1]), int(point[0]), int(point[1]))
            prev = point

        # 峰值标注
        if valid_steps:
            best_i = max(
                (i for i, (_, _, ok) in enumerate(self._steps) if ok),
                key=lambda i: self._steps[i][1],
            )
            bx, by = points[best_i]
            painter.setBrush(QColor(p["accent"]))
            painter.setPen(QColor(p["accent"]))
            painter.drawEllipse(int(bx) - 4, int(by) - 4, 8, 8)
            painter.setPen(QColor(p["ink"]))
            label = f"峰值 {self._steps[best_i][1]:.2f} · {self._steps[best_i][0]}"
            painter.drawText(int(bx) - 46, int(by) - 10, label)

        # 时间轴首尾标签
        painter.setPen(QColor(p["ink3"]))
        painter.drawText(rect.left(), self.height() - 8, self._steps[0][0])
        if n > 1:
            painter.drawText(
                rect.right() - 62, self.height() - 8, self._steps[-1][0]
            )


def draw_pin(painter: QPainter, x: float, y: float, color: str) -> None:
    """小圆点 pin（区域结果表格内使用）。"""
    painter.setPen(QPen(QColor(color)))
    painter.setBrush(QColor(color))
    painter.drawEllipse(int(x) - 4, int(y) - 4, 8, 8)
