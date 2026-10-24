# -*- coding: utf-8 -*-
"""真实浏览器内核抓取（v9.18.0）：像浏览器一样打开网页 + 自由抓包。

给 AI 工作台补上 ``app/webcap.py`` 那个纯 HTTP 抓取抓不到的东西：

  · **JS 渲染后的 DOM** —— 现代站点（SPA、易查分、各种后台）正文是 JS 跑完才有的，
    纯 GET 只能拿到一具空壳 HTML。这里用 QtWebEngine（Chromium 内核）**真实加载**页面，
    等 JS 执行完再取 ``document.documentElement.outerHTML``。
  · **页面到底发了哪些请求** —— 通过 ``QWebEngineUrlRequestInterceptor`` 把页面发出的
    全部网络请求（主文档 / 脚本 / XHR / Fetch …）记下来，模型就能照着复现真正的数据接口，
    而不用对着静态 HTML 猜。

设计要点（都是踩过坑才这么写的）：

1. **必须能独立工作**：模型工具跑在 ``ChatWorker(QThread)`` 工作线程里，而 QtWebEngine 的
   控件只能在 **Qt 主线程** 创建。所以这里做了一层「调度到主线程」的封装：
   - 没有 ``QApplication``（比如探针脚本）→ 当场自建一个，当前线程就是主线程；
   - 已经在主线程 → 直接用嵌套 ``QEventLoop`` 等；
   - 在工作线程 → 用 ``moveToThread`` + 队列调用把活儿丢给主线程，再用 ``Event`` 带**超时**等待。
   任何一步超时都绝不永久阻塞。
2. **串行化**：全局一把锁，避免多个离屏页面互相抢内核。
3. **优雅降级**：WebEngine 装不上 / 主线程调度超时 → 回落到 ``webcap`` 的静态抓取，
   返回里标 ``engine="urllib"`` 并给中文提示，**绝不抛异常**。
4. **不把窗口藏屏幕外**：离屏就用「建了不 ``show()``」的 ``QWebEngineView``，
   不使用 ``-position=-32000,-32000`` 那种会闪出真窗口的脏手法。

踩坑记录（本机 Qt6 6.10 实测，改动前请先看这段）：
  · QtWebEngine 必须在 ``QApplication`` **之前**导入，否则挂请求拦截器会直接把进程
    干掉（exit 127）。所以本模块**在 import 时就导入内核**，并把"是否导入太晚"记在
    ``_APP_EXISTED_AT_IMPORT`` 上：太晚就只渲染、不抓包（见 ``_install_sniffer``）。
    主程序若想启用抓包，需保证 ``app.webengine`` / ``app.gui.browser_page`` 在
    ``QApplication(...)`` 之前被导入。
  · ``QApplication([])``（空参数）之后装拦截器同样会崩，必须给个占位 ``argv[0]``。
    ★ v9.19 又定位到它的另一半：**空 argv 连"建 QWebEngineProfile"都会静默 exit 127**
    （无栈、无 stderr、无任何提示，最容易误判成"命名 profile 不能用"）。凡是脚本 /
    自检路径自建 QApplication，一律写 ``QApplication([sys.argv[0] or "AIWorkbench"])``。
  · 不能用无痕 Profile ``QWebEngineProfile()``（一建就崩），拦截器只能挂默认 Profile。
  · 拦截器必须**先装、后建 view**；默认 Profile 上一旦有页面在跑，再挂就崩。
  · Chromium 渲染沙箱在本机起不来，不加 ``--no-sandbox`` 则所有页面 ``loadFinished``
    都是 False；``--disable-software-rasterizer`` 也会把进程搞崩，别加。
"""
import os
import re
import sys
import threading

