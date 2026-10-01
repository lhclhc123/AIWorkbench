# -*- coding: utf-8 -*-
"""第一层：Chrome DevTools Protocol（CDP）最小封装。

只懂"调试协议"本身，不懂 DeepSeek：
- 通过 HTTP `GET /json` 发现页面 target，取其 `webSocketDebuggerUrl`；
- 用 `websocket-client` 建立 WS，按 `id` 匹配同步等回包；
- 提供 `evaluate` 执行 JS、`navigate` 导航。

设计参考了项目自己的 `tests/probe_cdp_poc.py`（本仓库自研代码）的握手/导航写法。
**未引用任何第三方仓库的代码。**
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # websocket-client 已在 requirements.txt（第 41 行）
    import websocket  # type: ignore
except Exception:  # pragma: no cover - 缺依赖时只在真正使用时才报错
    websocket = None


class CDPError(RuntimeError):
    """CDP 通道异常（连接、收发、脚本执行）。"""


def _http_json(url, timeout=3.0, method="GET"):
    """向 CDP 的 HTTP 发现口发一个请求并解析 JSON。"""
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    if not body:
        return None
    return json.loads(body)


class CDPClient:
    """一个页面 target 的 CDP 连接（同步、带互斥锁）。"""

    def __init__(self, ws, timeout=25.0):
        """
        Args:
            ws: 已建立连接的 websocket 对象。
            timeout: 默认单条命令的等待上限（秒）。
        """
        self._ws = ws
        self._id = 0
        self._lock = threading.RLock()
        self._timeout = float(timeout or 25.0)

    # ---------- 建立连接 ----------
    @classmethod
    def from_page(cls, port, url_match="chat.deepseek.com", timeout=25.0):
        """选一个 `type=='page'` 的 target 并建立 WS 连接。

        Args:
            port: Chrome 的远程调试端口。
            url_match: 优先选 url 里含该子串的页面（如 chat.deepseek.com）。
            timeout: 建连超时（秒）。
        """
        if websocket is None:
            raise CDPError("缺少 websocket-client 依赖（pip install websocket-client）")
        base = f"http://127.0.0.1:{int(port)}"
        targets = cls._list_targets(base, timeout=min(timeout, 5.0))
        page = cls._pick_page(targets, url_match)
        if page is None and url_match:
            # 没有匹配页面：尝试新开一个标签页指向目标站点
            try:
                cls.open_new_tab(port, "https://" + url_match.strip("/") + "/",
                                 timeout=min(timeout, 5.0))
                time.sleep(1.0)
                targets = cls._list_targets(base, timeout=min(timeout, 5.0))
                page = cls._pick_page(targets, url_match)
            except Exception:
                page = None
        if page is None:
            page = cls._pick_page(targets, None)
        if page is None:
            raise CDPError("调试端口里没有可用的页面标签(target)")
        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError("页面缺少 webSocketDebuggerUrl（无法建立调试连接）")
        try:
            ws = websocket.create_connection(ws_url, timeout=float(timeout))
        except Exception as exc:  # pragma: no cover - 依赖真实浏览器
            raise CDPError(f"无法连接调试端口 {port}：{exc}")
        return cls(ws, timeout=timeout)

    @staticmethod
    def _list_targets(base, timeout=3.0):
        try:
            data = _http_json(base + "/json", timeout=timeout)
        except Exception as exc:
            raise CDPError(f"读取调试目标列表失败：{exc}")
        return data if isinstance(data, list) else []

    @staticmethod
    def _pick_page(targets, url_match):
        pages = [t for t in (targets or []) if t.get("type") == "page"]
        if not pages:
            return None
        if url_match:
            for t in pages:
                if url_match in (t.get("url") or ""):
                    return t
        return pages[0]

    @staticmethod
    def open_new_tab(port, url, timeout=3.0):
        """新开一个标签页（Chrome 较新版本要求用 PUT）。"""
        base = f"http://127.0.0.1:{int(port)}"
        endpoint = base + "/json/new?" + urllib.parse.quote(url, safe="")
        return _http_json(endpoint, timeout=timeout, method="PUT")

    # ---------- 命令收发 ----------
    def _next_id(self):
        self._id += 1
        return self._id

    def send(self, method, params=None):
        """只发不等回包（用于 Page.enable / Runtime.enable 这类命令）。"""
        with self._lock:
            mid = self._next_id()
            payload = json.dumps({"id": mid, "method": method, "params": params or {}})
            try:
                self._ws.send(payload)
            except Exception as exc:
                raise CDPError(f"发送 CDP 命令失败：{exc}")
            return mid

    def call(self, method, params=None, timeout=None):
        """发一条 CDP 命令并同步等回包（按 id 匹配），返回整个消息 dict。"""
        with self._lock:
            mid = self._next_id()
            payload = json.dumps({"id": mid, "method": method, "params": params or {}})
            try:
                self._ws.send(payload)
            except Exception as exc:
                raise CDPError(f"发送 CDP 命令失败：{exc}")
            deadline = time.time() + (timeout if timeout is not None else self._timeout)
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    raise CDPError(f"CDP 命令超时：{method}")
                try:
                    self._ws.settimeout(remain)
                except Exception:
                    pass
                try:
                    raw = self._ws.recv()
                except Exception as exc:
                    raise CDPError(f"CDP 接收失败：{exc}")
                if raw is None:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue          # 事件流里的非 JSON 噪声，忽略
                if msg.get("id") == mid:
                    if msg.get("error"):
                        raise CDPError(f"CDP 返回错误：{msg['error']}")
                    return msg

    def evaluate(self, expression, await_promise=True, timeout=None):
        """Runtime.evaluate(returnByValue=True) 并取出 result.value。"""
        res = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": bool(await_promise),
        }, timeout=timeout)
        result = res.get("result") or {}
        exc = result.get("exceptionDetails")
        if exc:
            desc = ""
            try:
                desc = (exc.get("exception") or {}).get("description") or exc.get("text") or ""
            except Exception:
                desc = str(exc)
            raise CDPError(f"页面脚本执行异常：{desc[:200]}")
        inner = result.get("result") or {}
        if "value" in inner:
            return inner.get("value")
        if inner.get("type") == "undefined":
            return None
        return inner.get("value")

    def navigate(self, url, timeout=None):
        """导航到指定 URL。"""
        return self.call("Page.navigate", {"url": url}, timeout=timeout)

    def enable_domains(self):
        """打开常用域（失败不影响主流程）。"""
        for method in ("Page.enable", "Runtime.enable"):
            try:
                self.send(method)
            except Exception:
                pass

    def is_alive(self):
        """WS 是否仍然连着。"""
        try:
            return bool(getattr(self._ws, "connected", True))
        except Exception:
            return False

    def close(self):
        """关闭 WS（失败静默）。"""
        try:
            self._ws.close()
        except Exception:
            pass
