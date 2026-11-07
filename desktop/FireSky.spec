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
]

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
    datas=[],
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
