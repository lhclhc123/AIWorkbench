# -*- coding: utf-8 -*-
"""希沃共享屏幕自动化 —— 步骤引擎（v9.23.0）。

为什么要这个模块
================
用户原话：「程序可定时在内置浏览器中自动登录主任账号，按步骤定位并操作共享
屏幕流程；主任可预先告知手势操或视频的大致位置，程序据此自动定位并播放，
播放结束后自动结束共享。」

这里只做**纯自动化**：给一个页面对象 + 一份配置，把「登录 → 开共享 → 找媒体 →
播放 → 等结束 → 关共享」这条链路走完。配置的读写、加密、UI 在别处
（``app/seewo.py`` / ``app/gui/seewo_settings.py``）。

自动化通道的选择（重要，改动前先读）
====================================
**最终走的是 Qt 侧 ``QWebEnginePage.runJavaScript``，不是 CDP，也不是 pyautogui。**

三条候选方案与取舍：

1. ``pyautogui`` 点屏幕坐标 —— **否掉**。分辨率/缩放/窗口遮挡一变就点错，
   而且会把鼠标抢走，用户没法同时干别的。这是用户最反感的"自动化"。
2. CDP（``http://127.0.0.1:9333/json`` + WebSocket）—— **否掉**。
   ``app/webengine.py:95`` 确实开了端口，但：
     · 只有 ``main.py`` 在 QApplication 之前调用过 ``enable_remote_debugging()``
       才有效；探针脚本、离屏测试、别的入口都不一定调。
     · ``/json`` 返回的是**整个进程所有 target**，得自己按 url 挑对那一个，
       挑错了就是在别的页面上瞎点（比点错坐标更隐蔽）。
     · 多一个 WebSocket 帧编解码/心跳/重连的活，全是可失败点。
3. **``page.runJavaScript(js, cb)``** —— **选它**。理由：
     · 页面对象是我们自己传进来的，不存在"挑错 target"；
     · 离屏（``QT_QPA_PLATFORM=offscreen``）下照样能跑，能写可执行的自动化测试；
     · 只依赖 PyQt6，无额外依赖、无端口、无防火墙问题；
     · DOM 层面 ``el.click()`` + 派发 ``pointerdown/mousedown/pointerup/mouseup``
       足以驱动绝大多数前端框架（Vue/React 都监听这些冒泡事件）。
   代价：不是"受信任输入"（``isTrusted=false``），极少数站点会校验。这类站点
   在报告里明确列为限制，不假装能过。

线程模型
--------
``runJavaScript`` 只能在页面所属线程（Qt 主线程）调用，而整个流程跑在
``QThread`` 里（UI 不能卡）。所以 :class:`PageBridge` 把每次求值**调度到页面
线程并同步等结果**（复用 ``app.webengine._Dispatcher``，它自带超时，
绝不永久阻塞）。每次求值都是毫秒级，不会卡住主线程；长流程靠轮询推进，
而不是让主线程干等。

所有对外函数都**不抛异常**（内部异常转成 ``{"ok": False, "error": ...}``），
因为它们的调用方是定时任务和 UI，不能因为页面抽风就把程序带崩。
"""
import json
import os
import re
import threading
import time

# ---------------------------------------------------------------------------
# JS 公共工具（每段求值都会带上，保证任何一步都能独立工作）
# ---------------------------------------------------------------------------
_JS_HELPERS = r"""
  function __norm(s){return String(s==null?'':s).replace(/[\s\u00a0\u3000]+/g,' ').trim();}
  function __low(s){return __norm(s).toLowerCase();}
  function __txt(e){
    if(!e) return '';
    var t='';
    try{ t=(e.innerText!=null? e.innerText : (e.textContent||'')); }catch(_){ t=''; }
    if(!__norm(t)){
      t=e.value||e.getAttribute('aria-label')||e.getAttribute('title')||'';
    }
    return __norm(t);
  }
  function __vis(e){
    try{
      if(!e) return false;
      var r=e.getBoundingClientRect();
      if(r.width>0&&r.height>0) return true;
      return !!(e.getClientRects&&e.getClientRects().length);
    }catch(_){ return false; }
  }
  function __byCss(s){ try{ return document.querySelector(s); }catch(_){ return null; } }
  function __byXpath(x){
    try{ var r=document.evaluate(x,document,null,9,null); return (r&&r.singleNodeValue)||null; }
    catch(_){ return null; }
  }
  function __byText(t){
    var want=__low(t); if(!want) return null;
    var sel='a,button,label,li,td,th,span,div,p,h1,h2,h3,h4,h5,h6,input,textarea,select,option,img,i,b,strong,em';
    var all=document.querySelectorAll(sel);
    var n=Math.min(all.length,4000);
    var best=null,bestLen=0;
    for(var i=0;i<n;i++){
      var e=all[i], tx=__low(__txt(e));
      if(!tx||tx.indexOf(want)<0) continue;
      if(tx.length===want.length&&__vis(e)) return e;
      if(!best||tx.length<bestLen){best=e;bestLen=tx.length;}
    }
    return best;
  }
  function __find(c){
    c=c||{};
    if(c.css){var e=__byCss(c.css); if(e) return e;}
    if(c.xpath){var e2=__byXpath(c.xpath); if(e2) return e2;}
    if(c.text){var e3=__byText(c.text); if(e3) return e3;}
    return null;
  }
  function __fire(e,type){
    try{
      var r=e.getBoundingClientRect();
      var o={bubbles:true,cancelable:true,view:window,
             clientX:r.left+r.width/2,clientY:r.top+r.height/2};
      var C=(type.indexOf('pointer')===0&&window.PointerEvent)?PointerEvent:MouseEvent;
      e.dispatchEvent(new C(type,o));
    }catch(_){}
  }
  function __click(e){
    if(!e) return false;
    try{ e.scrollIntoView({block:'center',inline:'center'}); }catch(_){}
    __fire(e,'pointerdown'); __fire(e,'mousedown');
    __fire(e,'pointerup');   __fire(e,'mouseup');
    try{ e.click(); }catch(_){ __fire(e,'click'); }
    return true;
  }
  function __setValue(e,v){
    if(!e) return false;
    try{ e.focus(); }catch(_){}
    var ok=false;
    try{
      if('value' in e){
        var proto=Object.getPrototypeOf(e)||e;
        var d=Object.getOwnPropertyDescriptor(proto,'value');
        /* React 会在原型上装自己的 value setter，直接 e.value= 它收不到，
           必须走原型上的原生 setter —— 这是这类自动化最常见的失败点。 */
        if(d&&d.set){ d.set.call(e,v); } else { e.value=v; }
        ok=true;
      }
    }catch(_){ try{ e.value=v; ok=true; }catch(__){} }
    if(!ok){ try{ e.textContent=v; ok=true; }catch(_){} }
    try{ e.dispatchEvent(new Event('input',{bubbles:true})); }catch(_){}
    try{ e.dispatchEvent(new Event('change',{bubbles:true})); }catch(_){}
    try{ e.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true})); }catch(_){}
    try{ e.dispatchEvent(new Event('blur')); }catch(_){}
    return ok;
  }
"""


def _wrap(body, helpers=True):
    """把一段 JS 函数体包成统一返回结构 ``{ok, value, error}``。

    ``body`` 必须是**函数体**（要用 ``return``）。异常在 JS 里就地捕获，
    Python 侧永远拿到一个 dict，不会因为页面报错而抛。
    """
    inner = (_JS_HELPERS + "\n" if helpers else "") + str(body or "")
    return ("(function(){try{var __r=(function(){\n" + inner +
            "\n})();return {ok:true,value:__r,error:''};}"
            "catch(e){return {ok:false,value:null,"
            "error:String((e&&e.message)||e)};}})()")


def _js_lit(obj):
    """Python 值 -> JS 字面量（``</`` 转义，防止嵌进页面时被提前截断）。"""
    s = json.dumps(obj, ensure_ascii=False)
    return s.replace("</", "<\\/")


# ---------------------------------------------------------------------------
# 页面桥：把异步的 runJavaScript 变成「同步 + 超时」
# ---------------------------------------------------------------------------
def as_page(page):
    """允许传 ``QWebEnginePage`` 或 ``QWebEngineView``（自动取 ``.page()``）。"""
    if page is None:
        return None
    if hasattr(page, "runJavaScript"):
        return page
    getter = getattr(page, "page", None)
    if callable(getter):
        try:
            cand = getter()
        except Exception:
            cand = None
        if cand is not None and hasattr(cand, "runJavaScript"):
            return cand
    return page


