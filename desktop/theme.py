"""FireSky 桌面应用主题：暖沙亮色 / 暗色 QSS + 档位配色。

设计稿见 desktop/design-mockup.html（v2）。调色板：
- 亮色：画布 #f2ecdf、卡片 #fbf7f0、边框 #e3d6c2、主色 #d96c2c、
  文字 #2a2118 / 次要文字 #7a6a55。
- 暗色：画布 #16181d、卡片 #1e2128、强调 #e8823c。

档位→颜色映射与 Web/引擎 fire_cloud_quality_level 的十个档位一一对应。
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

# ── 档位配色（与 forecast_characteristics.fire_cloud_quality_level 对齐）──

LEVEL_COLORS: dict[str, str] = {
    "无明显火烧云": "#9e9e9e",
    "微微烧": "#a8a29e",
    "小烧": "#d6b56a",
    "小烧到中等烧": "#e09b3d",
    "中等烧": "#ea7c2c",
    "中等烧到大烧": "#e05a1e",
    "不完美大烧": "#d43d1a",
    "典型大烧": "#c0261b",
    "优质大烧": "#a31a1f",
    "世纪大烧": "#7d0f1e",
}

LEVEL_ORDER: list[str] = list(LEVEL_COLORS.keys())

# 允许 "微微烧" 这类短名命中（设计稿/缓存里的别名兜底）
_LEVEL_ALIAS: dict[str, str] = {
    "微烧": "微微烧",
    "小中": "小烧到中等烧",
    "小中烧": "小烧到中等烧",
    "中大": "中等烧到大烧",
    "中大奖": "中等烧到大烧",
    "不完美": "不完美大烧",
    "典型": "典型大烧",
    "优质": "优质大烧",
    "世纪": "世纪大烧",
    "无明显": "无明显火烧云",
}


def level_color(level: str | None) -> str:
    """档位名 → 主题色；未知档位返回中性灰。"""
    text = str(level or "").strip()
    if text in LEVEL_COLORS:
        return LEVEL_COLORS[text]
    if text in _LEVEL_ALIAS:
        return LEVEL_COLORS[_LEVEL_ALIAS[text]]
    for full, color in LEVEL_COLORS.items():
        if text and (text in full or full in text):
            return color
    return "#8a8074"


# ── 调色板 ──

LIGHT_PALETTE: dict[str, str] = {
    "canvas": "#f2ecdf",
    "canvas_alt": "#ece4d2",
    "card": "#fbf7f0",
    "card_sunken": "#f6efdf",
    "border": "#e3d6c2",
    "border_strong": "#cbb998",
    "accent": "#d96c2c",
    "accent_hover": "#e8823c",
    "accent_pressed": "#b8561f",
    "ink": "#2a2118",
    "ink2": "#7a6a55",
    "ink3": "#9a8d78",
    "sel_bg": "#f3e2c8",
    "ok": "#2e7d5b",
    "warn": "#b07020",
}

DARK_PALETTE: dict[str, str] = {
    "canvas": "#16181d",
    "canvas_alt": "#121419",
    "card": "#1e2128",
    "card_sunken": "#191c22",
    "border": "#33363f",
    "border_strong": "#4a4e59",
    "accent": "#e8823c",
    "accent_hover": "#f2954f",
    "accent_pressed": "#c96a2a",
    "ink": "#e8e2d8",
    "ink2": "#9a917f",
    "ink3": "#6e675b",
    "sel_bg": "#3a2d20",
    "ok": "#5bb894",
    "warn": "#d99a4e",
}

_current_dark = False


def is_dark() -> bool:
    return _current_dark


def palette() -> dict[str, str]:
    return DARK_PALETTE if _current_dark else LIGHT_PALETTE


def _build_qss(p: dict[str, str]) -> str:
    return f"""
* {{ font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; }}
QMainWindow, QDialog {{ background: {p['canvas']}; }}
QWidget {{ color: {p['ink']}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel[role="ink2"] {{ color: {p['ink2']}; font-size: 11px; }}
QLabel[role="h1"] {{ font-size: 17px; font-weight: 700; }}
QLabel[role="h5"] {{
    color: {p['ink2']}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; background: transparent;
}}
QLabel[role="cardTitle"] {{ font-size: 12px; color: {p['ink2']}; font-weight: 600; }}

QTabWidget::pane {{
    border: 1px solid {p['border']}; border-radius: 10px;
    background: {p['canvas']}; top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {p['ink2']}; padding: 7px 20px;
    font-size: 13px; font-weight: 600; border: 1px solid transparent;
    border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px;
    margin-right: 3px;
}}
QTabBar::tab:selected {{
    background: {p['card']}; color: {p['ink']};
    border: 1px solid {p['border']};
}}
QTabBar::tab:hover {{ color: {p['ink']}; }}

QGroupBox {{
    background: {p['card']}; border: 1px solid {p['border']};
    border-radius: 10px; margin-top: 10px; padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; top: 2px;
    color: {p['ink2']}; font-size: 11px;
}}

QLineEdit, QDoubleSpinBox, QSpinBox, QDateEdit, QComboBox {{
    background: {p['card']}; border: 1px solid {p['border']};
    border-radius: 8px; padding: 4px 9px; min-height: 22px;
    selection-background-color: {p['accent']}; selection-color: #ffffff;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus,
QComboBox:focus {{ border-color: {p['accent']}; }}
QLineEdit:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled,
QDateEdit:disabled, QComboBox:disabled {{
    color: {p['ink3']}; background: {p['card_sunken']};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['ink2']}; margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {p['card']}; border: 1px solid {p['border']};
    selection-background-color: {p['sel_bg']}; selection-color: {p['ink']};
}}
QDateEdit::drop-down {{ border: none; width: 20px; }}
QDateEdit::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['ink2']}; margin-right: 6px;
}}

