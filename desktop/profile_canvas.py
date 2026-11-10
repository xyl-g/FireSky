"""FireSky 桌面应用 · 大气剖面绘制器（QPainter 自绘）。

对应 Web 端两个剖面组件（字段契约以 frontend 实现为准）：
- NationalProfileCanvas  ← 单点 payload ``cross_section``（模式大气剖面，
  对照 frontend NationalCrossSection.svelte：X=沿光路距离、Y=压力层
  几何高度 ASL、压力族云色块、云底/云顶包络线、受光阈值虚线、
  地形填充与观测点标记）
- SatelliteProfileCanvas ← nowcast payload ``nowcast.satellite_vertical_profile``
  （卫星剖面，对照 SatelliteVerticalProfile.svelte：X=沿光路样本、
  Y=高度，样本×压力层色块按 HSD 后验概率上色，缺失样本灰色斜纹）

两个画布都不持有外部依赖：set_data(...) 收 dict 快照，paintEvent 实时
读 desktop.theme 当前调色板（亮/暗主题自动适配），on_theme_changed()
由主窗口主题切换时调用触发重绘。
"""

from __future__ import annotations

import math
from typing import Any, Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QWidget

from desktop import theme

__all__ = [
    "NationalProfileCanvas",
    "SatelliteProfileCanvas",
    "FIELD_CLOUD",
    "FIELD_RH",
    "FIELD_TEMP",
]

# 显示字段切换（单点模式剖面的下拉框取值）
FIELD_CLOUD = "cloud"
FIELD_RH = "rh"
FIELD_TEMP = "temp"

# 模式剖面 Y 轴上限：与 Web 版 maxHeight=18000 一致（ASL 几何高度）
_NATIONAL_MAX_HEIGHT_M = 18_000.0

# 云色块半高：Web 版以 ±700 m 画每格云层矩形（压力层间距的视觉近似）
_CELL_HALF_HEIGHT_M = 700.0

_CLOUD_FAMILY_RGB = {
    1: (116, 157, 194),   # 高云压力族（蓝灰）
    2: (105, 154, 142),   # 中云压力族（青灰）
    3: (126, 136, 145),   # 低云压力族（灰）
}


