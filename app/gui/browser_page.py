# -*- coding: utf-8 -*-
"""内嵌浏览器（v9.22.10 重构：从「一个工具页」改成「像真浏览器的一栏」）。

用户原话：
  「将浏览器改为类似参考图右侧那种内嵌形式的样式……将浏览器内现有的刷新、前进、
    后退等按钮全部替换为更美观的 Web UI 风格控件，使其整体看起来像一个真正的浏览器，
    并补齐真实浏览器应有的其他必要元素（如地址栏、标签页、菜单等）」

## 这一版把什么换掉了

v9.18.0 的第一版是「一条 QPushButton 横排 + 一个大 QLineEdit + 一个彩色文字按钮」，
放在左侧导航里当一整页用。问题：
  · 按钮是 QPushButton 默认样子（蓝底、凸起、44px 宽），跟浏览器毫不相干；
  · 「交给 AI 分析当前页」是一个大按钮，把浏览器的工具行挤得很怪；
  · 没有标签页、没有菜单、没有地址栏里的图标，本质是「输入框 + 网页」；
  · 它是一整页，打开就把对话页顶掉了 —— 用户要的是「边聊边看」。

所以本文件现在是一套**浏览器外壳（chrome）**：

    细标签条  [标签 ×] [＋]                                  [✕ 收起]
    工具行    ← → ↻ ⌂  [◎ 圆角地址栏                    ]  ⋯
    内容区    <QWebEngineView>（多标签各自一个 view）
    状态行    一行小灰字（加载进度 / 抓取结果）

设计约束（都在测试里守着）：
  · 图标一律单色线性字形：**优先取 app/icons.py 的 ic()**，icons.py 里没有的
    浏览器专用字形在本文件 `_GLYPH` 里兜底传入 `ic(name, default)`（不走 emoji，
    全是 Segoe UI Symbol 的文字呈现码位，见 icons.py 顶部的铁律）。
  · 所有颜色来自 `app.themes.tokens()`，深浅色都自动跟随，不写死黑白。
  · 顺带保留 v9.18.0 的全部既有能力与 API：`load` / `current_url` /
    `analyze_current` / `fetched` 信号 / `_on_download` / 内网拦截 `normalize_url` /
    独立持久化 profile（登录态落盘）。

⚠️ 导入时机（重要，别动）：本模块在 **import 时**就 import ``app.webengine``，
而后者会导入 QtWebEngine。QtWebEngine 必须在 ``QApplication`` **之前**导入
（见 webengine.py 的踩坑记录），所以主程序要在建 QApplication 前先 import 本模块
（pages.py 顶部 import，而 pages 是在 main.py 建 app 之前被 import 的）。
"""
import os
import re

from PyQt6.QtCore import Qt, QUrl, QThread, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import themes, webengine
from ..icons import ic

# 命名 profile 的**强引用**：QWebEngineProfile 由 Python 侧持有更保险，
# 否则极端情况下（页面还没建好就触发 GC）profile 被回收会连带丢 cookie。
_PROFILE_HOLDER = []

# 判断"用户输入里是不是已经写了协议"，例如 http: / https: / javascript:
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")

# 内网 / 本机地址：这些几乎都是 http 服务（路由器后台、学校内网、本地开发端口）
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]")

# 首页（点 ⌂ 回这里）
HOME_URL = "https://www.bing.com"

# 搜索引擎（地址栏里输入的不是网址时走这里）
SEARCH_URL = "https://www.bing.com/search?q={q}"

# 标签页标题最多显示这么多字（超出省略号）；真正的省略由 QFontMetrics 按像素做，
# 这里只是给"还没量到宽度"时的一个上限。
TAB_TITLE_MAX_CHARS = 28

