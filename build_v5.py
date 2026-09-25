# -*- coding: utf-8 -*-
"""用装了 PyQt6 的系统 Python 3.12 打包 v5 到 dist_v5（绕过旧 dist 被锁的问题）。"""
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
    "--distpath", os.path.join(PROJ, "dist_v5"),
    "main.py",
]

print("PYINSTALLER:", " ".join(args[:4]), "...")
r = subprocess.run(args)
print("RETURNCODE:", r.returncode)
sys.exit(r.returncode)
