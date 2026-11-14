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
import html as _html
import os
import re

from PyQt6.QtCore import Qt, QUrl, QThread, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QSizePolicy, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from .. import themes, webengine
from ..icons import ic
from .preview_pane import FilePreview, is_supported, supported_extensions

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
# 新标签页（内联、离线、深浅色自适应）
#
# 用户原话：「加号新建的每个界面都要有默认首页内容，别空白难看」。
# 以前 new_tab(None) 什么都不 load，标签直接 about:blank 白屏。
# 现在每个空标签都 setHtml(start_page_html(), QUrl("about:blank"))。
#
# 硬性约束：**不引用任何 CDN / 外链字体 / 外链图片**。样式、图标（纯 CSS
# 圆角方块 + 站点首字）、脚本全部内联；外链只出现在 <a href> 和「提交搜索」
# 的 JS 里 —— 也就是只有用户主动点击时才联网，页面渲染本身完全离线。
# ---------------------------------------------------------------------------
_QUICK_SITES = (
    ("Bing", "https://www.bing.com"),
    ("百度", "https://www.baidu.com"),
    ("GitHub", "https://github.com"),
    ("知乎", "https://www.zhihu.com"),
    ("哔哩哔哩", "https://www.bilibili.com"),
    ("腾讯文档", "https://docs.qq.com"),
    ("CSDN", "https://www.csdn.net"),
    ("微博", "https://weibo.com"),
)

