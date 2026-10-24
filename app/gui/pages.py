# -*- coding: utf-8 -*-
"""v9 各功能页：侧边导航、欢迎页、记忆、工具记录、集成（钉钉/MCP/更新）、设置、关于。"""
import os
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QPlainTextEdit,
    QTextBrowser, QMessageBox, QListWidget, QListWidgetItem, QComboBox, QCheckBox,
    QLineEdit, QDialog, QDialogButtonBox, QTabWidget, QGroupBox, QFormLayout,
    QSpinBox, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDoubleSpinBox, QSplitter, QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from .. import (themes, plan as plan_mod, security, asr as asr_mod,
                tts as tts_mod, dingtalk as dt_mod, config, version as ver,
                skills as skills_mod, scheduler as sched_mod,
                memory as memory_mod, worklog as worklog_mod,
                workspace as ws_mod, icons as icons_mod)

# ★★ v9.18.0（需求 2）：QtWebEngine 有一条**硬规则** —— 必须在 `QApplication`
#   建出来之前被导入，否则启动渲染进程时整个进程会被干掉（webengine.py 实测 exit 127），
#   而且挂请求拦截器也会当场崩。
#   时机正好：`main.py` 先 `import main_window` → 本模块，之后才建 `QApplication`。
#   所以这一行导入**不能挪到函数里、也不能删** —— 想启用"抓包"就得靠它。
#   （browser_page 内部会 import app.webengine，而后者在模块级就把内核导进来了。）
from . import browser_page  # noqa: E402,F401

# ★ v9.18.0（需求 16）：字形一律从 `app/icons.py` 取，别在这里写死。
#   `ic()` 的名字见 icons.ICON；想换图标只改那张表，这里一行都不用动。
PAGES = [
    ("chat", icons_mod.ic("chat"), "对话"),
    ("assistant", icons_mod.ic("assistant"), "助理"),
    ("skills", icons_mod.ic("skills"), "技能"),
    ("tasks", icons_mod.ic("schedule"), "定时"),
    ("memory", icons_mod.ic("memory"), "记忆"),
    ("tools", icons_mod.ic("tools"), "工具"),
    ("bg", icons_mod.ic("parallel"), "并行任务"),
    ("team", icons_mod.ic("team"), "团队"),
    ("integrations", icons_mod.ic("integrations"), "集成"),
    ("settings", icons_mod.ic("settings"), "设置"),
    ("about", icons_mod.ic("info"), "关于"),
    # ★ v9.18.0（需求 2）：内嵌浏览器页。
    #   ⚠ 只能**追加在最后**：`main_window._open_page` 是拿本表的下标当
    #     `QStackedWidget` 的下标用的，插在中间会把后面所有页面全部错位。
    #     对应地，main_window 里 `self.stack.addWidget(self.page_browser)`
    #     也必须排在所有页面之后。
    ("browser", icons_mod.ic("web"), "浏览器"),
]

# 侧栏「常显」的主项（v9.13 引入）。v9.18.0 起导航改成"几行小格子"，
# 这个常量不再参与布局，但保留导出以免老代码 / 回归用例引用时炸掉。
NAV_PRIMARY = ("chat", "assistant", "tools", "settings")

# ---------------------------------------------------------------------------
# 侧栏导航（v9.18.0 需求 10 重做）
#
# 用户原话：
#   「界面优化，去掉左侧菜单栏，统一在对话列表上方弄出几行这样的选项，
#     去掉工具选项，然后其余的再合并合并」
#
# 做法：原来那条 76px 宽、竖着一长溜图标的「菜单栏」整条拆掉，
# 改成**几行小按钮**，由主窗口摆到「对话列表」的**正上方**
# （见 main_window._build_shell 里的 SidePanel）。
#
# 两条约定：
#   · 「工具」不再单独占一格 —— 调用轨迹现在已经按层级直接显示在对话流里
#     （需求 9 的「过程」折叠组），老的轨迹页仍留在页面栈里，
#     从侧栏最底部那行小字进去，功能不丢。
#   · 「团队」（v9.18.0 需求 13）也不再占格子 —— 它已经搬进**对话里**
#     （输入框上方那条 TeamBar：选团队 + 成员状态灯），这才是它该在的地方。
#     老页面留在页面栈里只是用来回看历史运行，入口在 TeamBar 下拉菜单最底部。
#   · PAGES 一项都不删：页面栈的下标跟 PAGES 顺序绑定
#     （main_window._open_page 用 PAGES 的下标当 stack 下标），
#     删一项会把后面所有页面全部错位。所以这里只是"不画那一格"。
# ---------------------------------------------------------------------------
NAV_SKIP = ("tools", "team")   # 不画格子的页面

# 「对话」是主战场，单独占第一行；其余按这个顺序 3 列排布。
# 9 项正好排满 3 行（3 列 × 3 行），不用再多一行半。
NAV_CHIPS = ("assistant", "skills", "browser",
             "tasks", "bg", "memory",
             "integrations", "settings", "about")

NAV_COLS = 3


class NavRail(QWidget):
    """侧栏上方的导航区：第一行「对话」，下面 3 列小格子放其余功能页。"""

    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self.buttons = {}
        self._current = "chat"
        self._meta = {k: (i, n) for k, i, n in PAGES}

        def _make(key, text, height=34):
            b = QPushButton(text)
            b.setObjectName("NavChip")
            b.setCheckable(True)
            b.setFixedHeight(height)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            self.buttons[key] = b
            return b

        # 第一行：对话（整行宽，最显眼）
        icon, name = self._meta["chat"]
        home = _make("chat", f"{icon} {name}", height=36)
        home.setObjectName("NavHome")
        v.addWidget(home)

        # 其余：3 列网格
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(5)
        n = 0
        for key in NAV_CHIPS:
            if key in NAV_SKIP or key not in self._meta:
                continue
            icon, name = self._meta[key]
            grid.addWidget(_make(key, f"{icon} {name}"), n // NAV_COLS, n % NAV_COLS)
            n += 1
        for c in range(NAV_COLS):
            grid.setColumnStretch(c, 1)
        v.addLayout(grid)

        self.chip_keys = [k for k in NAV_CHIPS if k in self.buttons]
        self.buttons["chat"].setChecked(True)

    def select(self, key):
        """切到某一页。`NAV_SKIP` 里的页（如「工具」）没有格子，照常派发切换。"""
        if key not in self.buttons:
            self.changed.emit(key)
            return
        if key == self._current:
            return
        self.set_current(key)
        self.changed.emit(key)

    def set_current(self, key):
        """只改高亮、不派发切换（供主窗口在代码里直接翻页后同步高亮）。

        没有对应格子的页（如「工具」）不动高亮 —— 否则会变成"一个都没选中"。
        """
        if key not in self.buttons:
            return
        self._current = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)

    def current(self):
        return self._current


class WelcomeView(QWidget):
    """空对话时的欢迎页：快捷入口卡片。"""

    quick = pyqtSignal(str)

    ACTIONS = [
        ("▭ 生成 Word 文档", "写一份《…》的 Word，保存到桌面"),
        ("▦ 做一张数据表", "把下面的数据做成 Excel 表格，每列带表头"),
        ("▨ 看图/OCR", "识别我拖进来的图片里的文字"),
        ("⊙ 联网查资料", "上网查一下 … 的最新情况，附来源"),
        ("◍ 语音输入", "点输入框左边的麦克风说话，自动转成文字"),
        ("▲ 画图表", "用这几组数据画一张柱状图，存成 PNG"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 34, 30, 20)
        v.setSpacing(6)

        title = QLabel(f"欢迎使用 {ver.APP_NAME}")
        title.setStyleSheet(f"font-size:26px;font-weight:bold;color:{t['accent_text']};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        sub = QLabel(f"本地 AI Agent · v{ver.VERSION} · 免费的模型 + 你的电脑直接干活")
        sub.setStyleSheet(f"color:{t['text_muted']};font-size:13px;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(sub)
        v.addSpacing(18)

        grid = QHBoxLayout()
        grid.setSpacing(12)
        col1 = QVBoxLayout()
        col1.setSpacing(12)
        col2 = QVBoxLayout()
        col2.setSpacing(12)
        for i, (label, prompt) in enumerate(self.ACTIONS):
            b = QPushButton(f"{label}\n{_dim(prompt)}")
            b.setObjectName("QuickCard")
            b.setMinimumHeight(76)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, p=prompt: self.quick.emit(p))
            (col1 if i % 2 == 0 else col2).addWidget(b)
        col1.addStretch(1)
        col2.addStretch(1)
        grid.addLayout(col1, 1)
        grid.addLayout(col2, 1)
        v.addLayout(grid, 1)

        tip = QLabel("提示：可以把 Word / PDF / Excel / PPT / 图片直接拖进窗口；"
                     "也可以让它生成文档、搜索网页、截屏、跑代码、推送到钉钉。")
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tip.setStyleSheet(f"color:{t['text_muted']};font-size:12px;")
        v.addWidget(tip)


def _dim(s):
    return "　" + s


# ===========================================================================
# 记忆
# ===========================================================================
class MemoryPage(QWidget):
    saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = None
        self.ws = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel("◈ 长期记忆 · 工作总结")
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("PageSub")
        head.addWidget(self.hint)
        v.addLayout(head)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        # ---------- 页 1：长期记忆正文 ----------
        w1 = QWidget()
        v1 = QVBoxLayout(w1)
        v1.setContentsMargins(8, 8, 8, 8)
        tip = QLabel("AI 会在每轮对话结束后**自动**把值得长期记住的信息写进来"
                     "（偏好、身份、项目约定、结论、待办），也会用 remember 工具主动记。"
                     "你可以直接编辑下面这份 Markdown。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v1.addWidget(tip)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "## 用户与身份\n- 名字：…\n\n## 偏好与习惯\n- 喜欢…\n\n"
            "## 项目与约定\n- …\n\n## 重要结论\n- …\n\n## 待办与计划\n- …")
        v1.addWidget(self.edit, 1)
        row = QHBoxLayout()
        self.save_btn = QPushButton("保存记忆")
        self.save_btn.clicked.connect(self._save)
        self.reload_btn = QPushButton("重新载入")
        self.reload_btn.clicked.connect(self.reload)
        clear = QPushButton("清空全部")
        clear.clicked.connect(self._clear)
        row.addWidget(self.save_btn)
        row.addWidget(self.reload_btn)
        row.addStretch(1)
        row.addWidget(clear)
        v1.addLayout(row)
        self.tabs.addTab(w1, "长期记忆")

        # ---------- 页 2：自动写入记录 ----------
        w2 = QWidget()
        v2 = QVBoxLayout(w2)
        v2.setContentsMargins(8, 8, 8, 8)
        self.auto_hint = QLabel("")
        self.auto_hint.setObjectName("PageSub")
        v2.addWidget(self.auto_hint)
        self.auto_view = QPlainTextEdit()
        self.auto_view.setReadOnly(True)
        self.auto_view.setPlaceholderText("还没有自动写入的记录。随便聊两句、或让它干点活，这里就会出现。")
        v2.addWidget(self.auto_view, 1)
        self.tabs.addTab(w2, "自动写入记录")

        # ---------- 页 3：工作总结 ----------
        w3 = QWidget()
        v3 = QVBoxLayout(w3)
        v3.setContentsMargins(8, 8, 8, 8)
        self.log_hint = QLabel("")
        self.log_hint.setObjectName("PageSub")
        v3.addWidget(self.log_hint)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("还没有工作总结。每完成一件事，这里会自动追加一条。")
        v3.addWidget(self.log_view, 1)
        row3 = QHBoxLayout()
        open_btn = QPushButton("打开 worklog 文件夹")
        open_btn.clicked.connect(self._open_worklog)
        row3.addWidget(open_btn)
        row3.addStretch(1)
        v3.addLayout(row3)
        self.tabs.addTab(w3, "工作总结")

    def bind(self, store, ws=None):
        self.store = store
        if ws:
            self.ws = ws
        elif store is not None:
            self.ws = os.path.dirname(getattr(store, "path", "") or "") or None
        self.reload()

    # 兼容旧调用名（主窗口收尾时会调 refresh）
    def refresh(self):
        self.reload()

    def reload(self):
        if self.store is not None:
            self.edit.setPlainText(self.store.load_text() or "")
            self._update_hint()
        self.reload_auto()
        self.reload_worklog()

    def reload_auto(self):
        if not self.ws:
            return
        try:
            recs = memory_mod.recent_auto_log(self.ws, 60)
        except Exception:
            recs = []
        lines = []
        self._auto_texts = []
        for r in recs:
            t = r.get("t", "")
            src = r.get("source", "")
            for it in (r.get("items") or []):
                cat = it.get("cat") or ""
                text = it.get("text") or ""
                if text:
                    self._auto_texts.append(text)
                    lines.append(f"[{t}]（{src}）{('[' + cat + '] ') if cat else ''}{text}")
        self.auto_view.setPlainText("\n".join(lines))
        n = len(self._auto_texts)
        self.auto_hint.setText(
            f"共 {n} 条自动写入记录　|　文件：{memory_mod.auto_log_path(self.ws)}"
            if self.ws else "")
        self._update_hint()

    def auto_items(self):
        return list(getattr(self, "_auto_texts", []) or [])

    def reload_worklog(self):
        if not self.ws:
            return
        try:
            st = worklog_mod.today_stats(self.ws)
            days = worklog_mod.list_days(self.ws)
            txt = worklog_mod.read_day(self.ws)
        except Exception:
            st, days, txt = {"total": 0, "ok": 0, "day": ""}, [], ""
        self.log_view.setPlainText(txt or "")
        if self.ws:
            self.log_hint.setText(
                f"今天 {st.get('total', 0)} 件事（成功 {st.get('ok', 0)}）"
                f"　|　共 {len(days)} 天记录　|　目录："
                f"{self._worklog_dir()}")

    def _worklog_dir(self):
        """工作总结目录（★ v9.18.0 需求 8：已收进工作区的隐藏内部目录）。

        这里只拿得到工作区**路径**（`self.ws`），所以直接用模块级的
        `workspace.internal_dir()` —— 它自带"新位置优先、老位置兜底"，
        老工作区没迁移完也能看到文件。
        """
        if not self.ws:
            return ""
        return ws_mod.internal_dir(self.ws, "worklog")

    def _open_worklog(self):
        if not self.ws:
            return
        try:
            d = self._worklog_dir()
            os.makedirs(d, exist_ok=True)
            os.startfile(d)  # noqa: S606 (Windows)
        except Exception:
            pass

    def _update_hint(self):
        if self.store is None:
            return
        try:
            data = self.store.parse()
            n = sum(len(x) for x in data.values())
            cats = "、".join(f"{k}({len(v)})" for k, v in data.items() if v) or "无"
            self.hint.setText(f"共 {n} 条　|　{cats}")
        except Exception:
            self.hint.setText("")

    def _save(self):
        if self.store is None:
            return
        self.store.save_text(self.edit.toPlainText())
        self._update_hint()
        self.saved.emit()

    def _clear(self):
        if self.store is None:
            return
        if QMessageBox.question(self, "确认", "确定要清空全部长期记忆吗？") \
                == QMessageBox.StandardButton.Yes:
            self.store.clear()
            self.reload()
            self.saved.emit()


# ===========================================================================
# 工具调用记录
# ===========================================================================
class TracePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.trace = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("▦ 工具调用记录")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._btn("刷新", self.reload))
        head.addWidget(self._btn("清空记录", self._clear))
        v.addLayout(head)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("PageSub")
        v.addWidget(self.summary)
        self.body = QTextBrowser()
        v.addWidget(self.body, 1)

    @staticmethod
    def _btn(text, slot):
        b = QPushButton(text)
        b.setObjectName("cardBtn")
        b.setFixedHeight(28)
        b.clicked.connect(slot)
        return b

    def bind(self, trace):
        self.trace = trace
        self.reload()

    def reload(self):
        if self.trace is None:
            return
        t = themes.tokens()
        recs = list(reversed(self.trace.tail(200)))
        html = []
        for r in recs:
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("t", 0)))
            ok = r.get("ok")
            mark = "✓" if ok else "✕"
            args = ", ".join(f"{k}={_short(v)}" for k, v in (r.get("args") or {}).items())
            res = (r.get("result") or "").replace("\n", " ")[:180]
            html.append(
                f'<div style="margin:7px 0;line-height:1.65;">'
                f'<span style="color:{t["text_muted"]};font-size:11px;">{ts}</span> '
                f'<b style="color:{t["text"]};">{mark} {_esc(r.get("name"))}</b> '
                f'<span style="color:{t["text_muted"]};font-size:11px;">'
                f'{r.get("ms", 0)} ms</span><br>'
                f'<span style="color:{t["text_muted"]};font-size:12px;">'
                f'参数：{_esc(args)}</span><br>'
                f'<span style="color:{t["text"]};font-size:12px;">{_esc(res)}</span></div>')
            if not ok:
                html[-1] = html[-1].replace('style="margin:7px 0', 'style="background:rgba(200,60,60,.07);border-radius:8px;padding:6px 8px;margin:7px 0', 1)
        self.body.setHtml("".join(html) or
                          f'<div style="color:{t["text_muted"]};">还没有工具调用记录。</div>')
        by = self.trace.stats()
        if by:
            tot = sum(d["n"] for d in by.values())
            oks = sum(d["ok"] for d in by.values())
            top = sorted(by.items(), key=lambda x: -x[1]["n"])[:6]
            self.summary.setText(f"累计 {tot} 次调用，成功 {oks} 次　|　"
                                 + "　".join(f"{k}×{v['n']}" for k, v in top))

    def _clear(self):
        if self.trace is None:
            return
        if QMessageBox.question(self, "确认", "确定清空工具调用记录吗？") \
                == QMessageBox.StandardButton.Yes:
            self.trace.clear()
            self.reload()


def _short(v):
    s = str(v)
    return s if len(s) <= 46 else s[:46] + "…"


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_KEY_HINTS = {
    "zhipu": "智谱 Key（形如 xxxx.yyyy）——对话 / 看图 / 语音识别都用它，已内置一个",
    "scnet": "超算互联网 Key，形如 sk-…",
    "dashscope": "阿里百炼 Key，形如 sk-…（免费额度可跑 DeepSeek-V4.1 / Qwen 系列）",
    "siliconflow": "硅基流动 Key，形如 sk-…（注册免费、实名后可用；★永久免费的 DeepSeek-R1 就在这一档，填了 Key 才能用）",
}


