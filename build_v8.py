# -*- coding: utf-8 -*-
"""v8 打包：在 v7（文档解析 + 视觉识别）基础上，加入文档生成、记忆、计划、
知识库、系统交互、联网搜索、MCP 等 31 个工具。

关键点：
- 文档相关库都是「函数内动态导入」，PyInstaller 静态分析会漏 -> 显式 hidden-import；
- python-docx / python-pptx 依赖包内模板文件，必须 --collect-data，否则运行时报找不到模板；
- reportlab 的中文字体走内置 CID 字体，需要 --collect-data reportlab；
- 截图要 PIL + mss，不能再把 PIL 排除了。
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
    # 文档解析（读）
    "docx", "openpyxl", "pdfminer", "pdfminer.high_level",
    "bs4", "lxml", "requests",
    # 文档生成（写）
    "pptx", "reportlab", "reportlab.pdfbase._cidfontdata",
    "reportlab.pdfbase.cidfonts", "reportlab.platypus",
    "reportlab.pdfbase.ttfonts",
    # 系统交互
    "psutil", "pyperclip", "mss", "PIL", "PIL.Image", "PIL.ImageGrab",
]

collect_data = ["docx", "pptx", "reportlab", "pdfminer", "lxml"]

collect_sub = ["pdfminer", "reportlab", "pptx", "docx", "openpyxl"]

exclude = [
    "numpy", "matplotlib", "cv2", "tkinter", "moviepy", "imageio",
    "edge_tts", "pandas", "scipy", "IPython", "notebook",
    "PyQt6.QtWebEngineCore", "PyQt6.QtQuick", "PyQt6.QtMultimedia",
    # 本机同时装了 PyQt5（别的项目用的）。PyInstaller 不允许一个冻结程序里
    # 混入多种 Qt 绑定，检测到 PyQt5 会直接 abort —— 本程序只用 PyQt6，全部排除。
    "PyQt5", "PyQt5.sip", "PySide2", "PySide6", "qtpy",
]

args = [
    sys.executable, "-m", "PyInstaller",
    "--name", "AIWorkbench",
    "--onedir", "--windowed", "--noconfirm",
    "--distpath", os.path.join(PROJ, "dist_v8"),
]
for d in collect_data:
    args += ["--collect-data", d]
for s in collect_sub:
    args += ["--collect-submodules", s]
for e in exclude:
    args += ["--exclude-module", e]
for h in hidden:
    args += ["--hidden-import", h]
args.append("main.py")

print("PYINSTALLER 开始：", " ".join(args[:6]), "...")
r = subprocess.run(args)
print("RETURNCODE:", r.returncode)
sys.exit(r.returncode)
