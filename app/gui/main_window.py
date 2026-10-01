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
import threading
import time

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton,
    QScrollArea, QFrame, QLabel, QMessageBox, QApplication,
    QStackedWidget, QFileDialog, QTextEdit, QComboBox, QDialog,
    QSystemTrayIcon, QMenu,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QTextCursor, QGuiApplication

from .. import (config, llm_client, workspace as ws_mod, agent as agent_mod,
                markdown_render, themes, docread, memory as memory_mod,
                trace as trace_mod, mcp_client, tts as tts_mod,
                dingtalk as dt_mod, updater, version as ver,
                skills as skills_mod, scheduler as _sched_mod,
                hotkey as _hotkey_mod, worklog as _worklog_mod,
                assistant as assistant_mod)
from ..deepseek_web import service as _dsw_service
from .chat_worker import ChatWorker
from .startup_dialog import StartupDialog
from .panels import PlanPanel
from .widgets import (MessageCard, ThinkingIndicator, make_icon, tool_label,
                      strip_tool_markup, BODY_WIDTH, ChatInput, file_write_stats,
                      StatusLine, tool_verb)
from .voice_bar import VoiceInput
from .pages import (NavRail, WelcomeView, MemoryPage, TracePage,
                    IntegrationPage, SettingsPage, AboutPage,
                    SkillsPage, TasksPage, AssistantPage, PAGES)

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
    # dws 工作台 CLI（个人授权，能读会话列表/消息）
    _dingtalk_dws_log = pyqtSignal(str)
    _dingtalk_dws_convs = pyqtSignal(list)
    _dingtalk_dws_msgs = pyqtSignal(str, list)
    # 自动更新：下载进度 / 完成(zip 路径) / 失败(原因)
    _update_progress = pyqtSignal(int, int)
    _update_done = pyqtSignal(str)
    _update_fail = pyqtSignal(str)
    # DeepSeek 网页版（v9.14.0）：后台线程 -> 界面
    _dsw_launch_ready = pyqtSignal(dict)
    _dsw_selftest_ready = pyqtSignal(list)
    # GitHub 检测（v9.15.0）
    _gh_status_ready = pyqtSignal(bool, str)

    def __init__(self, workspace: ws_mod.Workspace, settings: dict, parent=None):
        super().__init__(parent)
        self.workspace = workspace
        self.settings = settings
        # GitHub Token 生效（v9.15.0）
        try:
            config.GITHUB_TOKEN = str(settings.get("github_token", "") or "")
        except Exception:
            pass
        themes.set_font_scale(self.settings.get("font_scale", 100))

        self.client = llm_client.LLMClient()
        self.client.policy = self.settings.get("model_policy", "strict")
        self.client.set_keys(self.settings.get("api_keys", {}))
        # 后台无人值守跑的时候用独立客户端，避免和前台对话抢状态
        self.bg_client = llm_client.LLMClient()
        self.bg_client.policy = self.client.policy
        self.bg_client.set_keys(self.settings.get("api_keys", {}))

        # ---- DeepSeek 网页版（v9.14.0）----
        # 单例协调器。遵循红线：**不主动拉起浏览器**，也**不在未使用时启动后台服务**
        # （HTTP 服务只在选中网页版 / 打开设置页 / 点区块按钮时按需启动）。
        self.dsw = _dsw_service.instance()
        try:
            self.client.set_web_gate(self.dsw.auto_ready)
            self.bg_client.set_web_gate(self.dsw.auto_ready)
        except Exception:
            pass
        self._dsw_timer = QTimer(self)
        self._dsw_timer.setInterval(3000)
        self._dsw_timer.timeout.connect(self._poll_deepseek_web)
        self._dsw_launch_ready.connect(self._dsw_on_launch)
        self._gh_status_ready.connect(self._on_gh_status_ready)
        self._dsw_selftest_ready.connect(self._dsw_on_selftest)
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
        self._dsw_selected = False      # 本轮是否使用「DeepSeek 网页版」（状态行用）
        self._pending_update = None
        # 折叠卡片的用户手动展开/收起，用消息对象身份 (id(msg), 角色) 记住，重建时恢复
        self._card_collapsed = {}
        # 重建后要恢复的滚动位置：(是否贴底, 旧值)
        self._pending_scroll = (True, 0)
        # 助理：钉钉消息 -> AI 处理 -> 结果发回钉钉
        self.assistant = assistant_mod.AssistantService(
            self.ding, os.path.join(self.workspace.path, "assistant.json"))
        self._asst_queue = []       # 待处理的消息
        self._asst_worker = None    # 正在处理消息的 worker
        self._asst_cur = None       # 正在处理的那条消息

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
        # 助理：上次开着就自动恢复
        QTimer.singleShot(2500, self._assistant_boot)

        self._setup_tray()
        QTimer.singleShot(600, self._setup_hotkey)

        # 仅在"已选中网页版"或用户开了"启动时自动接入"时才按需启动本机服务；
        # 开了「自动拉起」且当前是网页版模型时，_dsw_boot 会自己把浏览器拉起来（v9.14.2）。
        _sel = self.settings.get("selected_model", "auto")
        if (isinstance(_sel, str) and "deepseek" in _sel and "web" in _sel) or \
                self.settings.get("deepseek_web_auto"):
            QTimer.singleShot(0, self._dsw_boot)

    # ================= DeepSeek 网页版（v9.14.0 / v9.14.2 免手动） =================
    def _dsw_sync_prefs(self):
        """把设置里的偏好同步给服务层（后台无头 / 是否自动拉起）。"""
        try:
            self.dsw.silent = bool(self.settings.get("deepseek_web_silent", True))
        except Exception:
            pass

    def _dsw_auto_on(self):
        return bool(self.settings.get("deepseek_web_auto", True))

    def _dsw_is_web_model(self):
        """当前选中的模型是不是 DeepSeek 网页版。"""
        try:
            sel = str(self.settings.get("selected_model") or "")
        except Exception:
            sel = ""
        return sel in (config.WEB_MODEL_ID, f"{config.WEB_MODEL_ID}@{config.WEB_PROVIDER_NAME}") \
            or sel.startswith(config.WEB_PROVIDER_NAME)

    def _dsw_boot(self):
        """启动时准备通道。

        v9.14.2：开了「自动拉起」且当前就是 DeepSeek 网页版模型时，**程序自己把浏览器拉起来**
        （用户不必再双击 .bat / 自己开 Chrome）；否则只接入已在线网页，不打扰。
        """
        self._dsw_ensure()
        self._dsw_sync_prefs()
        if self._dsw_auto_on() and self._dsw_is_web_model():
            self._dsw_auto_ensure("启动")
        else:
            try:
                self.dsw.connect_if_online()
            except Exception:
                pass
        self._poll_deepseek_web()

    def _dsw_auto_ensure(self, reason="需要时"):
        """后台自动确保网页可用（不在线就拉起浏览器）。绝不阻塞界面。"""
        self._dsw_ensure()
        self._dsw_sync_prefs()
        self.page_settings.set_web_status("not_started", f"正在准备网页（{reason}）…")

        def work():
            try:
                res = self.dsw.ensure_page(auto_launch=True)
            except Exception as exc:
                res = {"ok": False, "detail": f"{exc}"}
            self._dsw_launch_ready.emit(res)

        threading.Thread(target=work, daemon=True, name="awb-dsw-auto").start()

    def _dsw_ensure(self):
        """确保本机 HTTP 服务在跑 + 状态轮询定时器在跑（幂等、无浏览器副作用）。"""
        try:
            if not self.dsw.is_started():
                self.dsw.ensure_started()
            if not self._dsw_timer.isActive():
                self._dsw_timer.start()
        except Exception:
            pass

    def _poll_deepseek_web(self):
        """状态轮询：刷新设置页状态行 + 取走网页层推来的提示。"""
        try:
            st = self.dsw.status()
        except Exception:
            return
        state = st.get("state") or "not_started"
        try:
            self.page_settings.set_web_status(state, st.get("detail") or "")
        except Exception:
            pass
        # v9.14.2：检测到"已打开但没登录"时，自动把窗口挪回屏幕内（否则用户没法扫码），
        # 每进入一次该状态只挪一次，避免反复抢焦点。
        if state == "not_logged_in":
            if not getattr(self, "_dsw_login_shown", False):
                self._dsw_login_shown = True
                if str(st.get("kind") or "") == "not_logged_in":
                    threading.Thread(target=self.dsw.show_window, daemon=True,
                                     name="awb-dsw-showwin").start()
                    self.statusBar().showMessage(
                        "DeepSeek 网页还没登录：已在屏幕上打开网页窗口，"
                        "登录一次后长期免登。", 20000)
        else:
            self._dsw_login_shown = False
        try:
            for note in self.dsw.take_notices():
                self.statusBar().showMessage(str(note)[:160], 12000)
        except Exception:
            pass

    def _dsw_do_show(self):
        """「显示网页窗口」：亮出网页窗口（无头后台模式下会重启成显示窗口模式）。

        切换模式要重启浏览器（登录态在专用 profile 里，不会掉），所以放后台线程做。
        """
        self._dsw_ensure()

        def work():
            ok = False
            try:
                ok = self.dsw.show_window()
            except Exception:
                ok = False
            self._dsw_launch_ready.emit(
                {"ok": True, "detail": "已显示网页窗口" if ok else
                 "暂时没有可显示的网页窗口（先点「启动/打开网页」）"})

        threading.Thread(target=work, daemon=True, name="awb-dsw-show").start()

    def _dsw_do_hide(self):
        """「收起窗口」：收回后台（切到无头模式，桌面零窗口，发送照常）。"""
        self._dsw_ensure()

        def work():
            ok = False
            try:
                ok = self.dsw.hide_window()
            except Exception:
                ok = False
            self._dsw_launch_ready.emit(
                {"ok": True, "detail": "已收起网页窗口" if ok else "暂时没有可收起的网页窗口"})

        threading.Thread(target=work, daemon=True, name="awb-dsw-hide").start()

    def _gh_do_test(self, token=""):
        """「检测连接」：看 GitHub 通不通（优先本机 gh 登录态，其次 Token）。"""
        token = (token or "").strip()
        try:
            self.page_settings.set_gh_status(True, "正在检测…")
        except Exception:
            pass

        def work():
            ok, msg = False, ""
            try:
                from .. import webcap
                detail = []
                if token:
                    g = webcap.github(action="repo", repo="octocat/Hello-World",
                                      token=token)
                    ok_t = g.startswith("[成功]")
                    detail.append("Token " + ("可用" if ok_t else "不可用"))
                    ok = ok or ok_t
                gh_ok, gh_info = webcap._gh_ready()
                if gh_ok:
                    acc = ""
                    for ln in (gh_info or "").split("\n"):
                        if "account" in ln.lower():
                            acc = ln.split(":", 1)[-1].strip()
                    detail.append("本机 gh 已登录" + (f"（{acc}）" if acc else ""))
                    ok = True
                else:
                    detail.append("本机 gh 不可用")
                # 真正读一次你自己的仓库
                g2 = webcap.github(action="repo", repo="lhclhc123/AIWorkbench",
                                   token=token)
                if g2.startswith("[成功]"):
                    ok = True
                    detail.append("已能读取 lhclhc123/AIWorkbench")
                else:
                    detail.append("读仓库失败：" + g2[:80])
                msg = "；".join(detail)
            except Exception as exc:
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            self._gh_status_ready.emit(ok, msg)

        threading.Thread(target=work, daemon=True, name="awb-gh-test").start()

    def _on_gh_status_ready(self, ok, msg):
        try:
            self.page_settings.set_gh_status(ok, msg)
        except Exception:
            pass

    def _dsw_do_launch(self):
        """「启动/打开网页」：拉起（或复用）Chrome 并接入网页。"""
        self._dsw_ensure()
        self.page_settings.set_web_status("not_started", "正在启动网页…")

        def work():
            try:
                res = self.dsw.launch()
            except Exception as exc:
                res = {"ok": False, "detail": f"{exc}"}
            self._dsw_launch_ready.emit(res)

        threading.Thread(target=work, daemon=True, name="awb-dsw-launch").start()

    def _dsw_on_launch(self, res):
        detail = (res or {}).get("detail") or ""
        if (res or {}).get("ok"):
            self.statusBar().showMessage("DeepSeek 网页：" + detail[:150], 12000)
        else:
            self._info(detail or "启动网页失败", "DeepSeek 网页版")
        self._poll_deepseek_web()

    def _dsw_do_selftest(self):
        """「连通性自检」：后台跑三项自检，结果回到设置页。"""
        self._dsw_ensure()
        self.page_settings.set_web_status("not_started", "正在自检…")

        def work():
            try:
                results = self.dsw.selftest()
            except Exception as exc:
                results = [{"name": "自检", "ok": False, "detail": f"{exc}"}]
            self._dsw_selftest_ready.emit(results)

        threading.Thread(target=work, daemon=True, name="awb-dsw-selftest").start()

    def _dsw_on_selftest(self, results):
        try:
            self.page_settings.show_web_selftest(results)
        except Exception:
            pass
        self._poll_deepseek_web()

    def _dsw_do_retry(self):
        """「重试」：重连 CDP / 重载网页（**不重发**历史提问）。"""
        self._dsw_ensure()
        self.page_settings.set_web_status("not_started", "正在重试…")

        def work():
            try:
                self.dsw.reset()
            except Exception:
                pass
            try:
                res = self.dsw.launch()
            except Exception as exc:
                res = {"ok": False, "detail": f"{exc}"}
            self._dsw_launch_ready.emit(res)

        threading.Thread(target=work, daemon=True, name="awb-dsw-retry").start()

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
        # 真正退出：收拾 DeepSeek 网页通道（停本机服务 / 断开 CDP / 关掉我们拉起的浏览器）
        try:
            self._dsw_timer.stop()
        except Exception:
            pass
        try:
            self.dsw.shutdown()
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
        self.chat_scroll.setStyleSheet(
            f"background:{t['chat_bg']};border-radius:{RADIUS_CARD}px;")
        self._style_input_area()
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

        self.page_assistant = AssistantPage()
        self.stack.addWidget(self.page_assistant)
        self.page_assistant.toggle.connect(self._assistant_toggle)
        self.page_assistant.cfg_saved.connect(self._assistant_save_cfg)
        self.page_assistant.process_one.connect(self._assistant_process_one)
        self.page_assistant.process_all.connect(self._assistant_process_all)
        self.page_assistant.resend.connect(self._assistant_resend)
        self.page_assistant.cleared.connect(lambda: self._asst_queue.clear())
        self.assistant.incoming.connect(self._assistant_on_incoming)
        self.assistant.log.connect(lambda s: self.page_assistant.log(str(s)))
        self.assistant.state.connect(self.page_assistant.set_running)

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
        self.page_integrations.dingtalk_dws_login.connect(self._dingtalk_dws_login)
        self.page_integrations.dingtalk_dws_conversations.connect(self._dingtalk_dws_conversations)
        self.page_integrations.dingtalk_dws_messages.connect(self._dingtalk_dws_messages)
        self.page_integrations.dingtalk_dws_send.connect(self._dingtalk_dws_send)
        self._dingtalk_dws_log.connect(self.page_integrations.show_dws_login_log)
        self._dingtalk_dws_convs.connect(self.page_integrations.show_dws_conversations)
        self._dingtalk_dws_msgs.connect(self.page_integrations.show_dws_messages)
        self.page_integrations.update_check.connect(lambda: self._do_update_check(False))
        self.page_integrations.update_saved.connect(self._save_update_settings)
        self.page_integrations.update_apply.connect(self._update_apply)
        self._update_progress.connect(self._update_on_progress)
        self._update_done.connect(self._update_on_done)
        self._update_fail.connect(self._update_on_fail)

        self.page_settings = SettingsPage()
        self.stack.addWidget(self.page_settings)
        self.page_settings.saved.connect(self._on_settings_saved)
        self.page_settings.probe_requested.connect(self._probe_providers)
        self.page_settings.voice_test.connect(self._tts_test)
        # DeepSeek 网页版区块（v9.14.0）
        self.page_settings.web_launch.connect(self._dsw_do_launch)
        self.page_settings.web_selftest.connect(self._dsw_do_selftest)
        self.page_settings.web_retry.connect(self._dsw_do_retry)
        self.page_settings.web_show.connect(self._dsw_do_show)
        self.page_settings.web_hide.connect(self._dsw_do_hide)
        # GitHub 区块（v9.15.0）
        self.page_settings.gh_test.connect(self._gh_do_test)

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

        # ---------- 顶栏：只留极简会话标题 + 会话列表开关 ----------
        # （模型下拉 / 联网 / Agent / 主题 / 状态 chip 全部从这里移除，
        #   模型选择器下移到输入框右下角；联网与 Agent 改由「设置」页配置。）
        top = QFrame()
        top.setObjectName("TopBar")
        tb = QHBoxLayout(top)
        tb.setContentsMargins(12, 6, 12, 6)
        tb.setSpacing(8)
        self.page_title = QLabel("💬 对话")
        self.page_title.setObjectName("PageTitle")
        tb.addWidget(self.page_title)
        tb.addStretch(1)
        self.list_btn = QPushButton("☰ 列表")
        self.list_btn.setObjectName("cardBtn")
        self.list_btn.setFixedHeight(28)
        self.list_btn.setCheckable(True)
        self.list_btn.setChecked(True)
        self.list_btn.setToolTip("显示 / 隐藏左侧对话列表")
        self.list_btn.clicked.connect(self._toggle_conv_list)
        tb.addWidget(self.list_btn)
        v.addWidget(top)

        # 内部状态标签（实际模型 / 联网·Agent 状态 / 上下文占用）：
        # 顶部不再展示这些 chip，但 _refresh_chips() 与回归测试仍读其文本，
        # 因此保留为「不可见」的内部状态标签，不占用任何界面空间。
        self.chip_model = QLabel("")
        self.chip_model.setObjectName("Chip")
        self.chip_model.setToolTip("本次回复真实使用的模型")
        self.chip_flags = QLabel("")
        self.chip_flags.setObjectName("Chip")
        self.chip_ctx = QLabel("")
        self.chip_ctx.setObjectName("Chip")
        for _c in (self.chip_model, self.chip_flags, self.chip_ctx):
            _c.setParent(page)
            _c.setVisible(False)

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
        self.chat_layout.setSpacing(10)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_scroll.setWidget(self.chat_container)
        right.addWidget(self.chat_scroll, 1)

        self.plan_panel = PlanPanel()
        right.addWidget(self.plan_panel)

        # 语音输入条：录音逻辑仍由 VoiceInput 承载（波形/静音判定/识别都在里面），
        # 但界面入口改成输入卡片右下角那个紧凑的麦克风按钮，这里整体隐藏。
        self.voice = VoiceInput(
            keys_provider=lambda: self.settings.get("api_keys", {}),
            settings_provider=lambda: self.settings)
        self.voice.transcribed.connect(self._on_transcribed)
        self.voice.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.voice.failed.connect(self._on_voice_error)
        right.addWidget(self.voice)
        self.voice.setVisible(False)

        # ---------- 输入区：圆角卡片（上=输入框，下=＋ / 模型 / 麦克风 / 发送）----------
        self.input_card = QFrame()
        self.input_card.setObjectName("InputCard")

        # 附件条（拖进来的文件显示成小卡片，不往输入框里灌正文）
        self.attach_bar = QWidget()
        self.attach_bar.setObjectName("AttachBar")
        self.attach_bar_layout = QHBoxLayout(self.attach_bar)
        self.attach_bar_layout.setContentsMargins(4, 2, 4, 0)
        self.attach_bar_layout.setSpacing(6)
        self.attach_bar.setVisible(False)

        # 输入框：Enter 发送 / Shift+Enter 换行（中文输入法组字时不会误发）
        self.input = ChatInput()
        self.input.setPlaceholderText(
            "输入消息，Enter 发送，Shift+Enter 换行　·　"
            "点右下角麦克风可以直接说话　·　文件/图片拖进窗口即可交给 AI")
        self.input.setMinimumHeight(68)
        self.input.setMaximumHeight(180)
        self.input.textChanged.connect(self._on_input_changed)
        self.input.submitted.connect(self._send)

        # 左下角：添加文件（复用现有附件逻辑 _pick_file / _attach_file）
        self.attach_btn = QPushButton("＋")
        self.attach_btn.setObjectName("InputIcon")
        self.attach_btn.setFixedSize(32, 32)
        self.attach_btn.setToolTip("添加文件（Word / PDF / Excel / PPT / 图片 / 文本）")
        self.attach_btn.clicked.connect(self._pick_file)

        # 右下角：模型选择器（紧凑）+ 麦克风 + 发送箭头
        self.model_combo = QComboBox()
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.model_combo.setMaximumWidth(240)
        self.model_combo.setToolTip("选择本次对话使用的模型")
        self.model_combo.currentIndexChanged.connect(self._on_model_change)

        self.mic_btn = QPushButton("🎙")
        self.mic_btn.setObjectName("InputIcon")
        self.mic_btn.setCheckable(True)
        self.mic_btn.setFixedSize(32, 32)
        self.mic_btn.setToolTip("语音输入：点一下开始说话，再点一下结束")
        self.mic_btn.clicked.connect(self._toggle_recording)
        try:
            # 两个麦克风按钮状态保持同步（自动静音结束时也能正确复位）
            self.voice.mic.toggled.connect(self.mic_btn.setChecked)
        except Exception:
            pass

        self.send_btn = QPushButton("➤")
        self.send_btn.setObjectName("InputIconPrimary")
        self.send_btn.setFixedSize(36, 32)
        self.send_btn.setToolTip("发送（Enter）")
        # 同一个按钮两种身份：空闲=发送，生成中=停止（见 _on_send_clicked）
        self.send_btn.clicked.connect(self._on_send_clicked)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(self.attach_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.model_combo)
        bottom.addWidget(self.mic_btn)
        bottom.addWidget(self.send_btn)

        card_v = QVBoxLayout(self.input_card)
        card_v.setContentsMargins(8, 8, 8, 8)
        card_v.setSpacing(6)
        card_v.addWidget(self.attach_bar)
        card_v.addWidget(self.input)
        card_v.addLayout(bottom)

        # 状态行「模型现在正在干什么」：放在输入卡片正上方（仿 WorkBuddy）。
        # 由 ChatWorker.phase 信号驱动：正在调用模型 / 正在执行 run_python /
        # 正在生成回复 / 正在联网搜索 / 正在运行验证。
        self.state_line = StatusLine()
        right.addWidget(self.state_line)
        right.addWidget(self.input_card)

        body.addLayout(right, 1)
        v.addLayout(body, 1)

        self.send_btn.setEnabled(False)
        self._style_input_area()
        self._render_timer = QTimer()
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._update_live)
        return page

    def _style_input_area(self):
        """按当前主题给输入卡片与图标按钮上色（跟随浅色 / 深色切换）。"""
        t = themes.tokens()
        try:
            self.input_card.setStyleSheet(
                f"QFrame#InputCard{{background:{t['input_bg']};"
                f"border:1px solid {t['input_border']};border-radius:{RADIUS_CARD}px;}}")
            self.input.setStyleSheet(
                "background:transparent;border:none;padding:2px 6px;color:"
                f"{t['text']};font-size:{themes.fs(15)};")
            self.model_combo.setStyleSheet(
                f"QComboBox{{background:{t['btn_bg']};color:{t['btn_text']};"
                f"border:1px solid {t['btn_border']};border-radius:8px;"
                "padding:3px 8px;font-size:12px;min-width:0;}}"
                f"QComboBox:hover{{border:1px solid {t['accent']};}}")
            for b in (self.attach_btn, self.mic_btn):
                b.setStyleSheet(
                    f"QPushButton{{background:{t['btn_bg']};color:{t['btn_text']};"
                    f"border:1px solid {t['btn_border']};border-radius:16px;"
                    "font-size:15px;padding:0px;}"
                    f"QPushButton:hover{{background:{t['btn_hover_bg']};"
                    f"border:1px solid {t['accent']};}}"
                    f"QPushButton:checked{{background:{t['danger']};color:#ffffff;"
                    f"border:1px solid {t['danger']};}}")
            self.send_btn.setStyleSheet(
                f"QPushButton{{background:{t['accent']};color:#ffffff;border:none;"
                "border-radius:16px;font-size:16px;padding:0px;}"
                f"QPushButton:hover{{background:{t['accent_hover']};}}"
                f"QPushButton:disabled{{background:{t['chip_bg']};"
                f"color:{t['text_muted']};}}")
        except Exception:
            pass

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

    def _update_page_title(self):
        """顶栏只显示当前会话标题（极简）。"""
        title = (self.conv or {}).get("title") or ""
        title = str(title).strip() or "新对话"
        if len(title) > 24:
            title = title[:24] + "…"
        try:
            self.page_title.setText("💬 " + title)
        except Exception:
            pass

    # ================= 页面切换 =================
    def _open_page(self, key):
        # 索引跟着 PAGES 顺序走，避免以后加页面忘记改映射
        keys = [k for k, _i, _n in PAGES]
        idx = keys.index(key) if key in keys else 0
        self.stack.setCurrentIndex(idx)
        if key == "assistant":
            self.page_assistant.load_cfg(self.assistant.cfg)
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
            self._dsw_ensure()          # 打开设置页才按需启动本机服务（无浏览器副作用）
            self._poll_deepseek_web()
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
                                model=data.get("model") or "",
                                extra_notes=data.get("notes") or "")
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

        # 4) 硬保障：用户明确要求「发钉钉」，但这轮模型根本没发 -> 程序自动补发
        #    （弱模型经常只道歉/只贴文字，这一步保证"要的东西真的到钉钉"）
        if ui and src == "对话" and self._wants_dingtalk(req) and not self._ding_sent(tools):
            self._auto_dingtalk_send(req, ans, files)

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
                           "download_file", "screenshot", "build_exe") \
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
            # 判据与前台对话一致（agent.honest_ok），别再各写一套
            _ok, _notes = agent_mod.honest_ok(prompt, used, files, answer,
                                              [answer] if str(
                                                  answer or "").startswith("[错误]") else [])
            self._wrapup({
                "user": (prompt or "")[:1000],
                "answer": (answer or "")[:2000],
                "tools": list(dict.fromkeys(used or [])),
                "files": files,
                "ok": _ok,
                "notes": _notes,
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
        # 聊天页不再放「联网 / Agent」开关，改由设置页配置且默认开启。
        # 老工作区里存的是旧默认（enable_search=False），这里做一次性迁移打开；
        # 之后一律以用户在设置页的选择为准（用 _chat_defaults_v2 标记只迁一次）。
        if not self.settings.get("_chat_defaults_v2"):
            self.settings["enable_search"] = True
            self.settings["agent_mode"] = True
            self.settings["_chat_defaults_v2"] = True
            try:
                self.workspace.save_settings(self.settings)
            except Exception:
                pass
        # 语音静音阈值一次性迁移（用户 2026-09-30 反馈"一停顿就停了"）。
        # 老工作区里存的还是更早的旧默认（实测用户工作区是 1.6 秒），
        # 只要小于 5 秒就抬到 10 秒；之后用户自己在设置页改的值不再覆盖。
        if not self.settings.get("_voice_silence_v2"):
            try:
                if float(self.settings.get("voice_silence", 10.0) or 10.0) < 5.0:
                    self.settings["voice_silence"] = 10.0
            except Exception:
                self.settings["voice_silence"] = 10.0
            self.settings["_voice_silence_v2"] = True
            try:
                self.workspace.save_settings(self.settings)
            except Exception:
                pass
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
        self._card_collapsed = {}      # 换会话：折叠记录随之清空
        self._render()
        self._update_page_title()
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
            self._card_collapsed = {}  # 换会话：折叠记录随之清空
            self._render()
            self._update_page_title()

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
        """把设置里的模型选择同步到输入区下拉框。

        「联网搜索 / Agent 模式」已从聊天页移除，改由设置页统一配置，
        这里不再同步 QCheckBox（它们已不存在）。
        """
        idx = self.model_combo.findData(self.settings.get("selected_model", "auto"))
        if idx >= 0:
            self.model_combo.blockSignals(True)
            self.model_combo.setCurrentIndex(idx)
            self.model_combo.blockSignals(False)
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
        bits.append("联网✓" if self.settings.get("enable_search", True) else "联网—")
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

        # ---- dws 工作台 CLI（个人授权，能读会话列表/消息，群聊+单聊）----
        if act in ("dws_status", "dws状态"):
            st = self.ding.dws_auth_status()
            if st.get("authenticated"):
                return f"dws 已登录：{st.get('user_name')} @ {st.get('corp_name')}"
            return "dws 未登录（可走「集成 → 钉钉 → 用 dws 登录钉钉」授权）。"

        if act in ("conversations", "会话列表", "list_conversations", "我的会话"):
            if not self.ding.dws_available():
                return ("没找到 dws（钉钉工作台 CLI）。它随 WorkBuddy 自带，"
                        "或 npm i -g dingtalk-workspace-cli。")
            st = self.ding.dws_auth_status()
            if not st.get("authenticated"):
                return ("dws 还没登录。请到「集成 → 钉钉」点「用 dws 登录钉钉」完成授权，"
                        "之后就能列出并读取你的会话列表（群聊+单聊）。")
            try:
                convs = self.ding.dws_list_conversations(int(args.get("limit") or 20))
            except Exception as e:
                return f"[错误] 读会话列表失败：{e}"
            if not convs:
                return "没有会话，或登录后缺少 chat 业务权限。"
            return "我的钉钉会话列表：\n" + "\n".join(
                f"· {n}（openConversationId={c}）" for n, c in convs)

        if act in ("messages", "读消息", "read_messages", "消息"):
            cid = str(args.get("open_conversation_id") or args.get("cid") or "").strip()
            if not cid:
                return "[错误] 需要 open_conversation_id（先用 conversations 列出）"
            try:
                msgs = self.ding.dws_read_messages(cid, int(args.get("limit") or 20))
            except Exception as e:
                return f"[错误] 读消息失败：{e}"
            if not msgs:
                return "这个会话没有可读到消息（可能没权限）。"
            return "最近消息：\n" + "\n".join(
                f"[{t}] {s}：{x}" for s, x, t in msgs)

        if act in ("dws_send", "dws_dm", "dws发送", "发消息dws"):
            target = str(args.get("target") or args.get("to") or "").strip()
            text = str(args.get("text") or args.get("content") or "").strip()
            if not target or not text:
                return "[错误] 需要 target（群名/姓名/openConversationId）和 text"
            try:
                self.ding.dws_send_group(target, text)
                return f"[成功] 已发到会话「{target}」"
            except Exception:
                try:
                    self.ding.dws_dm(target, text)
                    return f"[成功] 已作为单聊发给「{target}」"
                except Exception as e:
                    return f"[错误] 发送失败：{e}"

        if act in ("send_file", "dws_send_file", "发文件", "发文件dws", "发送文件"):
            target = str(args.get("target") or args.get("to") or "").strip()
            path = str(args.get("path") or args.get("file") or "").strip()
            if not target or not path:
                return ("[错误] 需要 target（群名/姓名/openConversationId）和 "
                        "path（要发送的本地文件完整路径）")
            if not os.path.isabs(path):
                cand = os.path.join(self.workspace.files_dir, path)
                path = cand if os.path.isfile(cand) else os.path.abspath(path)
            try:
                name = self.ding.dws_send_file(target, path)
                return (f"[成功] 文件「{name}」已直接发到钉钉会话「{target}」，"
                        f"对方在钉钉里点开就能收到。完整路径：{path}")
            except Exception as e:
                return (f"[错误] 发文件失败：{e}\n"
                        f"提示：文件必须真实存在；target 用 conversations 里查到的"
                        f"群名/姓名/openConversationId。")

        if act in ("dws_login", "dws登录", "dws授权"):
            return ("登录需要在「集成 → 钉钉」点「用 dws 登录钉钉」按钮，"
                    "由浏览器完成授权（页面报错就改点「设备码登录」）。"
                    "请到那里操作。")

        return ("[错误] 不认识的 action：" + act +
                "。可用：status / selftest / contacts / send / work_notice / "
                "group_send / group / whoami / conversations / messages / "
                "dws_send / send_file / dws_status")

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

    # ---------------- dws 工作台 CLI（个人授权，能读会话列表/消息） ----------------
    def _dingtalk_dws_login(self, device):
        pi = self.page_integrations
        if not self.ding.dws_available():
            pi.show_dws_login_log("没找到 dws（钉钉工作台 CLI）。装了 WorkBuddy 就自带；"
                                  "或自行 npm i -g dingtalk-workspace-cli。")
            return
        st = self.ding.dws_auth_status()
        if st.get("authenticated"):
            pi.show_dws_login_log(
                f"dws 已经登录过：{st.get('user_name')} @ {st.get('corp_name')}。"
                "可继续用；要换账号请先在系统里退出 dws。")
        try:
            p = self.ding.dws_login_start(
                device=bool(device),
                on_line=lambda line: self._dingtalk_dws_log.emit(line))
        except Exception as e:
            pi.show_dws_login_log(f"发起登录失败：{e}")
            return
        pi.show_dws_login_log("已在后台启动 dws 授权登录，请在浏览器 / 钉钉里完成授权…")
        import threading

        def _watch():
            try:
                p.wait(timeout=600)
            except Exception:
                pass
            try:
                st2 = self.ding.dws_auth_status()
                if st2.get("authenticated"):
                    self._dingtalk_dws_log.emit(
                        f"✅ 登录成功：{st2.get('user_name')} @ {st2.get('corp_name')}")
                else:
                    self._dingtalk_dws_log.emit("登录未完成 / 已超时，可重试。")
            except Exception as e:
                self._dingtalk_dws_log.emit(f"查询登录状态失败：{e}")
        threading.Thread(target=_watch, daemon=True).start()

    def _dingtalk_dws_conversations(self):
        pi = self.page_integrations
        if not self.ding.dws_available():
            pi.show_dws_login_log("没找到 dws，无法读取会话列表。")
            return
        st = self.ding.dws_auth_status()
        if not st.get("authenticated"):
            pi.show_dws_login_log("dws 还没登录，请先点「用 dws 登录钉钉」。")
            return
        import threading

        def worker():
            try:
                convs = self.ding.dws_list_conversations(30)
            except Exception as e:
                self._dingtalk_dws_convs.emit([])
                self._dingtalk_dws_log.emit(f"读会话列表失败：{e}")
                return
            self._dingtalk_dws_convs.emit(convs)
        threading.Thread(target=worker, daemon=True).start()

    def _dingtalk_dws_messages(self, cid):
        if not cid:
            return
        import threading

        def worker():
            try:
                msgs = self.ding.dws_read_messages(cid, 30)
            except Exception as e:
                self._dingtalk_dws_msgs.emit(cid, [])
                self._dingtalk_dws_log.emit(f"读消息失败：{e}")
                return
            self._dingtalk_dws_msgs.emit(cid, msgs)
        threading.Thread(target=worker, daemon=True).start()

    def _dingtalk_dws_send(self, target, content):
        if not target or not content:
            return
        import threading

        def worker():
            try:
                self.ding.dws_send_group(target, content)
                self._dingtalk_dws_log.emit(f"✅ 已发到会话「{target}」")
            except Exception:
                try:
                    self.ding.dws_dm(target, content)
                    self._dingtalk_dws_log.emit(f"✅ 已作为单聊发给「{target}」")
                except Exception as e2:
                    self._dingtalk_dws_log.emit(f"❌ 发送失败：{e2}")
        threading.Thread(target=worker, daemon=True).start()

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

    # ================= 助理（钉钉消息 -> AI -> 发回钉钉） =================
    def _assistant_toggle(self, start):
        if start:
            self._asst_queue.clear()
            self.assistant.start()
        else:
            self.assistant.stop()

    def _assistant_save_cfg(self, cfg):
        self.assistant.apply_cfg(cfg)
        self.statusBar().showMessage("助理设置已保存", 4000)
        self.page_assistant.log(
            "设置已保存：间隔 %s 秒 / 范围 %s / 自动回复 %s"
            % (cfg.get("interval"), cfg.get("scope"),
               "开" if cfg.get("auto_reply") else "关"))

    def _assistant_boot(self):
        """启动时若上次助理是开着的，自动恢复。"""
        try:
            if self.assistant.cfg.get("enabled"):
                self.assistant.start()
            self.page_assistant.load_cfg(self.assistant.cfg)
        except Exception:
            pass

    def _assistant_on_incoming(self, msg):
        """服务收到新消息：显示 + 通知 + 按需排队处理。"""
        mid = self.page_assistant.add_message(msg)
        self.assistant.mark_processed(mid)
        who = msg.get("conv") or msg.get("sender") or "钉钉"
        self.statusBar().showMessage(
            f"🤝 助理收到消息（{who}）：{str(msg.get('text'))[:40]}", 8000)
        if self.assistant.cfg.get("auto_reply"):
            self._asst_queue.append(mid)
            self._asst_pump()
        else:
            self.page_assistant.log("新消息待处理（自动回复关着）：点「处理选中」")

    def _assistant_process_one(self, msgid):
        if not msgid:
            self.page_assistant.log("先在列表里选一条消息")
            return
        self._asst_queue.append(msgid)
        self._asst_pump()

    def _assistant_process_all(self):
        for mid in self.page_assistant.row_msgids():
            m = self.page_assistant.msgs.get(mid) or {}
            if m.get("status") in ("待处理", "失败"):
                self._asst_queue.append(mid)
        self._asst_pump()

    def _asst_pump(self):
        """串行处理队列：一次只跑一条，避免和前台对话抢 runner。"""
        if self._asst_worker is not None and self._asst_worker.isRunning():
            return
        # 前台正在对话时先等等，别两边同时用同一个 runner
        try:
            if self.worker is not None and self.worker.isRunning():
                QTimer.singleShot(3000, self._asst_pump)
                return
        except Exception:
            pass
        if self._asst_queue:
            self._asst_start(self._asst_queue.pop(0))

    def _asst_start(self, msgid):
        msg = self.page_assistant.msgs.get(msgid)
        if not msg:
            return
        self._asst_cur = msg
        self.page_assistant.update_status(msgid, "处理中")
        system = self.settings.get("system_prompt") or config.SYSTEM_PROMPT
        prompt = (
            "【助理模式】这是别人从钉钉发给我的消息，请当作我的助手来处理：\n"
            "· 能直接做完的就做完（写文件、跑脚本、查资料都行，产出物放工作区 files/ 下）；\n"
            "· 做完后用**一段话**把最终结果说清楚——这就是要回给对方的原话，"
            "不要客套、不要步骤流水账，涉及文件时带上完整路径；\n"
            "· 消息里要文件/程序就真的生成，别只说怎么做。\n\n"
            f"对方（{msg.get('conv') or msg.get('sender') or '钉钉'}）说：{msg.get('text')}"
        )
        api_msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": prompt}]
        self.page_assistant.log(f"开始处理：{str(msg.get('text'))[:50]}")
        self._asst_worker = ChatWorker(
            self.bg_client, self.runner, api_msgs, True,
            model_sel=self.settings.get("selected_model") or "auto",
            enable_search=(bool(self.settings.get("enable_search"))
                           and agent_mod.needs_web_search(
                               str(msg.get("text") or prompt))),
            max_iter=12)
        self._asst_worker.finished.connect(lambda out, m=msg: self._asst_finish(m, out))
        self._asst_worker.error.connect(lambda e, m=msg: self._asst_error(m, e))
        self._asst_worker.notice.connect(lambda s: self.page_assistant.log(str(s)))
        self._asst_worker.start()

    def _asst_error(self, msg, err):
        self.page_assistant.update_status(msg.get("msgid"), "失败", f"[处理出错] {err}")
        self._asst_worker = None
        self._asst_cur = None
        self._asst_pump()

    def _asst_finish(self, msg, out_messages):
        try:
            answer = ""
            for m in reversed(out_messages or []):
                if m.get("role") == "assistant" and (m.get("content") or "").strip():
                    answer = m["content"].strip()
                    break
            if not answer:
                answer = "（这轮没有产出可回复的内容）"
            files = [f.get("path") if isinstance(f, dict) else str(f)
                     for f in (getattr(self.runner, "written", None) or [])]
            files = [p for p in files if p and os.path.isfile(p)]
            self.page_assistant.update_status(msg.get("msgid"), "已回复", answer)
            if self.assistant.cfg.get("auto_reply"):
                self._asst_reply(msg, answer, files)
        except Exception as e:
            self.page_assistant.log(f"收尾失败：{e}")
        finally:
            self._asst_worker = None
            self._asst_cur = None
            self._asst_pump()

    def _asst_reply(self, msg, answer, files):
        """把最终结果（+ 产出文件）发回钉钉；后台线程，不卡界面。"""
        import threading
        cid = msg.get("cid") or ""
        target_name = self._ding_self_name()
        text = answer if len(answer) <= 1500 else answer[:1500] + "\n…（内容较长，已截断）"

        def worker():
            try:
                if cid:
                    self.ding.dws_send_to_cid(cid, text)
                    self._asst_log_ui(f"✅ 已把结果发回钉钉（{msg.get('conv') or '会话'}）")
                else:
                    self.ding.dws_dm(target_name, text)
                    self._asst_log_ui("✅ 已把结果作为单聊发给本人")
            except Exception as e:
                self._asst_log_ui(f"❌ 结果发回钉钉失败：{e}")
            for p in files[:5]:
                try:
                    if cid:
                        self.ding.dws_send_file(cid, p)
                    else:
                        self.ding.dws_send_file(target_name, p)
                    self._asst_log_ui(f"📎 已把文件发到钉钉：{os.path.basename(p)}")
                except Exception as e:
                    self._asst_log_ui(f"❌ 文件发送失败（{os.path.basename(p)}）：{e}")
        threading.Thread(target=worker, daemon=True).start()

    def _asst_log_ui(self, line):
        try:
            self.page_assistant.log(line)
            self.statusBar().showMessage(line, 8000)
        except Exception:
            pass

    def _assistant_resend(self, msgid):
        m = self.page_assistant.msgs.get(msgid)
        if not m or not m.get("result"):
            self.page_assistant.log("这条还没有可重发的结果")
            return
        files = [f.get("path") if isinstance(f, dict) else str(f)
                 for f in (getattr(self.runner, "written", None) or [])]
        self._asst_reply(m, m["result"], [p for p in files if p and os.path.isfile(p)])

    def _ding_self_name(self):
        try:
            st = self.ding.dws_auth_status()
            return (st.get("user_name") or "").strip() or "我"
        except Exception:
            return "我"

    # ---------- 硬保障：用户要「发钉钉」但 AI 没真发 -> 程序自动补发 ----------
    def _wants_dingtalk(self, text):
        t = str(text or "")
        if not any(k in t for k in ("钉钉", "dingtalk", "dws")):
            return False
        return any(k in t for k in ("发我", "发给我", "发给", "发到", "发过去",
                                    "推送", "传给我", "传我", "发一份", "发个"))

    def _ding_sent(self, tools):
        return any(str(t) in ("dingtalk", "dingtalk_push", "send_file",
                              "dws_send", "dws_send_file") for t in (tools or []))

    def _auto_dingtalk_send(self, req, ans, files):
        """AI 没发钉钉时的兜底：有文件就发文件，没文件就把结论原话发过去。"""
        import threading
        real_files = []
        for f in (files or []):
            p = f.get("path") if isinstance(f, dict) else str(f)
            if p and os.path.isfile(p):
                real_files.append(p)
        target_name = self._ding_self_name()
        self_cid = self._ding_self_cid()

        def worker():
            sent_any = False
            target = self_cid or target_name
            for p in real_files[:5]:
                try:
                    self.ding.dws_send_file(target, p)
                    self._asst_log_ui(f"📎 自动补发文件到钉钉：{os.path.basename(p)}")
                    sent_any = True
                except Exception as e:
                    self._asst_log_ui(f"自动补发文件失败：{e}")
            if not real_files:
                try:
                    txt = (ans or "").strip() or "（这轮没有可回复的内容）"
                    if len(txt) > 1500:
                        txt = txt[:1500] + "…（已截断）"
                    if self_cid:
                        self.ding.dws_send_to_cid(self_cid, txt)
                    else:
                        self.ding.dws_dm(target_name, txt)
                    self._asst_log_ui("📨 自动补发结论到钉钉")
                    sent_any = True
                except Exception as e:
                    self._asst_log_ui(f"自动补发结论失败：{e}")
            if sent_any:
                self.wrapup_notice.emit(
                    "📨 已自动补发到钉钉（你要求发钉钉、模型没发，程序兜底补发）")
        threading.Thread(target=worker, daemon=True).start()

    def _ding_self_cid(self):
        """「自己发给自己」那个会话的 cid（发文件/回消息都用它最稳）。"""
        try:
            return self.assistant._self_cid() or ""
        except Exception:
            return ""

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
                if _NO_MODAL:
                    self.statusBar().showMessage(
                        f"发现新版本 {info.get('tag')}（当前 {ver.VERSION}）", 15000)
                    return
                r = QMessageBox.question(
                    self, "发现新版本",
                    f"AI 工作台有新版本 {info.get('tag')}（当前 {ver.VERSION}）。\n\n"
                    f"要现在自动下载并安装吗？\n"
                    f"（下载完成后本程序会退出、自动替换文件并重新启动）",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.Yes:
                    self._update_apply()
            return
        self._open_page("integrations")
        self.nav.select("integrations")
        self.statusBar().showMessage(
            ("有更新：" + str(info.get("tag"))) if info.get("has_update")
            else ("已是最新" if info.get("ok") else "检查更新失败（见集成页说明）"), 6000)

    # ---------- 自动更新：下载 -> 解压 -> 换文件 -> 重启 ----------
    def _update_apply(self):
        """下载新版本压缩包并自动应用。UI 线程只管弹进度，下载在后台线程。"""
        info = getattr(self, "_pending_update", None)
        if not (info and info.get("ok") and info.get("has_update")):
            self._info("当前没有待安装的更新。先点「立即检查更新」看看。", "自动更新")
            return
        asset = updater.pick_asset(info, prefer=(".zip", ".exe"))
        if not asset:
            self._info(f"这个 Release 没有附件，请到发布页手动下载：\n{info.get('html_url')}",
                       "自动更新")
            return
        import tempfile as _tf
        dest = os.path.join(_tf.gettempdir(), "AIWorkbench_update_" + asset["name"])
        from PyQt6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(f"正在下载 {asset['name']} …", "", 0, 100, self)
        dlg.setWindowTitle("自动更新")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setCancelButton(None)
        self._upd_dlg = dlg
        mb = asset.get("size", 0) / 1048576
        if mb:
            dlg.setLabelText(f"正在下载 {asset['name']}（约 {mb:.1f} MB）…")
        import threading

        def worker():
            try:
                updater.download(asset["url"], dest,
                                 progress=lambda d, t: self._update_progress.emit(d, t))
                self._update_done.emit(dest)
            except Exception as e:
                self._update_fail.emit(f"{type(e).__name__}: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def _update_on_progress(self, done, total):
        dlg = getattr(self, "_upd_dlg", None)
        if not dlg:
            return
        if total > 0:
            dlg.setValue(min(99, int(done * 100 / total)))
            dlg.setLabelText(f"正在下载更新… {done / 1048576:.1f} / {total / 1048576:.1f} MB")

    def _update_on_fail(self, err):
        dlg = getattr(self, "_upd_dlg", None)
        if dlg:
            dlg.close()
            self._upd_dlg = None
        self._info(f"更新下载失败：{err}\n\n可以到发布页手动下载安装：\n"
                   f"https://github.com/{ver.GITHUB_REPO}/releases", "自动更新")

    def _update_on_done(self, zip_path):
        dlg = getattr(self, "_upd_dlg", None)
        if dlg:
            dlg.setValue(100)
            dlg.close()
            self._upd_dlg = None
        try:
            self._finish_update(zip_path)
        except Exception as e:
            self._info(f"应用更新失败：{e}\n\n请到发布页手动下载：\n"
                       f"https://github.com/{ver.GITHUB_REPO}/releases", "自动更新")

    def _finish_update(self, zip_path):
        """解压新包，用 PowerShell 在本程序退出后覆盖安装目录并重启。

        用 -EncodedCommand（UTF-16LE base64）传脚本，彻底避开中文路径在
        bat/ps1 文件里的编码坑。"""
        import sys as _sys
        import zipfile as _zf
        import tempfile as _tf
        import base64 as _b64
        app_dir = os.path.dirname(_sys.executable) if getattr(_sys, "frozen", False) \
            else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        tmp_root = os.path.join(_tf.gettempdir(), f"AIWorkbench_upd_{int(time.time())}")
        with _zf.ZipFile(zip_path) as z:
            z.extractall(tmp_root)
        # 在解压结果里找「直接装着 AIWorkbench.exe 的那一层」
        src = None
        for root, _dirs, files in os.walk(tmp_root):
            if any(f.lower() in ("aiworkbench.exe", "aiworkbench") for f in files):
                src = root
                break
        if not src:
            raise RuntimeError("压缩包里没找到 AIWorkbench.exe，无法自动安装")
        src = src.replace("'", "''")
        dst = app_dir.replace("'", "''")
        exe = os.path.join(app_dir, "AIWorkbench.exe").replace("'", "''")
        ps = (
            "$ErrorActionPreference = 'Stop'\n"
            "Start-Sleep -Seconds 2\n"
            "$ok = $false\n"
            f"for ($i = 0; $i -lt 40; $i++) {{\n"
            f"  try {{ Copy-Item -Path '{src}\\*' -Destination '{dst}' -Recurse -Force\n"
            "      $ok = $true; break }\n"
            "  catch { Start-Sleep -Milliseconds 500 }\n"
            "}\n"
            "if ($ok) {\n"
            "  Start-Sleep -Milliseconds 800\n"
            f"  Start-Process -FilePath '{exe}'\n"
            "}\n"
            f"Remove-Item -LiteralPath '{tmp_root}' -Recurse -Force -ErrorAction SilentlyContinue\n"
            f"Remove-Item -LiteralPath '{zip_path}' -Force -ErrorAction SilentlyContinue\n"
        )
        encoded = _b64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        from .. import winproc
        winproc.popen(["powershell", "-NoProfile", "-EncodedCommand", encoded],
                      creationflags=0x08000000)
        if _NO_MODAL:
            self.statusBar().showMessage("更新包已就绪，退出后将自动替换并重启", 8000)
            QApplication.quit()
            return
        QMessageBox.information(
            self, "自动更新",
            "更新包已下载并准备好。\n点「确定」后本程序会关闭，"
            "几秒后自动替换文件并重新启动新版本。")
        QApplication.quit()

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
        # DeepSeek 网页版（v9.14.2）：开了"自动拉起"就直接把网页准备好（不在线就自己开浏览器）
        self._dsw_sync_prefs()
        # GitHub（v9.15.0）：把设置里的 Token 生效到运行时
        try:
            config.GITHUB_TOKEN = str(self.settings.get("github_token", "") or "")
        except Exception:
            pass
        if self._dsw_auto_on() and self._dsw_is_web_model():
            self._dsw_auto_ensure("设置已更新")
        elif self.settings.get("deepseek_web_auto"):
            self._dsw_ensure()
            try:
                self.dsw.connect_if_online()
            except Exception:
                pass
            self._poll_deepseek_web()
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
        self._dsw_ensure()      # 让网页版端点的 /models 可被体检到（用户主动操作）
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
                f"联网搜索：{'开' if self.settings.get('enable_search', True) else '关'}；"
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
        # v9.14.2：切到 DeepSeek 网页版模型时，自动把网页准备好（免手动开浏览器）
        if self._dsw_auto_on() and self._dsw_is_web_model():
            self._dsw_auto_ensure("切换到 DeepSeek 网页版")

    def _send(self):
        if self.running:
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
        # 本轮若用「DeepSeek 网页版」：按需启动本机服务（不拉起浏览器）
        _sel = self.settings.get("selected_model", "auto")
        self._dsw_selected = bool(isinstance(_sel, str)
                                  and "deepseek" in _sel and "web" in _sel)
        if self._dsw_selected:
            self._dsw_ensure()
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
            # 联网门控：设置里允许联网 + 这一轮看起来真的需要外部实时信息，
            # 才让端点自动搜。本机/本地任务不再被塞进无关搜索结果
            # （模型需要时仍可自己显式调 web_search 工具）。
            enable_search=(bool(self.settings.get("enable_search", True))
                           and agent_mod.needs_web_search(text)),
            max_iter=28,
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
        self.worker.phase.connect(self._on_phase)
        self.running = True
        # 生成中：右下角按钮变成红色方形「停止」，点一下就能打断模型
        self._set_stop_mode(True)
        # 选中网页版时，状态行显示"正在等待 DeepSeek 网页输出"；否则沿用旧的"调用模型"
        self.state_line.set_phase("web" if self._dsw_selected else "model", "")
        self._open_page("chat")
        # 发新消息必须强制贴底
        self._render(stick=True)
        self.worker.start()

    def _on_send_clicked(self):
        """右下角按钮：空闲=发送；生成中=停止（打断模型）。"""
        if self.running:
            self._interrupt()
        else:
            self._send()

    def _interrupt(self):
        """打断当前生成。"""
        w = self.worker
        if w is None or not self.running:
            return
        try:
            w.abort()
        except Exception:
            pass
        self.state_line.set_phase("stop", "正在收尾")
        self.send_btn.setEnabled(False)
        self.send_btn.setText("…")
        self.statusBar().showMessage("已停止本轮生成，正在收尾…", 4000)

    def _set_stop_mode(self, stop):
        """切换右下角按钮的形态：生成中=红色方形停止键，空闲=蓝色圆形发送键。"""
        t = themes.tokens()
        try:
            if stop:
                self.send_btn.setText("■")
                self.send_btn.setToolTip("停止生成（点一下打断模型）")
                self.send_btn.setEnabled(True)
                self.send_btn.setStyleSheet(
                    f"QPushButton{{background:{t['danger']};color:#ffffff;"
                    "border:none;border-radius:9px;font-size:13px;padding:0px;}"
                    f"QPushButton:hover{{background:{t['danger']};}}")
            else:
                self.send_btn.setText("➤")
                self.send_btn.setToolTip("发送（Enter）")
                self._style_input_area()
        except Exception:
            pass

    def _on_phase(self, kind, detail):
        """模型阶段变化 -> 状态行。"""
        try:
            if kind == "tool":
                detail = tool_verb(detail)
            self.state_line.set_phase(kind, detail or "")
        except Exception:
            pass

    def _on_notice(self, text):
        self.statusBar().showMessage(text[:160], 12000)

    def _on_token(self, delta):
        self.live_text += delta
        if not self._render_timer.isActive():
            self._render_timer.start(80)

    def _update_live(self):
        # ⚠️ 流式刷新时**不能强制贴底**：否则用户往上翻看历史时，会被
        #    每 80ms 一次的强制滚动拽回底部（用户 2026-09-30 反馈
        #    「AI 生成的时候我没办法往上翻」）。
        #    改成"只在自己本来就贴着底时才跟随" —— 用户主动上滑后就不再打扰，
        #    等他滚回底部，_scroll_to_bottom 的贴底判断会自动恢复跟随。
        if self._live_card is None:
            self._render_full()
        else:
            shown = strip_tool_markup(self.live_text)
            prose, blocks = markdown_render.render_with_blocks(shown)
            self._live_card.set_content(prose, blocks)
            # 卡片高度刚变，滚动条最大值要等布局刷新后再取 -> 延后一帧再跟随
            QTimer.singleShot(0, lambda: self._scroll_to_bottom(False))

    def _on_tool_start(self, desc):
        self.statusBar().showMessage("正在执行工具：" + desc[:130])

    def _on_tool_result(self, result):
        # 工具前的「中间话」不再丢弃：先作为折叠的「深度思考」块留在本轮记录里
        # （整轮结束后 chat_worker 也会把它作为 role="thinking" 交回来，pending 只是
        #   本轮的即时展示，_on_finished 会清空 pending，不会重复落进对话记录。）
        _think = strip_tool_markup(self.live_text).strip() if self.live_text else ""
        if _think:
            self.pending_tool.append({"role": "thinking", "content": _think})
        self.live_text = ""
        self._live_card = None
        self.pending_tool.append({"role": "tool", "content": result})
        self.statusBar().clearMessage()
        self._refresh_chips()
        # 工具结果到达也不强制贴底：用户可能正往上翻（同上）。
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
        self._set_stop_mode(False)          # 按钮恢复成圆形发送键
        self.send_btn.setEnabled(bool(self.input.toPlainText().strip())
                                 or bool(self.attachments))
        try:
            self.state_line.set_phase("idle", "")
        except Exception:
            pass
        self.statusBar().clearMessage()
        self._refresh_chips()
        self._load_conversations()
        # 收尾渲染同样不强制贴底：用户若正在上翻看历史，不该被拽走。
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
            m = msgs[index]
            # 清掉这条消息的折叠记录，避免其 dict 被回收后 id 复用、误套到新卡片上
            for _r in ("tool", "thinking"):
                self._card_collapsed.pop(self._card_key(m, index, _r), None)
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
        self._update_page_title()

    # ================= 渲染 =================
    def _render(self, stick=None):
        self._render_full(stick=stick)

    def _clear_chat(self):
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                if hasattr(w, "stop"):
                    w.stop()
                # ★ 先 setParent(None) 立刻脱离父子关系，再 deleteLater。
                #   只调 deleteLater 的话，控件要等事件循环才真正销毁，
                #   在那之前它仍然是 chat_container 的子控件、**仍会按原位绘制** ——
                #   所以重建时会出现「旧卡片 / 欢迎页和新消息叠在一起」的残影
                #   （2026-09-30 截图复现）。
                w.setParent(None)
                w.deleteLater()

    def _scroll_to_bottom(self, force=False):
        sb = self.chat_scroll.verticalScrollBar()
        if force or sb.value() >= sb.maximum() - 40:
            sb.setValue(sb.maximum())

    def _card_key(self, msg, index, role):
        """折叠状态键：与会变动的下标解耦。

        优先用消息 dict 的稳定身份 —— 同一次会话内 `id(m)` 稳定，
        删掉别的消息不会改变它（所以删中间一条不会让展开态"跑"到别的卡上）；
        非 dict 的临时项退回下标兜底。
        """
        if isinstance(msg, dict):
            return ("obj", id(msg), str(role))
        return ("idx", int(index), str(role))

    def _render_full(self, stick=None):
        # 重建前先记住滚动状态：stick=True 强制贴底；否则沿用"当前是否贴底"。
        # 这就是「一发新消息就跳到最上面」的根因修复 —— 重建后延后一帧再恢复。
        sb = self.chat_scroll.verticalScrollBar()
        at_bottom = True if stick is True else (sb.value() >= sb.maximum() - 40)
        old_value = sb.value()

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
            role = m.get("role")
            content = m.get("content", "") or ""
            footer = ""
            if role == "assistant" and m.get("_model"):
                footer = "实际使用模型：" + m["_model"]
            label = tool_label(content) if role == "tool" else None
            # 文件写入卡片：从工具结果文本解析 +N / -N
            stats = file_write_stats(content) if role == "tool" else None
            # 中间过程分级：带结论 / 原因 / 建议 / 报错定位的那几句默认**展开**
            # （左侧加一道强调竖线），"我来帮你看看"这种流水话才折叠。
            # 用户原话："中间过程性的那些话并不是每一句都是要隐藏的，
            #           那些重点的结论那种留着"。
            emphasis = False
            if role == "thinking":
                emphasis = agent_mod.is_key_progress(content)
            # 折叠状态跨重建保持：优先用用户手动设置过的值
            saved = self._card_collapsed.get(self._card_key(m, i, role))
            if saved is None and role == "thinking":
                saved = not emphasis          # 重点默认展开
            card = MessageCard.from_text(role, content, footer_text=footer,
                                        label=label, index=i,
                                        stats=stats, collapsed=saved,
                                        emphasis=emphasis)
            # 分组留白：过程卡片贴紧，正式消息之间留出呼吸感
            card.setContentsMargins(
                0, 2 if role in ("tool", "thinking") else 8, 0, 0)
            if role in ("assistant", "user", "tool"):
                card.copy_requested.connect(lambda _=False, c=m: self._copy_msg(c))
                card.delete_requested.connect(
                    lambda _=False, k=i: self._delete_message(k))
                card.speak_requested.connect(self._speak_message)
            if role == "assistant":
                card.regenerate_requested.connect(self._regenerate)
            if role == "user":
                card.edit_requested.connect(
                    lambda _=False, k=i: self._edit_message(k))
            if role in ("tool", "thinking"):
                card.collapse_changed.connect(
                    lambda collapsed, k=self._card_key(m, i, role):
                        self._card_collapsed.__setitem__(k, collapsed))
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
        # 此刻布局还没最终确定，直接读滚动条最大值不准 —— 延后一帧再恢复位置
        self._pending_scroll = (at_bottom, old_value)
        QTimer.singleShot(0, self._after_render)

    def _copy_msg(self, m):
        QGuiApplication.clipboard().setText(m.get("content") or "")
        self.statusBar().showMessage("已复制到剪贴板", 2500)

    def _after_render(self):
        """布局落定后撑开卡片并按需要恢复滚动位置。

        修的是用户反馈的「一弹出来 / 一发新消息就自动跳到最顶端」：
        重建卡片后先让布局重新计算高度，再贴底或还原旧位置。
        """
        try:
            self.chat_layout.activate()
            self.chat_container.adjustSize()
        except Exception:
            pass
        for c in self._cards:
            if hasattr(c, "refit"):
                c.refit()
        self._restore_scroll()

    def _restore_scroll(self):
        sb = self.chat_scroll.verticalScrollBar()
        at_bottom, old = getattr(self, "_pending_scroll", (True, 0))
        if at_bottom:
            sb.setValue(sb.maximum())
        else:
            sb.setValue(min(old, max(sb.maximum(), 0)))

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
