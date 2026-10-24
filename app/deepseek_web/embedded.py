# -*- coding: utf-8 -*-
"""内嵌浏览器宿主（v9.18.0 P0）：用 QtWebEngine 当 DeepSeek 网页的"浏览器"。

背景
----
v9.17 及以前，DeepSeek 网页通道靠 ``service._start_chrome()`` 在本机拉起一个
**外部 Chrome/Edge**（独立 user-data-dir + ``--remote-debugging-port``），再用
``CDPClient`` 连上去。用户机器上不一定有 Chrome，且多一个外部进程不好管。

实测（见 00-context / 本次可行性验证）：QtWebEngine 设
``QTWEBENGINE_REMOTE_DEBUGGING=<port>`` 后，``http://127.0.0.1:<port>/json``
会暴露**标准 CDP** 的 page target，``Input.*`` 事件 ``isTrusted=true``。
所以这里**只换宿主、不换驱动**：``web_driver.py`` 的 JS 状态机 / 轮询 / 防重发
一行不改，仍然对着一个 CDP 端口说话。

为什么不用 ``webengine._render_on_main``
---------------------------------------
``app/webengine.py`` 里那套是"一次性抓取"模型：渲染完就 ``deleteLater()`` 掉
view。DeepSeek 需要的是**长连接常驻页面**（登录态、输入框、流式回答都在页面里），
所以这里自己管 view/profile 生命周期，只借用它的主线程调度器 ``_Dispatcher``。

线程约束（必须遵守）
--------------------
- ``QWebEngineView`` / ``QWebEngineProfile`` **只能在 Qt GUI 线程**创建、show、hide、
  销毁。本模块所有触碰 Qt 控件的动作都包在 ``_gui_call()`` 里（内部走
  ``webengine._Dispatcher``：GUI 线程直接跑，工作线程排队 + 超时等待）。
- ``start()`` 里的 ``/json`` 轮询、``attach()`` 里的 CDP 连接是纯网络操作，
  留在**调用方线程**执行，避免阻塞 GUI 事件循环。
"""
import json
import os
import re
import threading
import time
import urllib.request

from .. import webengine as _webengine

TARGET_URL = "https://chat.deepseek.com/"
TARGET_HOST = "chat.deepseek.com"
PROFILE_NAME = "deepseek-web"

# 命名 profile 的强引用：QWebEngineProfile 被 Python GC 掉的话，页面会立刻丢 cookie。
_PROFILE_HOLDER = []


class EmbeddedHostError(RuntimeError):
    """内嵌宿主启动 / 连接失败（service 会据此回退到 Chrome）。"""


def debug_port():
    """当前进程的 CDP 调试端口（唯一来源是 ``webengine.REMOTE_DEBUG_PORT``）。"""
    return int(getattr(_webengine, "REMOTE_DEBUG_PORT", 9333))


def app_data_dir():
    """应用数据目录：与 Chrome 专用 profile 同一层（%LOCALAPPDATA%\\AIWorkbench）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AIWorkbench")


def profile_storage_path():
    return os.path.join(app_data_dir(), "webengine", PROFILE_NAME)


def list_targets(port=None, timeout=2.0):
    """读 ``http://127.0.0.1:<port>/json``，返回 target 列表（失败返回 []）。"""
    port = int(port or debug_port())
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json",
                                    timeout=float(timeout)) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _gui_call(fn, timeout=20.0):
    """把只能在 GUI 线程做的事丢过去执行（带超时，绝不永久阻塞）。"""
    if not _webengine.engine_available():
        raise EmbeddedHostError("浏览器内核不可用（QtWebEngine 未安装或导入过晚）")
    return _webengine._Dispatcher.call(fn, timeout)


