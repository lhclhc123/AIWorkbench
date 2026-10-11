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
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QSizePolicy,
)

from .. import webengine


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
        self._available = webengine.engine_available()

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
        self._view = QWebEngineView()
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        # 先 addWidget 拿到父窗口，再谈可见性（见上面的坑）
        root.addWidget(self._view, 1)

    def _wire(self):
        self.addr.returnPressed.connect(lambda: self.load(self.addr.text()))
        self.btn_back.clicked.connect(self._view.back)
        self.btn_fwd.clicked.connect(self._view.forward)
        self.btn_home.clicked.connect(self._view.reload)
        self.btn_ai.clicked.connect(self.analyze_current)
        self._view.urlChanged.connect(self._on_url_changed)
        self._view.loadStarted.connect(lambda: self.status.setText("加载中…"))
        self._view.loadFinished.connect(self._on_load_finished)

    # -- 对外 API ---------------------------------------------------------
    def load(self, url):
        """让内嵌浏览器打开某个网址。"""
        url = str(url or "").strip()
        if not url:
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        self.addr.setText(url)
        if self._view is not None:
            self._view.load(QUrl(url))
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
        """抓取当前页并交给 AI（结果走 ``fetched`` 信号）。"""
        url = self.current_url()
        if not url:
            self.status.setText("还没有打开任何页面，先在地址栏输入网址。")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("上一次分析还在进行中，请稍候…")
            return
        self.btn_ai.setEnabled(False)
        self.status.setText(f"正在抓取 {url} 的渲染内容与网络请求…")
        self._worker = _FetchWorker(url, self)
        self._worker.done.connect(self._on_fetched)
        self._worker.finished.connect(lambda: self.btn_ai.setEnabled(True))
        self._worker.start()

    # -- 内部回调 ---------------------------------------------------------
    def _on_url_changed(self, qurl):
        u = qurl.toString()
        if u and u != self.addr.text():
            self.addr.setText(u)

    def _on_load_finished(self, ok):
        self.status.setText("加载完成。" if ok else "加载失败（网络或内核问题）。")

    def _on_fetched(self, res):
        n = len(res.get("requests") or [])
        nx = len(res.get("xhr") or [])
        txt = res.get("text") or ""
        self.status.setText(
            f"抓取完成：正文 {len(txt)} 字，抓到 {n} 个请求（其中疑似接口 {nx} 个）。"
            + (f" 提示：{res.get('warn')}" if res.get("warn") else ""))
        self.fetched.emit(res)
