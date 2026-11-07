"""FireSky 桌面应用主题 · 暮光玻璃 v4。

对应设计稿 desktop/design-mockup.html（v4）。两套调色板：
- 亮色：暖白 #f2f0eb 画布 + 半透明白玻璃 + 朝霞橙强调
- 暗色：深暮蓝 #111218 画布 + 半透明深灰玻璃 + 暖橙强调

Qt 没有 backdrop-filter，玻璃质感由三层模拟：半透明表面（QSS rgba）、
1px 顶部高光（GlassCard.paintEvent）、背景霞光晕染（AppShell.paintEvent）。

档位→颜色映射与 Web/引擎 fire_cloud_quality_level 的十个档位一致。
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
    text = str(level or "").strip()
    if text in LEVEL_COLORS:
        return LEVEL_COLORS[text]
    if text in _LEVEL_ALIAS:
        return LEVEL_COLORS[_LEVEL_ALIAS[text]]
    for full, color in LEVEL_COLORS.items():
        if text and (text in full or full in text):
            return color
    return "#8a8074"


# ── 调色板 ──────────────────────────────────────────────────────────────
# 所有颜色都是 QColor 可解析的 #rrggbb（自绘控件直接 QColor(p[...])）；
# *_a 为透明度 0–255，由 _rgba() 合成 QSS 的 rgba() 字符串。

LIGHT_PALETTE: dict = {
    "canvas": "#f2f0eb",
    "canvas_alt": "#e9e5de",
    "glass": "#ffffff",
    "glass_strong": "#ffffff",
    "sidebar": "#ffffff",
    "well": "#f4f1eb",
    "card": "#ffffff",
    "card_sunken": "#f4f1eb",
    "card_strong": "#ffffff",
    "border": "#e5ded3",
    "border_strong": "#cfc5b6",
    "line_rgb": "#4c3f31",
    "ink": "#28262c",
    "ink2": "#535059",
    "ink3": "#858089",
    "faint": "#aaa4aa",
    "accent": "#df603b",
    "accent_hover": "#f38a50",
    "accent_pressed": "#c9502f",
    "accent_soft": "#fbeee8",
    "accent_line": "#e9b9a6",
    "sel_bg": "#f7e7dd",
    "gold": "#e9a23b",
    "ok": "#31846a",
    "ok_soft": "#e6f2ee",
    "warn": "#b07020",
    "warn_soft": "#fbf1df",
    "red": "#c84c3c",
    "highlight": "#ffffff",
    "chart_grid": "#5d5246",
    "map_land": "#dcdcc7",
    "map_water": "#cbdedd",
    "glow": "#ffb780",
    "glow_alt": "#ac9dd4",
    # alpha
    "glass_a": 163, "glass_strong_a": 226, "sidebar_a": 122,
    "well_a": 210, "border_a": 27, "border_strong_a": 43,
    "highlight_a": 214, "chart_grid_a": 23, "scrim_a": 88,
    "glow_a": 86, "glow_alt_a": 48,
}

DARK_PALETTE: dict = {
    "canvas": "#111218",
    "canvas_alt": "#0b0c11",
    "glass": "#1d1f28",
    "glass_strong": "#21232d",
    "sidebar": "#181a22",
    "well": "#0d0f15",
    "card": "#1d1f28",
    "card_sunken": "#0d0f15",
    "card_strong": "#21232d",
    "border": "#2b2d36",
    "border_strong": "#3c3f4b",
    "line_rgb": "#ffffff",
    "ink": "#f3efe8",
    "ink2": "#d0cbd0",
    "ink3": "#9996a2",
    "faint": "#6d6b77",
    "accent": "#ff8c62",
    "accent_hover": "#ffab68",
    "accent_pressed": "#e87248",
    "accent_soft": "#342621",
    "accent_line": "#6a3d2e",
    "sel_bg": "#3a2c26",
    "gold": "#f1bc5c",
    "ok": "#65c4a2",
    "ok_soft": "#1c332c",
    "warn": "#d99a4e",
    "warn_soft": "#352a1c",
    "red": "#ff806d",
    "highlight": "#ffffff",
    "chart_grid": "#ffffff",
    "map_land": "#3d4645",
    "map_water": "#283a40",
    "glow": "#98422f",
    "glow_alt": "#423e74",
    # alpha
    "glass_a": 163, "glass_strong_a": 224, "sidebar_a": 158,
    "well_a": 122, "border_a": 19, "border_strong_a": 33,
    "highlight_a": 28, "chart_grid_a": 17, "scrim_a": 148,
    "glow_a": 64, "glow_alt_a": 50,
}

_current_dark = False


def is_dark() -> bool:
    return _current_dark


def palette() -> dict:
    return DARK_PALETTE if _current_dark else LIGHT_PALETTE


def _rgba(hex_color: str, alpha: int) -> str:
    text = hex_color.lstrip("#")
    r, g, b = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def rgba(key: str, alpha_key: str | None = None, alpha: int | None = None) -> str:
    """主题色 → QSS rgba 字符串。"""
    p = palette()
    a = alpha if alpha is not None else p.get(alpha_key or "", 255)
    return _rgba(p[key], int(a))


def _build_qss(p: dict) -> str:
    g = lambda a: _rgba(p["glass"], a)  # noqa: E731
    gs = lambda a: _rgba(p["glass_strong"], a)  # noqa: E731
    line = lambda a: _rgba(p["line_rgb"], a)  # noqa: E731

    return f"""