def _dispatcher():
    """懒加载 ``app.webengine._Dispatcher``（复用已有的主线程调度 + 超时）。"""
    try:
        from .webengine import _Dispatcher       # noqa: WPS433（故意延迟导入）
        return _Dispatcher
    except Exception:
        return None


class PageBridge:
    """页面求值的同步封装。

    ``eval(js) -> {"ok": bool, "value": any, "error": str}``，**永不抛异常**。
    """

    def __init__(self, page, timeout=10.0):
        self.page = as_page(page)
        self.timeout = float(timeout or 10.0)

    # ---------- 线程调度 ----------
    def _in_page_thread(self):
        try:
            from PyQt6.QtCore import QThread
            pt = self.page.thread()
            return pt is None or pt is QThread.currentThread()
        except Exception:
            return True

    def _eval_here(self, js, timeout):
        """在页面所属线程里求值（内部使用）。"""
        from PyQt6.QtCore import QEventLoop, QTimer

        box = {"done": False, "value": None}
        loop = QEventLoop()

        def _cb(value):
            box["value"] = value
            box["done"] = True
            try:
                loop.quit()
            except Exception:
                pass

        try:
            self.page.runJavaScript(js, _cb)
        except TypeError:
            # 个别 PyQt6 版本要显式给 worldId
            try:
                self.page.runJavaScript(js, 0, _cb)
            except Exception as e:
                return {"ok": False, "value": None,
                        "error": f"runJavaScript 失败：{type(e).__name__}: {e}"}
        except Exception as e:
            return {"ok": False, "value": None,
                    "error": f"runJavaScript 失败：{type(e).__name__}: {e}"}

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(int(max(0.05, float(timeout)) * 1000))
        if not box["done"]:
            loop.exec()
        try:
            timer.stop()
        except Exception:
            pass
        if not box["done"]:
            return {"ok": False, "value": None, "error": "执行 JS 超时"}
        val = box["value"]
        if isinstance(val, dict) and "ok" in val:
            return {"ok": bool(val.get("ok")), "value": val.get("value"),
                    "error": str(val.get("error") or "")}
        return {"ok": True, "value": val, "error": ""}

    def eval(self, js, timeout=None):
        """执行一段 JS 并同步等结果。"""
        if self.page is None:
            return {"ok": False, "value": None, "error": "页面对象为空"}
        to = float(timeout if timeout is not None else self.timeout)
        try:
            if self._in_page_thread():
                return self._eval_here(js, to)
            disp = _dispatcher()
            if disp is None:
                return {"ok": False, "value": None,
                        "error": "页面不在当前线程，且拿不到 Qt 调度器"}
            try:
                return disp.call(lambda: self._eval_here(js, to), to + 3.0)
            except Exception as e:
                return {"ok": False, "value": None,
                        "error": f"跨线程调度失败：{type(e).__name__}: {e}"}
        except Exception as e:                    # 最后一道保险，绝不外抛
            return {"ok": False, "value": None,
                    "error": f"{type(e).__name__}: {e}"}

    def call(self, body, timeout=None):
        """执行一段函数体（自带工具函数），返回值见 :meth:`eval`。"""
        return self.eval(_wrap(body), timeout=timeout)

    # ---------- 便捷方法 ----------
    def title(self):
        r = self.call("return document.title||'';", timeout=5)
        return str(r.get("value") or "") if r.get("ok") else ""

    def url(self):
        r = self.call("return location.href||'';", timeout=5)
        return str(r.get("value") or "") if r.get("ok") else ""

    def ready(self):
        r = self.call("return document.readyState||'';", timeout=5)
        return str(r.get("value") or "") if r.get("ok") else ""

    def snapshot(self):
        """失败时给主任看的现场：标题 + 正文前 200 字。"""
        r = self.call(
            "return {title:document.title||'',"
            "body:((document.body&&document.body.innerText)||'').slice(0,200)};",
            timeout=5)
        v = r.get("value") if r.get("ok") else None
        if not isinstance(v, dict):
            return {"title": "", "body": "", "url": ""}
        return {"title": str(v.get("title") or ""),
                "body": str(v.get("body") or ""),
                "url": self.url()}


_BRIDGES = {}
_BRIDGES_LOCK = threading.Lock()


def page_bridge(page, timeout=10.0):
    """按页面对象缓存一个 :class:`PageBridge`（避免每次重建）。"""
    if isinstance(page, PageBridge):
        return page
    p = as_page(page)
    key = id(p)
    with _BRIDGES_LOCK:
        hit = _BRIDGES.get(key)
        if hit is not None and hit[0] is p:
            return hit[1]
        br = PageBridge(p, timeout=timeout)
        if len(_BRIDGES) > 16:
            _BRIDGES.clear()
        _BRIDGES[key] = (p, br)
        return br


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _sleep(sec, stop_event=None):
    """可被 ``stop_event`` 打断的 sleep。"""
    s = float(sec)
    if s <= 0:
        return
    if stop_event is not None:
        try:
            stop_event.wait(s)
            return
        except Exception:
            pass
    time.sleep(s)


def _stopped(stop_event):
    try:
        return bool(stop_event is not None and stop_event.is_set())
    except Exception:
        return False


def _now_ms():
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# 定位策略：CSS -> XPath -> 文本
# ---------------------------------------------------------------------------
def _xpath_literal(text):
    """把 Python 字符串变成安全的 XPath 字面量（处理引号混用）。"""
    s = str(text or "")
    if '"' not in s:
        return '"%s"' % s
    if "'" not in s:
        return "'%s'" % s
    parts = s.split('"')
    pieces = []
    for i, p in enumerate(parts):
        if p:
            pieces.append('"%s"' % p)
        if i != len(parts) - 1:
            pieces.append("'\"'")
    return "concat(%s)" % ", ".join(pieces)


def _locator(selector):
    """把用户填的选择器拆成 ``{css, xpath, text}`` 三种找法。

    支持的写法：
      · ``#id`` / ``.cls`` / ``div > a``      -> CSS
      · ``//div[@id="x"]`` / ``(//a)[1]``     -> XPath
      · ``xpath=...`` / ``text=...``          -> 显式指定
      · 含中文字符的裸字符串                  -> 先当 CSS 试，失败再按文本找
    """
    s = str(selector or "").strip()
    out = {"css": "", "xpath": "", "text": ""}
    if not s:
        return out
    low = s.lower()
    if low.startswith("xpath="):
        out["xpath"] = s[6:].strip()
        return out
    if low.startswith("text="):
        t = s[5:].strip()
        out["text"] = t
        out["xpath"] = '//*[contains(text(),%s)]' % _xpath_literal(t)
        return out
    if s.startswith("/") or s.startswith("./") or s.startswith("("):
        out["xpath"] = s
        return out
    out["css"] = s
    # 含中日韩字符的裸串，几乎不可能是合法 CSS —— 追加文本兜底
    if re.search(r"[\u4e00-\u9fff\u3000-\u303f]", s):
        out["text"] = s
        out["xpath"] = '//*[contains(text(),%s)]' % _xpath_literal(s)
    return out


def _find_js(selector):
    return ("var __c=" + _js_lit(_locator(selector)) + ";"
            "var __e=__find(__c);\n")


# ---------------------------------------------------------------------------
# 步骤引擎
# ---------------------------------------------------------------------------
ACTIONS = ("click", "type", "wait", "wait_gone", "js", "sleep")

STAGE_LABELS = {
    "login": "登录",
    "open_share": "打开共享",
    "resolve_media": "定位媒体",
    "play": "播放",
    "wait_end": "等待播放结束",
    "stop_share": "结束共享",
    "done": "完成",
}


def _log_line(log, on_log, entry):
    log.append(entry)
    if on_log is not None:
        try:
            on_log(entry)
        except Exception:
            pass


