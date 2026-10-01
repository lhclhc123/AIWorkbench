# -*- coding: utf-8 -*-
"""进程内单例协调器：把"本机 HTTP 服务 / 网页驱动 / Chrome 进程"串起来。

对外只暴露一个入口：`from app.deepseek_web import service` → `service.instance()`。

重要约束（00-context 3.4 与红线）：
- **不主动打扰**：`ensure_started()` 只起本地 HTTP 服务，不碰浏览器；`auto_ready()` 未就绪返回 False。
- **自动拉起只走 `ensure_page()` 一个口**（v9.14.2 起）：用户开了「自动拉起」且当前是
  DeepSeek 网页版模型时才自动开浏览器；否则仍只在用户点「启动/打开网页」「重试」时启动。
  自动拉起默认走**后台无头**（`config.WEB_SILENT_WINDOW=True`，桌面零窗口但可见性仍是 visible），
  用户随时可点「显示网页窗口」切到有窗口模式（会重启浏览器，登录态不丢）。
- `connect_if_online()` 依旧**绝不**拉起浏览器（保守入口，供不想被打扰的路径使用）。
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
from .cdp_client import CDPClient
from .local_server import LocalServer
from .web_driver import WebDriver

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
        self._headless = False          # 当前 Chrome 是否为后台无头模式
        self._port = int(config.WEB_CHROME_PORT)
        # 静默离屏偏好（由设置页同步；None 时跟随 config）
        self.silent = bool(getattr(config, "WEB_SILENT_WINDOW", True))
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

    def _start_chrome(self, browser, port, silent=None):
        """拉起 Chrome（独立 profile + 远程调试端口）。

        Args:
            silent: True / None 时用 **无头后台模式**（`--headless=new`）——
                    桌面完全不留窗口，但实测 `visibilityState` 仍为 `visible`，
                    发送/收答复照常（v9.14.2 实测：headless 下发送 1.9s 拿到答复）。
                    显式传 False 则显示正常窗口（用户要扫码登录 / 想看页面时用）。
                    默认取本实例的 `self.silent`（设置页可改）。

        注意：**不要**再用 `--window-position=-32000,-32000` 那种"挪到屏幕外"的土办法 ——
        实测那种窗口 `visibilityState=hidden`，浏览器会直接丢弃点击与真实按键，
        表现就是"字填进了输入框却发不出去"。无头模式没有这个问题。
        """
        profile = _profile_dir()
        os.makedirs(profile, exist_ok=True)
        if silent is None:
            silent = bool(self.silent)
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
        ]
        if silent:
            # 无头后台：桌面不留窗口，可见性仍是 visible（发送不受影响）
            args += ["--headless=new", "--window-size=1280,900"]
        args.append(TARGET_URL)
        # startupinfo=None：**必须**覆盖 winproc 的 SW_HIDE，否则 Chrome 窗口会被隐藏
        proc = winproc.popen(args, creationflags=detached, close_fds=True,
                             startupinfo=None,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self._chrome_proc = proc
        self._chrome_owned = True
        self._headless = bool(silent)
        self.log("info", f"已拉起浏览器：{browser}（调试端口 {port}，"
                         f"{'后台无头' if silent else '显示窗口'}）")
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
        self._detect_mode()
        self._mask_headless_ua()
        return self._driver

    def _detect_mode(self):
        """探测当前浏览器是不是无头模式（也可能是别处拉起的，别只信自己的标记）。"""
        if self._driver is None or getattr(self._driver, "cdp", None) is None:
            return
        try:
            res = self._driver.cdp.call("Browser.getVersion", {}, timeout=6)
            ua = ((res.get("result") or {}).get("userAgent") or "")
            self._headless = "HeadlessChrome" in ua
        except Exception:
            pass

    def _mask_headless_ua(self):
        """无头模式：把 UA 里的 `HeadlessChrome` 换成 `Chrome`。

        无头 Chrome 的 UA 默认带 `HeadlessChrome/149.0.0.0` 标记，属于典型的
        自动化特征，长期用有被风控的风险。这里从 `Browser.getVersion` 取**真实** UA
        再替换标记（不硬编码版本号），通过 `Emulation.setUserAgentOverride` 覆盖。
        """
        if not self._headless or self._driver is None:
            return
        if getattr(self._driver, "cdp", None) is None:
            return
        try:
            res = self._driver.cdp.call("Browser.getVersion", {}, timeout=6)
            ua = ((res.get("result") or {}).get("userAgent") or "")
            if "HeadlessChrome" not in ua:
                return
            masked = ua.replace("HeadlessChrome", "Chrome")
            self._driver.cdp.call("Emulation.setUserAgentOverride",
                                  {"userAgent": masked}, timeout=6)
            self.log("info", "已覆盖无头 UA（去掉 HeadlessChrome 标记）")
        except Exception as exc:
            self.log("warn", f"覆盖无头 UA 失败（不影响功能）：{exc}")

    # ---------- 供界面调用 ----------
    def launch(self, silent=None):
        """用户点「启动/打开网页」：拉起（或复用）Chrome 并接入网页。

        v9.14.2 起统一走 `ensure_page()`，与"自动接入"共用同一条链路。
        silent: None = 跟随 config.WEB_SILENT_WINDOW（默认 True = 后台无头，桌面零窗口）；
                需要弹出来给用户看时，调 show_window()。
        """
        res = self.ensure_page(auto_launch=True, silent=silent)
        return {"ok": bool(res.get("ok")), "detail": res.get("detail") or ""}

    # ---------- 免手动：自动拉起 / 窗口显隐（v9.14.2）----------
    def ensure_page(self, auto_launch=True, silent=None):
        """确保网页可用：**已在线就接入，没在线就自己拉起浏览器**。

        这是 v9.14.2 新增的"免手动"入口 —— 用户不必再双击 .bat、也不必自己开 Chrome。
        登录态存在专用 profile 里（`%LOCALAPPDATA%\\AIWorkbench\\deepseek-profile`），
        一次登录长期有效，**不需要每次重新登录**。

        Args:
            auto_launch: 允许自动拉起浏览器（False 时只接入已在线网页）。
            silent: True = 后台无头（桌面零窗口）；False = 显示窗口。
                    None = 取 config.WEB_SILENT_WINDOW（默认 True）。

        Returns:
            dict(ok, detail, launched, need_login)
        """
        try:
            self.ensure_started()
        except Exception as exc:
            return {"ok": False, "detail": f"本机服务启动失败：{exc}",
                    "launched": False, "need_login": False}

        # ① 端口已在线 -> 直接接入（无论是本程序拉的还是用户手动开的）
        if self._port_online():
            if self._driver is None:
                try:
                    self._connect()
                except Exception as exc:
                    return {"ok": False, "detail": f"接入已在线网页失败：{exc}",
                            "launched": False, "need_login": False}
            self._refresh_once()
            kind, detail = self.cached_state()
            need_login = kind == errors.WebErrorKind.NOT_LOGGED_IN
            if need_login:
                # 要用户扫码/输验证码 -> 把窗口亮出来，不然他没法登
                self.show_window()
            elif kind == errors.WebErrorKind.WINDOW_HIDDEN:
                # 窗口被最小化/藏起来了 -> 恢复出来（不可见时发送 100% 失效）
                self.set_window_visible()
            elif kind == errors.WebErrorKind.PAGE_CHANGED:
                # 已在线但页面没就绪 -> 导航一次
                self._goto_home()
            return {"ok": True, "detail": detail, "launched": False,
                    "need_login": need_login}

        # ② 端口不在线 -> 需要自动拉起
        if not auto_launch:
            return {"ok": False,
                    "detail": errors.WEB_ERROR_TEXT[errors.WebErrorKind.NOT_STARTED],
                    "launched": False, "need_login": False}
        browser = self._find_browser()
        if not browser:
            return {"ok": False,
                    "detail": errors.WEB_ERROR_TEXT[errors.WebErrorKind.NOT_INSTALLED],
                    "launched": False, "need_login": False}
        try:
            self._start_chrome(browser, self._port, silent=silent)
        except Exception as exc:
            return {"ok": False, "detail": f"自动拉起浏览器失败：{exc}",
                    "launched": False, "need_login": False}
        if not self._wait_port(self._port, wait=25.0):
            return {"ok": False,
                    "detail": "浏览器调试端口 25 秒内未就绪（可能被安全软件拦截）。",
                    "launched": True, "need_login": False}
        try:
            self._connect()
        except Exception as exc:
            return {"ok": False, "detail": f"连接网页失败：{exc}",
                    "launched": True, "need_login": False}
        self._goto_home()
        self._refresh_once()
        kind, detail = self.cached_state()
        # 兜底：无头模式下拉起来的页面若没渲染好（老 Chrome 不支持 --headless=new 等），
        # 自动退回显示窗口模式，宁可多一个窗口也不能让用户发不出去。
        if self._headless and kind in (errors.WebErrorKind.PAGE_CHANGED,
                                       errors.WebErrorKind.OTHER,
                                       errors.WebErrorKind.NOT_STARTED):
            self.log("warn", f"无头模式页面未就绪（{kind}），退回显示窗口模式")
            if self._switch_mode(False):
                self._refresh_once()
                kind, detail = self.cached_state()
        need_login = kind == errors.WebErrorKind.NOT_LOGGED_IN
        if need_login:
            self._set_state(errors.WebErrorKind.NOT_LOGGED_IN)
            self.show_window()
            return {"ok": True,
                    "detail": "首次使用：请在刚打开的网页里登录一次 DeepSeek"
                              "（手机验证码 / 微信扫码都行）。登录一次后长期免登。",
                    "launched": True, "need_login": True}
        return {"ok": True, "detail": detail or "网页已就绪（已自动启动）",
                "launched": True, "need_login": False}

    def _goto_home(self):
        """导航到 DeepSeek 首页（失败只记日志）。"""
        if self._driver is None:
            return
        try:
            self._driver.open_page(TARGET_URL, wait=20.0)
        except Exception as exc:
            self.log("warn", f"打开网页时：{exc}")

    def _switch_mode(self, headless):
        """在「后台无头」与「显示窗口」之间切换（需重启浏览器）。

        登录态 cookie 存在专用 profile 里，重启**不会**掉登录。
        返回是否切换成功。
        """
        headless = bool(headless)
        if self._driver is None:
            return False
        if bool(self._headless) == headless:
            return True
        # 关掉当前实例（同一 profile 不能两开）
        try:
            self._driver.cdp.call("Browser.close", {}, timeout=5)
        except Exception:
            pass
        self._driver = None
        t0 = time.time()
        while time.time() - t0 < 12 and self._port_online():
            time.sleep(0.4)
        browser = self._find_browser()
        if not browser:
            self.log("warn", "切换浏览器模式失败：找不到浏览器")
            return False
        try:
            self._start_chrome(browser, self._port, silent=headless)
        except Exception as exc:
            self.log("warn", f"切换浏览器模式时拉起失败：{exc}")
            return False
        if not self._wait_port(self._port, wait=25.0):
            self.log("warn", "切换浏览器模式失败：端口未就绪")
            return False
        try:
            self._connect()
        except Exception as exc:
            self.log("warn", f"切换浏览器模式后重连失败：{exc}")
            return False
        self._goto_home()
        self.log("info", f"已切换到{'后台无头' if headless else '显示窗口'}模式")
        return True

    def _window_id(self):
        """取当前页面所在 Chrome 窗口的 windowId（失败返回 None）。"""
        if self._driver is None or getattr(self._driver, "cdp", None) is None:
            return None
        try:
            res = self._driver.cdp.call("Browser.getWindowForTarget", {}, timeout=6)
            return (res.get("result") or {}).get("windowId")
        except Exception:
            return None

    def show_window(self):
        """把网页窗口亮出来（用户要扫码 / 手动操作 / 想看页面时用）。

        无头后台模式下没有可显示的窗口 —— 此时会**重启成显示窗口模式**
        （登录态在专用 profile 里，不会掉登录）。
        """
        if self._headless:
            return self._switch_mode(False)
        wid = self._window_id()
        if wid is None:
            return False
        try:
            self._driver.cdp.call("Browser.setWindowBounds", {
                "windowId": wid, "bounds": {"windowState": "normal"},
            }, timeout=6)
            time.sleep(0.25)
            self._driver.cdp.call("Browser.setWindowBounds", {
                "windowId": wid,
                "bounds": {"left": 90, "top": 70, "width": 1200, "height": 860},
            }, timeout=6)
            try:
                self._driver.cdp.call("Page.bringToFront", {}, timeout=5)
            except Exception:
                pass
            self.log("info", "已把网页窗口挪回屏幕内")
            return True
        except Exception as exc:
            self.log("warn", f"显示网页窗口失败：{exc}")
            return False

    def set_window_visible(self):
        """恢复窗口可见性（Page.bringToFront + 取消最小化）。

        与 show_window 的区别：这里**保留用户原有的窗口大小/位置**，
        只做"取消最小化 + 提到前台"，用于自动修复"窗口不可见导致发送失效"。
        无头模式下本来就是 visible，直接返回 True。
        """
        if self._driver is None or getattr(self._driver, "cdp", None) is None:
            return False
        if self._headless:
            return True
        try:
            self._driver.cdp.call("Page.bringToFront", {}, timeout=6)
        except Exception:
            pass
        wid = self._window_id()
        if wid is None:
            return False
        try:
            self._driver.cdp.call("Browser.setWindowBounds", {
                "windowId": wid, "bounds": {"windowState": "normal"},
            }, timeout=6)
            self.log("info", "已把网页窗口从最小化恢复（不可见时发送会失效）")
            return True
        except Exception as exc:
            self.log("warn", f"恢复网页窗口失败：{exc}")
            return False

    def hide_window(self):
        """收走网页窗口，不占用户屏幕（程序仍照常驱动）。

        v9.14.2 起改为切到**后台无头模式**，而不是"最小化"——
        实测最小化的窗口 `visibilityState=hidden`，浏览器会丢弃发送动作；
        无头模式的 `visibilityState` 是 `visible`，发送照常且桌面零窗口。
        """
        if self._headless:
            return True
        if self._switch_mode(True):
            return True
        # 兜底：切不过去就退化成最小化（ensure_interactive 会在发送前再拉回来）
        wid = self._window_id()
        if wid is None:
            return False
        try:
            self._driver.cdp.call("Browser.setWindowBounds", {
                "windowId": wid, "bounds": {"windowState": "minimized"},
            }, timeout=6)
            self.log("info", "已把网页窗口最小化（兜底）")
            return True
        except Exception as exc:
            self.log("warn", f"隐藏网页窗口失败：{exc}")
            return False

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