* {{ font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif; }}
QMainWindow, QDialog {{ background: {p['canvas']}; }}
QWidget {{ color: {p['ink']}; font-size: 12px; }}
QWidget#appShell {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel[role="ink2"] {{ color: {p['ink2']}; font-size: 11px; }}
QLabel[role="ink3"] {{ color: {p['ink3']}; font-size: 10px; }}
QLabel[role="hint"] {{ color: {p['faint']}; font-size: 9px; }}
QLabel[role="eyebrow"] {{ color: {p['accent']}; font-size: 9px; font-weight: 700; letter-spacing: 2px; }}
QLabel[role="h1"] {{ font-size: 25px; font-weight: 700; letter-spacing: -1px; }}
QLabel[role="h2"] {{ font-size: 13px; font-weight: 680; }}
QLabel[role="subtitle"] {{ color: {p['ink3']}; font-size: 11px; }}
QLabel[role="cardTitle"] {{ font-size: 12px; color: {p['ink2']}; font-weight: 660; }}
QLabel[role="metric"] {{ font-size: 21px; font-weight: 700; }}
QLabel[role="heroScore"] {{ font-size: 52px; font-weight: 730; letter-spacing: -3px; }}
QLabel[role="heroTime"] {{ font-size: 34px; font-weight: 680; letter-spacing: -1px; }}
QLabel[role="chip"] {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 8px; color: {p['ink3']}; font-size: 9px; padding: 4px 8px;
}}
QLabel[role="chip"][tone="accent"] {{
    background: {_rgba(p['accent'], 28)}; border: 1px solid {_rgba(p['accent'], 60)};
    color: {p['accent']}; font-weight: 650;
}}
QLabel[role="chip"][tone="ok"] {{
    background: {_rgba(p['ok'], 26)}; border: 1px solid {_rgba(p['ok'], 60)};
    color: {p['ok']}; font-weight: 600;
}}

QFrame[glass="true"] {{ background: transparent; border: none; }}
QFrame[plain="true"] {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 12px;
}}
QFrame[card="true"] {{
    background: {g(150)}; border: 1px solid {line(p['border_a'])}; border-radius: 14px;
}}
QFrame[cardSunken="true"] {{
    background: {_rgba(p['well'], p['well_a'])};
    border: 1px solid {line(p['border_a'])}; border-radius: 14px;
}}

QPushButton {{
    background: {g(150)}; border: 1px solid {line(p['border_a'])};
    border-radius: 10px; padding: 6px 12px; color: {p['ink']}; font-weight: 600;
}}
QPushButton:hover {{ border-color: {line(p['border_strong_a'])}; background: {gs(210)}; }}
QPushButton:pressed {{ background: {_rgba(p['accent'], 30)}; }}
QPushButton:disabled {{ color: {p['faint']}; border-color: {line(p['border_a'])}; }}
QPushButton:focus {{ outline: none; }}

QPushButton#runBtn {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #f3ad4e, stop:0.52 #ed7745, stop:1 #df5242);
    color: #ffffff; border: none; border-radius: 12px;
    font-size: 12px; font-weight: 700; letter-spacing: 1px; padding: 10px 18px;
}}
QPushButton#runBtn:hover {{ background: #f0954a; }}
QPushButton#runBtn:pressed {{ background: #cd4b3b; }}
QPushButton#runBtn:disabled {{ background: {_rgba(p['line_rgb'], 30)}; color: {p['ink3']}; }}

QPushButton#navBtn {{
    background: transparent; border: 1px solid transparent; border-radius: 12px;
    color: {p['ink3']}; font-size: 12px; font-weight: 520;
    text-align: left; padding: 0 12px; min-height: 42px;
}}
QPushButton#navBtn:hover {{ color: {p['ink']}; background: {g(120)}; }}
QPushButton#navBtn[active="true"] {{
    color: {p['accent']}; background: {_rgba(p['accent'], 26)};
    border: 1px solid {_rgba(p['accent'], 56)}; font-weight: 680;
}}

QPushButton#segmentBtn {{
    background: transparent; border: none; border-radius: 8px;
    color: {p['ink3']}; font-size: 11px; font-weight: 520; padding: 5px 10px;
}}
QPushButton#segmentBtn:hover {{ color: {p['ink']}; }}
QPushButton#segmentBtn[active="true"] {{
    background: {gs(230)}; color: {p['ink']}; font-weight: 680;
}}

QPushButton#chipBtn {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 9px; color: {p['ink3']}; font-size: 10px; font-weight: 520; padding: 6px 11px;
}}
QPushButton#chipBtn:hover {{ color: {p['ink']}; }}
QPushButton#chipBtn[active="true"] {{
    background: {_rgba(p['accent'], 28)}; border: 1px solid {_rgba(p['accent'], 56)};
    color: {p['accent']}; font-weight: 650;
}}

