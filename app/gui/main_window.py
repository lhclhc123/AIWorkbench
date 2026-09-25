# -*- coding: utf-8 -*-
"""v9 主窗口：侧边导航 + 对话 / 记忆 / 工具 / 集成 / 设置 / 关于 六个页面。

相比 v8 的大改动：
- 左侧竖直导航栏，功能分页（不再把按钮堆在顶部一行）；
- 欢迎页 + 快捷指令卡片；
- 输入区加入「语音输入」：麦克风按钮 + 实时电平波形 + 自动静音结束；
- 每条消息都有 复制 / 朗读 / 重答 / 删除；
- 顶部状态标签（模型 / 联网 / Agent / 记忆 / 钉钉）实时反映真实状态；
- 「实际使用模型」脚注 + 模型被切换时的显式警告。
"""
import json
import os
import re
import time

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton,
    QPlainTextEdit, QScrollArea, QFrame, QLabel, QMessageBox, QApplication,
    QStackedWidget, QFileDialog, QTextEdit, QComboBox, QCheckBox, QDialog,
    QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor, QGuiApplication

from .. import (config, llm_client, workspace as ws_mod, agent as agent_mod,
                markdown_render, themes, docread, memory as memory_mod,
                trace as trace_mod, mcp_client, tts as tts_mod,
                dingtalk as dt_mod, updater, version as ver,
                skills as skills_mod, scheduler as _sched_mod,
                hotkey as _hotkey_mod, worklog as _worklog_mod)
from .chat_worker import ChatWorker
from .startup_dialog import StartupDialog
from .panels import PlanPanel
from .widgets import (MessageCard, ThinkingIndicator, make_icon, tool_label,
                      strip_tool_markup, BODY_WIDTH)
from .voice_bar import VoiceInput
from .pages import (NavRail, WelcomeView, MemoryPage, TracePage,
                    IntegrationPage, SettingsPage, AboutPage,
                    SkillsPage, TasksPage)

RADIUS_CARD = 12

# 自动化测试（离屏）时不要弹模态框，否则会一直等人点确定。
_NO_MODAL = bool(os.environ.get("AIWORKBENCH_TEST"))

# 只读命令（查版本、列目录、看 git 状态…）不弹确认框，直接放行。
_is_readonly_command = agent_mod.is_readonly_command