# --------------------------------------------------------------------------
# 内核初始化：这两个环境变量必须在 Chromium 启动前设好
# --------------------------------------------------------------------------
# 这两个开关是**实测必需**的（Windows 下）：
#   · --no-sandbox         Chromium 的渲染沙箱在本机环境起不来，不加则每个页面
#                          loadFinished 都是 False（渲染进程一启动就被干掉）。
#   · --disable-gpu        离屏渲染不需要 GPU，关掉能少一堆显卡驱动相关的白屏/崩溃。
# 另外三个是 v9.18.0 内嵌 DeepSeek 宿主加的：内嵌页面以
# WA_DontShowOnScreen 的形式常驻后台，默认会被 Chromium 判为"被遮挡/后台"而降频，
# 定时器被节流后输入框的防抖、流式回答的轮询都会变慢，所以全部关掉。
# 用户可用环境变量 QTWEBENGINE_CHROMIUM_FLAGS 覆盖这里的默认值。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox "
    "--disable-background-timer-throttling "
    "--disable-renderer-backgrounding "
    "--disable-backgrounding-occluded-windows")

# --------------------------------------------------------------------------
# CDP 远程调试口（v9.18.0 内嵌浏览器宿主）
# --------------------------------------------------------------------------
# ★ 端口常量的**唯一来源**就是这里。main.py 在导入任何 GUI 模块之前调用
#   ``enable_remote_debugging()``；QtWebEngine 只在 QApplication 创建时才启动
#   Chromium，所以只要在那之前设好环境变量，``http://127.0.0.1:<port>/json``
#   就会暴露标准 CDP，``deepseek_web/embedded.py`` 直接拿这个端口去连。
REMOTE_DEBUG_PORT = 9333


def enable_remote_debugging(port=None):
    """打开 QtWebEngine 的 CDP 调试口；返回最终生效的端口。

    必须在 QApplication / Chromium 启动**之前**调用（main.py 顶部已调用）。
    若外部已用 ``QTWEBENGINE_REMOTE_DEBUGGING`` 指定了别的端口，以外部为准。
    """
    global REMOTE_DEBUG_PORT
    if port is not None:
        REMOTE_DEBUG_PORT = int(port)
    os.environ.setdefault("QTWEBENGINE_REMOTE_DEBUGGING", str(int(REMOTE_DEBUG_PORT)))
    try:
        REMOTE_DEBUG_PORT = int(os.environ["QTWEBENGINE_REMOTE_DEBUGGING"])
    except Exception:
        pass
    return REMOTE_DEBUG_PORT


def remote_debug_port():
    """当前生效的 CDP 端口（供 deepseek_web 使用）。"""
    try:
        return int(os.environ.get("QTWEBENGINE_REMOTE_DEBUGGING") or REMOTE_DEBUG_PORT)
    except Exception:
        return int(REMOTE_DEBUG_PORT)

# 记录"导入本模块时是不是已经有 QApplication 了"。QtWebEngine 有一条硬规则：
# 它必须在 QApplication **之前**导入。若已经晚了，渲染还能用，但再往默认 Profile
# 上挂请求拦截器会把进程直接干掉（实测 exit 127），所以那种情况下我们**不做抓包**，
# 只保证渲染可用（见 _install_sniffer / fetch_rendered）。
try:
    from PyQt6.QtCore import QCoreApplication as _QCore
    _APP_EXISTED_AT_IMPORT = _QCore.instance() is not None
except Exception:
    _APP_EXISTED_AT_IMPORT = False

