# -*- coding: utf-8 -*-
"""第三层：本机 OpenAI 兼容服务（127.0.0.1，仅回环）。

为什么需要这一层（选型关键，见 02-arch.md 1.2）：
`app/llm_client.py` 只认 `{base_url}/chat/completions`（流式 SSE）与 `{base_url}/models`。
只要提供这两个端点，`config.py` 里加一个 provider 就能接入，
**Agent 循环 / chat_worker / 51 个工具 / 工具调用协议全部零改动**。

本层要正面回答 00-context 3.4 节的硬约束：
- 硬约束 2：SSE 严格 `data: {...}\\n\\n`，收尾 `data: [DONE]\\n\\n`，心跳用注释行 `: ping`。
- 硬约束 7：等待期间每 2s 发心跳，避免 llm_client 的 25s 读超时切断。
- 硬约束 8：**永远 200 + 带内中文失败**（绝不制造 4xx/5xx，让重试不发生）；
  再加幂等表（请求指纹）+ 驱动层 sessionStorage 标记做兜底，保证不重发。
"""
import hashlib
import json
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import config
from . import errors
from . import prompt as prompt_mod
from .web_driver import WebDriverError

# 发送给 llm_client 的 SSE chunk 里用的模型标识
MODEL_ID = config.WEB_MODEL_ID


class WebTask:
    """一次网页调用的任务记录（用于幂等与镜像）。"""

    __slots__ = ("fp", "messages", "state", "answer", "error", "detail",
                 "done", "created_at", "done_at", "chunks", "lock")

    def __init__(self, fp, messages):
        self.fp = fp
        self.messages = [dict(m) for m in (messages or [])]
        self.state = "queued"          # queued / generating / done / error
        self.answer = None
        self.error = None              # WebErrorKind 或 None
        self.detail = ""
        self.done = threading.Event()
        self.created_at = time.time()
        self.done_at = 0.0
        self.chunks = []               # [(content_delta, reasoning_delta)]
        self.lock = threading.Lock()