# ---------------------------------------------------------------------------
# 浏览器专用字形
#
# ★ 为什么不直接写进 app/icons.py：那张表是「界面图标」的单一来源，本轮改造的
#   文件白名单里没有它。这里全部通过 `ic(name, default)` 的**既有兜底参数**取，
#   等 icons.py 下一次收编时，只要在 ICON 里加同名条目，本文件一行都不用改
#   （`ic("back", ...)` 会优先返回表里的值）。
#
# 选字形仍然遵守 icons.py 顶部的铁律：
#   · 只用**文字呈现**（Emoji_Presentation=No）的码位，渲染出来是单色线条；
#   · ←  U+2190 / → U+2192 是普通文字箭头（不是彩色 emoji 的 U+2B05/U+27A1）；
#   · ⟳  U+27F3 / ✕ U+2715 / ⚠ U+26A0 / ⚿ U+26BF / ⊙ U+2299 已在生产环境验证过；
#   · ⌂  U+2302（房屋）、⋯ U+22EF（横向省略号）走 Segoe UI Symbol，测试里逐像素验。
# ---------------------------------------------------------------------------
_GLYPH = {
    "back": "\u2190",       # ←  后退
    "forward": "\u2192",    # →  前进
    "reload": "\u27F3",     # ⟳  刷新
    # ★ 名字用 "cancel" 而不是 "stop"：`glyph()` 优先取 app/icons.py 的正式表，
    #   而表里的 "stop" 是 ⏹（方形停止键，聊天区的「停止生成」在用）。
    #   浏览器加载中的按钮是 ✕（跟 Chrome/Edge 一致），所以换一个不冲突的名字。
    "cancel": "\u2715",     # ✕  停止加载（加载中替换 ⟳）
    "home": "\u2302",       # ⌂  回到首页
    "menu": "\u22EF",       # ⋯  菜单
    "newtab": "+",          # +  新建标签
    "closetab": "\u2715",   # ✕  关闭标签
    "hide": "\u2715",       # ✕  收起面板
}


def glyph(name):
    """取一个浏览器字形：**优先 app/icons.py 的正式表**，缺的用本文件的兜底。

    这样图标仍然只有一个"出口"（`ic()`），测试也仍然按 `ic()` 的语义校验。
    """
    return ic(name, _GLYPH.get(name, ic("dot")))


def _glyph_pixmap(text, color, px=14):
    """把一个文字字形画成单色 QPixmap（地址栏左侧的小图标用）。"""
    size = max(8, int(px))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = QFont("Segoe UI Symbol")
        font.setPixelSize(max(9, size - 2))
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
    finally:
        painter.end()
    return pm


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


def looks_like_url(text):
    """地址栏里的这串东西，像不像一个网址（不像就走搜索）。

    判据刻意保守：有协议头，或者「没有空格且含点 / 是 localhost / 是 IP」。
    这样 ``deepseek v3 价格`` 会去搜索，``baidu.com`` 会直接打开。
    """
    txt = str(text or "").strip()
    if not txt:
        return False
    if _SCHEME_RE.match(txt):
        return normalize_url(txt) is not None
    if " " in txt or "\t" in txt:
        return False
    head = txt.split("/")[0].split("?")[0].split("#")[0]
    host = head.split(":")[0]
    if host in _LOCAL_HOSTS or re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
        return True
    # 必须有"点"，且点两边都有东西（挡掉 "v3.2" / "1.5" 这类版本号）
    return bool(re.fullmatch(r"[^\s.]+\.[^\s.]{2,}", host))


def search_url(text):
    """把一段文字变成搜索 URL（百分号编码，中文没问题）。"""
    q = bytes(QUrl.toPercentEncoding(str(text or "").strip())).decode("ascii")
    return QUrl(SEARCH_URL.format(q=q))


