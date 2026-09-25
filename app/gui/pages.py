# -*- coding: utf-8 -*-
"""v9 各功能页：侧边导航、欢迎页、记忆、工具记录、集成（钉钉/MCP/更新）、设置、关于。"""
import os
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QPlainTextEdit,
    QTextBrowser, QMessageBox, QListWidget, QListWidgetItem, QComboBox, QCheckBox,
    QLineEdit, QDialog, QDialogButtonBox, QTabWidget, QGroupBox, QFormLayout,
    QSpinBox, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication

from .. import (themes, plan as plan_mod, security, asr as asr_mod,
                tts as tts_mod, dingtalk as dt_mod, config, version as ver,
                skills as skills_mod, scheduler as sched_mod,
                memory as memory_mod, worklog as worklog_mod)

PAGES = [
    ("chat", "💬", "对话"),
    ("skills", "🧩", "技能"),
    ("tasks", "⏰", "定时"),
    ("memory", "🧠", "记忆"),
    ("tools", "📊", "工具"),
    ("integrations", "🔌", "集成"),
    ("settings", "⚙", "设置"),
    ("about", "ℹ", "关于"),
]


class NavRail(QWidget):
    """左侧竖直导航栏。"""

    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(76)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 14, 8, 14)
        v.setSpacing(6)

        logo = QLabel("AI\n工作台")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            f"color:{themes.tokens()['accent_text']};font-size:12px;font-weight:bold;"
            "line-height:1.3;")
        v.addWidget(logo)
        v.addSpacing(10)

        self.buttons = {}
        self._current = "chat"
        for key, icon, name in PAGES:
            b = QPushButton(f"{icon}\n{name}")
            b.setObjectName("NavBtn")
            b.setCheckable(True)
            b.setFixedHeight(58)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.select(k))
            v.addWidget(b)
            self.buttons[key] = b
        v.addStretch(1)
        self.buttons["chat"].setChecked(True)

    def select(self, key):
        if key == self._current:
            return
        self._current = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        self.changed.emit(key)

    def current(self):
        return self._current


class WelcomeView(QWidget):
    """空对话时的欢迎页：快捷入口卡片。"""

    quick = pyqtSignal(str)

    ACTIONS = [
        ("📄 生成 Word 文档", "写一份《…》的 Word，保存到桌面"),
        ("📊 做一张数据表", "把下面的数据做成 Excel 表格，每列带表头"),
        ("🖼 看图/OCR", "识别我拖进来的图片里的文字"),
        ("🔎 联网查资料", "上网查一下 … 的最新情况，附来源"),
        ("🎙 语音输入", "点输入框左边的麦克风说话，自动转成文字"),
        ("📈 画图表", "用这几组数据画一张柱状图，存成 PNG"),
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
        self.title = QLabel("🧠 长期记忆 · 工作总结")
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
                f"{os.path.join(self.ws, 'worklog')}")

    def _open_worklog(self):
        if not self.ws:
            return
        try:
            d = os.path.join(self.ws, "worklog")
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
        title = QLabel("📊 工具调用记录")
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
            mark = "✅" if ok else "❌"
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
    "baidu": "百度千帆 Key，形如 bce-v3/ALTAK-…",
    "dashscope": "阿里百炼 Key，形如 sk-…（用于 Qwen 系列）",
    "siliconflow": "硅基流动 Key，形如 sk-…（注册免费，可用于语音识别）",
    "deepseek": "DeepSeek 官方 Key（需充值）",
}


def _key_hint(provider_name):
    return _KEY_HINTS.get(provider_name, "API Key")


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = dict(dt_mod.DEFAULT_CONFIG)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(10)
        title = QLabel("🔌 集成")
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
            "⚠️ 接收人必须填 userId，填昵称（如「张三」）会报 staffId 不存在。")
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
        self.dt_selftest = QPushButton("🔍 一键接口自检")
        self.dt_selftest.setToolTip("逐项真实调用：凭据 / 通讯录 / 机器人 / 工作通知 / 群，把结果摊开")
        self.dt_selftest.clicked.connect(
            lambda: self.dingtalk_selftest.emit(self._collect_dingtalk()))
        self.dt_contacts = QPushButton("📇 浏览通讯录")
        self.dt_contacts.setToolTip("列出部门与成员（真实姓名 + userId），可直接填成接收人")
        self.dt_contacts.clicked.connect(
            lambda: self.dingtalk_contacts.emit(self._collect_dingtalk()))
        self.dt_login = QPushButton("🌐 扫码授权登录")
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
        self.dt_dws_login = QPushButton("🌐 用 dws 登录钉钉")
        self.dt_dws_login.setToolTip("打开浏览器用你的钉钉账号 OAuth 授权；授权后可读写你的会话/消息")
        self.dt_dws_login.clicked.connect(lambda: self.dingtalk_dws_login.emit(False))
        self.dt_dws_login_device = QPushButton("🔑 设备码登录")
        self.dt_dws_login_device.setToolTip("无法开浏览器时用：命令行输出授权链接+码，你去浏览器输入")
        self.dt_dws_login_device.clicked.connect(lambda: self.dingtalk_dws_login.emit(True))
        self.dt_dws_convs = QPushButton("📋 查看我的会话列表")
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
            mark = "✅" if ok else "❌"
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
        row.addWidget(save)
        row.addWidget(check)
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
                    f'🎉 发现新版本 {_esc(info.get("tag"))}（当前 {_esc(info.get("current"))}）</div>')
        else:
            head = (f'<div style="font-size:15px;font-weight:bold;color:{t["accent_text"]};">'
                    f'✅ 已是最新版本（{_esc(info.get("current"))}）</div>')
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
        self.probe_view = QTextBrowser()
        v.addWidget(self.probe_view, 1)
        return page

    def show_probe(self, results):
        t = themes.tokens()
        html = ['<table style="font-size:13px;border-collapse:collapse;">']
        for label, ok, msg in results:
            if ok is None:
                color, mark = t["text_muted"], "—"
            elif ok:
                color, mark = t["success"], "✅"
            else:
                color, mark = t["danger"], "❌"
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
        self.silence_spin = QDoubleSpinBoxCompat(0.5, 30.0, 0.5, 3.0)
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
        self.search_cb.setChecked(bool(s.get("enable_search", False)))
        j = self.policy_cb.findData(s.get("model_policy", "strict"))
        self.policy_cb.setCurrentIndex(max(0, j))

        k = self.asr_cb.findData(s.get("asr_prefer", "auto"))
        self.asr_cb.setCurrentIndex(max(0, k))
        m = self.mic_cb.findData(int(s.get("mic_device", -1) or -1))
        self.mic_cb.setCurrentIndex(max(0, m))
        self.silence_spin.setValue(float(s.get("voice_silence", 3.0) or 3.0))
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
        keys = dict(s.get("api_keys") or {})
        for name, e in self.key_edits.items():
            keys[name] = e.text().strip()
        s["api_keys"] = keys
        self._settings = s
        self.saved.emit(s)


class QDoubleSpinBoxCompat(QDoubleSpinBox):
    """浮点秒数输入框（录音静音的等待时间等）。"""

    def __init__(self, minimum=0.5, maximum=30.0, step=0.5,
                 value=3.0, parent=None):
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
        self.title = QLabel("🧩 技能库")
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
        self.title = QLabel("⏰ 定时任务")
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