def run_steps(page, steps, stop_event=None, on_log=None,
              default_timeout=15.0):
    """按顺序执行步骤列表。

    返回 ``{"ok", "log", "failed_at"}``：
      · ``failed_at`` 是**失败步骤的下标**（0 起），成功时为 ``None``；
      · 失败时 ``log[-1]`` 里带 ``title`` / ``body`` 现场，方便主任自己看出卡哪；
      · ``stop_event`` 每 200ms 检查一次，可随时中断（``ok=False``，
        ``failed_at`` 指向被打断的那一步）。

    步骤字段：``action`` / ``selector`` / ``value`` / ``timeout``。
    """
    br = page_bridge(page)
    log = []
    for idx, step in enumerate(list(steps or [])):
        if _stopped(stop_event):
            _log_line(log, on_log, {"i": idx, "ok": False, "ms": 0,
                                    "detail": "已停止",
                                    "action": str((step or {}).get("action"))})
            return {"ok": False, "log": log, "failed_at": idx,
                    "error": "已停止"}
        step = step if isinstance(step, dict) else {}
        action = str(step.get("action") or "").strip().lower()
        selector = str(step.get("selector") or "").strip()
        value = step.get("value")
        value = "" if value is None else str(value)
        try:
            timeout = float(step.get("timeout") or default_timeout)
        except Exception:
            timeout = float(default_timeout)
        timeout = max(0.2, min(3600.0, timeout))

        t0 = _now_ms()
        ok, detail, snapshot = False, "", None
        try:
            if action == "sleep":
                sec = value or step.get("timeout") or 1
                try:
                    sec = float(sec)
                except Exception:
                    sec = 1.0
                _sleep(max(0.0, min(3600.0, sec)), stop_event)
                ok = not _stopped(stop_event)
                detail = f"等待 {sec:g} 秒"

            elif action == "wait":
                ok, detail = _wait_for(br, selector, stop_event, timeout)
                if not ok:
                    snapshot = br.snapshot()

            elif action == "wait_gone":
                ok, detail = _wait_gone(br, selector, stop_event, timeout)
                if not ok:
                    snapshot = br.snapshot()

            elif action == "click":
                ok, detail = _do_click(br, selector, stop_event, timeout)
                if not ok:
                    snapshot = br.snapshot()

            elif action == "type":
                ok, detail = _do_type(br, selector, value, stop_event, timeout)
                if not ok:
                    snapshot = br.snapshot()

            elif action == "js":
                r = br.call("return (function(){\n" + value + "\n})();",
                            timeout=timeout)
                ok = bool(r.get("ok"))
                detail = ("JS 执行成功" if ok else
                          f"JS 出错：{r.get('error')}")
                rv = r.get("value")
                if ok and rv is not None:
                    try:
                        detail += " · 返回：" + json.dumps(
                            rv, ensure_ascii=False)[:160]
                    except Exception:
                        detail += " · 返回：" + str(rv)[:160]

            else:
                ok = False
                detail = (f"不认识的步骤动作「{action}」"
                          f"（可用 {'/'.join(ACTIONS)}）")
        except Exception as e:                  # 单步异常不该炸掉整条流程
            ok = False
            detail = f"{type(e).__name__}: {e}"
            try:
                snapshot = br.snapshot()
            except Exception:
                snapshot = None

        entry = {"i": idx, "action": action, "selector": selector,
                 "ok": bool(ok), "detail": detail, "ms": _now_ms() - t0}
        if snapshot:
            entry["title"] = snapshot.get("title", "")
            entry["body"] = snapshot.get("body", "")
            entry["url"] = snapshot.get("url", "")
        _log_line(log, on_log, entry)
        if not ok:
            return {"ok": False, "log": log, "failed_at": idx,
                    "error": detail, "snapshot": snapshot or {}}
    return {"ok": True, "log": log, "failed_at": None, "error": ""}


def _wait_for(br, selector, stop_event, timeout):
    """等元素出现。返回 ``(ok, detail)``。"""
    if not str(selector or "").strip():
        return False, "wait 步骤缺 selector"
    js = _wrap(_find_js(selector) + "return __e?1:0;")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _stopped(stop_event):
            return False, "已停止"
        r = br.eval(js, timeout=min(5.0, max(1.0, timeout)))
        if r.get("ok") and r.get("value"):
            return True, f"元素已出现（{int(timeout - (deadline - time.time()))}s 内）"
        _sleep(0.2, stop_event)
    return False, f"等 {timeout:g} 秒仍未出现：{selector}"


def _wait_visible(br, selector, stop_event, timeout):
    """等元素出现**并且可见**。

    为什么要和 :func:`_wait_for` 分开：很多站点把「已登录」区域或「共享中」
    横幅用 ``display:none`` 藏着，DOM 里一直存在。只判断「存在」会得到
    「一直已登录 / 一直没结束」这种致命误判。
    """
    if not str(selector or "").strip():
        return False, "缺 selector"
    js = _wrap(_find_js(selector) + "return (__e&&__vis(__e))?1:0;")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _stopped(stop_event):
            return False, "已停止"
        r = br.eval(js, timeout=min(5.0, max(1.0, timeout)))
        if r.get("ok") and r.get("value"):
            return True, "元素已出现且可见"
        _sleep(0.2, stop_event)
    return False, f"等 {timeout:g} 秒仍未出现（或一直不可见）：{selector}"


def _wait_gone(br, selector, stop_event, timeout):
    """等元素消失。返回 ``(ok, detail)``。

    「消失」= **DOM 里没有 或 已经不可见（display:none / 0 尺寸）**。
    只判断 DOM 是否存在会漏掉「隐藏式结束」的站点（本地夹具就是这么写的）。
    """
    if not str(selector or "").strip():
        return False, "wait_gone 步骤缺 selector"
    js = _wrap(_find_js(selector) + "return (__e&&__vis(__e))?1:0;")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _stopped(stop_event):
            return False, "已停止"
        r = br.eval(js, timeout=min(5.0, max(1.0, timeout)))
        if r.get("ok") and not r.get("value"):
            return True, "元素已消失（或已不可见）"
        _sleep(0.2, stop_event)
    return False, f"等 {timeout:g} 秒仍未消失：{selector}"


def _do_click(br, selector, stop_event, timeout):
    """等元素出现 -> scrollIntoView -> 派发鼠标事件 + click()。"""
    if not str(selector or "").strip():
        return False, "click 步骤缺 selector"
    ok, detail = _wait_for(br, selector, stop_event, timeout)
    if not ok:
        return False, detail
    js = _wrap(_find_js(selector) +
               "if(!__e) return {clicked:false,why:'元素已消失'};\n"
               "var __r=__click(__e);\n"
               "return {clicked:!!__r,tag:(__e.tagName||'').toLowerCase(),"
               "text:__txt(__e).slice(0,60),vis:__vis(__e)};")
    r = br.eval(js, timeout=min(10.0, max(2.0, timeout)))
    if not r.get("ok"):
        return False, f"点击出错：{r.get('error')}"
    v = r.get("value") or {}
    if not v.get("clicked"):
        return False, f"点击失败：{v.get('why') or '元素不可点'}"
    return True, f"已点击 <{v.get('tag')}>「{v.get('text')}」"


def _click_if_visible(br, selector, stop_event, timeout, required=False):
    """等元素**可见**再点。``required=False`` 时超时就放弃（返回 ok=True）。

    用途：确认弹窗。有些站点把「确定」按钮常驻在 DOM 里但一直 ``display:none``，
    盲点会触发意料之外的副作用（本地夹具里就复现过：点结束共享后又把这个共享
    重新打开了）。所以「没弹出来就别点」。
    """
    if not str(selector or "").strip():
        return True, "未配置"
    ok, _detail = _wait_visible(br, selector, stop_event, timeout)
    if not ok:
        if required:
            return False, f"等 {timeout:g} 秒确认按钮也没出现：{selector}"
        return True, "确认按钮没出现，跳过"
    return _do_click(br, selector, stop_event, min(10.0, timeout))


