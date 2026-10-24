# -*- coding: utf-8 -*-
"""内嵌浏览器页（v9.18.0）：像 WorkBuddy 那样在界面里开个真浏览器，还能一键交给 AI 分析。

给用户看的：地址栏 + 后退 / 前进 / 刷新 + 「交给 AI 分析当前页」。
给模型用的：点「交给 AI 分析当前页」后，背后调 :func:`app.webengine.fetch_rendered`
把**渲染后的 DOM + 页面发出的全部请求**打包成 dict，通过 ``fetched`` 信号抛给主窗口，
主窗口再接给 agent（这部分由 main_window / agent 侧接线，本文件只负责发信号）。

⚠️ 导入时机（重要）：本模块在 **import 时**就 import ``app.webengine``，而后者会导入
QtWebEngine。QtWebEngine 必须在 ``QApplication`` **之前**导入（见 webengine 的踩坑记录），
所以主程序要在建 QApplication 前先 import 本模块（例如 pages.py 顶部 import，
而 pages 是在 main.py 建 app 之前被 import 的）。
若导入晚了也不会死：网页仍能渲染，只是这次进程不启用抓包。
"""
import os
import re

from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QSizePolicy,
)

from .. import webengine

# 命名 profile 的**强引用**：QWebEngineProfile 由 Python 侧持有更保险，
# 否则极端情况下（页面还没建好就触发 GC）profile 被回收会连带丢 cookie。
_PROFILE_HOLDER = []

# 判断"用户输入里是不是已经写了协议"，例如 http: / https: / javascript:
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

# 内网 / 本机地址：这些几乎都是 http 服务（路由器后台、学校内网、本地开发端口）
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")


def _is_private_host(host):
    h = (host or "").lower()
    if h in _LOCAL_HOSTS:
        return True
    if h.startswith(("127.", "10.", "192.168.", "169.254.")):
        return True
    # 172.16.0.0 ~ 172.31.255.255
    if h.startswith("172."):
        try:
            second = int(h.split(".")[1])
            return 16 <= second <= 31
        except Exception:
            return False
    return False


def normalize_url(text):
    """把用户输入变成可加载的 QUrl；非法/不支持返回 ``None``。

    · ``baidu.com``            → ``https://baidu.com``（默认优先 https）
    · ``localhost:8080/x``     → ``http://localhost:8080/x``（本机/内网走 http）
    · ``192.168.1.1``          → ``http://192.168.1.1``
    · ``https://a.com/?q=中文`` → 原样交给 QUrl（会做百分号编码）
    · ``javascript:alert(1)``  → None（只允许 http/https，挡掉脚本伪协议）
    """
    txt = str(text or "").strip()
    if not txt:
        return None
    if _SCHEME_RE.match(txt):
        qurl = QUrl.fromUserInput(txt)
    else:
        # 没写协议：先按"主机名"猜一个更合适的协议再交给 QUrl，
        # 不要直接用 fromUserInput —— 它对无协议输入一律补 http。
        head = txt.split("/")[0]
        host = head.split("?")[0].split("#")[0]
        if host.count(":") == 1:                      # host:port
            host = host.split(":")[0]
        scheme = "http" if _is_private_host(host) else "https"
        qurl = QUrl.fromUserInput(f"{scheme}://{txt}")
    if not qurl.isValid() or qurl.scheme().lower() not in ("http", "https"):
        return None
    if not qurl.host():
        return None
    return qurl


def _argv_ready():
    """当前进程的 QApplication 是否带着 argv[0]。

    ★ 实测（v9.19）：``QApplication([])`` 之后，只要一建 ``QWebEngineProfile`` /
    ``QWebEngineView``，Chromium 初始化就会**不抛异常、不打日志**地把整个进程打死
    （脚本里是 exit 127，测试框架里是 0xC0000409）。所以这里在动手之前先自查，
    宁可让浏览器页降级成"不可用"，也绝不把主程序一起带走。
    """
    try:
        from PyQt6.QtCore import QCoreApplication
        return len(QCoreApplication.arguments()) > 0
    except Exception:
        return True


