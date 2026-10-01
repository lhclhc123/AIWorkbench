# -*- coding: utf-8 -*-
"""进程内单例协调器：把"本机 HTTP 服务 / 网页驱动 / Chrome 进程"串起来。

对外只暴露一个入口：`from app.deepseek_web import service` → `service.instance()`。

重要约束（00-context 3.4 与红线）：
- **绝不主动拉起浏览器**：Chrome 只在用户点「启动/打开网页」「重试」时启动；
  其余时候 `ensure_started()` 只起本地 HTTP 服务，`auto_ready()` 未就绪就返回 False。
- `app/` 下禁止裸 subprocess，一律走 `app/winproc.py` 的 `popen()`。
"""
import os
import subprocess
import tempfile
import threading
import time
import urllib.request

from .. import config
from .. import winproc
from . import errors
from .cdp_client import CDPClient, CDPError
from .local_server import LocalServer
from .web_driver import WebDriver, WebDriverError

# 浏览器候选路径（Chrome 优先，Edge 兜底）
BROWSER_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

TARGET_URL = "https://chat.deepseek.com/"
POLL_INTERVAL = 2.5


def instance():
    """模块级便捷入口：固定用法 `from app.deepseek_web import service; service.instance()`。"""
    return DeepSeekWebService.instance()