def _argv_ready():
    """当前进程的 QApplication 是否带着 argv[0]。

    ★ 实测（v9.19）：``QApplication([])`` 之后，只要一建 ``QWebEngineProfile`` /
    ``QWebEngineView``，Chromium 初始化就会**不抛异常、不打日志**地把整个进程打死
    （脚本里是 exit 127，测试框架里是 0xC0000409）。所以这里在动手之前先自查，
    宁可让浏览器降级成"不可用"，也绝不把主程序一起带走。
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
      ② 建 profile 的动作必须在 GUI 线程里做（本函数由 ``_make_view`` 调，满足）。

    v9.22.10：**所有标签页共用这一个 profile** —— 这样「A 标签登录了，B 标签也是
    登录态」，才像真浏览器；同时登录态在标签页之间、重启之后都还在。
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
# 小组件：圆角线性图标按钮 / 标签页胶囊
# ---------------------------------------------------------------------------
class _ChromeButton(QPushButton):
    """28×28 圆角线性图标按钮（Web UI 风格）。

    刻意**不用** QToolButton：QToolButton 的默认样式在 Fusion 下带凸起边框和
    hover 高亮，跟浏览器外壳格格不入。这里用 QPushButton + objectName 走 QSS，
    三态（hover / pressed / disabled）都在 themes.py 里定义。
    """

    def __init__(self, text, tooltip="", parent=None, size=28,
                 object_name="BrowserBtn"):
        super().__init__(str(text or ""), parent)
        self.setObjectName(object_name)
        self.setFixedSize(QSize(int(size), int(size)))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        if tooltip:
            self.setToolTip(tooltip)

    def set_glyph(self, text, tooltip=None):
        """换字形（刷新 ↔ 停止），不动按钮尺寸/样式。"""
        self.setText(str(text or ""))
        if tooltip is not None:
            self.setToolTip(tooltip)


class _TabChip(QFrame):
    """标签条上的一个标签：标题（过长省略）+ ✕ 关闭。

    选中态用 QSS 的 ``[selected="true"]`` 属性选择器驱动 —— 这样主题一切换，
    配色自动跟着走，不需要在 Python 里记两套颜色。
    """

    activated = pyqtSignal(object)      # 参数是 chip 自己
    close_clicked = pyqtSignal(object)  # 参数是 chip 自己

    def __init__(self, title="新标签页", parent=None):
        super().__init__(parent)
        self.setObjectName("BrowserTab")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.setMinimumWidth(96)
        self.setMaximumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._title = str(title or "新标签页")

        h = QHBoxLayout(self)
        h.setContentsMargins(9, 0, 3, 0)
        h.setSpacing(2)

        self.label = QLabel(self._title)
        self.label.setObjectName("BrowserTabTitle")
        # 标题不接鼠标事件，点击落到 chip 上（整块都能点，跟真浏览器一致）
        self.label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        h.addWidget(self.label, 1)

        self.btn_close = _ChromeButton(
            glyph("closetab"), "关闭标签页", self, size=18,
            object_name="BrowserTabClose")
        self.btn_close.clicked.connect(lambda: self.close_clicked.emit(self))
        h.addWidget(self.btn_close, 0)

        self.refresh_theme()

    # -- 外观 -----------------------------------------------------------
    def set_selected(self, on):
        """选中态：QSS 管底色，标题文字颜色在这里按主题 token 直接设。"""
        self.setProperty("selected", bool(on))
        try:
            self.style().unpolish(self)
            self.style().polish(self)
        except Exception:
            pass
        t = themes.tokens()
        color = t["nav_active_text"] if on else t["text_muted"]
        weight = "bold" if on else "normal"
        self.label.setStyleSheet(
            f"color:{color};font-size:12px;font-weight:{weight};"
            "background:transparent;padding:0px;")
        self.update()

    def set_title(self, title):
        self._title = str(title or "").strip() or "新标签页"
        self._elide()

    def title(self):
        return self._title

    def refresh_theme(self):
        self.set_selected(bool(self.property("selected")))

    def _elide(self):
        """按像素宽度省略标题（中文按字宽算，比按字符数截准）。"""
        avail = max(40, self.width() - 40)
        text = self._title
        if len(text) > TAB_TITLE_MAX_CHARS:
            text = text[:TAB_TITLE_MAX_CHARS] + "\u2026"
        try:
            fm = self.label.fontMetrics()
            self.label.setText(fm.elidedText(
                text, Qt.TextElideMode.ElideRight, avail))
        except Exception:
            self.label.setText(text)
        self.label.setToolTip(self._title)

    def resizeEvent(self, ev):                        # noqa: N802（Qt 回调）
        super().resizeEvent(ev)
        self._elide()

    def mousePressEvent(self, ev):                    # noqa: N802（Qt 回调）
        if ev.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self)
        super().mousePressEvent(ev)


class _Tab(object):
    """一个标签页：一个独立 QWebEngineView + 它的 chip。"""

    __slots__ = ("chip", "view")

    def __init__(self, chip, view):
        self.chip = chip
        self.view = view


# ---------------------------------------------------------------------------
# 内嵌浏览器面板
# ---------------------------------------------------------------------------
class BrowserPane(QWidget):
    """细标签条 + ← → ↻ ⌂ + 圆角地址栏 + ⋯ 菜单 + 内嵌 Chromium。

    对外 API（v9.18.0 起就有，别改签名）：
      · ``load(url)``            打开一个网址（自动补协议、校验合法性）
      · ``current_url()``        当前地址
      · ``analyze_current()``    读当前页已渲染的 DOM，结果走 ``fetched`` 信号
      · ``fetched`` / ``hide_requested``  信号
    """

    fetched = pyqtSignal(dict)      # 把「交给 AI」的结果抛出去
    hide_requested = pyqtSignal()   # 点右上角 ✕：收起整个右侧面板（不销毁）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BrowserPane")

        self._worker = None
        self._view = None            # 当前活动标签的 view（老 API，保留）
        self._tabs = []
        self._active = -1
        self._devtools = None
        self._loading = False
        self._zoom = 1.0
        self._argv_ok = _argv_ready()
        self._available = webengine.engine_available() and self._argv_ok
        self._profile = None
        self._addr_action = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ⚠️ 本仓库踩过的坑：控件还没父窗口时 setVisible(True) 等价于 show()，
        #    会当场弹出一个真实顶层窗口。这里全程「先 addWidget/addLayout，
        #    后设置」，任何地方都不调 setVisible(True)。
        self._build_tab_strip(root)
        self._build_toolbar(root)
        self._build_body(root)
        self._wire_chrome()

        if self._available:
            self.new_tab(None, activate=True)
        else:
            self._show_engine_tip()

    # ================= 外壳：标签条 / 工具行 / 内容区 =================
    def _build_tab_strip(self, root):
        """细标签条：左边一串标签 + ＋，右边「收起面板」。"""
        strip = QWidget()
        strip.setObjectName("BrowserTabStrip")
        strip.setFixedHeight(34)
        h = QHBoxLayout(strip)
        h.setContentsMargins(8, 4, 8, 0)
        h.setSpacing(4)

        # 标签容器：chips 动态加到这个 layout 里（左侧，不拉伸）
        self._tabs_host = QWidget()
        self._tabs_host.setObjectName("BrowserTabsHost")
        self._tabs_layout = QHBoxLayout(self._tabs_host)
        self._tabs_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_layout.setSpacing(3)
        h.addWidget(self._tabs_host, 0)

        self.btn_new_tab = _ChromeButton(
            glyph("newtab"), "新建标签页", strip, size=24,
            object_name="BrowserNewTab")
        h.addWidget(self.btn_new_tab, 0)
        h.addStretch(1)

        self.btn_hide = _ChromeButton(
            glyph("hide"), "收起浏览器面板（网页不关，登录态保留）", strip,
            size=24, object_name="BrowserNewTab")
        h.addWidget(self.btn_hide, 0)

        self.tab_strip = strip
        root.addWidget(strip)

    def _build_toolbar(self, root):
        """工具行：← → ↻ ⌂ + 圆角地址栏 + ⋯ 菜单。"""
        bar = QWidget()
        bar.setObjectName("BrowserToolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 6)
        h.setSpacing(2)

        self.btn_back = _ChromeButton(glyph("back"), "后退", bar)
        self.btn_fwd = _ChromeButton(glyph("forward"), "前进", bar)
        self.btn_reload = _ChromeButton(glyph("reload"), "刷新", bar)
        self.btn_home = _ChromeButton(glyph("home"), "回到首页", bar)
        for b in (self.btn_back, self.btn_fwd, self.btn_reload, self.btn_home):
            h.addWidget(b, 0)

        h.addSpacing(4)
        self.addr = QLineEdit()
        self.addr.setObjectName("BrowserAddr")
        self.addr.setPlaceholderText("搜索或输入网址")
        self.addr.setClearButtonEnabled(True)
        self.addr.setMinimumHeight(28)
        self.addr.setMaximumHeight(28)
        self.addr.setCursor(Qt.CursorShape.IBeamCursor)
        # 地址栏左侧的小图标（安全 / 不安全 / 放大镜）——用 QLineEdit 的
        # LeadingPosition action 放进去，天然在圆角框内部，不用额外套一层 Frame。
        try:
            self._addr_action = self.addr.addAction(
                QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        except Exception:
            self._addr_action = None
        h.addWidget(self.addr, 1)
        h.addSpacing(4)

        self.btn_menu = _ChromeButton(glyph("menu"), "菜单", bar)
        h.addWidget(self.btn_menu, 0)

        self.toolbar = bar
        root.addWidget(bar)

        # ---- ⋯ 菜单 ----
        self.menu = QMenu(self)
        self.menu.setObjectName("BrowserMenu")
        self.act_open_window = self.menu.addAction("在新窗口打开")
        self.act_copy_link = self.menu.addAction("复制链接")
        self.menu.addSeparator()
        self.act_reload = self.menu.addAction("刷新")
        self.act_home = self.menu.addAction("回到首页")
        self.menu.addSeparator()
        self.act_clear = self.menu.addAction("清除本站数据")
        self.act_devtools = self.menu.addAction("开发者工具")
        self._menu_zoom = self.menu.addMenu("缩放")
        self.act_zoom_in = self._menu_zoom.addAction("放大")
        self.act_zoom_out = self._menu_zoom.addAction("缩小")
        self.act_zoom_reset = self._menu_zoom.addAction("重置为 100%")
        self.menu.addSeparator()
        # ★ 原有能力必须保留：分析当前页面（v9.18.0 的「交给 AI」）
        self.act_analyze = self.menu.addAction("分析当前页面（交给 AI）")

    def _build_body(self, root):
        """内容区：多标签各自一个 view，用 QStackedWidget 切。"""
        self._stack = QStackedWidget()
        self._stack.setObjectName("BrowserViewStack")
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)
        root.addWidget(self._stack, 1)

        self.status = QLabel("就绪")
        self.status.setObjectName("BrowserStatus")
        self.status.setWordWrap(False)
        self.status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.status, 0)

        self._update_addr_icon()

    def _show_engine_tip(self):
        """内核不可用时的体面降级：说清楚原因和怎么装，按钮仍在但不加载。"""
        if not self._argv_ok:
            text = (
                "浏览器内核无法在本进程启动：应用启动参数不完整。\n\n"
                "正常情况下不会出现这个提示；如果你是在脚本里嵌了本页面，\n"
                "请把 QApplication 建成 QApplication(sys.argv) 而不是 QApplication([])。\n\n"
                "（地址栏与按钮仍在，只是不加载页面。）")
        else:
            text = (
                "未安装浏览器内核，无法内嵌网页。\n\n"
                "请在命令行执行以下命令安装后重启应用：\n"
                "    python -m pip install PyQt6-WebEngine\n\n"
                "（地址栏与按钮仍在，只是不加载页面。）")
        tip = QLabel(text)
        tip.setObjectName("BrowserTip")
        tip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        tip.setStyleSheet(
            f"color:{themes.tokens()['text_muted']};font-size:12px;padding:24px;")
        # 先入库拿父窗口，再谈别的（绝不在这里 setVisible(True)）
        self._stack.addWidget(tip)
        self._stack.setCurrentWidget(tip)
        self.btn_back.setEnabled(False)
        self.btn_fwd.setEnabled(False)

    # ================= 接线 =================
    def _wire_chrome(self):
        self.btn_back.clicked.connect(self.go_back)
        self.btn_fwd.clicked.connect(self.go_forward)
        self.btn_reload.clicked.connect(self.reload_or_stop)
        self.btn_home.clicked.connect(self.go_home)
        self.btn_new_tab.clicked.connect(lambda: self.new_tab(None, True))
        self.btn_hide.clicked.connect(self.hide_requested.emit)
        self.btn_menu.clicked.connect(self._show_menu)

        self.addr.returnPressed.connect(self._on_addr_enter)

        self.act_open_window.triggered.connect(self.open_in_new_window)
        self.act_copy_link.triggered.connect(self.copy_link)
        self.act_reload.triggered.connect(self.reload_or_stop)
        self.act_home.triggered.connect(self.go_home)
        self.act_clear.triggered.connect(self.clear_site_data)
        self.act_devtools.triggered.connect(self.open_devtools)
        self.act_zoom_in.triggered.connect(lambda: self.set_zoom(self._zoom + 0.1))
        self.act_zoom_out.triggered.connect(lambda: self.set_zoom(self._zoom - 0.1))
        self.act_zoom_reset.triggered.connect(lambda: self.set_zoom(1.0))
        self.act_analyze.triggered.connect(self.analyze_current)

        self._set_reload_state(False)

    # ================= 标签页 =================
    def _make_view(self):
        """建一个 QWebEngineView（所有标签共用同一个持久化 profile）。"""
        from PyQt6.QtWebEngineCore import QWebEnginePage
        from PyQt6.QtWebEngineWidgets import QWebEngineView

        pane = self

        class _SameWindowPage(QWebEnginePage):
            """target=_blank / window.open 时在**当前视图**里打开。

            默认 QWebEnginePage 不实现 createWindow，点击这类链接会
            **什么都不发生**（用户以为页面卡死）；返回 self 最稳
            （v9.19 起的行为，回归用例 ``probe_browser_download_v919`` 守着）。
            """

            def createWindow(self, _type):     # noqa: N802
                return self

        if self._profile is None:
            self._profile = _make_profile()
            try:
                self._profile.downloadRequested.connect(pane._on_download)
            except Exception:
                pass

        view = QWebEngineView()
        view.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        view.setPage(_SameWindowPage(self._profile, view))
        # 网页里的右键菜单交还给 Chromium 自带的那套（复制 / 检查元素等）
        try:
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        except Exception:
            pass
        return view

    def new_tab(self, url=None, activate=True):
        """新建标签页并返回它的 view；内核不可用时返回 None。"""
        if not self._available:
            self.status.setText("未安装浏览器内核，无法新建标签页。")
            return None
        try:
            view = self._make_view()
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"浏览器内核启动失败：{type(e).__name__}: {e}")
            return None

        chip = _TabChip("新标签页", self._tabs_host)
        chip.activated.connect(self._on_chip_activated)
        chip.close_clicked.connect(self._on_chip_close)
        self._tabs_layout.addWidget(chip)

        tab = _Tab(chip=chip, view=view)
        self._tabs.append(tab)
        self._stack.addWidget(view)
        self._wire_view(view)

        if url:
            qurl = normalize_url(url)
            if qurl is not None:
                view.load(qurl)
        if activate or self._active < 0:
            self.activate_tab(len(self._tabs) - 1)
        return view

    def _wire_view(self, view):
        view.urlChanged.connect(
            lambda qurl, v=view: self._on_url_changed(qurl, v))
        view.titleChanged.connect(
            lambda title, v=view: self._on_title(title, v))
        view.loadStarted.connect(
            lambda v=view: self._on_load_started(v))
        view.loadProgress.connect(
            lambda p, v=view: self._on_progress(p, v))
        view.loadFinished.connect(
            lambda ok, v=view: self._on_load_finished(ok, v))
        view.renderProcessTerminated.connect(
            lambda st, code, v=view: self._on_render_terminated(st, code, v))

    def current_view(self):
        """当前活动标签的 view（没有就 None）。"""
        if 0 <= self._active < len(self._tabs):
            return self._tabs[self._active].view
        return None

    def tabs_count(self):
        return len(self._tabs)

    def activate_tab(self, index):
        """切到第 index 个标签。"""
        if not (0 <= index < len(self._tabs)):
            return
        self._active = index
        tab = self._tabs[index]
        self._view = tab.view
        self._stack.setCurrentWidget(tab.view)
        for i, t in enumerate(self._tabs):
            t.chip.set_selected(i == index)
        self._sync_addr()
        self._sync_nav()
        self._update_addr_icon()
        self._apply_zoom(tab.view)
        self._set_reload_state(False)
        title = ""
        try:
            title = tab.view.title() or ""
        except Exception:
            pass
        self.status.setText(f"{title} — {self.current_url()}" if title
                            else (self.current_url() or "就绪"))

    def close_tab(self, index):
        """关掉第 index 个标签；最后一个关掉后自动补一个新空白标签。"""
        if not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs.pop(index)
        try:
            tab.chip.hide()
            self._tabs_layout.removeWidget(tab.chip)
            tab.chip.setParent(None)
            tab.chip.deleteLater()
        except Exception:
            pass
        try:
            self._stack.removeWidget(tab.view)
            tab.view.stop()
            tab.view.hide()
            tab.view.setParent(None)
            tab.view.deleteLater()
        except Exception:
            pass
        if not self._tabs:
            self._active = -1
            self._view = None
            self.new_tab(None, activate=True)
            return
        self.activate_tab(min(index, len(self._tabs) - 1))

    def _index_of_chip(self, chip):
        for i, t in enumerate(self._tabs):
            if t.chip is chip:
                return i
        return -1

    def _on_chip_activated(self, chip):
        idx = self._index_of_chip(chip)
        if idx >= 0:
            self.activate_tab(idx)

    def _on_chip_close(self, chip):
        idx = self._index_of_chip(chip)
        if idx >= 0:
            self.close_tab(idx)

    # ================= 导航动作 =================
    def go_back(self):
        v = self.current_view()
        if v is not None:
            v.back()

    def go_forward(self):
        v = self.current_view()
        if v is not None:
            v.forward()

    def reload_or_stop(self):
        """↻ / ✕：加载中就是「停止」，否则是「刷新」。"""
        v = self.current_view()
        if v is None:
            return
        if self._loading:
            v.stop()
            self._set_reload_state(False)
            self.status.setText("已停止加载。")
        else:
            v.reload()

    def go_home(self):
        self.load(HOME_URL)

    def set_zoom(self, factor):
        """缩放（0.25 ~ 3.0）。"""
        try:
            self._zoom = max(0.25, min(3.0, round(float(factor), 2)))
        except Exception:
            self._zoom = 1.0
        self._apply_zoom(self.current_view())
        self.status.setText(f"缩放：{int(self._zoom * 100)}%")

    def _apply_zoom(self, view):
        if view is None:
            return
        try:
            view.setZoomFactor(self._zoom)
        except Exception:
            pass

    def open_in_new_window(self):
        """用系统默认浏览器打开当前页（真浏览器里"在新窗口打开"的等价物）。"""
        url = self.current_url()
        if not url or url.startswith("about:"):
            self.status.setText("还没有打开任何页面。")
            return
        try:
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))
            self.status.setText(f"已用系统浏览器打开：{url}")
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"打开失败：{e}")

    def copy_link(self):
        """复制当前地址到剪贴板。"""
        url = self.current_url()
        if not url:
            self.status.setText("还没有可复制的地址。")
            return
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(url)
            self.status.setText(f"已复制链接：{url}")
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"复制失败：{e}")

    def clear_site_data(self):
        """清除本站数据：HTTP 缓存 + Cookie + localStorage / sessionStorage。"""
        v = self.current_view()
        if v is None:
            return
        try:
            v.page().runJavaScript(
                "try{localStorage.clear();sessionStorage.clear();}catch(e){}")
        except Exception:
            pass
        try:
            if self._profile is not None:
                self._profile.clearHttpCache()
                self._profile.cookieStore().deleteAllCookies()
                try:
                    self._profile.clearAllVisitedLinks()
                except Exception:
                    pass
        except Exception:
            pass
        self.status.setText("已清除本站缓存与登录数据。")
        try:
            v.reload()
        except Exception:
            pass

    def open_devtools(self):
        """开发者工具：挂在当前页上，单独一个小窗口（用户点了才开）。"""
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        v = self.current_view()
        if v is None:
            return
        try:
            if self._devtools is None:
                self._devtools = QWebEngineView()
                self._devtools.setWindowTitle("开发者工具")
                self._devtools.resize(920, 620)
            v.page().setDevToolsPage(self._devtools.page())
            self._devtools.show()
            self._devtools.raise_()
            self.status.setText("已打开开发者工具。")
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"开发者工具打开失败：{e}")

    def _show_menu(self):
        """⋯：在按钮下方弹出菜单。"""
        try:
            pos = self.btn_menu.mapToGlobal(
                QPoint(0, self.btn_menu.height() + 2))
            self.menu.exec(pos)
        except Exception:
            pass

    def menu_action_texts(self):
        """菜单里的文案清单（自证 / 回归用：确认该有的都在）。"""
        out = []
        for a in self.menu.actions():
            if a.isSeparator():
                continue
            if a.menu() is not None:
                out.append(a.text())
                out.extend(x.text() for x in a.menu().actions())
            else:
                out.append(a.text())
        return out

    # ================= 地址栏 =================
    def _on_addr_enter(self):
        """回车：像网址就打开，不像就搜索。"""
        txt = self.addr.text().strip()
        if not txt:
            return
        if looks_like_url(txt):
            self.load(txt)
        else:
            self.load_url(search_url(txt), txt)

    def load(self, url):
        """让内嵌浏览器打开某个网址（自动补协议、校验合法性）。"""
        qurl = normalize_url(url)
        if qurl is None:
            self.status.setText("网址无效：请输入 http:// 或 https:// 开头的地址。")
            return
        self.load_url(qurl, qurl.toString())

    def load_url(self, qurl, display=None):
        """内部用：直接加载一个 QUrl（搜索走的也是这条路）。"""
        if not self._available:
            self.status.setText("未安装浏览器内核，无法加载页面。")
            return
        if not self._tabs:
            self.new_tab(None, activate=True)
        v = self.current_view()
        if v is None:
            self.status.setText("浏览器内核未就绪，稍后再试。")
            return
        self.addr.setText(display or qurl.toString())
        self.status.setText("加载中…")
        self._set_reload_state(True)
        v.load(qurl)

    def current_url(self):
        """当前地址（优先取浏览器实际地址，其次取地址栏文本）。"""
        v = self.current_view()
        if v is not None:
            u = v.url().toString()
            if u and u != "about:blank":
                return u
        return self.addr.text().strip()

    def _sync_addr(self):
        v = self.current_view()
        if v is None:
            return
        u = v.url().toString()
        if u and u != "about:blank" and u != self.addr.text():
            self.addr.setText(u)
        elif not u:
            self.addr.setText("")

    def _update_addr_icon(self):
        """地址栏左侧小图标：https 给「安全」，http 给「不安全」，空则放大镜。"""
        if self._addr_action is None:
            return
        t = themes.tokens()
        v = self.current_view()
        url = v.url().toString().lower() if v is not None else ""
        if url.startswith("https://"):
            pm = _glyph_pixmap(glyph("key"), t["success"], 14)
            tip = "安全连接（HTTPS）"
        elif url.startswith("http://"):
            pm = _glyph_pixmap(glyph("warn"), t["warn"], 14)
            tip = "不安全：这是明文 HTTP 连接"
        else:
            pm = _glyph_pixmap(glyph("search"), t["text_muted"], 14)
            tip = "搜索或输入网址"
        try:
            self._addr_action.setIcon(QIcon(pm))
            self._addr_action.setToolTip(tip)
        except Exception:
            pass

    # ================= 「交给 AI 分析当前页」 =================
    def analyze_current(self):
        """抓取当前页并交给 AI（结果走 ``fetched`` 信号）。

        ★ 直接读**当前视图已渲染的 DOM**（``page().toHtml``），
        不另起离屏视图重新加载：否则会丢掉当前登录态 / 页面状态，而且慢得多。
        """
        url = self.current_url()
        if not url or url.startswith("about:"):
            self.status.setText("还没有打开任何页面，先在地址栏输入网址。")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("上一次分析还在进行中，请稍候…")
            return
        v = self.current_view()
        if v is None:
            self.status.setText("未安装浏览器内核，无法读取页面。")
            return
        try:
            self.act_analyze.setEnabled(False)
        except Exception:
            pass
        self.status.setText("正在读取当前页面的渲染内容…")
        try:
            v.page().toHtml(self._on_html_ready)
        except Exception as e:                       # noqa: BLE001
            try:
                self.act_analyze.setEnabled(True)
            except Exception:
                pass
            self.status.setText(f"读取页面失败：{e}")

    def _on_html_ready(self, html):
        try:
            self.act_analyze.setEnabled(True)
        except Exception:
            pass
        url = self.current_url()
        if not html:
            self.status.setText(
                "读取当前页面失败（页面可能还没加载完），请稍后重试。")
            return
        title = ""
        v = self.current_view()
        try:
            title = v.title() if v is not None else ""
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

    # ================= 页面回调 =================
    def _on_url_changed(self, qurl, view=None):
        if view is not None and view is not self.current_view():
            return
        u = qurl.toString()
        if u and u != self.addr.text():
            self.addr.setText(u)
        self._update_addr_icon()
        self._sync_nav()

    def _on_title(self, title, view=None):
        """标签标题跟随页面标题（过长会按像素省略）。"""
        if view is not None:
            for tab in self._tabs:
                if tab.view is view:
                    tab.chip.set_title(title or "新标签页")
                    break
        if not title:
            return
        if view is not None and view is not self.current_view():
            return
        self.status.setText(f"{title} — {self.current_url()}")

    def _on_load_started(self, view=None):
        if view is not None and view is not self.current_view():
            return
        self._set_reload_state(True)
        self.status.setText("加载中…")

    def _on_progress(self, p, view=None):
        if view is not None and view is not self.current_view():
            return
        if 0 <= p < 100:
            self.status.setText(f"加载中… {p}%")
            self._set_reload_state(True)

    def _on_load_finished(self, ok, view=None):
        if view is not None and view is not self.current_view():
            self._sync_nav()
            return
        self._set_reload_state(False)
        self._sync_nav()
        self._update_addr_icon()
        if ok:
            u = self.current_url()
            self.status.setText(f"加载完成：{u}" if u else "加载完成。")
        else:
            self.status.setText(
                "加载失败（网络或内核问题）。可点「刷新」重试，或检查网址。")

    def _on_render_terminated(self, _status, _code, view=None):
        if view is not None and view is not self.current_view():
            return
        self._set_reload_state(False)
        self.status.setText(
            "页面渲染进程异常退出（可能内存不足或内核问题），点「刷新」重试。")

    def _set_reload_state(self, loading):
        """加载中：↻ 变 ✕（点了停止）；加载完：✕ 变回 ↻。"""
        self._loading = bool(loading)
        if self._loading:
            self.btn_reload.set_glyph(glyph("cancel"), "停止加载")
        else:
            self.btn_reload.set_glyph(glyph("reload"), "刷新")

    def _sync_nav(self):
        """前进 / 后退按钮是否可用，跟着**当前标签**的实际浏览历史走。"""
        v = self.current_view()
        if v is None:
            self.btn_back.setEnabled(False)
            self.btn_fwd.setEnabled(False)
            return
        try:
            h = v.history()
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

    # ================= 主题 =================
    def refresh_theme(self):
        """换主题时重刷 Python 侧画的颜色（QSS 部分会自动跟随）。"""
        t = themes.tokens()
        self.status.setStyleSheet(f"color:{t['text_muted']};")
        for tab in self._tabs:
            tab.chip.refresh_theme()
        self._update_addr_icon()
