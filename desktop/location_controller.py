"""Explicit, one-shot OS positioning. Never substitute an IP-derived location."""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QCoreApplication, QDateTime, QLocationPermission, Qt, Signal


class CurrentLocationController(QObject):
    busyChanged = Signal(bool)
    located = Signal(float, float, float)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._busy = False

    def request(self):
        if self._busy:
            return
        # A command-line Python executable has no usage-description bundle.
        # Asking CoreLocation from it can be denied or terminate on macOS.
        if sys.platform == "darwin" and ".app/" not in str(Path(sys.executable).resolve()):
            self.failed.emit(
                "开发模式的 Python 没有 macOS 定位权限声明。请从 FireSky.app 启动定位，"
                "或使用地图选点/地点搜索；不会用 IP 定位替代。"
            )
            return
        self._busy = True
        self.busyChanged.emit(True)
        permission = QLocationPermission()
        permission.setAccuracy(QLocationPermission.Accuracy.Precise)
        permission.setAvailability(QLocationPermission.Availability.WhenInUse)
        app = QCoreApplication.instance()
        status = app.checkPermission(permission)
        if status == Qt.PermissionStatus.Undetermined:
            app.requestPermission(permission, self, self._permission)
        elif status == Qt.PermissionStatus.Granted:
            self._start()
        else:
            self._error("定位权限被拒绝。请在系统隐私设置中允许 FireSky 定位，或手动选点。")

    def _permission(self, permission):
        if permission.status() == Qt.PermissionStatus.Granted:
            self._start()
        else:
            self._error("未获得定位权限；原观测点保持不变，可继续使用地图和搜索。")

    def _start(self):
        try:
            from PySide6.QtPositioning import QGeoPositionInfoSource
            if self._source is None:
                self._source = QGeoPositionInfoSource.createDefaultSource(self)
                if self._source is None:
                    self._error("系统没有可用的定位服务；请检查系统定位开关，或使用地图选点。")
                    return
                self._source.positionUpdated.connect(self._position)
                self._source.errorOccurred.connect(
                    lambda error: self._error(f"定位失败或超时（{error.name}）；原观测点未改变。")
                )
            self._source.requestUpdate(20000)
        except Exception as exc:
            self._error(f"定位不可用：{exc}。可使用地图选点。")

    def _position(self, info):
        if not self._busy:
            return
        from PySide6.QtPositioning import QGeoPositionInfo
        coordinate = info.coordinate()
        if not info.isValid() or not coordinate.isValid():
            self._error("系统未返回有效坐标；原观测点保持不变。")
            return
        age = info.timestamp().secsTo(QDateTime.currentDateTimeUtc())
        if age > 300 or age < -60:
            self._error("系统返回的位置时间已过期或异常；请重新定位，原观测点保持不变。")
            return
        accuracy = (
            info.attribute(QGeoPositionInfo.Attribute.HorizontalAccuracy)
            if info.hasAttribute(QGeoPositionInfo.Attribute.HorizontalAccuracy) else math.nan
        )
        self._busy = False
        self.busyChanged.emit(False)
        self.located.emit(coordinate.latitude(), coordinate.longitude(), accuracy)

    def _error(self, message):
        if self._busy:
            self._busy = False
            self.busyChanged.emit(False)
        self.failed.emit(message)
