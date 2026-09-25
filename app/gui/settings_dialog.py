# -*- coding: utf-8 -*-
"""设置对话框：系统提示词、默认模型、联网/Agent 开关、API 密钥。"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QLineEdit, QComboBox,
    QCheckBox, QPushButton, QHBoxLayout, QFormLayout,
)
from PyQt6.QtCore import Qt

from .. import config


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(580)
        self.settings = settings

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("系统提示词（AI 的人设，可自由修改）："))
        self.prompt = QPlainTextEdit(settings.get("system_prompt", ""))
        self.prompt.setMinimumHeight(120)
        layout.addWidget(self.prompt)

        layout.addWidget(QLabel("默认模型："))
        self.model = QComboBox()
        self.model.addItem("[自动] 免费优先", "auto")
        for m in config.all_models_sorted():
            badge = "[免费]" if m["free"] else "[付费]"
            label = f"{badge} {m['name']}  ·  {m['provider_label']}"
            self.model.addItem(label, m["id"])
        idx = self.model.findData(settings.get("selected_model", "auto"))
        if idx >= 0:
            self.model.setCurrentIndex(idx)
        layout.addWidget(self.model)

        layout.addWidget(QLabel("界面主题（深色模式会用强制调色板，保证文字清晰）："))
        self.theme = QComboBox()
        self.theme.addItem("浅色", "light")
        self.theme.addItem("深色", "dark")
        tidx = self.theme.findData(settings.get("theme", "light"))
        if tidx >= 0:
            self.theme.setCurrentIndex(tidx)
        layout.addWidget(self.theme)

        self.search = QCheckBox("默认开启联网搜索")
        self.search.setChecked(settings.get("enable_search", False))
        self.agent = QCheckBox("默认开启 Agent 模式（允许 AI 操作工作区文件/命令）")
        self.agent.setChecked(settings.get("agent_mode", True))
        layout.addWidget(self.search)
        layout.addWidget(self.agent)

        layout.addWidget(QLabel("API 密钥（仅保存在本工作区 settings.json，不会上传）："))
        self.keys = {}
        form = QFormLayout()
        for p in config.PROVIDERS:
            le = QLineEdit(settings.get("api_keys", {}).get(p["name"], ""))
            le.setEchoMode(QLineEdit.EchoMode.Password)
            self.keys[p["name"]] = le
            form.addRow(p["label"], le)
        layout.addLayout(form)

        btns = QHBoxLayout()
        save = QPushButton("保存")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        layout.addLayout(btns)

    def get_settings(self):
        s = dict(self.settings)
        s["system_prompt"] = self.prompt.toPlainText()
        s["selected_model"] = self.model.currentData()
        s["enable_search"] = self.search.isChecked()
        s["agent_mode"] = self.agent.isChecked()
        s["theme"] = self.theme.currentData() or "light"
        keys = dict(s.get("api_keys", {}))
        for name, le in self.keys.items():
            keys[name] = le.text().strip()
        s["api_keys"] = keys
        return s
