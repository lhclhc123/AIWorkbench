# -*- coding: utf-8 -*-
"""让子进程在 Windows 上「静默运行」——不弹控制台黑窗口。

为什么需要它：本项目是 windowed exe（无控制台）。每次 `subprocess.run(...)`
在 Windows 上默认都会新建一个 console 窗口，于是每执行一条命令、跑一次 Python、
调一次通知、起一个 MCP 服务，用户就会看到一个黑窗口"啪"地冒出来又消失。
多步任务连着跑就是"一堆小窗口"。

统一用这里的 `run()` / `popen()`，等于同时加：
  - `creationflags=CREATE_NO_WINDOW`（不分配控制台）
  - `startupinfo` + `SW_HIDE`（双保险，兼容 shell=True 的情况）
"""
import subprocess
import sys

IS_WIN = sys.platform == "win32"

# Windows 进程创建标志（非 Windows 上不存在，所以用字面量）
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def startupinfo():
    """让子进程窗口隐藏的 STARTUPINFO；非 Windows 返回 None。"""
    if not IS_WIN:
        return None
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    except Exception:
        return None


def no_window_kwargs():
    """给 subprocess.run / Popen 用的"不弹窗"参数。"""
    if not IS_WIN:
        return {}
    kw = {}
    si = startupinfo()
    if si is not None:
        kw["startupinfo"] = si
    kw["creationflags"] = CREATE_NO_WINDOW
    return kw


def run(args, **kw):
    """等价 subprocess.run，但不弹窗。调用方显式传的 kw 优先级更高。"""
    merged = no_window_kwargs()
    merged.update(kw)
    return subprocess.run(args, **merged)


def popen(args, **kw):
    """等价 subprocess.Popen，但不弹窗。"""
    merged = no_window_kwargs()
    merged.update(kw)
    return subprocess.Popen(args, **merged)