# QtWebEngineWidgets 有一条硬性规则：它必须在 QApplication 建出来之前被导入，
# 否则抛 ImportError。所以这里主动补设 AA_ShareOpenGLContexts，并**紧接着**导入
# 内核模块 —— 顺序不能乱（详见文件末尾"踩坑记录"）。
try:
    from PyQt6.QtCore import Qt as _Qt
    from PyQt6.QtWidgets import QApplication as _QApp

    _QApp.setAttribute(_Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
except Exception:
    pass

_WEBENGINE_VIEW = None          # QWebEngineView 类
_InterceptorBase = object       # QWebEngineUrlRequestInterceptor 基类（缺省给个占位）
_WE_CHECKED = None              # None=没查过 / True / False

# 内核相关模块在**模块导入时**就试着拿一次：
# 一来 QtWebEngineWidgets 越早导入越省事（它自带的那条顺序检查），
# 二来下面的 _Sniffer 要继承拦截器基类，基类必须此刻就在手。
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView as _WEBENGINE_VIEW
    from PyQt6.QtWebEngineCore import (
        QWebEngineUrlRequestInterceptor as _InterceptorBase,
    )
    _WE_CHECKED = True
except Exception:
    _WEBENGINE_VIEW = None
    _InterceptorBase = object
    _WE_CHECKED = False

_LOCK = threading.RLock()       # 串行化所有离屏渲染
_APP_HOLDER = None              # 自建 QApplication 的强引用，防止被 GC
_PENDING = set()                # 还在主线程排队/执行中的调度对象，防止被 GC


# --------------------------------------------------------------------------
# 内核可用性探测
# --------------------------------------------------------------------------
def engine_available():
    """WebEngine 内核是否可用（不抛异常，只回 True/False）。"""
    return bool(_WE_CHECKED)


# --------------------------------------------------------------------------
# 主线程调度：把「只能在主线程做」的活儿安全地送过去
# --------------------------------------------------------------------------
def _in_gui_thread(app):
    """当前线程是不是 ``app`` 所在的主线程。"""
    try:
        return bool(app.thread().isCurrentThread())
    except Exception:
        return True


def _ensure_app():
    """拿到（必要时自建）QApplication；没有 GUI 时自建的一个让整块能独立跑。"""
    global _APP_HOLDER
    try:
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtWidgets import QApplication
    except Exception:
        return None
    app = QCoreApplication.instance()
    if app is None:
        # 无 GUI（探针/脚本）——当前线程就是主线程，放心建。
        # 注意：绝不能传空列表 QApplication([])，实测那样再装请求拦截器会把进程
        # 直接搞崩（exit 127）；给个占位 argv[0] 就没事。
        app = QApplication(sys.argv[:1] or ["AIWorkbench"])
        _APP_HOLDER = app
    return app


class _Dispatcher:
    """把可调用对象丢到 Qt 主线程执行，并把结果/异常带回来。"""

    @staticmethod
    def call(fn, timeout):
        app = _ensure_app()
        if app is None:
            raise RuntimeError("没有 QApplication，无法使用浏览器内核")
        if _in_gui_thread(app):
            # 就在主线程：直接跑（内部自带 QEventLoop + 超时）。
            return fn()

        # 工作线程 → 主线程。用队列调用 + Event 等待，保证有超时不会死锁。
        from PyQt6.QtCore import QObject, Qt, QMetaObject, pyqtSlot

        class _Invoker(QObject):
            def __init__(self, func):
                super().__init__()
                self._func = func
                self.result = None
                self.error = None
                self.done = threading.Event()

            @pyqtSlot()
            def run(self):
                try:
                    self.result = self._func()
                except BaseException as e:      # 异常也要带回调用方
                    self.error = e
                finally:
                    self.done.set()
                    _PENDING.discard(self)

        inv = _Invoker(fn)
        inv.moveToThread(app.thread())
        _PENDING.add(inv)
        QMetaObject.invokeMethod(inv, "run", Qt.ConnectionType.QueuedConnection)
        if not inv.done.wait(float(timeout)):
            _PENDING.discard(inv)
            raise TimeoutError("浏览器内核等待主线程调度超时")
        if inv.error is not None:
            raise inv.error
        return inv.result


# --------------------------------------------------------------------------
# 请求嗅探器
# --------------------------------------------------------------------------
# 拦截器实例全局只造一个，挂在**默认 Profile** 上（整个进程一份）。
# 为什么不用无痕 Profile：实测 ``QWebEngineProfile()`` 在本机内核下会直接把进程干掉
# （exit 127），而默认 Profile 一切正常。
# 为什么"挂一次 + 开关"：拦截器要捕获的是"当前这次抓取"，用 ``set_sink()`` 把记录
# 落到调用方的列表里；不抓的时候把 sink 置空，用户自己在内嵌浏览器里点网页不会被误记。
_SNIFFER = None
_SNIFFER_READY = False


class _Sniffer(_InterceptorBase):
    """记录页面发出的网络请求（URL / method / resourceType）。"""

    def __init__(self):
        super().__init__()
        self._sink = None

    def set_sink(self, sink):
        """开始/停止记录；sink 是个 list 或 None。"""
        self._sink = sink

    def interceptRequest(self, info):           # noqa: N802 （Qt 回调命名）
        sink = self._sink
        if sink is None:
            return
        try:
            url = info.requestUrl().toString()
            # requestMethod() 给的是 QByteArray（不是 bytes），直接 str() 会变成
            # "b'GET'" 这种鬼东西，必须先转成 bytes 再 decode。
            method = info.requestMethod()
            try:
                method = bytes(method).decode("utf-8", "replace")
            except Exception:
                method = str(method)
            rt = info.resourceType()
            rt_name = getattr(rt, "name", None) or str(rt)
            rt_name = rt_name.replace("ResourceType", "") or "Unknown"
            sink.append({
                "url": url,
                "method": (method or "GET").upper(),
                "resource_type": rt_name,
                # QtWebEngine 的拦截接口不暴露 POST body（没有这个 API），
                # 这里保留字段：能拿到就给，拿不到就空串。
                "body": "",
                "first_party": info.firstPartyUrl().toString(),
            })
        except Exception:
            pass                                # 嗅探失败绝不影响正常加载


def _install_sniffer():
    """把拦截器装上默认 Profile（只装一次）。

    若本模块是在 QApplication 之后才被导入的（``_APP_EXISTED_AT_IMPORT``），
    装拦截器会让进程崩溃，所以直接放弃抓包（返回 None），渲染仍可用。
    """
    global _SNIFFER, _SNIFFER_READY
    if _SNIFFER is not None or _SNIFFER_READY or not engine_available():
        return _SNIFFER
    _SNIFFER_READY = True
    if _APP_EXISTED_AT_IMPORT:
        return None                             # 导入太晚，不冒这个险
    try:
        from PyQt6.QtWebEngineCore import QWebEngineProfile
        sniffer = _Sniffer()
        QWebEngineProfile.defaultProfile().setUrlRequestInterceptor(sniffer)
        _SNIFFER = sniffer
    except Exception:
        _SNIFFER = None
    return _SNIFFER


# --------------------------------------------------------------------------
# 主流程：离屏真实渲染
# --------------------------------------------------------------------------
def _render_on_main(url, wait_ms, timeout, sniff):
    """在 Qt 主线程里真实加载 URL 并取回结果（内部使用，勿直接调用）。"""
    from PyQt6.QtCore import QUrl, QTimer, QEventLoop

    # 注意顺序：拦截器必须**先装、后建 view**。默认 Profile 一旦已经有页面/渲染进程
    # 跑起来，再往上挂拦截器会把进程直接搞崩（实测 exit 127）。
    sink = [] if sniff else None
    sniffer = _install_sniffer() if sniff else None
    if sniffer is not None:
        sniffer.set_sink(sink)

    view = _WEBENGINE_VIEW()
    view.resize(1280, 900)                      # 给个像样的视口，别用默认的 0x0
    try:
        state = {"finished": False, "ok": False, "timed_out": False}

        def _on_load(ok):
            state["finished"] = True
            state["ok"] = bool(ok)
            # 加载完成后再多等一会儿，让 XHR / 懒加载把内容补上。
            QTimer.singleShot(max(0, int(wait_ms)), loop.quit)

        loop = QEventLoop()
        view.loadFinished.connect(_on_load)

        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: (state.__setitem__("timed_out", True),
                                          loop.quit()))
        watchdog.start(int(max(1, timeout) * 1000))
        view.load(QUrl(url))
        loop.exec()                             # 等待：加载完成 + wait_ms，或超时

        # 取渲染后的完整 DOM（toHtml 是异步回调，同样要带超时）
        html_box = {"html": ""}
        loop2 = QEventLoop()

        def _got(html):
            html_box["html"] = html or ""
            loop2.quit()

        view.page().toHtml(_got)
        QTimer.singleShot(8000, loop2.quit)
        loop2.exec()

        reqs = _filter_by_host(sink or [], url)
        try:
            final_url = view.url().toString() or url
        except Exception:
            final_url = url
        try:
            title = view.title() or ""
        except Exception:
            title = ""

        warn = ""
        if state["timed_out"]:
            warn = f"加载超过 {timeout}s 未完成，返回的是已渲染到的部分内容"
        if sniff and sniffer is None:
            warn = (warn + "；" if warn else "") + \
                "本次未抓到网络请求（浏览器内核导入晚于主程序，本进程只提供渲染结果）"

        return {
            "url": url,
            "final_url": final_url,
            "status": 200 if state["ok"] else 0,
            "title": title,
            "html": html_box["html"],
            "requests": reqs,
            "engine": "webengine",
            "warn": warn,
            "loaded": state["ok"],
        }
    finally:
        if sniffer is not None:
            sniffer.set_sink(None)               # 抓完就停，别记用户自己的浏览
        try:
            view.deleteLater()
        except Exception:
            pass


