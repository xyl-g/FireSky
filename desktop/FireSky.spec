# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the FireSky desktop app.

Usage (from repo root, inside the project venv):
    pyinstaller desktop/FireSky.spec

Builds a one-folder .app bundle on macOS / an exe folder on Windows.
Folder mode (not one-file) keeps startup fast — the native scorer dylib,
eccodes data and PySide6 resources stay as loose files beside the launcher.

The heavy optional extras (satpy/pyresample for the satellite nowcast and
cdsapi/ecmwf-opendata for CAMS/EC downloads) are intentionally excluded:
the desktop app ships the model-forecast core (GFS + CAMS cache reading +
Rust scorer). See README "Desktop app" for the exact dependency set.
"""

import sys
from pathlib import Path

REPO = Path(SPECPATH).resolve()
if REPO.name == "desktop":
    REPO = REPO.parent
SPEC_PYTHON = sys.prefix

hiddenimports = [
    "firesky",
    "firesky.web_server",
    "firesky.national_batch",
    "firesky.ecmwf_ifs",
    "firesky.ecmwf_point",
    "firesky.aerosol",
    "firesky.gfs",
    "firesky.satellite",
    "firesky.native_core",
    "firesky.config",
    "jinja2",           # fastapi template dependencies pulled lazily
    "anyio._backends._asyncio",
    # 地图选点控件（QWebEngine + 本地 Leaflet）
    "desktop.web_map",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtPositioning",
]

# 地图页面与本地 Leaflet 资源：QWebEngine 用 file:// 加载，必须随包分发到
# desktop/assets，与 web_map.ASSETS_DIR 的相对路径保持一致。
map_assets = REPO / "desktop" / "assets"
extra_datas = []
if map_assets.is_dir():
    extra_datas.append((str(map_assets), "desktop/assets"))

# Rust scorer: publish the CPU variant next to the app so
# FIRESKY_NATIVE_LIBRARY_PATH can point at it deterministically.
native_dylib = REPO / "native" / "target" / "release"
native_binaries = []
for name in (
    "libfiresky_native.dylib",
    "libfiresky_native_cpu.dylib",
    "libfiresky_native.so",
    "libfiresky_native_cpu.so",
    "firesky_native.dll",
    "firesky_native_cpu.dll",
):
    candidate = native_dylib / name
    if candidate.is_file():
        native_binaries.append((str(candidate), "native"))

a = Analysis(
    [str(REPO / "desktop" / "app.py")],
    pathex=[str(REPO / "src"), str(REPO / "desktop")],
    binaries=native_binaries,
    datas=extra_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # nowcast / heavy optional stacks — desktop ships the model core
        "satpy",
        "pyresample",
        "matplotlib",
        "tkinter",
        "IPython",
        "pytest",
    ],
    noarchive=False,
)

# Some PySide wheels contain an extra Versions/Resources directory. The Qt
# hook mistakes it for a framework version; keep resources beside the A binary.
if sys.platform == "darwin":
    misplaced = "PySide6/Qt/lib/QtWebEngineCore.framework/Versions/Resources/"
    canonical = "PySide6/Qt/lib/QtWebEngineCore.framework/Versions/A/"
    for toc in (a.binaries, a.datas):
        toc[:] = [
            (canonical + dest[len(misplaced):] if dest.startswith(misplaced) else dest,
             source, kind)
            for dest, source, kind in toc
        ]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FireSky",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no terminal window
    icon="desktop/icon.ico" if sys.platform == "win32" else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FireSky",
)

# macOS 需要 .app 包才能双击启动（有 Dock 图标、不弹终端）。
# QtWebEngine 的 QtWebEngineProcess.app / Resources 由 PyInstaller 的
# Qt hook 自动收进 Frameworks，BUNDLE 只负责外层包结构。
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="FireSky.app",
        icon=str(map_assets / "firesky.icns"),
        bundle_identifier="cn.firesky.desktop",
        info_plist={
            "CFBundleName": "FireSky",
            "CFBundleDisplayName": "FireSky",
            "CFBundleShortVersionString": "0.1.1",
            "CFBundleVersion": "0.1.1",
            "NSHighResolutionCapable": True,
            "NSLocationWhenInUseUsageDescription": "仅在您点击获取当前位置时定位，用于选择火烧云预测与实况观测地点。",
            "NSLocationUsageDescription": "根据您主动请求的位置选择预测地点。",
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.weather",
            "NSHumanReadableCopyright": "Open source · non-commercial",
        },
    )