QPushButton#pickBtn {{
    background: {_rgba(p['accent'], 28)}; border: 1px solid {_rgba(p['accent'], 56)};
    border-radius: 10px; color: {p['accent']}; font-size: 10px; font-weight: 680; padding: 7px 12px;
}}
QPushButton#pickBtn:hover {{ background: {_rgba(p['accent'], 46)}; }}
QPushButton#ghostBtn, QPushButton#themeBtn {{
    background: transparent; border: 1px solid transparent; border-radius: 9px;
    color: {p['ink3']}; font-size: 10px; font-weight: 600; padding: 5px 10px;
}}
QPushButton#ghostBtn:hover, QPushButton#themeBtn:hover {{ color: {p['ink']}; }}
QPushButton#themeBtn[active="true"] {{
    background: {gs(230)}; color: {p['ink']}; border: 1px solid {line(p['border_a'])};
}}
QPushButton#iconBtn {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 9px; color: {p['ink3']}; font-size: 14px; padding: 0;
}}

QLineEdit, QDoubleSpinBox, QSpinBox, QDateEdit, QComboBox {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 10px; padding: 5px 9px; min-height: 24px; color: {p['ink']};
    selection-background-color: {p['accent']}; selection-color: #ffffff;
}}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus,
QComboBox:focus {{ border-color: {_rgba(p['accent'], 110)}; }}
QLineEdit:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled,
QDateEdit:disabled, QComboBox:disabled {{ color: {p['faint']}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['ink3']}; margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background: {p['card_strong']}; border: 1px solid {line(p['border_strong_a'])};
    selection-background-color: {p['sel_bg']}; selection-color: {p['ink']};
}}
QDateEdit::drop-down {{ border: none; width: 19px; }}
QDateEdit::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['ink3']}; margin-right: 6px;
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QSpinBox::up-button, QSpinBox::down-button {{ width: 14px; border: none; background: transparent; }}

QListWidget {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 12px; padding: 5px; outline: none;
}}
QListWidget::item {{
    color: {p['ink2']}; padding: 8px 9px; border-radius: 9px; margin: 2px 0; font-size: 11px;
}}
QListWidget::item:selected {{
    background: {_rgba(p['accent'], 30)}; color: {p['ink']}; font-weight: 650;
}}
QListWidget::item:hover:!selected {{ color: {p['ink']}; background: {g(120)}; }}

QTableWidget, QTableView {{
    background: {g(150)}; alternate-background-color: {_rgba(p['well'], 90)};
    border: 1px solid {line(p['border_a'])}; border-radius: 12px;
    gridline-color: {line(p['border_a'])}; color: {p['ink2']};
    selection-background-color: {p['sel_bg']}; selection-color: {p['ink']};
    outline: none;
}}
QHeaderView::section {{
    background: {_rgba(p['well'], p['well_a'])}; color: {p['ink3']}; font-size: 10px;
    font-weight: 600; border: none; border-bottom: 1px solid {line(p['border_a'])};
    padding: 8px 12px;
}}
QTableCornerButton::section {{ background: {_rgba(p['well'], p['well_a'])}; border: none; }}
QTableWidget::item {{ padding: 3px 6px; }}

QProgressBar {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 6px; height: 8px; text-align: center; color: transparent;
    min-height: 8px; max-height: 8px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #f3ad4e, stop:1 #df5242); border-radius: 4px;
}}

QTextEdit {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 12px; color: {p['ink3']}; font-size: 10px; padding: 6px;
}}
QTextEdit[role="log"] {{ font-family: "SF Mono", Menlo, Consolas, monospace; }}

QCheckBox, QRadioButton {{ spacing: 7px; color: {p['ink2']}; font-size: 11px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {line(p['border_strong_a'])};
    border-radius: 4px; background: {g(150)};
}}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}

QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {line(p['border_strong_a'])}; border-radius: 5px; min-height: 32px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {line(p['border_strong_a'])}; border-radius: 5px; min-width: 32px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QStatusBar {{
    background: transparent; color: {p['faint']}; border: none; font-size: 9px;
}}
QToolTip {{
    background: {p['card_strong']}; color: {p['ink']};
    border: 1px solid {line(p['border_strong_a'])}; padding: 5px 9px; border-radius: 8px;
}}
QMenu {{ background: {p['card_strong']}; border: 1px solid {line(p['border_strong_a'])}; border-radius: 10px; }}
QMenu::item {{ padding: 5px 20px; }}
QMenu::item:selected {{ background: {p['sel_bg']}; }}
QSplitter::handle {{ background: transparent; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{ background: transparent; }}
"""


def apply_theme(app: QApplication, dark: bool) -> None:
    global _current_dark
    _current_dark = bool(dark)
    app.setStyleSheet(_build_qss(palette()))
    QSettings("FireSky", "FireSkyDesktop").setValue("theme/dark", _current_dark)


def load_theme_preference() -> bool:
    return bool(QSettings("FireSky", "FireSkyDesktop").value("theme/dark", False, type=bool))


def repolish(widget) -> None:
    """动态属性（active 等）变化后刷新 QSS。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
