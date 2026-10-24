# -*- coding: utf-8 -*-
"""Web UI 桥（v9.21.0）：QWebChannel 通信桥。只做「翻译 + 限流 + 生命周期」，不含业务逻辑。

职责（见 ``docs/ui/01-v921-web-ui-plan.md`` §3 / §4.3）：

- **上行**：网页 ``bridge.call(name, args)`` → 前端 ``remote.call(name, args_json)``
  → 这里唯一的 ``@pyqtSlot(str, str) call(name, args_json)`` 解析出 name，只
  「打包参数 → emit 对应信号」，真正的活交给主窗口已有的槽（``ChatWorker`` 在工作线程）。
  槽跑在 UI 线程，**绝不能做耗时活**。
- **下行**：主窗口调用 ``send_*`` / ``emit_event`` → ``webui_event = pyqtSignal(str, str)``
  把 ``(type, payload_json)`` 推给前端（前端订阅 ``remote.webui_event.connect``）。
  ⚠ 信号名不能叫 ``event``（QObject 保留方法名，会 qFatal），故叫 ``webui_event``。
- **流式合批**：token / reason 不能每来一个就发一次（40 token/s 会打满），桥里攒
  缓冲区 + ``QTimer``（50ms）flush 一次；``send_finished`` 前先 flush。
- **生命周期**：``QWebChannel`` 的 objects 只对当前页面实例有效，刷新/切页后必须
  ``attach(page)`` 重新注册（主窗口在 ``loadFinished`` 后调用）。

**契约（与前端 ``app/webui/js/transport-qwebchannel.js`` 一一对应，别改）**：
- 对象注册名 **``awb``**。
- 上行：前端 ``remote.call(name, JSON.stringify(args))`` → 本类 ``call(name, args_json)``。
- 下行：本类 ``webui_event(type, payload_json)`` → 前端 ``remote.webui_event.connect`` 解析。

本模块不调模型、不判断该不该回消息、不碰 ``app/gui/``。
"""
import json

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel

FLUSH_INTERVAL_MS = 50
OBJECT_NAME = "awb"


