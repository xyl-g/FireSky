"""FireSky 桌面应用包（PySide6 前端；与 firesky 引擎层解耦）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_qtwebengine() -> None:
    """QtWebEngine 的启动前置条件，必须在导入 QtWebEngine 之前完成。

    1. **macOS 沙箱**：从 Python 进程启动时 Chromium 沙箱会让渲染进程初始化
       失败（页面全白、脚本不执行），需显式关闭；Windows / Linux 保持默认。
    2. **打包路径**：PyInstaller 会把 ``QtWebEngineCore.framework`` 里原本平铺
       的 ``Helpers`` / ``Resources`` 两个真实目录挪进 ``Versions/Resources``，
       顶层符号链接因此断掉。Qt 于是报「could not find QtWebEngineProcess」，
       地图整块白屏。这里按实际存在的位置补齐三个环境变量。

    放在包初始化里而不是 ``web_map`` 里，是为了保证任何入口（应用主程序、
    单独脚本、测试）都不会在修复之前先碰 QtWebEngine。
    """
    if sys.platform == "darwin":
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return

    bundle = Path(sys.executable).resolve().parent.parent.parent
    framework = (bundle / "Contents" / "Frameworks" / "PySide6" / "Qt"
                 / "lib" / "QtWebEngineCore.framework")
    if not framework.is_dir():
        return

    # 标准 framework 的两者都在顶层；打包产物的实际位置在 Versions/Resources 下
    layouts = ("", "Versions/A/", "Versions/Current/", "Versions/Resources/")

    if not os.environ.get("QTWEBENGINE_RESOURCES_PATH"):
        for prefix in layouts + ("Versions/Resources/Resources/",):
            root = framework / prefix
            if (root / "qtwebengine_resources.pak").is_file():
                os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(root)
                locales = root / "qtwebengine_locales"
                if locales.is_dir():
                    os.environ.setdefault("QTWEBENGINE_LOCALES_PATH", str(locales))
                break

    if not os.environ.get("QTWEBENGINEPROCESS_PATH"):
        candidates = []
        for prefix in layouts:
            base = framework / prefix
            candidates.append(base / "Helpers" / "QtWebEngineProcess.app"
                              / "Contents" / "MacOS" / "QtWebEngineProcess")
            candidates.append(base / "QtWebEngineProcess.app"
                              / "Contents" / "MacOS" / "QtWebEngineProcess")
        candidates.append(bundle / "Contents" / "MacOS" / "QtWebEngineProcess")
        for candidate in candidates:
            if candidate.is_file():
                os.environ["QTWEBENGINEPROCESS_PATH"] = str(candidate)
                break


_prepare_qtwebengine()