_START_PAGE_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>新标签页</title>
<style>
:root{
  --bg:#f5f7fa; --card:#ffffff; --fg:#1f2328; --muted:#7a828b;
  --line:#e3e6ea; --accent:#2196F3; --soft:#e3f2fd;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#1e2126; --card:#252a31; --fg:#e6edf3; --muted:#9aa4af;
    --line:#363c45; --accent:#3b82f6; --soft:#22303f;
  }
}
*{box-sizing:border-box;}
html,body{height:100%;margin:0;}
body{
  background:var(--bg);color:var(--fg);
  font-family:"Microsoft YaHei","Segoe UI",system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;padding:24px;
}
.wrap{width:100%;max-width:720px;text-align:center;}
.brand{font-size:12px;letter-spacing:3px;color:var(--muted);margin:0 0 10px;}
h1{font-size:26px;font-weight:600;margin:0 0 8px;color:var(--fg);}
.sub{font-size:13px;color:var(--muted);margin:0 0 26px;}
form{display:flex;gap:8px;margin:0 auto 28px;max-width:560px;}
#q{
  flex:1;min-width:0;padding:12px 16px;border-radius:999px;
  border:1px solid var(--line);background:var(--card);color:var(--fg);
  font-size:14px;outline:none;
}
#q:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--soft);}
button{
  padding:12px 22px;border-radius:999px;border:none;
  background:var(--accent);color:#ffffff;font-size:14px;cursor:pointer;
}
button:hover{opacity:0.9;}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 0 26px;}
.tile{
  display:flex;flex-direction:column;align-items:center;gap:8px;
  padding:14px 6px;border-radius:14px;border:1px solid var(--line);
  background:var(--card);color:var(--fg);text-decoration:none;font-size:12px;
}
.tile:hover{border-color:var(--accent);background:var(--soft);}
.ico{
  width:38px;height:38px;border-radius:12px;background:var(--soft);
  color:var(--accent);display:flex;align-items:center;justify-content:center;
  font-size:16px;font-weight:600;
}
.hint{
  border:1px dashed var(--line);border-radius:14px;padding:14px 16px;
  background:var(--card);color:var(--muted);font-size:12px;line-height:1.9;
}
.hint b{color:var(--accent);font-weight:600;}
.foot{margin-top:18px;font-size:11px;color:var(--muted);}
@media (max-width:560px){
  .tiles{grid-template-columns:repeat(2,1fr);}
  h1{font-size:22px;}
}
</style>
</head>
<body>
<div class="wrap">
<p class="brand">AI 工作台</p>
<h1>AI 工作台 · 新标签页</h1>
<p class="sub">在下面搜索，或点快捷入口开始；这个页面完全本地生成，离线也好看。</p>
<form onsubmit="awbSearch(event)">
<input id="q" type="text" placeholder="输入关键词，按回车用 Bing 搜索" autocomplete="off">
<button type="submit">搜索</button>
</form>
<div class="tiles">
"""

_START_PAGE_TAIL = """</div>
<div class="hint">
<b>打开本地文件预览</b>：点工具行的「▤」按钮选一个文件，或直接把文件拖进这个面板。<br>
<b>拖入文件即可预览</b>：图片、Word、PPT、Excel、PDF 和常见文本格式，全部在本地解析，不上传、不联网。
</div>
<p class="foot">搜索与快捷入口只在你点击时才联网；页面本身的样式与图标全部内联。</p>
</div>
<script>
function awbSearch(ev){
  if(ev && typeof ev.preventDefault === 'function'){ ev.preventDefault(); }
  var box = document.getElementById('q');
  var q = box ? box.value.trim() : '';
  if(!q){ return; }
  window.location.href = 'https://www.bing.com/search?q=' + encodeURIComponent(q);
}
</script>
</body>
</html>
"""


def start_page_html() -> str:
    """生成完全内联、不联网的新标签页 HTML。

    返回的字符串可以直接交给 ``QWebEngineView.setHtml(html, QUrl("about:blank"))``。
    页面里没有 ``<link>`` / ``<img src="http...">`` / ``@import`` / 外链字体，
    只有用户点击链接或提交搜索时才会联网。
    """
    tiles = "".join(
        '<a class="tile" href="' + _html.escape(url, quote=True) + '">'
        '<span class="ico">' + _html.escape(name[:1]) + '</span>'
        '<span class="nm">' + _html.escape(name) + '</span></a>'
        for name, url in _QUICK_SITES
    )
    return _START_PAGE_HEAD + tiles + _START_PAGE_TAIL


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
    # ▤ U+25A4：文字呈现的单色几何字形，app/icons.py 里已被逐像素验过，
    # 不走彩色 emoji（tests/probe_icons_v9180.py 会扫全部 UI 字面量）。
    "preview": "\u25A4",    # ▤  打开文件预览
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


def _is_clean_addr(url: str) -> bool:
    """这个 URL 能不能安全地显示进地址栏。

    ``setHtml()`` 之后 ``view.url()`` 通常是 ``about:blank``，个别 Qt 版本
    会给出一整段 ``data:text/html;...``（把整页 HTML 当成 URL）。这两种都
    不能塞进地址栏：前者显示空占位符，后者会把一整页源码糊到界面上。
    """
    if not url or len(url) > 2048:
        return False
    low = url.lower()
    return not low.startswith(("about:", "data:", "javascript:", "blob:"))


class LocalStartPage(QWidget):
    """内核不可用时的本地新标签页：纯 Qt 控件，绝不白屏。

    只在 ``_make_view()`` 失败 / 内核未装时出现。它和 ``FilePreview`` 一样
    是「非 WebEngine 标签」，BrowserPane 里所有会调 ``.page()/.url()`` 的
    方法都通过 ``_web_view()`` 把它过滤掉。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BrowserStartPage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.body = QTextBrowser()
        self.body.setObjectName("BrowserStartBody")
        self.body.setReadOnly(True)
        self.body.setOpenExternalLinks(False)
        self.body.setOpenLinks(False)
        lay.addWidget(self.body, 1)
        self.refresh_theme()

    def title(self) -> str:
        """标签标题（不是 QWidget.windowTitle，别混）。"""
        return "新标签页"

    def refresh_theme(self) -> None:
        """主题切换后重新取色，深浅色都保持可读。"""
        t = themes.tokens()
        fg = t.get("text", "#1f2328")
        muted = t.get("text_muted", "#7a828b")
        line = t.get("border", "#e3e6ea")
        card = t.get("card_bg", "#ffffff")
        bg = t.get("panel_bg", "#ffffff")
        accent = t.get("accent_text", "#0d47a1")
        rows = "".join(
            "<tr>"
            f'<td style="padding:4px 16px 4px 0;color:{fg};font-weight:bold;">'
            f'{_html.escape(name)}</td>'
            f'<td style="padding:4px 0;color:{muted};">{_html.escape(url)}</td>'
            "</tr>"
            for name, url in _QUICK_SITES)
        self.body.setHtml(
            f'<div style="font-family:\'Microsoft YaHei\';color:{fg};padding:24px 28px;">'
            f'<p style="font-size:12px;color:{muted};letter-spacing:2px;margin:0 0 8px;">'
            f'AI 工作台</p>'
            f'<h1 style="font-size:24px;color:{fg};margin:0 0 8px;">新标签页</h1>'
            f'<p style="font-size:12px;color:{muted};margin:0 0 18px;">'
            f'浏览器内核暂不可用，网页浏览已停用；本地文件预览完全可用。</p>'
            f'<div style="border:1px solid {line};background:{card};'
            f'border-radius:10px;padding:12px 16px;">'
            f'<p style="font-size:13px;color:{accent};font-weight:bold;margin:0 0 8px;">'
            f'常用站点（复制网址到地址栏打开）</p>'
            f'<table border="0" cellspacing="0" cellpadding="0" '
            f'style="font-size:12px;">{rows}</table>'
            f'</div>'
            f'<p style="font-size:12px;color:{muted};margin:16px 0 0;line-height:1.9;">'
            f'打开本地文件预览：点工具行的「▤」按钮，或直接把文件拖进这个面板。<br>'
            f'拖入文件即可预览：图片、Word、PPT、Excel、PDF 和常见文本格式，'
            f'全部在本地解析，不上传。</p>'
            f'</div>')
        self.body.setStyleSheet(
            f"QTextBrowser{{background:{bg};color:{fg};border:none;}}")