def _key_hint(provider_name):
    return _KEY_HINTS.get(provider_name, "API Key")


# ===========================================================================
# 助理（钉钉消息 -> AI 处理 -> 结果发回钉钉）
# ===========================================================================
# 「删除」按钮一律红色（跟主题无关，用户要求显眼）
DEL_BTN_QSS = (
    "QPushButton{background:#d93025;color:#ffffff;border:none;border-radius:4px;"
    "padding:6px 14px;font-weight:600;}"
    "QPushButton:hover{background:#b3261e;}"
    "QPushButton:pressed{background:#8c1d18;}"
    "QPushButton:disabled{background:#9aa0a6;color:#f1f3f4;}")

DEFAULT_UNCERTAIN_TEXT = "消息已纳入收件箱，加急处理中，主人正在赶来的路上，此消息由AI代回"

# 消息状态只可能是这五种
_IM_STATUS = ("待处理", "处理中", "已回复", "失败", "需人工")
_IM_STATUS_COLORS = {
    "处理中": "#d98200",
    "失败": "#d93025",
    "需人工": "#8250df",
    "已回复": "#1a7f37",
}


def _tok():
    """主题 token（补一个 card 别名，老主题里叫 card_bg）。"""
    t = dict(themes.tokens())
    t.setdefault("card", t.get("card_bg") or t.get("panel_bg") or "#ffffff")
    return t


def _spin_set(sp, value, lo, hi):
    try:
        sp.setValue(max(int(lo), min(int(hi), int(value))))
    except Exception:
        pass


class AssistantPage(QWidget):
    """钉钉助手主界面：左边待回、右边已处理，三标签（消息 / 设置 / 标签）。

    ★ 只负责界面与本地消息表；真正的收发、模型调用、落盘都在 AssistantService
      和 app/im 里。对外一律走信号，主窗口按信号接线。
    """

    # —— 老接口（main_window 已在用，签名不能变）——
    toggle = pyqtSignal(bool)          # True=启动，False=停止
    cfg_saved = pyqtSignal(dict)       # {"interval":int,"auto_reply":bool}
    process_one = pyqtSignal(str)      # msgid
    process_all = pyqtSignal()
    resend = pyqtSignal(str)           # msgid
    cleared = pyqtSignal()
    # —— 新接口 ——
    msgs_deleted = pyqtSignal(list)        # [msgid, ...]
    im_settings_saved = pyqtSignal(dict)
    tag_scan = pyqtSignal()
    tag_saved = pyqtSignal(str, str)       # (cid, tag)
    manage_requested = pyqtSignal()
    whitelist_added = pyqtSignal(dict)     # {"id","name","kind"}
    whitelist_removed = pyqtSignal(str)
    # ★ v9.20.0：钉钉连接（CLI / 账号 / 会话）—— 用户要求都收进助理的配置里
    ding_login = pyqtSignal(bool)          # True=设备码登录，False=浏览器扫码
    ding_refresh_convs = pyqtSignal()
    ding_test_send = pyqtSignal(str, str)  # (cid, text)
    # —— IMDialog 原样转发 ——
    thread_saved = pyqtSignal(dict)
    task_added = pyqtSignal(dict)
    task_deleted = pyqtSignal(str)
    task_toggled = pyqtSignal(str, bool)
    memory_saved = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.msgs = {}                 # msgid -> dict（见 add_message）
        self._checked = set()          # 勾选中的 msgid
        self._last_checked = ""        # 最后被勾选的那条
        self._sig = None               # 上次重建时的指纹（避免白重建）
        self._log_open = False
        self._dlg = None
        self._tag_by_id = {}
        self._im_extra = {"threads": None, "tasks": None,
                          "logs": None, "memory": None, "review_fn": None}

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        # ---------- 顶部：标题 + 启停 ----------
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("⚑ 钉钉助手")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.btn_run = QPushButton("▶ 启动助理")
        self.btn_run.setMinimumHeight(34)
        self.btn_run.clicked.connect(self._on_toggle)
        self.lb_state = QLabel("未运行")
        self.lb_state.setObjectName("PageSub")
        head.addWidget(self.btn_run)
        head.addWidget(self.lb_state)
        v.addLayout(head)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_msgs_tab(), "消息")
        self.tabs.addTab(self._build_settings_tab(), "设置")
        self.tabs.addTab(self._build_tags_tab(), "标签")

        self._rebuild()

    # ==================================================================
    # 标签①「消息」
    # ==================================================================
    def _build_msgs_tab(self):
        w = QWidget()
        mv = QVBoxLayout(w)
        mv.setContentsMargins(10, 10, 10, 10)
        mv.setSpacing(8)

        sub = QLabel("左边是还没回的消息，右边是已经回过的。勾上左边的方框，"
                     "再点下面的红色删除键就能删掉。")
        sub.setObjectName("PageSub")
        sub.setWordWrap(True)
        mv.addWidget(sub)

        cols = QHBoxLayout()
        cols.setSpacing(10)
        col_l, self._lay_left, self.lb_left = self._make_col("新消息（未回复） 0")
        col_r, self._lay_right, self.lb_right = self._make_col("已处理 0")
        cols.addWidget(col_l, 1)
        cols.addWidget(col_r, 1)
        mv.addLayout(cols, 1)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.btn_del = QPushButton("⊘ 删除选中")
        self.btn_del.setStyleSheet(DEL_BTN_QSS)
        self.btn_del.setEnabled(False)
        self.btn_del.clicked.connect(self._delete_checked)
        brow.addWidget(self.btn_del)

        b_all = QPushButton("⟳ 全部交给 AI")
        b_all.clicked.connect(self.process_all.emit)
        brow.addWidget(b_all)

        b_more = QPushButton("▥ 更多管理")
        b_more.clicked.connect(self._on_more)
        brow.addWidget(b_more)

        brow.addStretch(1)
        self.btn_log = QPushButton("▸ 运行日志")
        self.btn_log.setObjectName("cardBtn")
        self.btn_log.setFixedHeight(28)
        self.btn_log.clicked.connect(self._toggle_log)
        brow.addWidget(self.btn_log)
        mv.addLayout(brow)

        self.logv = QPlainTextEdit()
        self.logv.setReadOnly(True)
        self.logv.setMaximumHeight(140)
        mv.addWidget(self.logv)        # ★ 先 addWidget，再隐藏（否则会弹小窗）
        self.logv.setVisible(False)
        return w

    def _make_col(self, title):
        """一个栏：标题 + 可滚动的卡片容器。"""
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)

        lb = QLabel(title)
        f = lb.font()
        f.setBold(True)
        lb.setFont(f)
        lb.setStyleSheet("color:%s;" % _tok()["text"])
        bl.addWidget(lb)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(2, 2, 6, 2)
        il.setSpacing(8)
        il.addStretch(1)
        sa.setWidget(inner)
        bl.addWidget(sa, 1)
        return box, il, lb

    # ==================================================================
    # 标签②「设置」—— 最上面是钉钉连接（用户要求 CLI 配置也收进这里）
    # ==================================================================
    def _build_ding_group(self):
        g = QGroupBox("钉钉连接（CLI / 账号 / 会话都在这儿配）")
        v = QVBoxLayout(g)
        v.setSpacing(6)

        self.lb_ding = QLabel("正在读取钉钉登录状态…")
        self.lb_ding.setWordWrap(True)
        self.lb_ding.setObjectName("PageSub")
        v.addWidget(self.lb_ding)

        row = QHBoxLayout()
        row.setSpacing(6)
        b1 = QPushButton("◎ 扫码授权登录")
        b1.setToolTip("打开浏览器让钉钉账号授权（dws 个人授权）。")
        b1.clicked.connect(lambda: self.ding_login.emit(False))
        b2 = QPushButton("⌨ 设备码登录")
        b2.setToolTip("浏览器回调被拦时用这个：给一串码，在钉钉里确认即可。")
        b2.clicked.connect(lambda: self.ding_login.emit(True))
        b3 = QPushButton("⟳ 刷新会话")
        b3.setToolTip("重新读一遍钉钉会话列表，填进右边的下拉框")
        b3.clicked.connect(lambda: self.ding_refresh_convs.emit())
        for b in (b1, b2, b3):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        trow = QHBoxLayout()
        trow.setSpacing(6)
        trow.addWidget(QLabel("发到："))
        self.cb_ding_conv = QComboBox()
        self.cb_ding_conv.setMinimumWidth(200)
        trow.addWidget(self.cb_ding_conv, 1)
        self.ed_ding_test = QLineEdit("测试一下，这条是助理发的")
        trow.addWidget(self.ed_ding_test, 2)
        b4 = QPushButton("✉ 发测试消息")
        b4.clicked.connect(self._send_ding_test)
        trow.addWidget(b4)
        v.addLayout(trow)

        hint = QLabel("换电脑 / 换账号后，先在这里登录 + 刷新会话，助理才读得到消息。"
                      "更多接口自检和通讯录在「集成 → 钉钉」。")
        hint.setObjectName("PageSub")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return g

    def _send_ding_test(self):
        cid = self.cb_ding_conv.currentData()
        if not cid:
            self.log("先点「刷新会话」，选一个会话再发")
            return
        text = self.ed_ding_test.text().strip() or "测试消息"
        self.ding_test_send.emit(str(cid), text)
        self.log("已请求发送测试消息")

    def set_ding_status(self, text, convs=None):
        """主窗口回填钉钉登录状态；`convs` 不为 None 时顺便重建会话下拉。"""
        try:
            self.lb_ding.setText(str(text or ""))
        except Exception:
            pass
        if convs is None:
            return
        try:
            keep = self.cb_ding_conv.currentData()
            self.cb_ding_conv.clear()
            for c in (convs or []):
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("id") or c.get("openConversationId") or "").strip()
                if not cid:
                    continue
                kind = str(c.get("kind") or "group")
                prefix = "群 · " if kind == "group" else "人 · "
                self.cb_ding_conv.addItem(prefix + str(c.get("name") or cid), cid)
            if keep:
                for i in range(self.cb_ding_conv.count()):
                    if self.cb_ding_conv.itemData(i) == keep:
                        self.cb_ding_conv.setCurrentIndex(i)
                        break
        except Exception:
            pass

    def _build_settings_tab(self):
        inner = QWidget()
        sv = QVBoxLayout(inner)
        sv.setContentsMargins(10, 10, 10, 10)
        sv.setSpacing(10)

        # 0) ★ v9.20.0：钉钉连接（原来散在「集成」页，用户要求都收进助理的配置里）
        sv.addWidget(self._build_ding_group())

        # 1) 自动回复范围
        g1 = QGroupBox("自动回复范围")
        g1v = QVBoxLayout(g1)
        g1v.setSpacing(6)
        self.ck_scope_group = QCheckBox("群聊")
        self.ck_scope_p2p = QCheckBox("单聊")
        self.ck_scope_at_me = QCheckBox("群里只有 @我 的消息才回")
        for ck in (self.ck_scope_group, self.ck_scope_p2p, self.ck_scope_at_me):
            ck.setChecked(True)
            g1v.addWidget(ck)
        hint1 = QLabel("不勾的话，群里每一条消息都算")
        hint1.setObjectName("PageSub")
        hint1.setWordWrap(True)
        g1v.addWidget(hint1)
        sv.addWidget(g1)

        # 2) 白名单
        g2 = QGroupBox("白名单（这些会话每条消息都回，不受上面的范围限制）")
        g2v = QVBoxLayout(g2)
        g2v.setSpacing(6)
        self.list_wl = QListWidget()
        self.list_wl.setMaximumHeight(120)
        g2v.addWidget(self.list_wl)
        wl_row = QHBoxLayout()
        wl_row.setSpacing(6)
        self.ed_wl_name = QLineEdit()
        self.ed_wl_name.setPlaceholderText("群名 或 对方姓名")
        wl_row.addWidget(self.ed_wl_name, 1)
        self.cb_wl_kind = QComboBox()
        self.cb_wl_kind.addItem("群聊", "group")
        self.cb_wl_kind.addItem("单聊", "p2p")
        wl_row.addWidget(self.cb_wl_kind)
        b_wl_add = QPushButton("＋ 添加")
        b_wl_add.clicked.connect(self._add_whitelist)
        wl_row.addWidget(b_wl_add)
        b_wl_del = QPushButton("⊘ 移除选中")
        b_wl_del.clicked.connect(self._remove_whitelist)
        wl_row.addWidget(b_wl_del)
        g2v.addLayout(wl_row)
        sv.addWidget(g2)

        # 3) 什么时候代我回
        g3 = QGroupBox("什么时候代我回")
        g3v = QVBoxLayout(g3)
        g3v.setSpacing(6)
        drow = QHBoxLayout()
        drow.setSpacing(8)
        self.sp_delay = QSpinBox()
        self.sp_delay.setRange(0, 3600)
        self.sp_delay.setSuffix(" 秒")
        self.sp_delay.setValue(60)
        drow.addWidget(self.sp_delay)
        drow.addWidget(QLabel("我多少秒内没自己回，才由 AI 代回；填 0 = 收到就回"))
        drow.addStretch(1)
        g3v.addLayout(drow)
        sv.addWidget(g3)

        # 4) 拿不准的时候
        g4 = QGroupBox("AI 拿不准的时候")
        g4v = QVBoxLayout(g4)
        g4v.setSpacing(6)
        self.ck_uncertain = QCheckBox("拿不准也要回（回下面这句话）")
        self.ck_uncertain.setChecked(True)
        g4v.addWidget(self.ck_uncertain)
        self.ed_uncertain = QLineEdit(DEFAULT_UNCERTAIN_TEXT)
        g4v.addWidget(self.ed_uncertain)
        hint4 = QLabel("不勾就不会瞎猜，那条消息会留在左边并标成「需人工」，等你自己回")
        hint4.setObjectName("PageSub")
        hint4.setWordWrap(True)
        g4v.addWidget(hint4)
        sv.addWidget(g4)

        # 5) 身份背景
        g5 = QGroupBox("我的身份背景（AI 替你回消息时会以它为准）")
        g5v = QVBoxLayout(g5)
        self.tx_identity = QPlainTextEdit()
        self.tx_identity.setMinimumHeight(160)
        self.tx_identity.setPlaceholderText(
            "例：\n"
            "我是刘浩辰，初三（2）班班长，荣成三中。\n"
            "别人一般叫我「浩辰」，不喜欢被叫全名。\n"
            "最近在准备信息学奥赛，周三下午有课，那会儿回不了消息。\n"
            "我和张老师关系不错，说话可以随意点；跟班主任要客气。")
        g5v.addWidget(self.tx_identity)
        sv.addWidget(g5)

        # 6) 其它
        g6 = QGroupBox("其它")
        g6v = QVBoxLayout(g6)
        g6v.setSpacing(6)
        irow = QHBoxLayout()
        irow.setSpacing(8)
        irow.addWidget(QLabel("检查钉钉新消息的间隔"))
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(10, 600)
        self.sp_interval.setSuffix(" 秒")
        self.sp_interval.setValue(25)
        irow.addWidget(self.sp_interval)
        irow.addStretch(1)
        g6v.addLayout(irow)
        self.ck_auto = QCheckBox("启用自动回复（关掉只收消息，等你手动点处理）")
        self.ck_auto.setChecked(True)
        g6v.addWidget(self.ck_auto)
        sv.addWidget(g6)

        b_save = QPushButton("⇩ 保存设置")
        b_save.clicked.connect(self._emit_settings)
        sv.addWidget(b_save)
        sv.addStretch(1)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setWidget(inner)
        return sa

    # ==================================================================
    # 标签③「标签」
    # ==================================================================
    def _build_tags_tab(self):
        w = QWidget()
        tv = QVBoxLayout(w)
        tv.setContentsMargins(10, 10, 10, 10)
        tv.setSpacing(8)

        trow = QHBoxLayout()
        trow.setSpacing(8)
        self.lb_scan = QLabel("还没扫描过")
        self.lb_scan.setObjectName("PageSub")
        trow.addWidget(self.lb_scan)
        trow.addStretch(1)
        b_scan = QPushButton("⟳ 扫描并打标签")
        b_scan.clicked.connect(self.tag_scan.emit)
        trow.addWidget(b_scan)
        tv.addLayout(trow)

        hint = QLabel("第一次配好钉钉时点一次，AI 会把你的会话按「群聊 → 单聊 → 最近聊天」"
                      "过一遍，尽量标出每个人和你的关系。拿不准的会空着，你也可以自己改。")
        hint.setObjectName("PageSub")
        hint.setWordWrap(True)
        tv.addWidget(hint)

        self.list_tags = QListWidget()
        self.list_tags.currentItemChanged.connect(self._on_tag_selected)
        tv.addWidget(self.list_tags, 1)

        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.ed_tag = QLineEdit()
        self.ed_tag.setPlaceholderText("比如：同班同学 / 班主任 / 妈妈 / 家长群")
        brow.addWidget(self.ed_tag, 1)
        b_save = QPushButton("⇩ 保存标签")
        b_save.clicked.connect(self._save_tag)
        brow.addWidget(b_save)
        tv.addLayout(brow)
        return w

    # ==================================================================
    # 对外接口
    # ==================================================================
    def set_running(self, running):
        running = bool(running)
        self.btn_run.setText("⏹ 停止助理" if running else "▶ 启动助理")
        self.lb_state.setText("运行中…" if running else "未运行")

    def load_cfg(self, cfg):
        """老接口：设置项已搬到「设置」标签，这里只同步启停状态。"""
        try:
            self.set_running(bool((cfg or {}).get("enabled")))
        except Exception:
            pass

    def add_message(self, msg):
        msg = dict(msg or {})
        mid = str(msg.get("msgid") or "").strip()
        if not mid:
            mid = "local-%d" % int(time.time() * 1000)
        now = time.time()
        msg["recv_ts"] = now               # ★ 主窗口要用它算「我多久没回」
        msg["msgid"] = mid
        self.msgs[mid] = {
            "msgid": mid,
            "cid": str(msg.get("cid") or ""),
            "name": str(msg.get("conv") or msg.get("sender") or "钉钉"),
            "kind": str(msg.get("kind") or "group"),
            "text": str(msg.get("text") or ""),
            "time": time.strftime("%m-%d %H:%M"),
            "status": "待处理",
            "result": "",
            "recv_ts": now,
            "sender": str(msg.get("sender") or ""),
        }
        self._rebuild()
        return mid

    def update_status(self, msgid, status, result=None):
        m = self.msgs.get(str(msgid or ""))
        if not m:
            return
        st = str(status or "待处理")
        m["status"] = st if st in _IM_STATUS else "待处理"
        if result is not None:
            m["result"] = str(result)
        self._rebuild()

    def log(self, line):
        try:
            self.logv.appendPlainText("%s  %s" % (time.strftime("%H:%M:%S"), line))
        except Exception:
            pass

    def selected_msgid(self):
        if self._last_checked and self._last_checked in self._checked \
                and self._last_checked in self.msgs:
            return self._last_checked
        if self._checked:
            cand = [m for m in self.msgs.values()
                    if m.get("msgid") in self._checked]
            if cand:
                return max(cand, key=lambda m: float(m.get("recv_ts") or 0))["msgid"]
        if self.msgs:
            return max(self.msgs.values(),
                       key=lambda m: float(m.get("recv_ts") or 0))["msgid"]
        return ""

    def row_msgids(self):
        return [m.get("msgid") for m in self.msgs.values()]

    def set_im_data(self, rows=None, settings=None, tags=None, threads=None,
                    tasks=None, logs=None, memory=None, review_fn=None):
        if rows is not None:
            self._merge_rows(rows)
        if settings is not None:
            self._fill_settings(settings)
        if tags is not None:
            self._set_tags(tags)
        # threads / tasks / logs / memory / review_fn 原样暂存，等「更多管理」再喂给 IMDialog
        if threads is not None:
            self._im_extra["threads"] = threads
        if tasks is not None:
            self._im_extra["tasks"] = tasks
        if logs is not None:
            self._im_extra["logs"] = logs
        if memory is not None:
            self._im_extra["memory"] = memory
        if review_fn is not None:
            self._im_extra["review_fn"] = review_fn
        self._rebuild()

    def set_scan_state(self, text):
        try:
            self.lb_scan.setText(str(text))
        except Exception:
            pass

    def show_manager(self):
        try:
            if self._dlg is None:
                self._dlg = IMDialog(self)
                self._dlg.thread_saved.connect(self.thread_saved.emit)
                self._dlg.task_added.connect(self.task_added.emit)
                self._dlg.task_deleted.connect(self.task_deleted.emit)
                self._dlg.task_toggled.connect(self.task_toggled.emit)
                self._dlg.memory_saved.connect(self.memory_saved.emit)
                self._dlg.refresh_requested.connect(self.refresh_requested.emit)
            e = self._im_extra
            self._dlg.set_data(threads=e.get("threads"), tasks=e.get("tasks"),
                               logs=e.get("logs"), memory=e.get("memory"),
                               review_fn=e.get("review_fn"))
            self._dlg.show()
            self._dlg.raise_()
            self._dlg.activateWindow()
        except Exception as exc:
            self.log("打开管理面板失败：%s: %s" % (type(exc).__name__, exc))

    # ==================================================================
    # 内部：消息表 / 渲染
    # ==================================================================
    def _merge_rows(self, rows):
        now = time.time()
        for row in (rows or []):
            if not isinstance(row, dict):
                continue
            mid = str(row.get("msgid") or "").strip()
            if not mid:
                continue
            old = self.msgs.get(mid)
            # 本地正在处理的条目不被盘上的旧状态覆盖（否则界面会倒退）
            if old is not None and old.get("status") == "处理中":
                continue
            try:
                ts = float(row.get("ts") or 0)
            except Exception:
                ts = 0.0
            if ts <= 0:
                ts = float((old or {}).get("recv_ts") or now)
            st = str(row.get("status") or "待处理")
            self.msgs[mid] = {
                "msgid": mid,
                "cid": str(row.get("cid") or (old or {}).get("cid") or ""),
                "name": str(row.get("name") or (old or {}).get("name") or "钉钉"),
                "kind": str(row.get("kind") or (old or {}).get("kind") or "group"),
                "text": str(row.get("text") or ""),
                "time": str(row.get("time") or time.strftime("%m-%d %H:%M",
                                                             time.localtime(ts))),
                "status": st if st in _IM_STATUS else "待处理",
                "result": str(row.get("reply") or ""),
                "recv_ts": ts,
                "sender": str(row.get("sender") or ""),
            }

    def _fill_settings(self, s):
        if not isinstance(s, dict):
            return
        if "scope_group" in s:
            self.ck_scope_group.setChecked(bool(s.get("scope_group")))
        if "scope_p2p" in s:
            self.ck_scope_p2p.setChecked(bool(s.get("scope_p2p")))
        if "scope_at_me" in s:
            self.ck_scope_at_me.setChecked(bool(s.get("scope_at_me")))
        if "delay_seconds" in s:
            _spin_set(self.sp_delay, s.get("delay_seconds"), 0, 3600)
        if "reply_uncertain" in s:
            self.ck_uncertain.setChecked(bool(s.get("reply_uncertain")))
        if "uncertain_text" in s:
            self.ed_uncertain.setText(str(s.get("uncertain_text") or ""))
        # ★ 用户正在打身份背景时不覆盖，避免把字冲掉
        if "identity" in s and not self.tx_identity.hasFocus():
            self.tx_identity.setPlainText(str(s.get("identity") or ""))
        if "interval" in s:
            _spin_set(self.sp_interval, s.get("interval"), 10, 600)
        if "whitelist" in s:
            self._set_whitelist(s.get("whitelist"))

    def _set_whitelist(self, items):
        self.list_wl.clear()
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            cid = str(it.get("id") or "").strip()
            if not cid:
                continue
            kind = "p2p" if str(it.get("kind") or "") == "p2p" else "group"
            name = str(it.get("name") or cid)
            row = QListWidgetItem(("单聊 · " if kind == "p2p" else "群聊 · ") + name)
            row.setData(Qt.ItemDataRole.UserRole, cid)
            row.setData(Qt.ItemDataRole.UserRole + 1, kind)
            self.list_wl.addItem(row)

    def _rebuild(self):
        self._checked &= set(self.msgs)
        items = sorted(self.msgs.values(),
                       key=lambda m: float(m.get("recv_ts") or 0), reverse=True)
        sig = tuple((m.get("msgid"), m.get("status"), m.get("result", ""),
                     m.get("name", ""), m.get("text", "")) for m in items)
        if self._sig == sig:
            self._sync_del_btn()
            return
        self._sig = sig
        left = [m for m in items if m.get("status") != "已回复"]
        right = [m for m in items if m.get("status") == "已回复"]
        self._fill(self._lay_left, left, False)
        self._fill(self._lay_right, right, True)
        self.lb_left.setText("新消息（未回复） %d" % len(left))
        self.lb_right.setText("已处理 %d" % len(right))
        self._sync_del_btn()

    def _fill(self, lay, msgs, done):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for m in msgs:
            card = _MsgCard(m, checked=(m.get("msgid") in self._checked), done=done)
            card.toggled.connect(self._on_card_toggled)
            lay.addWidget(card)
        lay.addStretch(1)

    def _on_card_toggled(self, msgid, checked):
        if checked:
            self._checked.add(msgid)
            self._last_checked = msgid
        else:
            self._checked.discard(msgid)
        self._sync_del_btn()

    def _sync_del_btn(self):
        try:
            self.btn_del.setEnabled(bool(self._checked & set(self.msgs)))
        except Exception:
            pass

    def _delete_checked(self):
        ids = [i for i in self._checked if i in self.msgs]
        if not ids:
            return
        # ★ 先发信号再删本地：主窗口要靠 msgs[mid]["cid"] 把「已删」写回盘上，
        #   删早了它拿不到 cid，下次刷新这些消息又会冒出来。
        self.msgs_deleted.emit(ids)
        for mid in ids:
            self.msgs.pop(mid, None)
        self._checked -= set(ids)
        self._last_checked = ""
        self._rebuild()
        self.log("已删除 %d 条消息" % len(ids))

    def _on_toggle(self):
        self.toggle.emit(self.btn_run.text().startswith("▶"))

    def _toggle_log(self):
        self._log_open = not self._log_open
        self.logv.setVisible(self._log_open)
        self.btn_log.setText("▾ 运行日志" if self._log_open else "▸ 运行日志")

    def _on_more(self):
        # 主窗口收到 manage_requested 会自己调 show_manager() 并刷一遍数据；
        # 这里兜底（页面单独用、没人接线时也能打开），已经开出来了就不再重复弹。
        self.manage_requested.emit()
        if self._dlg is None or not self._dlg.isVisible():
            self.show_manager()

    def _emit_settings(self):
        d = {
            "scope_group": self.ck_scope_group.isChecked(),
            "scope_p2p": self.ck_scope_p2p.isChecked(),
            "scope_at_me": self.ck_scope_at_me.isChecked(),
            "delay_seconds": self.sp_delay.value(),
            "reply_uncertain": self.ck_uncertain.isChecked(),
            "uncertain_text": (self.ed_uncertain.text().strip()
                               or DEFAULT_UNCERTAIN_TEXT),
            "identity": self.tx_identity.toPlainText(),
            "interval": self.sp_interval.value(),
            "auto_reply": self.ck_auto.isChecked(),
        }
        self.im_settings_saved.emit(d)
        # 兼容老接线
        self.cfg_saved.emit({"interval": self.sp_interval.value(),
                             "auto_reply": self.ck_auto.isChecked()})
        self.log("设置已保存")

    # ---------- 白名单 ----------
    def _add_whitelist(self):
        txt = self.ed_wl_name.text().strip()
        if not txt:
            return
        kind = "p2p" if self.cb_wl_kind.currentData() == "p2p" else "group"
        row = QListWidgetItem(("单聊 · " if kind == "p2p" else "群聊 · ") + txt)
        row.setData(Qt.ItemDataRole.UserRole, txt)
        row.setData(Qt.ItemDataRole.UserRole + 1, kind)
        self.list_wl.addItem(row)
        self.ed_wl_name.clear()
        self.whitelist_added.emit({"id": txt, "name": txt, "kind": kind})

    def _remove_whitelist(self):
        for it in list(self.list_wl.selectedItems()):
            cid = str(it.data(Qt.ItemDataRole.UserRole) or "")
            self.list_wl.takeItem(self.list_wl.row(it))
            if cid:
                self.whitelist_removed.emit(cid)

    # ---------- 标签 ----------
    def _set_tags(self, tags):
        rows = []
        if isinstance(tags, dict):
            for cid, v in tags.items():
                rec = dict(v or {})
                rec["id"] = rec.get("id") or cid
                rows.append(rec)
        else:
            for v in (tags or []):
                if isinstance(v, dict):
                    rows.append(dict(v))
        self.list_tags.clear()
        self._tag_by_id = {}
        for r in rows:
            cid = str(r.get("id") or r.get("cid") or "")
            if not cid:
                continue
            name = str(r.get("name") or cid)
            kind = str(r.get("kind") or "group")
            tag = str(r.get("tag") or "")
            self._tag_by_id[cid] = {"id": cid, "name": name, "kind": kind, "tag": tag}
            self.list_tags.addItem(self._tag_item(cid, name, kind, tag))

    def _tag_item(self, cid, name, kind, tag):
        label = "单聊" if str(kind) in ("p2p", "single", "friend") else "群聊"
        it = QListWidgetItem("【%s】%s · %s" % (label, name, tag or "（未标注）"))
        it.setData(Qt.ItemDataRole.UserRole, cid)
        it.setData(Qt.ItemDataRole.UserRole + 1, tag)
        return it

    def _on_tag_selected(self, cur, _prev=None):
        if cur is None:
            return
        self.ed_tag.setText(str(cur.data(Qt.ItemDataRole.UserRole + 1) or ""))

    def _save_tag(self):
        cur = self.list_tags.currentItem()
        if cur is None:
            return
        cid = str(cur.data(Qt.ItemDataRole.UserRole) or "")
        if not cid:
            return
        tag = self.ed_tag.text().strip()
        cur.setData(Qt.ItemDataRole.UserRole + 1, tag)
        info = self._tag_by_id.get(cid) or {"id": cid, "name": cid, "kind": "group"}
        info["tag"] = tag
        self._tag_by_id[cid] = info
        cur.setText(self._tag_item(cid, info.get("name") or cid,
                                   info.get("kind") or "group", tag).text())
        self.tag_saved.emit(cid, tag)


