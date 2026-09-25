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
import os
import subprocess
import sys
import tempfile
import time

IS_WIN = sys.platform == "win32"

# Windows 进程创建标志（非 Windows 上不存在，所以用字面量）
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

_console_ready = False


def ensure_hidden_console():
    """给本进程挂一个「隐藏的控制台」，让所有子孙进程都继承它而不是新建可见窗口。

    为什么需要它（只靠 CREATE_NO_WINDOW 不够）：
      本程序是 --windowed（GUI，没有控制台）。CREATE_NO_WINDOW 只对我们**直接**
      起的那个子进程生效；一旦命令里再套一层（cmd /c python xxx.py、AI 写的代码里
      os.system / subprocess.run），那个「孙进程」是控制台程序而父进程又没控制台，
      Windows 就会给它**新建一个可见控制台** —— 用户看到的就是"啪地冒一下、
      不到一秒又消失"的黑窗口。

    挂上隐藏控制台后，子孙进程会继承这个（已隐藏的）控制台，于是不再新建窗口。
    """
    global _console_ready
    if _console_ready or not IS_WIN:
        return False
    _console_ready = True          # 只尝试一次，失败也不再试
    if os.environ.get("AIWORKBENCH_NO_HIDDEN_CONSOLE"):
        return False
    if os.environ.get("AIWORKBENCH_TEST"):
        return False          # 自动化测试（离屏）里不要给自己挂控制台
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.AllocConsole.restype = ctypes.c_int
        k32.GetConsoleWindow.restype = ctypes.c_void_p
        if k32.GetConsoleWindow():      # 开发环境下本来就有控制台，不用管
            return False
        if not k32.AllocConsole():
            return False
        hwnd = k32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
        return True
    except Exception:
        return False


def _has_console():
    """本进程现在有没有控制台（隐藏控制台也算）。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


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
    # ⚠️ 关键点：已经有（隐藏的）控制台时**不要**再传 CREATE_NO_WINDOW。
    # 传了它 = 子进程「不继承控制台」，于是孙进程（控制台程序）又会新建可见窗口，
    # 闪窗就又回来了。有隐藏控制台时让它继承即可，整条进程链都不可见。
    if not _has_console():
        kw["creationflags"] = CREATE_NO_WINDOW
    return kw


def _log_spawn(args, kw):
    """把每次子进程生成记到 %TEMP%/aiworkbench_spawns.log。
    用途：用户反馈「闪窗」时，能对着日志定位到底是哪条命令、
    当时挂没挂上隐藏控制台、用了什么 flags。失败静默（绝不能影响功能）。"""
    try:
        cmd = args if isinstance(args, str) else " ".join(map(str, args))
        line = (f"{time.strftime('%m-%d %H:%M:%S')} "
                f"console={_has_console()} hidden={_console_ready} "
                f"flags={kw.get('creationflags', 0) & 0xFFFFFFFF:#x} "
                f"shell={bool(kw.get('shell'))} :: {cmd}")
        with open(os.path.join(tempfile.gettempdir(), "aiworkbench_spawns.log"),
                  "a", encoding="utf-8", errors="ignore") as f:
            f.write(line[:400] + "\n")
    except Exception:
        pass


def run(args, **kw):
    """等价 subprocess.run，但不弹窗。调用方显式传的 kw 优先级更高。"""
    ensure_hidden_console()
    merged = no_window_kwargs()
    merged.update(kw)
    _log_spawn(args, merged)
    return subprocess.run(args, **merged)


def popen(args, **kw):
    """等价 subprocess.Popen，但不弹窗。"""
    ensure_hidden_console()
    merged = no_window_kwargs()
    merged.update(kw)
    _log_spawn(args, merged)
    return subprocess.Popen(args, **merged)