def _finite(value: Any) -> bool:
    """引擎矩阵元素可能是 None：非有限值一律按缺测处理。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if _finite(value) else default


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _lerp_color(start: QColor, end: QColor, t: float) -> QColor:
    t = _clamp(t, 0.0, 1.0)
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
    )


def _axis_font(size: int = 8) -> QFont:
    font = QFont()
    font.setPointSize(size)
    return font


def _title_font() -> QFont:
    font = QFont()
    font.setPointSize(9)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _short_time(stamp: Any) -> str:
    return str(stamp or "").replace("T", " ")[:16]


class _ProfileCanvasBase(QFrame):
    """共用骨架：占位文案、标题行、色标条、主题重绘钩子。"""

    MARGIN_L, MARGIN_T, MARGIN_R, MARGIN_B = 50, 44, 20, 72

    def __init__(self, placeholder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(260)
        self._placeholder = placeholder
        self._has_data = False

    # ── 数据接口（子类覆盖后调用 update()）──────────────────────────────
    def set_data(self, data: dict | None) -> None:
        raise NotImplementedError

    def on_theme_changed(self) -> None:
        self.update()

    # ── 绘制共用 ────────────────────────────────────────────────────────
    def _plot_rect(self, rect: QRectF) -> QRectF:
        return QRectF(rect).adjusted(
            self.MARGIN_L, self.MARGIN_T, -self.MARGIN_R, -self.MARGIN_B
        )

    def _draw_title(self, painter: QPainter, rect: QRectF, title: str, meta: str) -> None:
        p = theme.palette()
        painter.setFont(_title_font())
        painter.setPen(QColor(p["ink"]))
        painter.drawText(QPointF(rect.left() + 4, rect.top() + 22), title)
        if meta:
            painter.setFont(_axis_font(8))
            painter.setPen(QColor(p["ink3"]))
            meta_rect = QRectF(rect.left() + 4, rect.top() + 4,
                               rect.width() - 12, 18)
            painter.drawText(meta_rect, Qt.AlignmentFlag.AlignRight, meta)

    def _draw_placeholder(self, painter: QPainter, rect: QRectF) -> None:
        p = theme.palette()
        painter.setFont(_axis_font(10))
        painter.setPen(QColor(p["faint"]))
        painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, self._placeholder)

    def _draw_legend_bar(
        self,
        painter: QPainter,
        rect: QRectF,
        bar: QRectF,
        start: QColor,
        end: QColor,
        start_label: str,
        end_label: str,
        caption: str,
    ) -> None:
        p = theme.palette()
        gradient = QLinearGradient(bar.left(), 0, bar.right(), 0)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)
        painter.setBrush(gradient)
        border = QColor(p["line_rgb"])
        border.setAlpha(p["border_strong_a"])
        painter.setPen(QPen(border, 1))
        painter.drawRoundedRect(bar, 3, 3)
        painter.setFont(_axis_font(7))
        painter.setPen(QColor(p["ink3"]))
        painter.drawText(QPointF(bar.left() - 2, rect.bottom() - 14), start_label)
        painter.drawText(QPointF(bar.right() - 14, rect.bottom() - 14), end_label)


class NationalProfileCanvas(_ProfileCanvasBase):
    """模式大气剖面：单点 payload ``cross_section``（对照 Web NationalCrossSection）。

    - X = 沿光路距离（0..analysis_radius_km），Y = 压力层几何高度 ASL（非线性）
    - 云层色块按显示字段上色：云量（压力族色）/ 相对湿度（棕→蓝）/ 温度（蓝→红）
    - 叠加 cloud_layer_base/top 包络线、cloud_layer_status 橙色描边、
      required_illumination_base_asl_m 受光阈值虚线、地形填充、观测点标记
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("运行预测后显示模式大气剖面（太阳方向 · 压力层）", parent)
        self._section: dict | None = None
        self._field = FIELD_CLOUD

    def set_data(self, section: dict | None) -> None:
        """传入 payload 顶层 ``cross_section``；None/空结构时显示占位。"""
        ok = (
            isinstance(section, dict)
            and isinstance(section.get("distances_km"), list)
            and bool(section.get("distances_km"))
            and isinstance(section.get("pressure_levels_hpa"), list)
            and bool(section.get("pressure_levels_hpa"))
        )
        self._section = section if ok else None
        self._has_data = ok
        self.update()

    def set_display_field(self, field: str) -> None:
        """切换色块字段：cloud（云量）/ rh（相对湿度）/ temp（温度）。"""
        self._field = field if field in (FIELD_CLOUD, FIELD_RH, FIELD_TEMP) else FIELD_CLOUD
        self.update()

    # ── 绘制 ────────────────────────────────────────────────────────────
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect())
        if not self._has_data or self._section is None:
            self._draw_title(painter, rect, "太阳方向大气剖面", "")
            self._draw_placeholder(painter, self._plot_rect(rect))
            return
        self._paint_section(painter, rect)

    def _paint_section(self, painter: QPainter, rect: QRectF) -> None:
        p = theme.palette()
        section = self._section
        distances = [_num(d) for d in section.get("distances_km") or []]
        pressures = [int(v) for v in section.get("pressure_levels_hpa") or []]
        fields = section.get("fields") if isinstance(section.get("fields"), dict) else {}
        row_count = len(distances)
        level_count = len(pressures)
        max_distance = max(
            distances[-1] if distances else 1.0,
            _num(section.get("analysis_radius_km"), 1.0),
            1.0,
        )
        observer = section.get("observer") if isinstance(section.get("observer"), dict) else {}
        terrain = section.get("terrain_elevation_asl_m") or []
        required_asl = section.get("required_illumination_base_asl_m") or []
        required_agl = section.get("required_illumination_base_agl_m") or []

        plot = self._plot_rect(rect)

        def x_of(distance: float) -> float:
            return plot.left() + (distance / max_distance) * plot.width()

        def y_of(height: float) -> float:
            clamped = _clamp(height, 0.0, _NATIONAL_MAX_HEIGHT_M)
            return plot.bottom() - (clamped / _NATIONAL_MAX_HEIGHT_M) * plot.height()

        def cell(field_key: str, i: int, j: int) -> Any:
            try:
                return (fields.get(field_key) or [])[i][j]
            except (IndexError, TypeError):
                return None

        # 标题：时刻 + 太阳方位/仰角 + 剖面范围
        model_source = str(section.get("model_source") or "gfs")
        model_label = "ECMWF IFS" if model_source == "ec" else "GFS"
        meta = (
            f"{_short_time(section.get('valid_time_local'))} · "
            f"方位 {_num(section.get('solar_azimuth_deg')):.1f}° · "
            f"太阳高度 {_num(section.get('solar_elevation_deg')):.1f}° · "
            f"范围 {_num(section.get('analysis_radius_km'), max_distance):.0f} km"
        )
        self._draw_title(painter, rect, f"太阳方向大气剖面 · {model_label}", meta)

        # 绘图区底色
        background = QColor(p["canvas"])
        glass = QColor(p["glass"])
        background = background.lighter(104) if not theme.is_dark() else _lerp_color(background, glass, 0.35)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRect(plot)

        grid = QColor(p["chart_grid"])
        grid.setAlpha(max(20, p["chart_grid_a"] + 8))
        axis_pen = QPen(grid, 1, Qt.PenStyle.DashLine)
        font = _axis_font(8)

        # 水平网格：0/3/6/.../18 km
        painter.setFont(font)
        for altitude in range(0, 18_001, 3_000):
            y = y_of(float(altitude))
            painter.setPen(axis_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(p["ink3"]))
            painter.drawText(QPointF(6, y + 3), f"{altitude // 1000}km")

        # 垂直网格：每 4 个距离样本（末样本标签右对齐，避免溢出画布）
        for index in range(0, row_count, 4):
            x = x_of(distances[index])
            painter.setPen(axis_pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QColor(p["ink3"]))
            label = f"{distances[index]:.0f}km"
            if index == row_count - 1:
                painter.drawText(QPointF(x - 30, plot.bottom() + 15), label)
            else:
                painter.drawText(QPointF(x - 14, plot.bottom() + 15), label)

        # 地形填充 + 地形线
        terrain_points: list[QPointF] = []
        for index in range(row_count):
            if index < len(terrain) and _finite(terrain[index]):
                terrain_points.append(QPointF(x_of(distances[index]), y_of(float(terrain[index]))))
        if terrain_points:
            fill_path = QPainterPath(terrain_points[0])
            for point in terrain_points[1:]:
                fill_path.lineTo(point)
            fill_path.lineTo(terrain_points[-1].x(), plot.bottom())
            fill_path.lineTo(terrain_points[0].x(), plot.bottom())
            fill_path.closeSubpath()
            terrain_color = QColor("#68736f")
            painter.setPen(Qt.PenStyle.NoPen)
            fill = QColor(terrain_color)
            fill.setAlpha(46 if not theme.is_dark() else 64)
            painter.setBrush(fill)
            painter.drawPath(fill_path)
            painter.setPen(QPen(terrain_color, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(terrain_points))

        # 云层色块（按显示字段）+ 状态描边
        half_width = max(3.0, plot.width() / max(1, row_count) * 0.45)
        for i in range(row_count):
            cx = x_of(distances[i])
            for j in range(level_count):
                height = cell("geopotential_height_asl_m", i, j)
                if not _finite(height):
                    continue
                top = y_of(float(height) + _CELL_HALF_HEIGHT_M)
                bottom = y_of(float(height) - _CELL_HALF_HEIGHT_M)
                cell_rect = QRectF(cx - half_width, top, half_width * 2.0,
                                   max(3.0, bottom - top))
                fill = self._cell_fill(cell, i, j)
                painter.setPen(Qt.PenStyle.NoPen)
                if fill is not None:
                    painter.setBrush(fill)
                    painter.drawRect(cell_rect)
                status = cell("cloud_layer_status", i, j)
                if status == 3:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor("#ef6c35"), 2.2))
                    painter.drawRect(cell_rect)
                elif status == 2:
                    pen = QPen(QColor("#d38b39"), 1.6, Qt.PenStyle.CustomDashLine)
                    pen.setDashPattern([4.0, 3.0])
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(pen)
                    painter.drawRect(cell_rect)

        # 压力层几何高度线（温度分层色）+ 层压标签
        for j in range(level_count):
            points: list[QPointF] = []
            temps: list[float] = []
            for i in range(row_count):
                height = cell("geopotential_height_asl_m", i, j)
                if not _finite(height):
                    continue
                points.append(QPointF(x_of(distances[i]), y_of(float(height))))
                temperature = cell("temperature_c", i, j)
                if _finite(temperature):
                    temps.append(float(temperature))
            if not points:
                continue
            mean_temp = sum(temps) / len(temps) if temps else None
            if mean_temp is None:
                line_color = QColor("#ad624f")
            elif mean_temp < -30:
                line_color = QColor("#8b6574")
            elif mean_temp < 0:
                line_color = QColor("#b06b5c")
            else:
                line_color = QColor("#c64f37")
            line_color.setAlpha(158)
            painter.setPen(QPen(line_color, 1.1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(points))
            if j % 2 == 0:
                painter.setFont(_axis_font(7))
                painter.setPen(QColor(p["ink3"]))
                last = points[-1]
                painter.drawText(QPointF(last.x() - 40, last.y() - 3), f"{pressures[j]}hPa")

        # 云底/云顶包络线（cloud_layer_base/top 按距离取极值）
        envelope_base: list[tuple[float, float]] = []
        envelope_top: list[tuple[float, float]] = []
        for i in range(row_count):
            bases = [float(cell("cloud_layer_base_asl_m", i, j))
                     for j in range(level_count) if _finite(cell("cloud_layer_base_asl_m", i, j))]
            tops = [float(cell("cloud_layer_top_asl_m", i, j))
                    for j in range(level_count) if _finite(cell("cloud_layer_top_asl_m", i, j))]
            if bases:
                envelope_base.append((x_of(distances[i]), y_of(min(bases))))
            if tops:
                envelope_top.append((x_of(distances[i]), y_of(max(tops))))
        accent = QColor(p["accent"])
        for series, alpha in ((envelope_base, 235), (envelope_top, 200)):
            if len(series) >= 2:
                pen = QPen(accent, 1.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                color = pen.color()
                color.setAlpha(alpha)
                pen.setColor(color)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in series]))

        # 太阳受光需求线（云底理论受光阈值，ASL 坐标与 Web 版一致）
        shadow_points: list[QPointF] = []
        for i in range(row_count):
            value = required_asl[i] if i < len(required_asl) else None
            if not _finite(value) and i < len(required_agl):
                agl = required_agl[i]
                terr = terrain[i] if i < len(terrain) else None
                if _finite(agl) and _finite(terr):
                    value = float(terr) + float(agl)
            if _finite(value):
                shadow_points.append(QPointF(x_of(distances[i]), y_of(float(value))))
        if len(shadow_points) >= 2:
            pen = QPen(QColor("#d38b39"), 2.2, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([7.0, 5.0])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(QPolygonF(shadow_points))

        # 观测点标记
        observer_height = (
            float(terrain[0]) if terrain and _finite(terrain[0])
            else _num(observer.get("elevation_asl_m"))
        )
        observer_point = QPointF(plot.left(), y_of(observer_height))
        painter.setBrush(QColor("#f37d42"))
        glass_edge = QColor(p["glass_strong"])
        painter.setPen(QPen(glass_edge, 2))
        painter.drawEllipse(observer_point, 6, 6)
        painter.setFont(_axis_font(8))
        painter.setPen(QColor(p["ink3"]))
        painter.drawText(QPointF(plot.left() + 12, observer_point.y() - 10), "观测点")

        # 受光阈值注记（与 Web 版一致的暖色说明）
        painter.setPen(QColor("#b97836"))
        painter.drawText(QPointF(plot.left() + 155, plot.top() + 16),
                         "黄色虚线：云底理论受光阈值（ASL）")

        # 色标 + 图例
        self._draw_legend(painter, rect, plot)

    def _cell_fill(self, cell: Callable[[str, int, int], Any], i: int, j: int) -> QColor | None:
        if self._field == FIELD_RH:
            rh = cell("rh_percent", i, j)
            if not _finite(rh):
                return None
            norm = _clamp(float(rh) / 100.0, 0.0, 1.0)
            color = _lerp_color(QColor("#a8703a"), QColor("#4a82b8"), norm)
            color.setAlphaF(0.14 + 0.52 * norm)
            return color
        if self._field == FIELD_TEMP:
            temperature = cell("temperature_c", i, j)
            if not _finite(temperature):
                return None
            norm = _clamp((float(temperature) + 50.0) / 90.0, 0.0, 1.0)
            color = _lerp_color(QColor("#3f74b8"), QColor("#c8493a"), norm)
            color.setAlphaF(0.14 + 0.5 * norm)
            return color
        return self._cloud_fill(cell, i, j)

    def _cloud_fill(self, cell: Callable[[str, int, int], Any], i: int, j: int) -> QColor | None:
        """云量色块：与 Web 版 cloudFill 同口径（量值→透明度，压力族→色相）。"""
        amount = cell("cloud_amount", i, j)
        amount_f = float(amount) if _finite(amount) else None
        presence = cell("cloud_presence", i, j)
        fraction = cell("cloud_fraction_percent", i, j)
        rh = cell("rh_percent", i, j)
        family = cell("pressure_family_code", i, j)
        if amount_f is None:
            amount_f = max(
                float(presence) if _finite(presence) else 0.0,
                (float(fraction) if _finite(fraction) else 0.0) / 100.0,
            )
        humidity_amount = max(0.0, (float(rh) if _finite(rh) else 0.0) - 55.0) / 45.0
        if amount_f < 0.18:
            if _finite(fraction) and float(fraction) <= 0.0:
                return None
            if humidity_amount < 0.06:
                return None
            color = QColor(112, 151, 126)
            color.setAlphaF(min(0.34, 0.04 + humidity_amount * 0.26))
            return color
        red, green, blue = _CLOUD_FAMILY_RGB.get(int(family) if family else 0,
                                                 _CLOUD_FAMILY_RGB[3])
        color = QColor(red, green, blue)
        color.setAlphaF(min(0.82, 0.10 + amount_f * 0.68))
        return color

    def _draw_legend(self, painter: QPainter, rect: QRectF, plot: QRectF) -> None:
        p = theme.palette()
        bar = QRectF(plot.left(), rect.bottom() - 26, 110, 9)
        if self._field == FIELD_RH:
            caption = "相对湿度 0–100%（棕=干 · 蓝=湿）"
            self._draw_legend_bar(
                painter, rect, bar, QColor("#a8703a"), QColor("#4a82b8"),
                "0", "100", caption,
            )
        elif self._field == FIELD_TEMP:
            caption = "温度 -50–40 °C（蓝=冷 · 红=暖）"
            self._draw_legend_bar(
                painter, rect, bar, QColor("#3f74b8"), QColor("#c8493a"),
                "-50°", "40°", caption,
            )
        else:
            caption = "云量 0–100%（灰白→蓝灰，色相按压力族）"
            self._draw_legend_bar(
                painter, rect, bar,
                QColor(223, 227, 232, 70), QColor(111, 146, 184, 235),
                "0%", "100%", caption,
            )
        painter.setFont(_axis_font(7))
        painter.setPen(QColor(p["ink3"]))
        painter.drawText(
            QPointF(bar.right() + 16, bar.bottom() - 1),
            caption + "  ·  橙实线=受光且可见候选 · 橙虚线=达阈值不可见 · 橙包络=云底/云顶",
        )


class SatelliteProfileCanvas(_ProfileCanvasBase):
    """卫星剖面：nowcast payload ``nowcast.satellite_vertical_profile``。

    - X = 沿光路样本距离，Y = 高度（样本×压力层矩阵色块）
    - 色块按 hsd_posterior_percent（缺省回退 cloud_probability_percent）
      上色：透明→橙红；缺失层淡琥珀 + 虚线框
    - 叠加高概率层的云底/云顶连线；status 非 retrieved 的样本画灰色斜纹
    """

    #: 视为“有云证据”的后验概率门槛（%），用于云底/云顶包络
    ENVELOPE_MIN_POSTERIOR = 30.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("卫星剖面暂无数据（需实况新鲜帧）", parent)
        self._profile: dict | None = None

    def set_data(self, profile: dict | None) -> None:
        """传入 nowcast dict 的 ``satellite_vertical_profile``；None/unavailable 显示占位。"""
        ok = (
            isinstance(profile, dict)
            and str(profile.get("status") or "") != "unavailable"
            and isinstance(profile.get("samples"), list)
            and bool(profile.get("samples"))
        )
        self._profile = profile if ok else None
        self._has_data = ok
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = QRectF(self.rect())
        if not self._has_data or self._profile is None:
            self._draw_title(painter, rect, "太阳方向实况大气剖面 · 卫星代理", "")
            self._draw_placeholder(painter, self._plot_rect(rect))
            return
        self._paint_profile(painter, rect)

    def _paint_profile(self, painter: QPainter, rect: QRectF) -> None:
        p = theme.palette()
        profile = self._profile
        samples = [s for s in profile.get("samples") or [] if isinstance(s, dict)]
        if not samples:
            self._draw_placeholder(painter, self._plot_rect(rect))
            return
        distances = [_num(s.get("distance_km")) for s in samples]
        max_distance = max([100.0] + [abs(d) for d in distances])

        pressures = [int(v) for v in profile.get("pressure_levels_hpa") or []]
        if not pressures:
            seen: set[int] = set()
            for sample in samples:
                for level in sample.get("levels") or []:
                    if isinstance(level, dict) and level.get("pressure_hpa") is not None:
                        seen.add(int(level["pressure_hpa"]))
            pressures = sorted(seen, reverse=True)
        if not pressures:
            self._draw_placeholder(painter, self._plot_rect(rect))
            return

        # 每样本压力层查找表 + 全场最高度
        level_maps: list[dict[int, dict]] = []
        max_height = 10_000.0
        for sample in samples:
            mapping: dict[int, dict] = {}
            for level in sample.get("levels") or []:
                if not isinstance(level, dict) or level.get("pressure_hpa") is None:
                    continue
                mapping[int(level["pressure_hpa"])] = level
                if _finite(level.get("height_m")):
                    max_height = max(max_height, float(level["height_m"]))
            level_maps.append(mapping)

        # 每压力层代表高度：首个非缺测 height_m，缺省按比例回退（Web 版同口径）
        level_count = len(pressures)
        pressure_heights: list[float] = []
        for j, pressure in enumerate(pressures):
            height = None
            for mapping in level_maps:
                level = mapping.get(pressure)
                if level is not None and _finite(level.get("height_m")):
                    height = float(level["height_m"])
                    break
            if height is None:
                height = max_height * (0.08 + (j / max(1, level_count - 1)) * 0.84)
            pressure_heights.append(height)

        plot = self._plot_rect(rect)

        def x_of(distance: float) -> float:
            return plot.left() + (distance / max_distance) * plot.width()

        def y_of(height_m: int) -> float:
            clamped = _clamp(float(height_m), 0.0, max_height)
            return plot.bottom() - (clamped / max_height) * plot.height()

        def row_bounds(j: int) -> tuple[float, float]:
            current = pressure_heights[j]
            above = 0.0 if j == 0 else (pressure_heights[j - 1] + current) / 2.0
            below = max_height if j == level_count - 1 else (current + pressure_heights[j + 1]) / 2.0
            return y_of(below), max(4.0, y_of(above) - y_of(below))

        def sample_cell(index: int) -> tuple[float, float]:
            center = x_of(distances[index])
            left = plot.left() if index == 0 else (x_of(distances[index - 1]) + center) / 2.0
            right_edge = plot.right() if index + 1 == len(samples) else (center + x_of(distances[index + 1])) / 2.0
            return left, max(8.0, right_edge - left)

        # 标题
        usable = int(_num(profile.get("usable_sample_count")))
        total = int(_num(profile.get("sample_count"), len(samples)))
        coverage = _num(profile.get("coverage"))
        meta = (
            f"{_short_time(profile.get('valid_time_utc'))}Z · "
            f"覆盖率 {coverage * 100:.0f}% · 采样 {usable}/{total} · "
            f"{level_count} 层"
        )
        self._draw_title(painter, rect, "太阳方向实况大气剖面 · 卫星代理", meta)

        background = QColor(p["canvas"])
        glass = QColor(p["glass"])
        background = background.lighter(104) if not theme.is_dark() else _lerp_color(background, glass, 0.35)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRect(plot)

        grid = QColor(p["chart_grid"])
        grid.setAlpha(max(20, p["chart_grid_a"] + 8))
        axis_pen = QPen(grid, 1, Qt.PenStyle.DashLine)
        painter.setFont(_axis_font(8))

        # 高度网格
        for altitude in range(0, 15_001, 3_000):
            if altitude > max_height:
                break
            y = y_of(altitude)
            painter.setPen(axis_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(p["ink3"]))
            painter.drawText(QPointF(6, y + 3), f"{altitude // 1000}km")

        # 压力层行界线 + 层压标签
        pressure_color = QColor("#568f9b")
        for j in range(level_count):
            row_y, row_height = row_bounds(j)
            painter.setPen(axis_pen)
            painter.drawLine(QPointF(plot.left(), row_y), QPointF(plot.right(), row_y))
            painter.setFont(_axis_font(7))
            painter.setPen(pressure_color)
            painter.drawText(QPointF(6, row_y + row_height / 2.0 + 3), f"{pressures[j]}hPa")

        # 样本竖线（虚线）
        sample_line = QColor(p["line_rgb"])
        sample_line.setAlpha(max(24, p["border_a"] + 18))
        sample_pen = QPen(sample_line, 1, Qt.PenStyle.DashLine)
        sample_pen.setDashPattern([3.0, 5.0])
        painter.setPen(sample_pen)
        for distance in distances:
            x = x_of(distance)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # 样本 × 压力层色块
        unknown_pen_color = QColor("#b7791f")
        for index, mapping in enumerate(level_maps):
            cell_x, cell_w = sample_cell(index)
            for j, pressure in enumerate(pressures):
                row_y, row_height = row_bounds(j)
                cell_rect = QRectF(cell_x, row_y + 1.0, cell_w, max(2.0, row_height - 2.0))
                level = mapping.get(pressure)
                posterior = None
                if level is not None and str(level.get("status") or "") != "unknown":
                    posterior = level.get("hsd_posterior_percent")
                    if posterior is None:
                        posterior = level.get("cloud_probability_percent")
                if _finite(posterior):
                    ratio = _clamp(float(posterior), 0.0, 100.0) / 100.0
                    fill = QColor(216, 74, 30)
                    fill.setAlpha(int(14 + 210 * ratio))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(fill)
                    painter.drawRect(cell_rect)
                else:
                    fill = QColor(183, 121, 31, 16)
                    pen = QPen(unknown_pen_color, 1, Qt.PenStyle.CustomDashLine)
                    pen.setDashPattern([3.0, 2.0])
                    pen.setColor(QColor(unknown_pen_color.red(), unknown_pen_color.green(),
                                        unknown_pen_color.blue(), 150))
                    painter.setBrush(fill)
                    painter.setPen(pen)
                    painter.drawRect(cell_rect)

        # 云底/云顶连线（高概率层的高度包络）
        base_points: list[tuple[float, float]] = []
        top_points: list[tuple[float, float]] = []
        for index, mapping in enumerate(level_maps):
            qualifying = [
                float(level["height_m"])
                for pressure, level in mapping.items()
                if _finite(level.get("height_m")) and self._level_posterior(level) >= self.ENVELOPE_MIN_POSTERIOR
            ]
            if qualifying:
                x = x_of(distances[index])
                base_points.append((x, y_of(min(qualifying))))
                top_points.append((x, y_of(max(qualifying))))
        for series, color_hex, alpha in ((top_points, "#f09a5e", 220), (base_points, "#e8703f", 235)):
            if len(series) >= 2:
                pen = QPen(QColor(color_hex), 1.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                color = pen.color()
                color.setAlpha(alpha)
                pen.setColor(color)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in series]))

        # 缺失样本：灰色斜纹占位
        hatch = QColor(p["line_rgb"])
        hatch.setAlpha(96)
        hatch_pen = QPen(hatch, 1)
        for index, sample in enumerate(samples):
            if str(sample.get("status") or "") == "retrieved":
                continue
            cell_x, cell_w = sample_cell(index)
            column = QRectF(cell_x, plot.top(), cell_w, plot.height())
            painter.save()
            clip_path = QPainterPath()
            clip_path.addRect(column)
            painter.setClipPath(clip_path)
            painter.setPen(hatch_pen)
            step = 7.0
            start = column.left() - plot.height()
            end = column.right() + plot.height()
            offset = start
            while offset < end:
                painter.drawLine(
                    QPointF(offset, plot.bottom()),
                    QPointF(offset + plot.height(), plot.top()),
                )
                offset += step
            painter.restore()

        # X 轴距离标签（末样本右对齐，避免溢出画布）
        painter.setFont(_axis_font(8))
        painter.setPen(QColor(p["ink3"]))
        label_stride = 2 if len(samples) > 14 else 1
        for index, distance in enumerate(distances):
            if index % label_stride:
                continue
            label = f"{distance:.0f}km"
            if index == len(distances) - 1:
                painter.drawText(QPointF(x_of(distance) - 28, plot.bottom() + 15), label)
            else:
                painter.drawText(QPointF(x_of(distance) - 12, plot.bottom() + 15), label)

        self._draw_legend(painter, rect, plot)

    @staticmethod
    def _level_posterior(level: dict) -> float:
        posterior = level.get("hsd_posterior_percent")
        if posterior is None:
            posterior = level.get("cloud_probability_percent")
        return _clamp(float(posterior), 0.0, 100.0) if _finite(posterior) else 0.0

    def _draw_legend(self, painter: QPainter, rect: QRectF, plot: QRectF) -> None:
        p = theme.palette()
        bar = QRectF(plot.left(), rect.bottom() - 26, 110, 9)
        transparent = QColor(216, 74, 30, 14)
        opaque = QColor(216, 74, 30, 225)
        caption = "云概率 0–100%（透明→橙红）"
        self._draw_legend_bar(
            painter, rect, bar, transparent, opaque,
            "0%", "100%", caption,
        )
        painter.setFont(_axis_font(7))
        painter.setPen(QColor(p["ink3"]))
        painter.drawText(
            QPointF(bar.right() + 16, bar.bottom() - 1),
            caption + "  ·  虚线框=缺失层 · 灰斜纹=缺失样本 · 橙线=云底/云顶包络",
        )