def _host_of(u):
    try:
        from urllib.parse import urlparse
        return (urlparse(str(u)).netloc or "").lower()
    except Exception:
        return ""


def _same_site(u, base):
    """u 的域名是不是 base（含子域）。"""
    if not u:
        return False
    h = _host_of(u).split(":")[0]
    return h == base or h.endswith("." + base)


def _filter_by_host(requests, target_url):
    """只保留与目标站点相关的请求。

    默认 Profile 是全局的，用户这时可能也在内嵌浏览器里点网页；用目标域名过滤掉
    无关请求（跨域 XHR 的 first_party 仍是目标页，所以不会被误杀）。
    """
    base = _host_of(target_url).split(":")[0]
    if not base:
        return list(requests)
    out = []
    for r in requests:
        if _same_site(str(r.get("url") or ""), base) or \
                _same_site(str(r.get("first_party") or ""), base):
            out.append(r)
    return out


# --------------------------------------------------------------------------
# 降级：静态抓取（WebEngine 不可用时）
# --------------------------------------------------------------------------
def _static_fetch(url, timeout):
    """回落抓取：普通 HTTP GET，拿不到 JS 渲染后的内容。"""
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36"),
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        r = s.get(url, timeout=int(timeout), allow_redirects=True)
        try:
            enc = r.apparent_encoding or "utf-8"
            html = r.content.decode(enc, "replace")
        except Exception:
            html = r.content.decode("utf-8", "replace")
        return {
            "url": url,
            "final_url": r.url,
            "status": r.status_code,
            "title": "",
            "html": html,
            "requests": [],
            "engine": "urllib",
            "warn": "未渲染 JS，动态页面可能拿不到内容",
            "loaded": True,
        }
    except Exception as e:
        return {
            "url": url,
            "final_url": url,
            "status": 0,
            "title": "",
            "html": "",
            "requests": [],
            "engine": "urllib",
            "warn": f"静态抓取也失败了：{type(e).__name__}: {e}",
            "loaded": False,
        }


