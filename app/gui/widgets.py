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
    QApplication, QDialog, QLineEdit, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QEvent
from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon, QFont as QGFont, QGuiApplication

from .. import themes

BODY_WIDTH = 640          # 消息卡片正文固定宽度（便于精确计算高度）
CODE_LINE_H = 22          # 代码块每行像素高（与 CSS line-height 保持一致）
CODE_PAD = 10             # 代码块内边距（让代码真的是"一块"，不贴着卡片边）
CODE_MAX_BODY_H = 460     # 代码区高度上限：超了就在卡片内部纵向滚动，不把界面撑爆
RADIUS_BUBBLE = 16
RADIUS_CARD = 12


def _rgb(hex_color):
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return None


def _shade(hex_color, delta):
    """把颜色整体提亮(delta>0)/压暗(delta<0) —— 用于从 token 推导边框、页头色。"""
    c = _rgb(hex_color)
    if c is None:
        return hex_color
    return "#%02x%02x%02x" % tuple(max(0, min(255, v + delta)) for v in c)


def _lum(hex_color):
    c = _rgb(hex_color)
    if c is None:
        return 0.5
    return (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255.0


def code_surface(t):
    """代码块的一套配色（底色 / 边框 / 页头），**保证和聊天底色拉得开**。

    ★ v9.18.0 修「代码看不出是代码」：浅色主题下 `code_bg=#f6f8fa` 和
      `chat_bg=#f4f6f8` 几乎是同一个颜色（亮度差 0.01），再加一条极浅的边框，
      用户眼里就是"一段没有格式的普通文字"。这里按**相对聊天底色的亮度差**
      自动兜底：底色太接近就朝反方向推一档，任何主题下代码块都是一块
      看得见的独立区域。
    """
    chat = t.get("chat_bg") or t.get("window_bg") or "#ffffff"
    bg = t.get("code_bg") or "#f6f8fa"
    if abs(_lum(bg) - _lum(chat)) < 0.10:
        bg = _shade(bg, 34 if _lum(chat) < 0.5 else -28)
    border = t.get("code_border") or "#dfe3e8"
    if abs(_lum(border) - _lum(bg)) < 0.08:
        border = _shade(bg, 30 if _lum(bg) < 0.5 else -34)
    head = _shade(bg, 12 if _lum(bg) < 0.5 else -10)
    return {
        "bg": bg,
        "border": border,
        "head_bg": head,
        "text": t.get("code_text") or "#24292e",
    }

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
        ("用户回答：", "向用户提问"), ("用户没有回答", "向用户提问（未答）"),
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
        "model":  ("◈", "正在调用模型"),
        "answer": ("✎", "正在生成回复"),
        "tool":   ("⚙", "正在执行"),
        "search": ("⊙", "正在联网搜索"),
        "verify": ("⚗", "正在运行验证"),
        "write":  ("✎", "正在写文件"),
        "web":    ("◎", "正在等待 DeepSeek 网页输出"),
        "stop":   ("⏹", "已停止"),
        # ★ v9.18.0（需求 13）：对话里选了团队时，状态行要能说清"现在是谁在干活"，
        #   而不是笼统一句"正在调用模型"。`⬡` 与 `icons.ic("team")` 同字形。
        "team":   ("⬡", "团队协作中"),
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

    ⚠ 中文输入法正在组字（拼音还没上屏）时，Enter 应该交给输入法选词，
    绝不能当成"发送"。这里用两重判断：Qt 的 `QInputMethod.isComposing()`
    加上自己维护的 `inputMethodEvent` 组字状态，避免误发半句话。

    ★ v9.20.0 修「输入框有点大，适当缩小」：
      以前高度是外面写死的 `setMinimumHeight(68)`，空着也是 68px 的大白框。
      现在**按内容自适应**：默认只占 1 行，内容变多就长高，最多 4 行
      （`_MAX_LINES`）之后不再长、改成内部滚动条。只在"算出来的高度真的变了"
      时才 `setFixedHeight`，避免每敲一个字都触发一次布局抖动。
    """

    submitted = pyqtSignal()

    # 高度策略：1 行起步，4 行封顶（再长就出滚动条）
    _MIN_LINES = 1
    _MAX_LINES = 4
    _DOC_MARGIN = 2          # 文档内边距（上下各 2px，够不贴边又不浪费纵向空间）
    _EXTRA_H = 6             # 上下留白 + QSS padding，经验值（见 _fit_height）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False
        self._last_h = 0
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.document().setDocumentMargin(self._DOC_MARGIN)
        self.textChanged.connect(self._fit_height)
        self._fit_height()

    # ---------- 高度自适应（v9.20.0） ----------
    def _line_h(self):
        return max(int(self.fontMetrics().lineSpacing()), 12)

    def _visible_lines(self):
        """文档现在占几行（软换行也算，见 QPlainTextDocument size）。"""
        try:
            n = int(round(float(self.document().size().height())))
        except Exception:
            n = self.blockCount()
        return n

    def _fit_height(self):
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            lines = max(self._MIN_LINES,
                        min(self._MAX_LINES, self._visible_lines()))
            h = lines * self._line_h() + self._DOC_MARGIN * 2 + self._EXTRA_H
            if h != self._last_h:      # ★ 高度没变就不动，避免抖动
                self._last_h = h
                self.setFixedHeight(h)
        finally:
            self._fitting = False

    def changeEvent(self, event):        # noqa: N802 (Qt 命名)
        """字体 / 样式换了（主题切换、QSS 字号）→ 旧的行高作废，重算一次。"""
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._last_h = 0
            self._fit_height()

    def showEvent(self, event):          # noqa: N802 (Qt 命名)
        super().showEvent(event)
        self._last_h = 0                 # 加入布局后字体才是最终值
        self._fit_height()

    def sizeHint(self):                # noqa: N802 (Qt 命名)
        s = super().sizeHint()
        return QSize(s.width(), self._last_h or s.height())

    def minimumSizeHint(self):         # noqa: N802 (Qt 命名)
        s = super().minimumSizeHint()
        return QSize(s.width(), self._last_h or s.height())

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
    """独立代码块卡片：语言标签 + 复制/保存按钮 + 高亮代码。

    ★★ v9.18.0 修「代码/命令都挤成一行、又长又带横向拖动条、下面一大片空白」：
       真凶是 `_inline_pygments()`（Pygments `nowrap=True`）吐出的是**裸 <span>**，
       里面虽然有 `\n`，但 HTML 里换行 = 空白 → Qt 的富文本引擎把整段**塌成一行**。
       于是：① 代码全部挤在一行、非常长；② 为了看全只能横向拖（那条"能拖的进度条"）；
       ③ 但控件高度是按**原始行数**算的（`lines * CODE_LINE_H`）→ 底下空一大片。
       修法三步：① 外面**补一层 `<pre style="white-space:pre-wrap">`** 把换行还原；
                 ② 换成 `WidgetWidth` 自动换行 + 关掉横向滚动条；
                 ③ 高度改成**按文档真实渲染高度**算，不再拿行数估。
    """

    # 语言 -> 左上角显示的标注（用户要求：代码标 `.py`/`.cpp`，命令标 `.bash`）
    _TAG = {
        "python": ".py", "py": ".py", "python3": ".py",
        "c": ".c", "h": ".h", "hpp": ".hpp", "cpp": ".cpp", "c++": ".cpp",
        "cxx": ".cpp", "cc": ".cpp", "csharp": ".cs", "cs": ".cs",
        "java": ".java", "go": ".go", "golang": ".go", "rust": ".rs", "rs": ".rs",
        "php": ".php", "ruby": ".rb", "rb": ".rb", "perl": ".pl", "lua": ".lua",
        "kotlin": ".kt", "kt": ".kt", "swift": ".swift", "scala": ".scala",
        "javascript": ".js", "js": ".js", "node": ".js", "jsx": ".jsx",
        "typescript": ".ts", "ts": ".ts", "tsx": ".tsx", "vue": ".vue",
        "svelte": ".svelte", "dart": ".dart",
        "html": ".html", "css": ".css", "scss": ".scss", "less": ".less",
        "json": ".json", "yaml": ".yml", "yml": ".yml", "toml": ".toml",
        "ini": ".ini", "sql": ".sql", "xml": ".xml",
        "markdown": ".md", "md": ".md", "text": ".txt", "plaintext": ".txt",
        "makefile": ".mk", "cmake": ".cmake", "dockerfile": "Dockerfile",
        "diff": ".diff", "patch": ".patch", "regex": ".regex",
        # 命令类：一律标 .bash（用户明确要的）
        "sh": ".bash", "bash": ".bash", "shell": ".bash", "zsh": ".bash",
        "console": ".bash", "cmd": ".bat", "bat": ".bat",
        "batch": ".bat", "powershell": ".ps1", "ps1": ".ps1", "ps": ".ps1",
        "pwsh": ".ps1",
    }

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
        "sql": ".sql", "sh": ".sh", "shell": ".sh", "bash": ".bash",
        "powershell": ".ps1", "ps1": ".ps1", "bat": ".bat", "cmd": ".bat",
        "xml": ".xml", "markdown": ".md", "md": ".md", "text": ".txt",
    }

    @classmethod
    def tag_of(cls, lang):
        """语言 -> 左上角标签（'.py' / '.cpp' / '.bash'）。空语言返回 ''。"""
        l = (lang or "").strip().lower()
        if not l:
            return ""
        if l in cls._TAG:
            return cls._TAG[l]
        if re.fullmatch(r"[a-z0-9+#.]+", l):
            return "." + l.lstrip(".")
        return l

    def __init__(self, lang, code_html, raw_code, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        cs = code_surface(t)          # ★ v9.18.0：保证底色和聊天底色拉得开
        self._raw = raw_code or ""
        self._lang = (lang or "").strip().lower()
        # 复用标记：流式刷新时 (语言, 代码) 没变就整块复用，不重建控件
        self._sig = (self._lang, self._raw)
        self.setStyleSheet(
            f"CodeBlockWidget{{background:{cs['bg']};"
            f"border:1px solid {cs['border']};border-radius:{RADIUS_CARD}px;}}")
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        head = QWidget()
        head.setStyleSheet(
            f"background:{cs['head_bg']};border-top-left-radius:{RADIUS_CARD}px;"
            f"border-top-right-radius:{RADIUS_CARD}px;"
            f"border-bottom:1px solid {cs['border']};")
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(12, 6, 10, 6)
        hrow.setSpacing(8)
        # ★ v9.18.0：左上角标注改成"扩展名"样式（.py / .cpp / .bash），
        #   让用户一眼就知道这是什么文件/命令（以前显示的是大写语言名 "PYTHON"）。
        tag = self.tag_of(lang)
        lbl = QLabel(tag or "代码")
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
        # ★ v9.18.0：自动换行 + 关掉横向滚动条（以前是 NoWrap + 横向滚动条 →
        #   代码挤成一行、还得拖着看）。
        body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setStyleSheet(
            f"background:{cs['bg']};border:none;color:{cs['text']};padding:0px;"
            "font-family:Consolas,'Microsoft YaHei',monospace;font-size:13px;line-height:22px;")
        doc = body.document()
        try:
            # ★ v9.18.0：给代码留内边距（原来贴着卡片边，看着不像"一块代码"）
            doc.setDocumentMargin(CODE_PAD)
        except Exception:
            pass
        # ★ 关键：给裸 <span> 补一层 <pre style="white-space:pre-wrap">。
        #   `_inline_pygments()` 用 Pygments nowrap=True，输出没有 <pre>，
        #   HTML 里换行=空白 → Qt 会把整段代码塌成一行（这就是"只占一行"的真凶）。
        body.setHtml('<pre style="white-space:pre-wrap;word-break:break-word;'
                     'margin:0;padding:0;">' + (code_html or "") + "</pre>")
        vbox.addWidget(body)
        self.body = body

        self.setFixedWidth(BODY_WIDTH)
        self._fit_body_height()

    def _fit_body_height(self):
        """按**文档真实渲染高度**撑开正文区（不再用行数估算 → 不留大片空白）。

        ★ v9.18.0：高度封顶 `CODE_MAX_BODY_H` —— 几百行的生成文件以前会把消息
          卡片抻到几千像素（"把界面撑爆"），现在超了就在**卡片内部纵向滚动**。
        """
        body = self.body
        w = BODY_WIDTH - 2
        try:
            doc = body.document()
            doc.setTextWidth(w)
            h = int(doc.size().height())
        except Exception:
            h = 0
        if h <= 0:
            lines = max(1, len((self._raw or "").rstrip("\n").split("\n")))
            h = lines * CODE_LINE_H
        if h > CODE_MAX_BODY_H:
            # 要让出滚动条的宽度，否则最后几个字会被压到滚动条底下
            try:
                doc.setTextWidth(w - 14)
            except Exception:
                pass
            body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            body.setFixedHeight(CODE_MAX_BODY_H)
        else:
            body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            body.setFixedHeight(max(h, CODE_LINE_H + 6))
        body.setFixedWidth(w)

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
                 stats=None, collapsed=None, emphasis=False, flow=None):
        super().__init__(parent)
        t = themes.tokens()
        self.role = role
        self.index = index
        self._raw = raw_text or ""
        self._prose = prose_html or ""
        self._blocks = blocks or []
        # ★★ v9.22.0 修「代码块全被堆到消息最底下」（见 markdown_render.render_flow）：
        #    `flow` 是按**原文顺序**排好的分段列表（正文段 / 代码块交替）。
        #    非空 -> 走「顺序模式」：正文与代码块按原位置逐段铺开；
        #    None / []（默认，所有非 assistant 角色以及直接构造的调用点）-> 老路径，
        #    保证「正文在上、代码块在下」的旧行为对它们零回归。
        self._flow = list(flow or [])
        self._flow_enabled = flow is not None
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
        _ICO = {"tool": "⚙", "thinking": "◍", "assistant": "◍", "user": ""}
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

        # ★★ v9.22.0：顺序模式的容器 —— 正文段与代码块按原文顺序交替铺进这里。
        #   位置紧贴 body 之后、code_host 之前：顺序模式下 body 隐藏，这个容器顶上来，
        #   视觉上仍与 body 左对齐（同一层 vbox、相同的 margins/宽度）。
        self.flow_host = QVBoxLayout()
        self.flow_host.setContentsMargins(0, 0, 0, 0)
        self.flow_host.setSpacing(8)
        vbox.addLayout(self.flow_host)

        self.code_host = QVBoxLayout()
        self.code_host.setContentsMargins(0, 0, 0, 0)
        self.code_host.setSpacing(8)
        vbox.addLayout(self.code_host)

        self.footer = QLabel(footer_text or "")
        self.footer.setStyleSheet(
            f"color:{t['text_muted']};font-size:11px;background:transparent;")
        self.footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        vbox.addWidget(self.footer)
        # ★★ v9.17.0 修「弹一堆小窗口」的真凶就在这一行：
        #   控件**还没有父窗口**时调 `setVisible(True)` 等价于 `show()` ——
        #   它会在那一瞬间变成一个**真实的独立小窗口**（实测 isVisible=True /
        #   isWindow=True），紧接着 `addWidget` 把它收进布局、窗口才消失。
        #   带 footer 的消息（助手回复的「实际使用模型：xxx」）每条都会闪一个，
        #   于是用户看到的就是「一执行任务就弹一堆小窗，又很快消失」
        #   （用户自己观察得很准：对话消息先被创建成窗口，再"烧制"进主界面）。
        #   → 纪律：**setVisible(True) 永远放在 addWidget 之后**。
        #   复现/验证：tests/probe_flicker_v9170.py（还原这一行顺序会立刻报泄漏）。
        self.footer.setVisible(bool(footer_text))

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

        # ★ v9.22.0：顺序模式里的正文段浏览器要长得**和 body 一模一样**，
        #   所以把上面按角色定好的样式表存一份复用，避免两处字面量各自漂移。
        self._body_qss = body.styleSheet()

        self._rebuild()

    # ---------- 构造入口 ----------
    @classmethod
    def from_text(cls, role, text, parent=None, footer_text="", label=None,
                  timestamp="", index=-1, actions=True, stats=None, collapsed=None,
                  emphasis=False):
        from .. import markdown_render
        prose, blocks = markdown_render.render_with_blocks(text)
        # ★ v9.22.0：只有 assistant 正文才走「顺序模式」—— 代码块留在原文位置。
        #   user / tool / thinking 保持老行为（工具结果里全是 ``` 命令输出，
        #   走顺序模式会把胶囊撑成一大坨，得不偿失）。
        #   ★ 没有代码块的消息**不启用**顺序模式（flow=None）：它只有一段正文，
        #     老路径的渲染结果一模一样，没必要多造一个 QTextBrowser —— 零回归。
        flow = None
        if role == "assistant":
            try:
                segs = markdown_render.render_flow(text)
                if any(s.get("type") == "code" for s in segs):
                    flow = segs
            except Exception:
                flow = None          # 渲染出任何意外都退回老路径，宁可位置不对也别崩
        return cls(role, prose, blocks, raw_text=text or "", parent=parent,
                   footer_text=footer_text, label=label, timestamp=timestamp,
                   index=index, actions=actions, stats=stats, collapsed=collapsed,
                   emphasis=emphasis, flow=flow)

    def update_live(self, text):
        """流式期间**原地**更新这张卡片的正文（v9.17.0）。

        为什么需要它：`_render_full()` 原来是「每次刷新把聊天区里所有卡片全部
        删掉重建」，流式输出时每秒要重建十几次 —— 消息一多就肉眼可见地卡，
        而且旧卡片在 `setParent(None)` 时还会瞬间闪成独立小窗口。
        流式正文只影响**最后一张**卡片，前面已经稳定的消息完全没必要动。

        返回 True 表示更新成功（调用方就不必再做全量重建了）。
        """
        try:
            from .. import markdown_render
            prose, blocks = markdown_render.render_with_blocks(text or "")
            flow = None
            if self._flow_enabled:
                flow = markdown_render.render_flow(text or "")
        except Exception:
            return False
        self._raw = text or ""
        self._prose = prose
        self._blocks = blocks
        if self._flow_enabled:
            self._flow = list(flow or [])
        try:
            self._rebuild()
        except Exception:
            return False
        return True

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

    def _flow_has_code(self):
        """flow 里是否**真的**存在代码段。

        ★ v9.22.1：只有存在代码段才值得走顺序模式。
          纯正文消息（含流式刚开头、还没吐到 ``` 的那段）走老路径：
          一个 QTextBrowser + `setHtml()`，每帧只改一个文档。
          否则流式时每 110ms 都要为"唯一的正文段"新建/销毁一个 QTextBrowser，
          正是用户以前抱怨过的「模型输出有点卡」。
          反过来，流式里一旦出现 ``` 围栏，下一次 `update_live()` 就会自动切进
          顺序模式 —— 代码块再也不会被挪到正文下面。
        """
        try:
            return any((s or {}).get("type") == "code" for s in (self._flow or []))
        except Exception:
            return False

    def _rebuild(self):
        # ★★ v9.22.0：有顺序分段就按原文顺序铺（正文段与代码块交替）。
        #   老路径（正文一坨 + 代码块全堆下面）**原样保留**给没有代码段的卡片，
        #   保证工具胶囊 / 用户消息 / 纯文字消息 / 流式刚开头零回归。
        if self._flow_has_code():
            self.body.setVisible(False)
            self._rebuild_flow()
            self._apply_height_policy()
            return

        # 从顺序模式退回老路径（例如流式中代码块被删、或内容重算成纯正文）：
        # 先把 flow_host 拆干净，否则两套控件会同时挂在卡片里。
        while self.flow_host.count():
            it = self.flow_host.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        self.body.setHtml(self._prose)
        # ★ v9.18.0：代码块控件**能复用就复用**。
        #   流式输出时这里每 110ms 被调一次，老实现是"全部销毁再重建" ——
        #   已经写完的代码块（常常几百行）也要跟着反复销毁重建 + 重新跑一遍
        #   QTextBrowser 布局，又卡又闪。现在按 (语言, 代码) 签名比对：
        #   没变的那几块原样留着，只有正在长的那一块才重建。
        old = []
        while self.code_host.count():
            it = self.code_host.takeAt(0)
            w = it.widget()
            if w is not None:
                old.append(w)
        pool = {}
        for w in old:
            sig = getattr(w, "_sig", None)
            if sig is not None:
                pool.setdefault(sig, w)
        fresh = []
        for b in self._blocks:
            sig = (b.get("lang", ""), b.get("code", ""))
            w = pool.pop(sig, None)
            if w is None:
                w = CodeBlockWidget(b.get("lang", ""), b.get("html", ""),
                                    b.get("code", ""))
            fresh.append(w)
        for w in pool.values():          # 这次没被复用的：销毁
            w.setParent(None)
            w.deleteLater()
        for w in fresh:
            self.code_host.addWidget(w)
        self._apply_height_policy()

    # ---------- 顺序模式（代码块回到原文位置） ----------
    def _make_prose_browser(self, html):
        """造一个「和 body 长得完全一样」的正文浏览器（顺序模式用）。"""
        b = QTextBrowser()
        b.setReadOnly(True)
        b.setOpenExternalLinks(False)
        b.setFrameShape(QFrame.Shape.NoFrame)
        b.setFixedWidth(BODY_WIDTH)
        b.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        b.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        b.setStyleSheet(getattr(self, "_body_qss", "") or "")
        b.setHtml(html or "")
        return b

    def _rebuild_flow(self):
        """按 `self._flow` 的顺序把「正文段 / 代码块」逐个铺进 `flow_host`。

        复用策略照抄 `_rebuild()`：先按签名建 pool，签名命中就原地复用，
        没被复用的 `setParent(None) + deleteLater()`。
        签名单独用 `_flow_sig` 存（不占 `_sig`）——`_sig` 是 `CodeBlockWidget`
        给 `code_host` 那套 pool 用的 2 元组，两套 pool 各管各的，互不污染。
        """
        old = []
        while self.flow_host.count():
            it = self.flow_host.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                old.append(w)
        pool = {}
        for w in old:
            sig = getattr(w, "_flow_sig", None)
            if sig is not None:
                pool.setdefault(sig, w)

        fresh = []
        for seg in self._flow:
            if seg.get("type") == "code":
                sig = ("code", seg.get("lang", ""), seg.get("code", ""))
                w = pool.pop(sig, None)
                if w is None:
                    w = CodeBlockWidget(seg.get("lang", ""), seg.get("html", ""),
                                        seg.get("code", ""))
                    w._flow_sig = sig
                fresh.append(w)
            else:
                sig = ("prose", seg.get("html", ""))
                w = pool.pop(sig, None)
                if w is None:
                    w = self._make_prose_browser(seg.get("html", ""))
                    w._flow_sig = sig
                fresh.append(w)

        for w in pool.values():          # 这次没被复用的：销毁
            w.setParent(None)
            w.deleteLater()
        for w in fresh:
            self.flow_host.addWidget(w)

    def _fit_flow_prose(self):
        """把顺序模式里的每个正文段撑到内容高度（不出现内部滚动条）。"""
        for i in range(self.flow_host.count()):
            it = self.flow_host.itemAt(i)
            w = it.widget() if it is not None else None
            sig = getattr(w, "_flow_sig", None) if w is not None else None
            if not sig or sig[0] != "prose":
                continue
            w.setMaximumHeight(16777215)
            fit_prose(w, BODY_WIDTH)

    def _set_flow_visible(self, visible):
        """顺序模式：整批子控件一起显/隐（照抄 `_apply_height_policy` 对 code_host 的做法）。"""
        for i in range(self.flow_host.count()):
            it = self.flow_host.itemAt(i)
            if it and it.widget():
                it.widget().setVisible(bool(visible))

    def _apply_height_policy(self):
        flow_on = self._flow_has_code()
        # 收起态：正文 / 代码块 / 脚注全部隐藏，只留一行标题栏
        if self._collapsible and self._collapsed:
            self.body.setVisible(False)
            for i in range(self.code_host.count()):
                it = self.code_host.itemAt(i)
                if it and it.widget():
                    it.widget().setVisible(False)
            self._set_flow_visible(False)
            self.footer.setVisible(False)
            if self.toggle_btn is not None:
                self.toggle_btn.setText("▸")
        else:
            if flow_on:
                # 顺序模式：body 已被 flow_host 取代，绝不能重新显示
                self.body.setVisible(False)
                self._fit_flow_prose()
            else:
                self.body.setVisible(True)
                self.body.setMaximumHeight(16777215)
                fit_prose(self.body, BODY_WIDTH)
            for i in range(self.code_host.count()):
                it = self.code_host.itemAt(i)
                if it and it.widget():
                    it.widget().setVisible(True)
            self._set_flow_visible(True)
            self.footer.setVisible(bool(self.footer.text()))
            if self.toggle_btn is not None:
                self.toggle_btn.setText("▾")
        # 行数统计只在收起时展示（展开时正文可见，无需重复）
        show_stats = bool(self._stats) and self._collapsible and self._collapsed
        self._add_lb.setVisible(show_stats)
        self._del_lb.setVisible(show_stats)

    def set_content(self, prose_html="", blocks=None, raw_text=None):
        """外部替换正文内容。

        ★ v9.22.0：顺序模式下光有 (prose, blocks) 拼不回原文的交替顺序，
          所以多给一个可选的 `raw_text` —— 传了且本卡是顺序模式时，顺便重算 flow。
          不传就沿用原来的 flow（没有 flow 的卡片依旧是老行为，等于没变）。
        """
        self._prose = prose_html or ""
        if blocks is not None:
            self._blocks = blocks
        if raw_text is not None:
            self._raw = raw_text or ""
            if self._flow_enabled:
                try:
                    from .. import markdown_render
                    self._flow = markdown_render.render_flow(self._raw)
                except Exception:
                    pass
        self._rebuild()

    def refit(self):
        if self._collapsible and self._collapsed:
            return
        if self._flow:
            # 顺序模式：body 是隐藏的，量它的 viewport 宽没意义 —— 每个正文段都撑一遍
            self._fit_flow_prose()
            return
        w = self.body.viewport().width()
        fit_prose(self.body, w if w > 80 else BODY_WIDTH)

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._apply_height_policy()
        self.collapse_changed.emit(self._collapsed)


class ProcessGroup(QWidget):
    """把连续的过程消息（工具调用 / 深度思考）折成**一条**默认隐藏的摘要行。

    ★ v9.18.0（用户需求 9）："按照不同的层级，把过程性消息都隐藏，参考 workbuddy"。
    以前每一步工具调用都是一行灰色胶囊，一轮任务跑十几步 → 满屏灰条，把真正的
    回答挤没了。现在：相邻的过程消息自动并成一组，只露一行
    「⚙ 执行了 N 步 · 点击展开」，展开后才逐条看细节（参考 WorkBuddy 的折叠过程）。
    """

    collapsed_changed = pyqtSignal(bool)

    # 和 MessageCard 对齐的最小接口：别的地方会遍历 `_cards` 读 `.role`、
    # 或调用 `.refit()`，这里必须也有，否则一遇到过程组就 AttributeError。
    role = "process"

    def __init__(self, items=None, parent=None, collapsed=True):
        super().__init__(parent)
        t = themes.tokens()
        self._items = list(items or [])
        self._collapsed = bool(collapsed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bubble = QFrame()
        bubble.setObjectName("ProcessGroup")
        bubble.setStyleSheet(
            f"#ProcessGroup{{background:{t['chip_bg']};border:none;"
            "border-radius:8px;}}")
        v = QVBoxLayout(bubble)
        v.setContentsMargins(10, 6, 8, 6)
        v.setSpacing(4)

        self._head = QPushButton()
        self._head.setObjectName("cardBtn")
        self._head.setCursor(Qt.CursorShape.PointingHandCursor)
        self._head.setStyleSheet(
            "QPushButton{background:transparent;border:none;text-align:left;"
            f"color:{t['text_muted']};font-size:12px;font-weight:bold;padding:2px 4px;}}"
            f"QPushButton:hover{{color:{t['accent_text']};}}")
        self._head.clicked.connect(self.toggle)
        v.addWidget(self._head)

        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(2, 2, 2, 2)
        self._body_lay.setSpacing(4)
        v.addWidget(self._body)

        self._head.setText(self._head_text())
        self._body.setVisible(not self._collapsed)

        outer.addStretch(0)
        outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
        outer.addStretch(1)

    def add_card(self, card):
        self._items.append(card)
        self._body_lay.addWidget(card)
        self._head.setText(self._head_text())

    def written_files(self):
        """这一组里"写入文件"的步数 —— 折起来之后用户最关心的就是这个。"""
        return sum(1 for c in self._items if getattr(c, "_label", "") == "写入文件")

    def _head_text(self):
        """组头一行摘要。

        ★ 过程细节折起来之后，"用户最想知道的那点信息"必须留在这一行上 ——
        否则折了等于丢了。所以这里除了步数，还补上"都干了哪几类事"和"写了几个文件"。
        """
        n = len(self._items)
        arrow = "▸" if self._collapsed else "▾"
        extra = ""
        tags = self.lines()
        if tags:
            shown = "、".join(tags[:3]) + (" 等" if len(tags) > 3 else "")
            extra += "　·　" + shown
        wf = self.written_files()
        if wf:
            extra += f"　·　写入 {wf} 个文件"
        return (f"⚙ {arrow} 过程 · {n} 步{extra}"
                f"（点击{'展开' if self._collapsed else '收起'}）")

    def toggle(self):
        self._collapsed = not self._collapsed
        self._body.setVisible(not self._collapsed)
        self._head.setText(self._head_text())
        self.collapsed_changed.emit(self._collapsed)

    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed):
        self._collapsed = bool(collapsed)
        self._body.setVisible(not self._collapsed)
        self._head.setText(self._head_text())

    def lines(self):
        """这一组里出现过的工具标签，用于给用户一个粗略的"都干了啥"。"""
        out = []
        for c in self._items:
            lab = getattr(c, "_label", "")
            if lab and lab not in out:
                out.append(lab)
        return out

    def refit(self):
        """布局落定后把组内卡片的正文高度重新撑开（和 MessageCard.refit 对齐）。"""
        for c in self._items:
            f = getattr(c, "refit", None)
            if callable(f):
                try:
                    f()
                except Exception:
                    pass


class ReasoningCard(QWidget):
    """「深度思考」**实时**卡片（v9.18.0，需求 17）。

    用户原话：
      「模型思考慢是不是因为我只看见了他思考后的结果，中间思考过程没呈现给我？
        这个也可以加进来」

    确实如此：带思维链的模型（DeepSeek-V4.x / Qwen3.x 等）在正式回答之前会先流式
    吐一大段 `reasoning_content`。以前客户端只是**悄悄收着**，等整轮结束才折成一条
    「深度思考」块 —— 用户对着转圈等十几秒，体感就是"很慢"。现在这段思考
    **边来边显示**：默认折成一行「◈ 思考中 · N 字」，点开就是实时滚动的全文。

    和 MessageCard 对齐的最小接口（`_cards` 会被别处遍历）：
      `role` / `refit()` / `is_collapsed()` / `set_collapsed()` / `collapsed_changed`。
    """

    collapsed_changed = pyqtSignal(bool)
    role = "reason"          # 用独立角色：不要被 ProcessGroup 收进去

    # 「展开全文」时的高度上限。取一个远超任何真实思维链的值 ——
    # 语义上等于"不限高"，但保留 setFixedHeight 这条路径（比动态切策略稳）。
    FULL_H = 100_000

    def __init__(self, text="", parent=None, collapsed=True, max_h=420):
        super().__init__(parent)
        t = themes.tokens()
        self._text = str(text or "")
        self._collapsed = bool(collapsed)
        # 正文最高留多高。流式那张要小一点（别把正在长的正文挤出屏幕），
        # 历史里折开来看的那张可以大一点。
        self._max_h = int(max_h)
        # ★★ v9.18.0（用户反馈「思维链过长会被截断」）：
        #   光有 max_h + 内部滚动条仍然不好用 —— 一条两万字的推理塞进 420px 的框里，
        #   要趴在那个小框里慢慢滚，用户体感就是"被截断了"。
        #   所以加一个「展开全文」：点一下就把高度放开到 FULL_H，整段一次看完。
        self._full = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(0)
        bubble = QFrame()
        bubble.setObjectName("ReasonCard")
        bubble.setStyleSheet(
            f"#ReasonCard{{background:{t['chip_bg']};border:none;"
            "border-left:3px solid %s;border-radius:8px;}}"
            % t.get("accent", t["chip_bg"]))
        v = QVBoxLayout(bubble)
        v.setContentsMargins(10, 6, 8, 6)
        v.setSpacing(4)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(6)

        self._head = QPushButton()
        self._head.setObjectName("cardBtn")
        self._head.setCursor(Qt.CursorShape.PointingHandCursor)
        self._head.setStyleSheet(
            "QPushButton{background:transparent;border:none;text-align:left;"
            f"color:{t['text_muted']};font-size:12px;font-weight:bold;padding:2px 4px;}}"
            f"QPushButton:hover{{color:{t['accent_text']};}}")
        self._head.clicked.connect(self.toggle)
        head_row.addWidget(self._head, 0)

        # ★★ v9.18.0（用户反馈「思维链过长会被截断」）：
        #   高度封顶后，长思维链只能趴在框里慢慢滚 —— 体感就是"被截断了"。
        #   于是并列给一个「展开全文」：一点就把高度上限放开，整段一次看完。
        #   只在「真被截到」时才出现（内容没超上限就别多一个按钮晃眼）。
        self._more = QPushButton()
        self._more.setObjectName("cardBtn")
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setStyleSheet(
            "QPushButton{background:transparent;border:none;text-align:right;"
            f"color:{t['accent_text']};font-size:12px;padding:2px 4px;}}"
            f"QPushButton:hover{{color:{t['text_muted']};}}")
        self._more.clicked.connect(self.toggle_full)
        head_row.addStretch(1)
        head_row.addWidget(self._more, 0)
        # ⚠ 纪律：任何 setVisible 都放在 addWidget **之后**（先 addWidget 才有父窗口）
        self._more.setVisible(False)

        v.addLayout(head_row)

        self._body = QTextEdit()
        self._body.setReadOnly(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        # ★ 自适应换行 + 关掉横向滚动条（需求 5 的同一条纪律）
        self._body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # ⚠ 纵向滚动条必须是 AsNeeded：思维链经常长过这里的高度上限，
        #    设成 AlwaysOff 的话超出部分**永远看不到**（内容被截断且没法滚）。
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._body.setStyleSheet(
            f"background:transparent;border:none;color:{t['text_muted']};"
            f"font-size:{themes.fs(12)};")
        self._body.document().setDocumentMargin(0)
        v.addWidget(self._body)

        self._head.setText(self._head_text())
        self._more.setText(self._full_text())
        self._body.setPlainText(self._text)
        self._body.setVisible(not self._collapsed)
        self._fit_body()
        self._update_more()

        outer.addStretch(0)
        outer.addWidget(bubble, 0, Qt.AlignmentFlag.AlignTop)
        outer.addStretch(1)

    # ---------- 内容 ----------
    def _head_text(self):
        n = len(self._text)
        arrow = "▸" if self._collapsed else "▾"
        tail = "点击展开" if self._collapsed else "点击收起"
        return f"◈ {arrow} 思考中 · {n} 字（{tail}）"

    def _full_text(self):
        return "⤡ 收起全文" if self._full else "⤢ 展开全文"

    def _natural_h(self):
        """这段思考**不封顶**时真实需要多高（用来判断要不要给「展开全文」）。"""
        try:
            doc = self._body.document()
            doc.setTextWidth(max(BODY_WIDTH - 40, 120))
            return int(doc.size().height()) + 8
        except Exception:
            return 0

    def _update_more(self):
        """只有「展开着 + 真的被高度截到」才亮出「展开全文」按钮。"""
        show = (not self._collapsed) and self._natural_h() > self._max_h + 2
        if show and not self._full:
            self._more.setText(self._full_text())
        elif self._full:
            self._more.setText(self._full_text())
        try:
            self._more.setVisible(bool(show or self._full))
        except Exception:
            pass

    def set_text(self, text):
        """流式更新：只在内容真的变了时才动控件（和 MessageCard.update_live 同一纪律）。"""
        text = str(text or "")
        if text == self._text:
            return False
        self._text = text
        self._head.setText(self._head_text())
        self._body.setPlainText(text)
        # 只在展开态才需要重新撑高（折叠时正文是隐藏的，算高度纯属浪费）
        if not self._collapsed:
            self._fit_body()
            sb = self._body.verticalScrollBar()
            sb.setValue(sb.maximum())        # 跟在最新那句思考后面
        return True

    def text(self):
        return self._text

    # ---------- 折叠 ----------
    def toggle(self):
        self.set_collapsed(not self._collapsed)
        self.collapsed_changed.emit(self._collapsed)

    def toggle_full(self):
        """「展开全文 / 收起全文」：把高度上限放开 / 收回去（不动折叠态）。"""
        self._full = not self._full
        self._fit_body()
        self._update_more()

    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, collapsed):
        self._collapsed = bool(collapsed)
        self._body.setVisible(not self._collapsed)
        self._head.setText(self._head_text())
        if not self._collapsed:
            self._fit_body()
            self._update_more()
        else:
            self._more.setVisible(False)

    # ---------- 布局 ----------
    def _fit_body(self):
        """按文档真实渲染高度撑开（和 CodeBlockWidget 同款做法）。

        ⚠ 别用"行数 × 行高"估：中文长句会自动换行，估出来的值和真实高度差很多，
        表现为框下面留一大片空白（用户抱怨过）。

        v9.18.0：上限取 `FULL_H`（≈不限高）还是 `_max_h`，取决于「展开全文」有没有点。
        走的仍是同一条 `setFixedHeight` 路径 —— 比动态切策略稳。
        """
        try:
            cap = self.FULL_H if self._full else self._max_h
            doc = self._body.document()
            doc.setTextWidth(max(BODY_WIDTH - 40, 120))
            h = int(doc.size().height()) + 8
            self._body.setFixedHeight(max(24, min(h, cap)))
        except Exception:
            self._body.setFixedHeight(min(120, self._max_h))

    def refit(self):
        if not self._collapsed:
            self._fit_body()
            self._update_more()


class AskUserDialog(QDialog):
    """AI 主动向用户提问（v9.17.0，参考 WorkBuddy 的提问卡片）。

    用户要求：「添加向用户提问的功能，有什么不懂的一定不要猜，可以向用户提问」。

    `ask_user` 工具在 **worker 线程**里执行，靠信号把问题送到 UI 线程弹这个框，
    用户答完再把字符串回传给工具 —— 所以这里只是个纯粹的"问一句、拿一句"。

    行为约定：
    - 给了 options：每个选项一个按钮，**点一下即为选中**（少一次点击）；
    - multi=True：选项变复选框，需点「确定」；
    - 永远保留「自己输入」框（选项不可能覆盖全部情况）；
    - 点「跳过」/ 直接关掉 = **没回答**，工具那边会退化成"按合理假设继续并在
      结论里注明"，绝不会把整轮卡死。
    """

    def __init__(self, question, options=None, header="", multi=False, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        self.answer = ""
        self._multi = bool(multi)
        self._boxes = []
        self.setWindowTitle(header or "AI 想跟你确认一下")
        self.setMinimumWidth(560)
        try:
            self.setStyleSheet(f"QDialog {{ background: {t['window_bg']}; }}")
        except Exception:
            pass

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        if header:
            h = QLabel("? " + header)
            h.setStyleSheet(f"color:{t['accent_text']};font-size:12px;font-weight:bold;")
            lay.addWidget(h)

        qlab = QLabel(question or "")
        qlab.setWordWrap(True)
        qlab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        qlab.setStyleSheet(f"color:{t['text']};font-size:{themes.fs(15)};")
        lay.addWidget(qlab)

        opts = [str(o) for o in (options or []) if str(o).strip()]
        if opts:
            if self._multi:
                tip = QLabel("（可多选，选完点确定）")
                tip.setStyleSheet(f"color:{t['text_muted']};font-size:11px;")
                lay.addWidget(tip)
            box = QVBoxLayout()
            box.setSpacing(6)
            for o in opts:
                if self._multi:
                    cb = QCheckBox(o)
                    cb.setStyleSheet(f"color:{t['text']};font-size:{themes.fs(14)};")
                    box.addWidget(cb)
                    self._boxes.append((cb, o))
                else:
                    b = QPushButton(o)
                    b.setCursor(Qt.CursorShape.PointingHandCursor)
                    b.setStyleSheet(
                        "QPushButton{text-align:left;padding:9px 13px;"
                        f"font-size:{themes.fs(14)};border:1px solid {t['card_border']};"
                        f"border-radius:8px;background:{t['card_bg']};color:{t['text']};}}"
                        f"QPushButton:hover{{border-color:{t['accent']};"
                        f"background:{t['accent_soft']};}}")
                    b.clicked.connect(lambda _=False, txt=o: self._pick(txt))
                    box.addWidget(b)
            lay.addLayout(box)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("也可以直接在这里写…（点了确定就以这里为准）")
        self.edit.setStyleSheet(
            f"QLineEdit{{background:{t['input_bg']};color:{t['text']};"
            f"border:1px solid {t['input_border']};border-radius:8px;"
            f"padding:7px 10px;font-size:{themes.fs(14)};}}")
        self.edit.returnPressed.connect(self._submit)
        row.addWidget(self.edit, 1)

        ok = QPushButton("确定")
        ok.setDefault(True)
        ok.setStyleSheet(
            f"QPushButton{{padding:7px 16px;font-size:{themes.fs(14)};"
            f"border:none;border-radius:8px;background:{t['accent']};color:#ffffff;}}"
            f"QPushButton:hover{{background:{t['accent_hover']};}}")
        ok.clicked.connect(self._submit)
        row.addWidget(ok)

        skip = QPushButton("跳过")
        skip.setToolTip("不回答，让 AI 自己按最合理的假设继续做")
        skip.setStyleSheet(
            f"QPushButton{{padding:7px 14px;font-size:{themes.fs(14)};"
            f"border:1px solid {t['btn_border']};border-radius:8px;"
            f"background:{t['btn_bg']};color:{t['text_muted']};}}")
        skip.clicked.connect(self.reject)
        row.addWidget(skip)
        lay.addLayout(row)

        self.edit.setFocus()

    def _pick(self, text):
        self.answer = str(text or "").strip()
        self.accept()

    def _submit(self):
        txt = (self.edit.text() or "").strip()
        if txt:
            # 手写的内容优先（用户可能想补充选项之外的第三种答案）
            self.answer = txt
        elif self._boxes:
            picked = [o for cb, o in self._boxes if cb.isChecked()]
            self.answer = "、".join(picked)
            if not picked:
                return          # 多选但一个都没勾 → 不关窗，等他勾
        if not self.answer:
            return
        self.accept()
