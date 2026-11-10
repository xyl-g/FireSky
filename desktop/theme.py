"""FireSky 桌面应用主题：中性亮暗表面、暖红操作色与青绿就绪态。

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


# ── 结果页中文映射（与 frontend MetricsGrid.svelte genusLabels/colorLabels/
#    trendLabels/regimeLabels 同表，桌面端不做自造口径）──

GENUS_LABELS: dict[str, str] = {
    "cirrus": "卷云", "cirrocumulus": "卷积云", "cirrostratus": "卷层云",
    "altocumulus": "高积云", "altostratus": "高层云", "nimbostratus": "雨层云",
    "stratocumulus": "层积云", "stratus": "层云", "cumulus": "积云",
    "cumulonimbus": "积雨云",
}

# 烧霞适配度排序取自 SunsetBot 火烧云教程（观测经验）：
# 高层云 > 卷层云 > 卷云 ≈ 高积云 > 层积云（条件性）> 层云（不成霞）。
GENUS_BURN_AFFINITY: dict[str, str] = {
    "altostratus": "最佳烧霞云属（橘黄到红）",
    "cirrostratus": "次佳（面积大、持续久）",
    "cirrus": "常见且持久（金黄到红）",
    "altocumulus": "壮观（橘黄到橘红）",
    "cirrocumulus": "少见，偶伴冰晕",
    "nimbostratus": "降水云 · 遮光风险高",
    "stratocumulus": "条件性（云底高 + 大气极洁才有短时橘红）",
    "cumulus": "对流云晚霞形态",
    "cumulonimbus": "对流晚霞 / 云砧晚霞",
    "stratus": "不成烧霞（可作云海陪衬）",
}

COLOR_DIAGNOSIS_LABELS: dict[str, str] = {
    "golden": "金黄",
    "orange_red": "橙红",
    "deep_red": "深红",
    "muted_glow": "闷烧",
}

TREND_LABELS: dict[str, str] = {
    "forming": "正在生成",
    "strengthening": "正在增强",
    "steady": "基本稳定",
    "weakening": "正在减弱",
    "dissipating": "正在消散",
    "unknown": "演变未知",
}

REGIME_LABELS: dict[str, str] = {
    "local_deck": "本地云幕",
    "distant_target": "远方目标云",
    "remote_boundary": "远方云边界",
    "cloud_hole": "云洞透光",
    "convective": "对流厚云",
    "horizon_blocked": "日出方向云幕遮挡",
    "solar_incoming_blocked_field": "太阳入射受阻云场",
    "clear": "晴空 / 无有效目标",
    "invalid": "无有效场景",
    "unknown": "未分类",
}

PRESSURE_FAMILY_LABELS: dict[str, str] = {
    "high": "高云压力族",
    "middle": "中云压力族",
    "low": "低云压力族",
}

CLOUD_PHASE_LABELS: dict[str, str] = {
    "ice": "冰相成分",
    "mixed": "冰水混合成分",
    "water": "液态水成分",
    "unknown": "相态未知",
}

BOUNDARY_STABILITY_LABELS: dict[bool, str] = {
    True: "可烧判断稳定",
    False: "可烧判断敏感",
}


def label_for(mapping: dict, key, fallback: str = "—") -> str:
    """通用中文映射：键缺失 / 值为空时回退，None 一律显示 fallback。"""
    if key is None:
        return fallback
    text = str(key)
    return mapping.get(text) or (text or fallback)

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
    "canvas": "#e2ecef",
    "canvas_alt": "#f3e9e5",
    "glass": "#ffffff",
    "glass_strong": "#ffffff",
    "sidebar": "#ffffff",
    "well": "#f3f7f8",
    "card": "#ffffff",
    "card_sunken": "#f5f7f8",
    "card_strong": "#ffffff",
    "border": "#dce1e5",
    "border_strong": "#bac3cb",
    "line_rgb": "#33414e",
    "ink": "#24343a",
    "ink2": "#4c5965",
    "ink3": "#62717e",
    "faint": "#66737e",
    "accent": "#bd513d",
    "accent_hover": "#ce624b",
    "accent_pressed": "#a54132",
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
    "glass_a": 154, "glass_strong_a": 192, "sidebar_a": 100,
    "well_a": 130, "border_a": 24, "border_strong_a": 47,
    "highlight_a": 235, "chart_grid_a": 23, "scrim_a": 88,
    "glow_a": 86, "glow_alt_a": 48,
}

DARK_PALETTE: dict = {
    "canvas": "#16272a",
    "canvas_alt": "#2e252b",
    "glass": "#293638",
    "glass_strong": "#344144",
    "sidebar": "#202326",
    "well": "#1b1e20",
    "card": "#222629",
    "card_sunken": "#1b1e20",
    "card_strong": "#292e32",
    "border": "#2b2d36",
    "border_strong": "#3c3f4b",
    "line_rgb": "#ffffff",
    "ink": "#edf5f4",
    "ink2": "#d0cbd0",
    "ink3": "#9996a2",
    "faint": "#a5aab0",
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
    "glass_a": 150, "glass_strong_a": 185, "sidebar_a": 108,
    "well_a": 105, "border_a": 26, "border_strong_a": 48,
    "highlight_a": 58, "chart_grid_a": 17, "scrim_a": 148,
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
* {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; }}
QMainWindow, QDialog {{ background: {p['canvas']}; }}
QWidget {{ color: {p['ink']}; font-size: 12px; }}
QWidget#appShell {{ background: transparent; }}
QWidget#sidebar {{
    background: {_rgba(p['sidebar'], p['sidebar_a'])};
    border-right: 1px solid {_rgba(p['highlight'], p['highlight_a'] // 2)};
}}
QLabel {{ background: transparent; }}
QLabel[role="ink2"] {{ color: {p['ink2']}; font-size: 11px; }}
QLabel[role="ink3"] {{ color: {p['ink3']}; font-size: 10px; }}
QLabel[role="hint"] {{ color: {p['faint']}; font-size: 9px; }}
QLabel[role="eyebrow"] {{ color: {p['accent']}; font-size: 9px; font-weight: 700; letter-spacing: 0; }}
QLabel[role="h1"] {{ font-size: 22px; font-weight: 700; letter-spacing: 0; }}
QLabel[role="h2"] {{ font-size: 13px; font-weight: 680; }}
QLabel[role="subtitle"] {{ color: {p['ink3']}; font-size: 11px; }}
QLabel[role="cardTitle"] {{ font-size: 12px; color: {p['ink2']}; font-weight: 660; }}
QLabel[role="metric"] {{ font-size: 21px; font-weight: 700; }}
QLabel[role="heroScore"] {{ font-size: 38px; font-weight: 730; letter-spacing: 0; }}
QLabel[role="heroTime"] {{ font-size: 28px; font-weight: 680; letter-spacing: 0; }}
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
QLabel[role="chip"][tone="warn"] {{
    background: {p['warn_soft']}; border: 1px solid {_rgba(p['warn'], 75)};
    color: {p['warn']}; font-weight: 600;
}}

QFrame[glass="true"] {{ background: transparent; border: none; }}
QFrame[plain="true"] {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 8px;
}}
QFrame[card="true"] {{
    background: {g(150)}; border: 1px solid {line(p['border_a'])}; border-radius: 8px;
}}
QFrame[cardSunken="true"] {{
    background: {_rgba(p['well'], p['well_a'])};
    border: 1px solid {line(p['border_a'])}; border-radius: 8px;
}}

QPushButton {{
    background: {g(150)}; border: 1px solid {line(p['border_a'])};
    border-radius: 8px; padding: 6px 12px; color: {p['ink']}; font-weight: 600;
}}
QPushButton:hover {{ border-color: {line(p['border_strong_a'])}; background: {gs(210)}; }}
QPushButton:pressed {{ background: {_rgba(p['accent'], 30)}; }}
QPushButton:disabled {{ color: {p['faint']}; border-color: {line(p['border_a'])}; }}
QPushButton:focus {{ border: 1px solid {p['accent']}; }}

QPushButton#runBtn {{
    background: {p['accent']};
    color: #ffffff; border: none; border-radius: 8px;
    font-size: 12px; font-weight: 700; letter-spacing: 0; padding: 10px 18px;
}}
QPushButton#runBtn:hover {{ background: #f0954a; }}
QPushButton#runBtn:pressed {{ background: #cd4b3b; }}
QPushButton#runBtn:disabled {{ background: {_rgba(p['line_rgb'], 30)}; color: {p['ink3']}; }}

QPushButton#navBtn {{
    background: transparent; border: 1px solid transparent; border-radius: 8px;
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
QComboBox QAbstractItemView {{
    background: {p['card_strong']}; border: 1px solid {line(p['border_strong_a'])};
    selection-background-color: {p['sel_bg']}; selection-color: {p['ink']};
}}
QDateEdit::drop-down {{ border: none; width: 19px; }}
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

QTableWidget, QTableView, QTreeWidget {{
    background: {g(120)}; alternate-background-color: {_rgba(p['well'], 70)};
    border: 1px solid {_rgba(p['highlight'], p['highlight_a'])}; border-radius: 8px;
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
QDialog#forecastDataDialog {{ background: {p['card']}; }}
QLabel#dataReadiness {{ font-size: 14px; font-weight: 600; color: {p['ink']}; }}
QLabel#dataSourceName {{ font-size: 14px; font-weight: 600; color: {p['ink']}; }}
QLabel#dataSourceSymbol {{ background: {p['well']}; border-radius: 6px; }}
QLabel#dataSourceStatus {{ font-size: 11px; font-weight: 550; color: {p['ink3']}; }}
QLabel#dataSourceStatus[state="missing"], QLabel#dataSourceStatus[state="old"] {{ color: {p['accent']}; }}
QLabel#dataSourceStatus[state="ready"] {{ color: {p['ok']}; }}
QFrame#dataSourceSeparator {{ background: {line(p['border_a'])}; border: none; }}
QPushButton#dataRefreshButton {{ border: none; background: transparent; padding: 0; border-radius: 5px; }}
QPushButton#dataRefreshButton:hover {{ background: {p['well']}; }}

QProgressBar {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 6px; height: 8px; text-align: center; color: transparent;
    min-height: 8px; max-height: 8px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #f3ad4e, stop:1 #df5242); border-radius: 4px;
}}
QProgressBar#dataPrepareProgress {{
    background: {p['well']}; border: none; border-radius: 1px;
    min-height: 3px; max-height: 3px;
}}
QProgressBar#dataPrepareProgress::chunk {{ background: {p['accent']}; border-radius: 1px; }}

QTextEdit {{
    background: {_rgba(p['well'], p['well_a'])}; border: 1px solid {line(p['border_a'])};
    border-radius: 8px; color: {p['ink3']}; font-size: 10px; padding: 6px;
}}
QTextEdit[role="log"] {{ font-family: "SF Mono", Menlo, Consolas, monospace; }}

QCheckBox, QRadioButton {{ spacing: 7px; color: {p['ink2']}; font-size: 11px; }}
QCheckBox::indicator:unchecked {{
    width: 15px; height: 15px; border: 1px solid {line(90)};
    border-radius: 4px; background: {g(100)};
}}
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
QTabBar::tab {{
    background: transparent; color: {p['ink3']}; padding: 9px 14px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {p['accent']}; border-bottom-color: {p['accent']}; }}
QTabBar::tab:focus {{ background: {p['sel_bg']}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def apply_theme(app: QApplication, dark: bool) -> None:
    global _current_dark
    _current_dark = bool(dark)
    app.setStyleSheet(_build_qss(palette()))
    from desktop.icons import set_icon
    for widget in app.allWidgets():
        if widget.property("glyph"):
            set_icon(widget, widget.property("glyph"))
    QSettings("FireSky", "FireSkyDesktop").setValue("theme/dark", _current_dark)


def load_theme_preference() -> bool:
    return bool(QSettings("FireSky", "FireSkyDesktop").value("theme/dark", False, type=bool))


def repolish(widget) -> None:
    """动态属性（active 等）变化后刷新 QSS。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