def _blank(url, warn):
    return {
        "url": url, "final_url": url, "status": 0, "title": "",
        "html": "", "requests": [], "engine": "urllib",
        "warn": warn, "loaded": False,
    }


# --------------------------------------------------------------------------
# 对外 API
# --------------------------------------------------------------------------
def fetch_rendered(url, *, wait_ms=2500, timeout=30, sniff=True):
    """用真实浏览器内核打开 ``url``，等 JS 渲染完，取回 DOM 与页面发出的请求。

    返回 dict：
        {"url", "final_url", "status", "title", "html", "text",
         "requests": [...], "xhr": [...], "engine": "webengine"|"urllib", "warn"}

    - ``html`` 是渲染后的完整页面（``document.documentElement.outerHTML``）；
    - ``text`` 是 ``html`` 去掉 script/style/nav/header/footer 后的可读正文；
    - ``requests`` 是页面发出的全部请求（URL / method / resourceType / body 摘要）；
    - ``xhr`` 是其中疑似"数据接口"的那批（见 :func:`find_api_requests`）。
    """
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return _blank(url, "url 必须以 http:// 或 https:// 开头")

    res = None
    if engine_available():
        try:
            with _LOCK:
                res = _Dispatcher.call(
                    lambda: _render_on_main(url, wait_ms, timeout, sniff),
                    timeout=float(timeout) + 8.0,
                )
        except Exception as e:
            res = None
            _degrade_reason = f"{type(e).__name__}: {e}"
        else:
            _degrade_reason = ""
    else:
        _degrade_reason = "未安装浏览器内核（PyQt6-WebEngine）"

    if res is None:
        res = _static_fetch(url, timeout)
        res["warn"] = (f"浏览器内核不可用（{_degrade_reason}），"
                       f"已降级为静态抓取：{res.get('warn') or ''}").strip()

    res.setdefault("xhr", [])
    try:
        res["text"] = extract_text(res.get("html") or "")
    except Exception:
        res["text"] = ""
    try:
        res["xhr"] = find_api_requests(res.get("requests") or [])
    except Exception:
        res["xhr"] = []
    return res