def pump_events():
    """在 GUI 线程上等待时泵一下事件队列。

    ★ 为什么必须泵：`view.show()` / `view.load()` 只是把事件排进队列，
    真正创建平台窗口、让 Chromium 建出 page target 要等事件循环跑一轮。
    如果调用方正好在 GUI 线程（用户点「启动网页」就是这种），而这里只是
    `time.sleep()` 干等，`/json` 永远不会出现 target —— 实测会白等 40 秒后
    误判失败、白白回退到 Chrome。

    用 `ExcludeUserInputEvents`：只处理窗口/定时器/网络事件，**不重入处理鼠标键盘**，
    避免在按钮回调里泵事件时用户又点出一次「启动」。
    """
    try:
        from PyQt6.QtCore import QCoreApplication, QEventLoop
        app = QCoreApplication.instance()
        if app is not None and app.thread().isCurrentThread():
            app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    except Exception:
        pass


def on_gui_thread():
    """当前线程是否是 Qt GUI 线程。"""
    try:
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        return app is not None and app.thread().isCurrentThread()
    except Exception:
        return False


def run_off_gui(fn, timeout=90.0):
    """把阻塞型 CDP 流程挪到工作线程；GUI 线程只负责泵事件。

    ★ 实测坑（v9.18.0）：QtWebEngine 的 DevTools HTTP/WS 端点**在 GUI 线程被
    阻塞时不再响应** —— 在 GUI 线程里 `urlopen("http://127.0.0.1:9333/json")`
    会一路超时，而同样的请求放到工作线程 0.7 秒就拿到 target。
    但 `QWebEngineView` 又只能在 GUI 线程创建，所以只能：
       GUI 线程跑嵌套事件循环（负责 `_Dispatcher` 的建视图调用）
       + 工作线程做 `/json` 轮询与 CDP 收发。

    不在 GUI 线程时直接执行，不再多开线程/事件循环。
    """
    if not on_gui_thread():
        return fn()

    from PyQt6.QtCore import QEventLoop, QTimer

    box = {}

    def runner():
        try:
            box["value"] = fn()
        except BaseException as exc:      # 异常原样带回调用方
            box["error"] = exc

    th = threading.Thread(target=runner, name="awb-embedded-io", daemon=True)
    th.start()

    deadline = time.time() + float(timeout)

    def _pump_once(max_ms=150):
        """跑一小段嵌套事件循环（只跑 max_ms，随后主动退出）。"""
        loop = QEventLoop()
        QTimer.singleShot(int(max_ms), loop.quit)
        loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    # ★ 实测坑：在 QtWebEngine 里 `deleteLater()` 掉 QWebEngineView / QWebEngineProfile
    #   会让**当前嵌套 QEventLoop 直接 return**（没有任何 quit() 调用），
    #   如果只 exec 一次就会误判成"工作线程还没结束"并抛超时。
    #   所以改成短周期泵：被打断就重新起一个，直到工作线程真正结束。
    while th.is_alive() and time.time() < deadline:
        _pump_once(150)

    if th.is_alive():
        raise EmbeddedHostError(
            f"内嵌宿主初始化超过 {float(timeout):.0f} 秒（GUI 线程等待超时）")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _strip_qt_ua(ua):
    """去掉 QtWebEngine 指纹（千万别把 `QtWebEngine/6.10.0` 发给 DeepSeek）。"""
    ua = str(ua or "")
    ua = re.sub(r"\s*QtWebEngine/[\d.]+", "", ua)
    ua = ua.replace("QtWebEngine", "")
    ua = re.sub(r"\s{2,}", " ", ua).strip()
    return ua


_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/130.0.0.0 Safari/537.36")