def _make_profile():
    """给内嵌浏览器一个**持久化** profile（登录态能留住）。

    注意：QtWebEngine 的 ``QWebEngineProfile.defaultProfile()`` 在本机是
    off-the-record（不落盘），所以这里显式用命名 profile + 持久化目录。
    ★ 另外两条实测铁律（改之前先看 webengine.py 顶部踩坑记录）：
      ① 建 QApplication 时 **argv 不能是空列表**，否则一建 profile 就静默 exit 127；
      ② 建 profile 的动作必须在 GUI 线程里做（本函数由 ``_build_view`` 调，满足）。
    """
    from PyQt6.QtWebEngineCore import QWebEngineProfile
    base = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "AIWorkbench", "webengine", "browser")
    cache = os.path.join(base, "cache")
    for d in (base, cache):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    prof = QWebEngineProfile("aiworkbench-browser")
    try:
        prof.setPersistentStoragePath(base)
        prof.setCachePath(cache)
        prof.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        try:
            prof.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        except Exception:
            pass
        # 去掉 UA 里的 QtWebEngine 指纹，减少站点差异
        ua = prof.httpUserAgent() or ""
        ua2 = re.sub(r"\s*QtWebEngine/[\d.]+", "", ua).strip()
        if ua2:
            prof.setHttpUserAgent(ua2)
    except Exception:
        pass
    _PROFILE_HOLDER.append(prof)
    return prof


# ---------------------------------------------------------------------------
# 后台抓取线程：fetch_rendered 内部要跑 QEventLoop，不能占着 GUI 线程
# ---------------------------------------------------------------------------
class _FetchWorker(QThread):
    """在后台线程里调 fetch_rendered，免得点按钮时界面卡住。"""

    done = pyqtSignal(dict)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            res = webengine.fetch_rendered(self._url, wait_ms=1500, timeout=25)
        except Exception as e:                          # 兜底：绝不把线程搞崩
            res = {"url": self._url, "final_url": self._url, "status": 0,
                   "title": "", "html": "", "text": "", "requests": [],
                   "xhr": [], "engine": "urllib",
                   "warn": f"抓取失败：{type(e).__name__}: {e}"}
        self.done.emit(res)


