# -*- coding: utf-8 -*-
"""崩溃与卡死取证（v9.22.7）。

为什么要有这个文件：用户在**别的电脑**上反馈「一上来就未响应，有时候能用了，
点两下就自己卡退」。这类问题在开发机上复现不了，没有日志只能靠猜。
这里做三件事，都是纯被动、零副作用的：

1. **接住所有未捕获异常**：主线程 `sys.excepthook`、子线程 `threading.excepthook`，
   把完整的 traceback 写进日志文件（而不是像现在这样在 windowed 模式下彻底消失）。
2. **接住 Qt 自己的警告**（`qInstallMessageHandler`）：像
   `QObject: Cannot create children for a parent that is in a different thread`
   这种跨线程误用，Qt 只是打一行警告就继续跑，等到崩的时候现场早没了 —— 现在落盘。
3. **接住原生崩溃**（`faulthandler`）：Qt 段错误不会经过 Python 异常钩子，
   只有 faulthandler 能把当时的调用栈写出来。

另外提供「上次是不是异常退出」的判断（`running.flag`）：
启动时如果发现上个进程没走到正常退出，就说明它崩过 / 被强杀过，
主窗口会据此提示「要不要用安全模式启动」（关掉 Web 渲染、跳过联网检查）。

日志位置：`%LOCALAPPDATA%\\AIWorkbench\\logs\\`（开发态同路径，不污染仓库）。
"""
import os
import sys
import time
import traceback

_APP_DIR_NAME = "AIWorkbench"
_installed = False
_log_path = None
_flag_path = None
_fh = None


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, _APP_DIR_NAME, "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.join(os.path.expanduser("~"), "." + _APP_DIR_NAME, "logs")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            return None
    return d


def state_dir():
    d = log_dir()
    if not d:
        return None
    return os.path.dirname(d)


def log_path():
    return _log_path


def _write(text):
    """写一行到日志（同时尽力打到 stderr，开发态能直接看见）。"""
    global _log_path
    if not _log_path:
        return
    try:
        with open(_log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(text if text.endswith("\n") else text + "\n")
    except Exception:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(text if text.endswith("\n") else text + "\n")
            sys.stderr.flush()
    except Exception:
        pass


def log(msg):
    """给业务代码用的普通日志（带时间戳）。"""
    _write("%s  %s" % (time.strftime("%H:%M:%S"), str(msg)))


def install(version=""):
    """装好钩子。**必须在创建 QApplication 之前调用**（faulthandler/Qt 都要求）。"""
    global _installed, _log_path, _flag_path, _fh
    if _installed:
        return _log_path
    _installed = True

    d = log_dir()
    if d:
        # 一天一个文件：崩了以后按日期就能找到；同一进程内复用同一个句柄。
        _log_path = os.path.join(d, "run-%s.log" % time.strftime("%Y%m%d"))
        sd = state_dir()
        _flag_path = os.path.join(sd, "running.flag") if sd else None

    _write("")
    _write("=" * 70)
    _write("启动 %s  v%s  frozen=%s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), version or "?",
        bool(getattr(sys, "frozen", False))))
    _write("exe = %s" % sys.executable)
    _write("cwd = %s" % os.getcwd())
    _write("=" * 70)

    # --- 1) Python 未捕获异常（主线程） ---
    def _hook(exc_type, exc, tb):
        _write("[主线程未捕获异常] %s: %s" % (exc_type.__name__, exc))
        _write("".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook

    # --- 2) 子线程未捕获异常（Python 3.8+）---
    try:
        import threading

        def _thook(args):
            if getattr(args, "exc_type", None) is None:
                return
            _write("[子线程未捕获异常] %s: %s  (thread=%s)" % (
                args.exc_type.__name__, args.exc_value,
                getattr(args.thread, "name", "?")))
            _write("".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))

        threading.excepthook = _thook
    except Exception:
        pass

    # --- 3) 原生崩溃（段错误 / 栈溢出 / Qt abort）---
    try:
        import faulthandler

        if _log_path:
            _fh = open(_log_path, "a", encoding="utf-8", errors="replace")
            faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        _fh = None

    # --- 4) Qt 自己的警告（跨线程误用就是从这里暴露的）---
    try:
        from PyQt6.QtCore import qInstallMessageHandler

        def _qt_handler(mode, ctx, message):
            try:
                _write("[Qt] %s" % str(message).strip())
            except Exception:
                pass

        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass

    return _log_path


# ---------------- 「上次是不是异常退出」 ----------------

def mark_running():
    """启动时打标记；正常退出时 clear_running() 会删掉它。"""
    if not _flag_path:
        return
    try:
        with open(_flag_path, "w", encoding="utf-8") as f:
            f.write("%d\n%s\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
    except Exception:
        pass


def clear_running():
    if not _flag_path:
        return
    try:
        os.remove(_flag_path)
    except Exception:
        pass


def last_unclean_exit():
    """上次是否没走正常退出。返回 True/False（读不到就当 False，不误报）。"""
    if not _flag_path or not os.path.exists(_flag_path):
        return False
    try:
        with open(_flag_path, "r", encoding="utf-8") as f:
            pid = int((f.readline() or "0").strip() or 0)
    except Exception:
        return False
    # 同一台机器上一个进程号还活着 → 不是崩溃，是多开抢锁失败留下的标记
    if pid and _pid_alive(pid):
        return False
    return True


def _pid_alive(pid):
    try:
        if os.name == "nt":
            import subprocess
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid)],
                                 capture_output=True, timeout=5)
            return str(pid) in (out.stdout or b"").decode("utf-8", "ignore")
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def tail(n=40):
    """读日志最后 n 行（给「关于」页/诊断用）。"""
    if not _log_path or not os.path.exists(_log_path):
        return ""
    try:
        with open(_log_path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-int(n):])
    except Exception:
        return ""


def start_watchdog(interval_ms=2000, stall_s=15):
    """「界面卡死」取证：主线程心跳 + 后台监视。

    用户说的「一上来就未响应」就是主线程被某个同步调用堵住了，但堵在哪里
    只有当时的栈知道。这里用一个 QTimer 在主线程打心跳，后台线程发现心跳
    停了超过 ``stall_s`` 秒，就把**所有线程的栈**（faulthandler）写进日志。
    这样下次用户说「卡死了」，日志里直接有现场。

    Args:
        interval_ms: 心跳间隔（毫秒）。
        stall_s: 超过这么久没心跳就判定为卡死并取证。

    Returns:
        心跳 QTimer（必须持有引用，否则会被 GC 掉停表）。
    """
    from PyQt6.QtCore import QTimer

    state = {"t": time.time(), "dumped": 0.0}
    timer = QTimer()
    timer.setInterval(int(interval_ms))

    def _beat():
        state["t"] = time.time()

    timer.timeout.connect(_beat)
    timer.start()

    def _watch():
        while True:
            time.sleep(3)
            gap = time.time() - state["t"]
            if gap < stall_s:
                continue
            # 同一次卡死只取证一次，避免刷满日志
            if time.time() - state["dumped"] < 30:
                continue
            state["dumped"] = time.time()
            _write("[卡死] 界面主线程已 %.0f 秒没有响应" % gap)
            try:
                import faulthandler
                if _fh is not None:
                    faulthandler.dump_traceback(file=_fh, all_threads=True)
            except Exception:
                pass
            _write("[卡死] 以上是所有线程的调用栈")

    import threading
    threading.Thread(target=_watch, daemon=True, name="awb-watchdog").start()
    return timer
