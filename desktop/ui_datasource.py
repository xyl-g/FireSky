"""Desktop settings: credentials and map preferences, not data operations."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from desktop import widgets
from desktop.widgets import load_app_config, save_app_config

CDSAPIRC = Path.home() / ".cdsapirc"


class SettingsPanel(QWidget):
    mapSettingsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)
        note = widgets.role_label(
            "凭据仅以明文保存在本机配置文件（权限 0600），不写入仓库。", "hint"
        )
        note.setWordWrap(True)
        root.addWidget(note)
        root.addWidget(widgets.section_title("CAMS · Copernicus ADS"))
        row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("ADS API Key")
        row.addWidget(self.key_edit, 1)
        show = QPushButton("显示")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        ))
        row.addWidget(show)
        save = QPushButton("保存 Key")
        save.clicked.connect(self._save_key)
        row.addWidget(save)
        root.addLayout(row)
        self.key_status = widgets.role_label("", "hint")
        self.key_status.setWordWrap(True)
        root.addWidget(self.key_status)

        root.addSpacing(16)
        root.addWidget(widgets.section_title("地图供应商"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("OpenStreetMap（免 Key）", "osm")
        self.provider_combo.addItem("天地图（需 Key）", "tianditu")
        self.provider_combo.addItem("Esri", "esri")
        root.addWidget(self.provider_combo)
        self.key_row = QWidget()
        key_layout = QHBoxLayout(self.key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.map_key_edit = QLineEdit()
        self.map_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.map_key_edit.setPlaceholderText("天地图浏览器端 Key")
        self.map_key_edit.returnPressed.connect(self._save_map_key)
        key_layout.addWidget(self.map_key_edit, 1)
        show_map = QPushButton("显示")
        show_map.setCheckable(True)
        show_map.toggled.connect(lambda on: self.map_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        ))
        key_layout.addWidget(show_map)
        save_map = QPushButton("保存并刷新地图")
        save_map.clicked.connect(self._save_map_key)
        key_layout.addWidget(save_map)
        root.addWidget(self.key_row)
        self.map_key_status = widgets.role_label("", "hint")
        self.map_key_status.setWordWrap(True)
        root.addWidget(self.map_key_status)
        root.addStretch(1)
        self._load_saved_key()
        self._load_map_key()
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

    def _load_saved_key(self):
        key = str(load_app_config().get("ads_api_key") or "")
        if key:
            self.key_edit.setText(key)
            self.key_status.setText("已保存本机 ADS 凭据。")
        elif CDSAPIRC.is_file():
            self.key_status.setText("检测到 ~/.cdsapirc；下载时由 ADS 客户端读取。")
        else:
            self.key_status.setText(
                "尚未配置；本地 CAMS 缺失时无法完成严格预测，请先配置并下载数据。"
            )

    def _save_key(self):
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "ADS 凭据", "请输入 ADS API Key。")
            return
        config = load_app_config()
        try:
            existing = CDSAPIRC.read_text("utf-8") if CDSAPIRC.is_file() else ""
            lines = [
                line for line in existing.splitlines()
                if not line.strip().lower().startswith(("url:", "key:"))
            ]
            lines[0:0] = [
                f"url: {config.get('ads_url') or 'https://ads.atmosphere.copernicus.eu/api'}",
                f"key: {key}",
            ]
            CDSAPIRC.write_text("\n".join(lines) + "\n", "utf-8")
            CDSAPIRC.chmod(0o600)
            config["ads_api_key"] = key
            save_app_config(config)
        except OSError as exc:
            QMessageBox.warning(self, "保存凭据失败", str(exc))
            return
        self.key_status.setText("已保存并同步 ~/.cdsapirc（明文本机存储，权限 0600）。")

    def _load_map_key(self):
        from desktop import web_map

        self.map_key_edit.setText(web_map.tianditu_key())
        self.provider_combo.setCurrentIndex(max(
            0, self.provider_combo.findData(web_map.map_source())
        ))
        self._refresh_key_row_visibility()
        self.map_key_status.setText(
            "天地图 Key 已配置。" if web_map.tianditu_key()
            else "未配置天地图 Key；OpenStreetMap 仍可使用。"
        )

    def _refresh_key_row_visibility(self):
        self.key_row.setVisible(self.provider_combo.currentData() == "tianditu")

    def _on_provider_changed(self, _index):
        from desktop import web_map

        web_map.set_map_source(self.provider_combo.currentData())
        self._refresh_key_row_visibility()
        self.mapSettingsChanged.emit()

    def _save_map_key(self):
        from desktop import web_map

        raw = self.map_key_edit.text().strip()
        if raw and not web_map.set_tianditu_key(raw):
            self.map_key_status.setText("Key 格式无效：需要 16–64 位十六进制串，原 Key 未修改。")
            return
        if not raw:
            web_map.set_tianditu_key("")
        self.map_key_edit.setText(web_map.tianditu_key())
        self.map_key_status.setText(
            "Key 已保存并生效。" if web_map.tianditu_key()
            else "本机 Key 已清除；环境变量中的 Key 仍可能生效。"
        )
        self.mapSettingsChanged.emit()

    def focus_map_key(self):
        self.provider_combo.setCurrentIndex(self.provider_combo.findData("tianditu"))
        self.map_key_edit.setFocus()
        self.map_key_edit.selectAll()


class SettingsDialog(QDialog):
    def __init__(self, panel: SettingsPanel, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FireSky · 设置")
        self.setModal(False)
        self.resize(720, 500)
        self.setMinimumSize(560, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        layout.addWidget(scroll)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

    def open(self):
        self.show()
        self.raise_()
        self.activateWindow()