# ---------------------------------------------------------------------------
# 内嵌浏览器面板
# ---------------------------------------------------------------------------
class BrowserPane(QWidget):
    """地址栏 + 导航按钮 + 内嵌浏览器 + 「交给 AI 分析当前页」。"""

    fetched = pyqtSignal(dict)      # 把 fetch_rendered 的结果抛出去

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._view = None
        self._argv_ok = _argv_ready()
        self._available = webengine.engine_available() and self._argv_ok

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # ---- 工具栏：后退 / 前进 / 刷新 / 地址栏 / 交给 AI ----
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_back = QPushButton("←")
        self.btn_fwd = QPushButton("→")
        self.btn_home = QPushButton("刷新")
        for b in (self.btn_back, self.btn_fwd, self.btn_home):
            b.setFixedWidth(44 if b is not self.btn_home else 56)
            bar.addWidget(b)

        self.addr = QLineEdit()
        self.addr.setPlaceholderText("输入网址，例如 https://example.com，回车打开")
        self.addr.setClearButtonEnabled(True)
        bar.addWidget(self.addr, 1)

        self.btn_ai = QPushButton("交给 AI 分析当前页")
        self.btn_ai.setToolTip("抓取当前页渲染后的内容与它发出的网络请求，交给 AI 分析")
        bar.addWidget(self.btn_ai)

        # ⚠️ 本仓库踩过的坑：控件还没父窗口时 setVisible(True) 等价于 show()，
        #    会当场弹出一个真实顶层窗口。这里一律「先 addLayout/addWidget，后设置」。
        root.addLayout(bar)

        self.status = QLabel("就绪")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        if self._available:
            self._build_view(root)
            self._wire()
        elif not self._argv_ok:
            tip = QLabel(
                "浏览器内核无法在本进程启动：应用启动参数不完整。\n\n"
                "正常情况下不会出现这个提示；如果你是在脚本里嵌了本页面，\n"
                "请把 QApplication 建成 QApplication(sys.argv) 而不是 QApplication([])。\n\n"
                "（地址栏与按钮仍在，只是不加载页面。）")
            tip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            tip.setWordWrap(True)
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            root.addWidget(tip, 1)
        else:
            tip = QLabel(
                "未安装浏览器内核，无法内嵌网页。\n\n"
                "请在命令行执行以下命令安装后重启应用：\n"
                "    python -m pip install PyQt6-WebEngine\n\n"
                "（地址栏与按钮仍在，只是不加载页面。）")
            tip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            tip.setWordWrap(True)
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            tip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            root.addWidget(tip, 1)

    # -- 内部装配 ---------------------------------------------------------
    def _build_view(self, root):
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEnginePage

        class _SameWindowPage(QWebEnginePage):
            """target=_blank / window.open 时在**当前视图**里打开。

            默认 QWebEnginePage 不实现 createWindow，点击这类链接会
            **什么都不发生**（用户以为页面卡死）；返回 self 最稳。
            """
            def createWindow(self, _type):      # noqa: N802
                return self

        self._profile = _make_profile()
        self._view = QWebEngineView()
        self._view.setPage(_SameWindowPage(self._profile, self._view))
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        # 先 addWidget 拿到父窗口，再谈可见性（见上面的坑）
        root.addWidget(self._view, 1)
        # 页面内点下载链接 → 选保存位置（离屏测试直接落 Downloads）
        try:
            self._profile.downloadRequested.connect(self._on_download)
        except Exception:
            pass

    def _wire(self):
        self.addr.returnPressed.connect(lambda: self.load(self.addr.text()))
        self.btn_back.clicked.connect(self._view.back)
        self.btn_fwd.clicked.connect(self._view.forward)
        self.btn_home.clicked.connect(self._view.reload)
        self.btn_ai.clicked.connect(self.analyze_current)
        self._view.urlChanged.connect(self._on_url_changed)
        self._view.loadStarted.connect(lambda: self.status.setText("加载中…"))
        self._view.loadProgress.connect(self._on_progress)
        self._view.loadFinished.connect(self._on_load_finished)
        self._view.renderProcessTerminated.connect(self._on_render_terminated)
        self._view.titleChanged.connect(self._on_title)
        # 初始没有历史，前进/后退应禁用
        self.btn_back.setEnabled(False)
        self.btn_fwd.setEnabled(False)

    # -- 对外 API ---------------------------------------------------------
    def load(self, url):
        """让内嵌浏览器打开某个网址（自动补协议、校验合法性）。"""
        qurl = normalize_url(url)
        if qurl is None:
            self.status.setText("网址无效：请输入 http:// 或 https:// 开头的地址。")
            return
        self.addr.setText(qurl.toString())
        if self._view is not None:
            self.status.setText("加载中…")
            self._view.load(qurl)
        else:
            self.status.setText("未安装浏览器内核，无法加载页面。")

    def current_url(self):
        """当前地址（优先取浏览器实际地址，其次取地址栏文本）。"""
        if self._view is not None:
            u = self._view.url().toString()
            if u:
                return u
        return self.addr.text().strip()

    def analyze_current(self):
        """抓取当前页并交给 AI（结果走 ``fetched`` 信号）。

        ★ 改成直接读**当前视图已渲染的 DOM**（``page().toHtml``），
        不再另起离屏视图重新加载：否则会丢掉当前登录态/页面状态，
        而且慢得多。
        """
        url = self.current_url()
        if not url or url.startswith("about:"):
            self.status.setText("还没有打开任何页面，先在地址栏输入网址。")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("上一次分析还在进行中，请稍候…")
            return
        if self._view is None:
            self.status.setText("未安装浏览器内核，无法读取页面。")
            return
        self.btn_ai.setEnabled(False)
        self.status.setText("正在读取当前页面的渲染内容…")
        try:
            self._view.page().toHtml(self._on_html_ready)
        except Exception as e:
            self.btn_ai.setEnabled(True)
            self.status.setText(f"读取页面失败：{e}")

    def _on_html_ready(self, html):
        self.btn_ai.setEnabled(True)
        url = self.current_url()
        if not html:
            self.status.setText(
                "读取当前页面失败（页面可能还没加载完），请稍后重试。")
            return
        title = ""
        try:
            title = self._view.title() if self._view else ""
        except Exception:
            pass
        res = {
            "url": url,
            "final_url": url,
            "status": 200,
            "title": title,
            "html": html,
            "text": webengine.extract_text(html),
            "requests": [],
            "xhr": [],
            "engine": "embedded",
            "warn": "来自当前页面已渲染的 DOM（未重新加载、未抓包）",
        }
        self._on_fetched(res)

    # -- 内部回调 ---------------------------------------------------------
    def _on_url_changed(self, qurl):
        u = qurl.toString()
        if u and u != self.addr.text():
            self.addr.setText(u)
        self._sync_nav()

    def _on_progress(self, p):
        if 0 <= p < 100:
            self.status.setText(f"加载中… {p}%")

    def _on_title(self, title):
        if title:
            self.setWindowTitle(title)

    def _on_load_finished(self, ok):
        self._sync_nav()
        if ok:
            u = self.current_url()
            self.status.setText(f"加载完成：{u}" if u else "加载完成。")
        else:
            self.status.setText(
                "加载失败（网络或内核问题）。可点「刷新」重试，或检查网址。")

    def _on_render_terminated(self, _status, _code):
        self.status.setText(
            "页面渲染进程异常退出（可能内存不足或内核问题），点「刷新」重试。")

    def _sync_nav(self):
        """前进/后退按钮是否可用，跟着实际浏览历史走。"""
        if self._view is None:
            return
        try:
            h = self._view.history()
            self.btn_back.setEnabled(bool(h.canGoBack()))
            self.btn_fwd.setEnabled(bool(h.canGoForward()))
        except Exception:
            pass

    def _on_download(self, item):
        """页面内点下载链接时，选保存位置并开始下载。"""
        try:
            suggested = item.suggestedFileName() or "download.bin"
        except Exception:
            suggested = "download.bin"
        base = os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        target = os.path.join(base, suggested)
        # 离屏（自动化测试）没有对话框可用，直接落到 Downloads
        if os.environ.get("QT_QPA_PLATFORM", "") != "offscreen":
            from PyQt6.QtWidgets import QFileDialog
            target, _filter = QFileDialog.getSaveFileName(self, "保存文件", target)
            if not target:
                item.cancel()
                return
        item.setPath(target)
        item.accept()
        self.status.setText(f"开始下载：{target}")
        item.downloadProgress.connect(
            lambda got, total: self.status.setText(
                f"下载中… {got}/{total} 字节" if total else f"下载中… {got} 字节"))
        item.finished.connect(lambda: self.status.setText(f"下载完成：{target}"))

    def _on_fetched(self, res):
        n = len(res.get("requests") or [])
        nx = len(res.get("xhr") or [])
        txt = res.get("text") or ""
        self.status.setText(
            f"抓取完成：正文 {len(txt)} 字，抓到 {n} 个请求（其中疑似接口 {nx} 个）。"
            + (f" 提示：{res.get('warn')}" if res.get("warn") else ""))
        self.fetched.emit(res)
