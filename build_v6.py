# -*- coding: utf-8 -*-
"""v6 打包：用装了 PyQt6 的系统 Python 3.12 打包到 dist_v6。

v6 变更：放开 write_file 到工作区外（系统目录仍护栏）、确认框置顶居中、
生成时可上划、工具卡折叠带标签、Agent 自主+完成自检、百炼 qwen3.7/3.8-flash、PROMPT_VERSION=6。
"""
import os
import sys
import subprocess

PROJ = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJ)

args = [
    sys.executable, "-m", "PyInstaller",
    "--name", "AIWorkbench",
    "--onedir", "--windowed", "--noconfirm",
    "--hidden-import", "PyQt6.sip",
    "--hidden-import", "markdown.extensions.tables",
    "--hidden-import", "markdown.extensions.nl2br",
    "--exclude-module", "numpy", "--exclude-module", "PIL",
    "--distpath", os.path.join(PROJ, "dist_v6"),
    "main.py",
]

print("PYINSTALLER:", " ".join(args[:4]), "...")
r = subprocess.run(args)
print("RETURNCODE:", r.returncode)
sys.exit(r.returncode)