def _do_type(br, selector, value, stop_event, timeout):
    """等输入框出现 -> focus -> 写入 -> 派发 input/change（React/Vue 必需）。"""
    if not str(selector or "").strip():
        return False, "type 步骤缺 selector"
    ok, detail = _wait_for(br, selector, stop_event, timeout)
    if not ok:
        return False, detail
    js = _wrap(_find_js(selector) +
               "if(!__e) return {typed:false,why:'元素已消失'};\n"
               "var __v=" + _js_lit(value) + ";\n"
               "var __ok=__setValue(__e,__v);\n"
               "return {typed:!!__ok,len:String(__v).length,"
               "value:String('value' in __e? __e.value : (__e.textContent||'')).slice(0,40)};")
    r = br.eval(js, timeout=min(10.0, max(2.0, timeout)))
    if not r.get("ok"):
        return False, f"输入出错：{r.get('error')}"
    v = r.get("value") or {}
    if not v.get("typed"):
        return False, f"输入失败：{v.get('why') or '元素不可写'}"
    return True, f"已输入 {v.get('len')} 个字符"


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
def navigate(page, url, stop_event=None, timeout=30.0):
    """用页面内跳转（``location.href``）打开地址并等 ``readyState=complete``。

    为什么不用 ``QWebEnginePage.setUrl``：那必须回到页面线程，还要接
    ``loadFinished`` 信号；而 ``location.href`` 在 JS 侧一句就够，且跨域跳转
    后新的文档照样能继续 ``runJavaScript``。
    """
    br = page_bridge(page)
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "地址为空"}
    cur = br.url()
    if cur and cur.split("#")[0] == url.split("#")[0]:
        return {"ok": True, "url": cur, "detail": "已经在目标页"}
    r = br.call("try{location.href=%s;return 1;}catch(e){return 0;}"
                % _js_lit(url), timeout=8)
    if not r.get("ok") or not r.get("value"):
        return {"ok": False, "error": f"跳转失败：{r.get('error') or '被拒绝'}"}
    _sleep(0.8, stop_event)          # 给新文档一点启动时间
    deadline = time.time() + max(3.0, timeout)
    while time.time() < deadline:
        if _stopped(stop_event):
            return {"ok": False, "error": "已停止"}
        if br.ready() == "complete":
            _sleep(0.4, stop_event)  # 再给 SPA 一次渲染机会
            return {"ok": True, "url": br.url(), "detail": "已打开"}
        _sleep(0.3, stop_event)
    return {"ok": False, "error": f"打开 {url} 超时（{timeout:g}s）"}


def ensure_logged_in(page, cfg, stop_event=None, user=None, password=None,
                     timeout=60.0):
    """确保已登录。已经登录（``logged_in_sel`` 存在）就直接返回 True。

    ``user`` / ``password`` 只从内存传入（由 ``app/seewo.py`` 解密后提供），
    **绝不写日志、绝不落盘**。
    """
    br = page_bridge(page)
    login = dict((cfg or {}).get("login") or {})
    logged_sel = str(login.get("logged_in_sel") or "").strip()
    user_sel = str(login.get("user_sel") or "").strip()
    pass_sel = str(login.get("pass_sel") or "").strip()
    submit_sel = str(login.get("submit_sel") or "").strip()

    # 1) 已经登录？（必须**可见**才算，隐藏的占位元素不算）
    if logged_sel:
        ok, _ = _wait_visible(br, logged_sel, stop_event, min(5.0, timeout))
        if ok:
            return True

    # 2) 没配账号选择器 = 这个站点不需要登录（或用户打算手点）
    if not user_sel:
        return True

    if not user or not password:
        raise ValueError("未配置希沃账号或密码（设置面板里填一次即可）")

    # 3) 先跳登录页
    login_url = str(login.get("url") or "").strip()
    if login_url:
        nv = navigate(page, login_url, stop_event, timeout=min(30.0, timeout))
        if not nv.get("ok"):
            raise RuntimeError(f"打开登录页失败：{nv.get('error')}")

    # 4) 填账号
    ok, detail = _do_type(br, user_sel, str(user), stop_event,
                          min(15.0, timeout))
    if not ok:
        raise RuntimeError(f"填账号失败：{detail}")
    # 5) 填密码（先 focus 再写，事件由 __setValue 统一派发）
    if pass_sel:
        ok, detail = _do_type(br, pass_sel, str(password), stop_event,
                              min(15.0, timeout))
        if not ok:
            raise RuntimeError(f"填密码失败：{detail}")

    # 6) 提交
    if submit_sel:
        ok, detail = _do_click(br, submit_sel, stop_event, min(15.0, timeout))
        if not ok:
            raise RuntimeError(f"点登录按钮失败：{detail}")
    else:
        # 没配提交按钮就按回车
        js = _wrap(_find_js(user_sel) +
                   "if(!__e) return 0;\n"
                   "['keydown','keypress','keyup'].forEach(function(t){"
                   "try{__e.dispatchEvent(new KeyboardEvent(t,{bubbles:true,"
                   "key:'Enter',code:'Enter',keyCode:13,which:13}));}catch(_){}});\n"
                   "var __f=__e.form;if(__f){try{__f.submit();}catch(_){}}\n"
                   "return 1;")
        br.eval(js, timeout=8)

    # 7) 等登录成功标志（同样要可见）
    if logged_sel:
        ok, detail = _wait_visible(br, logged_sel, stop_event, timeout)
        if not ok:
            snap = br.snapshot()
            raise RuntimeError(
                f"登录后 {timeout:g} 秒内没等到「已登录」标志"
                f"（logged_in_sel={logged_sel}）。"
                f"当前页面标题：{snap.get('title') or '（空）'}；"
                f"正文：{(snap.get('body') or '')[:120]}")
    else:
        _sleep(2.0, stop_event)
    return True


# ---------------------------------------------------------------------------
# 媒体定位（用户最关心的部分）
# ---------------------------------------------------------------------------
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

_ORD_REV = re.compile(
    r"倒数第\s*([0-9零〇一二两三四五六七八九十]+)\s*"
    r"(?:个|段|节|首|支|集|课|条|组|块|章|号)?")
_ORD = re.compile(
    r"第\s*([0-9零〇一二两三四五六七八九十]+)\s*"
    r"(?:个|段|节|首|支|集|课|条|组|块|章|号)?")

_PUNCT = "《》<>〈〉\"'“”‘’「」『』()（）[]【】,，.。、!！?？:：;；~～-—_/\\|"

# 只是口气/量词，不该拿来和列表项做匹配
_FILLERS = ("那一个", "这一个", "那一段", "这一段", "那个", "这个",
            "那首", "这首", "那支", "这支", "一个", "一首", "一段",
            "一支", "一节", "一条", "一遍",
            "视频", "音频", "片段", "片子", "文件", "内容", "节目",
            "歌曲", "的歌", "的操")