class TitleWorker(QThread):
    """后台用免费模型给对话生成简短标题。"""

    done = pyqtSignal(str)

    def __init__(self, client, user_text, ai_text):
        super().__init__()
        self.client = client
        self.user_text = user_text or ""
        self.ai_text = ai_text or ""

    def run(self):
        try:
            snippet = self.user_text[:400] + "\n---\n" + self.ai_text[:400]
            msgs = [
                {"role": "system", "content": config.TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": f"对话内容：\n{snippet}\n\n标题："},
            ]
            text = self.client.chat(msgs, "auto", False, None, 20)
            lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
            title = lines[0] if lines else ""
            title = title.strip().strip('"').strip("'").strip("《》").strip("`")
            title = re.sub(r"[：:。.！!？?\s]+$", "", title)
            self.done.emit(title[:16])
        except Exception:
            self.done.emit("")


class MainWindow(QMainWindow):
    # 后台任务（定时 / 子代理）跨线程回报
    sched_done = pyqtSignal(dict)
    sched_notice = pyqtSignal(str)
    subagent_notice = pyqtSignal(str)
    hotkey_triggered = pyqtSignal(str)
    # 「工作总结 + 自动记忆」的回执（后台线程 -> 界面）
    wrapup_notice = pyqtSignal(str)
    # 钉钉接口调试台：后台线程 -> 界面
    _dingtalk_probe_ready = pyqtSignal(list)
    _dingtalk_contacts_ready = pyqtSignal(str, list)
    _dingtalk_login_done = pyqtSignal(object, str, dict)

    def __init__(self, workspace: ws_mod.Workspace, settings: dict, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.settings = settings
        themes.set_font_scale(self.settings.get("font_scale", 100))

        self.client = llm_client.LLMClient()
        self.client.policy = self.settings.get("model_policy", "strict")
        self.client.set_keys(self.settings.get("api_keys", {}))
        # 后台无人值守跑的时候用独立客户端，避免和前台对话抢状态
        self.bg_client = llm_client.LLMClient()
        self.bg_client.policy = self.client.policy
        self.bg_client.set_keys(self.settings.get("api_keys", {}))
        self.memory = memory_mod.MemoryStore(self.workspace.path)
        self.trace = trace_mod.TraceLog(self.workspace.path)
        self.mcp = mcp_client.MCPManager(self.workspace.path)
        self.speaker = tts_mod.Speaker()
        self.speaker.engine = self.settings.get("tts_engine", "sapi")
        self.speaker.edge_voice = self.settings.get("tts_voice") or tts_mod.DEFAULT_EDGE_VOICE
        self.speaker.rate = int(self.settings.get("tts_rate", 0) or 0)
        self.ding = dt_mod.DingTalkClient(dt_mod.config_from_settings(self.settings))
        self.runner = self._make_runner()

        self.reminders = []          # [{text, ts, dingtalk, human}]
        self.attachments = []        # 待发送附件 [{name, kind, path, text, chars}]
        self.hotkeys = []            # 已注册的全局热键
        self.tray = None
        self._quit_requested = False
        self._last_bg_tools = []     # 后台任务最近一次真正执行过的工具名
        self._wrapup_busy = False    # 自动记忆（模型提炼）是否正在跑，防叠
        self.auto_mem_recent = []    # 界面显示：最近自动写入的记忆
        self.conv = None
        self.worker = None
        self.title_worker = None
        self.running = False
        self.live_text = ""
        self.pending_tool = []
        self._cards = []
        self._live_card = None
        self._last_model_label = ""
        self._pending_update = None

        self.setWindowTitle(f"{ver.APP_NAME} v{ver.VERSION}")
        self.setWindowIcon(make_icon())
        self.resize(1320, 860)
        self.setAcceptDrops(True)
        self._apply_theme()
        self._build_shell()
        self._open_workspace(self.workspace.path, first=True)
        self._wire_shortcuts()

        # 后台任务回报（跨线程 -> 界面）
        self.sched_done.connect(self._on_sched_done)
        self.sched_notice.connect(lambda m: self.statusBar().showMessage(str(m), 6000))
        self.subagent_notice.connect(
            lambda m: self.statusBar().showMessage(str(m), 9000))
        self.hotkey_triggered.connect(self._on_hotkey)
        self.wrapup_notice.connect(self._on_wrapup_notice)

        self._rem_timer = QTimer(self)
        self._rem_timer.setInterval(15000)
        self._rem_timer.timeout.connect(self._check_reminders)
        self._rem_timer.start()

        if self.settings.get("check_update_on_start", True):
            QTimer.singleShot(3000, lambda: self._do_update_check(silent=True))

        self._setup_tray()
        QTimer.singleShot(600, self._setup_hotkey)

    # ================= 托盘 + 全局热键 =================
    def _setup_tray(self):
        """系统托盘常驻：关窗口不退出，点托盘图标随时叫回来。"""
        self.tray = None
        self._quit_requested = False
        if not self.settings.get("tray_enabled", True):
            return
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            self.tray = QSystemTrayIcon(make_icon(), self)
            self.tray.setToolTip(f"{ver.APP_NAME} v{ver.VERSION}")
            menu = QMenu()
            act_show = menu.addAction("显示 / 隐藏主窗口")
            act_show.triggered.connect(self._toggle_window)
            act_voice = menu.addAction("语音输入（说话转文字）")
            act_voice.triggered.connect(self._tray_voice)
            act_ding = menu.addAction("发一句话到钉钉…")
            act_ding.triggered.connect(self._tray_dingtalk)
            menu.addSeparator()
            act_tasks = menu.addAction("查看定时任务")
            act_tasks.triggered.connect(lambda: self._show_window("tasks"))
            act_quit = menu.addAction("退出")
            act_quit.triggered.connect(self._quit_app)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()
            self._tray_menu = menu
        except Exception:
            self.tray = None

    def _setup_hotkey(self):
        """注册全局热键（Windows RegisterHotKey，不装钩子）。"""
        self.hotkeys = []
        spec = (self.settings.get("hotkey_show") or "ctrl+alt+space").strip()
        if spec:
            hk = _hotkey_mod.GlobalHotkey(spec, callback=self._hotkey_show)
            if hk.start_and_wait():
                self.hotkeys.append(hk)
            else:
                self.statusBar().showMessage(
                    f"全局热键「{spec}」注册失败：{hk.error or '被占用'}"
                    f"（可在设置里换一个）", 9000)
        vspec = (self.settings.get("hotkey_voice") or "ctrl+alt+v").strip()
        if vspec:
            hk2 = _hotkey_mod.GlobalHotkey(vspec, callback=self._hotkey_voice)
            if hk2.start_and_wait():
                self.hotkeys.append(hk2)

    def _hotkey_show(self):
        """全局热键触发（后台线程）-> 转成 Qt 信号，在界面线程里执行。"""
        self.hotkey_triggered.emit("show")

    def _hotkey_voice(self):
        self.hotkey_triggered.emit("voice")

    def _on_hotkey(self, what):
        if what == "voice":
            self._show_window("chat")
            self._toggle_recording()
        else:
            self._toggle_window()

    def _toggle_window(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self._show_window()

    def _show_window(self, page=None):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if page:
            self._open_page(page)
            if hasattr(self.nav, "select"):
                self.nav.select(page)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._toggle_window()

    def _tray_voice(self):
        self._show_window("chat")
        self._toggle_recording()

    def _tray_dingtalk(self):
        """托盘里快速往钉钉发一句（不打断当前对话）。"""
        from PyQt6.QtWidgets import QInputDialog
        if not self.ding.configured():
            QMessageBox.information(self, "钉钉未配置",
                                    "先到「集成」页把钉钉配置好。")
            return
        text, ok = QInputDialog.getMultiLineText(self, "发到钉钉", "内容：", "")
        if ok and text.strip():
            ok2, msg = self.ding.send(text.strip())
            self.statusBar().showMessage(("已发送：" if ok2 else "失败：") + str(msg)[:60],
                                         6000)

    def _quit_app(self):
        self._quit_requested = True
        self.close()

    def closeEvent(self, event):
        """默认「关窗口 = 收进托盘」，真正退出走托盘菜单的退出。"""
        try:
            self.scheduler.stop()
            self.scheduler.save()
        except Exception:
            pass
        for hk in getattr(self, "hotkeys", []) or []:
            try:
                hk.stop()
            except Exception:
                pass
        if (self.tray is not None and not self._quit_requested
                and self.settings.get("tray_on_close", True)):
            event.ignore()
            self.hide()
            try:
                self.tray.showMessage(
                    ver.APP_NAME, "已收进托盘，按 "
                    + (self.settings.get("hotkey_show") or "Ctrl+Alt+Space")
                    + " 随时叫回来。",
                    QSystemTrayIcon.MessageIcon.Information, 4000)
            except Exception:
                pass
            return
        try:
            if self.tray is not None:
                self.tray.hide()
        except Exception:
            pass
        event.accept()

    # ================= 主题 =================
    def _apply_theme(self):
        app = QApplication.instance()
        if app is None:
            themes.tokens()
            return
        return themes.apply(app, self.settings.get("theme", "light"))

    def _toggle_theme(self):
        self.settings["theme"] = "dark" if self.settings.get("theme") != "dark" else "light"
        self.workspace.save_settings(self.settings)
        self._apply_theme()
        self._refresh_all()

    def _refresh_all(self):
        t = themes.tokens()
        self.theme_btn.setText("☾ 深色" if t is themes.LIGHT else "☀ 浅色")
        self.chat_scroll.setStyleSheet(
            f"background:{t['chat_bg']};border-radius:{RADIUS_CARD}px;")
        self._refresh_chips()
        if hasattr(self, "plan_panel"):
            self.plan_panel.refresh_theme()
            self.plan_panel.set_plan((self.conv or {}).get("plan"))
        if hasattr(self, "voice"):
            self.voice.refresh_theme()
        if hasattr(self, "welcome"):
            self._stack_welcome()
        self._render()

    # ================= 外壳 =================
    def _build_shell(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = NavRail()
        self.nav.changed.connect(self._open_page)
        root.addWidget(self.nav)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.page_chat = self._build_chat_page()
        self.stack.addWidget(self.page_chat)

        self.page_skills = SkillsPage()
        self.stack.addWidget(self.page_skills)

        self.page_tasks = TasksPage()
        self.page_tasks.run_now.connect(self._run_task_now)
        self.stack.addWidget(self.page_tasks)

        self.page_memory = MemoryPage()
        self.page_memory.saved.connect(self._on_memory_saved)
        self.stack.addWidget(self.page_memory)

        self.page_tools = TracePage()
        self.stack.addWidget(self.page_tools)

        self.page_integrations = IntegrationPage()
        self.stack.addWidget(self.page_integrations)
        self.page_integrations.dingtalk_saved.connect(self._save_dingtalk)
        self.page_integrations.dingtalk_test.connect(self._test_dingtalk)
        self.page_integrations.dingtalk_stream.connect(self._toggle_dingtalk_stream)
        self.page_integrations.dingtalk_selftest.connect(self._dingtalk_selftest)
        self.page_integrations.dingtalk_contacts.connect(self._dingtalk_contacts)
        self.page_integrations.dingtalk_login.connect(self._dingtalk_login)
        self.page_integrations.dingtalk_group_send.connect(self._dingtalk_group_send)
        self._dingtalk_probe_ready.connect(self._on_dingtalk_selftest)
        self._dingtalk_contacts_ready.connect(self._on_dingtalk_contacts)
        self._dingtalk_login_done.connect(self._on_dingtalk_login_done)
        self.page_integrations.update_check.connect(lambda: self._do_update_check(False))
        self.page_integrations.update_saved.connect(self._save_update_settings)

        self.page_settings = SettingsPage()
        self.stack.addWidget(self.page_settings)
        self.page_settings.saved.connect(self._on_settings_saved)
        self.page_settings.probe_requested.connect(self._probe_providers)
        self.page_settings.voice_test.connect(self._tts_test)

        self.page_about = AboutPage()
        self.stack.addWidget(self.page_about)
        self.page_about.check_update.connect(lambda: self._do_update_check(False))

        self.statusBar().showMessage("就绪")

    # ================= 对话页 =================
    def _build_chat_page(self):
        t = themes.tokens()
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(12, 12, 12, 6)
        v.setSpacing(9)

        # ---------- 顶栏 ----------
        top = QFrame()
        top.setObjectName("TopBar")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(12, 8, 12, 8)
        tv.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.page_title = QLabel("💬 对话")
        self.page_title.setObjectName("PageTitle")
        bar.addWidget(self.page_title)
        bar.addSpacing(8)

        mlabel = QLabel("模型：")
        mlabel.setObjectName("PageSub")
        bar.addWidget(mlabel)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(280)
        self.model_combo.currentIndexChanged.connect(self._on_model_change)
        bar.addWidget(self.model_combo)

        self.search_cb = QCheckBox("联网搜索")
        self.search_cb.stateChanged.connect(self._on_search_change)
        bar.addWidget(self.search_cb)
        self.agent_cb = QCheckBox("Agent 模式")
        self.agent_cb.stateChanged.connect(self._on_agent_change)
        bar.addWidget(self.agent_cb)
        bar.addStretch(1)

        self.list_btn = QPushButton("☰ 列表")
        self.list_btn.setObjectName("cardBtn")
        self.list_btn.setFixedHeight(28)
        self.list_btn.setCheckable(True)
        self.list_btn.setChecked(True)
        self.list_btn.setToolTip("显示 / 隐藏左侧对话列表")
        self.list_btn.clicked.connect(self._toggle_conv_list)
        bar.addWidget(self.list_btn)

        self.stop_speak_btn = QPushButton("🔇")
        self.stop_speak_btn.setObjectName("cardBtn")
        self.stop_speak_btn.setFixedSize(34, 28)
        self.stop_speak_btn.setToolTip("停止朗读（Ctrl+Shift+S）")
        self.stop_speak_btn.clicked.connect(self._stop_speaking)
        bar.addWidget(self.stop_speak_btn)

        self.theme_btn = QPushButton("☾ 深色" if t is themes.LIGHT else "☀ 浅色")
        self.theme_btn.setObjectName("cardBtn")
        self.theme_btn.setFixedHeight(28)
        self.theme_btn.setToolTip("切换浅色 / 深色")
        self.theme_btn.clicked.connect(self._toggle_theme)
        bar.addWidget(self.theme_btn)
        tv.addLayout(bar)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_model = QLabel("")
        self.chip_model.setObjectName("Chip")
        self.chip_model.setToolTip("本次回复真实使用的模型（与你选的模型应当一致）")
        chips.addWidget(self.chip_model)
        self.chip_flags = QLabel("")
        self.chip_flags.setObjectName("Chip")
        chips.addWidget(self.chip_flags)
        self.chip_ctx = QLabel("")
        self.chip_ctx.setObjectName("Chip")
        self.chip_ctx.setToolTip("当前对话的上下文占用估算（中文约 1 字 ≈ 1 token）")
        chips.addWidget(self.chip_ctx)
        chips.addStretch(1)
        tv.addLayout(chips)
        v.addWidget(top)

        # ---------- 主体：左列表 + 右聊天 ----------
        body = QHBoxLayout()
        body.setSpacing(10)

        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(196)
        lv = QVBoxLayout(self.left_panel)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(7)
        lbl = QLabel("对话列表")
        lbl.setStyleSheet(f"color:{t['text']};font-size:13px;font-weight:bold;")
        lv.addWidget(lbl)
        self.conv_list = QListWidget()
        self.conv_list.itemClicked.connect(self._on_conv_selected)
        lv.addWidget(self.conv_list, 1)
        self.new_btn = QPushButton("＋ 新建对话")
        self.new_btn.setFixedHeight(32)
        self.new_btn.clicked.connect(self._new_conversation)
        self.del_btn = QPushButton("删除当前")
        self.del_btn.setObjectName("cardBtn")
        self.del_btn.setFixedHeight(28)
        self.del_btn.clicked.connect(self._delete_conversation)
        lv.addWidget(self.new_btn)
        lv.addWidget(self.del_btn)
        body.addWidget(self.left_panel, 0)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet(
            f"background:{t['chat_bg']};border-radius:{RADIUS_CARD}px;")
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(12, 12, 12, 12)
        self.chat_layout.setSpacing(14)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_scroll.setWidget(self.chat_container)
        right.addWidget(self.chat_scroll, 1)

        self.plan_panel = PlanPanel()
        right.addWidget(self.plan_panel)

        # 语音输入条
        self.voice = VoiceInput(
            keys_provider=lambda: self.settings.get("api_keys", {}),
            settings_provider=lambda: self.settings)
        self.voice.transcribed.connect(self._on_transcribed)
        self.voice.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.voice.failed.connect(self._on_voice_error)
        right.addWidget(self.voice)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        # ---------- 附件条（拖进来的文件显示成小卡片，不往输入框里灌正文）----------
        self.attach_bar = QWidget()
        self.attach_bar.setObjectName("AttachBar")
        self.attach_bar_layout = QHBoxLayout(self.attach_bar)
        self.attach_bar_layout.setContentsMargins(2, 0, 2, 0)
        self.attach_bar_layout.setSpacing(6)
        self.attach_bar.setVisible(False)
        right.addWidget(self.attach_bar)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(
            "输入消息，Enter 发送，Shift+Enter 换行　·　"
            "🎙 点左边的麦克风可以直接说话　·　文件/图片拖进窗口即可交给 AI")
        self.input.setMinimumHeight(84)
        self.input.setMaximumHeight(170)
        self.input.textChanged.connect(self._on_input_changed)
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.attach_btn = QPushButton("📎 添加文件")
        self.attach_btn.setFixedSize(96, 40)
        self.attach_btn.setToolTip("选择 Word / PDF / Excel / PPT / 图片 / 文本")
        self.attach_btn.clicked.connect(self._pick_file)
        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(96, 40)
        self.send_btn.clicked.connect(self._send)
        btn_col.addWidget(self.attach_btn)
        btn_col.addWidget(self.send_btn)
        btn_col.addStretch(1)
        input_row.addWidget(self.input, 1)
        input_row.addLayout(btn_col)
        right.addLayout(input_row)

        body.addLayout(right, 1)
        v.addLayout(body, 1)

        self.send_btn.setEnabled(False)
        self._render_timer = QTimer()
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._update_live)
        return page

    def _wire_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self, self._new_conversation)
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, self._toggle_recording)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._stop_speaking)
        QShortcut(QKeySequence("Ctrl+L"), self, lambda: self.input.clear())
        QShortcut(QKeySequence("Ctrl+="), self, lambda: self._zoom(5))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self._zoom(-5))

    def _zoom(self, delta):
        cur = int(self.settings.get("font_scale", 100) or 100)
        self.settings["font_scale"] = max(85, min(130, cur + delta))
        themes.set_font_scale(self.settings["font_scale"])
        self.workspace.save_settings(self.settings)
        self._render()
        self.statusBar().showMessage(f"界面缩放：{self.settings['font_scale']}%", 4000)

    def _toggle_conv_list(self):
        show = self.list_btn.isChecked()
        self.left_panel.setVisible(show)

    # ================= 页面切换 =================
    def _open_page(self, key):
        idx = {"chat": 0, "skills": 1, "tasks": 2, "memory": 3, "tools": 4,
               "integrations": 5, "settings": 6, "about": 7}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        if key == "skills":
            self.page_skills.bind(self.skills)
        elif key == "tasks":
            self.page_tasks.bind(self.scheduler)
        elif key == "memory":
            self.page_memory.bind(self.memory, self.workspace.path)
        elif key == "tools":
            self.page_tools.bind(self.trace)
        elif key == "integrations":
            self.page_integrations.load_dingtalk(self._ding_cfg())
            self.page_integrations.load_update(self.settings)
            self.page_integrations.bind_mcp(self.mcp, self.workspace.path)
        elif key == "settings":
            self.page_settings.load(self.settings)
        elif key == "chat":
            self.input.setFocus()
            self._scroll_to_bottom(True)

    # ================= 工作区 =================
    def _make_runner(self):
        r = agent_mod.AgentRunner(
            self.workspace.files_dir,
            vision_cb=self._vision,
            workspace_path=self.workspace.path,
            search_cb=self._web_search,
            mcp_manager=getattr(self, "mcp", None),
            speak_cb=self._speak,
            dingtalk_cb=self._dingtalk,
            dingtalk_api_cb=self._dingtalk_api,
            update_cb=self._update_text,
            reminder_cb=self._add_reminder,
            subagent_cb=self._run_subagent,
            keys=self.settings.get("api_keys", {}))
        # 让工具层也能读到设置（比如「禁止弹出新窗口」开关）
        r.settings = self.settings
        return r

    # ================= 工作总结 + 自动记忆（每轮结束都会跑） =================
    def _on_wrapup(self, data):
        try:
            self._wrapup(data)
        except Exception:
            pass

    def _wrapup(self, data, ui=True):
        """一轮结束后的自动归档：写工作总结 + 自动补长期记忆。

        刻意**不依赖模型是否愿意调 remember**：规则层当场落盘，
        语义层再交给便宜模型后台提炼一次，两层互为兜底。

        ui=False 时供后台线程（定时任务 / 子代理）调用，不碰界面。
        """
        if not isinstance(data, dict):
            return
        ws = self.workspace.path
        src = data.get("source") or "对话"
        req = data.get("user") or ""
        ans = data.get("answer") or ""
        tools = list(data.get("tools") or [])
        files = list(data.get("files") or [])
        ok = bool(data.get("ok", True))

        # 1) 工作总结 —— 程序直接写，不靠模型
        try:
            _worklog_mod.append(ws, source=src, request=req, tools=tools,
                                files=files, summary=ans, ok=ok,
                                model=data.get("model") or "")
        except Exception:
            pass

        # 2) 自动记忆：规则层（立刻生效）
        added = []
        try:
            for cat, text in memory_mod.auto_candidates(req):
                got, _c = self.memory.remember(text, cat)
                if got:
                    added.append((cat, text))
        except Exception:
            pass
        if added:
            try:
                memory_mod.log_auto(ws, added, source=src)
            except Exception:
                pass
            brief = "；".join(t for _c, t in added[:3])
            if ui:
                self.auto_mem_recent = (added + self.auto_mem_recent)[:50]
                self.statusBar().showMessage(f"🧠 已自动记入长期记忆：{brief}", 9000)
                try:
                    self.page_memory.refresh()
                except Exception:
                    pass
            else:
                self.wrapup_notice.emit(f"🧠 已自动记入长期记忆：{brief}")

        # 3) 语义层：让便宜模型再提炼「结论 / 待办」等（后台线程，不阻塞界面）
        if (tools or files) and not self._wrapup_busy:
            self._wrapup_busy = True
            self._wrapup_llm_async(req, ans, src)

    def _wrapup_llm_async(self, req, ans, src):
        import threading

        def worker():
            try:
                new = []
                for cat, text in self._extract_memories(req, ans):
                    got, _c = self.memory.remember(
                        text, cat or memory_mod.DEFAULT_CATEGORIES[0])
                    if got:
                        new.append((cat or memory_mod.DEFAULT_CATEGORIES[0], text))
                if new:
                    try:
                        memory_mod.log_auto(self.workspace.path, new,
                                            source=src + "·模型提炼")
                    except Exception:
                        pass
                    brief = "；".join(t for _c, t in new[:3])
                    self.wrapup_notice.emit(f"🧠 记忆提炼：{brief}")
            except Exception:
                pass
            finally:
                self._wrapup_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _extract_memories(self, req, ans):
        """用便宜的免费模型从这一轮里提炼值得长期记住的事实。"""
        if not req or not ans:
            return []
        prompt = (
            "从下面这一轮对话里提炼**值得跨会话长期记住**的信息。\n"
            "只输出 JSON 数组，不要任何解释、不要代码块标记。没有就输出 []。\n"
            "每项格式：{\"category\":\"用户与身份|偏好与习惯|项目与约定|重要结论|待办与计划\","
            "\"text\":\"一句话事实\"}\n"
            "规则：\n"
            "- 只记「用户是谁 / 习惯与禁忌 / 项目放在哪 / 得出了什么结论 / 有什么待办」；\n"
            "- 不要记一次性的问答内容、不要记寒暄、绝不编造用户没说的信息；\n"
            "- 每条 ≤ 60 字，最多 5 条；普通问答就输出 []。\n\n"
            f"【用户】{(req or '')[:800]}\n\n【助手】{(ans or '')[:1200]}\n"
        )
        try:
            msgs = [{"role": "system", "content": "你是信息抽取器，只输出 JSON。"},
                    {"role": "user", "content": prompt}]
            raw = self.bg_client.chat(msgs, "auto", False, None, 40)
            return memory_mod.extract_json_facts(raw)
        except Exception:
            return []

    def _on_wrapup_notice(self, text):
        self.statusBar().showMessage(text, 9000)
        try:
            self.page_memory.refresh()
        except Exception:
            pass
        try:
            self.auto_mem_recent = list(self.page_memory.auto_items())[:50]
        except Exception:
            pass

    # ================= 后台无人值守执行（定时任务 / 子代理） =================
    def _plain_runner(self, allow_subagent=True):
        """给后台线程用的 AgentRunner：只挂不碰界面的回调。"""
        def _sub(task, steps=6):
            return self._agent_run_once(task, max_iter=int(steps) + 2,
                                        quiet=True)
        return agent_mod.AgentRunner(
            self.workspace.files_dir,
            vision_cb=self._vision,
            workspace_path=self.workspace.path,
            search_cb=lambda q: self.client.web_search(q),
            mcp_manager=getattr(self, "mcp", None),
            dingtalk_cb=self._dingtalk,
            keys=self.settings.get("api_keys", {}),
            subagent_cb=(_sub if allow_subagent else None))

    def _agent_run_once(self, prompt, max_iter=8, quiet=False,
                        wrapup_source=None):
        """在两线程里跑一轮完整 Agent 循环（不碰界面），返回最终文字。

        定时任务和子代理都走这里：
        - 建一份干净的会话（系统提示 + 本任务），互不污染；
        - 用独立的 runner，避免和前台对话抢回调；
        - wrapup_source 不为 None 时，跑完自动写工作总结 + 自动记忆。
        """
        runner = self._plain_runner(allow_subagent=not quiet)
        model_sel = self.settings.get("selected_model", "auto")
        cli = self.bg_client
        cli.policy = self.client.policy
        cli.set_keys(self.settings.get("api_keys", {}))
        msgs = [{"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": prompt}]
        used = []
        last_text = ""
        plan_nudged = False
        meta_nudged = 0
        exec_nudged = 0
        tool_done = 0
        want_plan = agent_mod.needs_plan(prompt)
        want_action = agent_mod.wants_real_action(prompt)
        want_write = agent_mod.needs_write_action(prompt)
        write_done = False
        write_nudged = 0
        for _ in range(max(1, int(max_iter))):
            try:
                text = cli.chat(msgs, model_sel, False, None, 120)
            except Exception as e:
                return f"[错误] 模型调用失败：{type(e).__name__}: {e}"
            text = text or ""
            calls = agent_mod.parse_tool_calls(text, limit=3)
            clean = re.sub(r"<tool_call>.*?</tool_call>", "", text, flags=re.DOTALL)
            clean = re.sub(r"<tool_call>.*", "", clean, flags=re.DOTALL).strip()
            if clean:
                last_text = clean
            if not calls:
                # 要写入/创建文件却没调写入工具（含"嘴上说建好了"的假成功）-> 催它真写
                if want_write and not write_done and (
                        write_nudged < 1
                        or (write_nudged < 2 and agent_mod.claims_wrote_file(text))):
                    write_nudged += 1
                    msgs.append({"role": "assistant", "content": text})
                    hint = agent_mod.WRITE_NUDGE_HINT
                    if agent_mod.claims_wrote_file(text):
                        hint = agent_mod.FAKE_WRITE_HINT + "\n\n" + hint
                    msgs.append({"role": "user", "content": hint})
                    continue
                # 要动手却一个工具没调 -> 催它真做（实测模型会回"我明白了"式废话）
                if want_action and tool_done == 0 and exec_nudged < 1:
                    exec_nudged += 1
                    msgs.append({"role": "assistant", "content": text})
                    msgs.append({"role": "user",
                                 "content": agent_mod.EXECUTE_HINT})
                    continue
                # 要求列计划却没列 -> 催一次
                if want_plan and not plan_nudged and "update_plan" not in used:
                    plan_nudged = True
                    msgs.append({"role": "assistant", "content": text})
                    msgs.append({"role": "user",
                                 "content": agent_mod.PLAN_NUDGE_HINT})
                    continue
                # 对着系统说明"表态" -> 打回（最多 2 次）
                if agent_mod.is_meta_talk(clean or text) and meta_nudged < 2:
                    meta_nudged += 1
                    msgs.append({"role": "assistant", "content": text})
                    msgs.append({"role": "user",
                                 "content": agent_mod.ANSWER_NOW_HINT})
                    continue
                self._last_bg_tools = list(used)
                if wrapup_source:
                    self._bg_wrapup(wrapup_source, prompt, clean or last_text,
                                    used, runner)
                return clean or last_text or "（模型没有返回内容）"
            msgs.append({"role": "assistant", "content": text})
            for c in calls:
                res = runner.execute(c)
                _nm = runner.canonical_name(c.get("name"))
                used.append(_nm)
                tool_done += 1
                if _nm in ("write_file", "create_document", "archive",
                           "download_file", "screenshot") \
                        and str(res).startswith("[成功]"):
                    write_done = True
                # 未知工具 / 格式不对时，把「可用工具清单」一并回喂，帮它自己纠正
                if str(res).startswith("[错误]") and "未知工具" in str(res):
                    res = (str(res) + "\n提醒：工具名必须从上面的清单里选；"
                           "要按某个技能的步骤做，请调用 "
                           "use_skill({\"name\":\"技能名\"})。")
                msgs.append({"role": "tool",
                             "content": f"[工具 {c.get('name')} 返回]\n{res}"})
            msgs.append({"role": "user", "content": agent_mod.TOOL_FORMAT_HINT})
        self._last_bg_tools = list(used)
        tail = "、".join(used[-4:])
        final = (last_text or "（达到步数上限）") + (f"\n（执行过：{tail}）" if tail else "")
        if want_write and not write_done:
            final += ("\n\n> ⚠️ 这次要求写入/创建文件，但没有成功写出任何文件，"
                      "请以这条提醒为准。")
        if wrapup_source:
            self._bg_wrapup(wrapup_source, prompt, final, used, runner)
        return final

    def _bg_wrapup(self, source, prompt, answer, used, runner):
        """后台线程里的收尾归档（工作总结 + 自动记忆），不碰界面控件。"""
        try:
            files = list(getattr(runner, "written", []) or [])
            self._wrapup({
                "user": (prompt or "")[:1000],
                "answer": (answer or "")[:2000],
                "tools": list(dict.fromkeys(used or [])),
                "files": files,
                "ok": not str(answer or "").startswith("[错误]") or bool(files),
                "source": source,
                "model": getattr(self.bg_client, "last_model", "") or "",
            }, ui=False)
        except Exception:
            pass

    def _run_subagent(self, task, max_steps=6):
        """spawn_agent 工具的实现：独立上下文跑完，只把结论带回主对话。"""
        self.subagent_notice.emit(f"已派出子代理：{task[:60]}")
        res = self._agent_run_once(task, max_iter=int(max_steps) + 2, quiet=True)
        res = str(res or "").strip()
        if len(res) > 3000:
            res = res[:3000] + "\n…（子代理结果过长，已截断）"
        return res

    def _sched_log(self, msg):
        """调度器日志 -> 界面（跨线程，用信号）。"""
        try:
            self.sched_notice.emit(str(msg))
        except Exception:
            pass

    def _run_scheduled(self, task):
        """定时任务到期后的真实执行（在调度器线程里跑）。"""
        prompt = (task.get("prompt") or "").strip()
        if not prompt:
            return "[错误] 任务内容为空"
        self.sched_notice.emit(f"定时任务「{task.get('name')}」开始执行…")
        res = self._agent_run_once(
            prompt, max_iter=10,
            wrapup_source=f"定时任务「{task.get('name')}」")
        tools = list(getattr(self, "_last_bg_tools", []) or [])
        if tools:
            uniq = list(dict.fromkeys(tools))
            res = f"{res}\n（执行过：{'、'.join(uniq)}）"
        elif agent_mod.is_meta_talk(res):
            res = ("⚠️ 这次模型没有真正动手（只回了客套话），"
                   "任务内容可能描述得不够具体。下次触发会再试一次。\n" + str(res))
        if task.get("notify_dingtalk") and self.ding.configured():
            try:
                self.ding.send(f"【{task.get('name')}】\n{str(res)[:1200]}",
                               title=f"定时任务：{task.get('name')}")
            except Exception as e:
                res += f"\n（钉钉推送失败：{e}）"
        self.sched_done.emit({"task": task, "result": str(res)})
        return str(res)

    def _run_task_now(self, task_id):
        """「立即执行一次」按钮：丢到后台线程跑，别卡界面。"""
        t = self.scheduler.get(task_id)
        if not t:
            return
        self.statusBar().showMessage(f"正在执行定时任务「{t['name']}」…")

        class _Job(QThread):
            done = pyqtSignal(str, str)

            def __init__(self, outer, task):
                super().__init__()
                self.outer = outer
                self.task = task

            def run(self):
                try:
                    res = self.outer._run_scheduled(self.task)
                except Exception as e:
                    res = f"[错误] {type(e).__name__}: {e}"
                self.done.emit(self.task.get("id", ""), str(res))

        self._task_job = _Job(self, t)
        self._task_job.done.connect(self._on_task_job_done)
        self._task_job.start()

    def _on_task_job_done(self, task_id, result):
        self.page_tasks.reload()
        self.statusBar().showMessage("定时任务执行完成", 4000)
        self._append_system_note(
            f"⏰ 定时任务手动执行完成：\n{result[:1500]}")

    def _on_sched_done(self, payload):
        """调度器到点自动跑完后的界面回报。"""
        t = payload.get("task") or {}
        res = payload.get("result") or ""
        self.page_tasks.reload()
        self._append_system_note(
            f"⏰ 定时任务「{t.get('name')}」已自动执行：\n{res[:1500]}")

    def _append_system_note(self, text):
        """把后台发生的事写进当前对话（用户看得见）。"""
        if not self.conv:
            return
        self.conv.setdefault("messages", []).append(
            {"role": "tool", "content": text})
        try:
            self.workspace.save_conversation(self.conv)
        except Exception:
            pass
        self._render()

    def _open_workspace(self, path, first=False):
        self.workspace = ws_mod.Workspace(path).ensure()
        self.settings = self.workspace.load_settings()
        themes.set_theme(self.settings.get("theme", "light"))
        themes.set_font_scale(self.settings.get("font_scale", 100))
        self.client.set_keys(self.settings.get("api_keys", {}))
        self.client.policy = self.settings.get("model_policy", "strict")
        try:
            self.mcp.close_all()
        except Exception:
            pass
        self.memory = memory_mod.MemoryStore(self.workspace.path)
        memory_mod.build_default_memory_file(self.workspace.path)
        try:
            self.page_memory.bind(self.memory, self.workspace.path)
        except Exception:
            pass
        self.trace = trace_mod.TraceLog(self.workspace.path)
        self.mcp = mcp_client.MCPManager(self.workspace.path)
        self.skills = skills_mod.SkillStore(self.workspace.path)
        # 换工作区 -> 调度器换数据文件（先停旧的，避免两个线程同时写）
        try:
            self.scheduler.stop()
            self.scheduler.save()
        except Exception:
            pass
        self.scheduler = _sched_mod.Scheduler(self.workspace.path,
                                             run_cb=self._run_scheduled,
                                             log_cb=self._sched_log)
        self.scheduler.start()
        self.ding.update(self._ding_cfg())
        self.speaker.engine = self.settings.get("tts_engine", "sapi")
        self.speaker.edge_voice = self.settings.get("tts_voice") or tts_mod.DEFAULT_EDGE_VOICE
        self.speaker.rate = int(self.settings.get("tts_rate", 0) or 0)
        self.runner = self._make_runner()
        ws_mod.set_last_workspace(path)
        if first:
            self._apply_theme()
        if hasattr(self, "plan_panel"):
            self.plan_panel.set_plan(None)
        self._fill_model_combo()
        self._sync_toolbar()
        self._load_conversations()
        if self.conv_list.count() > 0:
            self._on_conv_selected(self.conv_list.item(0))
        else:
            self._new_conversation(silent=True)
        self._refresh_all()
        self._maybe_start_dingtalk_stream()

    def _load_conversations(self):
        self.conv_list.clear()
        for c in self.workspace.list_conversations():
            self.conv_list.addItem(c["title"])
            self.conv_list.item(self.conv_list.count() - 1).setData(
                Qt.ItemDataRole.UserRole, c["id"])

    def _new_conversation(self, silent=False):
        self.conv = self.workspace.new_conversation()
        self.live_text = ""
        self.pending_tool = []
        self._render()
        if not silent:
            self._load_conversations()
        self.input.setFocus()
        self._open_page("chat")

    def _on_conv_selected(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        conv = self.workspace.load_conversation(cid)
        if conv:
            self.conv = conv
            self.live_text = ""
            self.pending_tool = []
            self._render()

    def _delete_conversation(self):
        if not self.conv:
            return
        if QMessageBox.question(self, "确认", f"删除当前对话「{self.conv.get('title','')}」？") \
                != QMessageBox.StandardButton.Yes:
            return
        self.workspace.delete_conversation(self.conv["id"])
        self._load_conversations()
        self._new_conversation(silent=True)

    # ================= 模型 / 状态 =================
    def _fill_model_combo(self):
        """（重建）模型下拉框：屏蔽信号，避免 addItem 时把用户选择覆盖成 auto。"""
        prev = (self.settings or {}).get("selected_model", "auto")
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem("[自动] 免费优先（省钱）", "auto")
        last_provider = None
        for m in config.all_models_sorted():
            if m["provider_label"] != last_provider:
                last_provider = m["provider_label"]
                self.model_combo.insertSeparator(self.model_combo.count())
                self.model_combo.addItem(f"— {last_provider} —", "__sep__")
                idx = self.model_combo.count() - 1
                item = self.model_combo.model().item(idx)
                if item is not None:
                    item.setEnabled(False)
            badge = "[免费]" if m["free"] else "[付费]"
            vision = " · 看图" if m.get("vision") else ""
            self.model_combo.addItem(f"{badge} {m['name']}{vision}", m["choice"])
        idx = self.model_combo.findData(prev)
        if idx < 0:
            idx = self.model_combo.findData("auto")
        self.model_combo.setCurrentIndex(max(0, idx))
        self.model_combo.blockSignals(False)

    def _sync_toolbar(self):
        idx = self.model_combo.findData(self.settings.get("selected_model", "auto"))
        if idx >= 0:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(idx)
            self.model_combo.blockSignals(False)
        self.search_cb.blockSignals(True)
        self.search_cb.setChecked(bool(self.settings.get("enable_search", False)))
        self.search_cb.blockSignals(False)
        self.agent_cb.blockSignals(True)
        self.agent_cb.setChecked(bool(self.settings.get("agent_mode", True)))
        self.agent_cb.blockSignals(False)
        self._refresh_chips()

    def _on_search_change(self, state):
        self.settings["enable_search"] = (state == Qt.CheckState.Checked.value)
        self.workspace.save_settings(self.settings)
        self._refresh_chips()

    def _on_agent_change(self, state):
        self.settings["agent_mode"] = (state == Qt.CheckState.Checked.value)
        self.workspace.save_settings(self.settings)
        self._refresh_chips()

    def _model_label_short(self):
        data = self.settings.get("selected_model", "auto")
        if not data or data == "auto":
            return "模型：自动（免费优先）"
        txt = self.model_combo.currentText().strip()
        return "模型：" + (txt[:34] + "…" if len(txt) > 34 else txt)

    def _refresh_chips(self):
        if getattr(self, "_last_model_label", ""):
            self.chip_model.setText("本次实际使用：" + self._last_model_label)
        else:
            self.chip_model.setText(self._model_label_short())
        bits = []
        bits.append("联网✓" if self.settings.get("enable_search") else "联网—")
        bits.append("Agent✓" if self.settings.get("agent_mode") else "Agent—")
        try:
            data = self.memory.parse()
            n = sum(len(x) for x in data.values())
            if n:
                bits.append(f"记忆 {n}")
        except Exception:
            pass
        try:
            if self.mcp.configured():
                bits.append("MCP✓")
        except Exception:
            pass
        bits.append("钉钉✓" if self.ding.configured() else "钉钉—")
        bits.append("语音✓" if _asr_ok(self.settings) else "语音—")
        if self.reminders:
            bits.append(f"提醒 {len(self.reminders)}")
        if self.attachments:
            bits.append(f"附件 {len(self.attachments)}")
        if self.scheduler.list_all():
            n = sum(1 for t in self.scheduler.list_all() if t.get("enabled"))
            bits.append(f"定时 {n}")
        self.chip_flags.setText("　".join(bits))
        # 上下文占用估算（把即将发给模型的东西按字符估个 token 数）
        try:
            total = len(self.settings.get("system_prompt", "") or "")
            for m in (self.conv or {}).get("messages", []):
                total += len(str(m.get("content") or "")) \
                    + len(str(m.get("attached") or ""))
            total += sum(a["chars"] for a in self.attachments)
            k = total / 1000.0
            self.chip_ctx.setText(f"上下文≈{k:.1f}k")
            self.chip_ctx.setToolTip(
                f"当前对话约 {total} 字（含系统提示、附件）。\n"
                f"中文大致 1 字 ≈ 1 token，超过 70k 会自动压缩早年消息。")
        except Exception:
            self.chip_ctx.setText("")

    def _update_status(self):
        self._refresh_chips()

    def _on_input_changed(self):
        if not self.running:
            self.send_btn.setEnabled(bool(self.input.toPlainText().strip())
                                     or bool(self.attachments))

    # ================= 语音 =================
    def _on_transcribed(self, text):
        cur = self.input.toPlainText().strip()
        self.input.setPlainText((cur + " " + text).strip() if cur else text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self._open_page("chat")
        if self.settings.get("voice_auto_send"):
            self._send()

    def _on_voice_error(self, msg):
        QMessageBox.warning(self, "语音识别", str(msg))
        self.statusBar().showMessage("语音识别失败", 5000)

    def _toggle_recording(self):
        self.voice.toggle()

    def _speak(self, text, engine=None):
        if self.speaker.speak(text, engine=engine or self.speaker.engine):
            return f"[成功] 已朗读（{len(text)} 字，{engine or self.speaker.engine} 引擎）"
        return "[提示] 没有可朗读的内容。"

    def _stop_speaking(self):
        self.speaker.stop()
        self.statusBar().showMessage("已停止朗读", 3000)

    def _tts_test(self, engine):
        self.speaker.engine = engine
        self.speaker.edge_voice = self.page_settings.tts_voice.currentData() or \
            tts_mod.DEFAULT_EDGE_VOICE
        self.speaker.rate = self.page_settings.tts_rate.value()
        self.speaker.speak("你好，我是 AI 工作台，这就是我的声音。")

    # ================= 钉钉 =================
    def _ding_cfg(self):
        return dt_mod.config_from_settings(self.settings)

    # ---------- 钉钉连接器：AI 工具可调用的高级操作 ----------
    def _dingtalk_api(self, action, args):
        """供 Agent 的 dingtalk 工具调用。action 决定做什么，返回给人看的文本。

        支持：status / selftest / contacts / send / work_notice / group_send / whoami
        """
        cfg = self._ding_cfg()
        self.ding.update(cfg)
        act = str(action or "status").strip().lower()
        args = args or {}

        if act in ("status", "状态"):
            return self._dingtalk("", "__status__", False)

        if act in ("selftest", "自检", "test", "diagnose"):
            items = self.ding.self_test()
            lines = [f"钉钉接口自检（{sum(1 for _n, ok, _m in items if ok)}/{len(items)} 可用）："]
            for n, ok, m in items:
                lines.append(("✅ " if ok else "❌ ") + n + "\n    " + str(m))
            return "\n".join(lines)

        if act in ("contacts", "通讯录", "members", "找联系人", "查联系人"):
            dept = int(args.get("dept_id") or 1)
            kw = str(args.get("keyword") or args.get("name") or "").strip()
            depts = self.ding.list_departments(dept)
            mem = self.ding.list_members(dept, int(args.get("limit") or 100))
            if kw:
                mem = [m for m in mem if kw in (m.get("name") or "")
                       or kw in m.get("userid", "")]
            if not mem and dept == 1:
                # 根部门常常没有直属成员，去子部门找一层
                for d in depts[:10]:
                    try:
                        sub = self.ding.list_members(d["id"], 100)
                    except Exception:
                        continue
                    for m in sub:
                        if not kw or kw in (m.get("name") or "") or kw in m["userid"]:
                            m["_dept"] = d["name"]
                            mem.append(m)
            if not mem:
                return ("通讯录里没读到成员。子部门：" +
                        ("、".join(d["name"] for d in depts) if depts else "无") +
                        "。请确认应用已开通「通讯录」成员信息读权限。")
            head = f"通讯录（部门 {dept}）：{len(mem)} 人"
            if kw:
                head += f"，匹配「{kw}」"
            lines = [head]
            for m in mem[:40]:
                extra = " · " + m["title"] if m.get("title") else ""
                if m.get("_dept"):
                    extra += f" · 部门：{m['_dept']}"
                lines.append(f"· {m['name']}（userId={m['userid']}）{extra}")
            if depts:
                lines.append("子部门：" + "、".join(
                    f"{d['name']}({d['id']})" for d in depts[:10]))
            return "\n".join(lines)

        if act in ("send", "发送", "push", "推送", "send_message"):
            text = (args.get("text") or args.get("content") or args.get("message") or "").strip()
            if not text:
                return "[错误] 需要 text（要发送的内容）"
            r = self.ding.send(text, title=(args.get("title") or "").strip(),
                               at_all=args.get("at_all"))
            return f"[成功] 已通过钉钉发送（{r.get('target') or '群机器人'}）"

        if act in ("work_notice", "工作通知", "notify"):
            text = (args.get("text") or args.get("content") or "").strip()
            if not text:
                return "[错误] 需要 text"
            users = args.get("user_ids") or args.get("userid")
            if isinstance(users, str):
                users = [u.strip() for u in users.split(",") if u.strip()]
            r = self.ding.send_work_notice(
                text, user_ids=users, to_all=bool(args.get("to_all")))
            return f"[成功] 工作通知已受理（task_id={r.get('task_id')}）"

        if act in ("group_send", "发群", "send_group"):
            text = (args.get("text") or args.get("content") or "").strip()
            if not text:
                return "[错误] 需要 text"
            r = self.ding.send_group(text, args.get("open_conversation_id"),
                                     title=(args.get("title") or "").strip())
            return f"[成功] 已发到群（{r.get('target')}）"

        if act in ("group", "groups", "群信息"):
            cid = str(args.get("chat_id") or "").strip()
            if not cid:
                return ("按 chatId 查群需要 chat_id 参数。"
                        "（钉钉不提供「列出我所有群」的接口，"
                        "要往群里发消息请用 openConversationId。）")
            info = self.ding.get_group(cid)
            return "群信息：" + json.dumps(info, ensure_ascii=False)[:1500]

        if act in ("whoami", "我是谁", "身份"):
            d = self.settings.get("dingtalk") or {}
            if d.get("login_user_name") or d.get("login_user_id"):
                return (f"已授权登录的钉钉账号：{d.get('login_user_name') or '(无名)'}\n"
                        f"unionId：{d.get('login_user_id') or '无'}")
            return ("还没做过扫码授权登录。可以到「集成 → 钉钉 → 扫码授权登录」"
                    "点一下，用浏览器扫码。")

        return ("[错误] 不认识的 action：" + act +
                "。可用：status / selftest / contacts / send / work_notice / "
                "group_send / group / whoami")

    def _save_dingtalk(self, cfg):
        self.settings["dingtalk"] = cfg
        self.workspace.save_settings(self.settings)
        self.ding.update(cfg)
        ok = self.ding.configured()
        self.page_integrations.set_dingtalk_status(
            "已保存。当前状态：" + self.ding.status_text())
        self._refresh_chips()
        if ok:
            QTimer.singleShot(200, lambda: self._test_dingtalk(cfg))

    def _test_dingtalk(self, cfg):
        self.ding.update(cfg)
        ok, msg = self.ding.test()
        self.page_integrations.set_dingtalk_status(
            ("✅ " if ok else "❌ ") + msg)
        self.statusBar().showMessage(("钉钉：" + msg)[:120], 8000)

    # ---------- 接口调试台：自检 / 通讯录 / 扫码登录 / 发群 ----------
    def _dingtalk_selftest(self, cfg):
        """逐项真调钉钉接口，把结果摊在界面上（后台线程，别卡 UI）。"""
        self.ding.update(cfg)
        pi = self.page_integrations
        pi.set_dingtalk_status("正在逐项调用钉钉接口…")
        pi.set_probe_html(
            f"<span style='color:{themes.tokens()['text_muted']}'>正在自检…</span>")
        import threading

        def worker():
            try:
                items = self.ding.self_test()
            except Exception as e:
                items = [("自检异常", False, f"{type(e).__name__}: {e}")]
            self._dingtalk_probe_ready.emit(items)
        threading.Thread(target=worker, daemon=True).start()

    def _on_dingtalk_selftest(self, items):
        pi = self.page_integrations
        pi.show_probe_items(items, "钉钉接口自检结果")
        okn = sum(1 for _n, ok, _m in items if ok)
        pi.set_dingtalk_status(
            f"自检完成：{okn} / {len(items)} 项可用。"
            + ("有项目不通，看下面每一项的钉钉原话。" if okn < len(items) else "全部可用。"))

    def _dingtalk_contacts(self, cfg):
        """列出部门与成员（真实姓名 + userId），可直接填成接收人。"""
        self.ding.update(cfg)
        pi = self.page_integrations
        if not (cfg.get("client_id") and cfg.get("client_secret")):
            pi.set_dingtalk_status("请先填 AppKey / AppSecret。")
            return
        pi.set_dingtalk_status("正在读钉钉通讯录…")
        import threading

        def worker():
            try:
                depts = self.ding.list_departments(1)
                mem = self.ding.list_members(1, 100)
                lines = [f"根部门（dept_id=1）下子部门 {len(depts)} 个"]
                for d in depts[:20]:
                    lines.append(f"  · {d['name']}（id={d['id']}）")
                lines.append(f"\n根部门直属成员 {len(mem)} 人：")
                for m in mem[:40]:
                    extra = f" · {m['title']}" if m.get("title") else ""
                    lines.append(f"  · {m['name']}（userId={m['userid']}）{extra}")
                if not mem:
                    lines.append("  （空）根部门下没有直属成员，成员可能在子部门里。")
                self._dingtalk_contacts_ready.emit(
                    "\n".join(lines), [m["userid"] for m in mem[:20]])
            except Exception as e:
                self._dingtalk_contacts_ready.emit(f"读通讯录失败：{e}", [])
        threading.Thread(target=worker, daemon=True).start()

    def _on_dingtalk_contacts(self, text, userids):
        pi = self.page_integrations
        pi.show_probe_text(text)
        if userids:
            pi._found_users = list(userids)
            pi.dt_copy_uid.setEnabled(True)
            pi.set_dingtalk_status(
                f"查到 {len(userids)} 个成员。点「复制到接收人」即可填入前几个。")
        else:
            pi.set_dingtalk_status("通讯录没读到成员，请看下面的原始结果。")

    def _dingtalk_login(self, cfg):
        """弹浏览器让用户扫码授权登录（本地起 127.0.0.1 回调）。"""
        self.ding.update(cfg)
        pi = self.page_integrations
        if not (cfg.get("client_id") and cfg.get("client_secret")):
            pi.set_dingtalk_status("扫码登录需要先填 AppKey / AppSecret。")
            return
        import threading

        def worker():
            try:
                srv = dt_mod.OAuthCallbackServer(
                    port=int(cfg.get("oauth_port") or 8765)).start()
            except Exception as e:
                self._dingtalk_login_done.emit(False, f"起本地回调失败：{e}", {})
                return
            try:
                url = self.ding.auth_url(srv.redirect_uri)
            except Exception as e:
                srv.stop()
                self._dingtalk_login_done.emit(False, str(e), {})
                return
            self._dingtalk_login_done.emit(
                None, "已打开浏览器，请在钉钉里扫码/确认授权…\n"
                      f"（回调地址：{srv.redirect_uri}，最长等 5 分钟）", {})
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
            ok, res = srv.wait()
            if not ok:
                self._dingtalk_login_done.emit(False, f"授权没完成：{res}", {})
                return
            try:
                tok, raw = self.ding.exchange_user_token(res)
                me = self.ding.get_login_user(tok)
            except Exception as e:
                self._dingtalk_login_done.emit(False, f"换取用户信息失败：{e}", {})
                return
            info = {"nick": me.get("nick") or "", "unionId": me.get("unionId") or "",
                    "openId": me.get("openId") or "",
                    "mobile": me.get("mobile") or "", "token": tok}
            self._dingtalk_login_done.emit(True, "授权成功", info)
        threading.Thread(target=worker, daemon=True).start()

    def _on_dingtalk_login_done(self, ok, msg, info):
        pi = self.page_integrations
        if ok is None:
            pi.set_dingtalk_status(msg)
            pi.show_probe_text(msg)
            return
        if not ok:
            pi.set_dingtalk_status("❌ " + msg)
            pi.show_probe_text("扫码授权登录失败：\n" + msg +
                               "\n\n常见原因：\n"
                               "1) 钉钉开发者后台 → 应用 → 「登录与分享」里没有把\n"
                               "   http://127.0.0.1:8765/callback 加进「回调域名」；\n"
                               "2) redirect_uri 必须与后台登记的一模一样（含端口）；\n"
                               "3) 应用的「登录」能力没开通。")
            return
        d = self.settings.setdefault("dingtalk", {})
        d["login_user_id"] = info.get("unionId", "")
        d["login_user_name"] = info.get("nick", "")
        # 用户级 token 单独存，便于后续调用需要用户身份的接口（待办/日历等）
        d["login_user_token"] = info.get("token", "")
        self.workspace.save_settings(self.settings)
        lines = ["✅ 扫码授权登录成功",
                 f"昵称：{info.get('nick') or '（未返回）'}",
                 f"unionId：{info.get('unionId') or '（未返回）'}",
                 f"手机号：{info.get('mobile') or '（未返回）'}",
                 "",
                 "已保存用户身份。这条通道拿到的是「用户级 token」，"
                 "可以继续用来调用需要用户身份的钉钉接口。"]
        pi.set_dingtalk_status(f"✅ 已授权登录：{info.get('nick') or info.get('unionId')}")
        pi.show_probe_text("\n".join(lines))

    def _dingtalk_group_send(self, cfg, text):
        self.ding.update(cfg)
        pi = self.page_integrations
        if not text:
            pi.set_dingtalk_status("请先填要发到群里的内容。")
            return
        try:
            r = self.ding.send_group(text)
            pi.set_dingtalk_status(f"✅ 已发到群（{r.get('target')}）")
        except Exception as e:
            pi.set_dingtalk_status(f"❌ 发群失败：{e}")

    def _toggle_dingtalk_stream(self, cfg, enable):
        self.ding.update(cfg)
        if enable:
            ok, msg = self.ding.start_stream(self._dingtalk_on_message,
                                            on_log=self._dingtalk_log)
            self.page_integrations.set_dingtalk_status(("✅ " if ok else "❌ ") + msg)
            if ok:
                # 连接是异步的：别只等 2.5 秒就下结论（以前会一直显示"连接中…"，
                # 用户看起来就像"连不上"）。这里持续刷新 12 次 × 2 秒，
                # 直到真的连上或超时，并把最后一条日志也显示出来。
                self._dt_tick = {"n": 0}

                def tick():
                    d = getattr(self, "_dt_tick", None)
                    if not d:
                        return
                    d["n"] += 1
                    live = bool(getattr(self.ding, "_stream_running", False))
                    tail = (self._dt_last_log or "").strip()
                    if live:
                        self.page_integrations.set_dingtalk_status(
                            "✅ Stream 已连接，正在接收机器人消息")
                        self._dt_tick = None
                        return
                    if d["n"] >= 12:
                        self.page_integrations.set_dingtalk_status(
                            "❌ Stream 连不上（已等 24 秒）。\n"
                            "看下面日志最后一行；常见原因是应用没开机器人的 Stream 模式。"
                            + (("\n最后一条日志：" + tail) if tail else ""))
                        self._dt_tick = None
                        return
                    self.page_integrations.set_dingtalk_status(
                        f"⏳ Stream 连接中…（{d['n'] * 2}s）"
                        + ((" · " + tail) if tail else ""))
                    QTimer.singleShot(2000, tick)

                QTimer.singleShot(2000, tick)
        else:
            self._dt_tick = None
            self.ding.stop_stream()
            self.page_integrations.set_dingtalk_status("Stream 已断开。")

    def _maybe_start_dingtalk_stream(self):
        cfg = self._ding_cfg()
        if cfg.get("receive_enabled") and cfg.get("client_id") and cfg.get("client_secret"):
            self.ding.start_stream(self._dingtalk_on_message, on_log=self._dingtalk_log)

    def _dingtalk_log(self, msg):
        try:
            self._dt_last_log = str(msg)[:160]
        except Exception:
            self._dt_last_log = ""
        try:
            self.statusBar().showMessage("钉钉：" + str(msg)[:110], 6000)
        except Exception:
            pass

    def _dingtalk_on_message(self, text, sender, ctype, raw):
        """钉钉来的消息 -> 用本地 Agent 处理 -> 回复（在 SDK 线程里执行，注意别碰 UI）。"""
        t = (text or "").strip()
        if not t:
            return None
        if t in ("帮助", "/help", "help"):
            return ("我是 AI 工作台，可以直接给我派活：\n"
                    "· 帮我查一下今天的天气\n· 把这段话整理成周报\n"
                    "· 搜索最新消息并总结\n（本机桌面版能看到更完整的执行过程）")
        try:
            self.client.reset_used()
            msgs = [{"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": t}]
            reply = self.client.chat(msgs, self.settings.get("selected_model", "auto"),
                                     False, None, 60)
            note = (getattr(self.client, "notice", "") or "")
            return ((note + "\n\n" if note else "") + (reply or "（没有生成有效回复）"))[:4000]
        except Exception as e:
            return f"处理失败：{e}"

    def _dingtalk(self, text, title, at_all):
        """AgentRunner 的钉钉回调。title == '__status__' 时只返回状态。"""
        if title == "__status__":
            lines = ["钉钉通道状态：" + self.ding.status_text()]
            cfg = self.ding.cfg
            lines.append(f"启用：{cfg.get('enabled')}　模式：{self.ding.mode}")
            if self.ding.mode == "webhook":
                lines.append("Webhook：" + ("已配置" if cfg.get("webhook") else "未配置"))
                lines.append("加签：" + ("已配置" if cfg.get("secret") else "无"))
            else:
                lines.append("AppKey：" + ("已配置" if cfg.get("client_id") else "未配置"))
                lines.append("群会话 ID：" + ("已配置" if cfg.get("open_conversation_id") else "未配置"))
                lines.append("接收人：" + (cfg.get("user_ids") or "未配置"))
            lines.append("Stream 收消息：" + ("运行中" if self.ding._stream_running else "未运行"))
            return "\n".join(lines)
        if not self.ding.configured():
            return ("[提示] 钉钉还没配置好。请到「集成 → 钉钉」里填好 Webhook（或 AppKey/AppSecret）"
                    "并勾选「启用」，然后点「发送测试消息」验证一下。")
        try:
            r = self.ding.send(text, title=title or None, at_all=at_all)
            return f"[成功] 已推送到钉钉（{r.get('target') or '群机器人'}）：{text[:60]}"
        except Exception as e:
            return f"[错误] 钉钉推送失败：{e}"

    # ================= 提醒 =================
    def _add_reminder(self, text, ts, use_dingtalk, human):
        self.reminders.append({"text": text, "ts": float(ts),
                               "dingtalk": bool(use_dingtalk), "human": human})
        self._refresh_chips()
        extra = "，到点也会推到钉钉" if use_dingtalk else ""
        return f"[成功] 提醒已设置：{human} —— {text}{extra}"

    def _check_reminders(self):
        now = time.time()
        fired = [r for r in self.reminders if r["ts"] <= now]
        if not fired:
            return
        self.reminders = [r for r in self.reminders if r["ts"] > now]
        for r in fired:
            msg = f"⏰ 提醒：{r['text']}"
            self._notify("AI 工作台提醒", r["text"])
            if r["dingtalk"] and self.ding.configured():
                try:
                    self.ding.send(msg, title="AI 工作台提醒")
                except Exception:
                    pass
            if self.conv is not None:
                self.pending_tool.append({"role": "tool", "content": "[成功] " + msg})
                self._render()
        self._refresh_chips()

    @staticmethod
    def _notify(title, message):
        try:
            agent_mod._toast(title, message)
        except Exception:
            pass

    # ================= 更新 =================
    def _save_update_settings(self, cfg):
        self.settings.update(cfg)
        self.workspace.save_settings(self.settings)
        self.statusBar().showMessage("更新设置已保存", 4000)

    def _update_text(self):
        """给 AgentRunner 用的 check_update 工具。"""
        info = updater.check(self.settings.get("update_repo") or ver.GITHUB_REPO,
                             ver.VERSION,
                             self.settings.get("github_token") or None)
        if not info.get("ok"):
            if info.get("no_release"):
                return (f"[成功] 当前版本 {info.get('current')}。"
                        f"仓库 {info.get('repo')} 还没发布过 Release，"
                        f"所以没有可对比的新版本。\n发布页：{info.get('html_url')}")
            return f"[错误] 检查更新失败：{info.get('error')}"
        if info.get("has_update"):
            asset = updater.pick_asset(info)
            extra = f"\n下载：{asset['url']}" if asset else ""
            return (f"[成功] 发现新版本 {info.get('tag')}（当前 {info.get('current')}）。\n"
                    f"发布页：{info.get('html_url')}{extra}\n\n"
                    f"更新说明：\n{updater.format_notes(info.get('notes'))}")
        return f"[成功] 已是最新版本（{info.get('current')}）。"

    def _do_update_check(self, silent):
        self.statusBar().showMessage("正在检查更新…")
        QApplication.processEvents()
        info = updater.check(self.settings.get("update_repo") or ver.GITHUB_REPO,
                             ver.VERSION,
                             self.settings.get("github_token") or None)
        self._pending_update = info
        try:
            self.page_integrations.show_update_result(info)
        except Exception:
            pass
        if silent:
            self.statusBar().clearMessage()
            if info.get("ok") and info.get("has_update"):
                self._info(
                    f"AI 工作台有新版本 {info.get('tag')}（当前 {ver.VERSION}）。\n\n"
                    f"发布页：{info.get('html_url')}\n（可以到「关于」页看更新说明）",
                    "发现新版本", 15000)
            return
        self._open_page("integrations")
        self.nav.select("integrations")
        self.statusBar().showMessage(
            ("有更新：" + str(info.get("tag"))) if info.get("has_update")
            else ("已是最新" if info.get("ok") else "检查更新失败（见集成页说明）"), 6000)

    def _info(self, text, title="提示", ms=5000):
        """统一的信息提示：正常运行时是弹窗，自动化测试时只写状态栏（不阻塞）。"""
        if _NO_MODAL:
            self.statusBar().showMessage(f"{title}：{text}", ms)
            return
        QMessageBox.information(self, title, text)

    # ================= 设置 =================
    def _on_settings_saved(self, s):
        self.settings.update(s)
        self.settings["prompt_version"] = config.PROMPT_VERSION
        self.workspace.save_settings(self.settings)
        themes.set_font_scale(self.settings.get("font_scale", 100))
        self.client.set_keys(self.settings.get("api_keys", {}))
        self.client.policy = self.settings.get("model_policy", "strict")
        self.runner.keys = dict(self.settings.get("api_keys", {}))
        self.speaker.engine = self.settings.get("tts_engine", "sapi")
        self.speaker.edge_voice = self.settings.get("tts_voice") or tts_mod.DEFAULT_EDGE_VOICE
        self.speaker.rate = int(self.settings.get("tts_rate", 0) or 0)
        self.mcp.reload()
        self.bg_client.set_keys(self.settings.get("api_keys", {}))
        # 托盘 / 热键设置变更后立刻重建（先把旧的注册释放掉，再按新配置注册）
        for hk in getattr(self, "hotkeys", []) or []:
            try:
                hk.stop()
            except Exception:
                pass
        self.hotkeys = []
        if self.tray is not None:
            try:
                self.tray.hide()
                self.tray = None
            except Exception:
                pass
        self._setup_tray()
        self._setup_hotkey()
        self._apply_theme()
        self._refresh_all()
        self._info("设置已保存并立即生效。", "已保存")

    def _on_memory_saved(self):
        self._refresh_chips()
        self.statusBar().showMessage("记忆已更新，下一轮对话就会生效。", 5000)

    def _probe_providers(self):
        self.page_settings.probe_view.setHtml("正在逐个端点体检，请稍候…")
        QApplication.processEvents()
        results = self.client.probe_all(timeout=8)
        self.page_settings.show_probe(results)

    # ================= 文件理解 =================
    def _web_search(self, query):
        return self.client.web_search(query)

    def _vision(self, path, prompt):
        return self.client.vision_extract(path, prompt)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要交给 AI 阅读的文件", "",
            "支持的文件 (*.docx *.doc *.pdf *.xlsx *.xls *.pptx *.ppt *.txt *.md "
            "*.csv *.json *.log *.html *.py *.wav *.mp3 *.m4a *.png *.jpg *.jpeg *.bmp *.webp);;"
            "所有文件 (*.*)")
        if path:
            self._attach_file(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        handled = False
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.isfile(p):
                self._attach_file(p)
                handled = True
        if handled:
            event.acceptProposedAction()

    def _attach_file(self, path):
        name = os.path.basename(path)
        kind = docread.file_kind(path)
        self.statusBar().showMessage(f"正在解析：{name} …")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            if docread.is_image(path):
                text = self.client.vision_extract(
                    path, "请识别这张图片：逐字提取其中的全部文字（尽量保留排版），"
                          "并简要描述画面主要内容。用中文回答。")
                head = f"【图片：{name}（{kind}）】"
            elif docread.is_supported(path):
                text = docread.read_document(path)
                head = f"【文件：{name}（{kind}）】"
            else:
                ext = os.path.splitext(name)[1] or "未知"
                text = (f"这个类型（{ext}）暂时无法直接解析。文件路径：{path}")
                head = f"【文件：{name}】"
        except Exception as e:
            text = f"解析失败：{e}　文件路径：{path}"
            head = f"【文件：{name}】"
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()

        self.attachments.append({
            "name": name, "kind": kind, "path": path,
            "text": f"{head}\n{text}",
            "chars": len(text or ""),
        })
        self._refresh_attach_bar()
        self._open_page("chat")
        self.input.setFocus()

    def _refresh_attach_bar(self):
        """把附件画成小卡片；没有附件时整条隐藏。"""
        lay = self.attach_bar_layout
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        t = themes.tokens()
        for i, a in enumerate(self.attachments):
            chip = QFrame()
            chip.setObjectName("AttachChip")
            hv = QHBoxLayout(chip)
            hv.setContentsMargins(8, 4, 4, 4)
            hv.setSpacing(6)
            icon = "🖼" if docread.is_image(a["path"]) else "📄"
            lb = QLabel(f"{icon} {a['name']}　{a['chars']} 字")
            lb.setStyleSheet(f"color:{t['text']};font-size:12px;")
            hv.addWidget(lb)
            x = QPushButton("✕")
            x.setFixedSize(20, 20)
            x.setObjectName("AttachX")
            x.setToolTip("移除这个附件")
            x.clicked.connect(lambda _=False, idx=i: self._remove_attachment(idx))
            hv.addWidget(x)
            lay.addWidget(chip)
        lay.addStretch(1)
        self.attach_bar.setVisible(bool(self.attachments))
        try:
            self._refresh_chips()
            self._on_input_changed()
        except Exception:
            pass
        if self.attachments:
            total = sum(a["chars"] for a in self.attachments)
            self.statusBar().showMessage(
                f"已附 {len(self.attachments)} 个文件（共 {total} 字），发送时会一并交给 AI",
                6000)

    def _remove_attachment(self, idx):
        if 0 <= idx < len(self.attachments):
            self.attachments.pop(idx)
        self._refresh_attach_bar()

    def _pending_attachment_text(self):
        if not self.attachments:
            return ""
        return "\n\n".join(a["text"] for a in self.attachments)

    def _attachment_note(self):
        if not self.attachments:
            return ""
        return "📎 已附：" + "、".join(a["name"] for a in self.attachments)

    # ================= 发送 / Agent 循环 =================
    @staticmethod
    def _api_messages(messages):
        """把会话历史整理成 API 消息。

        注意：**工具结果也必须进上下文**。以前只保留 user/assistant/system，
        工具回传被整段丢掉——下一轮模型就"忘了"上一步查到了什么，只能重来或瞎编。
        （role=tool 需要配套 tool_call_id，很多兼容端点直接 400，
          所以这里用 user 角色 + 明确标识回喂。）
        """
        out = []
        for m in messages or []:
            role = m.get("role")
            content = m.get("content", "")
            if not isinstance(content, str) or not role:
                continue
            if role in ("user", "assistant", "system"):
                extra = m.get("attached") if role == "user" else None
                if extra:
                    content = content + "\n\n" + str(extra)
                out.append({"role": role, "content": content})
            elif role == "tool":
                out.append({"role": "user",
                            "content": "（上一步工具执行的真实结果）\n" + content})
        return out

    def _build_system_prompt(self):
        parts = [self.settings.get("system_prompt", "") or ""]
        try:
            block = self.memory.prompt_block()
            if block:
                parts.append(block)
        except Exception:
            pass
        # 技能清单（只有名字 + 适用场景，正文要模型自己 use_skill 取，省上下文）
        try:
            lines = self.skills.summary_lines()
            if lines:
                parts.append("# 可用技能（对上场景必须先 use_skill 取出步骤再照做）\n"
                             + "\n".join(lines))
        except Exception:
            pass
        # 定时任务清单（让模型知道自己手上有哪些自动化在跑）
        try:
            ts = self.scheduler.list_all()
            if ts:
                rows = [f"- [{t['id']}] {t['name']}（{_sched_mod.describe(t)}，"
                        f"下次 {_sched_mod.next_text(t)}）" for t in ts[:12]]
                parts.append("# 已有的定时任务\n" + "\n".join(rows)
                             + "\n（要改/删就调 cancel_task / schedule_task）")
        except Exception:
            pass
        # 本机真实文件夹位置（桌面/文档/下载…）—— 避免模型把「桌面」当相对路径写进沙箱
        try:
            from .. import folders as _folders
            hint = _folders.hint_text()
            if hint:
                parts.append(
                    "# 本机真实文件夹（重要）\n"
                    "用户说「桌面 / 文档 / 下载 / 图片 / 音乐 / 视频」时，"
                    "直接把这些名字当路径开头即可（程序会自动翻译成真实路径），"
                    "不需要你查。当前对应关系：\n" + hint +
                    "\n例：要放桌面就写 `桌面\\周报.docx`；"
                    "写完后一律把**完整绝对路径**告诉用户。")
        except Exception:
            pass
        # 工作总结：让模型知道这件事有专门的工具
        try:
            from .. import worklog as _wl
            st = _wl.today_stats(self.workspace.path)
            parts.append(
                "# 工作总结（每次干完活都要留痕）\n"
                "系统会在每轮对话结束后**自动**写一条工作总结到 "
                f"`{self.workspace.path}\\worklog\\` 下（不需要你操心）；\n"
                "但当你完成一件**多步的大任务**时，请额外主动调用 "
                "`write_worklog` 记一条更详细的（参数：request 做了什么、"
                "summary 结论、files 产出文件路径、ok 是否成功）。\n"
                f"今天已完成 {st['total']} 件（成功 {st['ok']} 件）。")
        except Exception:
            pass
        try:
            import datetime
            n = datetime.datetime.now()
            wd = "一二三四五六日"[n.weekday()]
            parts.append(
                f"# 六、当前环境\n现在的时间是 {n.strftime('%Y-%m-%d %H:%M:%S')}（星期{wd}）。"
                f"工作区目录：{self.workspace.path}；文件沙箱：{self.workspace.files_dir}。"
                f"\n当前选择模型：{self._model_label_short().replace('模型：', '')}；"
                f"联网搜索：{'开' if self.settings.get('enable_search') else '关'}；"
                f"钉钉推送：{'可用' if self.ding.configured() else '未配置'}；"
                f"语音识别：{'可用' if _asr_ok(self.settings) else '不可用'}。")
        except Exception:
            pass
        return "\n\n".join(p for p in parts if p)

    def _on_plan_changed(self, plan):
        # 计划面板显隐/变高会改变聊天区可视高度，Qt 会把滚动位置重置到顶部
        # （用户反馈："一弹出来对话就自动蹦到最顶端"）。这里先记住位置再还原。
        sb = self.chat_scroll.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 40
        old = sb.value()
        self.plan_panel.set_plan(plan)
        if at_bottom:
            sb.setValue(sb.maximum())
        else:
            sb.setValue(min(old, max(sb.maximum(), 0)))
        if self.conv is not None:
            self.conv["plan"] = plan
            try:
                self.workspace.save_conversation(self.conv)
            except Exception:
                pass

    def _on_model_change(self):
        data = self.model_combo.currentData()
        if data is None or data == "__sep__":
            return
        self.settings["selected_model"] = data
        self.workspace.save_settings(self.settings)
        self._refresh_chips()

    def _send(self):
        if self.running:
            return
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            return
        text = self.input.toPlainText().strip()
        att_text = self._pending_attachment_text()
        note = self._attachment_note()
        if not text and not att_text:
            return
        if not text:
            text = "请阅读我附上的文件，并按我的要求处理。"
        self.input.clear()
        self.client.set_keys(self.settings.get("api_keys", {}))
        self.client.policy = self.settings.get("model_policy", "strict")
        self.client.reset_used()
        self._last_model_label = ""
        msg = {"role": "user", "content": (note + "\n" + text) if note else text}
        if att_text:
            msg["attached"] = att_text
        self.conv.setdefault("messages", []).append(msg)
        self.attachments = []
        self._refresh_attach_bar()
        self.workspace.save_conversation(self.conv)
        self._load_conversations()

        self.live_text = ""
        self.pending_tool = []
        api_messages = ([{"role": "system", "content": self._build_system_prompt()}]
                        + self._api_messages(self.conv["messages"]))
        self.worker = ChatWorker(
            self.client, self.runner, api_messages,
            agent_mode=bool(self.settings.get("agent_mode", True)),
            model_sel=self.settings.get("selected_model", "auto"),
            enable_search=bool(self.settings.get("enable_search")),
            max_iter=24,
            plan=self.conv.get("plan"),
        )
        self.worker.token.connect(self._on_token)
        self.worker.tool_start.connect(self._on_tool_start)
        self.worker.tool_result.connect(self._on_tool_result)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.confirm_requested.connect(self._on_confirm)
        self.worker.plan_changed.connect(self._on_plan_changed)
        self.worker.notice.connect(self._on_notice)
        self.worker.wrapup.connect(self._on_wrapup)
        self.running = True
        self.send_btn.setEnabled(False)
        self.send_btn.setText("生成中…")
        self._open_page("chat")
        self._render()
        self.worker.start()

    def _on_notice(self, text):
        self.statusBar().showMessage(text[:160], 12000)

    def _on_token(self, delta):
        self.live_text += delta
        if not self._render_timer.isActive():
            self._render_timer.start(80)

    def _update_live(self):
        if self._live_card is None:
            self._render_full()
        else:
            shown = strip_tool_markup(self.live_text)
            prose, blocks = markdown_render.render_with_blocks(shown)
            self._live_card.set_content(prose, blocks)
            self._scroll_to_bottom(False)

    def _on_tool_start(self, desc):
        self.statusBar().showMessage("正在执行工具：" + desc[:130])

    def _on_tool_result(self, result):
        self.live_text = ""
        self._live_card = None
        self.pending_tool.append({"role": "tool", "content": result})
        self.statusBar().clearMessage()
        self._refresh_chips()
        self._render()

    def _on_finished(self, new_messages):
        msgs = [dict(m) for m in (new_messages or [])]
        try:
            label = self.client.last_used_label()
        except Exception:
            label = ""
        if label:
            self._last_model_label = label
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "assistant":
                    msgs[i]["_model"] = label
                    break
        self.conv.setdefault("messages", []).extend(msgs)
        self.workspace.save_conversation(self.conv)
        self.live_text = ""
        self.pending_tool = []
        self._finish_run()
        self._maybe_rename()
        if self.settings.get("tts_enabled"):
            last_ai = next((m["content"] for m in reversed(msgs)
                            if m.get("role") == "assistant"), "")
            if last_ai:
                self.speaker.speak(last_ai)

    def _on_error(self, msg):
        self.pending_tool.append({"role": "tool", "content": "出错了：" + str(msg)})
        self._finish_run()

    def _finish_run(self):
        self.running = False
        self.send_btn.setEnabled(bool(self.input.toPlainText().strip())
                                 or bool(self.attachments))
        self.send_btn.setText("发送")
        self.statusBar().clearMessage()
        self._refresh_chips()
        self._load_conversations()
        self._render()

    def _on_confirm(self, cmd):
        # ① 设置里关掉了确认 -> 直接放行（危险命令在 agent 层已被拦截）
        if not self.settings.get("confirm_commands", True):
            self.worker.confirm(True)
            return
        # ② 只读命令不问：查版本、列目录、看状态这类，每问一次都是打扰
        if _is_readonly_command(cmd):
            self.worker.confirm(True)
            return
        # ③ 本次会话已经点过"全部允许"
        if getattr(self, "_confirm_all_session", False):
            self.worker.confirm(True)
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("确认执行操作")
        box.setText("AI 请求在你的电脑上执行以下操作：")
        box.setInformativeText(cmd)
        yes = box.addButton("允许", QMessageBox.ButtonRole.YesRole)
        allb = box.addButton("本次会话全部允许", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("拒绝", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        box.setEscapeButton(QMessageBox.StandardButton.Cancel)
        box.setWindowFlags(box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        box.adjustSize()
        center = self.geometry().center()
        box.move(center.x() - box.width() // 2, center.y() - box.height() // 2)
        box.exec()
        clicked = box.clickedButton()
        if clicked is allb:
            self._confirm_all_session = True
        self.worker.confirm(clicked in (yes, allb))

    # ================= 消息级操作 =================
    def _regenerate(self):
        """把最后一条用户消息之后的内容丢掉，重新生成一次。"""
        if self.running or not self.conv:
            return
        msgs = self.conv.get("messages", [])
        idx = None
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                idx = i
                break
        if idx is None:
            return
        last_user = self._visible_user_text(msgs[idx])
        self.conv["messages"] = msgs[:idx]
        self.workspace.save_conversation(self.conv)
        self.input.setPlainText(last_user)
        self._render()
        QTimer.singleShot(60, self._send)

    @staticmethod
    def _visible_user_text(msg):
        """去掉「📎 已附：…」这行，只留用户真正打的字。"""
        c = str((msg or {}).get("content") or "")
        lines = [ln for ln in c.split("\n") if not ln.startswith("📎 已附：")]
        return "\n".join(lines).strip()

    def _edit_message(self, index):
        """编辑某条用户消息：截断到它之前，把原文放进输入框重发。"""
        if self.running or not self.conv:
            return
        msgs = self.conv.get("messages", [])
        if not (0 <= index < len(msgs)):
            return
        if msgs[index].get("role") != "user":
            return
        text = self._visible_user_text(msgs[index])
        attached = msgs[index].get("attached")
        self.conv["messages"] = msgs[:index]
        self.workspace.save_conversation(self.conv)
        self.input.setPlainText(text)
        # 原附件仍留在对话里会丢，这里提醒一下
        if attached:
            self.statusBar().showMessage(
                "已把这条消息放回输入框（原来附带的文件内容已不在了，需要的话重新拖一次）。",
                7000)
        self._render()
        self.input.setFocus()
        cur = self.input.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        self.input.setTextCursor(cur)

    def _delete_message(self, index):
        if not self.conv:
            return
        msgs = self.conv.get("messages", [])
        if 0 <= index < len(msgs):
            del msgs[index]
            self.workspace.save_conversation(self.conv)
            self._render()

    def _speak_message(self, text):
        self._speak(text)

    # ================= AI 自动重命名 =================
    def _maybe_rename(self):
        conv = self.conv
        if not conv:
            return
        msgs = conv.get("messages", [])
        if len(msgs) < 2:
            return
        first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
        default_title = (first_user[:20] + "…") if len(first_user) > 20 else (first_user or "未命名对话")
        if conv.get("title") not in ("新对话", "未命名对话", default_title, ""):
            return
        first_ai = next((m["content"] for m in msgs if m["role"] == "assistant"), "")
        self.title_worker = TitleWorker(self.client, first_user, first_ai)
        self.title_worker.done.connect(self._on_title)
        self.title_worker.start()

    def _on_title(self, title):
        if not title or not self.conv:
            return
        self.conv["title"] = title
        self.workspace.save_conversation(self.conv)
        self._load_conversations()

    # ================= 渲染 =================
    def _render(self):
        self._render_full()

    def _clear_chat(self):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                if hasattr(w, "stop"):
                    w.stop()
                w.deleteLater()

    def _scroll_to_bottom(self, force=False):
        sb = self.chat_scroll.verticalScrollBar()
        if force or sb.value() >= sb.maximum() - 40:
            sb.setValue(sb.maximum())

    def _render_full(self):
        self._clear_chat()
        self._cards = []
        self._live_card = None
        try:
            self.plan_panel.set_plan((self.conv or {}).get("plan"))
        except Exception:
            pass

        display = list(self.conv.get("messages", [])) if self.conv else []
        for t in self.pending_tool:
            display.append(t)

        if not display and not self.running:
            self._stack_welcome()
            return

        for i, m in enumerate(display):
            footer = ""
            if m.get("role") == "assistant" and m.get("_model"):
                footer = "实际使用模型：" + m["_model"]
            label = tool_label(m.get("content", "")) if m.get("role") == "tool" else None
            card = MessageCard.from_text(m["role"], m["content"], footer_text=footer,
                                        label=label, index=i)
            if m.get("role") in ("assistant", "user", "tool"):
                card.copy_requested.connect(
                    lambda _=False, c=m: self._copy_msg(c))
                card.delete_requested.connect(
                    lambda _=False, k=i: self._delete_message(k))
                card.speak_requested.connect(self._speak_message)
            if m.get("role") == "assistant":
                card.regenerate_requested.connect(self._regenerate)
            if m.get("role") == "user":
                card.edit_requested.connect(
                    lambda _=False, k=i: self._edit_message(k))
            self.chat_layout.addWidget(card)
            self._cards.append(card)

        if self.running and not self.live_text and not self.pending_tool:
            ind = ThinkingIndicator()
            self.chat_layout.addWidget(ind)
            self._cards.append(ind)
        elif strip_tool_markup(self.live_text):
            shown = strip_tool_markup(self.live_text)
            prose, blocks = markdown_render.render_with_blocks(shown)
            card = MessageCard("assistant", prose, blocks, raw_text=shown, actions=False)
            self.chat_layout.addWidget(card)
            self._cards.append(card)
            self._live_card = card

        self.chat_layout.addStretch(1)
        self._scroll_to_bottom(self.running)
        QTimer.singleShot(0, self._refit_cards)

    def _copy_msg(self, m):
        QGuiApplication.clipboard().setText(m.get("content") or "")
        self.statusBar().showMessage("已复制到剪贴板", 2500)

    def _refit_cards(self):
        for c in self._cards:
            if hasattr(c, "refit"):
                c.refit()
        self._scroll_to_bottom(self.running)

    def _stack_welcome(self):
        self._clear_chat()
        self._cards = []
        w = WelcomeView()
        w.quick.connect(self._on_quick)
        self.chat_layout.addWidget(w)
        self.chat_layout.addStretch(1)
        self._cards.append(w)

    def _on_quick(self, prompt):
        self.input.setPlainText(prompt)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.input.setFocus()

    # ================= 其它 =================
    def _export(self):
        if not self.conv:
            return
        path = self.workspace.export_conversation(self.conv)
        self._info(f"对话已导出到：\n{path}", "已导出")

    def _switch_workspace(self):
        dlg = StartupDialog(self, suggest=self.workspace.path)
        from PyQt6.QtWidgets import QDialog
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected:
            self._open_workspace(dlg.selected)

    def closeEvent(self, event):
        try:
            self.speaker.stop()
        except Exception:
            pass
        try:
            self.ding.stop_stream()
        except Exception:
            pass
        try:
            self.mcp.close_all()
        except Exception:
            pass
        try:
            self.voice.cancel()
        except Exception:
            pass
        super().closeEvent(event)


def _asr_ok(settings):
    """当前配置下语音识别是否可用（有任一可用后端的 Key）。"""
    try:
        from .. import asr as asr_mod
        return bool(asr_mod.available_backends((settings or {}).get("api_keys") or {}))
    except Exception:
        return False