class WebUIBridge(QObject):
    """网页 <-> Python 的通信桥。

    主窗口用法（示意）：

        view = create_view()
        bridge = WebUIBridge(view)
        bridge.cmd_send.connect(self._on_send)      # 上行命令接到已有槽
        view.loadFinished.connect(lambda ok: bridge.attach(view.page()))
        worker.token.connect(bridge.send_token)     # 下行流式接到桥
        worker.finished.connect(bridge.send_finished)
    """

    # ---- 下行：Python -> 网页（前端订阅 remote.webui_event.connect 接收）----
    # ⚠ 不能叫 `event`：那是 QObject 的保留方法名，用它做 signal 名会在实例化时
    #   触发 qFatal 崩溃（Qt 元对象系统冲突）。故命名 webui_event。
    webui_event = pyqtSignal(str, str)    # (type, payload_json)

    # ---- 上行命令信号（连到主窗口的槽）----
    cmd_send = pyqtSignal(str)          # send(text)
    cmd_stop = pyqtSignal()             # stop()
    cmd_regenerate = pyqtSignal()       # regenerate()
    cmd_pick_file = pyqtSignal()        # pick_file()
    cmd_set_model = pyqtSignal(str)     # set_model(choice)
    cmd_set_theme = pyqtSignal(str)     # set_theme("light"/"dark")
    cmd_new_conv = pyqtSignal()         # new_conv()
    cmd_select_conv = pyqtSignal(str)   # select_conv(convId)
    cmd_ready = pyqtSignal()            # ready() —— 前端连接成功后主动上报

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._page = None
        self._channel = None
        self._ready = False

        # 流式合批缓冲区：只攒文本，定时器到点一次性 dispatch
        self._token_buf = []
        self._reason_buf = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush)

    # ------------------------------------------------------------------
    # 上行：网页 -> Python。前端 remote.call(name, JSON.stringify(args))。
    # 唯一的槽：解析 name，emit 对应信号。绝不做事。
    # ------------------------------------------------------------------
    @pyqtSlot(str, str)
    def call(self, name, args_json):
        """前端上行命令入口。name 是命令名，args_json 是参数 JSON 字符串。"""
        name = str(name or "")
        args = {}
        try:
            args = json.loads(args_json or "{}")
            if not isinstance(args, dict):
                args = {}
        except Exception:
            args = {}

        if name == "send":
            self.cmd_send.emit(str(args.get("text", "")))
        elif name == "stop":
            self.cmd_stop.emit()
        elif name == "regenerate":
            self.cmd_regenerate.emit()
        elif name == "pick_file":
            self.cmd_pick_file.emit()
        elif name == "set_model":
            self.cmd_set_model.emit(str(args.get("choice", "")))
        elif name == "set_theme":
            self.cmd_set_theme.emit(str(args.get("theme", "")))
        elif name == "new_conv":
            self.cmd_new_conv.emit()
        elif name == "select_conv":
            self.cmd_select_conv.emit(str(args.get("convId", "")))
        elif name == "ready":
            self.cmd_ready.emit()

    # ------------------------------------------------------------------
    # 下行：Python -> 网页。
    # ------------------------------------------------------------------
    def emit_event(self, type_, payload=None):
        """通用下行：把 (type, payload) 通过 ``webui_event`` 信号推给前端。

        payload 用 ``json.dumps`` 保证换行 / 引号 / 特殊字符都安全。
        """
        self.webui_event.emit(
            str(type_),
            json.dumps(payload, ensure_ascii=False),
        )

    # -- 命名方法（主窗口直接调用，语义见名字）--

    def send_init(self, payload):
        self.emit_event("init", payload)

    def send_messages(self, payload):
        self.emit_event("messages", payload)

    def send_token(self, delta):
        """流式 token：只往 buffer 追加，50ms 由定时器 flush。"""
        if delta:
            self._token_buf.append(str(delta))
            self._ensure_flush()

    def send_reason(self, delta):
        """思维链 reason：同 token 合批。"""
        if delta:
            self._reason_buf.append(str(delta))
            self._ensure_flush()

    def send_tool_start(self, text):
        self.emit_event("tool_start", {"text": text})

    def send_tool_result(self, text):
        self.emit_event("tool_result", {"text": text})

    def send_phase(self, kind, detail):
        self.emit_event("phase", {"kind": kind, "detail": detail})

    def send_finished(self, messages):
        self._flush()                       # 先清空缓冲，保证 token 不丢
        self.emit_event("finished", {"messages": messages})

    def send_error(self, msg):
        self._flush()
        self.emit_event("error", {"message": msg})

    def send_attachments(self, files):
        self.emit_event("attachments", files)

    def send_theme(self, theme):
        self.emit_event("theme", theme)

    # ------------------------------------------------------------------
    # 流式合批内部实现
    # ------------------------------------------------------------------
    def _ensure_flush(self):
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush(self):
        """把累计的 token / reason 一次性派发，并停掉定时器。"""
        try:
            if self._token_buf:
                self.emit_event("token", {"delta": "".join(self._token_buf)})
                self._token_buf = []
            if self._reason_buf:
                self.emit_event("reason", {"delta": "".join(self._reason_buf)})
                self._reason_buf = []
        finally:
            self._flush_timer.stop()

    def flush(self):
        """对外提供的立即 flush（主窗口在切换对话等时机可主动调用）。"""
        self._flush()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def attach(self, page):
        """把桥注册进指定页面。主窗口须在 ``loadFinished`` 后调用。

        QWebChannel 的 objects 只对当前页面实例有效，刷新/切页后必须重新 attach。
        前端用 qwebchannel.js 自己 ``new QWebChannel(qt.webChannelTransport, ...)``
        拿到对象；这里后端用 ``registerObject(OBJECT_NAME, self)``（名字 "awb"，
        与前端 transport-qwebchannel.js 的 OBJECT_NAME 一致）。
        """
        self._page = page
        self._channel = QWebChannel(page)
        page.setWebChannel(self._channel)
        self._channel.registerObject(OBJECT_NAME, self)
        self._ready = True

    def page(self):
        return self._page

    def view(self):
        return self._view