class _MsgCard(QFrame):
    """一条消息卡片：勾选框 + 名称/时间/状态 + 正文（已处理的还带 AI 回复）。"""

    toggled = pyqtSignal(str, bool)

    def __init__(self, msg, checked=False, done=False, parent=None):
        super().__init__(parent)
        self.msgid = str(msg.get("msgid") or "")
        t = _tok()
        self.setObjectName("msgCard")
        self.setStyleSheet("QFrame#msgCard{background:%s;border:1px solid %s;"
                           "border-radius:6px;}" % (t["card"], t["border"]))
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(5)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.ck = QCheckBox()
        self.ck.setChecked(bool(checked))
        self.ck.toggled.connect(lambda on: self.toggled.emit(self.msgid, bool(on)))
        head.addWidget(self.ck)

        lb_name = QLabel(str(msg.get("name") or "钉钉"))
        f = lb_name.font()
        f.setBold(True)
        lb_name.setFont(f)
        lb_name.setStyleSheet("color:%s;" % t["text"])
        head.addWidget(lb_name)

        lb_time = QLabel(str(msg.get("time") or ""))
        lb_time.setStyleSheet("color:%s;font-size:12px;" % t["text_muted"])
        head.addWidget(lb_time)
        head.addStretch(1)

        st = str(msg.get("status") or "待处理")
        lb_st = QLabel(st)
        color = _IM_STATUS_COLORS.get(st) or t["text_muted"]
        lb_st.setStyleSheet("color:%s;font-size:12px;" % color)
        head.addWidget(lb_st)
        v.addLayout(head)

        lb_text = QLabel(str(msg.get("text") or ""))
        lb_text.setWordWrap(True)
        lb_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lb_text.setStyleSheet("color:%s;" % t["text"])
        v.addWidget(lb_text)

        if done:
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet("background:%s;border:none;" % t["border"])
            v.addWidget(line)
            cap = QLabel("AI 回复")
            cap.setStyleSheet("color:%s;font-size:12px;" % t["text_muted"])
            v.addWidget(cap)
            lb_rep = QLabel(str(msg.get("result") or "") or "（暂无回复）")
            lb_rep.setWordWrap(True)
            lb_rep.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            lb_rep.setStyleSheet("color:%s;" % t["text"])
            v.addWidget(lb_rep)

    def mousePressEvent(self, ev):
        # 点卡片空白处也能勾选（点勾选框本身时事件不会冒泡到这里）
        try:
            self.ck.setChecked(not self.ck.isChecked())
        except Exception:
            pass
        super().mousePressEvent(ev)


