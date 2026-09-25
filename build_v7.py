# -*- coding: utf-8 -*-
"""v7 打包：在 v6 基础上加入文档解析（Word/PDF/Excel/PPT）+ 图片视觉识别 + 新工具集。

关键：文档库是「函数内动态导入」，PyInstaller 静态分析容易漏，
所以这里显式 --hidden-import + --collect-submodules/--collect-data 把它们抓进包。
"""
import os
import sys
import subprocess

PROJ = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJ)

hidden = [
    "PyQt6.sip",
    "markdown.extensions.tables",
    "markdown.extensions.nl2br",
    # 文档解析
    "docx", "openpyxl", "PyPDF2", "pdfminer", "pdfminer.high_level",
    "bs4", "lxml", "requests",
]

args = [
    sys.executable, "-m", "PyInstaller",
    "--name", "AIWorkbench",
    "--onedir", "--windowed", "--noconfirm",
    "--distpath", os.path.join(PROJ, "dist_v7"),
    "--collect-submodules", "pdfminer",
    "--collect-data", "pdfminer",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "--exclude-module", "tkinter",
]
for h in hidden:
    args += ["--hidden-import", h]
args.append("main.py")

print("PYINSTALLER:", " ".join(args[:4]), "...")
r = subprocess.run(args)
print("RETURNCODE:", r.returncode)
sys.exit(r.returncode)
