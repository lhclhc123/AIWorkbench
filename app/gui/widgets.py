# -*- coding: utf-8 -*-
"""聊天区可复用控件：气泡卡片、代码块、思考动画、工具标签。

v9 增强：
- 每条消息悬停/常显操作按钮（复制 / 朗读 / 重新生成 / 删除）；
- 助手消息显示角色头像点与时间；
- 工具卡片标签更聪明（能认出 v9 的 9 个新工具）。
"""
import os
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QTextBrowser, QTextEdit, QPlainTextEdit, QFileDialog, QMessageBox,
    QApplication,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QFont as QGFont, QGuiApplication

from .. import themes

BODY_WIDTH = 640          # 消息卡片正文固定宽度（便于精确计算高度）
CODE_LINE_H = 22          # 代码块每行像素高（与 CSS line-height 保持一致）
RADIUS_BUBBLE = 16
RADIUS_CARD = 12

# 角色 -> 标题栏文案。"thinking" 是 v9.8 新增的「深度思考」中间过程块。
_ROLE_LABEL = {"user": "你", "assistant": "AI 助手", "tool": "工具",
               "thinking": "深度思考"}
# 可折叠卡片类型（工具调用、文件写入、深度思考）—— 默认收起，只留一行标题
_COLLAPSIBLE_ROLES = ("tool", "thinking")


def make_icon():
    """程序化生成窗口图标（蓝底白字 AI）。"""
    pm = QPixmap(64, 64)
    pm.fill(QColor("#2196F3"))
    p = QPainter(pm)
    p.setPen(QColor("white"))
    p.setFont(QGFont("Microsoft YaHei", 30, QGFont.Weight.Bold))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "AI")
    p.end()
    return QIcon(pm)


def tool_label(content):
    """根据工具返回内容推断简短标签，折叠时也能一眼看出这一步干了什么。"""
    raw = content or ""
    c = raw
    for pre in ("[成功] ", "[成功]", "[提示] ", "[提示]"):
        if c.startswith(pre):
            c = c[len(pre):].lstrip()
            break
    head = c.split("\n")[0]
    table = (
        ("文件 ", "读取文件"), ("目录 ", "列出目录"), ("命令：", "执行命令"),
        ("【系统完成自检】", "完成自检"), ("已生成", "生成文档/图表"),
        ("已解析", "解析文档"), ("已识别图片", "识别图片"), ("已截屏", "屏幕截图"),
        ("已下载到", "下载文件"), ("已执行 Python", "执行代码"),
        ("已建立知识库索引", "建立索引"), ("已压缩", "压缩"), ("已解压", "解压"),
        ("已移入回收站", "删除（回收站）"), ("已用系统默认程序打开", "打开路径"),
        ("已打出可执行文件", "打包 exe"), ("打进", "打包 exe"), ("可执行文件", "打包 exe"),
        ("联网搜索", "联网搜索"), ("剪贴板", "剪贴板"), ("系统信息", "系统信息"),
        ("进程列表", "进程列表"), ("计划已更新", "更新计划"), ("长期记忆", "检索记忆"),
        ("知识库检索", "检索知识库"), ("当前时间", "获取时间"), ("MCP ", "MCP 调用"),
        ("当前可见窗口", "窗口列表"), ("网页 ", "抓取网页"), ("HTTP ", "HTTP 请求"),
        ("已转写音频", "语音转文字"), ("已朗读", "语音朗读"), ("正在朗读", "语音朗读"),
        ("已推送", "钉钉推送"), ("钉钉", "钉钉"),
        ("已resize", "图片缩放"), ("已compress", "图片压缩"),
        ("已convert", "图片转格式"), ("已rotate", "图片旋转"),
        ("已flip", "图片翻转"), ("已crop", "图片裁剪"), ("已watermark", "加水印"),
        ("已设置提醒", "设置提醒"), ("提醒已设置", "设置提醒"),
        ("检查更新", "检查更新"), ("发现新版本", "检查更新"), ("已是最新", "检查更新"),
        ("无法识别", "模型错误"), ("你选择的模型", "模型错误"),
    )
    for pre, lab in table:
        if c.startswith(pre):
            return lab
    if c.startswith(("已复制", "已移动", "已重命名")):
        return "文件操作"
    if "已写入" in c or ("写入" in c and ("[成功]" in raw or "[错误]" in raw)):
        return "写入文件"
    if raw.startswith(("[错误]", "[拒绝]", "[取消]", "[跳过]")):
        return "工具（异常）"
    if head.startswith("已"):
        return head[:14]
    return "工具"