def _profile_dir():
    """专用 user-data-dir（登录态持久化在这里，不污染日常 Chrome）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AIWorkbench", "deepseek-profile")


class DeepSeekWebService:
    """DeepSeek 网页通道的进程内单例。"""

    _singleton = None
    _singleton_lock = threading.Lock()

    # ---------- 单例 ----------
    @classmethod
    def instance(cls):
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    def __init__(self):
        self._server = None
        self._driver = None
        self._chrome_proc = None
        self._chrome_owned = False
        self._port = int(config.WEB_CHROME_PORT)
        self._state_lock = threading.Lock()
        self._last_kind = errors.WebErrorKind.NOT_STARTED
        self._last_detail = ""
        self._poll_thread = None
        self._poll_stop = threading.Event()

    # ---------- 日志 ----------
    def log(self, level, msg):
        """统一写 %TEMP%/aiworkbench_deepseek_web.log（绝不写用户内容/密钥）。"""
        try:
            path = os.path.join(tempfile.gettempdir(), "aiworkbench_deepseek_web.log")
            line = f"{time.strftime('%H:%M:%S')} | {level} | {msg}"
            with open(path, "a", encoding="utf-8", errors="ignore") as f:
                f.write(line[:500] + "\n")
        except Exception:
            pass

    # ---------- 状态缓存 ----------
    def _set_state(self, kind, detail=""):
        with self._state_lock:
            self._last_kind = kind if isinstance(kind, errors.WebErrorKind) else errors.WebErrorKind.OTHER
            self._last_detail = detail or ""

    def cached_state(self):
        with self._state_lock:
            return self._last_kind, self._last_detail

    def auto_ready(self):
        """供 llm_client.chat_auto 的 web_gate 使用：网页已就绪才纳入自动模式候选。"""
        if self._driver is None:
            return False
        kind, _ = self.cached_state()
        return kind in (errors.WebErrorKind.READY, errors.WebErrorKind.GENERATING)

    # ---------- HTTP 服务 ----------
    def ensure_started(self):
        """确保本机 HTTP 服务已起（幂等）；返回端口。**不拉起浏览器**。"""
        if self._server is None:
            self._server = LocalServer(
                driver_provider=lambda: self._driver,
                notify=self.notify,
                log=self.log,
                state_provider=self.cached_state,
            )
        port = self._server.start(config.WEB_DEFAULT_PORT)
        self._write_back_port(port)
        self._start_poller()
        return port

    def is_started(self):
        return self._server is not None and self._server.is_started()

    def _write_back_port(self, port):
        """把真实端口写回 config provider 的 base_url（provider_by_name 返回同一 dict）。"""
        p = config.provider_by_name(config.WEB_PROVIDER_NAME)
        if p is not None:
            p["base_url"] = f"http://127.0.0.1:{int(port)}"

    # ---------- 后台状态轮询（避免界面线程被 CDP 阻塞） ----------
    def _start_poller(self):
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, name="awb-dsw-poll",
                                             daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._poll_stop.is_set():
            try:
                self._refresh_once()
            except Exception:
                pass
            self._poll_stop.wait(POLL_INTERVAL)

    def _refresh_once(self):
        drv = self._driver
        if drv is None:
            kind = (errors.WebErrorKind.NOT_STARTED if self._find_browser()
                    else errors.WebErrorKind.NOT_INSTALLED)
            self._set_state(kind, errors.WEB_ERROR_TEXT.get(kind, ""))
            return
        try:
            kind, detail = drv.probe_state()
        except Exception as exc:
            kind, detail = errors.WebErrorKind.OTHER, str(exc)
        self._set_state(kind, detail)

    # ---------- 浏览器 ----------
    def _find_browser(self):
        for path in BROWSER_CANDIDATES:
            if path and os.path.isfile(path):
                return path
        return None

    def _port_online(self, port=None, timeout=1.5):
        port = int(port or self._port)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version",
                                        timeout=timeout) as resp:
                return len(resp.read()) > 0
        except Exception:
            return False

    def _start_chrome(self, browser, port):
        profile = _profile_dir()
        os.makedirs(profile, exist_ok=True)
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：让 Chrome 独立存活
        detached = 0x00000008 | 0x00000200
        args = [
            browser,
            f"--remote-debugging-port={int(port)}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--disable-features=Translate",
            TARGET_URL,
        ]
        # startupinfo=None：**必须**覆盖 winproc 的 SW_HIDE，否则 Chrome 窗口会被隐藏
        proc = winproc.popen(args, creationflags=detached, close_fds=True,
                             startupinfo=None,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self._chrome_proc = proc
        self._chrome_owned = True
        self.log("info", f"已拉起浏览器：{browser}（调试端口 {port}）")
        return proc

    def _wait_port(self, port=None, wait=20.0):
        port = int(port or self._port)
        deadline = time.time() + float(wait)
        while time.time() < deadline:
            if self._port_online(port):
                return True
            time.sleep(0.5)
        return False

    def _connect(self):
        """连接当前调试端口下的页面并建立 WebDriver。"""
        cdp = CDPClient.from_page(self._port, "chat.deepseek.com", timeout=20.0)
        self._driver = WebDriver(cdp, log=self.log)
        return self._driver

    # ---------- 供界面调用 ----------
    def launch(self):
        """用户点「启动/打开网页」：拉起（或复用）Chrome 并接入网页。"""
        try:
            self.ensure_started()
        except Exception as exc:
            return {"ok": False, "detail": f"本机服务启动失败：{exc}"}
        browser = self._find_browser()
        if not browser:
            return {"ok": False, "detail": errors.humanize(errors.WebErrorKind.NOT_INSTALLED)}
        if not self._port_online():
            try:
                self._start_chrome(browser, self._port)
            except Exception as exc:
                return {"ok": False, "detail": f"拉起浏览器失败：{exc}"}
            if not self._wait_port(self._port, wait=20.0):
                return {"ok": False, "detail": "浏览器调试端口 20 秒内未就绪"
                                               "（可能被安全软件拦截），请点「重试」。"}
        try:
            self._connect()
        except (CDPError, Exception) as exc:
            return {"ok": False, "detail": f"连接网页失败：{exc}"}
        # 打开 DeepSeek 网页（已登录则直接进对话页）
        try:
            self._driver.open_page(TARGET_URL, wait=20.0)
        except WebDriverError as exc:
            self.log("warn", f"打开网页时：{exc}")
        self._refresh_once()
        kind, detail = self.cached_state()
        if kind == errors.WebErrorKind.NOT_LOGGED_IN:
            return {"ok": True, "detail": "网页已打开，请在弹出的窗口里登录一次 DeepSeek，"
                                          "然后回来点「连通性自检」。"}
        return {"ok": True, "detail": detail or "网页已启动"}

    def connect_if_online(self):
        """若调试端口已在线则接入（**绝不主动拉起浏览器**）。返回是否接入成功。"""
        if self._driver is not None:
            return True
        if not self._port_online():
            return False
        try:
            self._connect()
            self._refresh_once()
            return True
        except Exception as exc:
            self.log("warn", f"接入已在线网页失败：{exc}")
            return False

    def reset(self):
        """「重试」：重连 CDP / 重载网页（**不重发**历史提问）。"""
        try:
            if self._driver is not None:
                self._driver.close()
        except Exception:
            pass
        self._driver = None
        if self._server is not None:
            self._server.reset_conversation_state()
        self._set_state(errors.WebErrorKind.NOT_STARTED)
        if self._port_online():
            try:
                self._connect()
            except Exception as exc:
                self.log("warn", f"重连失败：{exc}")
        elif self._find_browser():
            # 端口没了：可能用户手动关了网页，重新拉起
            try:
                self._start_chrome(self._find_browser(), self._port)
                self._wait_port(self._port, wait=15.0)
                if self._port_online():
                    self._connect()
            except Exception as exc:
                self.log("warn", f"重启浏览器失败：{exc}")
        self._refresh_once()

    def status(self):
        """返回给界面用的状态字典（快速、不阻塞）。"""
        chrome = bool(self._find_browser())
        if self._server is not None:
            self._server.chrome_present = chrome
            st = self._server.status()
        else:
            kind, detail = self.cached_state()
            st = {"state": kind.value, "kind": kind.value, "detail": detail,
                  "logged_in": False, "chrome": chrome, "queue": 0, "port": 0,
                  "last_error": ""}
        st["chrome"] = chrome
        st["running"] = self.is_started()
        st["chrome_owned"] = self._chrome_owned
        return st

    def selftest(self):
        """三项自检（用户点「连通性自检」时调用）。"""
        try:
            self.ensure_started()
        except Exception as exc:
            return [{"name": "本机服务", "ok": False, "detail": str(exc)}]
        if self._driver is None:
            # 先尝试接入已在线网页（不主动拉起浏览器）
            self.connect_if_online()
        if self._driver is None:
            return [
                {"name": "网页可达", "ok": False,
                 "detail": "网页未启动，请先点「启动/打开网页」"},
                {"name": "已登录", "ok": False, "detail": "网页未启动，无法判断"},
                {"name": "能否取回答复", "ok": False, "detail": "网页未启动，无法判断"},
            ]
        results = self._server.selftest()
        self._refresh_once()
        return results

    def notify(self, text):
        """推一条给用户看的提示（新对话 / 超长等）。"""
        if self._server is not None:
            self._server.push_notice(text)

    def take_notices(self):
        if self._server is None:
            return []
        return self._server.take_notices()

    def shutdown(self):
        """退出收拾：停服务、断 CDP、结束我们拉起的那个浏览器实例。"""
        self._poll_stop.set()
        try:
            if self._driver is not None:
                self._driver.close()
        except Exception:
            pass
        self._driver = None
        try:
            if self._server is not None:
                self._server.stop()
        except Exception:
            pass
        if self._chrome_owned and self._chrome_proc is not None:
            try:
                self._chrome_proc.terminate()
                self.log("info", "已结束本次拉起的浏览器实例")
            except Exception:
                pass
            self._chrome_proc = None
            self._chrome_owned = False
        self.log("info", "DeepSeek 网页通道已关闭")