QPushButton {{
    background: {p['card']}; border: 1px solid {p['border']};
    border-radius: 8px; padding: 6px 14px; color: {p['ink']}; font-weight: 600;
}}
QPushButton:hover {{ border-color: {p['border_strong']}; background: {p['card_sunken']}; }}
QPushButton:pressed {{ background: {p['sel_bg']}; }}
QPushButton:disabled {{ color: {p['ink3']}; border-color: {p['border']}; }}
QPushButton#runBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p['accent_hover']}, stop:1 {p['accent']});
    color: #ffffff; border: none; border-radius: 10px;
    font-size: 14px; font-weight: 700; letter-spacing: 2px; padding: 9px 18px;
}}
QPushButton#runBtn:hover {{
    background: {p['accent_hover']};
}}
QPushButton#runBtn:pressed {{ background: {p['accent_pressed']}; }}
QPushButton#runBtn:disabled {{
    background: {p['border']}; color: {p['ink2']};
}}
QPushButton#themeBtn, QPushButton#ghostBtn {{
    background: {p['card_sunken']}; border: 1px solid {p['border']};
    border-radius: 8px; color: {p['ink2']}; font-weight: 600; padding: 5px 12px;
}}

QListWidget {{
    background: {p['card_sunken']}; border: 1px solid {p['border']};
    border-radius: 10px; padding: 4px; outline: none;
}}
QListWidget::item {{
    color: {p['ink2']}; padding: 6px 8px; border-radius: 8px; margin: 1px 0;
}}
QListWidget::item:selected {{
    background: {p['card']}; color: {p['ink']}; font-weight: 600;
    border: 1px solid {p['border']};
}}
QListWidget::item:hover:!selected {{ color: {p['ink']}; }}

QTableWidget, QTableView {{
    background: {p['card']}; alternate-background-color: {p['card_sunken']};
    border: 1px solid {p['border']}; border-radius: 10px; gridline-color: {p['border']};
    selection-background-color: {p['sel_bg']}; selection-color: {p['ink']};
}}
QHeaderView::section {{
    background: {p['card_sunken']}; color: {p['ink2']}; font-size: 11px;
    font-weight: 600; border: none; border-bottom: 1px solid {p['border']};
    padding: 7px 10px;
}}
QTableCornerButton::section {{ background: {p['card_sunken']}; border: none; }}

QProgressBar {{
    background: {p['card_sunken']}; border: 1px solid {p['border']};
    border-radius: 6px; height: 10px; text-align: center; color: transparent;
    min-height: 10px; max-height: 10px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {p['accent_hover']}, stop:1 {p['accent']});
    border-radius: 5px;
}}

QTextEdit {{
    background: {p['card_sunken']}; border: 1px solid {p['border']};
    border-radius: 10px; color: {p['ink2']}; font-size: 11px;
}}
QTextEdit[role="log"] {{ font-family: "SF Mono", Menlo, Consolas, monospace; }}

QFrame[card="true"] {{
    background: {p['card']}; border: 1px solid {p['border']}; border-radius: 12px;
}}
QFrame[cardSunken="true"] {{
    background: {p['card_sunken']}; border: 1px solid {p['border']}; border-radius: 12px;
}}

QCheckBox {{ spacing: 7px; color: {p['ink2']}; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {p['border_strong']};
    border-radius: 4px; background: {p['card']};
}}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
QRadioButton {{ spacing: 7px; color: {p['ink2']}; }}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p['border_strong']}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p['border_strong']}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QStatusBar {{
    background: {p['canvas_alt']}; color: {p['ink2']};
    border-top: 1px solid {p['border']};
}}
QToolTip {{
    background: {p['card']}; color: {p['ink']}; border: 1px solid {p['border_strong']};
    padding: 4px 8px; border-radius: 6px;
}}
QMenu {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 8px; }}
QMenu::item {{ padding: 5px 22px; }}
QMenu::item:selected {{ background: {p['sel_bg']}; }}
"""


def apply_theme(app: QApplication, dark: bool) -> None:
    """应用亮/暗主题并持久化到 QSettings。"""
    global _current_dark
    _current_dark = bool(dark)
    app.setStyleSheet(_build_qss(DARK_PALETTE if _current_dark else LIGHT_PALETTE))
    QSettings("FireSky", "FireSkyDesktop").setValue("theme/dark", _current_dark)


def load_theme_preference() -> bool:
    return bool(QSettings("FireSky", "FireSkyDesktop").value("theme/dark", False, type=bool))