def strip_tool_markup(text):
    """去掉流式文本里的 <tool_call>...</tool_call>（含只输出了一半的尾巴）。"""
    if not text:
        return ""
    s = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
    s = re.sub(r"<tool_call>.*", "", s, flags=re.DOTALL)
    s = re.sub(r"<tool_?c?a?l?l?$", "", s)
    return s.strip()


# 从文件写入工具的结果里解析行数徽标（+N / -N）。
# 首选：工具返回的**真实行数**（agent.write_file / create_document 的新格式）：
#   write_file      -> `[成功] 已写入 {chars} 字符，共 {lines} 行 → 完整路径：…`
#   create_document -> `[成功] 已生成{label}：{path}（{size}，共 {lines} 行，已确认落盘）`
# 兜底：老格式只有字符数，按每行约 40 字估算。
# 删除行数任何情况下都拿不到，恒为 0。
_WRITE_LINES_RE = re.compile(r"共\s*(\d+)\s*行")
_WRITE_CHARS_PER_LINE = 40
_WRITE_CHARS_RE = re.compile(r"已写入\s+(\d+)\s+字符")


def file_write_stats(content):
    """解析工具结果，返回 (新增行数, 删除行数) 或 None（非写入卡片）。

    ① 命中「共 N 行」→ 用真实行数（注意正则带"行"字，`共 12 项` 不会误命中）；
    ② 否则退回「已写入 N 字符」按每行约 40 字估算（兼容老格式）；
    ③ 都不中 → None（不是写入卡片）。
    """
    s = content or ""
    m = _WRITE_LINES_RE.search(s)
    if m:
        try:
            return (int(m.group(1)), 0)
        except Exception:
            pass
    m = _WRITE_CHARS_RE.search(s)
    if not m:
        return None
    try:
        chars = int(m.group(1))
    except Exception:
        return None
    add = max(1, (chars + _WRITE_CHARS_PER_LINE - 1) // _WRITE_CHARS_PER_LINE)
    return add, 0


def tool_mark(content):
    """工具胶囊右侧的状态点：✓ 成功 / ✕ 异常 / ⏭ 跳过。"""
    s = (content or "").lstrip()
    for pre in ("[错误]", "[拒绝]", "[取消]"):
        if s.startswith(pre):
            return "✕"
    if s.startswith("[跳过]") or "已连续失败" in s:
        return "⏭"
    return "✓"


# 工具名 -> 中文短名（胶囊上显示的动词，仿 WorkBuddy 的「正在做 X」）
_TOOL_VERB = {
    "run_python": "执行代码", "run_command": "执行命令", "write_file": "写入文件",
    "read_file": "读取文件", "list_dir": "列出目录", "web_search": "联网搜索",
    "fetch_url": "抓取网页", "http_request": "HTTP 请求", "download_file": "下载文件",
    "create_document": "生成文档", "read_document": "解析文档", "update_plan": "更新计划",
    "remember": "记住", "recall": "回忆", "build_exe": "打包程序", "screenshot": "截屏",
    "system_info": "读取系统信息", "list_processes": "查看进程", "use_skill": "调用技能",
}


def tool_verb(name):
    """工具名 -> 中文短名（状态行显示「正在执行代码」这种）。"""
    return _TOOL_VERB.get((name or "").strip(), (name or "工具"))


class StatusLine(QWidget):
    """输入框正上方的一行细状态条：「模型现在正在干什么」。

    WorkBuddy 里这一行一直在，用户随时知道是"在调模型 / 在跑工具 / 在写回复"，
    不用猜是不是卡住了。这里用 QLabel 拼，跟着主题走，空闲时淡下来。
    """

    _PHASES = {
        "idle":   ("●", "就绪"),
        "model":  ("🧠", "正在调用模型"),
        "answer": ("✍️", "正在生成回复"),
        "tool":   ("🔧", "正在执行"),
        "search": ("🔍", "正在联网搜索"),
        "verify": ("🧪", "正在运行验证"),
        "write":  ("📝", "正在写文件"),
        "stop":   ("⏹", "已停止"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        self._kind = "idle"
        self._detail = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)
        self.icon = QLabel("●")
        self.icon.setStyleSheet(f"color:{t['text_muted']};font-size:12px;")
        self.label = QLabel("就绪")
        self.label.setStyleSheet(f"color:{t['text_muted']};font-size:12px;")
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        lay.addStretch(1)
        self._n = 0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)

    def set_phase(self, kind, detail=""):
        """kind 见 _PHASES；detail 是补充（如工具名）。"""
        t = themes.tokens()
        kind = kind if kind in self._PHASES else "idle"
        self._kind, self._detail = kind, detail or ""
        icon, text = self._PHASES[kind]
        busy = kind not in ("idle", "stop")
        color = t["accent_text"] if busy else t["text_muted"]
        self.icon.setText(icon)
        self.label.setText(("%s %s" % (text, self._detail)).strip())
        self.icon.setStyleSheet(f"color:{color};font-size:12px;")
        self.label.setStyleSheet(f"color:{color};font-size:12px;")
        if busy and not self._t.isActive():
            self._n = 0
            self._t.start(420)
        elif not busy:
            self._t.stop()
        if not busy:
            self.label.setText(("%s %s" % (self._PHASES[kind][1],
                                           self._detail)).strip())

    def _tick(self):
        self._n = (self._n + 1) % 4
        _, text = self._PHASES[self._kind]
        self.label.setText(("%s %s" % (text, self._detail)).strip()
                           + "·" * self._n)

    def stop(self):
        self._t.stop()


class ChatInput(QPlainTextEdit):
    """聊天输入框：Enter 发送，Shift+Enter 换行。

    ⚠️ 中文输入法正在组字（拼音还没上屏）时，Enter 应该交给输入法选词，
    绝不能当成"发送"。这里用两重判断：Qt 的 `QInputMethod.isComposing()`
    加上自己维护的 `inputMethodEvent` 组字状态，避免误发半句话。
    """

    submitted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False

    def _ime_composing(self):
        try:
            im = QApplication.inputMethod()
            if im is not None and bool(im.isComposing()):
                return True
        except Exception:
            pass
        return self._composing

    def inputMethodEvent(self, event):        # noqa: N802 (Qt 命名)
        super().inputMethodEvent(event)
        try:
            self._composing = bool(event.preeditString())
        except Exception:
            pass

    def keyPressEvent(self, event):           # noqa: N802 (Qt 命名)
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not shift:
            if self._ime_composing():
                super().keyPressEvent(event)
                return
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)