class LocalServer:
    """本机 OpenAI 兼容 HTTP 服务。"""

    def __init__(self, driver_provider, notify=None, log=None, state_provider=None):
        """
        Args:
            driver_provider: 无参可调用对象，返回当前 WebDriver 或 None（**不得**主动拉起浏览器）。
            notify: 可选，向界面推一条提示 notify(text)。
            log: 可选，log(level, msg)。
            state_provider: 可选，返回缓存的 (WebErrorKind, detail)（供 /status 快速取值，不阻塞）。
        """
        self._driver_provider = driver_provider
        self._notify = notify or (lambda *a, **k: None)
        self._log = log or (lambda *a, **k: None)
        self._state_provider = state_provider

        self._httpd = None
        self._thread = None
        self._port = 0

        # 串行：同一时间只跑一轮；其余排队
        self._run_lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._queued = 0

        # 幂等表：fp -> WebTask
        self._task_lock = threading.Lock()
        self._tasks = {}

        # 当前网页会话"我们已发送过的消息列表"
        self._sent_lock = threading.Lock()
        self._sent_messages = []

        self._notices = deque(maxlen=50)
        self._notice_lock = threading.Lock()

        self._last_error = ""
        self._resp_seq = 0

    # ---------- 生命周期 ----------
    def start(self, preferred_port=None):
        """启动服务；返回实际监听的端口（固定端口被占用则回退随机端口）。

        ⚠️ 注意 `preferred_port=0` 的语义是"**随机端口**"，不是"用默认端口"。
        旧写法 `int(preferred_port or config.WEB_DEFAULT_PORT)` 里 `0 or X == X`，
        会把 0 吞掉 -> 传 0 变成抢 18921。测试探针因此和真机实例撞端口，
        又因为 Windows 的 SO_REUSEADDR 允许重复绑定，请求会被真机实例截走，
        表现为"离线测试莫名拿到真实网页的内容"。
        """
        if self._httpd is not None:
            return self._port
        preferred = (config.WEB_DEFAULT_PORT if preferred_port is None
                     else int(preferred_port))
        bound = None
        for port in (preferred, 0):
            try:
                httpd = _make_server("127.0.0.1", port, self)
                bound = httpd
                break
            except OSError as exc:
                self._logf("warn", f"端口 {port} 绑定失败：{exc}")
                continue
        if bound is None:
            raise RuntimeError("无法绑定本机回环端口")
        self._httpd = bound
        self._port = bound.server_address[1]
        self._thread = threading.Thread(target=bound.serve_forever,
                                        kwargs={"poll_interval": 0.3},
                                        name="awb-deepseek-web", daemon=True)
        self._thread.start()
        self._logf("info", f"本机服务已启动：127.0.0.1:{self._port}")
        return self._port

    def stop(self):
        """停止服务。"""
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
        except Exception:
            pass
        try:
            self._httpd.server_close()
        except Exception:
            pass
        self._httpd = None
        self._port = 0
        self._logf("info", "本机服务已停止")

    @property
    def port(self):
        return self._port

    def is_started(self):
        return self._httpd is not None

    # ---------- 通知 ----------
    def push_notice(self, text):
        """线程安全地推一条"给用户看"的提示。"""
        if not text:
            return
        with self._notice_lock:
            self._notices.append(str(text))

    def take_notices(self):
        """主窗口定时器取走全部提示。"""
        with self._notice_lock:
            out = list(self._notices)
            self._notices.clear()
        return out

    # ---------- 供 HTTP 处理器调用 ----------
    def _driver(self):
        try:
            return self._driver_provider()
        except Exception as exc:
            self._logf("warn", f"取网页驱动失败：{exc}")
            return None

    def status(self):
        """返回状态字典（**不阻塞**：状态由 service 的轮询线程缓存后经 state_provider 取回）。"""
        kind = errors.WebErrorKind.NOT_STARTED
        detail = ""
        logged_in = False
        if self._state_provider is not None:
            try:
                kind, detail = self._state_provider()
                logged_in = kind in (errors.WebErrorKind.READY, errors.WebErrorKind.GENERATING)
            except Exception:
                kind, detail = errors.WebErrorKind.OTHER, ""
        if not detail:
            detail = errors.WEB_ERROR_TEXT.get(kind, "")
        return {
            "state": kind.value,
            "kind": kind.value,
            "detail": detail,
            "logged_in": logged_in,
            "chrome": bool(getattr(self, "chrome_present", True)),
            "queue": self._queued,
            "port": self._port,
            "last_error": self._last_error,
        }

    def selftest(self):
        """三项自检：① 网页可达 ② 已登录 ③ 能否取回一条答复。"""
        drv = self._driver()
        if drv is None:
            return [
                {"name": "网页可达", "ok": False, "detail": "网页未启动，请先点「启动/打开网页」"},
                {"name": "已登录", "ok": False, "detail": "网页未启动，无法判断"},
                {"name": "能否取回答复", "ok": False, "detail": "网页未启动，无法判断"},
            ]
        try:
            kind, detail = drv.probe_state()
        except Exception as exc:
            kind, detail = errors.WebErrorKind.OTHER, str(exc)
        reachable = kind != errors.WebErrorKind.NOT_STARTED
        logged = kind in (errors.WebErrorKind.READY, errors.WebErrorKind.GENERATING)
        out = [
            {"name": "网页可达", "ok": reachable, "detail": detail},
            {"name": "已登录", "ok": logged,
             "detail": "已登录" if logged else errors.WEB_ERROR_TEXT[errors.WebErrorKind.NOT_LOGGED_IN]},
        ]
        if logged:
            try:
                answer = drv.submit("你好。请只回复两个字：收到", ack_timeout=config.WEB_ACK_TIMEOUT,
                                    answer_timeout=60.0, dedupe_key=None)
                ok3 = bool((answer or "").strip())
                out.append({"name": "能否取回答复", "ok": ok3,
                            "detail": ("已取回：" + (answer or "").strip()[:40]) if ok3
                                      else "网页没有返回内容"})
            except WebDriverError as exc:
                out.append({"name": "能否取回答复", "ok": False,
                            "detail": errors.humanize(exc.kind, str(exc))})
            except Exception as exc:
                out.append({"name": "能否取回答复", "ok": False, "detail": f"{exc}"})
        else:
            out.append({"name": "能否取回答复", "ok": False, "detail": "未登录，无法取回答复"})
        # 自检发过话之后，网页会话状态已变，重置"已发送消息"基线
        with self._sent_lock:
            self._sent_messages = []
        return out

    def reset_conversation_state(self):
        """网页被重载 / 新建对话后，忘掉"以为网页已拥有的消息"。"""
        with self._sent_lock:
            self._sent_messages = []

    # ---------- SSE 工具 ----------
    def _chunk_obj(self, content=None, reasoning=None, finish=None):
        delta = {}
        if reasoning:
            delta["reasoning_content"] = reasoning
        if content:
            delta["content"] = content
        self._resp_seq += 1
        return {
            "id": f"chatcmpl-web-{self._resp_seq}",
            "object": "chat.completion.chunk",
            "model": MODEL_ID,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    # ---------- 幂等 ----------
    def _fingerprint(self, model, messages):
        raw = (str(model or "") + "|" + json.dumps(messages, ensure_ascii=False, sort_keys=True))
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()

    def _get_or_create_task(self, fp, messages):
        """返回 (task, mirrored)。mirrored=True 表示这是对同一在途/近期任务的重复请求。"""
        now = time.time()
        with self._task_lock:
            task = self._tasks.get(fp)
            if task is not None:
                if task.state in ("queued", "generating"):
                    return task, True
                if task.state == "done" and (now - task.done_at) < float(config.WEB_DEDUPE_GRACE):
                    return task, True
            task = WebTask(fp, messages)
            self._tasks[fp] = task
            # 清理过期任务，避免无限增长
            for key in [k for k, t in self._tasks.items()
                        if t.done.is_set() and (now - t.done_at) > 3600]:
                self._tasks.pop(key, None)
            return task, False

    def _logf(self, level, msg):
        try:
            self._log(level, msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AIWorkbenchDeepSeekWeb/1.0"

    # --- 基础 ---
    def log_message(self, fmt, *args):        # noqa: D401 - 保持安静
        return

    @property
    def app(self):
        return self.server.app          # type: ignore[attr-defined]

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        hostname = host.split(":")[0]
        return hostname in ("127.0.0.1", "localhost", "[::1]", "::1", "")

    def _auth_ok(self):
        auth = self.headers.get("Authorization") or ""
        low = auth.lower()
        return low.startswith("bearer ") and bool(auth[7:].strip())

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _guard(self):
        if not self._host_ok():
            self._json(403, {"error": "forbidden host"})
            return False
        if not self._auth_ok():
            self._json(401, {"error": "missing bearer token"})
            return False
        return True

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return {}

    # --- SSE 写手（严格对齐 llm_client 解析器） ---
    def _sse_begin(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._flush()

    def _flush(self):
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _sse_write(self, text):
        try:
            self.wfile.write(text.encode("utf-8"))
            self._flush()
        except Exception:
            raise ConnectionError("客户端已断开")

    def _sse_chunk(self, content=None, reasoning=None, finish=None):
        obj = self.app._chunk_obj(content=content, reasoning=reasoning, finish=finish)
        self._sse_write("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n")

    def _sse_ping(self):
        # SSE 注释行：llm_client 第 182 行只认 "data:"，注释会被跳过
        self._sse_write(": ping\n\n")

    def _sse_done(self):
        self._sse_write("data: [DONE]\n\n")

    # --- 路由 ---
    def do_GET(self):                          # noqa: N802 (http.server 约定)
        if not self._guard():
            return
        path = self.path.split("?", 1)[0]
        app = self.app
        if path == "/models":
            self._json(200, {
                "object": "list",
                "data": [{
                    "id": MODEL_ID,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local-web",
                }],
            })
        elif path == "/healthz":
            st = app.status()
            self._json(200, {"ok": True, "state": st.get("state", "unknown")})
        elif path == "/status":
            self._json(200, app.status())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):                         # noqa: N802
        if not self._guard():
            return
        path = self.path.split("?", 1)[0]
        app = self.app
        if path == "/chat/completions":
            body = self._read_json()
            self._handle_chat(body)
        elif path == "/web/selftest":
            self._read_json()
            try:
                results = app.selftest()
            except Exception as exc:
                results = [{"name": "自检", "ok": False, "detail": str(exc)}]
            self._json(200, {"ok": True, "results": results})
        else:
            self._json(404, {"error": "not found"})

    # --- 核心：/chat/completions ---
    def _handle_chat(self, body):
        app = self.app
        messages = body.get("messages") if isinstance(body, dict) else None
        if not isinstance(messages, list):
            messages = []
        model = body.get("model") if isinstance(body, dict) else None
        fp = app._fingerprint(model, messages)

        task, mirrored = app._get_or_create_task(fp, messages)

        try:
            self._sse_begin()
        except Exception:
            return
        self._sse_ping()

        try:
            if mirrored:
                # 同一请求重复到达：**绝不重发**，镜像原任务的输出
                self._mirror_task(task)
            else:
                self._run_own_task(task)
        except ConnectionError:
            return

        # 统一收尾：失败 -> 带内中文文本；成功 -> 已由 _run_own_task 下发
        if task.error is not None:
            try:
                self._sse_chunk(content=errors.humanize(task.error, task.detail))
            except ConnectionError:
                return
            app._last_error = errors.humanize(task.error, task.detail)
        try:
            self._sse_done()
        except ConnectionError:
            pass

    def _run_own_task(self, task):
        app = self.app
        # 排队：等待轮到自己（期间持续心跳，避免读超时）
        with app._queue_lock:
            app._queued += 1
        try:
            while not app._run_lock.acquire(timeout=config.WEB_HEARTBEAT):
                self._sse_ping()
        except Exception:
            pass
        finally:
            with app._queue_lock:
                app._queued = max(0, app._queued - 1)

        try:
            if app._queued > 0:
                try:
                    self._sse_chunk(content=f"（前面还有 {app._queued} 条提问在排队，"
                                            f"网页通道是串行的，请稍候…）\n")
                except ConnectionError:
                    raise
            self._pump_task(task, owner=True)
        finally:
            try:
                app._run_lock.release()
            except Exception:
                pass

    def _mirror_task(self, task):
        """镜像一个在途 / 近期已完成的任务（不重发）。"""
        if task.state == "done" and task.answer is not None:
            self._sse_chunk(content=task.answer)
            return
        self._pump_task(task, owner=False)

    def _pump_task(self, task, owner):
        """把任务的增量转发给当前连接，直到任务结束。owner=True 时本连接负责执行。"""
        app = self.app
        index = 0
        last_ping = time.time()
        if owner:
            # 本连接负责实际执行：在线程里跑，主循环转发增量
            runner = threading.Thread(target=self._execute_task, args=(task,), daemon=True)
            runner.start()
        while True:
            with task.lock:
                new_chunks = task.chunks[index:]
                index = len(task.chunks)
            for content, reasoning in new_chunks:
                self._sse_chunk(content=content or None, reasoning=reasoning or None)
            if task.done.is_set():
                # 收尾：任务已结束，再取一次，确保"结束前刚写入的最后一个增量"不被漏掉
                with task.lock:
                    tail_chunks = task.chunks[index:]
                    index = len(task.chunks)
                for content, reasoning in tail_chunks:
                    self._sse_chunk(content=content or None, reasoning=reasoning or None)
                break
            if time.time() - last_ping >= config.WEB_HEARTBEAT:
                self._sse_ping()
                last_ping = time.time()
            time.sleep(0.15)

    def _execute_task(self, task):
        """真正执行一轮：driver.submit(...)，把增量写入 task.chunks。"""
        app = self.app
        try:
            driver = app._driver()
            if driver is None:
                task.error = errors.WebErrorKind.NOT_STARTED
                task.detail = ""
                return
            # 先探状态：未就绪直接带内提示（不提交）
            try:
                kind, detail = driver.probe_state()
            except Exception as exc:
                task.error = errors.WebErrorKind.OTHER
                task.detail = str(exc)
                return
            if kind not in (errors.WebErrorKind.READY, errors.WebErrorKind.GENERATING):
                task.error = kind
                task.detail = "" if kind == errors.WebErrorKind.NOT_LOGGED_IN else detail
                return

            # 前缀增量：网页靠自身记忆保住上文，只发新增尾段
            with app._sent_lock:
                sent = list(app._sent_messages)
            tail = prompt_mod.diff_tail(sent, task.messages)
            if tail is None:
                # 前缀不命中 -> 换对话：新建网页对话 + 全量平铺
                try:
                    driver.new_conversation()
                    app.reset_conversation_state()
                    app.push_notice("检测到新的对话，已在 DeepSeek 网页里新建一个会话。")
                except WebDriverError as exc:
                    task.error = exc.kind
                    task.detail = str(exc)
                    return
                tail = prompt_mod.flatten(task.messages)

            # 超长：新建对话 + 携带最近若干轮（必须给提示，禁止静默丢弃）
            if prompt_mod.estimate_chars(task.messages) > config.WEB_MAX_PROMPT_CHARS:
                try:
                    driver.new_conversation()
                    app.reset_conversation_state()
                    trimmed = prompt_mod.trim_recent(task.messages, keep_turns=6)
                    tail = prompt_mod.flatten(trimmed)
                    app.push_notice("网页会话较长，已新建网页对话并携带最近 6 轮上下文"
                                    "（更早内容未带入网页）。")
                except WebDriverError as exc:
                    task.error = exc.kind
                    task.detail = str(exc)
                    return

            if not (tail or "").strip():
                task.error = errors.WebErrorKind.OTHER
                task.detail = "没有需要发送的内容"
                return

            task.state = "generating"

            def _on_progress(delta, reasoning):
                if not delta and not reasoning:
                    return
                with task.lock:
                    task.chunks.append((delta or "", reasoning or ""))

            try:
                answer = driver.submit(tail, on_progress=_on_progress,
                                       ack_timeout=config.WEB_ACK_TIMEOUT,
                                       answer_timeout=config.WEB_ANSWER_TIMEOUT,
                                       dedupe_key=task.fp)
            except WebDriverError as exc:
                task.error = exc.kind
                task.detail = str(exc)
                return
            except Exception as exc:
                task.error = errors.WebErrorKind.OTHER
                task.detail = str(exc)
                return

            task.answer = answer
            task.state = "done"
            with app._sent_lock:
                app._sent_messages = [dict(m) for m in task.messages]
        except Exception as exc:               # pragma: no cover - 兜底
            task.error = errors.WebErrorKind.OTHER
            task.detail = str(exc)
        finally:
            task.done_at = time.time()
            task.done.set()
            if task.state not in ("done",):
                task.state = "error"


def _make_server(host, port, app):
    """构造 ThreadingHTTPServer 并挂上 app 引用（回环绑定、允许复用地址）。"""

    class _Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = _Server((host, port), _Handler)
    httpd.app = app                 # type: ignore[attr-defined]
    try:
        httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    return httpd