def _web_view(view):
    """把标签里的对象收敛成「能调 WebEngine API 的视图」或 ``None``。

    ⚠ 这里必须用**鸭子类型能力检测**而不是 ``isinstance(QWebEngineView)``：
    ``tests/probe_browser_ui_v92210.py`` 会用一个纯 Python 的 ``_FakeView``
    替换标签视图来验证 ``analyze_current()``，它不是 QWebEngineView 却必须
    继续走网页分析路径。反过来，``FilePreview`` / ``LocalStartPage`` 是
    QWidget，没有 ``page()/url()/load()``，必须被挡在门外。
    """
    if view is None:
        return None
    if isinstance(view, (FilePreview, LocalStartPage)):
        return None
    for name in ("page", "url", "load", "back", "forward", "reload",
                 "stop", "history"):
        if not callable(getattr(view, name, None)):
            return None
    return view


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
        # 文件预览支持拖放：把本地文件拖进面板即可开一个预览标签。
        self.setAcceptDrops(True)

        if self._available:
            self.new_tab(None, activate=True)
        else:
            self._show_engine_tip()
            # 内核不可用也别白屏：再补一个纯 Qt 的本地新标签页。
            # new_tab() 在不可用时会走 _new_local_start_tab()，不建 Chromium。
            self.new_tab(None, activate=True)

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
        # 「打开文件预览」：▤ 是单色几何字形，不是彩色 emoji；objectName 沿用
        # _ChromeButton 默认的 "BrowserBtn"，跟其它工具按钮共用同一套 QSS。
        self.btn_preview = _ChromeButton(
            glyph("preview"),
            "打开文件预览（图片 / Word / PPT / Excel / PDF / 文本）", bar)
        for b in (self.btn_back, self.btn_fwd, self.btn_reload,
                  self.btn_home, self.btn_preview):
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
        self.btn_preview.clicked.connect(self._choose_preview_file)
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
        """新建标签页并返回它的 view；内核不可用时返回 None。

        ★ v9.22 变更：``url`` 为空时用 ``setHtml(start_page_html(),
        QUrl("about:blank"))`` 显示一张完全内联的本地新标签页，而不是留一个
        ``about:blank`` 白屏。内核不可用 / 建视图失败时也不白屏：补一个纯
        Qt 的 ``LocalStartPage`` 标签（同样让 ``tabs_count()`` 递增），
        只是返回值仍为 None，语义是「没有 WebEngine view」。
        """
        if not self._available:
            self.status.setText("未安装浏览器内核，已显示本地新标签页；"
                                "可用「打开文件预览」看本地文件。")
            return self._new_local_start_tab(activate)
        try:
            view = self._make_view()
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"浏览器内核启动失败：{type(e).__name__}: {e}")
            return self._new_local_start_tab(activate)

        chip = _TabChip("新标签页", self._tabs_host)
        chip.activated.connect(self._on_chip_activated)
        chip.close_clicked.connect(self._on_chip_close)
        self._tabs_layout.addWidget(chip)

        tab = _Tab(chip=chip, view=view)
        self._tabs.append(tab)
        self._stack.addWidget(view)
        self._wire_view(view)

        qurl = normalize_url(url) if url else None
        if qurl is not None:
            view.load(qurl)
        else:
            # 空标签 / 非法网址：一律显示内联首页，绝不留白屏。
            try:
                view.setHtml(start_page_html(), QUrl("about:blank"))
            except Exception:                       # noqa: BLE001
                pass
            if url:
                self.status.setText("网址无法解析，已显示新标签页。")
        if activate or self._active < 0:
            self.activate_tab(len(self._tabs) - 1)
        return view

    def _new_local_start_tab(self, activate=True):
        """内核不可用时也建一个「本地首页」标签；返回 None（没有 WebEngine view）。"""
        try:
            widget = LocalStartPage()
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"首页控件初始化失败：{type(e).__name__}: {e}")
            return None
        chip = _TabChip("新标签页", self._tabs_host)
        chip.activated.connect(self._on_chip_activated)
        chip.close_clicked.connect(self._on_chip_close)
        self._tabs_layout.addWidget(chip)
        tab = _Tab(chip=chip, view=widget)
        self._tabs.append(tab)
        self._stack.addWidget(widget)
        if activate or self._active < 0:
            self.activate_tab(len(self._tabs) - 1)
        self.status.setText(
            "浏览器内核未就绪：已显示本地新标签页，本地文件预览仍可使用。")
        return None

    # ================= 本地文件预览 =================
    def preview_file(self, path: str) -> bool:
        """新建一个标签预览本地文件，切过去并返回是否解析成功。

        这个入口**不依赖 Chromium**：即使 WebEngine 内核没装，图片 / Word /
        PPT / Excel / PDF / 文本照样能看。失败时标签里是一张中文说明页，
        绝不把异常抛到 UI。
        """
        p = str(path or "").strip().strip('"')
        if not p:
            self.status.setText("没有选择文件。")
            return False
        if not os.path.isfile(p):
            self.status.setText(f"文件不存在：{p}")
            return False
        try:
            widget = FilePreview()
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"预览控件初始化失败：{type(e).__name__}: {e}")
            return False
        try:
            ok = bool(widget.preview(p))
        except Exception as e:                       # noqa: BLE001
            ok = False
            try:
                widget.show_error(f"{type(e).__name__}: {e}")
            except Exception:
                pass
        name = widget.title() or os.path.basename(p) or "文件预览"
        chip = _TabChip(name, self._tabs_host)
        chip.activated.connect(self._on_chip_activated)
        chip.close_clicked.connect(self._on_chip_close)
        self._tabs_layout.addWidget(chip)
        tab = _Tab(chip=chip, view=widget)
        self._tabs.append(tab)
        self._stack.addWidget(widget)
        self.activate_tab(len(self._tabs) - 1)
        if ok:
            suffix = "" if is_supported(p) else "（暂无内置解析器，只显示文件信息）"
            self.status.setText(f"本地预览：{name}{suffix}")
        else:
            self.status.setText(f"预览失败：{name}，已显示说明页。")
        return ok

    def _choose_preview_file(self):
        """工具行「▤」按钮：弹文件对话框选一个本地文件去预览。"""
        # 离屏自动化（QT_QPA_PLATFORM=offscreen）没有可用的原生文件对话框，
        # 这里直接给出提示，避免自动测试被一个模态对话框挂住。
        if os.environ.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            self.status.setText(
                "离屏环境不弹文件对话框：把文件拖进面板，或调用 preview_file(路径)。")
            return
        exts = " ".join("*" + e for e in supported_extensions())
        filt = f"可预览的文件 ({exts});;所有文件 (*.*)"
        try:
            path, _selected = QFileDialog.getOpenFileName(
                self, "选择要预览的本地文件", "", filt)
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"打开文件对话框失败：{type(e).__name__}: {e}")
            return
        if path:
            self.preview_file(path)

    # ================= 拖放 =================
    def dragEnterEvent(self, event):                 # noqa: N802（Qt 回调）
        """只接本地文件 URL；其它拖入内容一律忽略。"""
        try:
            mime = event.mimeData()
            if mime is not None and mime.hasUrls():
                for url in mime.urls():
                    if url.isLocalFile():
                        event.acceptProposedAction()
                        return
        except Exception:
            pass
        event.ignore()

    def dropEvent(self, event):                      # noqa: N802（Qt 回调）
        """本地文件拖进来就各开一个预览标签。"""
        handled = False
        try:
            mime = event.mimeData()
            urls = mime.urls() if (mime is not None and mime.hasUrls()) else []
            for url in urls:
                if not url.isLocalFile():
                    continue
                p = url.toLocalFile()
                if p and os.path.isfile(p):
                    self.preview_file(p)
                    handled = True
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"拖入文件预览失败：{type(e).__name__}: {e}")
        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()

    def web_view(self):
        """当前标签里能调 WebEngine API 的视图；本地预览 / 起始页返回 None。

        所有需要 ``.page()/.url()/.load()`` 的方法都必须先过这一层，
        否则在文件预览标签上点前进/后退/刷新会直接抛 AttributeError。
        """
        return _web_view(self.current_view())

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
        """当前活动标签的 view（没有就 None）。

        ⚠ 它可能是 ``QWebEngineView``，也可能是 ``FilePreview`` /
        ``LocalStartPage``（本地预览 / 无内核首页）。所有调用方都必须先用
        ``web_view()`` 过滤，绝不直接 ``.page()/.url()/.load()``。
        """
        if 0 <= self._active < len(self._tabs):
            return self._tabs[self._active].view
        return None

    def tabs_count(self):
        return len(self._tabs)

    def activate_tab(self, index):
        """切到第 index 个标签（本地预览标签会自动禁用导航按钮）。"""
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
            fn = getattr(tab.view, "title", None)
            title = str(fn() or "") if callable(fn) else ""
        except Exception:
            title = ""
        if isinstance(tab.view, FilePreview):
            self.status.setText(
                f"本地预览：{title or '文件'}（可用工具行按钮打开其它文件）")
        elif _web_view(tab.view) is None:
            self.status.setText(f"本地新标签页：{title or '新标签页'}")
        else:
            self.status.setText(f"{title} — {self.current_url()}" if title
                                else (self.current_url() or "就绪"))

    def close_tab(self, index):
        """关掉第 index 个标签；最后一个关掉后自动补一个新首页标签。"""
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
            w = _web_view(tab.view)
            if w is not None:
                w.stop()
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
        """本地预览 / 起始页没有浏览历史，直接返回（绝不调 .back()）。"""
        w = self.web_view()
        if w is None:
            return
        try:
            w.back()
        except Exception:
            pass

    def go_forward(self):
        """同 go_back：不是 WebEngine 视图就什么也不做。"""
        w = self.web_view()
        if w is None:
            return
        try:
            w.forward()
        except Exception:
            pass

    def reload_or_stop(self):
        """↻ / ✕：加载中就是「停止」，否则是「刷新」。

        本地文件预览标签没有可刷新的网页，给一行中文提示就返回。
        """
        w = self.web_view()
        if w is None:
            self.status.setText("当前是本地文件预览，没有网页可刷新。")
            return
        try:
            if self._loading:
                w.stop()
                self._set_reload_state(False)
                self.status.setText("已停止加载。")
            else:
                w.reload()
        except Exception as e:                       # noqa: BLE001
            self.status.setText(f"刷新失败：{type(e).__name__}: {e}")

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
        """只有 WebEngine 视图才有 setZoomFactor；本地预览直接跳过。"""
        w = _web_view(view)
        if w is None:
            return
        try:
            w.setZoomFactor(self._zoom)
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
        w = self.web_view()
        if w is None:
            self.status.setText("本地文件预览没有网站数据需要清除。")
            return
        try:
            w.page().runJavaScript(
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
            w.reload()
        except Exception:
            pass

    def open_devtools(self):
        """开发者工具：挂在当前页上，单独一个小窗口（用户点了才开）。"""
        w = self.web_view()
        if w is None:
            self.status.setText("本地文件预览没有开发者工具。")
            return
        try:
            # 内核缺失时这行 import 会抛，必须在 try 里，不能放函数开头。
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            if self._devtools is None:
                self._devtools = QWebEngineView()
                self._devtools.setWindowTitle("开发者工具")
                self._devtools.resize(920, 620)
            w.page().setDevToolsPage(self._devtools.page())
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
        """内部用：直接加载一个 QUrl（搜索走的也是这条路）。

        当前标签是本地文件预览时**另开一个网页标签**再加载 —— 不能对
        ``FilePreview`` 调 ``.load()``，那样会直接抛 AttributeError。
        """
        if qurl is None:
            return
        if not self._available:
            self.status.setText(
                "未安装浏览器内核，无法加载页面；可用「打开文件预览」看本地文件。")
            return
        if not self._tabs:
            self.new_tab(None, activate=True)
        w = self.web_view()
        if w is None:
            self.new_tab(None, activate=True)
            w = self.web_view()
        if w is None:
            self.status.setText("浏览器内核未就绪，稍后再试。")
            return
        self.addr.setText(display or qurl.toString())
        self.status.setText("加载中…")
        self._set_reload_state(True)
        try:
            w.load(qurl)
        except Exception as e:                       # noqa: BLE001
            self._set_reload_state(False)
            self.status.setText(f"加载失败：{type(e).__name__}: {e}")

    def current_url(self):
        """当前地址；本地预览 / 首页返回空串，绝不把 data: 长 HTML 塞出来。"""
        w = self.web_view()
        if w is None:
            return ""
        try:
            qurl = w.url()
            u = qurl.toString() if qurl is not None else ""
        except Exception:
            u = ""
        if _is_clean_addr(u):
            return u
        return self.addr.text().strip()

    def _sync_addr(self):
        """同步地址栏；预览页 / 起始页一律留空，让占位符说话。"""
        w = self.web_view()
        if w is None:
            if self.addr.text():
                self.addr.setText("")
            return
        try:
            qurl = w.url()
            u = qurl.toString() if qurl is not None else ""
        except Exception:
            u = ""
        if _is_clean_addr(u):
            if u != self.addr.text():
                self.addr.setText(u)
        elif self.addr.text():
            self.addr.setText("")

    def _update_addr_icon(self):
        """地址栏左侧小图标：https 给「安全」，http 给「不安全」，空则放大镜。"""
        if self._addr_action is None:
            return
        t = themes.tokens()
        v = self.web_view()
        try:
            qurl = v.url() if v is not None else None
            url = (qurl.toString() if qurl is not None else "").lower()
        except Exception:
            url = ""
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
            if self.web_view() is None:
                self.status.setText("当前是本地文件预览，没有网页地址可分析。")
            else:
                self.status.setText("还没有打开任何页面，先在地址栏输入网址。")
            return
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("上一次分析还在进行中，请稍候…")
            return
        w = self.web_view()
        if w is None:
            self.status.setText("当前是本地文件预览，没有网页 DOM 可分析。")
            return
        try:
            self.act_analyze.setEnabled(False)
        except Exception:
            pass
        self.status.setText("正在读取当前页面的渲染内容…")
        try:
            w.page().toHtml(self._on_html_ready)
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
        w = self.web_view()
        try:
            fn = getattr(w, "title", None) if w is not None else None
            title = str(fn() or "") if callable(fn) else ""
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
        """首页 setHtml 会带 about:blank / data: 回来，这两种绝不进地址栏。"""
        if view is not None and view is not self.current_view():
            return
        try:
            u = qurl.toString() if qurl is not None else ""
        except Exception:
            u = ""
        if _is_clean_addr(u):
            if u != self.addr.text():
                self.addr.setText(u)
        elif not u or u.lower().startswith(("about:", "data:")):
            if self.addr.text():
                self.addr.setText("")
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
        """前进 / 后退 / 刷新跟着当前标签走；本地预览一律禁用三个按钮。"""
        w = self.web_view()
        if w is None:
            self.btn_back.setEnabled(False)
            self.btn_fwd.setEnabled(False)
            self.btn_reload.setEnabled(False)
            return
        self.btn_reload.setEnabled(True)
        try:
            h = w.history()
            self.btn_back.setEnabled(bool(h.canGoBack()))
            self.btn_fwd.setEnabled(bool(h.canGoForward()))
        except Exception:
            self.btn_back.setEnabled(False)
            self.btn_fwd.setEnabled(False)

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
            # 本地预览 / 起始页用 tokens() 现取色，主题变了要重新生成 HTML。
            # QWebEngineView 没有这个方法，getattr 会静默跳过。
            try:
                fn = getattr(tab.view, "refresh_theme", None)
                if callable(fn):
                    fn()
            except Exception:
                pass
        self._update_addr_icon()