def fit_prose(browser, width, extra=18, minimum=30):
    """把 QTextBrowser 按内容高度撑开（不出现内部滚动条）。"""
    doc = browser.document()
    doc.setTextWidth(max(width - 4, 100))
    h = int(doc.size().height())
    browser.setFixedHeight(max(h + extra, minimum))


class ThinkingIndicator(QWidget):
    """流式等待时的"正在思考"动画。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bubble = QFrame()
        bubble.setStyleSheet(
            f"background:{t['thinking_bg']};border:1px solid {t['border']};"
            f"border-radius:{RADIUS_BUBBLE}px;")
        vbox = QVBoxLayout(bubble)
        vbox.setContentsMargins(14, 12, 14, 12)
        vbox.setSpacing(6)
        label = QLabel("AI 助手")
        label.setStyleSheet(f"color:{t['ai_label']};font-size:12px;font-weight:bold;")
        vbox.addWidget(label)
        self.dots = QLabel("正在思考")
        self.dots.setStyleSheet(f"color:{t['ai_text']};font-size:16px;line-height:1.75;")
        vbox.addWidget(self.dots)
        outer.addStretch(0)
        outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
        outer.addStretch(1)
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._n = 0
        self._t.start(400)

    def _tick(self):
        self._n = (self._n + 1) % 4
        self.dots.setText("正在思考" + "•" * self._n)

    def stop(self):
        self._t.stop()


class CodeBlockWidget(QFrame):
    """独立代码块卡片：语言标签 + 复制/保存按钮 + 高亮代码。"""

    # 语言 -> 默认扩展名（点「保存为文件」时用）
    _EXT = {
        "python": ".py", "py": ".py", "python3": ".py",
        "javascript": ".js", "js": ".js", "node": ".js",
        "typescript": ".ts", "ts": ".ts", "tsx": ".tsx", "jsx": ".jsx",
        "java": ".java", "c": ".c", "h": ".h", "cpp": ".cpp", "c++": ".cpp",
        "csharp": ".cs", "cs": ".cs", "go": ".go", "golang": ".go",
        "rust": ".rs", "rs": ".rs", "php": ".php", "ruby": ".rb",
        "html": ".html", "css": ".css", "scss": ".scss", "json": ".json",
        "yaml": ".yml", "yml": ".yml", "toml": ".toml", "ini": ".ini",
        "sql": ".sql", "sh": ".sh", "shell": ".sh", "bash": ".sh",
        "powershell": ".ps1", "ps1": ".ps1", "bat": ".bat", "cmd": ".bat",
        "xml": ".xml", "markdown": ".md", "md": ".md", "text": ".txt",
    }

    def __init__(self, lang, code_html, raw_code, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        self._raw = raw_code or ""
        self._lang = (lang or "").strip().lower()
        self.setStyleSheet(
            f"CodeBlockWidget{{background:{t['code_bg']};"
            f"border:1px solid {t['code_border']};border-radius:{RADIUS_CARD}px;}}")
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        head = QWidget()
        head.setStyleSheet(
            f"background:{t['code_head_bg']};border-top-left-radius:{RADIUS_CARD}px;"
            f"border-top-right-radius:{RADIUS_CARD}px;border-bottom:1px solid {t['code_border']};")
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(12, 6, 10, 6)
        hrow.setSpacing(8)
        lbl = QLabel((lang or "代码").upper())
        lbl.setStyleSheet(f"color:{t['text_muted']};font-size:12px;font-weight:bold;")
        hrow.addWidget(lbl)
        hrow.addStretch(1)
        self.save_btn = QPushButton("保存为文件")
        self.save_btn.setObjectName("cardBtn")
        self.save_btn.setFixedHeight(24)
        self.save_btn.setToolTip("把这段代码直接存成本地文件（省得自己复制粘贴）")
        self.save_btn.clicked.connect(self._save)
        hrow.addWidget(self.save_btn)
        self.copy_btn = QPushButton("复制代码")
        self.copy_btn.setObjectName("cardBtn")
        self.copy_btn.setFixedHeight(24)
        self.copy_btn.clicked.connect(self._copy)
        hrow.addWidget(self.copy_btn)
        vbox.addWidget(head)

        body = QTextBrowser()
        body.setReadOnly(True)
        body.setOpenExternalLinks(False)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body.setStyleSheet(
            f"background:{t['code_bg']};border:none;color:{t['code_text']};padding:0px;"
            "font-family:Consolas,'Microsoft YaHei',monospace;font-size:13px;line-height:22px;")
        body.setHtml(code_html or "")
        vbox.addWidget(body)
        self.body = body

        lines = max(1, len((raw_code or "").rstrip("\n").split("\n")))
        self.setFixedWidth(BODY_WIDTH)
        body.setFixedHeight(lines * CODE_LINE_H + 20)

    def _copy(self):
        QGuiApplication.clipboard().setText(self._raw)
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("复制代码"))

    def _guess_name(self):
        """从代码里猜个文件名（def/class/第一行注释），猜不到就叫 code。"""
        code = self._raw or ""
        m = re.search(r"(?m)^\s*(?:async\s+)?def\s+(\w+)", code)
        if m and m.group(1) not in ("main",):
            return m.group(1)
        m = re.search(r"(?m)^\s*class\s+(\w+)", code)
        if m:
            return m.group(1)
        m = re.search(r"(?m)^\s*#\s*([\w\u4e00-\u9fa5\-]{2,24})", code)
        if m:
            return m.group(1)
        m = re.search(r"(?m)^\s*//\s*([\w\u4e00-\u9fa5\-]{2,24})", code)
        if m:
            return m.group(1)
        return "code"

    def _default_dir(self):
        d = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(d):
            d = os.path.expanduser("~")
        return d

    def _save(self):
        ext = self._EXT.get(self._lang, ".txt")
        default = os.path.join(self._default_dir(), self._guess_name() + ext)
        path, _sel = QFileDialog.getSaveFileName(
            self, "保存代码到文件", default,
            f"代码文件 (*{ext});;所有文件 (*.*)")
        if not path:
            return
        code = self._raw or ""
        if not code.endswith("\n"):
            code += "\n"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            if not os.path.isfile(path):
                raise IOError("写入后没找到文件（可能被安全软件拦了）")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"{type(e).__name__}: {e}\n\n目标：{path}")
            return
        self.save_btn.setText("已保存 ✓")
        self.save_btn.setToolTip(path)
        QTimer.singleShot(2000, lambda: (self.save_btn.setText("保存为文件"),
                                         self.save_btn.setToolTip(
                                             "把这段代码直接存成本地文件（省得自己复制粘贴）")))


class MessageCard(QWidget):
    """单条消息卡片。

    v9.8 变化：
    - 新增 `thinking` 角色（「深度思考」中间过程块）：字号更小、颜色更灰、
      默认收起，只占一行标题；
    - 工具 / 文件写入 / 深度思考三类卡片统一为「一行标题 + 右侧箭头」；
    - 文件写入卡片收起时在箭头左侧显示绿色 `+N` / 红色 `-N`（行数）。

    信号：
      copy_requested / speak_requested(text) / regenerate_requested /
      delete_requested / edit_requested / collapse_changed(bool)
    """
    copy_requested = pyqtSignal()
    speak_requested = pyqtSignal(str)
    regenerate_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    collapse_changed = pyqtSignal(bool)

    def __init__(self, role, prose_html="", blocks=None, raw_text="", parent=None,
                 footer_text="", label=None, timestamp="", index=-1, actions=True,
                 stats=None, collapsed=None, emphasis=False):
        super().__init__(parent)
        t = themes.tokens()
        self.role = role
        self.index = index
        self._raw = raw_text or ""
        self._prose = prose_html or ""
        self._blocks = blocks or []
        self._stats = stats                    # (新增行数, 删除行数) 或 None
        self._emphasis = bool(emphasis)        # 关键中间结论（保留可见 + 左侧竖线）
        self._label = label if label else _ROLE_LABEL.get(role, role)
        self._collapsible = role in _COLLAPSIBLE_ROLES
        # 折叠初值：工具 / 深度思考默认收起；也可由外部显式指定（用于跨重建恢复）
        self._collapsed = (self._collapsible if collapsed is None else bool(collapsed))

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bubble = QFrame()
        vbox = QVBoxLayout(bubble)
        vbox.setContentsMargins(16, 12, 16, 12)
        vbox.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(6)
        _ICO = {"tool": "⚙", "thinking": "💭", "assistant": "◍", "user": ""}
        prefix = _ICO.get(role, "")
        label = QLabel((prefix + " " + self._label).strip())
        header.addWidget(label)
        # 工具胶囊的状态点：✓ 成功 / ✕ 异常 —— 收起时也能一眼看出这步行不行
        self._mark = QLabel("")
        if role == "tool":
            self._mark.setText(tool_mark(raw_text))
            _ok = self._mark.text() == "✓"
            self._mark.setStyleSheet(
                f"color:{t['success'] if _ok else t['danger']};"
                "font-size:12px;font-weight:bold;")
            header.addWidget(self._mark)
        if timestamp:
            ts = QLabel(timestamp)
            ts.setStyleSheet(f"color:{t['text_muted']};font-size:10px;")
            header.addWidget(ts)
        header.addStretch(1)

        self._btns = {}

        def _mk(text, tip, slot, width=44):
            b = QPushButton(text)
            b.setObjectName("MsgAction")
            if role == "user":
                # 蓝色气泡上的操作按钮：灰字在上面根本看不清，改用浅色
                # （全局 QSS 里的 MsgAction 是按浅底设计的）。
                b.setStyleSheet(
                    "QPushButton{background:transparent;color:#e8f2ff;border:none;"
                    "font-size:11px;padding:1px 6px;border-radius:6px;}"
                    "QPushButton:hover{background:rgba(255,255,255,0.24);"
                    "color:#ffffff;}")
            b.setToolTip(tip)
            b.setFixedHeight(22)
            b.setMinimumWidth(width)
            b.clicked.connect(slot)
            header.addWidget(b)
            return b

        if actions:
            _mk("复制", "复制这条消息", self.copy_requested.emit)
            if role == "assistant":
                _mk("朗读", "朗读这条回复", lambda: self.speak_requested.emit(self._raw), 44)
                _mk("重答", "用同样的输入重新生成一次", self.regenerate_requested.emit, 44)
            if role == "user":
                _mk("编辑", "改一下这条消息，然后从这儿重新开始", self.edit_requested.emit, 44)
            if role in ("assistant", "user"):
                _mk("删除", "删除这条消息", self.delete_requested.emit, 44)

        # 文件写入卡片的 +N / -N（收起时显示，展开时隐藏）
        self._add_lb = QLabel("")
        self._del_lb = QLabel("")
        if self._stats:
            add, dele = self._stats
            self._add_lb.setText(f"+{add}")
            self._add_lb.setStyleSheet(
                f"color:{t['success']};font-size:12px;font-weight:bold;")
            self._del_lb.setText(f"-{dele}")
            self._del_lb.setStyleSheet(
                f"color:{t['danger']};font-size:12px;font-weight:bold;")
            header.addWidget(self._add_lb)
            header.addWidget(self._del_lb)
        self._add_lb.setVisible(False)
        self._del_lb.setVisible(False)

        # 折叠箭头（工具 / 深度思考卡片才有）
        self.toggle_btn = None
        if self._collapsible:
            self.toggle_btn = QPushButton("▸" if self._collapsed else "▾")
            self.toggle_btn.setObjectName("cardBtn")
            self.toggle_btn.setFixedSize(26, 22)
            self.toggle_btn.setToolTip("展开 / 收起这一步")
            self.toggle_btn.clicked.connect(self._toggle)
            header.addWidget(self.toggle_btn)

        vbox.addLayout(header)

        body = QTextBrowser()
        body.setReadOnly(True)
        body.setOpenExternalLinks(False)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setFixedWidth(BODY_WIDTH)
        if role in ("tool", "thinking"):
            body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = body
        vbox.addWidget(body)

        self.code_host = QVBoxLayout()
        self.code_host.setContentsMargins(0, 0, 0, 0)
        self.code_host.setSpacing(8)
        vbox.addLayout(self.code_host)

        self.footer = QLabel(footer_text or "")
        self.footer.setVisible(bool(footer_text))
        self.footer.setStyleSheet(
            f"color:{t['text_muted']};font-size:11px;background:transparent;")
        self.footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vbox.addWidget(self.footer)

        if role == "user":
            vbox.setContentsMargins(14, 9, 14, 9)
            bubble.setStyleSheet(
                f"background:{t['user_bg']};border:none;"
                f"border-radius:{RADIUS_BUBBLE}px;")
            label.setStyleSheet(
                f"color:{t['user_label']};font-size:11px;font-weight:bold;")
            body.setStyleSheet("background:transparent;border:none;color:#ffffff;"
                               f"padding:0px;font-size:{themes.fs(16)};line-height:1.75;")
            outer.addStretch(1)
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
            outer.addStretch(0)
        elif role == "assistant":
            # ★ 仿 WorkBuddy：助手回复**不加气泡、不描边**，正文直接铺在聊天底色上。
            #   以前每条回复都套一圈灰底+描边，满屏都是"框"，喧宾夺主（用户 2026-09-30 反馈）。
            vbox.setContentsMargins(4, 2, 10, 2)
            bubble.setStyleSheet("background:transparent;border:none;")
            label.setStyleSheet(
                f"color:{t['text_muted']};font-size:11px;font-weight:bold;")
            body.setStyleSheet("background:transparent;border:none;color:" + t["ai_text"] + ";"
                               f"padding:0px;font-size:{themes.fs(16)};line-height:1.78;")
            outer.addStretch(0)
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
            outer.addStretch(1)
        else:
            # 工具调用 / 中间过程：压成一行灰色**胶囊**，展开才看细节。
            # `emphasis=True` 的「关键中间结论」用左侧强调竖线 + 正常字色，
            # 与流水账式的碎语（更灰、更小）区分开 —— 用户要求
            # "中间的话不是每句都要藏，重点结论要留着"。
            vbox.setContentsMargins(10, 4, 8, 4)
            vbox.setSpacing(5)
            if emphasis:
                bubble.setStyleSheet(
                    f"background:{t['chip_bg']};border:none;"
                    f"border-left:3px solid {t['accent']};border-radius:8px;")
                label.setStyleSheet(
                    f"color:{t['accent_text']};font-size:12px;font-weight:bold;")
                body.setStyleSheet("background:transparent;border:none;color:"
                                   + t["text"] + ";"
                                   f"padding:0px;font-size:{themes.fs(14)};line-height:1.72;")
            else:
                bubble.setStyleSheet(
                    f"background:{t['chip_bg']};border:none;border-radius:8px;")
                label.setStyleSheet(
                    f"color:{t['text_muted']};font-size:11px;font-weight:bold;")
                body.setStyleSheet("background:transparent;border:none;color:"
                                   + t["text_muted"] + ";"
                                   f"padding:0px;font-size:{themes.fs(13)};line-height:1.7;")
            outer.addStretch(0)
            outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
            outer.addStretch(1)

        self._rebuild()

    # ---------- 构造入口 ----------
    @classmethod
    def from_text(cls, role, text, parent=None, footer_text="", label=None,
                  timestamp="", index=-1, actions=True, stats=None, collapsed=None,
                  emphasis=False):
        from .. import markdown_render
        prose, blocks = markdown_render.render_with_blocks(text)
        return cls(role, prose, blocks, raw_text=text or "", parent=parent,
                   footer_text=footer_text, label=label, timestamp=timestamp,
                   index=index, actions=actions, stats=stats, collapsed=collapsed,
                   emphasis=emphasis)

    def set_footer(self, text):
        t = themes.tokens()
        self.footer.setText(text or "")
        self.footer.setVisible(bool(text))
        self.footer.setStyleSheet(
            f"color:{t['text_muted']};font-size:11px;background:transparent;")

    def is_collapsed(self):
        return bool(self._collapsed)

    def set_collapsed(self, collapsed):
        """外部设置折叠状态（用于 _render_full 重建后恢复用户的展开/收起）。"""
        self._collapsed = bool(collapsed)
        self._apply_height_policy()

    def _rebuild(self):
        self.body.setHtml(self._prose)
        while self.code_host.count():
            it = self.code_host.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for b in self._blocks:
            cw = CodeBlockWidget(b.get("lang", ""), b.get("html", ""), b.get("code", ""))
            self.code_host.addWidget(cw)
        self._apply_height_policy()

    def _apply_height_policy(self):
        # 收起态：正文 / 代码块 / 脚注全部隐藏，只留一行标题栏
        if self._collapsible and self._collapsed:
            self.body.setVisible(False)
            for i in range(self.code_host.count()):
                it = self.code_host.itemAt(i)
                if it and it.widget():
                    it.widget().setVisible(False)
            self.footer.setVisible(False)
            if self.toggle_btn is not None:
                self.toggle_btn.setText("▸")
        else:
            self.body.setVisible(True)
            self.body.setMaximumHeight(16777215)
            fit_prose(self.body, BODY_WIDTH)
            for i in range(self.code_host.count()):
                it = self.code_host.itemAt(i)
                if it and it.widget():
                    it.widget().setVisible(True)
            self.footer.setVisible(bool(self.footer.text()))
            if self.toggle_btn is not None:
                self.toggle_btn.setText("▾")
        # 行数统计只在收起时展示（展开时正文可见，无需重复）
        show_stats = bool(self._stats) and self._collapsible and self._collapsed
        self._add_lb.setVisible(show_stats)
        self._del_lb.setVisible(show_stats)

    def set_content(self, prose_html="", blocks=None):
        self._prose = prose_html or ""
        if blocks is not None:
            self._blocks = blocks
        self._rebuild()

    def refit(self):
        if self._collapsible and self._collapsed:
            return
        w = self.body.viewport().width()
        fit_prose(self.body, w if w > 80 else BODY_WIDTH)

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._apply_height_policy()
        self.collapse_changed.emit(self._collapsed)