def _cn_to_int(text):
    """``"三"`` / ``"十一"`` / ``"3"`` -> int；认不出来返回 None。"""
    s = str(text or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        tens = _CN_DIGIT.get(a, 1) if a else 1
        ones = _CN_DIGIT.get(b, 0) if b else 0
        return tens * 10 + ones
    if len(s) == 1 and s in _CN_DIGIT:
        return _CN_DIGIT[s]
    return None


def _clean_keyword(text):
    """去掉标点、量词、口气词，留下能和条目文本比对的关键词。"""
    t = str(text or "").translate(_FULLWIDTH_DIGITS)
    for ch in _PUNCT:
        t = t.replace(ch, " ")
    t = re.sub(r"\s+", " ", t).strip()
    changed = True
    while changed and t:
        changed = False
        for f in _FILLERS:
            if t == f:
                t = ""
                changed = True
                break
            if t.endswith(f):
                t = t[:-len(f)].strip()
                changed = True
                break
            if t.startswith(f):
                t = t[len(f):].strip()
                changed = True
                break
    return t.strip()


def parse_media_hint(hint):
    """把主任的「人话」拆成 ``{"ordinal": int|None, "keyword": str}``。

    规则（按优先级）：
      · ``倒数第 N 个`` -> ``ordinal = -N``；``最后`` -> ``-1``；
      · ``第 N 个/段/节/首`` -> ``ordinal = N``（中文数字、阿拉伯数字都认）；
      · 剩下的词就是关键词（去掉标点与「视频/那首/这个」这类口气词）。

    例子::

        "第三个视频"        -> {"ordinal": 3,  "keyword": ""}
        "最后一个"          -> {"ordinal": -1, "keyword": ""}
        "第二段手势操"      -> {"ordinal": 2,  "keyword": "手势操"}
        "《七彩阳光》那首"  -> {"ordinal": None, "keyword": "七彩阳光"}
    """
    raw = str(hint or "").strip()
    s = raw.translate(_FULLWIDTH_DIGITS)
    ordinal = None
    m = _ORD_REV.search(s)
    if m:
        n = _cn_to_int(m.group(1))
        if n:
            ordinal = -int(n)
        s = s[:m.start()] + " " + s[m.end():]
    else:
        m = _ORD.search(s)
        if m:
            n = _cn_to_int(m.group(1))
            if n:
                ordinal = int(n)
            s = s[:m.start()] + " " + s[m.end():]
        elif "最后" in s:
            ordinal = -1
            s = s.replace("最后", " ")
        elif "倒数" in s:
            # 「倒数第十个」之类没写序号的，当作最后一个
            ordinal = -1
            s = s.replace("倒数", " ")
    return {"ordinal": ordinal, "keyword": _clean_keyword(s), "raw": raw}


def _norm_key(s):
    return re.sub(r"\s+", "", str(s or "")).lower()


def _media_items(br, media_cfg):
    """取出列表条目（索引 + 文本）。取不到返回空列表。"""
    list_sel = str((media_cfg or {}).get("list_sel") or "").strip()
    if not list_sel:
        return []
    js = _wrap("var __sel=" + _js_lit(list_sel) + ";\n"
               "var __n=[];\n"
               "try{ __n=document.querySelectorAll(__sel); }catch(e){ return []; }\n"
               "var __o=[];\n"
               "for(var i=0;i<__n.length && i<500;i++){"
               " __o.push({i:i,text:__txt(__n[i])}); }\n"
               "return __o;")
    r = br.eval(js)
    if not r.get("ok") or not isinstance(r.get("value"), list):
        return []
    out = []
    for it in r["value"]:
        if isinstance(it, dict):
            out.append({"i": int(it.get("i") or 0),
                        "text": str(it.get("text") or "")})
    return out


def _match_by_text(items, keyword):
    """在条目文本里做包含匹配（忽略大小写与空白）。命中返回下标，否则 None。"""
    kw = _norm_key(keyword)
    if not kw:
        return None
    for it in items:                       # 1) 完全相等最可靠
        if _norm_key(it["text"]) == kw:
            return it["i"]
    for it in items:                       # 2) 条目包含关键词
        if kw in _norm_key(it["text"]):
            return it["i"]
    for it in items:                       # 3) 关键词比条目长（反向包含）
        t = _norm_key(it["text"])
        if t and t in kw:
            return it["i"]
    return None


def _item_selector(tpl, index0):
    """把 ``item_sel_tpl`` 展开成具体选择器。

    支持三种占位符：``{i}`` = 1 起、``{n}`` = 0 起、``%d`` = 1 起。
    """
    s = str(tpl or "").strip()
    if not s:
        return ""
    if "%d" in s:
        try:
            return s % (int(index0) + 1)
        except Exception:
            return s
    return s.replace("{i}", str(int(index0) + 1)).replace("{n}", str(int(index0)))


def resolve_media(page, hint, cfg, stop_event=None, play=True, timeout=15.0):
    """按「人话」定位列表里的那条媒体，并按需点开播放。

    返回::

        {"ok": bool, "index": int, "label": str,
         "match": "text"|"ordinal"|"", "played": bool, "error": str}

    定位优先级：**文本优先，序数兜底**（文本更精确；「第二个」这种只有序数时
    才按位置找）。``index`` 从 0 起；找不到时是 ``-1``。
    """
    br = page_bridge(page)
    media = dict((cfg or {}).get("media") or cfg or {})
    parsed = parse_media_hint(hint)
    fail = {"ok": False, "index": -1, "label": "", "match": "",
            "played": False, "error": "", "hint": parsed}
    if _stopped(stop_event):
        fail["error"] = "已停止"
        return fail

    if not media.get("list_sel"):
        fail["error"] = ("还没配置媒体列表选择器（media.list_sel）——"
                         "用「学一次」导出页面元素后再挑")
        return fail

    items = _media_items(br, media)
    if not items:
        title = br.title()
        fail["error"] = (f"按 list_sel={media.get('list_sel')} 没找到任何条目"
                         f"（当前页面：{title or '未知'}）")
        return fail

    keyword = parsed["keyword"]
    ordinal = parsed["ordinal"]
    index = None
    match = ""

    # ① 文本优先
    if keyword:
        index = _match_by_text(items, keyword)
        if index is not None:
            match = "text"
    # ② 序数兜底
    if index is None and ordinal is not None:
        n = len(items)
        i = (n + ordinal) if ordinal < 0 else (ordinal - 1)
        if 0 <= i < n:
            index = i
            match = "ordinal"
        else:
            fail["error"] = (f"按位置「{hint}」超出范围：列表里只有 {n} 条")
            fail["label"] = ""
            return fail

    if index is None:
        fail["error"] = (f"没在列表里找到「{hint}」。现有条目：" +
                         "、".join(f"{i + 1}.{it['text'][:18]}"
                                   for i, it in enumerate(items[:8])))
        return fail

    label = items[index]["text"] if index < len(items) else ""
    out = {"ok": True, "index": int(index), "label": label, "match": match,
           "played": False, "error": "", "hint": parsed,
           "total": len(items)}
    if not play:
        return out

    p = play_media(page, media, index, stop_event, timeout=timeout)
    out["played"] = bool(p.get("ok"))
    out["play"] = p
    if not p.get("ok"):
        out["ok"] = False
        out["error"] = p.get("error") or "播放失败"
    return out


def play_media(page, media_cfg, index, stop_event=None, timeout=20.0):
    """点开第 ``index`` 条并等 ``<video>`` 真的开始播。

    ``index`` 是 0 起。返回 ``{"ok","reason","detail","state"}``。
    """
    br = page_bridge(page)
    media = dict(media_cfg or {})
    list_sel = str(media.get("list_sel") or "").strip()
    video_sel = str(media.get("video_sel") or "video").strip() or "video"
    play_sel = str(media.get("play_btn_sel") or "").strip()
    tpl = str(media.get("item_sel_tpl") or "").strip()

    item_sel = _item_selector(tpl, index)
    body = ("var __listSel=" + _js_lit(list_sel) + ";\n"
            "var __idx=" + _js_lit(int(index)) + ";\n"
            "var __itemSel=" + _js_lit(item_sel) + ";\n"
            "var __playSel=" + _js_lit(play_sel) + ";\n"
            "var __item=null;\n"
            "if(__itemSel){ try{__item=document.querySelector(__itemSel);}catch(e){} }\n"
            "if(!__item){ var __l=[]; try{__l=document.querySelectorAll(__listSel);}"
            "catch(e){} if(__l.length>__idx) __item=__l[__idx]; }\n"
            "if(!__item) return {clicked:false,why:'条目不存在'};\n"
            "try{ __item.scrollIntoView({block:'center',inline:'center'}); }catch(e){}\n"
            "/* 很多列表是「双击播放」 */\n"
            "__fire(__item,'dblclick');\n"
            "var __btn=null;\n"
            "if(__playSel){ try{ __btn=__item.querySelector(__playSel); }catch(e){} "
            "if(!__btn){ try{ __btn=document.querySelector(__playSel); }catch(e){} } }\n"
            "var __target=__btn||__item;\n"
            "var __r=__click(__target);\n"
            "return {clicked:!!__r,usedPlayBtn:!!__btn,"
            "tag:(__target.tagName||'').toLowerCase(),text:__txt(__target).slice(0,60)};")
    r = br.eval(_wrap(body), timeout=10)
    if not r.get("ok"):
        return {"ok": False, "reason": "error",
                "detail": f"点击出错：{r.get('error')}"}
    clicked = (r.get("value") or {}).get("clicked")
    if not clicked:
        return {"ok": False, "reason": "error",
                "detail": f"点不开：{(r.get('value') or {}).get('why')}"}

    state = _wait_playing(br, video_sel, stop_event, timeout)
    if state.get("playing"):
        return {"ok": True, "reason": "playing",
                "detail": f"已开始播放（currentTime={state.get('ct'):.2f}s）",
                "state": state}
    # 兜底：直接调 play() 一次
    try:
        br.eval(_wrap("var v=document.querySelector(%s);\n"
                      "if(!v) return 0;\n"
                      "try{ var p=v.play(); if(p&&p.catch) p.catch(function(){}); }"
                      "catch(e){}\nreturn 1;" % _js_lit(video_sel)), timeout=5)
    except Exception:
        pass
    state = _wait_playing(br, video_sel, stop_event, min(6.0, timeout))
    if state.get("playing"):
        return {"ok": True, "reason": "playing",
                "detail": "兜底 play() 后开始播放", "state": state}
    return {"ok": False, "reason": "not_playing",
            "detail": f"{timeout:g} 秒内视频没开始播（可能不是 <video>，"
                      f"或站点要求点全屏/授权）",
            "state": state}


def _wait_playing(br, video_sel, stop_event, timeout):
    """轮询到 ``currentTime > 0.3 && !paused``。"""
    js = _wrap("var v=document.querySelector(%s);\n"
               "if(!v) return {has:false};\n"
               "return {has:true,ct:Number(v.currentTime||0),"
               "paused:!!v.paused,dur:(isFinite(v.duration)?Number(v.duration):-1),"
               "ended:!!v.ended};" % _js_lit(video_sel))
    deadline = time.time() + max(0.5, float(timeout))
    last = {"has": False}
    while time.time() < deadline:
        if _stopped(stop_event):
            break
        r = br.eval(js, timeout=5)
        v = r.get("value") if r.get("ok") else None
        if isinstance(v, dict):
            last = v
            if v.get("has") and not v.get("paused") and float(v.get("ct") or 0) > 0.3:
                v = dict(v)
                v["playing"] = True
                return v
        _sleep(0.2, stop_event)
    out = dict(last or {})
    out["playing"] = False
    return out


# ---------------------------------------------------------------------------
# 等待播放结束
# ---------------------------------------------------------------------------
def wait_playback_end(page, stop_event, cfg, max_hours=3.0, poll=0.5,
                      stall_sec=30.0):
    """等视频播完。返回 ``{"ok", "reason", "played_sec", "detail"}``。

    ``reason`` 取值：``ended`` / ``stalled`` / ``timeout`` / ``stopped`` /
    ``error``。

    判定顺序（先到先算）：
      1. ``video.ended === true``
      2. ``duration`` 是有限正数且 ``currentTime >= duration - 0.6``
      3. 页面出现 ``ended_sel``（配了才算）
      4. 视频元素消失（播完后播放器卸载；**必须先见过它**才敢这么判）
      5. 直播流 / 未知时长（``duration`` 是 Infinity/NaN）：
         ``currentTime`` 连续 30 秒不增长且 ``paused === false`` -> ``stalled``
      6. 超过 ``max_hours`` -> ``timeout``
    """
    br = page_bridge(page)
    media = dict((cfg or {}).get("media") or cfg or {})
    video_sel = str(media.get("video_sel") or "video").strip() or "video"
    ended_sel = str(media.get("ended_sel") or "").strip()

    js = _wrap(
        "var v=null; try{v=document.querySelector(%s);}catch(e){}\n"
        "var o={has:!!v};\n"
        "if(v){o.ct=Number(v.currentTime||0);o.paused=!!v.paused;"
        "o.ended=!!v.ended;o.rs=Number(v.readyState||0);"
        "o.dur=(isFinite(Number(v.duration))?Number(v.duration):-1);"
        "o.src=String(v.currentSrc||v.src||'').slice(0,120);}\n"
        "var es=%s;\n"
        "if(es){try{o.marker=!!document.querySelector(es);}catch(e){o.marker=false;}}\n"
        "return o;" % (_js_lit(video_sel), _js_lit(ended_sel)))

    started = time.time()
    # 下限 1 秒：允许测试/短片段传很小的 max_hours，同时保证循环至少跑一轮
    deadline = started + max(1.0 / 3600.0, float(max_hours)) * 3600.0
    seen_video = False
    first_ct = None
    played_sec = 0.0
    last_ct = None
    last_ct_change = started
    STALL_SEC = max(1.0, float(stall_sec))

    while time.time() < deadline:
        if _stopped(stop_event):
            return {"ok": False, "reason": "stopped",
                    "played_sec": round(played_sec, 2),
                    "detail": "被手动停止"}
        r = br.eval(js, timeout=6)
        if not r.get("ok"):
            _sleep(poll, stop_event)
            continue
        v = r.get("value") or {}
        if not isinstance(v, dict):
            _sleep(poll, stop_event)
            continue

        has = bool(v.get("has"))
        ct = float(v.get("ct") or 0.0)
        dur = float(v.get("dur") or -1.0)
        paused = bool(v.get("paused"))
        ended = bool(v.get("ended"))
        marker = bool(v.get("marker"))

        if has:
            seen_video = True
            if first_ct is None:
                first_ct = ct
            played_sec = max(played_sec, ct)
            if last_ct is None or abs(ct - last_ct) > 1e-3:
                last_ct = ct
                last_ct_change = time.time()

        # 1) ended
        if has and ended:
            return {"ok": True, "reason": "ended",
                    "played_sec": round(played_sec, 2),
                    "detail": "video.ended = true"}
        # 2) 到达片尾
        if has and dur > 0 and ct >= dur - 0.6 and not paused:
            return {"ok": True, "reason": "ended",
                    "played_sec": round(played_sec, 2),
                    "detail": f"currentTime {ct:.2f} ≥ duration {dur:.2f} - 0.6"}
        # 3) 结束标志
        if marker:
            return {"ok": True, "reason": "ended",
                    "played_sec": round(played_sec, 2),
                    "detail": f"出现结束标志 {ended_sel}"}
        # 4) 元素消失（只在我们确实见过它之后）
        if seen_video and not has:
            return {"ok": True, "reason": "ended",
                    "played_sec": round(played_sec, 2),
                    "detail": "视频元素已从页面移除（播放器播完卸载）"}
        # 5) 直播/未知时长：卡住不动 30 秒
        if has and dur <= 0 and not paused:
            if time.time() - last_ct_change >= STALL_SEC:
                return {"ok": True, "reason": "stalled",
                        "played_sec": round(played_sec, 2),
                        "detail": f"未知时长流：currentTime 连续 "
                                  f"{int(STALL_SEC)}s 不增长，判为卡死/结束"}
        _sleep(poll, stop_event)

    return {"ok": False, "reason": "timeout",
            "played_sec": round(played_sec, 2),
            "detail": f"超过 {max_hours:g} 小时仍未结束"}


# ---------------------------------------------------------------------------
# 结束共享
# ---------------------------------------------------------------------------
def stop_share(page, cfg, stop_event=None, timeout=15.0):
    """结束共享屏幕。返回 ``{"ok", "how", "detail"}``。

    顺序：点 ``stop_share_sel`` -> 点确认按钮（配了才点）-> 校验
    ``share.started_sel`` 消失 -> 仍失败就用 JS 按文本找「结束共享/停止共享」
    再点一次。
    """
    br = page_bridge(page)
    cfg = dict(cfg or {})
    share = dict(cfg.get("share") or {})
    stop_sel = str(cfg.get("stop_share_sel") or
                   share.get("stop_sel") or "").strip()
    confirm_sel = str(share.get("confirm_sel") or "").strip()
    stop_confirm_sel = str(share.get("stop_confirm_sel") or "").strip()
    started_sel = str(share.get("started_sel") or "").strip()
    attempts = []

    def _did_it_work():
        if not started_sel:
            return None                       # 没配标志，无法校验
        ok, _ = _wait_gone(br, started_sel, stop_event, min(6.0, timeout))
        return ok

    if stop_sel:
        ok, detail = _do_click(br, stop_sel, stop_event, min(12.0, timeout))
        attempts.append({"how": "stop_share_sel", "ok": ok, "detail": detail})
        if ok:
            # 结束共享的二次确认：**配了专用选择器就点**；只配了通用 confirm_sel
            # 时，必须先看到它真的弹出来才点 —— 否则同一个选择器在别的页面上
            # 可能是「开始共享」，会把刚关掉的共享又打开。
            if stop_confirm_sel:
                ok2, d2 = _click_if_visible(br, stop_confirm_sel, stop_event,
                                            min(8.0, timeout), required=True)
                attempts.append({"how": "stop_confirm_sel", "ok": ok2,
                                 "detail": d2})
            elif confirm_sel:
                ok2, d2 = _click_if_visible(br, confirm_sel, stop_event,
                                            min(6.0, timeout), required=False)
                attempts.append({"how": "confirm_sel(if_visible)", "ok": ok2,
                                 "detail": d2})
        state = _did_it_work()
        if state is not False:
            used = "stop_share_sel"
            if stop_confirm_sel:
                used += "+stop_confirm_sel"
            elif confirm_sel:
                used += "(+confirm)"
            return {"ok": True, "how": used,
                    "detail": "已点结束共享" +
                              ("（已确认）" if stop_confirm_sel else ""),
                    "attempts": attempts}
        attempts.append({"how": "verify", "ok": False,
                         "detail": f"{started_sel} 仍然存在"})

    # 兜底：按文本找。
    # ★ 必须挑「文字最短」的那个命中元素：容器 div 的 innerText 往往也包含
    #   「结束共享」四个字（比如「共享中 结束共享」），按 DOM 顺序取第一个会点到
    #   容器上，看着像点了其实没反应 —— 这是最容易白跑一趟的坑。
    fallback = ("var __kw=['结束共享','停止共享','退出共享','结束屏幕共享','结束投屏'];\n"
                "var __all=document.querySelectorAll('a,button,div,span,li');\n"
                "var __best=null,__bl=0,__bk='';\n"
                "for(var k=0;k<__kw.length;k++){\n"
                "  for(var i=0;i<__all.length;i++){\n"
                "    var e=__all[i]; if(!__vis(e)) continue;\n"
                "    var t=__txt(e); if(!t||t.length>20) continue;\n"
                "    if(t.indexOf(__kw[k])<0) continue;\n"
                "    if(!__best||t.length<__bl){__best=e;__bl=t.length;__bk=__kw[k];}\n"
                "  }\n"
                "}\n"
                "if(!__best) return {clicked:false};\n"
                "__click(__best);\n"
                "return {clicked:true,text:__txt(__best),by:__bk};")
    r = br.eval(_wrap(fallback), timeout=8)
    v = r.get("value") or {}
    if r.get("ok") and v.get("clicked"):
        attempts.append({"how": "text_fallback", "ok": True,
                         "detail": f"按文本「{v.get('by')}」点了「{v.get('text')}」"})
        if stop_confirm_sel:
            _click_if_visible(br, stop_confirm_sel, stop_event,
                              min(6.0, timeout), required=False)
        elif confirm_sel:
            _click_if_visible(br, confirm_sel, stop_event, min(4.0, timeout),
                              required=False)
        state = _did_it_work()
        if state is not False:
            return {"ok": True, "how": "text_fallback",
                    "detail": f"按文本点了「{v.get('text')}」",
                    "attempts": attempts}
    attempts.append({"how": "text_fallback", "ok": False,
                     "detail": "页面里没找到「结束共享/停止共享」按钮"})

    return {"ok": False, "how": "none",
            "detail": "尝试过配置的选择器与文本兜底，都没能结束共享"
                      f"（当前页面：{br.title() or '未知'}）",
            "attempts": attempts}


# ---------------------------------------------------------------------------
# 一键流程
# ---------------------------------------------------------------------------
def run_share_flow(page, cfg, media_hint, stop_event=None, on_progress=None,
                   max_hours=3.0):
    """登录 -> 进共享 -> 选媒体 -> 播放 -> 等结束 -> 结束共享。

    ``on_progress(stage, detail)`` 的 ``stage`` 固定为：
    ``login / open_share / resolve_media / play / wait_end / stop_share / done``。

    返回 ``{"ok", "stage", "error", "log", "result": {...}}``；
    ``stage`` 是**失败时卡在哪一步**（成功为 ``"done"``）。
    """
    br = page_bridge(page)
    cfg = dict(cfg or {})
    log = []
    state = {"stage": "login"}

    def progress(stage, detail):
        state["stage"] = stage
        _log_line(log, None, {"stage": stage, "detail": str(detail),
                              "ts": time.time()})
        if on_progress is not None:
            try:
                on_progress(stage, str(detail))
            except Exception:
                pass

    def fail(msg):
        return {"ok": False, "stage": state["stage"], "error": str(msg),
                "log": log, "result": {}}

    try:
        # ---- 0. 打开站点 ----
        site = str(cfg.get("site_url") or "").strip()
        if site:
            cur = br.url()
            if not cur.startswith(site.split("#")[0]) and \
                    not site.startswith(cur.split("#")[0] or "\0"):
                progress("login", f"打开 {site}")
                nv = navigate(page, site, stop_event, timeout=40)
                if not nv.get("ok"):
                    return fail(f"打开站点失败：{nv.get('error')}")

        # ---- 1. 登录 ----
        progress("login", "检查登录状态")
        if _stopped(stop_event):
            return fail("已停止")
        ensure_logged_in(page, cfg, stop_event,
                         user=cfg.get("_user"), password=cfg.get("_pass"))
        progress("login", "已登录")

        # ---- 2. 打开共享 ----
        share = dict(cfg.get("share") or {})
        entry = str(share.get("entry_sel") or "").strip()
        if entry:
            progress("open_share", "点「共享屏幕」")
            ok, detail = _do_click(br, entry, stop_event, 20)
            if not ok:
                return fail(f"点共享入口失败：{detail}")
            confirm = str(share.get("confirm_sel") or "").strip()
            if confirm:
                # 只点「真的弹出来了」的确认框，避免误点常驻 DOM 的隐藏按钮
                ok, detail = _click_if_visible(br, confirm, stop_event, 15,
                                               required=False)
                if not ok:
                    return fail(f"共享确认失败：{detail}")
            started = str(share.get("started_sel") or "").strip()
            if started:
                ok, detail = _wait_visible(br, started, stop_event, 25)
                if not ok:
                    return fail(f"共享没真正开始：{detail}")
        progress("open_share", "共享已开始")

        # ---- 3. 自定义步骤（可选，跑在选媒体之前）----
        steps = [s for s in (cfg.get("steps") or []) if isinstance(s, dict)]
        if steps:
            sr = run_steps(page, steps, stop_event)
            if not sr.get("ok"):
                bad = sr.get("failed_at")
                detail = ""
                for e in reversed(sr.get("log") or []):
                    if e.get("i") == bad:
                        detail = f"{e.get('detail')}（页面标题：{e.get('title') or '未知'}）"
                        break
                return fail(f"自定义步骤第 {int(bad) + 1} 步失败：{detail}")

        # ---- 4. 定位媒体 ----
        hint = media_hint if media_hint is not None else \
            cfg.get("_media_hint") or ""
        media = dict(cfg.get("media") or {})
        if not str(hint).strip() and media.get("default_index") is not None:
            hint = str(media.get("default_index"))
        progress("resolve_media", f"按「{hint}」定位")
        rm = resolve_media(page, hint, cfg, stop_event, play=False)
        if not rm.get("ok"):
            return fail(rm.get("error") or "定位失败")

        # ---- 5. 播放 ----
        progress("play", f"播第 {rm['index'] + 1} 条：{rm.get('label')}")
        pm = play_media(page, media, rm["index"], stop_event, timeout=25)
        if not pm.get("ok"):
            return fail(pm.get("detail") or "播放失败")
        progress("play", pm.get("detail") or "已开始播放")

        # ---- 6. 等播放结束 ----
        progress("wait_end", "等待播放结束")
        we = wait_playback_end(page, stop_event, cfg, max_hours=max_hours)
        progress("wait_end", we.get("detail") or we.get("reason"))
        if we.get("reason") == "stopped":
            # 用户按了停止：仍然要把共享关掉，别留个共享在那儿不管
            sp = stop_share(page, cfg, stop_event)
            return {"ok": False, "stage": "wait_end",
                    "error": "已手动停止（已尝试结束共享）",
                    "log": log, "result": {"end": we, "stop": sp}}
        if not we.get("ok"):
            sp = stop_share(page, cfg, stop_event)
            return {"ok": False, "stage": "wait_end",
                    "error": we.get("detail") or "等待播放结束超时",
                    "log": log, "result": {"end": we, "stop": sp}}

        # ---- 7. 结束共享 ----
        progress("stop_share", "结束共享")
        sp = stop_share(page, cfg, stop_event)
        if not sp.get("ok"):
            return {"ok": False, "stage": "stop_share",
                    "error": sp.get("detail") or "结束共享失败",
                    "log": log,
                    "result": {"media": rm, "play": pm, "end": we,
                               "stop": sp}}
        progress("done", "全部完成")
        return {"ok": True, "stage": "done", "error": "", "log": log,
                "result": {"media": rm, "play": pm, "end": we, "stop": sp}}
    except Exception as e:
        return {"ok": False, "stage": state["stage"],
                "error": f"{type(e).__name__}: {e}", "log": log, "result": {}}


def summarize_result(res):
    """把 :func:`run_share_flow` 的返回值压成一行中文（写进任务历史用）。"""
    res = res or {}
    if res.get("ok"):
        r = res.get("result") or {}
        media = r.get("media") or {}
        end = r.get("end") or {}
        return (f"[成功] 希沃共享完成：播放「{media.get('label') or '?'}」"
                f"（第 {int(media.get('index', 0)) + 1} 条，"
                f"{end.get('played_sec', 0)} 秒），已结束共享")
    stage = res.get("stage") or "?"
    return f"[错误] 希沃共享在「{STAGE_LABELS.get(stage, stage)}」失败：{res.get('error')}"


# ---------------------------------------------------------------------------
# 「学一次」：导出页面元素候选清单
# ---------------------------------------------------------------------------
_PROBE_JS = r"""
return (function(){
  function norm(s){return String(s==null?'':s).replace(/[\s\u00a0\u3000]+/g,' ').trim();}
  function selOf(e){
    try{
      if(e.id && window.CSS && CSS.escape){
        var s='#'+CSS.escape(e.id);
        if(document.querySelectorAll(s).length===1) return s;
      }
    }catch(_){}
    var parts=[], n=e, depth=0;
    while(n && n.nodeType===1 && depth<5){
      var p=n.tagName.toLowerCase();
      if(n.id && window.CSS && CSS.escape){ parts.unshift('#'+CSS.escape(n.id)); break; }
      var cls=[];
      try{
        cls=(n.className&&typeof n.className==='string')
            ? n.className.trim().split(/\s+/).filter(Boolean) : [];
      }catch(_){ cls=[]; }
      if(cls.length) p += '.' + cls.slice(0,2).map(function(c){return CSS.escape(c);}).join('.');
      var par=n.parentElement;
      if(par){
        var same=[];
        for(var i=0;i<par.children.length;i++){
          if(par.children[i].tagName===n.tagName) same.push(par.children[i]);
        }
        if(same.length>1) p += ':nth-of-type('+(same.indexOf(n)+1)+')';
      }
      parts.unshift(p); n=par; depth++;
    }
    return parts.join(' > ');
  }
  var sel='a,button,input,select,textarea,label,li,td,th,span,div,p,h1,h2,h3,h4,img,i,svg';
  var all=document.querySelectorAll(sel);
  var out=[], seen={};
  for(var i=0;i<all.length && out.length<200;i++){
    var e=all[i];
    var t='';
    try{ t=norm(e.innerText!=null? e.innerText : (e.textContent||'')); }catch(_){ t=''; }
    if(!t){ t=norm(e.value||e.getAttribute('aria-label')||e.getAttribute('title')||''); }
    if(!t || t.length>80) continue;
    if(e.children.length>6) continue;      // 大容器没意义
    var s=selOf(e);
    if(!s || seen[s]) continue;
    seen[s]=1;
    var cls='';
    try{ cls=(typeof e.className==='string')? e.className : (e.getAttribute('class')||''); }catch(_){ cls=''; }
    out.push({tag:e.tagName.toLowerCase(), text:t, id:e.id||'',
              cls:norm(cls).slice(0,80), selector_guess:s,
              visible: (e.getBoundingClientRect().width>0)});
  }
  return out;
})();
"""


def probe_page(page, out_path=None):
    """导出当前页面上的可点元素候选清单（「学一次」）。

    返回 ``{"ok", "items", "path", "error"}``，``items`` 每条是
    ``{tag, text, id, cls, selector_guess, visible}``，最多 200 条。
    ``out_path`` 给了就同时落盘（JSON）。
    """
    br = page_bridge(page)
    r = br.eval("(function(){try{" + _PROBE_JS + "}catch(e){return [];}})()",
                timeout=20)
    items = r.get("value") if r.get("ok") else []
    if not isinstance(items, list):
        items = []
    clean = []
    for it in items:
        if not isinstance(it, dict):
            continue
        clean.append({
            "tag": str(it.get("tag") or ""),
            "text": str(it.get("text") or "")[:120],
            "id": str(it.get("id") or ""),
            "cls": str(it.get("cls") or ""),
            "selector_guess": str(it.get("selector_guess") or ""),
            "visible": bool(it.get("visible")),
        })
    payload = {"ok": bool(r.get("ok")), "url": br.url(), "title": br.title(),
               "ts": time.time(), "items": clean,
               "error": "" if r.get("ok") else str(r.get("error") or "")}
    if out_path:
        try:
            folder = os.path.dirname(out_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, out_path)
            payload["path"] = out_path
        except Exception as e:
            payload["error"] = f"写探针文件失败：{e}"
    return payload


# 关键词 -> 要填的字段（顺序即优先级）
_SUGGEST_RULES = (
    ("share.entry_sel", ("共享屏幕", "开始共享", "屏幕共享", "发起共享", "共享")),
    ("share.confirm_sel", ("开始共享", "确定", "确认", "立即共享")),
    ("share.started_sel", ("结束共享", "停止共享", "退出共享", "共享中")),
    ("stop_share_sel", ("结束共享", "停止共享", "退出共享")),
    ("media.play_btn_sel", ("播放", "开始播放")),
    ("login.user_sel", ("账号", "工号", "用户名", "手机号", "帐号")),
    ("login.pass_sel", ("密码",)),
    ("login.submit_sel", ("登录", "登陆", "登 录")),
    ("login.logged_in_sel", ("退出登录", "退出", "个人中心", "我的")),
)


def suggest_config(probe, cfg, overwrite=False):
    """按文本关键词从探针结果里猜一批 ``_sel`` 字段，能填多少填多少。

    ``probe`` 是 :func:`probe_page` 的返回（或它的 ``items`` 列表）。
    ``overwrite=False``（默认）只填**空字段**，不会覆盖主任已经调好的。
    """
    items = []
    if isinstance(probe, dict):
        items = probe.get("items") or []
    elif isinstance(probe, list):
        items = probe
    out = json.loads(json.dumps(cfg or {}, ensure_ascii=False))
    out.setdefault("login", {})
    out.setdefault("share", {})
    out.setdefault("media", {})

    def get(path):
        if "." in path:
            a, _, b = path.partition(".")
            return str((out.get(a) or {}).get(b) or "").strip()
        return str(out.get(path) or "").strip()

    def put(path, val):
        if "." in path:
            a, _, b = path.partition(".")
            out.setdefault(a, {})[b] = val
        else:
            out[path] = val

    used = set()
    for path, kws in _SUGGEST_RULES:
        if get(path) and not overwrite:
            continue
        best = None
        for kw in kws:
            for it in items:
                text = str(it.get("text") or "")
                sel = str(it.get("selector_guess") or "")
                if not sel or sel in used:
                    continue
                if kw not in text:
                    continue
                # 文本越短越可能是按钮本身（避免命中整个容器）
                score = (len(text), 0 if it.get("visible") else 1)
                if best is None or score < best[0]:
                    best = (score, sel, text)
            if best:
                break
        if best:
            put(path, best[1])
            used.add(best[1])
    out["_suggested"] = sorted({p for p in
                                [r[0] for r in _SUGGEST_RULES] if get(p)})
    return out


# ---------------------------------------------------------------------------
# 自检（不依赖 Qt）：只验证纯逻辑，供探针脚本调用
# ---------------------------------------------------------------------------
def self_check():
    """返回一组断言结果，便于在没有页面时也能验证解析逻辑。"""
    cases = [
        ("第三个视频", {"ordinal": 3, "keyword": ""}),
        ("最后一个", {"ordinal": -1, "keyword": ""}),
        ("第二段手势操", {"ordinal": 2, "keyword": "手势操"}),
        ("《七彩阳光》那首", {"ordinal": None, "keyword": "七彩阳光"}),
        ("倒数第二个", {"ordinal": -2, "keyword": ""}),
        ("第十一个", {"ordinal": 11, "keyword": ""}),
    ]
    out = []
    for hint, want in cases:
        got = parse_media_hint(hint)
        out.append({"hint": hint, "want": want,
                    "got": {"ordinal": got["ordinal"],
                            "keyword": got["keyword"]},
                    "ok": got["ordinal"] == want["ordinal"]
                          and got["keyword"] == want["keyword"]})
    return out