def extract_text(html):
    """HTML -> 可读正文：去掉 script/style/nav/header/footer，压掉多余空白。"""
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "header",
                         "footer", "svg", "iframe", "template"]):
            tag.decompose()
        raw = soup.get_text("\n")
    except Exception:
        # 没装 bs4 时的兜底：先把这些标签整段抠掉，再剥掉剩下的标签。
        raw = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|svg)\b.*?</\1>",
                     " ", html)
        raw = re.sub(r"(?s)<[^>]+>", "\n", raw)

    lines = []
    for ln in raw.split("\n"):
        ln = re.sub(r"[\u200b\ufeff\xa0]", " ", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln:
            continue
        if lines and lines[-1] == ln:
            continue
        lines.append(ln)
    return "\n".join(lines)


# URL 里像"数据接口"的线索
_API_HINTS = re.compile(
    r"(/api/|/apis/|/v\d/|/graphql|/gql|/rest/|/rpc/|/query|/search|"
    r"/list|/data|/json|/ajax|/get|/load|/fetch|/feed|/items?)", re.I)
_API_EXT = re.compile(r"\.(json|jsonp|xml)(\?|$)", re.I)
_DATA_TYPES = {"XHR", "Xhr", "Fetch", "SubResource", "Ping", "Preflight"}


def find_api_requests(requests):
    """从嗅探到的请求里挑出疑似「数据接口」的，供模型照着复现。

    判定（命中其一即可）：XHR/Fetch 类型；URL 含 api/graphql/query/list/data 等线索；
    URL 以 .json/.jsonp 结尾。返回按「靠谱程度」排序的列表，每条带
    ``method`` / ``url`` / ``resource_type`` / ``body``（摘要）与 ``why``。
    """
    out = []
    seen = set()
    for r in requests or []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        if not url or url in seen:
            continue
        kind = str(r.get("resource_type") or "")
        why = []
        score = 0

        if kind.lower() in ("xhr", "fetch"):
            score += 5
            why.append(f"{kind} 类型")
        elif kind in _DATA_TYPES:
            score += 1
        if _API_HINTS.search(url):
            score += 3
            why.append("URL 含接口线索")
        if _API_EXT.search(url):
            score += 2
            why.append("URL 指向 json/jsonp")
        if str(r.get("method", "GET")).upper() == "POST":
            score += 1
            why.append("POST 提交")
        if not why:
            continue

        seen.add(url)
        body = str(r.get("body") or "")
        if len(body) > 300:
            body = body[:300] + "…"
        out.append({
            "method": str(r.get("method") or "GET").upper(),
            "url": url,
            "resource_type": kind or "Unknown",
            "body": body,
            "why": " + ".join(why),
            "score": score,
            "first_party": str(r.get("first_party") or ""),
        })
    out.sort(key=lambda x: -x["score"])
    return out