def _trim(s, n):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ===========================================================================
# 集成：钉钉 / MCP / 更新
# ===========================================================================
class IntegrationPage(QWidget):
    dingtalk_saved = pyqtSignal(dict)
    dingtalk_test = pyqtSignal(dict)
    dingtalk_stream = pyqtSignal(dict, bool)
    dingtalk_users_found = pyqtSignal(list, str)
    dingtalk_selftest = pyqtSignal(dict)
    dingtalk_contacts = pyqtSignal(dict)
    dingtalk_login = pyqtSignal(dict)
    dingtalk_group_send = pyqtSignal(dict, str)
    # —— dws（钉钉工作台 CLI：个人授权，能读会话列表/消息，群聊+单聊）——
    dingtalk_dws_login = pyqtSignal(bool)          # True=设备码登录，False=浏览器登录
    dingtalk_dws_conversations = pyqtSignal()
    dingtalk_dws_messages = pyqtSignal(str)        # openConversationId
    dingtalk_dws_send = pyqtSignal(str, str)       # target(群名/姓名/cid), content
    update_check = pyqtSignal()
    update_apply = pyqtSignal()      # 下载并自动安装待安装的更新

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = dict(dt_mod.DEFAULT_CONFIG)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(10)
        title = QLabel("⊕ 集成")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_dingtalk(), "钉钉")
        tabs.addTab(self._build_update(), "自动更新")
        tabs.addTab(self._build_mcp(), "MCP 扩展")
        outer.addWidget(tabs, 1)
        self.dingtalk_users_found.connect(self._on_dingtalk_users_found)

    # ------------------------------ 钉钉 ------------------------------
    def _build_dingtalk(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(12)

        guide = QLabel(
            "钉钉接入是<b>全免费</b>的，两种通道任选：<br>"
            "① <b>群机器人 Webhook</b>（最简单）：钉钉群里「群设置 → 智能群助手 → 添加机器人 "
            "→ 自定义」，安全设置建议选「加签」，把 Webhook 和密钥填到下面。<br>"
            "② <b>企业内部应用机器人</b>：在钉钉开放平台建应用并添加「机器人」能力，"
            "拿到 AppKey / AppSecret；接收消息请把机器人的接收模式设为 <b>Stream 模式</b>"
            "（不需要公网 IP）。")
        guide.setWordWrap(True)
        guide.setObjectName("PageSub")
        v.addWidget(guide)

        box = QGroupBox("基本设置")
        form = QFormLayout(box)
        self.dt_enabled = QCheckBox("启用钉钉推送")
        form.addRow(self.dt_enabled)
        self.dt_mode = QComboBox()
        self.dt_mode.addItem("群机器人 Webhook（最简单）", "webhook")
        self.dt_mode.addItem("企业内部应用机器人（AppKey/AppSecret）", "app")
        form.addRow("通道模式：", self.dt_mode)
        self.dt_tag = QLineEdit()
        self.dt_tag.setPlaceholderText("每条消息自动加的前缀，如【AI工作台】")
        form.addRow("消息前缀：", self.dt_tag)
        self.dt_kw = QLineEdit()
        self.dt_kw.setPlaceholderText("安全设置选「自定义关键词」时填（会自动加进消息）")
        form.addRow("关键词：", self.dt_kw)
        self.dt_atall = QCheckBox("@所有人")
        form.addRow(self.dt_atall)
        v.addWidget(box)

        box2 = QGroupBox("群机器人 Webhook")
        form2 = QFormLayout(box2)
        self.dt_webhook = QLineEdit()
        self.dt_webhook.setPlaceholderText("https://oapi.dingtalk.com/robot/send?access_token=…")
        form2.addRow("Webhook：", self.dt_webhook)
        self.dt_secret = QLineEdit()
        self.dt_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.dt_secret.setPlaceholderText("安全设置选「加签」时必填")
        form2.addRow("加签密钥：", self.dt_secret)
        v.addWidget(box2)

        box3 = QGroupBox("应用机器人")
        form3 = QFormLayout(box3)
        self.dt_cid = QLineEdit()
        self.dt_cid.setPlaceholderText("AppKey（ding… 开头）")
        form3.addRow("AppKey：", self.dt_cid)
        self.dt_csec = QLineEdit()
        self.dt_csec.setEchoMode(QLineEdit.EchoMode.Password)
        form3.addRow("AppSecret：", self.dt_csec)
        self.dt_robot = QLineEdit()
        self.dt_robot.setPlaceholderText("robotCode，一般与 AppKey 相同")
        form3.addRow("robotCode：", self.dt_robot)
        self.dt_agent = QLineEdit()
        self.dt_agent.setPlaceholderText("微应用 AgentId（发「工作通知」需要，纯数字）")
        self.dt_agent.setToolTip(
            "开发者后台 → 应用信息 里的 AgentId。填了就能用「工作通知」通道，\n"
            "比机器人更稳（不需要机器人在群里，直接推给指定成员）。")
        form3.addRow("AgentId：", self.dt_agent)
        self.dt_conv = QLineEdit()
        self.dt_conv.setPlaceholderText("群会话 openConversationId（发群里时填）")
        form3.addRow("群会话 ID：", self.dt_conv)
        self.dt_users = QLineEdit()
        self.dt_users.setPlaceholderText("接收人 userId，多个用逗号分隔（发单聊时填）")
        form3.addRow("接收人：", self.dt_users)
        urow = QHBoxLayout()
        self.dt_find_users = QPushButton("自动查我的 userId")
        self.dt_find_users.setToolTip(
            "把 AppKey/AppSecret 填好并保存后点这里，程序会去通讯录查真实可用的 userId。\n"
            "⚠ 接收人必须填 userId，填昵称（如「张三」）会报 staffId 不存在。")
        self.dt_find_users.clicked.connect(self._find_dingtalk_users)
        self.dt_copy_uid = QPushButton("复制到接收人")
        self.dt_copy_uid.setEnabled(False)
        self.dt_copy_uid.clicked.connect(self._copy_found_user)
        urow.addWidget(self.dt_find_users)
        urow.addWidget(self.dt_copy_uid)
        urow.addStretch(1)
        form3.addRow("", urow)
        self._found_users = []
        self.dt_recv = QCheckBox("启动时用 Stream 模式接收钉钉消息（在钉钉里直接和 AI 说话）")
        form3.addRow(self.dt_recv)
        v.addWidget(box3)

        row = QHBoxLayout()
        save = QPushButton("保存配置")
        save.clicked.connect(self._save_dingtalk)
        test = QPushButton("发送测试消息")
        test.clicked.connect(lambda: self.dingtalk_test.emit(self._collect_dingtalk()))
        stream = QPushButton("连接/断开 Stream")
        stream.clicked.connect(lambda: self.dingtalk_stream.emit(
            self._collect_dingtalk(), self.dt_recv.isChecked()))
        row.addWidget(save)
        row.addWidget(test)
        row.addWidget(stream)
        row.addStretch(1)
        v.addLayout(row)

        # ---------------- 连接器控制台（真调钉钉接口） ----------------
        box4 = QGroupBox("钉钉连接器 · 接口调试台")
        f4 = QVBoxLayout(box4)
        tip4 = QLabel(
            "这里是对着钉钉开放平台**真调接口**：哪一项通、哪一项缺权限、"
            "钉钉原话是什么，都会列出来，照着开权限即可。")
        tip4.setWordWrap(True)
        tip4.setObjectName("PageSub")
        f4.addWidget(tip4)

        brow = QHBoxLayout()
        self.dt_selftest = QPushButton("⊙ 一键接口自检")
        self.dt_selftest.setToolTip("逐项真实调用：凭据 / 通讯录 / 机器人 / 工作通知 / 群，把结果摊开")
        self.dt_selftest.clicked.connect(
            lambda: self.dingtalk_selftest.emit(self._collect_dingtalk()))
        self.dt_contacts = QPushButton("▩ 浏览通讯录")
        self.dt_contacts.setToolTip("列出部门与成员（真实姓名 + userId），可直接填成接收人")
        self.dt_contacts.clicked.connect(
            lambda: self.dingtalk_contacts.emit(self._collect_dingtalk()))
        self.dt_login = QPushButton("◎ 扫码授权登录")
        self.dt_login.setToolTip(
            "打开浏览器让钉钉账号扫码授权（OAuth2），登录后能拿到你的钉钉用户信息。\n"
            "需要在钉钉开发者后台把 http://127.0.0.1:8765/callback 加进「登录与分享 → 回调域名」。")
        self.dt_login.clicked.connect(
            lambda: self.dingtalk_login.emit(self._collect_dingtalk()))
        brow.addWidget(self.dt_selftest)
        brow.addWidget(self.dt_contacts)
        brow.addWidget(self.dt_login)
        brow.addStretch(1)
        f4.addLayout(brow)

        grow = QHBoxLayout()
        self.dt_group_msg = QLineEdit()
        self.dt_group_msg.setPlaceholderText("要发到群里的消息内容（用上面的「群会话 ID」）")
        self.dt_group_send = QPushButton("发到群里")
        self.dt_group_send.clicked.connect(
            lambda: self.dingtalk_group_send.emit(
                self._collect_dingtalk(), self.dt_group_msg.text().strip()))
        grow.addWidget(self.dt_group_msg, 1)
        grow.addWidget(self.dt_group_send)
        f4.addLayout(grow)

        self.dt_probe = QTextBrowser()
        self.dt_probe.setMinimumHeight(150)
        self.dt_probe.setOpenExternalLinks(True)
        self.dt_probe.setHtml(
            f"<span style='color:{themes.tokens()['text_muted']}'>"
            "点「一键接口自检」开始。</span>")
        f4.addWidget(self.dt_probe)
        v.addWidget(box4)

        # ---------------- dws 工作台 CLI：个人授权，能读会话列表/消息 ----------------
        box5 = QGroupBox("钉钉工作台 CLI（dws · 个人授权，能读会话列表）")
        f5 = QVBoxLayout(box5)
        tip5 = QLabel(
            "这条路<b>不需要企业应用</b>：用你本人的钉钉账号在浏览器里授权一次，"
            "就能像 WorkBuddy 那样<b>列出并读取你的会话列表和消息</b>（群聊 + 单聊），"
            "也能发消息。底层是官方 DingTalk Workspace CLI（dws）。")
        tip5.setWordWrap(True)
        tip5.setObjectName("PageSub")
        f5.addWidget(tip5)

        lrow = QHBoxLayout()
        self.dt_dws_login = QPushButton("◎ 用 dws 登录钉钉")
        self.dt_dws_login.setToolTip("打开浏览器用你的钉钉账号 OAuth 授权；授权后可读写你的会话/消息")
        self.dt_dws_login.clicked.connect(lambda: self.dingtalk_dws_login.emit(False))
        self.dt_dws_login_device = QPushButton("⚿ 设备码登录")
        self.dt_dws_login_device.setToolTip("无法开浏览器时用：命令行输出授权链接+码，你去浏览器输入")
        self.dt_dws_login_device.clicked.connect(lambda: self.dingtalk_dws_login.emit(True))
        self.dt_dws_convs = QPushButton("▥ 查看我的会话列表")
        self.dt_dws_convs.clicked.connect(lambda: self.dingtalk_dws_conversations.emit())
        lrow.addWidget(self.dt_dws_login)
        lrow.addWidget(self.dt_dws_login_device)
        lrow.addWidget(self.dt_dws_convs)
        lrow.addStretch(1)
        f5.addLayout(lrow)

        self.dt_dws_log = QLabel("")
        self.dt_dws_log.setWordWrap(True)
        self.dt_dws_log.setObjectName("PageSub")
        f5.addWidget(self.dt_dws_log)
        hint5 = QLabel("☼ 如果浏览器授权页面一直显示「出错」或打不开（本机代理/安全软件"
                       "拦截了本地回调），请改点上面的「⚿ 设备码登录」——它会给你一个"
                       "链接和一串码，在任意浏览器里打开输入即可，不走本地回调。")
        hint5.setWordWrap(True)
        hint5.setObjectName("PageSub")
        f5.addWidget(hint5)

        self.dt_dws_list = QListWidget()
        self.dt_dws_list.setMinimumHeight(140)
        self.dt_dws_list.itemDoubleClicked.connect(self._dws_open_conversation)
        f5.addWidget(self.dt_dws_list)

        self.dt_dws_msgs = QTextBrowser()
        self.dt_dws_msgs.setMinimumHeight(140)
        f5.addWidget(self.dt_dws_msgs)

        srow = QHBoxLayout()
        self.dt_dws_target = QLineEdit()
        self.dt_dws_target.setPlaceholderText("发给谁：群名 / 姓名 / openConversationId")
        self.dt_dws_content = QLineEdit()
        self.dt_dws_content.setPlaceholderText("消息内容…（回车发送）")
        self.dt_dws_content.returnPressed.connect(self._dws_send)
        self.dt_dws_send = QPushButton("发送")
        self.dt_dws_send.clicked.connect(self._dws_send)
        srow.addWidget(self.dt_dws_target, 2)
        srow.addWidget(self.dt_dws_content, 3)
        srow.addWidget(self.dt_dws_send)
        f5.addLayout(srow)
        v.addWidget(box5)

        self.dt_status = QLabel("")
        self.dt_status.setWordWrap(True)
        self.dt_status.setObjectName("PageSub")
        v.addWidget(self.dt_status)

        note = QLabel("小贴士：自定义机器人限流为每机器人每分钟 20 条；"
                      "如果暂时用不上，保持「启用」不勾选即可，不影响其它功能。")
        note.setWordWrap(True)
        note.setObjectName("PageSub")
        v.addWidget(note)
        v.addStretch(1)

        scroll.setWidget(inner)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(scroll)
        return page

    def load_dingtalk(self, cfg):
        self.cfg = dict(dt_mod.DEFAULT_CONFIG)
        self.cfg.update({k: v for k, v in (cfg or {}).items() if k in dt_mod.DEFAULT_CONFIG})
        c = self.cfg
        self.dt_enabled.setChecked(bool(c.get("enabled")))
        idx = self.dt_mode.findData(c.get("mode") or "webhook")
        self.dt_mode.setCurrentIndex(max(0, idx))
        self.dt_tag.setText(c.get("push_tag", ""))
        self.dt_kw.setText(c.get("keyword", ""))
        self.dt_atall.setChecked(bool(c.get("at_all")))
        self.dt_webhook.setText(c.get("webhook", ""))
        self.dt_secret.setText(c.get("secret", ""))
        self.dt_cid.setText(c.get("client_id", ""))
        self.dt_csec.setText(c.get("client_secret", ""))
        self.dt_robot.setText(c.get("robot_code", ""))
        self.dt_agent.setText(str(c.get("agent_id", "") or ""))
        self.dt_conv.setText(c.get("open_conversation_id", ""))
        self.dt_users.setText(c.get("user_ids", ""))
        self.dt_recv.setChecked(bool(c.get("receive_enabled")))

    def _collect_dingtalk(self):
        return {
            "enabled": self.dt_enabled.isChecked(),
            "mode": self.dt_mode.currentData(),
            "push_tag": self.dt_tag.text().strip(),
            "keyword": self.dt_kw.text().strip(),
            "at_all": self.dt_atall.isChecked(),
            "webhook": self.dt_webhook.text().strip(),
            "secret": self.dt_secret.text().strip(),
            "client_id": self.dt_cid.text().strip(),
            "client_secret": self.dt_csec.text().strip(),
            "robot_code": self.dt_robot.text().strip(),
            "agent_id": self.dt_agent.text().strip(),
            "open_conversation_id": self.dt_conv.text().strip(),
            "user_ids": self.dt_users.text().strip(),
            "receive_enabled": self.dt_recv.isChecked(),
        }

    # ---------------- 接口调试台的结果展示 ----------------
    def set_probe_html(self, html):
        self.dt_probe.setHtml(html)

    def show_probe_items(self, items, title="接口自检结果"):
        """items = [(项目, ok, 说明)]"""
        t = themes.tokens()
        rows = []
        okn = sum(1 for _n, ok, _m in items if ok)
        rows.append(f"<div style='font-weight:bold;margin-bottom:6px;'>"
                    f"{_esc(title)}：通过 {okn} / {len(items)}</div>")
        rows.append("<table cellspacing='0' cellpadding='4' width='100%'>")
        for name, ok, msg in items:
            color = t["success"] if ok else t["danger"]
            mark = "✓" if ok else "✕"
            rows.append(
                f"<tr><td valign='top' style='white-space:nowrap;color:{color};'>"
                f"{mark} <b>{_esc(name)}</b></td>"
                f"<td style='color:{t['text']};'>{_esc(str(msg))}</td></tr>")
        rows.append("</table>")
        self.dt_probe.setHtml("".join(rows))

    def show_probe_text(self, text):
        t = themes.tokens()
        self.dt_probe.setHtml(
            f"<pre style='white-space:pre-wrap;font-family:Consolas,Microsoft YaHei;"
            f"color:{t['text']};'>{_esc(text)}</pre>")

    def _save_dingtalk(self):
        cfg = self._collect_dingtalk()
        self.cfg = cfg
        self.dingtalk_saved.emit(cfg)
        self.set_dingtalk_status("配置已保存。")

    def _find_dingtalk_users(self):
        """去通讯录查真实可用的 userId（后台线程，别卡界面）。"""
        cfg = self._collect_dingtalk()
        if not (cfg.get("client_id") and cfg.get("client_secret")):
            self.set_dingtalk_status("请先填好 AppKey / AppSecret（并点「保存配置」）。")
            return
        self.dt_find_users.setEnabled(False)
        self.set_dingtalk_status("正在去钉钉通讯录查接收人…")
        import threading

        def worker():
            try:
                cli = dt_mod.DingTalkClient(cfg)
                users = cli.list_user_ids()
                if not users:
                    self.dingtalk_users_found.emit(
                        [], "没查到成员。请确认应用已开通「通讯录」成员信息读权限；"
                            "或者改用「群机器人 Webhook」方式（更简单）。")
                    return
                parts = []
                for u in users[:10]:
                    nm = cli.lookup_user_name(u)
                    parts.append(f"{u}（{nm}）" if nm else u)
                self.dingtalk_users_found.emit(
                    users, "查到可用接收人：" + "、".join(parts)
                    + ("　…" if len(users) > 10 else "")
                    + "　→ 点「复制到接收人」即可。")
            except Exception as e:
                self.dingtalk_users_found.emit([], f"查询失败：{e}")
        threading.Thread(target=worker, daemon=True).start()

    def _on_dingtalk_users_found(self, users, text):
        self.dt_find_users.setEnabled(True)
        self._found_users = list(users or [])
        self.dt_copy_uid.setEnabled(bool(self._found_users))
        self.set_dingtalk_status(text)

    def _copy_found_user(self):
        if not self._found_users:
            return
        self.dt_users.setText(",".join(self._found_users[:3]))
        self.set_dingtalk_status(
            "已填入接收人：" + self.dt_users.text() + "　→ 记得点「保存配置」，再点「发送测试消息」。")

    def set_dingtalk_status(self, text):
        self.dt_status.setText(text)

    # ---------------- dws 工作台 CLI 的界面响应 ----------------
    def _dws_open_conversation(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        if cid:
            self.dingtalk_dws_messages.emit(cid)

    def _dws_send(self):
        target = self.dt_dws_target.text().strip()
        content = self.dt_dws_content.text().strip()
        if not target or not content:
            self.show_dws_login_log("请填写「发给谁」和消息内容。")
            return
        self.dingtalk_dws_send.emit(target, content)
        self.dt_dws_content.clear()

    def show_dws_conversations(self, convs):
        """convs = [(会话名, openConversationId), ...]"""
        self.dt_dws_list.clear()
        if not convs:
            self.show_dws_login_log("没有会话，或 dws 还没登录 / 没权限。")
            return
        for name, cid in convs:
            it = QListWidgetItem(name or "(未命名会话)")
            it.setData(Qt.ItemDataRole.UserRole, cid)
            self.dt_dws_list.addItem(it)
        self.show_dws_login_log(f"共 {len(convs)} 个会话，双击可查看消息。")

    def show_dws_messages(self, cid, msgs):
        """msgs = [(发送者, 文本, 时间), ...]"""
        t = themes.tokens()
        if not msgs:
            self.dt_dws_msgs.setHtml(
                f"<span style='color:{t['text_muted']}'>这个会话没有可读到消息"
                f"（可能没权限，或用设备码登录后需要再授权 chat 业务权限）。</span>")
            return
        rows = []
        for sender, text, ts in msgs:
            rows.append(
                f"<div style='margin:4px 0;'>"
                f"<span style='color:{t['accent']};font-weight:bold;'>"
                f"{_esc(str(sender))}</span> "
                f"<span style='color:{t['text_muted']};font-size:11px;'>{_esc(str(ts))}</span><br>"
                f"<span style='color:{t['text']};'>{_esc(str(text))}</span></div>")
        self.dt_dws_msgs.setHtml("".join(rows))

    def show_dws_login_log(self, line):
        cur = self.dt_dws_log.text()
        if cur:
            self.dt_dws_log.setText(cur + "\n" + str(line))
        else:
            self.dt_dws_log.setText(str(line))

    # ------------------------------ 自动更新 ------------------------------
    def _build_update(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(12)
        tip = QLabel(
            "更新检查走 GitHub Releases（免费）。你只需要在自己的 GitHub 仓库里"
            "发布 Release、把 exe/zip 作为附件传上去，这里就能自动发现并给出下载入口。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)

        box = QGroupBox("设置")
        form = QFormLayout(box)
        self.up_repo = QLineEdit()
        self.up_repo.setPlaceholderText("用户名/仓库名，例如 lhclhc123/AIWorkbench")
        form.addRow("GitHub 仓库：", self.up_repo)
        self.up_start = QCheckBox("启动时自动检查一次更新")
        form.addRow(self.up_start)
        self.up_token = QLineEdit()
        self.up_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.up_token.setPlaceholderText("可选。私有仓库才需要；本机会用 DPAPI 加密保存")
        form.addRow("GitHub Token：", self.up_token)
        v.addWidget(box)

        row = QHBoxLayout()
        save = QPushButton("保存")
        save.clicked.connect(self._save_update)
        check = QPushButton("立即检查更新")
        check.clicked.connect(self.update_check.emit)
        apply_btn = QPushButton("⇩ 下载并自动安装更新")
        apply_btn.setToolTip("检查到新版本后点这个：自动下载 → 退出 → 替换文件 → 重启新版本")
        apply_btn.clicked.connect(self.update_apply.emit)
        row.addWidget(save)
        row.addWidget(check)
        row.addWidget(apply_btn)
        row.addStretch(1)
        v.addLayout(row)

        self.up_result = QTextBrowser()
        self.up_result.setMinimumHeight(200)
        v.addWidget(self.up_result, 1)
        v.addStretch(1)
        return page

    def load_update(self, settings):
        self.up_repo.setText(settings.get("update_repo") or ver.GITHUB_REPO)
        self.up_start.setChecked(bool(settings.get("check_update_on_start", True)))
        self.up_token.setText(settings.get("github_token") or "")

    def _save_update(self):
        self.update_saved.emit({
            "update_repo": self.up_repo.text().strip() or ver.GITHUB_REPO,
            "check_update_on_start": self.up_start.isChecked(),
            "github_token": self.up_token.text().strip(),
        })

    update_saved = pyqtSignal(dict)

    def show_update_result(self, info):
        t = themes.tokens()
        if not info.get("ok"):
            self.up_result.setHtml(
                f'<div style="color:{t["danger"]};">检查失败：{_esc(info.get("error"))}<br>'
                f'（如果你还没在 GitHub 上建仓库/发 Release，这就是正常的。'
                f'仓库地址：{_esc(info.get("repo", ""))}）</div>')
            return
        if info.get("has_update"):
            head = (f'<div style="font-size:16px;font-weight:bold;color:{t["success"]};">'
                    f'✧ 发现新版本 {_esc(info.get("tag"))}（当前 {_esc(info.get("current"))}）</div>')
        else:
            head = (f'<div style="font-size:15px;font-weight:bold;color:{t["accent_text"]};">'
                    f'✓ 已是最新版本（{_esc(info.get("current"))}）</div>')
        notes = (info.get("notes") or "").strip()
        assets = info.get("assets") or []
        html = [head,
                f'<div style="margin-top:8px;color:{t["text_muted"]};font-size:12px;">'
                f'来源：{_esc(info.get("source"))}　发布时间：{_esc(info.get("published_at"))}'
                f'</div>',
                f'<div style="margin-top:10px;"><b>发布页：</b>'
                f'<a href="{_esc(info.get("html_url"))}">{_esc(info.get("html_url"))}</a></div>']
        if assets:
            html.append('<div style="margin-top:10px;"><b>可下载文件：</b></div>')
            for a in assets[:8]:
                mb = a.get("size", 0) / 1048576
                html.append(f'<div style="margin-left:14px;">'
                            f'<a href="{_esc(a.get("url"))}">{_esc(a.get("name"))}</a>'
                            f' <span style="color:{t["text_muted"]};">({mb:.1f} MB)</span></div>')
        if notes:
            html.append(f'<div style="margin-top:14px;"><b>更新说明：</b></div>'
                        f'<pre style="white-space:pre-wrap;font-size:12px;'
                        f'color:{t["text"]};">{_esc(notes[:3000])}</pre>')
        self.up_result.setOpenExternalLinks(True)
        self.up_result.setHtml("".join(html))

    # ------------------------------ MCP ------------------------------
    def _build_mcp(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.mcp = None
        tip = QLabel("MCP 让你接入现成的外部能力（GitHub、数据库、自建服务等）。"
                     "配置文件写在<b>工作区根目录的 mcp.json</b>。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)
        self.mcp_path = QLabel("")
        self.mcp_path.setObjectName("PageSub")
        self.mcp_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.mcp_path)
        row = QHBoxLayout()
        row.addWidget(self._btn2("打开配置文件", self._open_mcp_file))
        row.addWidget(self._btn2("扫描工具", self._scan_mcp))
        row.addWidget(self._btn2("重新载入", self._reload_mcp))
        row.addStretch(1)
        v.addLayout(row)
        self.mcp_view = QTextBrowser()
        v.addWidget(self.mcp_view, 1)
        return page

    @staticmethod
    def _btn2(text, slot):
        b = QPushButton(text)
        b.setObjectName("cardBtn")
        b.setFixedHeight(28)
        b.clicked.connect(slot)
        return b

    def bind_mcp(self, manager, ws_path):
        self.mcp = manager
        path = os.path.join(ws_path, "mcp.json")
        self.mcp_path.setText("配置文件：" + path)
        self._reload_mcp()

    def _open_mcp_file(self):
        from .. import agent as agent_mod
        path = os.path.join(self.mcp_path.text().replace("配置文件：", "").strip())
        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write('{\n  "mcpServers": {\n  }\n}\n')
            except Exception as e:
                QMessageBox.warning(self, "打不开", str(e))
                return
        agent_mod.AgentRunner(os.path.dirname(path))._dispatch(
            "open_path", {"path": path}, None)

    def _reload_mcp(self):
        if self.mcp is None:
            return
        self.mcp.reload()
        cfg = self.mcp.configured()
        if not cfg:
            self.mcp_view.setHtml('<div style="color:#888;">（还没有配置任何 MCP 服务器）</div>')
            return
        html = []
        for name, c in cfg.items():
            html.append(f'<div style="margin:6px 0;"><b>{_esc(name)}</b><br>'
                        f'<span style="font-size:12px;">{_esc(c.get("command"))} '
                        f'{_esc(" ".join(map(str, c.get("args") or [])))}</span></div>')
        self.mcp_view.setHtml("".join(html))

    def _scan_mcp(self):
        if self.mcp is None:
            return
        self.mcp_view.setHtml('<div style="color:#888;">正在启动 MCP 服务器并拉取工具…</div>')
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        tools = self.mcp.list_all_tools()
        if not tools:
            extra = (f'<br><span style="color:#c33;">{_esc(self.mcp.last_error)}</span>'
                     if self.mcp.last_error else "")
            self.mcp_view.setHtml('<div style="color:#888;">没有取到工具。</div>' + extra)
            return
        html = [f'<div>共 {len(tools)} 个工具：</div>']
        cur = None
        for t in tools:
            if t["server"] != cur:
                cur = t["server"]
                html.append(f'<div style="margin-top:8px;"><b>【{_esc(cur)}】</b></div>')
            html.append(f'<div style="margin-left:14px;"><b>{_esc(t["name"])}</b>'
                        f'<span style="font-size:12px;"> — {_esc((t["description"] or "")[:160])}'
                        f'</span></div>')
        self.mcp_view.setHtml("".join(html))


# ===========================================================================
# 设置
# ===========================================================================
class SettingsPage(QWidget):
    saved = pyqtSignal(dict)
    probe_requested = pyqtSignal()
    voice_test = pyqtSignal(str)        # tts 试听
    # v9.14.0：DeepSeek 网页版区块的三个动作（由主窗口接线）
    web_launch = pyqtSignal()
    web_selftest = pyqtSignal()
    web_retry = pyqtSignal()
    # v9.14.2：窗口显隐
    web_show = pyqtSignal()
    web_hide = pyqtSignal()
    # v9.15.0：GitHub 检测
    gh_test = pyqtSignal(str)           # 传 Token（可为空 = 用本机 gh 登录态）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(10)
        head = QHBoxLayout()
        title = QLabel("⚙ 设置")
        title.setObjectName("PageTitle")
        head.addWidget(title)
        head.addStretch(1)
        save = QPushButton("保存全部设置")
        save.setFixedHeight(32)
        save.clicked.connect(self._save)
        head.addWidget(save)
        outer.addLayout(head)

        tabs = QTabWidget()
        tabs.addTab(self._tab_general(), "通用")
        tabs.addTab(self._tab_model(), "模型")
        tabs.addTab(self._tab_voice(), "语音")
        tabs.addTab(self._tab_keys(), "密钥")
        tabs.addTab(self._tab_prompt(), "提示词")
        outer.addWidget(tabs, 1)

    # ---------------- 通用 ----------------
    def _tab_general(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(12)

        box = QGroupBox("界面")
        f = QFormLayout(box)
        self.theme_cb = QComboBox()
        self.theme_cb.addItem("浅色", "light")
        self.theme_cb.addItem("深色", "dark")
        f.addRow("主题：", self.theme_cb)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(85, 130)
        self.font_spin.setSuffix(" %")
        f.addRow("界面缩放：", self.font_spin)
        v.addWidget(box)

        box_tray = QGroupBox("托盘与全局热键")
        ft = QFormLayout(box_tray)
        self.tray_enabled = QCheckBox("常驻系统托盘（右下角图标）")
        ft.addRow(self.tray_enabled)
        self.tray_on_close = QCheckBox("点关闭按钮时收进托盘，而不是退出")
        ft.addRow(self.tray_on_close)
        self.hotkey_show = QLineEdit()
        self.hotkey_show.setPlaceholderText("ctrl+alt+space")
        ft.addRow("呼出窗口热键：", self.hotkey_show)
        self.hotkey_voice = QLineEdit()
        self.hotkey_voice.setPlaceholderText("ctrl+alt+v")
        ft.addRow("语音输入热键：", self.hotkey_voice)
        hint = QLabel("写法示例：ctrl+alt+space、ctrl+shift+F9、win+alt+A。"
                      "留空表示不启用。改完保存后立刻生效（被占用的组合键会在状态栏提示）。")
        hint.setWordWrap(True)
        hint.setObjectName("PageSub")
        ft.addRow("", hint)
        v.addWidget(box_tray)

        box2 = QGroupBox("对话行为")
        f2 = QFormLayout(box2)
        self.agent_cb = QCheckBox("默认开启 Agent 模式（能真正调用工具干活）")
        f2.addRow(self.agent_cb)
        self.search_cb = QCheckBox("默认开启联网搜索")
        f2.addRow(self.search_cb)
        self.policy_cb = QComboBox()
        self.policy_cb.addItem("严格：只用我选的模型，失败就报错（推荐）", "strict")
        self.policy_cb.addItem("自动切换：失败时换别的模型，但会明确提示", "fallback")
        f2.addRow("指定模型失败时：", self.policy_cb)
        v.addWidget(box2)
        v.addStretch(1)
        scroll.setWidget(inner)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(scroll)
        return page

    # ---------------- 模型 ----------------
    def _tab_model(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        tip = QLabel("这里可以逐个端点体检，看哪个 Key 还能用、能用哪些模型。"
                     "（有些端点只开通了部分模型，体检结果以实际返回为准。）")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)
        row = QHBoxLayout()
        btn = QPushButton("开始体检")
        btn.clicked.connect(self.probe_requested.emit)
        row.addWidget(btn)
        row.addStretch(1)
        v.addLayout(row)

        # ---------------- DeepSeek 网页版（v9.14.0 新增区块） ----------------
        # 规范对齐 PRD 第 4 节：状态行 + 三个按钮 + 灰色提示 + 风险说明。
        box = QGroupBox("DeepSeek 网页版")
        bv = QVBoxLayout(box)
        bv.setSpacing(8)

        srow = QHBoxLayout()
        self.web_dot = QLabel("●")
        self.web_dot.setStyleSheet("color:#9e9e9e;font-size:15px;")
        self.web_state = QLabel("状态：网页未启动")
        self.web_state.setStyleSheet("font-size:13px;")
        srow.addWidget(self.web_dot)
        srow.addWidget(self.web_state)
        srow.addStretch(1)
        bv.addLayout(srow)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.web_btn_launch = QPushButton("启动/打开网页")
        self.web_btn_launch.setFixedHeight(30)
        self.web_btn_launch.clicked.connect(self.web_launch.emit)
        self.web_btn_selftest = QPushButton("连通性自检")
        self.web_btn_selftest.setFixedHeight(30)
        self.web_btn_selftest.clicked.connect(self.web_selftest.emit)
        self.web_btn_retry = QPushButton("重试")
        self.web_btn_retry.setFixedHeight(30)
        self.web_btn_retry.clicked.connect(self.web_retry.emit)
        # v9.14.2：窗口显隐（自动拉起后默认离屏，要看的时候挪回来）
        self.web_btn_show = QPushButton("显示网页窗口")
        self.web_btn_show.setFixedHeight(30)
        self.web_btn_show.clicked.connect(self.web_show.emit)
        self.web_btn_hide = QPushButton("收起窗口")
        self.web_btn_hide.setFixedHeight(30)
        self.web_btn_hide.clicked.connect(self.web_hide.emit)
        brow.addWidget(self.web_btn_launch)
        brow.addWidget(self.web_btn_selftest)
        brow.addWidget(self.web_btn_retry)
        brow.addWidget(self.web_btn_show)
        brow.addWidget(self.web_btn_hide)
        brow.addStretch(1)
        bv.addLayout(brow)

        web_hint = QLabel("不用手动开浏览器：程序会自己把 Chrome 拉起来（默认后台无头，"
                          "桌面上不会多出窗口）。"
                          "只有这一次需要你在网页里登录一下 DeepSeek（手机验证码 / 微信扫码），"
                          "登录态会保存在专用配置里，以后一直免登。\n"
                          "要亲眼看页面时点「显示网页窗口」，看完点「收起窗口」收回后台"
                          "（切换模式会重启浏览器，但不会掉登录）。")
        web_hint.setWordWrap(True)
        web_hint.setObjectName("PageSub")
        bv.addWidget(web_hint)

        self.web_selftest_view = QTextBrowser()
        self.web_selftest_view.setVisible(False)
        self.web_selftest_view.setFixedHeight(120)
        bv.addWidget(self.web_selftest_view)

        self.web_auto_cb = QCheckBox("程序启动 / 切到本模型时，自动拉起浏览器并接入"
                                     "（免手动；关闭后需自己点「启动/打开网页」）")
        bv.addWidget(self.web_auto_cb)
        self.web_silent_cb = QCheckBox("后台无头运行 —— 桌面不留浏览器窗口"
                                       "（推荐；发送与收答复完全正常）")
        bv.addWidget(self.web_silent_cb)

        web_risk = QLabel("ⓘ 本功能通过自动化驱动本机 Chrome 页面工作，非官方 API。"
                          "DeepSeek 网页若改版可能暂时失效；账号存在被判定为异常使用的潜在风险。")
        web_risk.setWordWrap(True)
        web_risk.setObjectName("PageSub")
        bv.addWidget(web_risk)

        # ---------------- v9.15.0：GitHub ----------------
        gh_box = QGroupBox("GitHub（让 AI 直接读你的仓库 / 搜代码 / 看 issue）")
        gv = QVBoxLayout(gh_box)
        gh_hint = QLabel("优先用本机已登录的 gh 命令行（免配置就能读你自己的仓库）。"
                         "没装 gh、或要读私有仓库 / 用搜索，才需要填下面的 Token。\n"
                         "Token 只存在本机工作区设置里，不会上传到任何地方。")
        gh_hint.setWordWrap(True)
        gh_hint.setObjectName("PageSub")
        gv.addWidget(gh_hint)
        gh_row = QHBoxLayout()
        gh_row.addWidget(QLabel("Token："))
        self.gh_token_edit = QLineEdit()
        self.gh_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.gh_token_edit.setPlaceholderText("可留空（本机 gh 已登录时不需要）；形如 ghp_xxx")
        gh_row.addWidget(self.gh_token_edit, 1)
        self.gh_btn_test = QPushButton("检测连接")
        self.gh_btn_test.setFixedHeight(30)
        self.gh_btn_test.clicked.connect(
            lambda: self.gh_test.emit(self.gh_token_edit.text().strip()))
        gh_row.addWidget(self.gh_btn_test)
        gv.addLayout(gh_row)
        self.gh_status = QLabel("尚未检测。点「检测连接」看能不能读到 GitHub。")
        self.gh_status.setWordWrap(True)
        self.gh_status.setObjectName("PageSub")
        gv.addWidget(self.gh_status)
        bv.addWidget(gh_box)
        self.gh_box = gh_box

        v.addWidget(box)

        self.probe_view = QTextBrowser()
        v.addWidget(self.probe_view, 1)
        return page

    def set_gh_status(self, ok, text):
        """主窗口回填 GitHub 检测结果（v9.15.0）。"""
        try:
            self.gh_status.setText(("✓ " if ok else "⚠ ") + str(text))
        except Exception:
            pass

    def set_web_status(self, kind, detail=""):
        """更新「DeepSeek 网页版」状态行（圆点颜色 + 文案）。kind 为 WebErrorKind 值或短名。"""
        try:
            from ..deepseek_web import errors as _dw_errors
            wkind = _dw_errors.WebErrorKind(kind) if not isinstance(
                kind, _dw_errors.WebErrorKind) else kind
            label = _dw_errors.label_of(wkind)
        except Exception:
            label = "未知"
            wkind = None
        color = "#2e7d32"   # 绿
        try:
            name = wkind.value if wkind is not None else str(kind)
        except Exception:
            name = str(kind)
        if name in ("not_logged_in",):
            color = "#f9a825"       # 黄
        elif name in ("not_started", "not_installed"):
            color = "#9e9e9e"       # 灰
        elif name in ("window_hidden",):
            color = "#ef6c00"       # 橙：看着就绪，实际发不出去
        elif name in ("timeout", "page_changed", "duplicate", "other"):
            color = "#c62828"       # 红
        text = f"状态：{label}"
        detail = (detail or "").strip()
        if detail and detail not in text:
            text += f"（{detail[:40]}）"
        try:
            self.web_dot.setStyleSheet(f"color:{color};font-size:15px;")
            self.web_state.setText(text)
        except Exception:
            pass

    def show_web_selftest(self, results):
        """把自检结果渲染进区块（逐项 ✓ / ✗ + 中文说明）。"""
        rows = ['<table style="font-size:13px;border-collapse:collapse;">']
        for item in results or []:
            ok = item.get("ok")
            mark = "✓" if ok else "✕"
            color = "#2e7d32" if ok else "#c62828"
            rows.append(
                f'<tr><td style="padding:4px 12px 4px 0;"><b>{_esc(item.get("name",""))}</b></td>'
                f'<td style="color:{color};padding:4px 8px;">{mark}</td>'
                f'<td style="padding:4px 0;">{_esc(item.get("detail",""))}</td></tr>')
        rows.append("</table>")
        try:
            self.web_selftest_view.setHtml("".join(rows))
            self.web_selftest_view.setVisible(True)
        except Exception:
            pass

    def show_probe(self, results):
        t = themes.tokens()
        html = ['<table style="font-size:13px;border-collapse:collapse;">']
        for label, ok, msg in results:
            if ok is None:
                color, mark = t["text_muted"], "—"
            elif ok:
                color, mark = t["success"], "✓"
            else:
                color, mark = t["danger"], "✕"
            html.append(
                f'<tr><td style="padding:5px 14px 5px 0;"><b>{_esc(label)}</b></td>'
                f'<td style="color:{color};padding:5px 10px;">{mark}</td>'
                f'<td style="color:{t["text"]};padding:5px 0;">{_esc(msg)}</td></tr>')
        html.append("</table>")
        self.probe_view.setHtml("".join(html))

    # ---------------- 语音 ----------------
    def _tab_voice(self):
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(12)

        box = QGroupBox("语音输入（说话变文字）")
        f = QFormLayout(box)
        self.asr_cb = QComboBox()
        for key, label in asr_mod.BACKEND_LABELS.items():
            self.asr_cb.addItem(label, key)
        f.addRow("识别引擎：", self.asr_cb)
        self.mic_cb = QComboBox()
        self.mic_cb.addItem("系统默认麦克风", -1)
        self._devices = asr_mod.list_input_devices()
        for d in self._devices:
            self.mic_cb.addItem(f"{d['name']}（{d['channels']} 声道）", d["index"])
        f.addRow("麦克风：", self.mic_cb)
        self.silence_spin = QDoubleSpinBoxCompat(0.5, 30.0, 0.5, 10.0)
        f.addRow("静音多久算说完：", self.silence_spin)
        self.max_rec_spin = QSpinBox()
        self.max_rec_spin.setRange(30, 1800)
        self.max_rec_spin.setSingleStep(30)
        self.max_rec_spin.setSuffix(" 秒")
        self.max_rec_spin.setValue(300)
        self.max_rec_spin.setToolTip("单次录音的最长时间，到点自动停；中途也可以点麦克风手动结束")
        f.addRow("单次最长录音：", self.max_rec_spin)
        hint2 = QLabel("建议：静音判定别低于 2 秒，否则说话中途想词会被提前掐断；"
                       "长口述把「单次最长录音」调大即可。")
        hint2.setWordWrap(True)
        hint2.setObjectName("PageSub")
        f.addRow(hint2)
        self.auto_send_cb = QCheckBox("说完自动发送（不用再点发送）")
        f.addRow(self.auto_send_cb)
        info = QLabel(f"当前可用引擎：" +
                      ("、".join(asr_mod.available_backends(
                          config.DEFAULT_API_KEYS)) or "（无）") +
                      "　—　智谱 Key 已内置，可直接用。")
        info.setWordWrap(True)
        info.setObjectName("PageSub")
        f.addRow(info)
        v.addWidget(box)

        box2 = QGroupBox("语音输出（朗读回复）")
        f2 = QFormLayout(box2)
        self.tts_on = QCheckBox("AI 回复后自动朗读")
        f2.addRow(self.tts_on)
        self.tts_eng = QComboBox()
        for key, label in config.TTS_ENGINES:
            self.tts_eng.addItem(label, key)
        f2.addRow("朗读引擎：", self.tts_eng)
        self.tts_voice = QComboBox()
        self.tts_voice.addItem("默认（晓晓）", "")
        for vid, label in tts_mod.EDGE_VOICES:
            self.tts_voice.addItem(label, vid)
        f2.addRow("Edge 音色：", self.tts_voice)
        self.tts_rate = QSpinBox()
        self.tts_rate.setRange(-10, 10)
        f2.addRow("语速（离线引擎）：", self.tts_rate)
        row = QHBoxLayout()
        test = QPushButton("试听")
        test.setObjectName("cardBtn")
        test.setFixedHeight(28)
        test.clicked.connect(lambda: self.voice_test.emit(self._tts_engine()))
        row.addWidget(test)
        row.addStretch(1)
        f2.addRow(row)
        v.addWidget(box2)
        v.addStretch(1)
        scroll.setWidget(inner)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(scroll)
        return page

    def _tts_engine(self):
        return self.tts_eng.currentData() or "sapi"

    # ---------------- 密钥 ----------------
    def _tab_keys(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        tip = QLabel("下面这些 Key 只保存在你本地，并且会用 <b>Windows DPAPI 加密</b>后落盘"
                     "（绑定当前 Windows 账户，别人拷走文件也解不开）。"
                     "内置的默认 Key 以密文形式编译在 exe 里，不是明文。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)
        box = QGroupBox("API Keys")
        f = QFormLayout(box)
        self.key_edits = {}
        for p in config.PROVIDERS:
            e = QLineEdit()
            e.setEchoMode(QLineEdit.EchoMode.Password)
            e.setPlaceholderText(_key_hint(p["name"]))
            f.addRow(f"{p['label']}：", e)
            self.key_edits[p["name"]] = e
        v.addWidget(box)
        v.addStretch(1)
        return page

    # ---------------- 提示词 ----------------
    def _tab_prompt(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        tip = QLabel("这是发给模型的总规则（系统提示词）。改坏了可以点「恢复默认」。")
        tip.setObjectName("PageSub")
        v.addWidget(tip)
        self.prompt_edit = QPlainTextEdit()
        v.addWidget(self.prompt_edit, 1)
        row = QHBoxLayout()
        reset = QPushButton("恢复默认")
        reset.clicked.connect(
            lambda: self.prompt_edit.setPlainText(config.DEFAULT_SYSTEM_PROMPT))
        row.addWidget(reset)
        row.addStretch(1)
        v.addLayout(row)
        return page

    # ---------------- 装载 / 收集 ----------------
    def load(self, settings):
        self._settings = dict(settings or {})
        s = self._settings
        i = self.theme_cb.findData(s.get("theme", "light"))
        self.theme_cb.setCurrentIndex(max(0, i))
        self.font_spin.setValue(int(s.get("font_scale", 100) or 100))
        self.agent_cb.setChecked(bool(s.get("agent_mode", True)))
        self.search_cb.setChecked(bool(s.get("enable_search", True)))
        j = self.policy_cb.findData(s.get("model_policy", "strict"))
        self.policy_cb.setCurrentIndex(max(0, j))

        k = self.asr_cb.findData(s.get("asr_prefer", "auto"))
        self.asr_cb.setCurrentIndex(max(0, k))
        m = self.mic_cb.findData(int(s.get("mic_device", -1) or -1))
        self.mic_cb.setCurrentIndex(max(0, m))
        self.silence_spin.setValue(float(s.get("voice_silence", 10.0) or 10.0))
        self.max_rec_spin.setValue(int(s.get("voice_max_seconds", 300) or 300))
        self.auto_send_cb.setChecked(bool(s.get("voice_auto_send")))
        self.tts_on.setChecked(bool(s.get("tts_enabled")))
        e = self.tts_eng.findData(s.get("tts_engine", "sapi"))
        self.tts_eng.setCurrentIndex(max(0, e))
        vv = self.tts_voice.findData(s.get("tts_voice", "") or "")
        self.tts_voice.setCurrentIndex(max(0, vv))
        self.tts_rate.setValue(int(s.get("tts_rate", 0) or 0))

        keys = s.get("api_keys") or {}
        for name, e in self.key_edits.items():
            e.setText(keys.get(name, "") or "")
        self.prompt_edit.setPlainText(s.get("system_prompt", "") or config.DEFAULT_SYSTEM_PROMPT)
        self.tray_enabled.setChecked(bool(s.get("tray_enabled", True)))
        self.tray_on_close.setChecked(bool(s.get("tray_on_close", True)))
        self.hotkey_show.setText(s.get("hotkey_show", "ctrl+alt+space") or "")
        self.hotkey_voice.setText(s.get("hotkey_voice", "ctrl+alt+v") or "")
        self.web_auto_cb.setChecked(bool(s.get("deepseek_web_auto", True)))
        self.web_silent_cb.setChecked(bool(s.get("deepseek_web_silent", True)))
        try:
            self.gh_token_edit.setText(str(s.get("github_token", "") or ""))
        except Exception:
            pass

    def _save(self):
        s = dict(self._settings)
        s["theme"] = self.theme_cb.currentData()
        s["font_scale"] = self.font_spin.value()
        s["agent_mode"] = self.agent_cb.isChecked()
        s["enable_search"] = self.search_cb.isChecked()
        s["model_policy"] = self.policy_cb.currentData()
        s["asr_prefer"] = self.asr_cb.currentData()
        s["mic_device"] = self.mic_cb.currentData()
        s["voice_silence"] = self.silence_spin.value()
        s["voice_max_seconds"] = self.max_rec_spin.value()
        s["voice_auto_send"] = self.auto_send_cb.isChecked()
        s["tts_enabled"] = self.tts_on.isChecked()
        s["tts_engine"] = self.tts_eng.currentData()
        s["tts_voice"] = self.tts_voice.currentData() or ""
        s["tts_rate"] = self.tts_rate.value()
        s["system_prompt"] = self.prompt_edit.toPlainText()
        s["tray_enabled"] = self.tray_enabled.isChecked()
        s["tray_on_close"] = self.tray_on_close.isChecked()
        s["hotkey_show"] = self.hotkey_show.text().strip()
        s["hotkey_voice"] = self.hotkey_voice.text().strip()
        s["deepseek_web_auto"] = self.web_auto_cb.isChecked()
        s["deepseek_web_silent"] = self.web_silent_cb.isChecked()
        s["github_token"] = self.gh_token_edit.text().strip()
        keys = dict(s.get("api_keys") or {})
        for name, e in self.key_edits.items():
            keys[name] = e.text().strip()
        s["api_keys"] = keys
        self._settings = s
        self.saved.emit(s)


class QDoubleSpinBoxCompat(QDoubleSpinBox):
    """浮点秒数输入框（录音静音的等待时间等）。"""

    def __init__(self, minimum=0.5, maximum=30.0, step=0.5,
                 value=10.0, parent=None):
        super().__init__(parent)
        self.setDecimals(1)
        self.setRange(float(minimum), float(maximum))
        self.setSingleStep(float(step))
        self.setSuffix(" 秒")
        self.setValue(float(value))

    def value(self):
        return float(super().value())

    def setValue(self, v):
        try:
            super().setValue(float(v))
        except (TypeError, ValueError):
            super().setValue(3.0)


# ===========================================================================
# 关于
# ===========================================================================
class AboutPage(QWidget):
    check_update = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(12)
        title = QLabel(f"{ver.APP_NAME}  v{ver.VERSION}")
        title.setStyleSheet(f"font-size:22px;font-weight:bold;color:{t['accent_text']};")
        v.addWidget(title)
        sub = QLabel(f"构建日期 {ver.BUILD_DATE}　·　本地 AI Agent 桌面应用　·　全免费")
        sub.setObjectName("PageSub")
        v.addWidget(sub)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setHtml(f"""
<h3>它是什么</h3>
<p>一个跑在你 Windows 电脑上的 AI Agent：能真正读写文件、生成 Word/Excel/PPT/PDF、
执行命令与 Python、联网搜索、看图 OCR、语音输入输出、推送到钉钉，还能跨会话记住你。</p>

<h3>免费能力来源</h3>
<ul>
<li><b>对话模型</b>：智谱 GLM-4-Flash / GLM-4.7-Flash、超算互联网、百炼 Qwen-Flash（均有免费额度）</li>
<li><b>语音识别</b>：智谱 GLM-ASR-2512（用现有 Key 即可）；可选硅基流动 SenseVoiceSmall（注册免费）</li>
<li><b>语音合成</b>：Windows 内置离线语音（零配置）+ 微软 Edge 在线语音（免费）</li>
<li><b>更新检查</b>：GitHub Releases（免费）</li>
</ul>

<h3>安全说明</h3>
<ul>
<li>API Key 不以明文存储：内置 Key 以密文编译进 exe；你自己填的 Key 用 Windows DPAPI 加密落盘。</li>
<li>客户端程序里的密钥理论上都能被逆向，这是所有桌面 App 的共同限制；这层防护的目的是
避免"顺手一看就看到"和"文件拷走就能用"。</li>
<li>删除文件只进回收站且必须确认；系统关键目录（Windows / Program Files）禁止写入。</li>
</ul>

<h3>链接</h3>
<p>项目主页：<a href="{ver.HOMEPAGE}">{ver.HOMEPAGE}</a><br>
发布页：<a href="{ver.RELEASES_PAGE}">{ver.RELEASES_PAGE}</a></p>
""")
        v.addWidget(body, 1)

        row = QHBoxLayout()
        b = QPushButton("检查更新")
        b.setFixedHeight(34)
        b.clicked.connect(self.check_update.emit)
        row.addWidget(b)
        copy = QPushButton("复制诊断信息")
        copy.setObjectName("cardBtn")
        copy.setFixedHeight(34)
        copy.clicked.connect(self._copy_diag)
        row.addWidget(copy)
        row.addStretch(1)
        v.addLayout(row)
        self.status = QLabel("")
        self.status.setObjectName("PageSub")
        v.addWidget(self.status)

    def _copy_diag(self):
        import platform
        import sys
        lines = [
            f"{ver.APP_NAME} v{ver.VERSION} (build {ver.BUILD_DATE})",
            f"Python {sys.version.split()[0]}",
            f"系统 {platform.platform()}",
            f"冻结运行: {getattr(sys, 'frozen', False)}",
            f"dpapi: {security.DPAPI_AVAILABLE}",
            f"edge-tts: {'有' if 'edge' in tts_mod.Speaker().available_engines else '无'}",
            f"语音引擎: {', '.join(asr_mod.available_backends(config.DEFAULT_API_KEYS)) or '无'}",
            f"钉钉 stream: {'有' if dt_mod.DingTalkClient().stream_available() else '无'}",
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status.setText("诊断信息已复制到剪贴板。")


# ===========================================================================
# 技能库（Skill）
# ===========================================================================
class SkillNewDialog(QDialog):
    """新建技能：填 slug + 名称 + 说明 + 正文（Markdown）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建技能")
        self.resize(720, 560)
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.slug = QLineEdit()
        self.slug.setPlaceholderText("英文短名，如 my-report（会作为目录名）")
        self.name = QLineEdit()
        self.name.setPlaceholderText("中文名，如 我的周报模板")
        self.when = QLineEdit()
        self.when.setPlaceholderText("什么时候用，如 用户要写周报时")
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("一句话说明")
        form.addRow("短名", self.slug)
        form.addRow("名称", self.name)
        form.addRow("适用场景", self.when)
        form.addRow("说明", self.desc)
        v.addLayout(form)
        v.addWidget(QLabel("执行步骤（Markdown，建议一行一步）："))
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText(
            "1. 先问清目标与范围\n2. 收集素材（真读文件，不要编）\n"
            "3. 按固定结构输出\n4. 生成文件并回读验证")
        v.addWidget(self.body, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("创建")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def payload(self):
        slug = self.slug.text().strip() or "skill"
        name = self.name.text().strip() or slug
        text = ("---\n"
                f"name: {name}\n"
                f"description: {self.desc.text().strip()}\n"
                f"when: {self.when.text().strip()}\n"
                "---\n\n"
                + (self.body.toPlainText().strip() or "1. 待补充"))
        return slug, text


class SkillsPage(QWidget):
    """技能库：看内置技能、建自己的技能。"""

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel("✦ 技能库")
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("PageSub")
        head.addWidget(self.hint)
        v.addLayout(head)

        tip = QLabel("技能 = 一套「遇到这类任务就照这个套路做」的专业指令。"
                     "AI 平时看不到技能正文（省上下文），只在任务匹配时自己取出来照着执行。"
                     "你想让它固定按某个套路做事，就在这儿写一个技能。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)

        split = QHBoxLayout()
        left = QVBoxLayout()
        self.listw = QListWidget()
        self.listw.setMinimumWidth(260)
        self.listw.currentRowChanged.connect(self._show_body)
        left.addWidget(self.listw, 1)
        split.addLayout(left, 0)

        right = QVBoxLayout()
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        right.addWidget(self.body, 1)
        split.addLayout(right, 1)
        v.addLayout(split, 1)

        row = QHBoxLayout()
        self.new_btn = QPushButton("＋ 新建技能")
        self.new_btn.clicked.connect(self._new)
        self.open_btn = QPushButton("打开技能目录")
        self.open_btn.clicked.connect(self._open_dir)
        self.del_btn = QPushButton("删除此技能")
        self.del_btn.clicked.connect(self._delete)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.reload)
        row.addWidget(self.new_btn)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        row.addWidget(self.del_btn)
        row.addWidget(self.refresh_btn)
        v.addLayout(row)

    # ---------- 数据 ----------
    def bind(self, store):
        self.store = store
        self.reload()

    def reload(self):
        self.listw.clear()
        if self.store is None:
            return
        items = self.store.list_all()
        for s in items:
            tag = "内置" if s["source"] == "builtin" else "自建"
            it = QListWidgetItem(f"{s['name']}\n{tag} · {s['slug']}")
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.listw.addItem(it)
        builtin = sum(1 for s in items if s["source"] == "builtin")
        self.hint.setText(f"共 {len(items)} 个（内置 {builtin}，自建 {len(items) - builtin}）")
        if items:
            self.listw.setCurrentRow(0)
        else:
            self.body.setHtml("<p style='color:#888'>还没有技能。</p>")

    def _show_body(self, row):
        if row < 0 or self.store is None:
            return
        it = self.listw.item(row)
        if it is None:
            return
        s = it.data(Qt.ItemDataRole.UserRole)
        body = self.store.body_of(s)
        where = "内置技能" if s["source"] == "builtin" else f"自建：{s.get('path', '')}"
        html = (f"<h2>{_esc(s['name'])}</h2>"
                f"<p style='color:#888'>{_esc(where)}</p>"
                f"<p><b>适用场景：</b>{_esc(s.get('when') or '—')}</p>"
                f"<pre style='white-space:pre-wrap'>{_esc(body)}</pre>")
        self.body.setHtml(html)
        self.del_btn.setEnabled(s["source"] != "builtin")

    # ---------- 操作 ----------
    def _new(self):
        if self.store is None:
            QMessageBox.warning(self, "提示", "还没有绑定工作区。")
            return
        dlg = SkillNewDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        slug, text = dlg.payload()
        try:
            self.store.install(slug, text)
        except Exception as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        self.reload()
        self.changed.emit()
        QMessageBox.information(self, "已创建",
                                f"技能「{slug}」已装进工作区。\n"
                                f"对 AI 说「用 {slug} 技能做…」它就会照着执行。")

    def _open_dir(self):
        if self.store is None or not self.store.dir:
            return
        os.makedirs(self.store.dir, exist_ok=True)
        try:
            os.startfile(self.store.dir)          # noqa: S606
        except Exception:
            QMessageBox.information(self, "技能目录", self.store.dir)

    def _delete(self):
        if self.store is None:
            return
        row = self.listw.currentRow()
        if row < 0:
            return
        s = self.listw.item(row).data(Qt.ItemDataRole.UserRole)
        if s["source"] == "builtin":
            QMessageBox.information(self, "提示", "内置技能不能删除。")
            return
        if QMessageBox.question(self, "确认", f"删除技能「{s['name']}」？") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            import shutil
            shutil.rmtree(os.path.dirname(s.get("path") or ""), ignore_errors=True)
        except Exception as e:
            QMessageBox.warning(self, "删除失败", str(e))
        self.reload()
        self.changed.emit()


# ===========================================================================
# 定时任务
# ===========================================================================
class TaskDialog(QDialog):
    """新建定时任务。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建定时任务")
        self.resize(680, 480)
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("如：每天早上发气象简报")
        form.addRow("任务名", self.name)
        self.kind = QComboBox()
        for k in sched_mod.KINDS:
            self.kind.addItem(sched_mod.KIND_LABELS.get(k, k), k)
        self.kind.setCurrentIndex(3)   # daily
        form.addRow("重复方式", self.kind)
        self.at = QLineEdit("08:00")
        self.at.setPlaceholderText("HH:MM，如 08:00")
        form.addRow("时间", self.at)
        self.weekdays = QLineEdit("1,2,3,4,5")
        self.weekdays.setPlaceholderText("1=周一…7=周日，可写 1,3,5 或 周一到周五")
        form.addRow("星期几", self.weekdays)
        self.interval = QSpinBox()
        self.interval.setRange(1, 1440)
        self.interval.setValue(60)
        self.interval.setSuffix(" 分钟")
        form.addRow("间隔", self.interval)
        self.run_at = QLineEdit("")
        self.run_at.setPlaceholderText("2026-09-26T08:00（仅一次时必填）")
        form.addRow("仅一次时间", self.run_at)
        self.notify = QCheckBox("执行完把结果推送到钉钉")
        form.addRow("", self.notify)
        v.addLayout(form)
        v.addWidget(QLabel("到期要做什么（自然语言，写清要交付什么）："))
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "例：查一下荣成今天的天气和空气质量，整理成一段简报，"
            "包含温度区间、穿衣建议、是否需要带伞。")
        v.addWidget(self.prompt, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("创建")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def payload(self):
        return {
            "name": self.name.text().strip() or "未命名任务",
            "prompt": self.prompt.toPlainText().strip(),
            "kind": self.kind.currentData(),
            "at": self.at.text().strip() or "09:00",
            "weekdays": self.weekdays.text().strip(),
            "interval_min": self.interval.value(),
            "run_at": self.run_at.text().strip(),
            "notify_dingtalk": self.notify.isChecked(),
        }


class TasksPage(QWidget):
    """定时任务：让 AI 在你不看着的时候也自己干活。"""

    run_now = pyqtSignal(str)      # 请求主窗口立即执行某任务
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sched = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel("◷ 定时任务")
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("PageSub")
        head.addWidget(self.hint)
        v.addLayout(head)

        tip = QLabel("到点后 AI 会真的把这件事做一遍（可以调工具、生成文件），"
                     "结果会记在下面「上次结果」里；勾了钉钉的还会推给你。"
                     "程序关掉就不跑了，开着才跑。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["任务名", "重复规则", "下次", "状态", "上次结果"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.new_btn = QPushButton("＋ 新建任务")
        self.new_btn.clicked.connect(self._new)
        self.run_btn = QPushButton("立即执行一次")
        self.run_btn.clicked.connect(self._run)
        self.toggle_btn = QPushButton("启用 / 停用")
        self.toggle_btn.clicked.connect(self._toggle)
        self.del_btn = QPushButton("删除")
        self.del_btn.clicked.connect(self._delete)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.reload)
        row.addWidget(self.new_btn)
        row.addWidget(self.run_btn)
        row.addWidget(self.toggle_btn)
        row.addStretch(1)
        row.addWidget(self.del_btn)
        row.addWidget(self.refresh_btn)
        v.addLayout(row)

    def bind(self, sched):
        self.sched = sched
        self.reload()

    def reload(self):
        self.table.setRowCount(0)
        if self.sched is None:
            return
        items = self.sched.list_all()
        self.table.setRowCount(len(items))
        for r, t in enumerate(items):
            cells = [
                t.get("name") or "未命名",
                sched_mod.describe(t),
                sched_mod.next_text(t),
                ("启用" if t.get("enabled") else "已停")
                + (f"　已跑 {t.get('run_count', 0)} 次" if t.get("run_count") else ""),
                (t.get("last_status") or "—") + "　"
                + (t.get("last_result") or "").replace("\n", " ")[:120],
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(str(text))
                if c == 0:
                    it.setData(Qt.ItemDataRole.UserRole, t.get("id"))
                self.table.setItem(r, c, it)
        enabled = sum(1 for t in items if t.get("enabled"))
        self.hint.setText(f"共 {len(items)} 个（启用 {enabled}）" if items else "还没有定时任务")

    def _current_id(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        it = self.table.item(r, 0)
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _new(self):
        if self.sched is None:
            return
        dlg = TaskDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        p = dlg.payload()
        if not p["prompt"]:
            QMessageBox.warning(self, "提示", "得写清楚到期要做什么。")
            return
        try:
            t = self.sched.add(**p)
        except Exception as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        self.reload()
        self.changed.emit()
        QMessageBox.information(
            self, "已创建",
            f"任务「{t['name']}」已创建。\n规则：{sched_mod.describe(t)}\n"
            f"下次执行：{sched_mod.next_text(t)}")

    def _run(self):
        tid = self._current_id()
        if not tid:
            QMessageBox.information(self, "提示", "先选中一个任务。")
            return
        self.run_now.emit(tid)

    def _toggle(self):
        tid = self._current_id()
        if not tid or self.sched is None:
            return
        t = self.sched.get(tid)
        if not t:
            return
        self.sched.update(tid, enabled=not t.get("enabled"))
        self.reload()
        self.changed.emit()

    def _delete(self):
        tid = self._current_id()
        if not tid or self.sched is None:
            return
        t = self.sched.get(tid)
        if not t:
            return
        if QMessageBox.question(self, "确认", f"删除定时任务「{t['name']}」？") \
                != QMessageBox.StandardButton.Yes:
            return
        self.sched.remove(tid)
        self.reload()
        self.changed.emit()



# ===========================================================================
# ★ v9.16.0 新增两页：并行任务 / 团队
#   用户原话：「模型在跑我本地命令的时候不影响它继续思考，也可以多条链路同时
#   执行」「增加一个像 WorkBuddy 这样的团队的功能，各个工程师、产品经理独立跑，
#   不影响主进程」。
#   这两页就是那两件事的**可见入口**：能看见有几条链路在跑、各自跑到哪了、
#   产物落在哪，也能一键取消 / 手动加任务 / 手动起一支团队。
# ===========================================================================
_STATUS_COLOR = {
    "queued": "#c08a00", "running": "#1a7f37", "done": "#1a7f37",
    "failed": "#c0392b", "cancelled": "#888",
}


class BgTasksPage(QWidget):
    """⟳ 并行任务：所有后台链路一览（跑慢命令、后台 AI、团队子任务都在这里）。"""

    cancel_one = pyqtSignal(str)      # 请求取消某个任务
    cancel_all = pyqtSignal()
    clear_done = pyqtSignal()
    refresh = pyqtSignal()
    allow_cmd = pyqtSignal(bool)      # "允许后台任务执行命令"开关

    def __init__(self, parent=None):
        super().__init__(parent)
        self.man = None
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel("⟳ 并行任务")
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("PageSub")
        head.addWidget(self.hint)
        v.addLayout(head)

        tip = QLabel(
            "这里能看到**所有在后台跑的链路**：跑的长命令、后台 AI 自主任务、"
            "团队里各个角色，全都在这里。它们在独立线程里跑，**不影响主对话** —— "
            "你可以一边让它打包，一边继续问别的问题。点一行看实时日志和产物。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["任务", "类型", "状态", "用时", "产物"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._show)
        v.addWidget(self.table, 2)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("选中上面任意一个任务，这里会显示它的实时日志与结果。")
        v.addWidget(self.detail, 3)

        row = QHBoxLayout()
        self.cancel_btn = QPushButton("停止选中任务")
        self.cancel_btn.clicked.connect(self._cancel)
        self.all_btn = QPushButton("停止全部")
        self.all_btn.clicked.connect(self.cancel_all.emit)
        self.clear_btn = QPushButton("清理已结束")
        self.clear_btn.clicked.connect(self.clear_done.emit)
        self.re_btn = QPushButton("刷新")
        self.re_btn.clicked.connect(self.refresh.emit)
        for b in (self.cancel_btn, self.all_btn, self.clear_btn, self.re_btn):
            row.addWidget(b)
        # 后台没有界面弹"确认执行这条命令"的框，所以**默认拒绝**命令类操作。
        # 用户明确勾了这个开关才放行（危险命令在工具层仍会被单独拦截）。
        self.allow_cb = QCheckBox("允许后台任务执行本地命令")
        self.allow_cb.setToolTip(
            "不勾：后台链路里的 run_command / 删除文件会被直接拒绝（更安全，默认）。\n"
            "勾上：允许它们执行（危险命令如删除/格式化仍会被拦截）。")
        self.allow_cb.toggled.connect(self.allow_cmd.emit)
        row.addWidget(self.allow_cb)
        row.addStretch(1)
        self.path_lbl = QLabel("")
        self.path_lbl.setObjectName("PageSub")
        row.addWidget(self.path_lbl)
        v.addLayout(row)

    def bind(self, man):
        self.man = man
        if man is not None:
            self.path_lbl.setText("记录目录：" + getattr(man, "dir", ""))
        self.reload()

    def set_allow_cmd(self, flag):
        """由主窗口把设置里的值同步到复选框（别触发一轮信号回环）。"""
        self.allow_cb.blockSignals(True)
        self.allow_cb.setChecked(bool(flag))
        self.allow_cb.blockSignals(False)

    def _current_id(self):
        it = self.table.currentItem()
        if it is None:
            return ""
        row = it.row()
        cell = self.table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else ""

    def _cancel(self):
        tid = self._current_id()
        if tid:
            self.cancel_one.emit(tid)

    def reload(self):
        if self.man is None:
            return
        keep = self._current_id()
        tasks = self.man.list(limit=80)
        self.table.setRowCount(len(tasks))
        for r, t in enumerate(tasks):
            s = t.snapshot(log_tail=0)
            it = QTableWidgetItem(("" if s["status"] not in ("queued", "running")
                                   else "▶ ") + (s["title"] or s["id"]))
            it.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(s["kind"]))
            st = QTableWidgetItem(s["status_text"])
            from PyQt6.QtGui import QColor
            st.setForeground(QColor(_STATUS_COLOR.get(s["status"], "#333")))
            self.table.setItem(r, 2, st)
            self.table.setItem(r, 3, QTableWidgetItem(f"{s['elapsed']}s"))
            self.table.setItem(r, 4, QTableWidgetItem(str(len(s["files"]))))
            if s["id"] == keep:
                self.table.selectRow(r)
        active = sum(1 for t in tasks if t.status in ("queued", "running"))
        self.hint.setText(f"共 {len(tasks)} 个任务，其中 {active} 个在跑/排队"
                          if tasks else "还没有后台任务")
        self._show()

    def _show(self):
        tid = self._current_id()
        if self.man is None or not tid:
            return
        t = self.man.get(tid)
        if t is None:
            return
        s = t.snapshot(log_tail=200)
        lines = [f"{s['title']}   [{s['status_text']}]",
                 f"任务号：{s['id']}    类型：{s['kind']}    用时：{s['elapsed']}s"]
        if s["detail"]:
            lines += ["", "指令/目标：", "  " + str(s["detail"])[:1500]]
        if s["progress"]:
            lines += ["", "当前：" + s["progress"]]
        if s["files"]:
            lines += ["", "产物："] + [f"  · {f['path']}（{f['size']} 字节）"
                                       for f in s["files"]]
        if s["error"]:
            lines += ["", "错误：" + s["error"]]
        if s["log"]:
            lines += ["", "日志："] + ["  " + ln for ln in s["log"]]
        if s["result"]:
            lines += ["", "结果：", s["result"][:6000]]
        self.detail.setPlainText("\n".join(lines))


class TeamPage(QWidget):
    """⬡ 团队：一键起一支"产品经理 + 架构师 + 工程师 + 测试 + 组长"的小队。"""

    start_run = pyqtSignal(str, list, int)     # goal, roles, max_steps
    cancel_run = pyqtSignal(str)
    refresh = pyqtSignal()

    ROLE_CHOICES = [
        ("pm", "▥ 产品经理"), ("arch", "⬢ 架构师"), ("dev", "⌨ 工程师"),
        ("qa", "⚗ 测试工程师"), ("lead", "⌖ 组长"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.man = None
        self._cur = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        head = QHBoxLayout()
        self.title = QLabel("⬡ 团队协作")
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.hint = QLabel("")
        self.hint.setObjectName("PageSub")
        head.addWidget(self.hint)
        v.addLayout(head)

        tip = QLabel(
            "把一整件事交给一支小队：**产品经理**定需求与验收标准 → **架构师**出方案、"
            "把活拆成互不重叠的几份 → **多个工程师并行实现**（各写各的目录，互不覆盖）→ "
            "**测试工程师**独立验证 → **组长**汇总交付。全程在后台跑，你可以继续用主对话。")
        tip.setWordWrap(True)
        tip.setObjectName("PageSub")
        v.addWidget(tip)

        form = QHBoxLayout()
        self.goal = QLineEdit()
        self.goal.setPlaceholderText(
            "要做的事（说清目标即可，拆活交给团队）…  例：做一个成绩统计小工具，"
            "能读 Excel 并输出排名表")
        form.addWidget(self.goal, 1)
        self.steps = QSpinBox()
        self.steps.setRange(2, 12)
        self.steps.setValue(6)
        self.steps.setPrefix("每人最多 ")
        self.steps.setSuffix(" 步")
        form.addWidget(self.steps)
        self.n_dev = QSpinBox()
        self.n_dev.setRange(1, 4)
        self.n_dev.setValue(2)
        self.n_dev.setPrefix("工程师 ")
        self.n_dev.setSuffix(" 人")
        form.addWidget(self.n_dev)
        self.go_btn = QPushButton("➤ 组建团队开工")
        self.go_btn.clicked.connect(self._start)
        form.addWidget(self.go_btn)
        v.addLayout(form)

        body = QHBoxLayout()
        self.runs = QListWidget()
        self.runs.setMaximumWidth(260)
        self.runs.currentRowChanged.connect(self._pick)
        body.addWidget(self.runs)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        body.addWidget(self.view, 1)
        v.addLayout(body, 1)

        row = QHBoxLayout()
        self.stop_btn = QPushButton("停止这支队")
        self.stop_btn.clicked.connect(self._stop)
        self.re_btn = QPushButton("刷新")
        self.re_btn.clicked.connect(self.refresh.emit)
        row.addWidget(self.stop_btn)
        row.addWidget(self.re_btn)
        row.addStretch(1)
        v.addLayout(row)

    def bind(self, man):
        self.man = man
        self.reload()

    def _start(self):
        goal = self.goal.text().strip()
        if not goal:
            QMessageBox.information(self, "还差一步", "先写清楚要做的事，再组建团队。")
            return
        roles = ["pm", "arch", f"dev:{self.n_dev.value()}", "qa", "lead"]
        self.start_run.emit(goal, roles, self.steps.value())
        self.goal.clear()

    def _stop(self):
        if self._cur:
            self.cancel_run.emit(self._cur)

    def reload(self):
        if self.man is None:
            return
        cur = self._cur
        runs = self.man.list(limit=40)
        self.runs.clear()
        for r in runs:
            s = r.snapshot()
            icon = {"running": "▶", "done": "✓", "failed": "✕",
                    "cancelled": "⊘"}.get(s["status"], "·")
            it = QListWidgetItem(f"{icon} {s['goal'][:22]}\n   {s['id']}")
            it.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.runs.addItem(it)
            if s["id"] == cur:
                self.runs.setCurrentRow(self.runs.count() - 1)
        active = sum(1 for r in runs if r.status in ("queued", "running"))
        self.hint.setText(f"共 {len(runs)} 支团队，{active} 支在跑" if runs else "还没跑过团队")
        self._pick(self.runs.currentRow())

    def _pick(self, row):
        if self.man is None or row < 0:
            return
        it = self.runs.item(row)
        if it is None:
            return
        rid = it.data(Qt.ItemDataRole.UserRole)
        self._cur = rid
        run = self.man.get(rid)
        if run is None:
            return
        s = run.snapshot()
        cards = []
        for c in s["cards"]:
            color = _STATUS_COLOR.get(c["status"], "#333")
            files = ""
            if c.get("files"):
                files = ("<br><span style='color:#888'>产物："
                         + "、".join(_esc(f["path"]) for f in c["files"][:8]) + "</span>")
            note = (f"<span style='color:#888'> · {_esc(c['note'])}</span>"
                    if c.get("note") else "")
            body = _esc(str(c.get("text") or "")[:2500]).replace("\n", "<br>")
            cards.append(
                f"<div style='margin:6px 0;padding:8px 10px;border-left:3px solid {color}'>"
                f"<b>{_esc(c['title'])}</b> "
                f"<span style='color:{color}'>[{c['status']}]</span>{note}"
                f"{files}<br><span style='color:#555'>{body}</span></div>")
        head = (f"<h2>{_esc(s['goal'][:80])}</h2>"
                f"<p style='color:#888'>{s['id']} · {s['status']} · 用时 {s['elapsed']}s"
                f"<br>工作目录：{_esc(s['dir'])}</p>")
        rep = ""
        if s["report"]:
            rep = ("<h3>组长汇总</h3><pre style='white-space:pre-wrap'>"
                   + _esc(s["report"][:8000]) + "</pre>")
        self.view.setHtml(head + "".join(cards) + rep)


# ---------------------------------------------------------------------------
# 内嵌浏览器页（v9.18.0 需求 2）
# ---------------------------------------------------------------------------
class BrowserPage(QWidget):
    """内嵌浏览器：像浏览器一样访问网页、自由抓包，再一键交给 AI。

    用户原话：
        「增强网址解析的功能，不仅仅让模型只能通过工具查看网页返回的 json，
          模型还可以想浏览器一样访问网页、自由抓取包，参考 workbuddy，
          可以内嵌浏览器内核」

    真正的实现在 :class:`app.gui.browser_page.BrowserPane`（地址栏 + Chromium 内核 +
    请求嗅探），这里只负责"把它装成一个页面"并写清楚怎么用。
    模型侧对应的是 `web_visit` 工具 —— 走的是同一个
    :func:`app.webengine.fetch_rendered`，所以人工看到的和模型拿到的是一致的。

    **懒加载**（v9.18.0）：Chromium 内核第一次实例化要几百毫秒 + 一百多 MB 内存，
    绝大部分用户从头到尾都不会打开这一页 —— 所以真正的 `BrowserPane` 等到
    **这一页第一次被显示**时才建（`showEvent`）。启动速度和内存占用不受影响。

    ⚠ 本模块顶部就 import 了 `browser_page`：QtWebEngine 必须在 `QApplication`
      之前导入，晚了不崩但会**自动关掉抓包**（见 webengine.py 的踩坑记录）。
      注意"导入内核"与"创建控件"是两件事，这里只把前者提前。
    """

    fetched = pyqtSignal(dict)      # 转发 BrowserPane.fetched（主窗口直接连这个）

    def __init__(self, parent=None):
        super().__init__(parent)
        t = themes.tokens()

        self.pane = None
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        head = QLabel(f"{icons_mod.ic('web')} 内嵌浏览器")
        head.setStyleSheet(f"font-size:15px;font-weight:bold;color:{t['accent_text']};")
        v.addWidget(head)

        # 占位（第一次显示时被真的 BrowserPane 顶掉）
        self._placeholder = QLabel("浏览器内核尚未启动，切换到本页时会自动就绪。")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        self._placeholder.setStyleSheet(f"color:{t['text_muted']};font-size:12px;")
        v.addWidget(self._placeholder, 1)

        self.tip = QLabel(
            "· 这里是**真实的浏览器内核**（Chromium）：JS 跑完的页面才拿得到正文，"
            "各种后台 / 单页应用在这儿能正常显示，纯抓 HTML 只会得到一具空壳。\n"
            "· 点「交给 AI 分析当前页」会把渲染后的正文 + 页面发出的数据接口整理好"
            "填进输入框，你看一眼按回车就交给 AI。\n"
            "· AI 自己也能调 web_visit 工具做同样的事（可选等多久、要不要抓包）。")
        self.tip.setWordWrap(True)
        self.tip.setStyleSheet(f"color:{t['text_muted']};font-size:12px;")
        v.addWidget(self.tip)

    # ------------------------------------------------------------------
    def ensure_pane(self):
        """建（并返回）真正的浏览器控件；已经建过就直接返回。

        用 `insertWidget(1, ...)` 插到标题下面 —— 这样"先 addWidget 后显示"的
        铁律天然满足（绝不会出现"控件还没父窗口就被 setVisible(True)"那种弹小窗）。
        """
        if self.pane is not None:
            return self.pane
        try:
            self.pane = browser_page.BrowserPane()
            self.pane.fetched.connect(self.fetched.emit)
        except Exception as e:                       # noqa: BLE001
            self._placeholder.setText(f"浏览器内核启动失败：{type(e).__name__}: {e}")
            return None
        lay = self.layout()
        lay.insertWidget(1, self.pane, 1)
        lay.removeWidget(self._placeholder)
        self._placeholder.hide()
        self._placeholder.deleteLater()
        self._placeholder = None
        return self.pane

    def showEvent(self, ev):                          # noqa: N802 （Qt 回调命名）
        super().showEvent(ev)
        self.ensure_pane()


# ============================================================================
# ★ v9.20.0：IM 助手「消息管理」对话框
#   会话上下文 / 定时任务 / 日志本 / 记忆 —— 全部可查看、可改、可删。
#   做成对话框而不是塞进主页，是为了不挤压「新消息 / AI处理结果」这两栏。
# ============================================================================
class IMDialog(QDialog):
    """IM 助手的管理面板：审查上下文、管定时任务、看日志、编辑记忆。"""

    thread_saved = pyqtSignal(dict)        # {id, mode, persona, rules, ctx_limit, memory_on}
    task_added = pyqtSignal(dict)
    task_deleted = pyqtSignal(str)
    task_toggled = pyqtSignal(str, bool)
    memory_saved = pyqtSignal(str)         # 共享记忆全文
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("▥ 钉钉助手 · 消息管理")
        self.resize(980, 660)
        self._threads = {}
        self._tasks = []
        self._review_fn = None

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        head = QHBoxLayout()
        lb = QLabel("消息、上下文、定时任务、日志都在这里——改完点保存即生效。")
        lb.setObjectName("PageSub")
        head.addWidget(lb, 1)
        b_ref = QPushButton("⟳ 刷新")
        b_ref.clicked.connect(self.refresh_requested.emit)
        head.addWidget(b_ref)
        v.addLayout(head)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)
        self._build_threads_tab()
        self._build_tasks_tab()
        self._build_log_tab()
        self._build_memory_tab()

        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)

    # ---------------- 会话 / 上下文 ----------------
    def _build_threads_tab(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        self.list_threads = QListWidget()
        self.list_threads.setMinimumWidth(240)
        self.list_threads.currentItemChanged.connect(self._on_thread_pick)
        h.addWidget(self.list_threads, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        form = QFormLayout()
        self.lb_thread = QLabel("（左边选一个会话）")
        form.addRow("会话：", self.lb_thread)
        self.cb_mode = QComboBox()
        self.cb_mode.addItem("秘书模式（群里不闲聊，只接指令/@）", "secretary")
        self.cb_mode.addItem("同事模式（被叫到才接话）", "colleague")
        self.cb_mode.addItem("群助手模式（按指令/关键词回）", "group_bot")
        form.addRow("模式：", self.cb_mode)
        self.sp_ctx = QSpinBox()
        self.sp_ctx.setRange(2, 200)
        self.sp_ctx.setValue(20)
        form.addRow("带多少条上下文：", self.sp_ctx)
        self.ck_mem = QCheckBox("回复时带上这个会话自己的记忆")
        self.ck_mem.setChecked(True)
        form.addRow(self.ck_mem)
        self.ed_persona = QLineEdit()
        self.ed_persona.setPlaceholderText("比如：这是家长群，说话客气点；别用表情")
        form.addRow("人设补充：", self.ed_persona)
        self.ed_rules = QLineEdit()
        self.ed_rules.setPlaceholderText("比如：只回答学习相关；不要发广告")
        form.addRow("额外规则：", self.ed_rules)
        rv.addLayout(form)

        btns = QHBoxLayout()
        b_save = QPushButton("保存这个会话")
        b_save.clicked.connect(self._save_thread)
        btns.addWidget(b_save)
        btns.addStretch(1)
        rv.addLayout(btns)

        rv.addWidget(QLabel("上下文审查（模型实际看到的东西）："))
        self.tx_review = QPlainTextEdit()
        self.tx_review.setReadOnly(True)
        rv.addWidget(self.tx_review, 1)
        h.addWidget(right, 2)
        self.tabs.addTab(w, "会话 / 上下文")

    def _on_thread_pick(self, cur, _prev=None):
        if cur is None:
            return
        tid = cur.data(Qt.ItemDataRole.UserRole)
        t = self._threads.get(tid) or {}
        self.lb_thread.setText(f"{t.get('name') or tid}（{tid}）")
        want = t.get("mode") or "group_bot"
        for i in range(self.cb_mode.count()):
            if self.cb_mode.itemData(i) == want:
                self.cb_mode.setCurrentIndex(i)
        self.sp_ctx.setValue(int(t.get("ctx_limit") or 20))
        self.ck_mem.setChecked(bool(t.get("memory_on", True)))
        self.ed_persona.setText(str(t.get("persona") or ""))
        self.ed_rules.setText(str(t.get("rules") or ""))
        if self._review_fn:
            try:
                self.tx_review.setPlainText(str(self._review_fn(tid) or ""))
            except Exception as e:
                self.tx_review.setPlainText(f"读取失败：{e}")
        else:
            self.tx_review.setPlainText("（暂无数据）")

    def _save_thread(self):
        cur = self.list_threads.currentItem()
        if cur is None:
            return
        tid = cur.data(Qt.ItemDataRole.UserRole)
        self.thread_saved.emit({
            "id": tid,
            "mode": self.cb_mode.currentData(),
            "ctx_limit": self.sp_ctx.value(),
            "memory_on": self.ck_mem.isChecked(),
            "persona": self.ed_persona.text().strip(),
            "rules": self.ed_rules.text().strip(),
        })

    # ---------------- 定时任务 ----------------
    def _build_tasks_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self.list_tasks = QListWidget()
        v.addWidget(self.list_tasks, 1)

        form = QFormLayout()
        self.ed_t_title = QLineEdit()
        self.ed_t_title.setPlaceholderText("比如：每周值日提醒")
        form.addRow("名称：", self.ed_t_title)
        self.ed_t_text = QLineEdit()
        self.ed_t_text.setPlaceholderText("到点要发出去的那句话")
        form.addRow("内容：", self.ed_t_text)
        self.cb_t_thread = QComboBox()
        form.addRow("发到：", self.cb_t_thread)
        self.cb_t_freq = QComboBox()
        self.cb_t_freq.addItem("每天", "daily")
        self.cb_t_freq.addItem("每周", "weekly")
        self.cb_t_freq.addItem("每月", "monthly")
        self.cb_t_freq.currentIndexChanged.connect(self._sync_freq_row)
        form.addRow("频率：", self.cb_t_freq)
        self.cb_t_wd = QComboBox()
        for i, n in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            self.cb_t_wd.addItem(f"周{n}", i)
        form.addRow("星期几：", self.cb_t_wd)
        self.sp_t_day = QSpinBox()
        self.sp_t_day.setRange(1, 31)
        form.addRow("每月几号：", self.sp_t_day)
        self.ed_t_at = QLineEdit("08:30")
        self.ed_t_at.setPlaceholderText("HH:MM，比如 08:30")
        form.addRow("时间：", self.ed_t_at)
        v.addLayout(form)

        row = QHBoxLayout()
        b_add = QPushButton("＋ 添加定时消息")
        b_add.clicked.connect(self._add_task)
        b_del = QPushButton("⊘ 删除选中")
        b_del.clicked.connect(self._del_task)
        b_tog = QPushButton("⏸ 启用/停用")
        b_tog.clicked.connect(self._toggle_task)
        for b in (b_add, b_del, b_tog):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self._sync_freq_row()
        self.tabs.addTab(w, "定时消息")

    def _sync_freq_row(self, *_):
        freq = self.cb_t_freq.currentData()
        self.cb_t_wd.setEnabled(freq == "weekly")
        self.sp_t_day.setEnabled(freq == "monthly")

    def _add_task(self):
        tid = self.cb_t_thread.currentData()
        if not tid:
            QMessageBox.information(self, "提示", "请先选一个要发到哪个会话")
            return
        text = self.ed_t_text.text().strip()
        if not text:
            QMessageBox.information(self, "提示", "消息内容不能为空")
            return
        self.task_added.emit({
            "title": self.ed_t_title.text().strip() or text[:12],
            "text": text,
            "thread": tid,
            "freq": self.cb_t_freq.currentData(),
            "at": self.ed_t_at.text().strip() or "08:30",
            "weekday": self.cb_t_wd.currentData(),
            "day": self.sp_t_day.value(),
            "enabled": True,
        })
        self.ed_t_title.clear()
        self.ed_t_text.clear()

    def _task_id(self):
        cur = self.list_tasks.currentItem()
        return cur.data(Qt.ItemDataRole.UserRole) if cur else ""

    def _del_task(self):
        tid = self._task_id()
        if tid:
            self.task_deleted.emit(str(tid))

    def _toggle_task(self):
        tid = self._task_id()
        if not tid:
            return
        cur = self.list_tasks.currentItem()
        on = not bool(cur.data(Qt.ItemDataRole.UserRole + 1))
        self.task_toggled.emit(str(tid), on)

    # ---------------- 日志本 ----------------
    def _build_log_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("按日期查（YYYY-MM-DD，留空=全部）："))
        self.ed_log_day = QLineEdit()
        row.addWidget(self.ed_log_day, 1)
        row.addWidget(QLabel("按人查："))
        self.ed_log_who = QLineEdit()
        row.addWidget(self.ed_log_who, 1)
        b = QPushButton("⊙ 查询")
        b.clicked.connect(self.refresh_requested.emit)
        row.addWidget(b)
        v.addLayout(row)
        self.tx_log = QPlainTextEdit()
        self.tx_log.setReadOnly(True)
        v.addWidget(self.tx_log, 1)
        self.tabs.addTab(w, "日志本")

    # ---------------- 记忆 ----------------
    def _build_memory_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        lb = QLabel("这份记忆**所有会话共用**（身份背景、最近发生的事、称呼习惯…）。"
                    "越具体，AI 回得越像你本人。")
        lb.setWordWrap(True)
        lb.setObjectName("PageSub")
        v.addWidget(lb)
        self.tx_mem = QPlainTextEdit()
        self.tx_mem.setPlaceholderText(
            "例：\n我叫刘浩辰，初三，班长。\n别人一般叫我「浩辰」。\n"
            "我不喜欢别人叫我全名。\n最近在准备信息学奥赛，周三下午有课。")
        v.addWidget(self.tx_mem, 1)
        row = QHBoxLayout()
        b = QPushButton("⇩ 保存共享记忆")
        b.clicked.connect(lambda: self.memory_saved.emit(self.tx_mem.toPlainText()))
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.tabs.addTab(w, "记忆")

    # ---------------- 数据填充 ----------------
    def set_data(self, threads=None, tasks=None, logs=None, memory=None,
                 review_fn=None, log_text=None):
        self._threads = dict(threads or {})
        self._review_fn = review_fn
        if threads is not None:
            prev = None
            cur = self.list_threads.currentItem()
            if cur is not None:
                prev = cur.data(Qt.ItemDataRole.UserRole)
            self.list_threads.clear()
            for tid, t in sorted(self._threads.items(),
                                 key=lambda kv: -(float(kv[1].get("last_ts") or 0))):
                n = int(t.get("msg_count") or 0)
                it = QListWidgetItem(
                    f"{t.get('name') or tid}　·{t.get('mode') or 'group_bot'}"
                    f"　·{n} 条")
                it.setData(Qt.ItemDataRole.UserRole, tid)
                self.list_threads.addItem(it)
                if tid == prev:
                    self.list_threads.setCurrentItem(it)

        if tasks is not None:
            self._tasks = list(tasks)
            self.list_tasks.clear()
            for t in self._tasks:
                on = "✓" if t.get("enabled", True) else "⏸"
                it = QListWidgetItem(
                    f"{on} {t.get('title')}　·{_im_desc(t)}　→ "
                    f"{(self._threads.get(t.get('thread')) or {}).get('name') or t.get('thread')}")
                it.setData(Qt.ItemDataRole.UserRole, t.get("id"))
                it.setData(Qt.ItemDataRole.UserRole + 1, bool(t.get("enabled", True)))
                self.list_tasks.addItem(it)
            # 同步"发到"下拉
            keep = self.cb_t_thread.currentData()
            self.cb_t_thread.clear()
            for tid, t in self._threads.items():
                self.cb_t_thread.addItem(t.get("name") or tid, tid)
            if keep:
                for i in range(self.cb_t_thread.count()):
                    if self.cb_t_thread.itemData(i) == keep:
                        self.cb_t_thread.setCurrentIndex(i)

        if memory is not None and not self.tx_mem.hasFocus():
            self.tx_mem.setPlainText(str(memory))
        if log_text is not None:
            self.tx_log.setPlainText(str(log_text))


def _im_desc(t):
    """定时任务的频率描述（页面内小工具，避免 import 循环）。"""
    freq = str(t.get("freq") or "daily")
    at = t.get("at") or "08:30"
    if freq == "weekly":
        wd = ["一", "二", "三", "四", "五", "六", "日"]
        try:
            i = int(t.get("weekday") or 0)
        except Exception:
            i = 0
        return f"每周{wd[max(0, min(6, i))]} {at}"
    if freq == "monthly":
        return f"每月 {t.get('day') or 1} 号 {at}"
    return f"每天 {at}"
