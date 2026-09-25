# -*- coding: utf-8 -*-
"""首次/切换工作空间的路径选择对话框。"""
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QFileDialog, QMessageBox,
)


class StartupDialog(QDialog):
    def __init__(self, parent=None, suggest=None):
        super().__init__(parent)
        self.setWindowTitle("选择工作空间")
        self.setMinimumWidth(540)
        self.selected = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "请选择本应用的“工作空间”位置——对话历史、文件、设置都会保存在这里。"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(suggest or "")
        self.path_edit.setPlaceholderText("例如：D:\\AI工作台")
        browse = QPushButton("浏览...")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path_edit)
        row.addWidget(browse)
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "建议放在有读写权限的目录（如文档\\AI工作台）。目录不存在会自动创建。"))

        btns = QHBoxLayout()
        cancel = QPushButton("退出")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择工作空间文件夹")
        if d:
            self.path_edit.setText(d)

    def accept(self):
        p = self.path_edit.text().strip()
        if not p:
            QMessageBox.warning(self, "提示", "请先选择或输入一个目录。")
            return
        low = os.path.abspath(p).lower()
        if "\\temp\\" in low + "\\" or low.rstrip("\\").endswith("\\temp") or "\\tmp\\" in low + "\\":
            QMessageBox.warning(
                self, "提示",
                "不要把工作空间放在临时目录里（会被系统清理、数据会丢失）。\n"
                "建议选择「文档\\AI工作台」这类固定目录。")
            return
        self.selected = p
        super().accept()

    def reject(self):
        self.selected = None
        super().reject()