class EmbeddedHost:
    """一个常驻的离屏 QWebEngineView + 命名 profile。

    典型用法::

        host = EmbeddedHost(log=log)
        host.start(9333)          # 建 view/profile，加载首页，等 CDP target
        driver = host.attach()    # 建 CDP 连接 -> WebDriver（embedded=True）
        host.show_login()         # 需要扫码时亮出窗口
        host.hide_login()         # 登录完收回去
        host.stop()               # 退出时收拾
    """

    def __init__(self, log=None):
        self._log = log or (lambda *a, **k: None)
        self._port = debug_port()
        self._view = None
        self._page = None
        self._profile = None
        self._driver = None
        self._target = None
        self._silent = True
        self._lock = threading.RLock()

    # ---------- 小工具 ----------
    def _logf(self, level, msg):
        try:
            self._log(level, msg)
        except Exception:
            pass

    @property
    def port(self):
        return self._port

    @property
    def running(self):
        """view 是否已建好（不代表页面已就绪）。"""
        return self._view is not None

    @property
    def driver(self):
        return self._driver

    def is_connected(self):
        drv = self._driver
        if drv is None or getattr(drv, "cdp", None) is None:
            return False
        try:
            return bool(drv.cdp.is_alive())
        except Exception:
            return False

    def pump(self):
        """GUI 线程等待时泵事件（工作线程调用无副作用）。"""
        pump_events()

    # ---------- 建 view / profile（GUI 线程） ----------
    def _create_view(self):
        """**必须在 GUI 线程执行**：建命名 profile + page + view 并加载首页。"""
        from PyQt6.QtCore import Qt, QUrl
        from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        storage = profile_storage_path()
        os.makedirs(storage, exist_ok=True)
        os.makedirs(os.path.join(storage, "cache"), exist_ok=True)

        # ★ 必须用**命名 profile**：默认 profile 是 off-the-record
        #   （实测 isOffTheRecord()==True），退出即丢 cookie，等于每次都要重新登录。
        profile = QWebEngineProfile(PROFILE_NAME)
        profile.setPersistentStoragePath(storage)
        profile.setCachePath(os.path.join(storage, "cache"))
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        try:
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        except Exception:
            pass
        ua = _strip_qt_ua(profile.httpUserAgent()) or _DEFAULT_UA
        profile.setHttpUserAgent(ua)
        _PROFILE_HOLDER.append(profile)

        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # 后台运行：不映射到桌面，但 Chromium 仍认为页面 visible（实测）。
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        page = QWebEnginePage(profile, view)
        view.setPage(page)
        view.resize(1280, 900)
        view.show()

        self._profile = profile
        self._view = view
        self._page = page
        view.load(QUrl(TARGET_URL))
        self._logf("info", f"内嵌宿主已创建（profile={storage}，UA={ua[:80]}…）")

    # ---------- 启动 / 连接 ----------
    def start(self, port=None, timeout=45.0):
        """建 view/profile、加载首页，并等 ``/json`` 出现 DeepSeek 页面 target。

        Args:
            port: CDP 端口（默认取 ``QTWEBENGINE_REMOTE_DEBUGGING``）。
            timeout: 等待 target 的上限（秒）。超时抛 ``EmbeddedHostError``，
                     service 捕获后会自动回退到 Chrome。
        """
        self._port = int(port or debug_port())
        env_port = os.environ.get("QTWEBENGINE_REMOTE_DEBUGGING")
        if not env_port:
            self._logf("warn", "QTWEBENGINE_REMOTE_DEBUGGING 未设置：CDP 端口不会出现，"
                               "内嵌宿主将启动失败")
        # 见 run_off_gui 的说明：CDP 的 HTTP/WS 不能在 GUI 线程上等。
        return run_off_gui(lambda: self._start_impl(timeout), timeout=float(timeout) + 45.0)

    def _start_impl(self, timeout=45.0):
        with self._lock:
            if self._view is None:
                _gui_call(self._create_view, timeout=30.0)

        deadline = time.time() + float(timeout)
        last_err = ""
        while time.time() < deadline:
            pump_events()
            targets = list_targets(self._port, timeout=1.5)
            for t in targets:
                if t.get("type") != "page":
                    continue
                url = (t.get("url") or "").lower()
                if TARGET_HOST in url:
                    self._target = t
                    self._logf("info", f"内嵌 CDP target 就绪：{t.get('url')} "
                                       f"(port={self._port})")
                    return t
            if targets:
                last_err = "有 target 但还没有 DeepSeek 页面：" + ", ".join(
                    str(t.get("url") or "") for t in targets[:3])
            time.sleep(0.25)

        raise EmbeddedHostError(
            f"内嵌浏览器 {int(timeout)} 秒内没有暴露 DeepSeek 页面 target"
            f"（端口 {self._port}）。{(' 最近状态：' + last_err) if last_err else ''}"
            "请确认 QTWEBENGINE_REMOTE_DEBUGGING 在 Chromium 启动前已设置。")

    def attach(self):
        """连上内嵌页面的 CDP，返回一个 ``WebDriver``（``embedded=True``）。"""
        return run_off_gui(self._attach_impl, timeout=60.0)

    def _attach_impl(self):
        from .cdp_client import CDPClient
        from .web_driver import WebDriver

        cdp = CDPClient.from_page(self._port, TARGET_HOST, timeout=20.0)
        try:
            cdp.enable_domains()
        except Exception:
            pass
        drv = WebDriver(cdp, log=self._log)
        # ★ 这个标志让 web_driver 放宽 visibilityState 硬闸门（见该文件说明）
        drv.embedded = True
        self._driver = drv
        self._logf("info", "已通过 CDP 接入内嵌 DeepSeek 页面")
        return drv

    # ---------- 窗口显隐（GUI 线程） ----------
    def show_login(self, width=1200, height=860):
        """把内嵌视图**显示到桌面**（扫码 / 输验证码时用）。"""
        def _do():
            from PyQt6.QtCore import Qt
            view = self._view
            if view is None:
                return False
            view.hide()
            view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
            view.resize(int(width), int(height))
            view.move(80, 60)
            view.show()
            view.raise_()
            view.activateWindow()
            return True

        try:
            ok = _gui_call(_do, timeout=15.0)
            self._silent = False
            if ok:
                self._logf("info", "内嵌网页已显示到桌面（登录 / 查看用）")
            return bool(ok)
        except Exception as exc:
            self._logf("warn", f"显示内嵌网页失败：{exc}")
            return False

    def hide_login(self):
        """把视图收回离屏（桌面无窗口，但页面仍然 visible）。"""
        def _do():
            view = self._view
            if view is None:
                return False
            from PyQt6.QtCore import Qt
            view.hide()
            view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
            view.show()
            return True

        try:
            ok = _gui_call(_do, timeout=15.0)
            self._silent = True
            if ok:
                self._logf("info", "内嵌网页已收回后台（桌面无窗口）")
            return bool(ok)
        except Exception as exc:
            self._logf("warn", f"隐藏内嵌网页失败：{exc}")
            return False

    # ---------- 收尾 ----------
    def stop(self):
        """断开 CDP、销毁 view、释放 profile。"""
        with self._lock:
            drv, self._driver = self._driver, None
            if drv is not None:
                try:
                    drv.close()
                except Exception:
                    pass

            def _destroy():
                view, page, profile = self._view, self._page, self._profile
                self._view = None
                self._page = None
                self._profile = None
                if view is not None:
                    try:
                        view.stop()
                        view.setPage(None)
                    except Exception:
                        pass
                    # page 是 view 的子对象，随 view 一起销毁；不再单独 deleteLater，
                    # 免得删两次（Qt 会清理已销毁对象上的 DeferredDelete 事件，但仍不必要）。
                    try:
                        view.deleteLater()
                    except Exception:
                        pass
                if profile is not None:
                    try:
                        profile.deleteLater()
                    except Exception:
                        pass
                try:
                    while profile is not None and profile in _PROFILE_HOLDER:
                        _PROFILE_HOLDER.remove(profile)
                except Exception:
                    pass

            if self._view is not None:
                try:
                    _gui_call(_destroy, timeout=20.0)
                    # deleteLater 只是排队；把事件泵掉，避免进程退出时 Qt 还在等
                    # WebEngine 的异步析构而卡住（实测无事件循环的脚本会挂）。
                    pump_events()
                    time.sleep(0.05)
                    pump_events()
                except Exception as exc:
                    self._logf("warn", f"销毁内嵌视图失败：{exc}")
        self._target = None
        self._logf("info", "内嵌浏览器宿主已停止")
