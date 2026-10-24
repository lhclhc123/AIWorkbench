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
                assistant as assistant_mod,
                taskman as _taskman_mod, team as _team_mod, icons as _icons_mod,
                # ★ v9.18.0（需求 13）：预置团队人设（谁在协作），对话内团队条要用
                team_presets)
from ..deepseek_web import service as _dsw_service
from .chat_worker import ChatWorker
from .startup_dialog import StartupDialog
from .panels import PlanPanel
from .widgets import (MessageCard, ThinkingIndicator, make_icon, tool_label,
                      strip_tool_markup, BODY_WIDTH, ChatInput, file_write_stats,
                      StatusLine, tool_verb, AskUserDialog, ProcessGroup,
                      ReasoningCard)
from .voice_bar import VoiceInput
from .pages import (NavRail, WelcomeView, MemoryPage, TracePage,
                    IntegrationPage, SettingsPage, AboutPage,
                    SkillsPage, TasksPage, AssistantPage, PAGES,
                    BgTasksPage, TeamPage, BrowserPage)
# ★ v9.18.0（需求 13）：对话内「团队」条 —— 用户要的是"在对话里选团队"，
#   不是跳去另一个页面点。控件自包含（不反向 import 本文件），可以独立测试。
from .team_bar import TeamBar

RADIUS_CARD = 12

# ★ v9.20.0 修「点开长对话严重卡顿 / 未响应」：聊天区**分页渲染**。
#   一张 MessageCard 内部有 QTextBrowser + 若干按钮 + 语法高亮，实测单张
#   约 5~17ms（纯文本 5ms / 带代码 17ms）；一次铺 120 条 = 1.7s 主线程全黑。
#   现在首次进对话只铺最近 CHAT_PAGE_SIZE 条，顶部留一个「加载更早」按钮，
#   点一次往前再铺一批。
#   为什么是 30 而不是 40：按实测（120 条混合消息）40 条约 480ms，卡在
#   "0.5 秒没反应"的红线上；30 条约 350ms，留出真机更慢的余量。
CHAT_PAGE_SIZE = 30

# 用户消息里"带了附件"那行标注的前缀。
# ★ v9.18.0（需求 16）：字形从 `app/icons.py` 取，**这里和 _visible_user_text()
#   必须用同一个常量** —— 以前两处各写一遍字面量 emoji，换图标时就会漏掉一处，
#   表现为"编辑消息后附件标注被当成正文带回输入框"。
ATTACH_MARK = _icons_mod.ic("attach") + " 已附："

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


class TeamJob(QThread):
    """在后台线程里跑完整支团队（v9.18.0，需求 13）。

    `TeamRun.run()` 是**同步**的（4 个阶段：总监派单 → 并行建造 → 独立校验 →
    总监汇总），直接在主线程调用会把界面冻住几分钟，所以丢进 QThread。

    过程事件不走这里 —— `TeamRun` 构造时注入的 `on_event` 内部只做
    `self._team_evt.emit(...)`（纯信号，不碰控件），因此从哪个线程抛来都安全。
    这里只负责"跑完了"这一刻，把整个 TeamRun 交回 UI 线程。

    ⚠ 必须持有引用（`MainWindow._team_jobs`），否则 QThread 对象被 GC 掉会
      `QThread: Destroyed while thread is still running` 直接崩。
    """

    done_one = pyqtSignal(str, object)      # (cid, TeamRun)

    def __init__(self, trun, cid="", parent=None):
        super().__init__(parent)
        self.trun = trun
        self.cid = cid

    def run(self):
        try:
            self.trun.run()
        except Exception as e:                       # noqa: BLE001
            # 团队引擎自己会吞掉大部分异常；真的漏出来也要落成错误，
            # 不能只留一个静默死掉的线程（用户会看到"一直在跑"）。
            try:
                self.trun.error = f"{type(e).__name__}: {e}"
                self.trun.status = "failed"
            except Exception:
                pass
        self.done_one.emit(self.cid, self.trun)


class ConvRun:
    """**一个对话自己的**运行时状态（v9.18.0，需求 7）。

    用户原话：「增加多任务、多进程、多对话可以同时进行，每个对话都是独立的，
    可以同时运行，现在就是在一个对话思考的时候点到别的对话会显示思考中，
    然后真正对话中本轮之前的输出内容也可能会消失，修复这个 bug」。

    病根：老实现把这些状态（`running` / `live_text` / `worker` / `pending_tool`）
    直接挂在 `MainWindow` 上 —— **整整一个程序只有一份**。于是在 A 对话思考时切到
    B 对话：B 会显示成"A 还在思考"（读的是同一个 `self.running`），而 A 本轮已经
    产出、但还没写进对话记录的过程/正文，被切走时清掉就再也回不来了。

    现在每个对话一份，互不干扰：切走→状态留在自己那份里；切回来→原样恢复。
    """

    __slots__ = ("cid", "worker", "runner", "running", "live_text", "pending_tool",
                 "live_card", "last_model_label", "dsw_selected",
                 "live_reason", "reason_card", "team_run")

    def __init__(self, cid=""):
        self.cid = cid
        self.worker = None
        # ★ v9.19.0：AgentRunner 也必须**每个对话一份**。
        #   老实现只有 `self.runner` 一个全局实例：A 对话在跑、用户切到 B 再发一条，
        #   B 的 `_make_runner()` 就把这个全局指针覆盖掉了 —— 于是 A 的产物清单
        #   (`runner.written`)、记忆回调全指向 B，落盘落进别人的文件夹。
        self.runner = None
        self.running = False
        self.live_text = ""
        self.pending_tool = []
        self.live_card = None
        self.last_model_label = ""
        self.dsw_selected = False
        # ★ v9.18.0（需求 17）：正在实时滚动的思维链正文，以及那张「◈ 思考中」
        #   卡片的引用。和 live_text / live_card 一样，**每个对话各一份** ——
        #   切到别的对话时思考不会被清掉，切回来接着看。
        self.live_reason = ""
        self.reason_card = None
        # ★ v9.18.0（需求 13）：这一轮如果是团队在干活，这里放那个 TeamRun。
        #   放**每个对话自己**这份里，于是「A 对话的团队在跑、B 对话照常聊天」
        #   互不干扰，`_interrupt()` 也能精确停到当前对话那支队伍。
        self.team_run = None


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
    # 并行任务 / 团队（v9.16.0）：后台线程改了任务状态 -> 通知界面刷新
    _bg_changed = pyqtSignal(str)          # 传任务号（只用于区分，界面整体刷新）
    _team_changed = pyqtSignal(str)
    _bg_notice = pyqtSignal(str)           # 后台 AI/团队完成后的提示
    # ★ v9.18.0（需求 13）：对话内团队的过程事件（后台线程 -> 界面）。
    #   `TeamRun._emit` 是在**成员线程**里被调用的（taskman.submit(..., nested=True)
    #   的线程，或 TeamJob 线程）—— 直接摸控件就是随机崩，必须过一道信号。
    #   payload 是 dict，所以第二个参数用 `object`（pyqtSignal 不支持原生 dict）。
    # ★ v9.19.0：加了第三个参数 cid —— 团队事件必须写回**发起它的那个对话**。
    #   老实现不带 cid，`_on_team_evt` 一律往 `self.conv`（= 正在显示的那个）里写，
    #   于是 A 对话的团队在跑、用户切到 B，A 的「派单/成员完成」提示全糊到了 B 上。
    _team_evt = pyqtSignal(str, object, str)
    # ★ v9.20.0：助理代回"发送结果"从后台线程回 UI 线程要走信号
    #   （跨线程直接动 Qt 控件在 Windows 上迟早炸）
    _im_send_evt = pyqtSignal(str, object, str, bool)
    # ★ v9.20.0：子进程预取回来的结果（后台线程 → UI 线程）
    _im_prefetch_evt = pyqtSignal(str, int, str, str, object)

    # ---------- ★ v9.18.0：把"运行态"按对话分派（需求 7）----------
    # 下面这些名字以前是 MainWindow 的普通实例属性（全局只有一份）。现在改成
    # property，读写都会落到"**当前对话**的那一份 ConvRun"上 —— 于是全文件里
    # 几百处 `self.running` / `self.live_text` / `self.worker` 一行都不用改，
    # 就自动变成了"每个对话各自独立"。
    def _conv_cid(self):
        try:
            return (self.conv or {}).get("id") or ""
        except Exception:
            return ""

    def _run(self, cid=None):
        """取（必要时新建）某个对话的运行时状态。cid 缺省 = 当前对话。"""
        try:
            cid = self._conv_cid() if cid is None else cid
            runs = self.__dict__.setdefault("_runs", {})
            r = runs.get(cid)
            if r is None:
                r = ConvRun(cid)
                runs[cid] = r
            return r
        except Exception:                       # 极端兜底：绝不因为状态机崩掉界面
            if not hasattr(self, "_fallback_run"):
                self._fallback_run = ConvRun("")
            return self._fallback_run

    def _is_current(self, cid):
        """这个回调是不是来自"当前正显示的那个对话"（不是就别去动界面）。"""
        return (cid or "") == self._conv_cid()

    def _any_running(self):
        return any(r.running for r in self._runs.values())

    @property
    def running(self):
        return self._run().running

    @running.setter
    def running(self, value):
        self._run().running = bool(value)

    @property
    def live_text(self):
        return self._run().live_text

    @live_text.setter
    def live_text(self, value):
        self._run().live_text = value or ""

    @property
    def pending_tool(self):
        return self._run().pending_tool

    @pending_tool.setter
    def pending_tool(self, value):
        self._run().pending_tool = list(value or [])

    @property
    def worker(self):
        return self._run().worker

    @worker.setter
    def worker(self, value):
        self._run().worker = value

    @property
    def runner(self):
        """**当前对话自己的** AgentRunner（v9.19.0，按需创建）。

        以前它是 MainWindow 上的一个普通属性 —— 全局只有一份。两个对话同时跑时，
        后发起的那一轮会把指针覆盖掉：`_asst_finish` 收文件、`keys` 热更新、
        产物清单 `runner.written` 全都看到的是**别人**那一轮的东西。
        现在跟着对话走，`_send` 里显式赋一次（绑定 cid），其余地方读到的
        永远是这个对话自己的那一份。
        """
        run = self._run()
        if run.runner is None:
            run.runner = self._make_runner(run.cid)
        return run.runner

    @runner.setter
    def runner(self, value):
        self._run().runner = value

    @property
    def _live_card(self):
        return self._run().live_card

    @_live_card.setter
    def _live_card(self, value):
        self._run().live_card = value

    @property
    def live_reason(self):
        return self._run().live_reason

    @live_reason.setter
    def live_reason(self, value):
        self._run().live_reason = value or ""

    @property
    def _reason_card(self):
        return self._run().reason_card

    @_reason_card.setter
    def _reason_card(self, value):
        self._run().reason_card = value

    @property
    def _last_model_label(self):
        return self._run().last_model_label

    @_last_model_label.setter
    def _last_model_label(self, value):
        self._run().last_model_label = value or ""

    @property
    def _dsw_selected(self):
        return self._run().dsw_selected

    @_dsw_selected.setter
    def _dsw_selected(self, value):
        self._run().dsw_selected = bool(value)

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
        # ---- 并行任务 + 团队（v9.16.0）----
        # 用户要的是"跑慢命令不阻塞模型继续思考 / 多条链路同时执行 / 一支小队独立跑"。
        # 这里建两个管理器，并把它们的变更接到 Qt 信号上 ——
        # ⚠ 回调是在**后台线程**里触发的，绝不能直接去摸控件，必须转成信号
        #    （队列连接）再在 UI 线程刷新，否则就是随机崩。
        self.taskman = _taskman_mod.TaskManager(
            self.workspace.path,
            max_running=int(self.settings.get("bg_max_parallel", 4) or 4))
        self.teamman = _team_mod.TeamManager(self.workspace.path)
        self.taskman.subscribe(self._on_bg_changed)
        self.teamman.subscribe(self._on_team_changed)
        self._bg_changed.connect(self._refresh_bg_pages)
        self._team_changed.connect(self._refresh_team_pages)
        self._bg_notice.connect(self._on_bg_notice)
        # ★ v9.18.0（需求 13）：团队过程事件（后台线程）-> UI 线程
        self._team_evt.connect(self._on_team_evt)
        self._im_send_evt.connect(self._im_sent_back)
        self._im_prefetch_evt.connect(self._asst_prefetch_store)
        # 同时起的团队可能不止一支（每个对话一支）；持有引用防 QThread 被 GC
        self._team_jobs = {}
        self.mcp = mcp_client.MCPManager(self.workspace.path)
        self.speaker = tts_mod.Speaker()
        self.speaker.engine = self.settings.get("tts_engine", "sapi")
        self.speaker.edge_voice = self.settings.get("tts_voice") or tts_mod.DEFAULT_EDGE_VOICE
        self.speaker.rate = int(self.settings.get("tts_rate", 0) or 0)
        self.ding = dt_mod.DingTalkClient(dt_mod.config_from_settings(self.settings))

        self.reminders = []          # [{text, ts, dingtalk, human}]
        self.attachments = []        # 待发送附件 [{name, kind, path, text, chars}]
        self.hotkeys = []            # 已注册的全局热键
        self.tray = None
        self._quit_requested = False
        self._last_bg_tools = []     # 后台任务最近一次真正执行过的工具名
        self._wrapup_busy = False    # 自动记忆（模型提炼）是否正在跑，防叠
        self.auto_mem_recent = []    # 界面显示：最近自动写入的记忆
        self.conv = None
        # ★ v9.18.0（需求 7）：每个对话一份运行态。下面这些"运行态"名字都通过
        #   property 路由到当前对话那一份（见类顶部的 property 块），全文件其它
        #   地方照旧写 `self.running` 就行。
        self._runs = {}
        # ★ v9.19.0：先给 `_runs` 建好再建 runner（runner 现在挂在 ConvRun 上）
        self.runner = self._make_runner()
        self.title_worker = None
        self._cards = []
        self._groups = []               # 过程折叠组 [(group, key)]
        self._pending_update = None
        # 折叠卡片的用户手动展开/收起，用消息对象身份 (id(msg), 角色) 记住，重建时恢复
        self._card_collapsed = {}
        # 重建后要恢复的滚动位置：(是否贴底, 旧值)
        self._pending_scroll = (True, 0)
        # 助理：钉钉消息 -> AI 处理 -> 结果发回钉钉
        # ★ v9.18.0（需求 8）：配置文件跟其它内部数据一起收进 `.awb/`，
        #   工作区根目录只留产物（见 workspace._migrate_layout）。
        self.assistant = assistant_mod.AssistantService(
            self.ding, self.workspace.assistant_file)
        self._asst_queue = []       # 待处理的消息
        self._asst_worker = None    # 正在处理消息的 worker
        self._asst_cur = None       # 正在处理的那条消息
        # ★ v9.19.0：这一轮助理任务**自己**的 runner（收产物时用它，不用全局的）
        self._asst_runner = None
        # ★ v9.20.0：这一轮开始的时间（看门狗用：卡住就兜底回复，不能一直"处理中"）
        self._asst_started = 0.0
        self._asst_wait_logged = 0.0
        # ★ v9.20.0：延时代回 —— msgid -> 最早可以开始处理的时间戳
        #   用户要求「我多少秒没自己回，才由 AI 代回」，所以消息进来先压着，
        #   到点了再检查一眼：这期间我自己回过就跳过。
        self._asst_not_before = {}
        # ★ v9.20.0：这一轮助理任务的性质 —— "manual"（手动点了就发）/
        #   "prefetch"（先拉回复，等 60 秒再决定发不发）
        self._asst_kind = "manual"
        self._asst_cid = ""
        # ★ v9.20.0：助理默认走 glm；glm 不可用时这里会被设成 "auto"，只降一次不反复试
        self._asst_force_model = ""
        # ★ v9.20.0：这次预取走的是不是"独立子进程"（看门狗要区别对待）
        self._asst_proc = False
        self.im_pipe = None
        # ★ v9.20.0：标签扫描线程（一次只跑一个）
        self._im_scan_thread = None
        self._asst_watchdog = QTimer(self)
        self._asst_watchdog.setInterval(15000)
        self._asst_watchdog.timeout.connect(self._asst_check_stuck)
        self._asst_watchdog.start()

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

        # ---------- 全局左栏（v9.18.0 需求 10）----------
        # 用户原话：「去掉左侧菜单栏，统一在对话列表上方弄出几行这样的选项」。
        # 所以原来那条 76px 的竖直图标栏没了 —— 现在整个左栏是一块**常驻面板**：
        #   上：几行导航格子（对话 / 助理 / 技能 / …）
        #   下：对话列表 + 新建 / 删除
        # 之所以做成"常驻"而不是继续塞在对话页里：切到别的页面时导航必须还在，
        # 否则回不了对话页。
        self.side = QWidget()
        self.side.setObjectName("SidePanel")
        self.side.setFixedWidth(226)
        sv = QVBoxLayout(self.side)
        sv.setContentsMargins(10, 12, 8, 12)
        sv.setSpacing(8)

        self.nav = NavRail()
        self.nav.changed.connect(self._open_page)
        sv.addWidget(self.nav)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{themes.tokens()['nav_border']};")
        sv.addWidget(sep)

        # 对话列表整块单独包一层：顶栏「☰ 列表」按钮收放的是**这一块**，
        # 导航格子不受影响（否则收起列表后就没法切页了）。
        self.conv_block = QWidget()
        cv = QVBoxLayout(self.conv_block)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        side_lbl = QLabel("对话列表")
        side_lbl.setObjectName("SideTitle")
        cv.addWidget(side_lbl)
        self.conv_list = QListWidget()
        self.conv_list.itemClicked.connect(self._on_conv_selected)
        cv.addWidget(self.conv_list, 1)
        self.new_btn = QPushButton("＋ 新建对话")
        self.new_btn.setFixedHeight(32)
        self.new_btn.clicked.connect(self._new_conversation)
        self.del_btn = QPushButton("删除当前")
        self.del_btn.setObjectName("cardBtn")
        self.del_btn.setFixedHeight(28)
        self.del_btn.clicked.connect(self._delete_conversation)
        cv.addWidget(self.new_btn)
        cv.addWidget(self.del_btn)
        sv.addWidget(self.conv_block, 1)

        # 侧栏最底部：老的「工具调用轨迹」页入口（需求 10 把它从导航里拿掉了，
        # 但页面还在栈里，留一行小字进去，功能不丢）。
        self.trace_link = QPushButton("▦ 工具调用记录")
        self.trace_link.setObjectName("SideLink")
        self.trace_link.setToolTip("查看每一次工具调用的输入 / 输出 / 耗时")
        self.trace_link.clicked.connect(lambda: self.nav.select("tools"))
        sv.addWidget(self.trace_link)

        root.addWidget(self.side)

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
        # ★ v9.20.0：助理页重做后的接线（消息双栏 / 设置 / 标签 / 更多管理）
        self.page_assistant.msgs_deleted.connect(self._im_delete_msgs)
        self.page_assistant.im_settings_saved.connect(self._im_settings_saved)
        self.page_assistant.tag_scan.connect(self._im_tag_scan)
        self.page_assistant.tag_saved.connect(self._im_tag_saved)
        self.page_assistant.manage_requested.connect(self._im_open_manager)
        self.page_assistant.whitelist_added.connect(self._im_whitelist_add)
        self.page_assistant.whitelist_removed.connect(self._im_whitelist_remove)
        self.page_assistant.thread_saved.connect(self._im_thread_saved)
        self.page_assistant.task_added.connect(self._im_task_added)
        self.page_assistant.task_deleted.connect(self._im_task_deleted)
        self.page_assistant.task_toggled.connect(self._im_task_toggled)
        self.page_assistant.memory_saved.connect(self._im_memory_saved)
        self.page_assistant.refresh_requested.connect(self._im_refresh_all)
        # ★ v9.20.0：钉钉连接（CLI/账号/会话）从「集成」收进助理「设置」页
        self.page_assistant.ding_login.connect(self._asst_ding_login)
        self.page_assistant.ding_refresh_convs.connect(self._asst_ding_convs)
        self.page_assistant.ding_test_send.connect(self._asst_ding_test)
        self.assistant.incoming.connect(self._assistant_on_incoming)
        # ★ v9.20.0：我自己在钉钉里发的消息也记下来（上下文 + 延时判断都要用）
        self.assistant.outgoing.connect(self._assistant_on_outgoing)
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

        self.page_bg = BgTasksPage()
        self.stack.addWidget(self.page_bg)
        self.page_bg.cancel_one.connect(self._bg_cancel_one)
        self.page_bg.cancel_all.connect(self._bg_cancel_all)
        self.page_bg.clear_done.connect(self._bg_clear_done)
        self.page_bg.refresh.connect(lambda: self.page_bg.reload())
        self.page_bg.allow_cmd.connect(self._bg_set_allow_cmd)

        self.page_team = TeamPage()
        self.stack.addWidget(self.page_team)
        self.page_team.start_run.connect(self._team_start)
        self.page_team.cancel_run.connect(self._team_cancel)
        self.page_team.refresh.connect(lambda: self.page_team.reload())

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

        # ★ v9.18.0（需求 2）：内嵌浏览器页。
        #   ⚠ 顺序要求：`PAGES` 末尾也要追加 ("browser", ...) —— `_open_page` 是拿
        #     PAGES 的下标当 stack 下标的，两边顺序必须严格一一对应。
        #   BrowserPage 是**懒加载**的：这里只建个空壳，Chromium 内核等这一页第一次
        #   被打开时才实例化（否则每个用户一启动就白吃一百多 MB 内存）。
        #   信号连在 BrowserPage.fetched 上（它转发内部 BrowserPane 的回执），
        #   所以不用担心"连的时候 pane 还没建"。
        self.page_browser = BrowserPage()
        self.stack.addWidget(self.page_browser)
        self.page_browser.fetched.connect(self._on_browser_fetched)

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
        self.page_title = QLabel("▤ 对话")
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

        # ---------- 主体：只剩聊天区（左侧列表已上移到全局侧栏，见需求 10）----------
        body = QHBoxLayout()
        body.setSpacing(10)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet(
            f"background:{t['chat_bg']};border-radius:{RADIUS_CARD}px;")
        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(12, 10, 12, 12)
        # ★ v9.18.0（需求 9）：行距收紧（10 -> 6），消息之间更紧凑
        self.chat_layout.setSpacing(6)
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
        # ★ v9.20.0 修「用户输入框有点大」：老代码在这里写死
        #   setMinimumHeight(68) / setMaximumHeight(180) —— 空着也是 68px 的大白框。
        #   现在高度由 ChatInput 自己按内容算：1 行起步、4 行封顶（再长出滚动条），
        #   这里**不要**再设 min/max，否则会把自适应压回去。
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

        self.mic_btn = QPushButton("◍")
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
        # ★★ v9.18.0（需求 13）：团队**搬进对话**。
        #   用户原话：「你团队的功能根本就做的不对，这个功能是直接放到对话中的，
        #   对话时可以选择使用团队……」→ 在状态行与输入卡片之间夹一条紧凑的
        #   团队条：左边「⬡ 团队：不使用 ▾」选队伍，中间是成员名片，右边是阶段提示。
        #   侧栏那个「团队」格子同时在 pages.NAV_CHIPS 里撤掉（回看历史运行的入口
        #   挪到本控件的下拉菜单最底部）。
        #   ⚠ 这里**绝不**能提前调 `setVisible(True)` —— 控件此刻还没有父窗口，
        #     那等价于 show()，会当场变成一扇真实顶层窗口（v9.17.0 那批
        #     "弹一堆小窗口"就是这个机理）。加进布局即可，由父窗口统一显示。
        self.team_bar = TeamBar()
        self.team_bar.team_changed.connect(self._on_team_choice)
        self.team_bar.open_page.connect(self._open_page)
        right.addWidget(self.team_bar)
        right.addWidget(self.input_card)

        body.addLayout(right, 1)
        v.addLayout(body, 1)

        self.send_btn.setEnabled(False)
        self._style_input_area()
        self._render_timer = QTimer()
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._update_live)
        # 流式刷新节拍（毫秒）。★ v9.18.0（需求 6）：不再是写死的 80ms，
        # 由 `_tune_live_interval` 按上一次渲染的真实耗时自适应升降。
        self._live_interval = 110
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
        """顶栏「☰ 列表」：只收放侧栏里的**对话列表**那一块。

        v9.18.0（需求 10）：导航格子不能跟着一起收 —— 否则在别的页面上
        把列表收起后就再也切不回对话页了。
        """
        self.conv_block.setVisible(self.list_btn.isChecked())

    def _update_page_title(self):
        """顶栏只显示当前会话标题（极简）。"""
        title = (self.conv or {}).get("title") or ""
        title = str(title).strip() or "新对话"
        if len(title) > 24:
            title = title[:24] + "…"
        try:
            self.page_title.setText("▤ " + title)
        except Exception:
            pass

    # ================= 页面切换 =================
    def _open_page(self, key):
        # 索引跟着 PAGES 顺序走，避免以后加页面忘记改映射
        keys = [k for k, _i, _n in PAGES]
        idx = keys.index(key) if key in keys else 0
        self.stack.setCurrentIndex(idx)
        # ★ v9.18.0（需求 10）：代码里直接翻页（托盘菜单、向导跳转等）时，
        #   侧栏高亮也要跟着走，否则会出现"页面是设置、高亮还停在对话"的错位。
        try:
            self.nav.set_current(key)
        except Exception:
            pass
        if key == "assistant":
            self.page_assistant.load_cfg(self.assistant.cfg)
            # ★ v9.20.0：进这一页就全量刷一次（消息 / 设置 / 标签），
            #   否则用户改了设置切走再回来看到的还是老值。
            self._im_refresh_all()
        if key == "skills":
            self.page_skills.bind(self.skills)
        elif key == "tasks":
            self.page_tasks.bind(self.scheduler)
        elif key == "memory":
            self.page_memory.bind(self.memory, self.workspace.path)
        elif key == "tools":
            self.page_tools.bind(self.trace)
        elif key == "bg":
            self.page_bg.bind(self.taskman)
            self.page_bg.set_allow_cmd(
                bool(self.settings.get("bg_allow_commands", False)))
        elif key == "team":
            self.page_team.bind(self.teamman)
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
        elif key == "browser":
            # 打开浏览器页：先把内核拉起来（懒加载），再把光标放进地址栏，省一次点击
            try:
                pane = self.page_browser.ensure_pane()
                if pane is not None:
                    pane.addr.setFocus()
            except Exception:
                pass

    def _on_browser_fetched(self, res):
        """内嵌浏览器「交给 AI 分析当前页」的回执（v9.18.0 需求 2）。

        做法刻意保守：**不直接替用户发消息**，而是把「渲染后的正文 + 抓到的数据接口」
        整理成一段现成的话填进输入框，用户看一眼按 Enter 就行。
        这样既省事，又不会让程序自作主张烧一次模型额度。
        """
        res = res or {}
        url = res.get("final_url") or res.get("url") or ""
        body = str(res.get("text") or "").strip()
        xhr = res.get("xhr") or []
        lines = [f"请分析这个页面：{url}", ""]
        if xhr:
            lines.append(f"页面发出的数据接口（{len(xhr)} 个，前面这些最像真接口）：")
            for it in xhr[:10]:
                lines.append(f"- {it.get('method') or 'GET'} {it.get('url') or ''}")
            lines.append("")
        if body:
            lines.append("页面正文（已截断）：")
            lines.append(body[:2500])
        try:
            self._open_page("chat")
            self.input.setPlainText("\n".join(lines)[:6000])
            self.input.setFocus()
            n = len(res.get("requests") or [])
            self.statusBar().showMessage(
                f"已把当前页内容（{n} 个请求 / {len(xhr)} 个疑似接口）填到输入框，"
                "按 Enter 交给 AI", 8000)
        except Exception:
            pass

    # ================= 工作区 =================
    def _make_runner(self, cid=""):
        """建一个 AgentRunner。

        ★ v9.19.0：**必须按 cid 绑定**。以前 files_dir / 记忆路径都读"全局当前对话"，
          用户在一轮跑到一半时切对话，这一轮写出的产物和记忆就会落进**别的对话**
          —— 这正是用户报的"切换对话会导致对话记录、记忆混乱错乱"。
        """
        cid = str(cid or "").strip()
        r = agent_mod.AgentRunner(
            self.workspace.files_dir_for(cid) if cid else self.workspace.files_dir,
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
            taskman=self.taskman, team_cb=self._team_start_sync,
            teamman=self.teamman, bg_agent_cb=self._bg_agent_run,
            keys=self.settings.get("api_keys", {}))
        # 让工具层也能读到设置（比如「禁止弹出新窗口」开关）
        r.settings = self.settings
        # ★ v9.17.0：把"当前对话的记忆文件"告诉工具层（remember/recall 用它决定
        #   记到本对话还是主记忆）。用回调而不是一次性取值，切换对话后自动跟随。
        r.conv_memory_path_cb = (
            (lambda c=cid: self.workspace.conv_memory_file(c)) if cid else
            (lambda: self.workspace.conv_memory_file()))
        # ★ v9.18.0 行为修复（根因 D）：产物目录同样改成回调。
        #   files_dir 原来只在构造时取一次（那时还没选定对话 → 落到
        #   `chats/00_历史文件/files`），切对话后永不更新。
        #   现在每次用都从 workspace 实时取 → 切到哪个对话，产物就写进哪个
        #   对话自己的 `chats/<NN_标题>/files`（需求 8）。
        r.files_dir_cb = (
            (lambda c=cid: self.workspace.files_dir_for(c)) if cid else
            (lambda: self.workspace.files_dir))
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
                self.statusBar().showMessage(f"◈ 已自动记入长期记忆：{brief}", 9000)
                try:
                    self.page_memory.refresh()
                except Exception:
                    pass
            else:
                self.wrapup_notice.emit(f"◈ 已自动记入长期记忆：{brief}")

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
                    self.wrapup_notice.emit(f"◈ 记忆提炼：{brief}")
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
    # =====================================================================
    # 并行任务 + 团队（v9.16.0）
    # 用户原话：「模型在跑我本地命令的时候不影响它继续思考，也可以多条链路
    # 同时执行」「增加一个像 WorkBuddy 这样的团队的功能，各个工程师、产品经理
    # 独立跑，不影响主进程」。
    #
    # 这一段的职责就一句话：**把活儿甩到后台线程，然后立刻回来**。
    # 界面只订阅状态、在 UI 线程刷新，绝不在后台线程里碰控件。
    # =====================================================================
    def _on_bg_changed(self, task, light=False):
        """后台线程回调：转成 Qt 信号（队列连接），回到 UI 线程再刷新。

        ⚠ 这里必须节流：打包这类任务的日志每秒可能几百行，
           每行都刷一次表格会把 UI 线程淹掉（表现就是"界面卡了"）。
           状态变化（light=False）不节流 —— 那是用户最需要马上看到的。
        """
        try:
            if light:
                now = time.time()
                if now - getattr(self, "_bg_last_ui", 0) < 0.4:
                    return
                self._bg_last_ui = now
            self._bg_changed.emit(task.id if task else "")
        except Exception:
            pass

    def _on_team_changed(self, run):
        try:
            self._team_changed.emit(getattr(run, "id", ""))
        except Exception:
            pass

    def _refresh_bg_pages(self, _tid=""):
        try:
            if getattr(self, "page_bg", None) is not None and \
                    self.stack.currentWidget() is self.page_bg:
                self.page_bg.reload()
            n = self.taskman.running_count()
            if n:
                self.statusBar().showMessage(f"⟳ 后台有 {n} 个任务在跑")
        except Exception:
            pass

    def _refresh_team_pages(self, _rid=""):
        try:
            if getattr(self, "page_team", None) is not None and \
                    self.stack.currentWidget() is self.page_team:
                self.page_team.reload()
        except Exception:
            pass

    def _on_bg_notice(self, text):
        try:
            self.statusBar().showMessage(str(text)[:180], 8000)
        except Exception:
            pass

    # ---------- 取消 / 清理 ----------
    def _bg_cancel_one(self, tid):
        self.taskman.cancel(tid)
        self.page_bg.reload()

    def _bg_cancel_all(self):
        n = self.taskman.cancel_all()
        self.page_bg.reload()
        self.statusBar().showMessage(f"已请求取消 {n} 个后台任务", 5000)

    def _bg_clear_done(self):
        self.taskman.clear_finished()
        self.page_bg.reload()

    def _bg_set_allow_cmd(self, flag):
        self.settings["bg_allow_commands"] = bool(flag)
        try:
            self.workspace.save_settings(self.settings)
        except Exception:
            pass
        self.statusBar().showMessage(
            "后台任务：允许执行本地命令" if flag else
            "后台任务：不允许执行本地命令（默认，更安全）", 5000)

    # ---------- 后台 AI：一条独立链路 ----------
    def _bg_agent_confirm(self, cmd):
        """后台任务里的命令确认。

        后台没有界面可以弹确认框 —— 于是：
          · 默认**拒绝**并记进任务日志（宁可不做，也不偷偷跑用户没看过的命令）；
          · 用户在「并行任务」页勾了"允许后台执行命令"才放行（危险命令在
            run_command 内部仍会被 _is_dangerous / window_spawning 拦掉）。
        """
        allow = bool(self.settings.get("bg_allow_commands", False))
        try:
            cur = self.taskman.list(active_only=True)
            if cur:
                cur[0].log_line(("允许" if allow else "已拒绝（后台默认不跑命令）")
                                + f"命令：{cmd[:120]}")
        except Exception:
            pass
        return allow

    def _bg_agent_run(self, task_text, title="", max_steps=8, work_dir=None):
        """在**当前线程**（后台任务线程）里跑一条独立 AI 链路，返回 {"text","files"}。

        为什么要自己建 client：`self.bg_client` 是共享实例，多个后台 AI 同时用它
        会互相抢状态（模型选择、取消标志、流式回调）。**每条链路一个客户端**才干净。

        `work_dir`：v9.18.0（需求 13）新加。团队成员的产物必须落在 TeamRun 指定的
        目录里（`team/<id>/<member>_files`），团队总监汇总阶段才登记得到这些文件；
        不传则沿用原来的独立沙箱 `.awb/bgagent/<tag>/files`。

        返回（v9.18.0 起多带三个字段，团队引擎靠它们判断「真失败」）：
          text            给用户/下游看的正文；**模型调用失败时为空串**
          files           登记到的产物
          ok              False = 模型调用失败（429/403/异常/超时），不是"正常没输出"
          error           失败原因（ok=False 时必有）
          steps_exhausted True = 工具步数用尽（已补一次纯文字收尾，结论可能不完整）
        """
        import uuid as _uuid
        tag = time.strftime("%m%d-%H%M") + "-" + _uuid.uuid4().hex[:4]
        if work_dir:
            base = os.path.abspath(str(work_dir))
            files_dir = base
        else:
            # ★ v9.18.0（需求 8）：后台 AI 的工作目录也收进 `.awb/`
            base = os.path.join(self.workspace.internal("bgagent"), tag)
            files_dir = os.path.join(base, "files")
        os.makedirs(files_dir, exist_ok=True)

        cli = llm_client.LLMClient()
        try:
            cli.policy = getattr(self.client, "policy", "strict")
            cli.set_keys(self.settings.get("api_keys", {}))
            cli.set_web_gate(self.dsw.auto_ready)
        except Exception:
            pass

        # ★ v9.19：如果这一轮是「团队」在派活（team._tls 里有 TeamRun），
        #   ① 把这个 client 登记到 TeamRun 上 —— 用户点"停止"时才能真正掐断
        #      正在路上的 HTTP 请求，而不是等它自己读超时（最长 150s）；
        #   ② 把单次调用超时压到 90s、重试压到 2 次 —— 端点抖动时
        #      一个成员最坏 90×2≈3 分钟，而不是 150×3≈7.5 分钟。
        _team_run = None
        try:
            _team_run = getattr(_team_mod._tls, "run", None)
        except Exception:
            _team_run = None
        _in_team = _team_run is not None
        if _in_team:
            try:
                _team_run.track_client(cli)
                cli.max_attempts = 2
            except Exception:
                pass
        _llm_timeout = 90 if _in_team else 150

        runner = agent_mod.AgentRunner(
            files_dir, workspace_path=base,
            search_cb=lambda q: cli.web_search(q),
            mcp_manager=getattr(self, "mcp", None),
            keys=self.settings.get("api_keys", {}),
            taskman=self.taskman, teamman=self.teamman,
            team_cb=self._team_start_sync, bg_agent_cb=self._bg_agent_run)
        runner.settings = self.settings
        runner.written = []

        sys_prompt = self._build_system_prompt()
        sys_prompt += (
            "\n\n【你现在是后台自主链路（v9.16）】\n"
            "- 你在**独立线程**里跑，用户不会实时看到你的每一步，所以最后必须"
            "用一段清楚的中文说明你做了什么、文件在哪。\n"
            "- 你的工作目录是独立沙箱，写文件用相对路径即可。\n"
            "- ⚠ 你**不能执行需要用户确认的危险操作**（run_command / 删除文件）："
            "这类调用会被直接拒绝。要跑代码请用 run_python。\n"
            "- 不要编造结果；没做到就如实说没做到。"
        )
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": task_text}]
        model_sel = self.settings.get("selected_model", "auto")
        tools_used, last = set(), ""
        model_error = ""       # ★ P0-4：模型调用失败（429/403/超时…）单独记，绝不当成答复
        steps_exhausted = False  # ★ P1-7：工具步数用尽 → 需要一次「只给结论」的强制收尾
        steps = max(1, min(int(max_steps or 8), 20))
        for _step in range(steps):
            try:
                text = cli.chat(msgs, model_sel, False, None, _llm_timeout)
            except Exception as exc:                   # noqa: BLE001
                # ★ 以前把 "[错误] 模型调用失败：429 …" 写进 last 后 break，
                #   上层只看 task 状态 → 成员全标 done、报告就是这段报错原文。
                model_error = f"{type(exc).__name__}: {exc}"
                break
            text = text or ""
            calls = agent_mod.parse_tool_calls(text, limit=3)
            clean = agent_mod.strip_tool_text(text)
            if clean:
                last = clean
            if not calls:
                break
            fb = []
            for call in calls:
                nm = runner.canonical_name(call.get("name"))
                try:
                    res = runner.execute(call, self._bg_agent_confirm)
                except Exception as exc:               # noqa: BLE001
                    res = f"[错误] {type(exc).__name__}: {exc}"
                tools_used.add(nm)
                fb.append(f"[工具 {nm} 返回]\n{str(res)[:4000]}")
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user",
                         "content": "\n\n".join(fb) +
                         "\n\n（继续；如果任务已完成就直接给出最终答复文字。）"})
        else:
            # for 正常跑满（没有 break）＝ 最后一轮还在调工具，被步数上限截断。
            steps_exhausted = True

        # ★ P1-7：步数用尽时补一次「只给结论、不给工具」的强制收尾调用。
        #   否则成员卡片拿到的是半截工具日志，看起来"做完"了其实没有结论。
        if steps_exhausted and not model_error:
            msgs.append({"role": "user",
                         "content": "（工具调用步数已用尽）请**不要再调用任何工具**，"
                                    "只用纯文字给出最终结论：做了什么、文件在哪、"
                                    "验证结果是什么；没做到的部分如实说明。"})
            try:
                tail = cli.chat(msgs, model_sel, False, None, _llm_timeout) or ""
                clean = agent_mod.strip_tool_text(tail)
                if clean:
                    last = clean
            except Exception as exc:                   # noqa: BLE001
                # 收尾调用失败不影响已有的部分结论，但要在 note 里说清楚
                model_error = "" if last else f"{type(exc).__name__}: {exc}"
        files = []
        _seen = set()
        for w in (runner.written or []):
            p = w.get("path") if isinstance(w, dict) else w
            if not p:
                continue
            try:
                real = os.path.normcase(os.path.abspath(p))
                if real in _seen:
                    continue
                _seen.add(real)
                files.append({"path": p, "size": os.path.getsize(p)})
            except Exception:
                files.append({"path": p, "size": 0})
        # ★ v9.19：`runner.written` 只记 write_file 写出来的文件；
        #   模型用 run_python 现场生成的产物（比如导出的 xlsx、跑出来的截图）
        #   不会进 written，导致报告里"没有产物"、用户也找不到文件。
        #   这里补一遍工作目录扫描（跳过 .awb/traces 之类的内部数据）。
        try:
            for dp, dn, fns in os.walk(files_dir):
                dn[:] = [d for d in dn if d not in (".awb", "__pycache__", ".git")]
                for fn in fns:
                    fp = os.path.join(dp, fn)
                    real = os.path.normcase(os.path.abspath(fp))
                    if real in _seen:
                        continue
                    _seen.add(real)
                    try:
                        files.append({"path": fp, "size": os.path.getsize(fp)})
                    except Exception:
                        files.append({"path": fp, "size": 0})
        except Exception:
            pass
        out = {"text": (last or "（没有产出文字结论）") if not model_error else "",
               "files": files,
               "dir": base,
               "tools": sorted(tools_used),
               "ok": not model_error,
               "steps_exhausted": bool(steps_exhausted)}
        if model_error:
            out["error"] = f"模型调用失败：{model_error}"
        if _in_team and _team_run is not None:
            # 成员跑完就注销，免得 TeamRun 长期攥着一堆 client（内存 + 误伤）。
            try:
                _team_run.untrack_client(cli)
            except Exception:
                pass
        return out

    # ---------- 团队 ----------
    def _team_member_run(self, member_id, prompt, files_dir, max_steps):
        """`TeamRun` 的**成员执行器**（v9.18.0 需求 13 接线）。

        为什么必须有这一层适配：`TeamRun._run_member` 是按
            agent_fn(member_id, prompt, files_dir, max_steps) -> {"text","files"}
        去调的（见 `app/team.py` 的 `TeamRun._run_member`），而 `_bg_agent_run(task_text, title="", max_steps=8)`
        是"一条独立 AI 链路"的入口、自己开沙箱目录 —— 两个签名**位置参数含义不同**。

        ⚠ 直接传 `self._bg_agent_run` 的后果（老代码就是这么写的，实为死路）：
          `files_dir` 会落到 `title` 上，而 `max_steps` 会收到一个**路径字符串**，
          紧接着 `int(max_steps or 8)` 抛 ValueError —— 整支团队第一步就崩。
          所以这里显式把位置参数重新绑好，并把团队给的目录当成工作目录。
        """
        res = self._bg_agent_run(str(prompt or ""),
                                 title=f"团队成员 {member_id}",
                                 max_steps=int(max_steps or 8) or 8,
                                 work_dir=files_dir)
        if isinstance(res, dict):
            return res
        return {"text": str(res or ""), "files": []}

    def _team_start(self, goal, roles=None, max_steps=6, team_id=None):
        """起一支团队：立刻返回 TeamRun，真正的执行在后台任务里。"""
        run = self.teamman.create(goal, self._team_member_run,
                                  taskman=self.taskman,
                                  roles=roles, max_steps=max_steps,
                                  team_id=team_id)
        holder = {}

        def _fn(task=None):
            if task is not None:
                task.log_line(f"团队 {run.id} 开始编排")
            rep = run.run()
            holder["r"] = rep
            files = []
            for c in run.cards.values():
                for f in (c.get("files") or []):
                    files.append({"path": f.get("path"), "size": f.get("size", 0)})
            if task is not None:
                for f in files:
                    if f.get("path"):
                        task.add_file(f["path"])
                if run.status != "done":
                    # ★ P0-4：后台任务页也要能看出「这轮没正常交付」，别只剩一个"已完成"。
                    task.log_line(f"团队状态：{run.status}"
                                  + (f"（{run.error}）" if run.error else ""))
                    for w in (run.warnings or [])[:10]:
                        task.log_line(f"警告：{w}")
            self._bg_notice.emit(f"团队 {run.id} 已结束（{run.status}）")
            return rep or "（团队无汇总）"

        self.taskman.submit("team", f"团队：{goal[:40]}", _fn,
                            detail=goal[:300],
                            dedup_key=f"teamrun:{run.id}")
        return run

    # 供工具层调用（同名不同用途，保持一处实现）
    # agent 侧调用签名是 team_cb(goal, roles, max_steps)（见 app/agent.py:2904），
    # 所以 team_id 必须留成带默认值的关键字参数，别插到中间去。
    def _team_start_sync(self, goal, roles=None, max_steps=6, team_id=None):
        return self._team_start(goal, roles, max_steps, team_id=team_id)

    def _team_cancel(self, rid):
        run = self.teamman.get(rid)
        if run is not None:
            run.cancel()
            self.page_team.reload()
            self.statusBar().showMessage(f"已请求停止团队 {rid}", 5000)

    # ---------- 对话内团队（v9.18.0 需求 13）----------
    def _on_team_choice(self, team_id):
        """用户在对话里换了一支队伍：记在**这个对话**上，不是全局设置。

        记在每个对话里，才符合"对话时可以选择使用团队" —— 一个对话用软件团队、
        另一个对话不用团队，互不干扰（和需求 7 的"每个对话独立"是一套逻辑）。
        """
        tid = str(team_id or "")
        if self.conv is not None:
            self.conv["team_id"] = tid
            try:
                self.workspace.save_conversation(self.conv)
            except Exception:
                pass
        if tid:
            t = team_presets.get_preset(tid) or {}
            who = "、".join(m["name"] for m in (t.get("members") or []))
            self._append_system_note(
                f"⬡ 已把本对话交给团队「{t.get('name', tid)}」。\n"
                f"成员：{who}\n发出消息后由这支队伍协作完成（可直接说需求）。")
        else:
            self._append_system_note("⬡ 本对话已切回普通模式（不再使用团队）。")

    def _sync_team_bar(self):
        """把**当前对话**记着的团队选择回填到团队条（切对话 / 新建对话时调）。

        `set_team` 对非法或空的 id 会自动回落成「不使用团队」，所以这里不用判空。
        """
        bar = getattr(self, "team_bar", None)
        if bar is None:
            return
        try:
            # ★ v9.19.0：silent=True —— 这里只是「把对话记着的选择回填到控件」，
            #   不是用户换了队伍。不静默的话每切一次对话都会往记录里塞一条
            #   「⬡ 本对话已切回普通模式」，还会被当 role=tool 喂给模型。
            bar.set_team(str((self.conv or {}).get("team_id") or ""), silent=True)
            bar.reset_transient()
        except Exception:
            pass

    def _on_team_evt(self, kind, payload, cid=None):
        """团队过程事件 —— **已经在 UI 线程**（经 `_team_evt` 信号转过来）。

        两件事：① 团队条上的成员名片亮灯/熄灯；② 关键节点同步到状态行与对话流，
        免得用户只盯着输入框上方那一行小字。

        ★ v9.19.0：`cid` = 发起这一轮团队的对话。对话流里的提示、状态行都必须
          按它判定 —— 用户可能早就切到别的对话去了。
          `cid=None` 表示"当前对话"（和本文件其它回调的约定一致）。
        """
        payload = payload or {}
        cid = self._conv_cid() if cid is None else str(cid or "")
        # 团队条只有"当前显示的那个对话"的才该被点亮
        if self._is_current(cid):
            try:
                self.team_bar.apply_event(kind, payload)
            except Exception:
                pass
        if kind == "phase":
            self._on_phase("team", payload.get("label") or "", cid)
        elif kind == "dispatch":
            n = len(payload.get("tasks") or [])
            self._append_system_note(
                f"⬡ 项目总监已把需求拆成 {n} 项派下去，开始并行执行。", cid)
        elif kind == "member_start":
            self._on_phase(
                "team", f"{payload.get('name', '')} 正在{payload.get('note') or '工作'}", cid)
        elif kind == "member_done":
            mark = (_icons_mod.ic("ok") if payload.get("status") == "done"
                    else _icons_mod.ic("fail"))
            self._append_system_note(
                f"{mark} {payload.get('name', '成员')} · {payload.get('title', '')}"
                f"（{payload.get('status')}）", cid)

    def _send_team(self, text, team_id, cid, note=""):
        """把这一轮交给预置团队去做（v9.18.0 需求 13）。

        与普通一轮的唯一差别是「谁来干活」：这里起一支 TeamRun 丢进后台线程，
        过程事件接到对话内的团队条上，跑完把总监的汇总当成本轮的 assistant 回复。
        （用户消息在 `_send` 里已经入会话并落盘，所以对话流里顺序是对的。）
        """
        run = self._run(cid)
        run.live_text = ""
        run.pending_tool = []
        run.live_reason = ""
        run.reason_card = None
        run.running = True

        t = team_presets.get_preset(team_id) or {}
        try:
            self.team_bar.reset_transient()
            self.team_bar.set_phase(f"{t.get('emoji', '')} 队伍已就位，正在开会…")
        except Exception:
            pass
        if self._is_current(cid):
            self._set_stop_mode(True)
            self._open_page("chat")
            self.state_line.set_phase("team", "")
            self._render(stick=True)
        self._append_system_note(
            f"⬡ 本轮交给团队「{t.get('name', team_id)}」执行"
            f"（成员：{'、'.join(m['name'] for m in (t.get('members') or []))}）。", cid)

        # ⚠ TeamRun._emit 是在成员线程里调的：**只能**发 Qt 信号，绝不能摸控件。
        # ★ v9.19.0：带上 cid —— 事件要回到**发起的那个对话**，不是"正在看的那个"。
        def _on_event(kind, payload):
            self._team_evt.emit(str(kind), payload or {}, cid)

        trun = self.teamman.create(
            text, self._team_member_run, taskman=self.taskman,
            max_steps=int(self.settings.get("team_max_steps", 6) or 6),
            team_id=team_id, on_event=_on_event)
        run.team_run = trun

        job = TeamJob(trun, cid)
        job.done_one.connect(self._on_team_finished)
        self._team_jobs[cid] = job        # 持引用，否则 QThread 被 GC 会崩
        job.start()

    def _on_team_finished(self, cid, trun):
        """团队跑完：把总监汇总写成**这一轮**的 assistant 回复（v9.18.0 需求 13）。"""
        run = self._run(cid)
        run.running = False
        run.team_run = None
        self._team_jobs.pop(cid, None)

        status = str(getattr(trun, "status", "") or "")
        rep = str(getattr(trun, "report", "") or "").strip()
        if not rep:
            err = str(getattr(trun, "error", "") or "")
            if status == "cancelled":
                rep = "（团队已按你的操作取消，没有产出汇总。）"
            else:
                # 绝不能静默收场：用户点一次"发送"什么也没看到，正是要消灭的体验。
                rep = ("团队这一轮没能给出汇总文字"
                       + (f"（{err}）" if err else "（成员产出可在「团队」页回看）"))
        # 1) 写进对话记录。可能用户已经切走了，那就单独读写那个对话。
        conv = self.conv if self._is_current(cid) else None
        if conv is None:
            try:
                conv = self.workspace.load_conversation(cid)
            except Exception:
                conv = None
        if conv is not None:
            conv.setdefault("messages", []).append(
                {"role": "assistant", "content": rep})
            try:
                self.workspace.save_conversation(conv)
            except Exception:
                pass
        # 2) 界面收尾
        if self._is_current(cid):
            try:
                self.team_bar.set_phase(
                    {"done": "✓ 团队已完成",
                     "partial": "⚠ 团队部分完成（有成员失败或未交付）",
                     "failed": "✕ 团队执行失败",
                     "cancelled": "⊘ 团队已取消"}.get(status, "团队已结束"))
            except Exception:
                pass
            self._render_sig = None          # 指纹失效，保证这轮答复一定渲染出来
            self._set_stop_mode(False)
            self.state_line.set_phase("idle", "")
            self._render(stick=True)
        self._bg_notice.emit(f"团队 {getattr(trun, 'id', '')} 已结束（{status}）")

    def _plain_runner(self, allow_subagent=True, cid=""):
        """给后台线程用的 AgentRunner：只挂不碰界面的回调。

        cid：把产物/记忆锁在指定对话上（v9.19.0，防切换对话串台）。
        """
        cid = str(cid or "").strip()
        def _sub(task, steps=6):
            return self._agent_run_once(task, max_iter=int(steps) + 2,
                                        quiet=True)
        r = agent_mod.AgentRunner(
            self.workspace.files_dir_for(cid) if cid else self.workspace.files_dir,
            vision_cb=self._vision,
            workspace_path=self.workspace.path,
            search_cb=lambda q: self.client.web_search(q),
            mcp_manager=getattr(self, "mcp", None),
            dingtalk_cb=self._dingtalk,
            keys=self.settings.get("api_keys", {}),
            taskman=self.taskman, teamman=self.teamman,
            team_cb=self._team_start_sync, bg_agent_cb=self._bg_agent_run,
            subagent_cb=(_sub if allow_subagent else None))
        r.conv_memory_path_cb = (
            (lambda c=cid: self.workspace.conv_memory_file(c)) if cid else
            (lambda: self.workspace.conv_memory_file()))
        r.files_dir_cb = (
            (lambda c=cid: self.workspace.files_dir_for(c)) if cid else
            (lambda: self.workspace.files_dir))
        return r

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
            final += ("\n\n> ⚠ 这次要求写入/创建文件，但没有成功写出任何文件，"
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
            res = ("⚠ 这次模型没有真正动手（只回了客套话），"
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
            f"◷ 定时任务手动执行完成：\n{result[:1500]}")

    def _on_sched_done(self, payload):
        """调度器到点自动跑完后的界面回报。"""
        t = payload.get("task") or {}
        res = payload.get("result") or ""
        self.page_tasks.reload()
        self._append_system_note(
            f"◷ 定时任务「{t.get('name')}」已自动执行：\n{res[:1500]}")

    def _conv_obj(self, cid=None):
        """取某个对话的记录对象（当前显示的那个优先，否则从磁盘读进来）。

        ★ v9.19.0：后台回调（团队事件等）回来时，用户往往已经切到别的对话了。
          这时往 `self.conv` 里写 = 把 A 的过程写进 B 的记录里 —— 用户报的
          「切换对话会导致对话记录混乱」就是这么来的。所以统一走这里取。
        """
        cid = self._conv_cid() if cid is None else str(cid or "")
        if not cid or self._is_current(cid):
            return self.conv
        try:
            return self.workspace.load_conversation(cid)
        except Exception:
            return None

    def _append_system_note(self, text, cid=None):
        """把后台发生的事写进**对应对话**（用户看得见）。

        ★ v9.19.0：加了 cid —— 一轮跑完前用户切走了，这条提示要落在**发起它的
          那个对话**里，不能糊到用户正在看的另一个对话上。
        """
        cid = self._conv_cid() if cid is None else str(cid or "")
        conv = self._conv_obj(cid)
        if not conv:
            return
        conv.setdefault("messages", []).append(
            {"role": "tool", "content": config.strip_stream_reset(text)})
        try:
            self.workspace.save_conversation(conv)
        except Exception:
            pass
        if self._is_current(cid):
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
        # ★ 换工作区：后台任务 / 团队也换数据目录（旧任务继续在旧目录里跑完，
        #   不强行掐断 —— 掐断可能打断用户正在等的一次打包）。订阅要重挂。
        try:
            self.taskman = _taskman_mod.TaskManager(
                self.workspace.path,
                max_running=int(self.settings.get("bg_max_parallel", 4) or 4))
            self.teamman = _team_mod.TeamManager(self.workspace.path)
            self.taskman.subscribe(self._on_bg_changed)
            self.teamman.subscribe(self._on_team_changed)
            if getattr(self, "page_bg", None) is not None:
                self.page_bg.bind(self.taskman)
                self.page_bg.set_allow_cmd(
                    bool(self.settings.get("bg_allow_commands", False)))
            if getattr(self, "page_team", None) is not None:
                self.page_team.bind(self.teamman)
        except Exception:
            pass
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
        # ★ v9.19.0：换工作区 = 换了一整套对话。旧对话的"每个对话一份运行态"
        #   （含 runner / 产物目录）在旧工作区里，绝不能带到新工作区来 ——
        #   否则新工作区里点开一个对话，产物会往旧工作区的 chats/ 里写。
        self.__dict__.pop("_runs", None)
        self.conv = None
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
        # ★ v9.20.0：IM 仓库（会话档案 / 消息流水 / 定时任务 / 日志本 / 记忆）
        self._init_im()
        self._maybe_start_dingtalk_stream()

    def _load_conversations(self):
        self.conv_list.clear()
        cur = self._conv_cid()
        for c in self.workspace.list_conversations():
            cid = c["id"]
            # ★ v9.18.0（需求 7）：哪些对话"正在跑"一眼可见（互不影响，可同时跑）
            running = bool(self._runs.get(cid) and self._runs[cid].running)
            title = c["title"] + ("　● 运行中" if running else "")
            self.conv_list.addItem(title)
            item = self.conv_list.item(self.conv_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            if cid == cur:
                item.setSelected(True)

    def _new_conversation(self, silent=False):
        self.conv = self.workspace.new_conversation()
        # ★ v9.17.0：切到新对话 → 之后产出的文件、记的事都落到它自己的文件夹
        #   （`chats/<NN_标题>/`，里面 files/ 与 MEMORY.md）
        try:
            self.workspace.set_current(self.conv.get("id"))
        except Exception:
            pass
        self._card_collapsed = {}      # 换会话：折叠记录随之清空
        self._render_sig = None        # ★ 换对话必须让渲染指纹失效，否则可能被短路
        # ★ v9.20.0：新对话 = 分页窗口重算（还没消息，等价于 fold=0）
        self._fold_auto = True
        self._fold_n = 0
        self._conv_gen = int(getattr(self, "_conv_gen", 0)) + 1   # 作废在途的延迟渲染
        self._sync_team_bar()          # ★ v9.18.0（需求 13）：新对话 = 默认不使用团队
        self._render()
        self._refresh_run_ui()
        self._update_page_title()
        if not silent:
            self._load_conversations()
        self.input.setFocus()
        self._open_page("chat")

    @staticmethod
    def _sanitize_conv(conv):
        """清掉历史记录里由 bug 写进去的垃圾（v9.19.0）。

        两类：
        ① `␜AWB-RESET␝` 及其残骸 —— 那是"作废重来"的内部信号，被 DeepSeek 网页
           通道当正文塞进了历史，模型看到会照着抄（实测出现过
           `第一步：列出 D:\\AI 工作空间\\chats␜AWB-RESET␝我先看一下当前进度`）。
        ② 重复的「⬡ 本对话已切回普通模式」—— 每切一次对话就写一条（老 `set_team`
           不分场合 emit），一个对话能攒七八条，还会被当 role=tool 喂给模型，
           模型就会在回答里"复述这些历史消息"。

        返回是否真的改动过（需要重新落盘）。
        """
        if not isinstance(conv, dict):
            return False
        msgs = conv.get("messages")
        if not isinstance(msgs, list):
            return False
        changed = False
        out = []
        seen_plain_note = False
        for m in msgs:
            if not isinstance(m, dict):
                out.append(m)
                continue
            content = m.get("content")
            if isinstance(content, str) and content:
                clean = config.strip_stream_reset(content)
                if clean != content:
                    m = dict(m)
                    m["content"] = clean
                    content = clean
                    changed = True
                if (m.get("role") == "tool"
                        and content.startswith("⬡ 本对话已切回普通模式")):
                    # 只留第一条，其余全是噪声
                    if seen_plain_note:
                        changed = True
                        continue
                    seen_plain_note = True
            if not (isinstance(content, str) and content.strip()) and m.get("role") in (
                    "tool", "thinking"):
                # 清空的过程块直接丢掉（不能留一条空气泡）。
                # 注意：**只清 tool/thinking**，assistant 哪怕空着也可能是模型
                # 当时真的没说话，删了就改变了历史，不碰。
                changed = True
                continue
            out.append(m)
        if changed:
            conv["messages"] = out
        return changed

    def _on_conv_selected(self, item):
        cid = item.data(Qt.ItemDataRole.UserRole)
        conv = self.workspace.load_conversation(cid)
        if conv:
            # ★ v9.19.0：先把老 bug 写进去的垃圾清掉，再显示（也顺手修好用户的旧记录）
            try:
                if self._sanitize_conv(conv):
                    self.workspace.save_conversation(conv)
            except Exception:
                pass
            self.conv = conv
            # ★ v9.17.0：跟着切换"当前对话目录"
            try:
                self.workspace.set_current(conv.get("id"))
            except Exception:
                pass
            # ★★ v9.18.0（需求 7）：**绝对不能**在这里把 live_text / pending_tool 清掉！
            #   那些是"每个对话自己"的运行态（存在 ConvRun 里）。老写法在这里
            #   无脑清空 —— 于是从 A 对话切到 B、再切回 A，A 本轮已经产出但还没
            #   写进记录的过程/正文就永远丢了（用户报的"本轮之前的输出内容也可能会消失"）。
            #   现在什么都不清：切回来原样恢复；界面控件按"这个对话当前是不是在跑"重设。
            self._card_collapsed = {}  # 折叠记录是纯界面态，重建时按当前内容重算即可
            self._render_sig = None    # ★ 换对话：渲染指纹失效（防跨对话误短路）
            # ★ v9.20.0：新对话进来 = 分页窗口重算（只铺最近一页）
            self._fold_auto = True
            self._fold_n = 0
            # ★ v9.18.0（需求 13）：团队选择是**每个对话自己的**（存在 conv["team_id"]），
            #   切过来要把团队条同步成这个对话记的那一支，否则会串台。
            self._sync_team_bar()
            self._refresh_run_ui()
            self._update_page_title()
            # ★★ v9.20.0 修「点开长对话严重卡顿 / 未响应」：
            #   老写法在这里同步 `_render()`，120 条消息要一次性造 120 张
            #   MessageCard（实测 ~1.7s 全在主线程）—— 用户点下去就是白屏 +
            #   窗口"未响应"。现在拆成两段：
            #     ① 立刻做：换 conv、标题、团队条、运行态、清空旧内容
            #        （长对话先给一行「正在加载对话…」占位，界面马上有反应）；
            #     ② 下一帧做：真正的卡片渲染（QTimer.singleShot(0)）。
            #   Qt 会先处理队列里已排的 paint 事件、再跑 0ms 定时器，所以
            #   点击的第一帧就能画出来，不会出现"点下去整窗发白"。
            #   `_conv_gen` 是代次号：用户快速连点两个对话时，只有最后一轮
            #   真渲染，前一轮直接丢掉（否则白烧一遍 CPU）。
            self._conv_gen = int(getattr(self, "_conv_gen", 0)) + 1
            gen = self._conv_gen
            if len(conv.get("messages") or []) > CHAT_PAGE_SIZE:
                self._show_loading()
            QTimer.singleShot(0, lambda g=gen: self._render_conv_async(g))

    def _render_conv_async(self, gen):
        """延后一帧真正渲染刚才选中的对话（见 `_on_conv_selected`）。"""
        if gen != int(getattr(self, "_conv_gen", gen)):
            return                      # 用户已经点了别的对话，这一轮作废
        self._render()

    def _show_loading(self):
        """切到长对话时的轻量占位：只清空 + 一行灰字，绝不建卡片。

        目的只有一个 —— 让"点下去"这一帧有反应。真正的渲染在下一帧。
        """
        self._clear_chat()
        self._cards = []
        self._groups = []
        self._live_card = None
        self._reason_card = None
        t = themes.tokens()
        lab = QLabel("正在加载对话…")
        lab.setStyleSheet(f"color:{t['text_muted']};font-size:{themes.fs(13)};"
                          "padding:18px 8px;")
        self.chat_layout.addWidget(lab)
        self.chat_layout.addStretch(1)

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
                lines.append(("✓ " if ok else "✕ ") + n + "\n    " + str(m))
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
            ("✓ " if ok else "✕ ") + msg)
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
            pi.set_dingtalk_status("✕ " + msg)
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
        lines = ["✓ 扫码授权登录成功",
                 f"昵称：{info.get('nick') or '（未返回）'}",
                 f"unionId：{info.get('unionId') or '（未返回）'}",
                 f"手机号：{info.get('mobile') or '（未返回）'}",
                 "",
                 "已保存用户身份。这条通道拿到的是「用户级 token」，"
                 "可以继续用来调用需要用户身份的钉钉接口。"]
        pi.set_dingtalk_status(f"✓ 已授权登录：{info.get('nick') or info.get('unionId')}")
        pi.show_probe_text("\n".join(lines))

    def _dingtalk_group_send(self, cfg, text):
        self.ding.update(cfg)
        pi = self.page_integrations
        if not text:
            pi.set_dingtalk_status("请先填要发到群里的内容。")
            return
        try:
            r = self.ding.send_group(text)
            pi.set_dingtalk_status(f"✓ 已发到群（{r.get('target')}）")
        except Exception as e:
            pi.set_dingtalk_status(f"✕ 发群失败：{e}")

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
                        f"✓ 登录成功：{st2.get('user_name')} @ {st2.get('corp_name')}")
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
                self._dingtalk_dws_log.emit(f"✓ 已发到会话「{target}」")
            except Exception:
                try:
                    self.ding.dws_dm(target, content)
                    self._dingtalk_dws_log.emit(f"✓ 已作为单聊发给「{target}」")
                except Exception as e2:
                    self._dingtalk_dws_log.emit(f"✕ 发送失败：{e2}")
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_dingtalk_stream(self, cfg, enable):
        self.ding.update(cfg)
        if enable:
            ok, msg = self.ding.start_stream(self._dingtalk_on_message,
                                            on_log=self._dingtalk_log)
            self.page_integrations.set_dingtalk_status(("✓ " if ok else "✕ ") + msg)
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
                            "✓ Stream 已连接，正在接收机器人消息")
                        self._dt_tick = None
                        return
                    if d["n"] >= 12:
                        self.page_integrations.set_dingtalk_status(
                            "✕ Stream 连不上（已等 24 秒）。\n"
                            "看下面日志最后一行；常见原因是应用没开机器人的 Stream 模式。"
                            + (("\n最后一条日志：" + tail) if tail else ""))
                        self._dt_tick = None
                        return
                    self.page_integrations.set_dingtalk_status(
                        f"◕ Stream 连接中…（{d['n'] * 2}s）"
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
            msg = f"◷ 提醒：{r['text']}"
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

    # ---------- ★ v9.20.0：IM 助手底座（持久化 / 上下文 / 定时 / 日志本） ----------
    def _init_im(self):
        """换工作区时重建 IM 仓库与编排核心。"""
        try:
            from ..im import IMStore
            from ..im.hub import IMHub
            self.im_store = IMStore(self.workspace.path)
            self.im_hub = IMHub(self.im_store, self._im_llm, self._im_send,
                                log=lambda s: self._safe_asst_log(s),
                                me_name=self._ding_self_name())
            # ★ v9.20.0：「先拉后等」的时序编排（预取回复 → 等 N 秒 → 查你回没回 → 合并发送）
            try:
                from ..im.pipeline import ReplyPipeline
                st0 = self.im_store.settings()
                self.im_pipe = ReplyPipeline(
                    store=self.im_store,
                    delay=int(st0.get("delay_seconds") or 60),
                    log=lambda s: self._safe_asst_log(s),
                    me_name=self._ding_self_name())
                self._safe_asst_log(
                    "代回策略：收到消息就先生成回复，等你 %s 秒；"
                    "这期间你自己回了就不代回" % self.im_pipe.delay)
            except Exception as exc:
                self.im_pipe = None
                self._safe_asst_log(
                    f"代回编排初始化失败（退回逐条处理）：{type(exc).__name__}: {exc}")
            # 定时任务（每日/每周/每月主动发消息）—— 10 秒检查一次，
            # 兼着驱动「预取 / 到点发送」，60 秒延时才能准到 ±10 秒内
            if not hasattr(self, "_im_timer") or self._im_timer is None:
                self._im_timer = QTimer(self)
                self._im_timer.setInterval(10000)
                self._im_timer.timeout.connect(self._im_tick)
            self._im_timer.start()
            self._safe_asst_log(
                f"IM 数据目录：{self.im_store.root}"
                f"（会话 {len(self.im_store.threads())} 个 / "
                f"定时任务 {len(self.im_store.tasks())} 条）")
            # ★ v9.20.0：把用户上次在「设置」里填的东西接上老轮询服务
            #   （间隔 / 自动回复开关 / 粗粒度的监听范围）
            try:
                st = self.im_store.settings()
                patch = {}
                if st.get("interval"):
                    patch["interval"] = int(st["interval"])
                g = bool(st.get("scope_group", True))
                p = bool(st.get("scope_p2p", True))
                patch["scope"] = ("at_me" if (g and not p)
                                  else ("all" if (g or p) else "self"))
                if patch:
                    self.assistant.apply_cfg(patch)
            except Exception:
                pass
            self._im_refresh_all()
        except Exception as exc:
            self.im_store = None
            self.im_hub = None
            self._safe_asst_log(f"IM 底座初始化失败：{type(exc).__name__}: {exc}")

    def _safe_asst_log(self, text):
        try:
            self.page_assistant.log(str(text))
        except Exception:
            pass

    def _assistant_on_outgoing(self, msg):
        """我自己本人在钉钉里发出去的消息 —— 只记流水，不进处理队列。

        ★ v9.20.0：两个用处：
          ① AI 组上下文时能看到"我本人是怎么回的"，语气才对得上；
          ② 「多少秒没回才由 AI 代回」靠它判断你到底回了没有。
        """
        try:
            hub = getattr(self, "im_hub", None)
            cid = str((msg or {}).get("cid") or "")
            if hub is None or not cid:
                return
            self.im_store.append_message(
                cid, mid=(msg or {}).get("msgid") or "", direction="out",
                sender=(msg or {}).get("sender") or self._ding_self_name(),
                text=(msg or {}).get("text") or "", ai=False, status="")
        except Exception as exc:
            self._safe_asst_log(f"记录我自己的消息失败：{type(exc).__name__}: {exc}")

    def _im_send(self, thread_id, text):
        """把一句话发回某个钉钉会话（hub 用）。"""
        try:
            if not thread_id:
                return False
            return bool(self.ding.dws_send_to_cid(thread_id, str(text)))
        except Exception as exc:
            self._safe_asst_log(f"发回钉钉失败：{type(exc).__name__}: {exc}")
            return False

    def _im_llm(self, messages, thread):
        """hub 用的模型调用（同步）。

        ★ 用户要求「模型不要让用户选，默认 glm」—— 助理固定优先 glm-4-flash，
          前台对话选什么模型都不影响它。免费档不可用时退到 auto（省钱优先），
          仍然不需要用户操心。
        """
        cli = self.bg_client
        try:
            return cli.chat(messages, "glm-4-flash", False, None, 90) or ""
        except Exception:
            try:
                return cli.chat(messages, "auto", False, None, 120) or ""
            except Exception:
                return ""

    def _im_tick(self):
        """定时消息 + 代回时序的心跳（每 10 秒）。失败绝不影响界面。"""
        hub = getattr(self, "im_hub", None)
        if hub is not None:
            try:
                sent = hub.tick()
                for item in sent:
                    t = item.get("task") or {}
                    self._safe_asst_log(
                        f"◷ 定时消息「{t.get('title')}」"
                        + ("已发到钉钉" if item.get("ok") else "发送失败"))
            except Exception as exc:
                self._safe_asst_log(f"定时消息检查失败：{type(exc).__name__}: {exc}")
        # ★ v9.20.0：到点的代回 + 需要预取的批次
        try:
            self._im_pipeline_due()
            self._im_pipeline_pump()
        except Exception as exc:
            self._safe_asst_log(f"代回时序检查失败：{type(exc).__name__}: {exc}")

    def _im_refresh_views(self):
        """只刷新「消息」双栏（每收到一条、每回一条都会走这里）。

        ★ v9.20.0：设置页 / 标签页**不跟着刷** —— 用户可能正在里面打字改东西，
          每 20 秒把他改到一半的勾选冲掉是灾难。要刷全量走 `_im_refresh_all()`。
        """
        if getattr(self, "im_hub", None) is None:
            return
        try:
            rows = self.im_store.rows()
        except Exception as exc:
            self._safe_asst_log(f"读消息列表失败：{type(exc).__name__}: {exc}")
            return
        try:
            self.page_assistant.set_im_data(rows=rows)
        except Exception:
            pass

    def _im_refresh_all(self):
        """全量刷新：消息双栏 + 设置 + 标签 + 更多管理对话框的数据。"""
        if getattr(self, "im_hub", None) is None:
            return
        try:
            st = self.im_store.settings()
            rows = self.im_store.rows()
            tags = self.im_store.tags()
            threads = self.im_store.threads()
            tasks = self.im_store.tasks()
            logs = self.im_store.logbook()
            memory = self.im_store.memory()
        except Exception as exc:
            self._safe_asst_log(f"读助手数据失败：{type(exc).__name__}: {exc}")
            return
        try:
            self.page_assistant.set_im_data(
                rows=rows, settings=st, tags=tags,
                threads=threads, tasks=tasks, logs=logs,
                memory=memory, review_fn=self.im_store.review)
            self.page_assistant.set_ding_status(self._asst_ding_status_text())
        except Exception as exc:
            self._safe_asst_log(f"刷新助手页失败：{type(exc).__name__}: {exc}")

    # ---------- 钉钉连接（在助理「设置」页里配）----------
    def _asst_ding_status_text(self):
        """读登录状态（带 60 秒缓存 —— `dws auth status` 是一次子进程，别每次刷都跑）。"""
        now = time.time()
        cache = getattr(self, "_ding_status_cache", None)
        if cache and now - cache[0] < 60:
            return cache[1]
        text = ""
        try:
            if not self.ding.dws_available():
                text = ("没找到 dws（钉钉工作台 CLI）。装了 WorkBuddy 就自带；"
                        "或自行 npm i -g dingtalk-workspace-cli。")
            else:
                st = self.ding.dws_auth_status() or {}
                if st.get("authenticated"):
                    who = st.get("user_name") or "（已登录）"
                    corp = st.get("corp_name") or ""
                    text = "✓ 已登录：%s%s" % (who, ("（%s）" % corp) if corp else "")
                else:
                    text = "✗ 还没登录 —— 点下面的「扫码授权登录」或「设备码登录」"
        except Exception as exc:
            text = "状态读取失败：%s: %s" % (type(exc).__name__, exc)
        self._ding_status_cache = (now, text)
        return text

    def _asst_ding_login(self, device):
        self._safe_asst_log("已发起 dws 授权登录（%s），请按提示完成授权…"
                            % ("设备码" if device else "浏览器扫码"))
        self._ding_status_cache = None
        try:
            self._dingtalk_dws_login(bool(device))
        except Exception as exc:
            self._safe_asst_log(f"发起登录失败：{type(exc).__name__}: {exc}")
            return
        # 登录完回来刷一次状态（用户不用手动点）
        def _later():
            time.sleep(8)
            self._ding_status_cache = None
            self._im_refresh_all()
        threading.Thread(target=_later, daemon=True).start()

    def _asst_ding_convs(self):
        """刷新会话列表（后台读，回来填下拉框）。"""
        if not self.ding.dws_available():
            self._safe_asst_log("没找到 dws，读不了会话列表")
            return
        self._safe_asst_log("正在读取钉钉会话列表…")

        def w():
            rows = []
            try:
                raw = self.ding.dws_list_conversations(30) or []
            except Exception as exc:
                self._safe_asst_log(f"读会话列表失败：{type(exc).__name__}: {exc}")
                raw = []
            for c in raw:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("id") or c.get("openConversationId") or "").strip()
                if not cid:
                    continue
                rows.append({"id": cid,
                             "name": str(c.get("name") or c.get("conversationName") or cid),
                             "kind": str(c.get("kind") or "group")})
            self._asst_ding_convs_back.emit(rows)
        threading.Thread(target=w, daemon=True).start()

    def _asst_ding_convs_back(self, rows):
        """（UI 线程）会话列表回来了。"""
        self._safe_asst_log("会话列表：%d 个" % len(rows or []))
        try:
            self.page_assistant.set_ding_status(self._asst_ding_status_text(), rows)
        except Exception:
            pass

    def _asst_ding_test(self, cid, text):
        if not cid:
            return
        def w():
            ok = False
            try:
                ok = bool(self._im_send(cid, text))
            except Exception as exc:
                self._safe_asst_log(f"发测试消息失败：{type(exc).__name__}: {exc}")
            self._asst_log_ui("✓ 测试消息已发到钉钉" if ok else "✕ 测试消息发送失败")
        threading.Thread(target=w, daemon=True).start()

    # ---------- ★ v9.20.0：助理页「消息 / 设置 / 标签」的写操作 ----------
    def _im_settings_saved(self, cfg):
        """设置页保存：范围 / 白名单 / 延时 / 不确定时是否回 / 身份背景。"""
        if getattr(self, "im_store", None) is None:
            return
        d = dict(cfg or {})
        interval = d.pop("interval", None)
        auto = d.pop("auto_reply", None)
        try:
            self.im_store.set_settings(**d)
        except Exception as exc:
            self._safe_asst_log(f"保存助手设置失败：{type(exc).__name__}: {exc}")
            return
        # 轮询间隔 / 自动回复开关仍然归老的轮询服务管
        patch = {}
        if interval:
            try:
                patch["interval"] = max(10, min(600, int(interval)))
            except Exception:
                pass
        if auto is not None:
            patch["auto_reply"] = bool(auto)
        g = bool(d.get("scope_group", True))
        p = bool(d.get("scope_p2p", True))
        patch["scope"] = ("at_me" if (g and not p) else ("all" if (g or p) else "self"))
        try:
            self.assistant.apply_cfg(patch)
        except Exception:
            pass
        # ★ v9.20.0：延时改了要立刻生效（不然要重启）
        try:
            if getattr(self, "im_pipe", None) is not None:
                self.im_pipe.set_delay(d.get("delay_seconds"))
        except Exception:
            pass
        self.statusBar().showMessage("助手设置已保存", 4000)
        self._safe_asst_log(
            "助手设置已保存：群聊 %s / 单聊 %s / 群里只在@我 %s / "
            "延时 %s 秒 / 拿不准也回 %s"
            % ("✓" if g else "✗", "✓" if p else "✗",
               "✓" if d.get("scope_at_me", True) else "✗",
               d.get("delay_seconds"), "✓" if d.get("reply_uncertain", True) else "✗"))
        self._im_refresh_all()

    def _im_delete_msgs(self, mids):
        """从两栏里删掉选中消息（落盘标记，重启也不会再冒出来）。"""
        if not mids or getattr(self, "im_store", None) is None:
            return
        mids = [str(x) for x in mids if x]
        n = 0
        for mid in mids:
            m = self.page_assistant.msgs.get(mid) or {}
            cid = str(m.get("cid") or "")
            if not cid:
                continue
            try:
                if self.im_store.hide_message(cid, mid):
                    n += 1
            except Exception:
                pass
        drop = set(mids)
        for mid in mids:
            self.page_assistant.msgs.pop(mid, None)
            self._asst_not_before.pop(mid, None)
        self._asst_queue[:] = [x for x in self._asst_queue if x not in drop]
        # ★ v9.20.0：也从"等待代回"的窗口里摘掉，否则删了还会被代回
        try:
            if getattr(self, "im_pipe", None) is not None:
                self.im_pipe.forget(mids)
        except Exception:
            pass
        self._safe_asst_log(f"已删除 {n} 条消息")
        self._im_refresh_views()

    def _im_whitelist_add(self, item):
        item = dict(item or {})
        raw = str(item.get("name") or item.get("id") or "").strip()
        if not raw or getattr(self, "im_store", None) is None:
            return
        cid, name, kind = raw, raw, str(item.get("kind") or "group")
        matched = False
        try:
            for tid, t in (self.im_store.threads() or {}).items():
                if tid == raw or str(t.get("name") or "") == raw:
                    cid = tid
                    name = str(t.get("name") or raw)
                    kind = str(t.get("kind") or kind)
                    matched = True
                    break
        except Exception:
            pass
        try:
            self.im_store.whitelist_add(cid, name, kind)
        except Exception as exc:
            self._safe_asst_log(f"加白名单失败：{type(exc).__name__}: {exc}")
            return
        if matched:
            self._safe_asst_log(f"白名单已加：{name}")
        else:
            self._safe_asst_log(
                f"白名单已记下「{name}」，但还没对上具体会话 —— "
                f"等助手收到过它的消息后，再加一次（或直接填会话 id）就生效")
        self._im_refresh_all()

    def _im_whitelist_remove(self, cid):
        if getattr(self, "im_store", None) is None or not cid:
            return
        try:
            self.im_store.whitelist_remove(str(cid))
        except Exception:
            return
        self._safe_asst_log(f"白名单已移除：{cid}")
        self._im_refresh_all()

    # ---------- 标签页 ----------
    def _im_tag_scan(self):
        """首次全量扫描：读全部会话 → 让 AI 一个个打关系标签。"""
        if getattr(self, "im_store", None) is None:
            return
        w = getattr(self, "_im_scan_thread", None)
        if w is not None and w.isRunning():
            self.page_assistant.set_scan_state("正在扫描中，请稍等…")
            return
        if not self.ding.dws_available():
            self.page_assistant.set_scan_state("没找到 dws，先把「集成 → 钉钉」配好")
            return
        self.page_assistant.set_scan_state("正在读取钉钉会话列表…")
        self._safe_asst_log("开始扫描全部会话并打标签（一次一个会话，会慢一些）")

        outer = self

        class _ScanWorker(QThread):
            progress = pyqtSignal(int, int, str)
            done = pyqtSignal(dict)

            def __init__(self, store, ding, llm, parent=None):
                super().__init__(parent)
                self.store = store
                self.ding = ding
                self.llm = llm
                self.aborted = False

            def run(self):
                try:
                    from ..im import tags as _imtags
                    res = _imtags.scan_and_tag(
                        self.store, self.ding, self.llm,
                        progress=lambda i, n, nm: self.progress.emit(int(i), int(n), str(nm)))
                except Exception as exc:
                    res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}",
                           "tagged": 0, "total": 0, "convs": []}
                self.done.emit(res)

        w = _ScanWorker(self.im_store, self.ding, self._im_llm, outer)
        w.progress.connect(
            lambda i, n, nm: self.page_assistant.set_scan_state(f"正在扫描 {i}/{n}：{nm}"))
        w.done.connect(self._im_tag_scan_done)
        self._im_scan_thread = w
        w.start()

    def _im_tag_scan_done(self, res):
        self._im_scan_thread = None
        res = dict(res or {})
        if not res.get("ok"):
            self.page_assistant.set_scan_state(
                f"扫描失败：{res.get('reason') or '未知原因'}")
            self._safe_asst_log(f"扫描失败：{res.get('reason')}")
            return
        self.page_assistant.set_scan_state(
            "扫描完成：共 %s 个会话，打上 %s 个标签（拿不准的留空了，你可以自己改）"
            % (res.get("total"), res.get("tagged")))
        self._safe_asst_log(
            "扫描完成：%s 个会话 / %s 个标签" % (res.get("total"), res.get("tagged")))
        try:
            self.im_store.set_settings(scanned=True)
        except Exception:
            pass
        self._im_refresh_all()

    def _im_tag_saved(self, cid, tag):
        if getattr(self, "im_store", None) is None or not cid:
            return
        try:
            self.im_store.set_tag(str(cid), str(tag or ""), note="手动", auto=False)
        except Exception as exc:
            self._safe_asst_log(f"保存标签失败：{type(exc).__name__}: {exc}")
            return
        self._safe_asst_log(f"标签已保存：{cid} → {tag or '（清空）'}")
        self._im_refresh_all()

    # ---------- 「更多管理」对话框（定时消息 / 日志本 / 记忆 / 会话模式） ----------
    def _im_open_manager(self):
        try:
            self.page_assistant.show_manager()
        except Exception as exc:
            self._safe_asst_log(f"打开消息管理失败：{type(exc).__name__}: {exc}")
            return
        self._im_refresh_all()

    def _im_thread_saved(self, d):
        d = dict(d or {})
        tid = str(d.pop("id", "") or "")
        if not tid or getattr(self, "im_store", None) is None:
            return
        try:
            self.im_store.update_thread(tid, **d)
        except Exception as exc:
            self._safe_asst_log(f"保存会话设置失败：{type(exc).__name__}: {exc}")
            return
        self._safe_asst_log(f"会话设置已保存：{tid}")
        self._im_refresh_all()

    def _im_task_added(self, t):
        if getattr(self, "im_store", None) is None:
            return
        try:
            rec = self.im_store.add_task(dict(t or {}))
        except Exception as exc:
            self._safe_asst_log(f"添加定时消息失败：{type(exc).__name__}: {exc}")
            return
        self._safe_asst_log(f"已添加定时消息：{rec.get('title')}")
        self._im_refresh_all()

    def _im_task_deleted(self, tid):
        if getattr(self, "im_store", None) is None:
            return
        try:
            self.im_store.remove_task(str(tid))
        except Exception:
            return
        self._safe_asst_log("已删除定时消息")
        self._im_refresh_all()

    def _im_task_toggled(self, tid, on):
        if getattr(self, "im_store", None) is None:
            return
        try:
            self.im_store.update_task(str(tid), enabled=bool(on))
        except Exception:
            return
        self._safe_asst_log(f"定时消息已{'启用' if on else '停用'}")
        self._im_refresh_all()

    def _im_memory_saved(self, text):
        if getattr(self, "im_store", None) is None:
            return
        try:
            self.im_store.set_memory(str(text or ""))
        except Exception as exc:
            self._safe_asst_log(f"保存记忆失败：{type(exc).__name__}: {exc}")
            return
        self._safe_asst_log("共享记忆已保存（所有会话都会带上它）")

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
            self._im_refresh_all()
        except Exception:
            pass

    def _assistant_on_incoming(self, msg):
        """服务收到新消息：落盘 → 显示 → 通知 → 按需排队处理。"""
        # ★ v9.20.0：给这一条打上"收到时刻"，延时回复和"你自己回过没"都靠它
        msg = dict(msg or {})
        msg["recv_ts"] = time.time()
        mid = self.page_assistant.add_message(msg)
        self.assistant.mark_processed(mid)
        # ★ v9.20.0：每条消息**先落盘**（含上下文与日志本抽取）。
        #   老实现只放在内存里，程序一重启就全没了 —— 用户说的"消息消失"。
        try:
            hub = getattr(self, "im_hub", None)
            if hub is not None:
                conv = str(msg.get("conv") or "")
                sender = str(msg.get("sender") or "")
                scope = str(self.assistant.cfg.get("scope") or "")
                kind = "self" if scope == "self" else (
                    "p2p" if conv and conv == sender else "group")
                hub.observe(dict(msg, kind=msg.get("kind") or kind))
        except Exception as exc:
            self._safe_asst_log(f"消息落盘失败（不影响处理）：{type(exc).__name__}: {exc}")
        who = msg.get("conv") or msg.get("sender") or "钉钉"
        self.statusBar().showMessage(
            f"⚑ 助理收到消息（{who}）：{str(msg.get('text'))[:40]}", 8000)
        if self.assistant.cfg.get("auto_reply"):
            # ★ v9.20.0：用户要的流程 ——「收到消息 → 立刻让 AI 把回复准备好
            #   → 等你 N 秒 → 你没自己回就发（多条合并成一条）」
            pipe = getattr(self, "im_pipe", None)
            if pipe is not None and msg.get("cid"):
                st = pipe.on_message(msg)
                if st is not None:
                    if len(st.get("msgids") or []) == 1:
                        self._safe_asst_log(
                            "收到新消息：先让 AI 把回复准备好，"
                            "等你 %s 秒；这期间你自己回了就不代回" % pipe.delay)
                    else:
                        self._safe_asst_log(
                            "同一个会话又来了 %d 条，已合并成一条回复一起处理"
                            % len(st.get("msgids") or []))
                    self._im_pipeline_pump()
                else:
                    self._asst_queue.append(mid)
                    self._asst_pump()
            else:
                # 编排层没起来（极少数情况）→ 退回老的逐条队列
                self._asst_queue.append(mid)
                self._asst_pump()
        else:
            self.page_assistant.log("新消息待处理（自动回复关着）：点「全部交给 AI」")
        self._im_refresh_views()

    def _assistant_process_one(self, msgid):
        if not msgid:
            self.page_assistant.log("先在右边勾一条消息")
            return
        # 手动点的一定立刻处理，不再等延时
        self._asst_not_before.pop(msgid, None)
        # ★ v9.20.0：从"等待代回"的窗口里摘掉，否则时间到了还会再代回一次
        try:
            if getattr(self, "im_pipe", None) is not None:
                self.im_pipe.forget([msgid])
        except Exception:
            pass
        self._asst_queue.append(msgid)
        self._asst_pump()

    def _assistant_process_all(self):
        for mid in self.page_assistant.row_msgids():
            m = self.page_assistant.msgs.get(mid) or {}
            if m.get("status") in ("待处理", "失败"):
                self._asst_not_before.pop(mid, None)
                self._asst_queue.append(mid)
        try:
            if getattr(self, "im_pipe", None) is not None:
                self.im_pipe.forget(list(self._asst_queue))
        except Exception:
            pass
        self._asst_pump()

    def _asst_check_stuck(self):
        """看门狗：这一轮太久没结果 -> 兜底，并把状态复位。

        ★ v9.20.0：用户报的「AI 正在处理中，最后消息消失不见」里，有一部分就是
          worker 卡死 / 事件没回来 —— 界面永远停在"处理中"。有了这个每 15 秒的
          检查，最坏情况 5 分钟内一定会给对方一个交代。

        ★ 两种任务分开处理：
          · manual（手动点了就发）→ 立刻兜底回一句；
          · prefetch（预取备用）→ **不能立刻发**，只把这次预取标成失败，
            交给 `due()` 到点决定（发兜底话术 / 标需人工）。
        """
        try:
            if not self._asst_started:
                return
            alive = (self._asst_worker is not None
                     and self._asst_worker.isRunning())
            # ★ 走独立子进程时没有 ChatWorker；子进程自己有 150 秒时限，
            #   这里把它当作"还活着"，只在真超时才兜底。
            if getattr(self, "_asst_proc", False):
                alive = True
            waited = time.time() - float(self._asst_started)
            if alive and waited < 300:
                return
            reason = "模型 5 分钟没有返回" if alive else "处理线程意外退出"
            kind = getattr(self, "_asst_kind", "manual")
            msg = self._asst_cur
            cid = getattr(self, "_asst_cid", "") or ""
            try:
                if self._asst_worker is not None:
                    self._asst_worker.abort()
            except Exception:
                pass
            self._asst_worker = None
            self._asst_cur = None
            self._asst_runner = None
            self._asst_started = 0.0
            self._asst_kind = "manual"
            self._asst_cid = ""
            if kind == "prefetch" and cid:
                self._safe_asst_log(f"⚠ 看门狗：预取{reason}，到点按兜底处理")
                pipe = getattr(self, "im_pipe", None)
                if pipe is not None:
                    b = pipe.batch(cid) or {}
                    # 用**当前** seq 结束这次预取：这样它不会被当成"过时"而无限重拉
                    pipe.end_fetch(cid, b.get("seq", -1), error=reason)
                try:
                    for mid, m in list((self.page_assistant.msgs or {}).items()):
                        if m.get("cid") == cid and m.get("status") == "处理中":
                            self.page_assistant.update_status(mid, "待处理")
                except Exception:
                    pass
                self._im_pipeline_pump()
                return
            self._safe_asst_log(f"⚠ 看门狗：{reason}，已自动兜底回复")
            self._asst_recover(msg, reason)
            self._asst_pump()
        except Exception:
            pass

    # ---------- ★ v9.20.0：「先拉后等再决定发不发」的完整时序 ----------
    def _im_pipeline_pump(self):
        """有会话需要预取就拉一次（串行，不跟前台对话/其它助理任务抢）。"""
        pipe = getattr(self, "im_pipe", None)
        if pipe is None:
            return
        if self._asst_worker is not None and self._asst_worker.isRunning():
            return
        # 前台正在对话时也让一让，别两边抢同一个模型配额
        try:
            if self._any_running():
                QTimer.singleShot(3000, self._im_pipeline_pump)
                return
        except Exception:
            pass
        cid = pipe.need_fetch()
        if cid:
            self._asst_prefetch(cid)

    def _im_fallback_text(self):
        """「AI 拿不准 / 预取失败」时该回的那句话；返回 None = 按设置不回。"""
        st = {}
        try:
            if getattr(self, "im_store", None) is not None:
                st = self.im_store.settings() or {}
        except Exception:
            st = {}
        if not st.get("reply_uncertain", True):
            return None
        return (str(st.get("uncertain_text") or "").strip()
                or "消息已纳入收件箱，加急处理中，主人正在赶来的路上，此消息由AI代回")

    def _asst_prefetch(self, cid):
        """立刻去拉这一批的回复 —— 「先拉后等」，到点就能发，用户零等待。"""
        pipe = getattr(self, "im_pipe", None)
        if pipe is None or not cid:
            return False
        if self._asst_worker is not None and self._asst_worker.isRunning():
            return False
        batch = pipe.batch(cid)
        if not batch or not batch.get("msgs"):
            pipe.end_fetch(cid, -1, error="没有可处理的消息")
            return False
        seq = pipe.begin_fetch(cid)
        if seq < 0:
            return False
        try:
            system, history = self._im_context_for({"cid": cid})
        except Exception as exc:
            pipe.end_fetch(cid, seq, error=f"组上下文失败：{exc}")
            return False
        merged = pipe.build_prompt_text(batch)
        hist_txt = ""
        if history:
            lines = []
            for h in history[-12:]:
                tag = "我" if h.get("role") == "assistant" else "对方"
                lines.append(f"{tag}：{h.get('content')}")
            hist_txt = "【最近的对话（供参考，别复述）】\n" + "\n".join(lines) + "\n\n"
        prompt = (
            "【钉钉收到的消息】你在替主人回复钉钉消息。\n"
            "· 直接用**一段话**写出要回给对方的内容 —— 像真人发微信那样短，"
            "不要客套开场、不要解释你在做什么、不要 Markdown、不要列步骤；\n"
            "· 对方问什么就答什么；多条消息要**合并成一条回复**一次答完；\n"
            "· 拿不准的（身份背景里没有、也推不出来）就只输出 [[UNCERTAIN]]，别硬编。\n\n"
            + hist_txt + f"【这次要回的】{merged}"
        )
        api_msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": prompt}]
        self._asst_kind = "prefetch"
        self._asst_cid = cid
        self._asst_cur = None
        self._asst_started = time.time()
        for m in batch["msgs"]:
            self.page_assistant.update_status(m.get("msgid"), "处理中")
        self._safe_asst_log("开始生成回复（%d 条合并）…" % len(batch["msgs"]))
        # ★ 用户要求「助理不要让人选模型，默认 glm」；万一 glm 不可用，自动退 auto
        model_sel = getattr(self, "_asst_force_model", "") or "glm-4-flash"
        # ★ v9.20.0：优先用**独立子进程**拿回复（卡死能硬杀、不拖累界面）；
        #   子进程不可用时退回进程内的 ChatWorker（功能等价，只是不能被硬杀）。
        use_proc = False
        try:
            from ..im import replyproc
            use_proc = bool(replyproc.available())
        except Exception:
            use_proc = False
        if use_proc:
            self._asst_proc = True
            self._asst_worker = None
            self._asst_runner = None
            threading.Thread(
                target=self._asst_prefetch_proc,
                args=(cid, seq, api_msgs, model_sel), daemon=True).start()
            return True
        self._asst_proc = False
        self._asst_runner = self._make_runner(self._conv_cid())
        self._asst_worker = ChatWorker(
            self.bg_client, self._asst_runner, api_msgs, True,
            model_sel=model_sel, enable_search=False, max_iter=4)
        self._asst_worker.finished.connect(
            lambda out, c=cid, s=seq: self._asst_prefetch_done(c, s, out))
        self._asst_worker.error.connect(
            lambda e, c=cid, s=seq: self._asst_prefetch_done(c, s, None, err=e))
        self._asst_worker.notice.connect(lambda s: self.page_assistant.log(str(s)))
        self._asst_worker.start()
        return True

    def _asst_prefetch_proc(self, cid, seq, api_msgs, model_sel):
        """（后台线程）走独立子进程拿回复，结果通过信号回 UI 线程。"""
        text, err, files = "", "", []
        try:
            from ..im import replyproc
            r = replyproc.request(api_msgs, model=model_sel, timeout=150) or {}
            if r.get("ok") and str(r.get("text") or "").strip():
                text = config.strip_stream_reset(str(r["text"]).strip())
            else:
                err = str(r.get("error") or "子进程没返回内容")
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        self._im_prefetch_evt.emit(str(cid), int(seq), str(text), str(err), files)

    def _asst_prefetch_done(self, cid, seq, out_messages, err=""):
        """进程内预取回来了：抽正文 → 交给 `_asst_prefetch_store`。"""
        text, files = "", []
        try:
            for m in reversed(out_messages or []):
                if m.get("role") == "assistant" and (m.get("content") or "").strip():
                    text = config.strip_stream_reset(m["content"].strip())
                    break
            _r = self._asst_runner if self._asst_runner is not None else self.runner
            files = [f.get("path") if isinstance(f, dict) else str(f)
                     for f in (getattr(_r, "written", None) or [])]
            files = [p for p in files if p and os.path.isfile(p)]
        except Exception as exc:
            err = err or f"收尾异常：{exc}"
        self._asst_prefetch_store(cid, seq, text, err, files)

    def _asst_prefetch_store(self, cid, seq, text, err="", files=None):
        """预取结束（两条路径共用）：存成草稿，**先不发**（等时间到再判定）。"""
        # glm 不可用 → 换 auto 再试一次（只试一次，避免死循环）
        if err and not getattr(self, "_asst_force_model", ""):
            self._asst_force_model = "auto"
            self._safe_asst_log(
                f"助理模型不可用（{str(err)[:60]}），换 auto 再试一次")
        pipe = getattr(self, "im_pipe", None)
        if pipe is not None:
            pipe.end_fetch(cid, seq, text=text, files=list(files or []),
                           error=err or "")
        try:
            for mid, m in list((self.page_assistant.msgs or {}).items()):
                if m.get("cid") == cid and m.get("status") == "处理中":
                    self.page_assistant.update_status(mid, "待处理")
        except Exception:
            pass
        if text and "[[UNCERTAIN]]" not in text:
            self._safe_asst_log(
                "回复已备好（%d 字），到 %s 秒还是没你自己回就发出去"
                % (len(text), pipe.delay if pipe is not None else "?"))
        elif err:
            self._safe_asst_log(f"预取没成（{str(err)[:60]}），到点会按兜底处理")
        self._asst_worker = None
        self._asst_runner = None
        self._asst_started = 0.0
        self._asst_proc = False
        self._asst_kind = "manual"
        self._asst_cid = ""
        self._im_refresh_views()
        QTimer.singleShot(0, self._im_pipeline_pump)

    def _im_pipeline_due(self):
        """到点了：该发的发，该跳过的跳过。"""
        pipe = getattr(self, "im_pipe", None)
        if pipe is None:
            return
        try:
            due = pipe.due()
        except Exception as exc:
            self._safe_asst_log(f"到点检查失败：{type(exc).__name__}: {exc}")
            return
        for item in due:
            cid = str(item.get("cid") or "")
            batch_msgs = list(item.get("msgs") or [])
            ids = [m.get("msgid") for m in batch_msgs if m.get("msgid")]
            if not ids:
                pipe.finish(cid, False, "没有消息 id")
                continue

            # ① 你自己已经回过了 —— 绝不代回
            if item.get("manual"):
                for mid in ids:
                    self.page_assistant.update_status(
                        mid, "已回复", "（你自己已经回过了，这条没让 AI 代回）")
                    try:
                        self.im_store.set_status(cid, mid, "sent",
                                                 "（你自己已经回过了）")
                    except Exception:
                        pass
                self._safe_asst_log(
                    "你已经自己回过（%d 条），这一批不代回" % len(ids))
                pipe.finish(cid, False, "你自己回过")
                self._im_refresh_views()
                continue

            # ② 拿不准 / 没取到回复
            text = str(item.get("text") or "").strip()
            if ("[[UNCERTAIN]]" in text) or item.get("fallback") or not text:
                fb = self._im_fallback_text()
                if fb is None:
                    for mid in ids:
                        self.page_assistant.update_status(mid, "需人工", "")
                        try:
                            self.im_store.set_status(cid, mid, "需人工", "")
                        except Exception:
                            pass
                    self._safe_asst_log(
                        "AI 拿不准这 %d 条，按你的设置不代回，留在左栏等你回" % len(ids))
                    pipe.finish(cid, False, "拿不准且设置不代回")
                    self._im_refresh_views()
                    continue
                text = fb
                self._safe_asst_log(
                    "AI 拿不准 / 没取到回复（%s），已按兜底话术代回"
                    % (item.get("error") or "拿不准"))

            # ③ 真发
            self._im_send_bg(cid, text, ids)
            pipe.finish(cid, True)

    def _im_send_bg(self, cid, text, ids):
        """后台把代回内容发出去，结果回 UI 线程再写状态（跨线程只走信号）。"""
        def w():
            ok = False
            try:
                ok = bool(self._im_send(cid, text))
            except Exception as exc:
                self._safe_asst_log(f"代回失败：{type(exc).__name__}: {exc}")
            self._im_send_evt.emit(str(cid), list(ids or []), str(text), bool(ok))
        threading.Thread(target=w, daemon=True).start()

    def _im_sent_back(self, cid, ids, text, ok):
        """（UI 线程）代回结果落地：写状态 + 刷界面。"""
        ids = list(ids or [])
        for i, mid in enumerate(ids):
            last = (i == len(ids) - 1)
            try:
                self.im_store.set_status(
                    str(cid), mid,
                    ("sent" if ok else "failed") if last else "merged",
                    text if last else "")
            except Exception:
                pass
            self.page_assistant.update_status(
                mid, "已回复" if ok else "失败",
                text if last else "（和另一条一起回了）")
        if ok:
            self._asst_log_ui("✓ 已代回钉钉：%s" % str(text)[:50])
        else:
            self._asst_log_ui("✕ 代回钉钉失败（%d 条，留在左栏）" % len(ids))
        self._im_refresh_views()

    def _asst_pump(self):
        """串行处理队列：一次只跑一条，避免和前台对话抢 runner。"""
        if self._asst_worker is not None and self._asst_worker.isRunning():
            return
        # 前台正在对话时先等等，别两边同时用同一个 runner
        # ★ v9.18.0：现在是"**任意一个**对话在跑"就让路（多个对话可能同时跑）。
        try:
            if self._any_running():
                # ★ v9.20.0：让路也要吭声，否则用户只看到"助理不动了"
                now = time.time()
                if now - float(self._asst_wait_logged or 0) > 90:
                    self._asst_wait_logged = now
                    self._safe_asst_log(
                        "前台对话正在跑，助理先排队等一会儿（不会丢消息）")
                QTimer.singleShot(3000, self._asst_pump)
                return
        except Exception:
            pass
        if self._asst_queue:
            mid = self._asst_queue[0]
            # ★ v9.20.0：延时未到就先等着（用户可能自己回）
            try:
                nb = float(self._asst_not_before.get(mid) or 0)
            except Exception:
                nb = 0.0
            left = nb - time.time()
            if left > 0:
                QTimer.singleShot(int(min(left, 20) * 1000) + 300, self._asst_pump)
                return
            self._asst_queue.pop(0)
            self._asst_not_before.pop(mid, None)
            self._asst_start(mid)

    def _im_context_for(self, msg):
        """取这个会话的（系统提示, 历史消息列表）。

        ★ v9.20.0：这就是用户要的「消息输入输出必须是一个独立的对话、能串上下文」。
          每个钉钉会话一份档案（模式/人设/规则/上下文条数）+ 共享记忆 + 私有记忆。
        """
        tid = str(msg.get("cid") or "")
        hub = getattr(self, "im_hub", None)
        if hub is not None and tid:
            try:
                from ..im import policy as _impolicy
                thread = self.im_store.thread(tid) or {}
                msgs = self.im_store.messages(tid, thread.get("ctx_limit") or 20)
                # ★ v9.20.0：用户在「设置」里填的身份背景是回答的最高依据
                try:
                    ident = str((self.im_store.settings() or {}).get("identity") or "")
                except Exception:
                    ident = ""
                ctx = _impolicy.build_messages(
                    thread, msgs, me_name=self._ding_self_name(),
                    shared_memory=self.im_store.memory(),
                    thread_memory=self.im_store.thread_memory(tid),
                    identity=ident)
                return ctx[0]["content"], ctx[1:]
            except Exception as exc:
                self._safe_asst_log(f"组上下文失败（退回默认提示）：{exc}")
        return (self.settings.get("system_prompt") or config.SYSTEM_PROMPT), []

    def _asst_start(self, msgid):
        msg = self.page_assistant.msgs.get(msgid)
        if not msg:
            # ★ v9.20.0：绝不静默丢弃 —— 老写法这里直接 return，队列项就永远没了
            self._safe_asst_log(f"找不到消息 {msgid}（可能已被清空），跳过这条")
            QTimer.singleShot(0, self._asst_pump)
            return
        # ★ v9.20.0：延时到了再看一眼 —— 这期间我自己在钉钉里回过就不代回了
        try:
            recv_ts = float(msg.get("recv_ts") or 0)
            last = 0.0
            if getattr(self, "im_store", None) is not None and msg.get("cid"):
                last = float(self.im_store.last_manual_out(str(msg.get("cid"))) or 0)
            if recv_ts and last > recv_ts:
                self.page_assistant.update_status(
                    msgid, "已回复", "（你已经自己回过了，这条没让 AI 代回）")
                self._safe_asst_log(
                    f"你已经自己回过「{msg.get('name') or ''}」，这条跳过")
                self._asst_done()
                return
        except Exception:
            pass
        self._asst_cur = msg
        self._asst_started = time.time()
        # ★ v9.20.0：手动点了就发 —— 不走「等 60 秒」那套
        self._asst_kind = "manual"
        self._asst_cid = str(msg.get("cid") or "")
        self.page_assistant.update_status(msgid, "处理中")
        system, history = self._im_context_for(msg)
        hist_txt = ""
        if history:
            lines = []
            for h in history[-12:]:
                tag = "我" if h.get("role") == "assistant" else "对方"
                lines.append(f"{tag}：{h.get('content')}")
            hist_txt = "【最近的对话（供参考，别复述）】\n" + "\n".join(lines) + "\n\n"
        prompt = (
            "【钉钉收到的消息】你在替主人回复钉钉消息，请当作他的助手来处理：\n"
            "· 能直接做完的就做完（写文件、跑脚本、查资料都行，产出物放工作区 files/ 下）；\n"
            "· 做完后用**一段话**把最终结果说清楚——这就是要回给对方的原话，"
            "像真人发微信一样短，不要客套、不要步骤流水账，涉及文件时带上完整路径；\n"
            "· 消息里要文件/程序就真的生成，别只说怎么做；\n"
            "· 不确定的事别硬编，直接说「我确认一下」。\n\n"
            + hist_txt +
            f"【这次要回的】{msg.get('conv') or ''}"
            f"{(' / ' + msg.get('sender')) if msg.get('sender') else ''}"
            f"：{msg.get('text')}"
        )
        api_msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": prompt}]
        self.page_assistant.log(f"开始处理：{str(msg.get('text'))[:50]}")
        # ★ v9.19.0：runner 自己持一份 —— 收产物时不能读全局 `self.runner`，
        #   用户中途切对话的话那是别人的 runner。
        self._asst_runner = self._make_runner(self._conv_cid())
        self._asst_worker = ChatWorker(
            self.bg_client, self._asst_runner, api_msgs, True,
            model_sel=self.settings.get("selected_model") or "auto",
            enable_search=(bool(self.settings.get("enable_search"))
                           and agent_mod.needs_web_search(
                               str(msg.get("text") or prompt))),
            max_iter=12)
        self._asst_worker.finished.connect(lambda out, m=msg: self._asst_finish(m, out))
        self._asst_worker.error.connect(lambda e, m=msg: self._asst_error(m, e))
        self._asst_worker.notice.connect(lambda s: self.page_assistant.log(str(s)))
        self._asst_worker.start()

    def _asst_recover(self, msg, why, bounced=False):
        """处理失败/卡住时的兜底：**一定要给对方回一句话**。

        ★ v9.20.0：用户原话「AI正在处理中，但是最后AI消息消失不见，不回复」。
          根因就是 `_asst_error` / `_asst_finish` 里一旦异常，只写日志不发送 ——
          对方永远等不到回复，界面上也只是一条"失败"。
        """
        if not msg:
            return
        text = ("这条我这边没处理完（%s），我回头补给你。"
                % (str(why or "未知原因")[:60]))
        mid = msg.get("msgid")
        try:
            self.page_assistant.update_status(mid, "失败", text)
        except Exception:
            pass
        try:
            self._asst_reply(msg, text, [])
        except Exception as exc:
            self._safe_asst_log(f"兜底回复也发不出去：{type(exc).__name__}: {exc}")
        try:
            hub = getattr(self, "im_hub", None)
            if hub is not None and msg.get("cid"):
                self.im_store.append_message(
                    msg.get("cid"), direction="out", sender=self._ding_self_name(),
                    text=text, ai=True, status="failed")
        except Exception:
            pass
        self._safe_asst_log(f"已发送兜底回复：{text}")

    def _asst_done(self, msg=None):
        """不发送地收尾：复位状态 + 刷新列表 + 继续下一条。

        ★ v9.20.0：给「AI 拿不准，按设置不代回」和「你自己已经回过了」这两条路径用。
          它们都**不能发消息**，但也绝不能把队列卡死。
        """
        self._asst_worker = None
        self._asst_cur = None
        self._asst_runner = None
        self._asst_started = 0.0
        try:
            self._im_refresh_views()
        except Exception:
            pass
        QTimer.singleShot(0, self._asst_pump)

    def _asst_error(self, msg, err):
        self.page_assistant.update_status(msg.get("msgid"), "失败", f"[处理出错] {err}")
        self._asst_worker = None
        self._asst_cur = None
        self._asst_runner = None
        self._asst_started = 0.0
        # ★ 绝不静默收场：出错了也要给对方回一句
        self._asst_recover(msg, err)
        self._asst_pump()

    def _asst_finish(self, msg, out_messages):
        answer = ""
        files = []
        try:
            for m in reversed(out_messages or []):
                if m.get("role") == "assistant" and (m.get("content") or "").strip():
                    answer = config.strip_stream_reset(m["content"].strip())
                    break
            # ★ v9.19.0：用**这一轮自己的** runner 收产物
            _r = self._asst_runner if self._asst_runner is not None else self.runner
            files = [f.get("path") if isinstance(f, dict) else str(f)
                     for f in (getattr(_r, "written", None) or [])]
            files = [p for p in files if p and os.path.isfile(p)]
            if not answer:
                # 模型一个字都没给：也要回一句，不能就这么没了
                answer = "这条我这边没生成出内容，你再发一次我马上处理。"
            # ★ v9.20.0：AI 拿不准（身份背景里查不到、也推不出来）时，
            #   模型只吐一个 [[UNCERTAIN]] 哨兵。回不回由用户在「设置」里决定。
            if "[[UNCERTAIN]]" in answer:
                answer = answer.replace("[[UNCERTAIN]]", "").strip()
                st = {}
                try:
                    if getattr(self, "im_store", None) is not None:
                        st = self.im_store.settings() or {}
                except Exception:
                    st = {}
                if not st.get("reply_uncertain", True):
                    self.page_assistant.update_status(msg.get("msgid"), "需人工", "")
                    self._safe_asst_log(
                        "这条 AI 拿不准（身份背景里没有），按你的设置不代回，留给你自己回")
                    self._asst_done()
                    return
                answer = (str(st.get("uncertain_text") or "").strip()
                          or "消息已纳入收件箱，加急处理中，主人正在赶来的路上，"
                             "此消息由AI代回")
                self._safe_asst_log("AI 拿不准，已按兜底话术代回")
            self.page_assistant.update_status(msg.get("msgid"), "已回复", answer)
        except Exception as e:
            self._safe_asst_log(f"收尾失败：{e}")
            self._asst_worker = None
            self._asst_cur = None
            self._asst_runner = None
            self._asst_started = 0.0
            self._asst_recover(msg, f"收尾异常：{e}")
            self._asst_pump()
            return
        try:
            if self.assistant.cfg.get("auto_reply"):
                self._asst_reply(msg, answer, files)
            # 记录到 IM 流水（可审查）
            hub = getattr(self, "im_hub", None)
            if hub is not None and msg.get("cid"):
                self.im_store.append_message(
                    msg.get("cid"), direction="out", sender=self._ding_self_name(),
                    text=answer, ai=True, status="sent")
                # 顺手把"这个人让我做什么"记进本会话记忆，下次有上下文
                try:
                    self.im_store.remember(
                        f"对方让我：{str(msg.get('text'))[:60]}", msg.get("cid"),
                        scope="conv")
                except Exception:
                    pass
                self._im_refresh_views()
        except Exception as e:
            self._safe_asst_log(f"发送/记录失败：{e}")
            self._asst_recover(msg, f"发送失败：{e}")
        finally:
            self._asst_worker = None
            self._asst_cur = None
            self._asst_runner = None
            self._asst_started = 0.0
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
                    self._asst_log_ui(f"✓ 已把结果发回钉钉（{msg.get('conv') or '会话'}）")
                else:
                    self.ding.dws_dm(target_name, text)
                    self._asst_log_ui("✓ 已把结果作为单聊发给本人")
            except Exception as e:
                self._asst_log_ui(f"✕ 结果发回钉钉失败：{e}")
            for p in files[:5]:
                try:
                    if cid:
                        self.ding.dws_send_file(cid, p)
                    else:
                        self.ding.dws_send_file(target_name, p)
                    self._asst_log_ui(f"⊞ 已把文件发到钉钉：{os.path.basename(p)}")
                except Exception as e:
                    self._asst_log_ui(f"✕ 文件发送失败（{os.path.basename(p)}）：{e}")
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
                    self._asst_log_ui(f"⊞ 自动补发文件到钉钉：{os.path.basename(p)}")
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
                    self._asst_log_ui("✉ 自动补发结论到钉钉")
                    sent_any = True
                except Exception as e:
                    self._asst_log_ui(f"自动补发结论失败：{e}")
            if sent_any:
                self.wrapup_notice.emit(
                    "✉ 已自动补发到钉钉（你要求发钉钉、模型没发，程序兜底补发）")
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
        # ★ v9.19.0：runner 现在是"每个对话一份"，改设置要**全部**刷一遍，
        #   否则只在用的那个对话拿到了新 key，其它还在跑的对话拿的还是旧的。
        try:
            _keys = dict(self.settings.get("api_keys", {}))
            for _r in list(getattr(self, "_runs", {}).values()):
                if getattr(_r, "runner", None) is not None:
                    _r.runner.keys = dict(_keys)
            self.runner.keys = dict(_keys)     # 当前对话兜底（可能还没建过）
        except Exception:
            pass
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
            icon = "▨" if docread.is_image(a["path"]) else "▭"
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
        return ATTACH_MARK + "、".join(a["name"] for a in self.attachments)

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
            # ★ v9.19.0：历史里的「作废重来」标记必须清掉再喂模型。
            #   DeepSeek 网页通道曾把它当正文拼进回答，模型看到后会照着抄，
            #   于是用户就收到「第一步：…␜AWB-RESET␝我先看一下」这种鬼东西。
            content = config.strip_stream_reset(content)
            if role in ("user", "assistant", "system"):
                extra = m.get("attached") if role == "user" else None
                if extra:
                    content = content + "\n\n" + str(extra)
                out.append({"role": role, "content": content})
            elif role == "tool":
                out.append({"role": "user",
                            "content": "（上一步工具执行的真实结果）\n" + content})
        return out

    def _build_system_prompt(self, cid=None):
        """拼这一轮的系统提示。

        ★ v9.19.0：`cid` 决定带上**哪个对话**的记忆。老写法直接读
          `workspace.conv_memory_file()`（= 全局"当前对话"），用户在一轮发起后
          立刻切走，这一轮挂上来的就是**另一个对话**的记忆 —— 典型的"记忆串台"。
        """
        parts = [self.settings.get("system_prompt", "") or ""]
        try:
            block = self.memory.prompt_block()
            if block:
                parts.append(block)
        except Exception:
            pass
        # ★ v9.17.0：本对话自己的记忆也要带上（用户要求「每个对话一个文件夹，
        #   分别存放产物和记忆，还要有主记忆」——上面那块是主记忆，这块是本对话的）。
        try:
            cm = self.workspace.conv_memory_file(cid)
            if cm and os.path.isfile(cm):
                with open(cm, "r", encoding="utf-8") as f:
                    _txt = f.read().strip()
                if _txt:
                    parts.append("# 本对话的记忆（只属于当前这个对话，跨对话不共享）\n"
                                 "（要往这里记：remember(..., scope=\"conv\")；"
                                 "要记长期/跨对话的事：remember(..., scope=\"main\")）\n"
                                 + _txt)
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
                f"`{self.workspace.worklog_dir}` 下（不需要你操心）；\n"
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
                f"工作区目录：{self.workspace.path}；"
                f"文件沙箱：{self.workspace.files_dir_for(cid) if cid else self.workspace.files_dir}。"
                f"\n当前选择模型：{self._model_label_short().replace('模型：', '')}；"
                f"联网搜索：{'开' if self.settings.get('enable_search', True) else '关'}；"
                f"钉钉推送：{'可用' if self.ding.configured() else '未配置'}；"
                f"语音识别：{'可用' if _asr_ok(self.settings) else '不可用'}。")
        except Exception:
            pass
        return "\n\n".join(p for p in parts if p)

    def _on_plan_changed(self, plan, cid=None):
        # 计划面板显隐/变高会改变聊天区可视高度，Qt 会把滚动位置重置到顶部
        # （用户反馈："一弹出来对话就自动蹦到最顶端"）。这里先记住位置再还原。
        # ★ v9.18.0：计划写回**它自己的**那个对话；只有当前显示它时才动界面。
        cid = self._conv_cid() if cid is None else cid
        if self._is_current(cid):
            sb = self.chat_scroll.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 40
            old = sb.value()
            self.plan_panel.set_plan(plan)
            if at_bottom:
                sb.setValue(sb.maximum())
            else:
                sb.setValue(min(old, max(sb.maximum(), 0)))
        try:
            target = self.conv if self._is_current(cid) else \
                self.workspace.load_conversation(cid)
            if target is not None:
                target["plan"] = plan
                self.workspace.save_conversation(target)
                if self._is_current(cid):
                    self.conv = target
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
        # ★ v9.18.0（需求 7）：只拦"**这个对话**自己"在跑，别的对话在跑不影响它。
        cid = self._conv_cid()
        run = self._run(cid)
        if run.running:
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

        run.live_text = ""
        run.pending_tool = []
        # ★ v9.18.0（需求 17）：新一轮开始，上一轮残留的思考区清掉
        run.live_reason = ""
        run.reason_card = None
        # ★★ v9.18.0（需求 13）：这个对话选了团队 -> 本轮**交给团队去做**，
        #   不再走单人链路。放在这里（用户消息已入会话并落盘、运行态已清空之后）
        #   是为了让对话流里的顺序天然正确：先看到自己的消息，再看到团队的过程与汇总。
        try:
            _tid = self.team_bar.selected_team_id()
        except Exception:
            _tid = ""
        if _tid:
            self._send_team(text, _tid, cid, note)
            return
        api_messages = ([{"role": "system", "content": self._build_system_prompt(cid)}]
                        + self._api_messages(self.conv["messages"]))
        # ★ v9.19.0：这一轮用**绑定到本对话**的 runner（产物/记忆都锁在 cid 上）。
        #   不这么做的话，用户切走对话后，这一轮的产物会写进另一个对话的文件夹。
        _runner = self._make_runner(cid)
        run.runner = _runner           # 存进**这个对话**的运行态（不是全局共享的那份）
        self.worker = ChatWorker(
            self.client, _runner, api_messages,
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
        # ★ 所有回调都带上 cid：回调回来时"当前对话"可能已经被用户切走了，
        #   必须按 cid 把结果写回它**自己的**那一份运行态（而不是当前显示的这个）。
        self.worker.token.connect(lambda d, c=cid: self._on_token(d, c))
        # ★ v9.18.0（需求 17）：思维链增量 —— 边想边显示，别让用户对着转圈干等
        self.worker.reason.connect(lambda d, c=cid: self._on_reason(d, c))
        self.worker.tool_start.connect(lambda s, c=cid: self._on_tool_start(s, c))
        self.worker.tool_result.connect(lambda r, c=cid: self._on_tool_result(r, c))
        self.worker.finished.connect(lambda m, c=cid: self._on_finished(m, c))
        self.worker.error.connect(lambda m, c=cid: self._on_error(m, c))
        self.worker.confirm_requested.connect(
            lambda cmd, c=cid: self._on_confirm(cmd, c))
        # ★ v9.17.0：AI 主动提问（用户要求"有什么不懂的一定不要猜"）
        self.worker.ask_requested.connect(
            lambda q, o, h, m, c=cid: self._on_ask_user(q, o, h, m, c))
        self.worker.ask_enabled = True      # 有界面在听 → 提问才会真的弹框
        self.worker.plan_changed.connect(lambda p, c=cid: self._on_plan_changed(p, c))
        self.worker.notice.connect(lambda t, c=cid: self._on_notice(t, c))
        self.worker.wrapup.connect(lambda p, c=cid: self._on_wrapup(p))
        self.worker.phase.connect(lambda k, d, c=cid: self._on_phase(k, d, c))
        run.running = True
        # 生成中：右下角按钮变成红色方形「停止」，点一下就能打断模型
        if self._is_current(cid):
            self._set_stop_mode(True)
            # 选中网页版时，状态行显示"正在等待 DeepSeek 网页输出"；否则旧的"调用模型"
            self.state_line.set_phase(
                "web" if run.dsw_selected else "model", "")
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

    def _interrupt(self, cid=None):
        """打断**当前对话**的生成（别的对话在跑不受影响）。"""
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)
        # ★ v9.18.0（需求 13）：这一轮若是团队在干活，得取消 TeamRun ——
        #   它没有 worker，`run.worker` 是 None，老逻辑会直接 return，
        #   于是"点了停止没反应、队伍继续跑"。`TeamRun.cancel()` 会置取消事件
        #   并连带取消所有还在跑的成员子任务，各阶段循环每步都会检查它。
        if run.team_run is not None:
            try:
                run.team_run.cancel()
            except Exception:
                pass
            if self._is_current(cid):
                self.state_line.set_phase("stop", "正在收尾")
                self.send_btn.setEnabled(False)
                self.send_btn.setText("…")
                self.statusBar().showMessage("已停止团队，正在收尾…", 4000)
            return
        w = run.worker
        if w is None or not run.running:
            return
        try:
            w.abort()
        except Exception:
            pass
        if self._is_current(cid):
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

    def _on_phase(self, kind, detail, cid=None):
        """模型阶段变化 -> 状态行（只关心当前显示的这个对话）。"""
        if cid is not None and not self._is_current(cid):
            return
        try:
            if kind == "tool":
                detail = tool_verb(detail)
            self.state_line.set_phase(kind, detail or "")
        except Exception:
            pass

    def _on_notice(self, text, cid=None):
        if cid is not None and not self._is_current(cid):
            return
        self.statusBar().showMessage(text[:160], 12000)

    def _on_token(self, delta, cid=None):
        """流式增量：写进**它自己**那个对话的运行态；只有它是当前显示时才刷界面。"""
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)

        # ★★ v9.18.0（需求 4「有的时候会出现生成内容有点重复」）——
        #   收到后台发来的"作废重来"标记：后台换了端点/模型重试，或者 DeepSeek 网页
        #   把整段答案**重写**了。客户端只会往后拼、不会撤回，所以必须在这里把自己
        #   攒的那半截清空 —— 否则界面上会「半截残留 + 完整新文本」叠一起 = 内容重复。
        if config.is_stream_reset(delta):
            run.live_text = ""
            run.live_card = None
            # 作废 = 这一版全不算数：正在滚的思维链也一起清掉，
            # 否则重试后新推理会叠在旧推理后面（就是那条"内容重复"的老毛病）。
            run.live_reason = ""
            run.reason_card = None
            if self._is_current(cid):
                self._render_sig = None          # 指纹失效，防止被渲染短路挡掉
                self._render()
            return

        run.live_text += delta
        if not self._is_current(cid):
            return          # 用户已经切到别的对话了 —— 收着，别去动界面
        if not self._render_timer.isActive():
            self._render_timer.start(self._live_interval)

    def _on_reason(self, delta, cid=None):
        """思维链增量（v9.18.0，需求 17）—— 边想边显示。

        用户原话：「模型思考慢是不是因为我只看见了他思考后的结果，中间思考过程
        没呈现给我？这个也可以加进来」。确实：带思维链的模型在正式回答前会先
        流式吐一大段 `reasoning_content`，老实现只是悄悄收着，用户对着转圈干等。

        机制和正文 token 完全一致：写进 **cid 那个对话** 的运行态，只有它当前
        可见时才刷界面；节流复用同一个 `_render_timer`（思考比正文还费串口，
        不额外开一个定时器）。
        """
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)
        if not delta:
            return
        run.live_reason += delta
        if not self._is_current(cid):
            return
        if not self._render_timer.isActive():
            self._render_timer.start(self._live_interval)

    def _update_live(self):
        # ⚠ 流式刷新时**不能强制贴底**：否则用户往上翻看历史时，会被
        #    每 80ms 一次的强制滚动拽回底部（用户 2026-09-30 反馈
        #    「AI 生成的时候我没办法往上翻」）。
        #    改成"只在自己本来就贴着底时才跟随" —— 用户主动上滑后就不再打扰，
        #    等他滚回底部，_scroll_to_bottom 的贴底判断会自动恢复跟随。
        live_shown = strip_tool_markup(self.live_text) if self.live_text else ""
        reason_shown = (self.live_reason or "").strip()
        touched = self._update_live_cards(live_shown, reason_shown)
        if touched is None:
            # 该有的卡片还没造出来（例如第一句思考刚到）→ 走一次全量渲染建卡
            self._render_full()
            return
        if touched:
            # 卡片高度刚变，滚动条最大值要等布局刷新后再取 -> 延后一帧再跟随
            QTimer.singleShot(0, lambda: self._scroll_to_bottom(False))

    def _update_live_cards(self, live_shown, reason_shown):
        """流式期间**原地**刷新「正文卡」和「思考卡」（需求 6 + 需求 17 共用）。

        返回：list = 这次真的变了的卡片（调用方据此只 refit 这几张）；
              None = 情况不匹配（卡片还不存在 / 已经不该存在了），调用方应全量渲染。
        """
        # 正文在长但正文卡还没造出来（或反过来：卡还在、正文已经清空）→ 交给全量渲染
        if bool(live_shown) != (self._live_card is not None):
            return None
        # 思考同理
        if bool(reason_shown) != (self._reason_card is not None):
            return None
        touched = []
        if live_shown:
            # ★ v9.18.0（需求 6「模型响应较慢」）：内容一个字都没变就直接不做 ——
            #   流式里常有整段都是工具调用标记的增量，strip 掉之后正文没长，
            #   老代码照样重新渲染一遍（Pygments 高亮是纯 CPU 硬活）。
            if getattr(self._live_card, "_raw", None) != live_shown:
                t0 = time.perf_counter()
                prose, blocks = markdown_render.render_with_blocks(live_shown)
                self._live_card.set_content(prose, blocks)
                self._tune_live_interval(time.perf_counter() - t0)
                touched.append(self._live_card)
        if reason_shown:
            if self._reason_card.set_text(reason_shown):
                touched.append(self._reason_card)
        return touched

    def _tune_live_interval(self, cost_seconds):
        """按上一次渲染的真实耗时，自适应调整流式刷新节拍（需求 6）。

        为什么不能写死 80ms：短回复时 80ms 很流畅，但正文里带一个几百行的代码块时，
        读一次 Pygments 高亮可能就要 50~150ms —— 还按 80ms 排，UI 线程会被排满，
        用户体感就是"整个界面卡住、字一个个往外蹦"。
        节拍跟着耗时走：便宜就快（110ms），贵就主动降到最多 400ms，
        宁可少刷几帧也不让界面失去响应。
        """
        if cost_seconds < 0.03:
            self._live_interval = 110
        else:
            self._live_interval = max(110, min(400, int(cost_seconds * 1000 * 3)))

    def _on_tool_start(self, desc, cid=None):
        if cid is not None and not self._is_current(cid):
            return
        self.statusBar().showMessage("正在执行工具：" + desc[:130])

    def _on_tool_result(self, result, cid=None):
        # 工具前的「中间话」不再丢弃：先作为折叠的「深度思考」块留在本轮记录里
        # （整轮结束后 chat_worker 也会把它作为 role="thinking" 交回来，pending 只是
        #   本轮的即时展示，_on_finished 会清空 pending，不会重复落进对话记录。）
        # ★ v9.18.0：一律写进 **cid 那个对话** 的运行态，界面只在它当前可见时刷新。
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)
        _think = strip_tool_markup(run.live_text).strip() if run.live_text else ""
        if _think:
            run.pending_tool.append({"role": "thinking", "content": _think})
        run.live_text = ""
        run.live_card = None
        run.pending_tool.append({"role": "tool", "content": result})
        if not self._is_current(cid):
            return
        self.statusBar().clearMessage()
        self._refresh_chips()
        # 工具结果到达也不强制贴底：用户可能正往上翻（同上）。
        self._render()

    def _on_finished(self, new_messages, cid=None):
        """一轮跑完：结果写回**它自己的**对话（哪怕用户已经切走了）。"""
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)
        # ★ v9.19.0：落盘前先清掉「作废重来」标记残骸 —— 否则它会留在历史里，
        #   下一轮又原样喂给模型（实测 DeepSeek 网页通道会把它当正文抄回来）。
        msgs = []
        for _m in (new_messages or []):
            _m = dict(_m)
            if isinstance(_m.get("content"), str):
                _m["content"] = config.strip_stream_reset(_m["content"])
            if not (_m.get("content") or "").strip() and _m.get("role") in (
                    "tool", "thinking"):
                continue
            msgs.append(_m)
        try:
            label = self.client.last_used_label()
        except Exception:
            label = ""
        if label:
            run.last_model_label = label
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "assistant":
                    msgs[i]["_model"] = label
                    break
        # ★ v9.18.0：写回 cid 对应的那个对话，而不是"当前显示的这个" ——
        #   老写法在这里用 self.conv，用户中途切走就会把 A 的回复塞进 B 里。
        target = self.conv if self._is_current(cid) else None
        if target is None:
            try:
                target = self.workspace.load_conversation(cid)
            except Exception:
                target = None
        if target is not None:
            target.setdefault("messages", []).extend(msgs)
            try:
                self.workspace.save_conversation(target)
            except Exception:
                pass
            if self._is_current(cid):
                self.conv = target
        run.live_text = ""
        run.pending_tool = []
        run.live_card = None
        # ★ v9.18.0（需求 17）：整轮结束 —— 实时思考区撤掉。
        #   这一轮的思维链已经由 chat_worker 折成一条 role="thinking" 落进
        #   msgs（上面刚写回对话），所以不会丢，只是从"实时滚动"变成
        #   历史里的「深度思考」块（需求 9：过程消息默认折进过程组）。
        run.live_reason = ""
        run.reason_card = None
        self._finish_run(cid)
        if self._is_current(cid):
            self._maybe_rename()
            if self.settings.get("tts_enabled"):
                last_ai = next((m["content"] for m in reversed(msgs)
                                if m.get("role") == "assistant"), "")
                if last_ai:
                    self.speaker.speak(config.strip_stream_reset(last_ai))

    def _on_error(self, msg, cid=None):
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)
        # ★ v9.18.0（需求 4）：出错时也要把流式那半截正文清掉 ——
        #   `_render_full` 只要看到 live_text 非空就会把它再画成一张卡片，
        #   不清的话就会和上一轮已经落盘的内容叠成"看起来重复"。
        run.live_text = ""
        run.live_card = None
        run.live_reason = ""
        run.reason_card = None
        run.pending_tool.append({"role": "tool", "content": "出错了：" + str(msg)})
        self._finish_run(cid)

    def _finish_run(self, cid=None):
        cid = self._conv_cid() if cid is None else cid
        run = self._run(cid)
        run.running = False
        if not self._is_current(cid):
            # 别的对话跑完了：只更新列表上的标记，别去打扰当前这个界面
            try:
                self._load_conversations()
            except Exception:
                pass
            return
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

    def _refresh_run_ui(self):
        """切换对话后：把界面控件（停止键 / 状态行 / 输入框）同步成**这个对话**的样子。"""
        try:
            run = self._run()
            if run.running:
                self._set_stop_mode(True)
                # ★ v9.18.0（需求 13）：团队在跑时状态行要说"团队协作中"，
                #   而不是笼统的"正在调用模型"（团队里其实是好几个模型在轮流跑）。
                if run.team_run is not None:
                    self.state_line.set_phase("team", "")
                else:
                    self.state_line.set_phase(
                        "web" if run.dsw_selected else "model", "")
            else:
                self._set_stop_mode(False)
                self.state_line.set_phase("idle", "")
                self.send_btn.setEnabled(bool(self.input.toPlainText().strip())
                                         or bool(self.attachments))
        except Exception:
            pass

    # ---------- ★ v9.17.0 AI 主动提问 ----------
    def _on_ask_user(self, question, options, header, multi, cid=None):
        """AI 通过 `ask_user` 提问 → 弹一张卡片，把答案回传给正在等的工具线程。

        工具在 worker 线程里阻塞等待（见 `ChatWorker._ask_cb_impl`），这里在 UI
        线程开一个模态对话框；用户答完 / 跳过之后唤醒它。
        **点跳过或直接关掉 = 没回答**，工具会退化成"按最合理的假设继续并在结论里
        注明"，所以不会把整轮对话卡死。

        ★ v9.18.0：答案回传给 **cid 那个对话的 worker**（不能拿 `self.worker`，
        用户可能已经切到别的对话了）。
        """
        ans = ""
        try:
            dlg = AskUserDialog(question, list(options or []), header or "",
                                bool(multi), parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                ans = dlg.answer or ""
        except Exception as exc:                      # noqa: BLE001
            try:
                self.statusBar().showMessage(f"提问框弹出失败：{exc}", 5000)
            except Exception:
                pass
        try:
            self._run(cid).worker.ask_answer(ans)
        except Exception:
            pass

    def _on_confirm(self, cmd, cid=None):
        # ★ v9.18.0：一律回给 **cid 那个对话的 worker**（用户可能已经切走）。
        w = self._run(cid).worker
        # ① 设置里关掉了确认 -> 直接放行（危险命令在 agent 层已被拦截）
        if not self.settings.get("confirm_commands", True):
            w.confirm(True)
            return
        # ② 只读命令不问：查版本、列目录、看状态这类，每问一次都是打扰
        if _is_readonly_command(cmd):
            w.confirm(True)
            return
        # ③ 本次会话已经点过"全部允许"
        if getattr(self, "_confirm_all_session", False):
            w.confirm(True)
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
        w.confirm(clicked in (yes, allb))

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
        """去掉「附件的标注行」，只留用户真正打的字。"""
        c = str((msg or {}).get("content") or "")
        lines = [ln for ln in c.split("\n") if not ln.startswith(ATTACH_MARK)]
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
        self._groups = []
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                if hasattr(w, "stop"):
                    w.stop()
                # ★★ v9.17.0 修「弹一堆小窗口」：
                #   `setParent(None)` 会把控件**变成顶层窗口**，而且它此刻还是
                #   **可见**的 —— Qt 会让它立刻以独立窗口的身份显示出来（任务栏上
                #   一闪而过），直到 deleteLater() 真正销毁它才消失。
                #   每次重建都会对**所有**旧卡片做一遍，所以消息越多、弹得越多，
                #   用户看到的就是"一执行任务就弹一堆小窗，又很快消失"。
                #   → 关键一步：**先 hide() 再 setParent(None)**，
                #     这样它脱离父子关系时是隐藏状态，不会再变成可见的顶层窗口。
                w.hide()
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

    # ---------- 分页渲染（v9.20.0，见 CHAT_PAGE_SIZE） ----------
    def _page_fold_for(self, n):
        """算这次渲染要在开头折掉多少条（0 = 全部显示）。

        `_fold_auto` 只在"刚切进/新建一个对话"时置 True：那时按"只显示最近
        一页"算一次 fold，之后就一直沿用用户当前的窗口（点过"加载更早"就更大），
        不会因为来了一条新消息就把用户刚展开的历史又收回去。
        """
        if getattr(self, "_fold_auto", False):
            self._fold_auto = False
            self._fold_n = max(0, int(n) - CHAT_PAGE_SIZE)
        fold = int(getattr(self, "_fold_n", 0) or 0)
        # 兜底：不越界；也不允许把最后一条都折掉（否则界面空白）
        fold = max(0, min(fold, max(0, int(n) - 1)))
        self._fold_n = fold
        return fold

    def _make_load_earlier(self, fold):
        """顶部「△ 加载更早的 N 条」按钮（折起来的消息数 = fold）。"""
        t = themes.tokens()
        n = min(CHAT_PAGE_SIZE, fold)
        btn = QPushButton(f"△ 加载更早的 {n} 条（还有 {fold} 条）")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:{t['chip_bg']};color:{t['text_muted']};"
            f"border:1px solid {t['border']};border-radius:10px;"
            f"font-size:{themes.fs(12)};padding:5px 12px;}}"
            f"QPushButton:hover{{color:{t['accent_text']};"
            f"border:1px solid {t['accent']};}}")
        btn.clicked.connect(self._load_earlier)
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 4, 0, 6)
        lay.addStretch(1)
        lay.addWidget(btn)
        lay.addStretch(1)
        return host

    def _load_earlier(self):
        """往前再铺一批，并**保持当前看到的位置**（不能跳到顶）。

        做法：记住"距底部还有多远"，插入更早的内容后按同样距离还原 ——
        上面的内容变高了，但用户视线里那几条消息仍在原地。
        """
        self._fold_n = max(
            0, int(getattr(self, "_fold_n", 0) or 0) - CHAT_PAGE_SIZE)
        sb = self.chat_scroll.verticalScrollBar()
        self._pending_scroll_off = max(0, sb.maximum() - sb.value())
        self._render_full(stick=False)

    def _render_full(self, stick=None):
        # 重建前先记住滚动状态：stick=True 强制贴底；否则沿用"当前是否贴底"。
        # 这就是「一发新消息就跳到最上面」的根因修复 —— 重建后延后一帧再恢复。
        sb = self.chat_scroll.verticalScrollBar()
        at_bottom = True if stick is True else (sb.value() >= sb.maximum() - 40)
        old_value = sb.value()

        plan = (self.conv or {}).get("plan")
        display = list(self.conv.get("messages", [])) if self.conv else []
        for t in self.pending_tool:
            display.append(t)
        live_shown = strip_tool_markup(self.live_text) if self.live_text else ""
        # ★ v9.18.0（需求 17）：正在实时滚动的思维链，和正文一样参与渲染指纹
        reason_shown = (self.live_reason or "").strip() if self.running else ""
        # ★★ v9.20.0 分页渲染（见 CHAT_PAGE_SIZE）：只铺最近一页，开头折起来的部分
        #   由顶部「加载更早」按钮按批放出。`_fold_auto` 只在"刚切进一个对话"时
        #   为 True，之后新增消息不会把用户已展开的窗口重新收回去。
        fold = self._page_fold_for(len(display))
        visible = display[fold:]

        # ★★ v9.17.0 渲染短路 + 流式增量 —— 修用户反馈的「一执行任务就很卡、
        #    弹一堆小窗口」。
        #    老逻辑：每次 _render_full 都把聊天区**所有**卡片删掉重建；
        #    流式输出时这个函数每秒被调十几次，消息一多就卡，而且每张旧卡片
        #    在 setParent(None) 时还会闪成小窗口（见 _clear_chat）。
        #    现在两道拦截：
        #      ① 整块内容一模一样 → 直接什么都不做；
        #      ② 只有流式正文 / 实时思考在长、其余没变 → 只原地刷新那两张卡。
        base_sig = tuple((m.get("role"), m.get("content", "")) for m in visible)
        sig = (base_sig, bool(self.running),
               live_shown if self.running else "", str(plan), reason_shown, fold)
        prev = getattr(self, "_render_sig", None)
        if prev == sig:
            return
        if (prev is not None and prev[0] == base_sig and prev[1] == bool(self.running)
                and prev[3] == str(plan) and self.running
                and not self.pending_tool):
            touched = self._update_live_cards(live_shown, reason_shown)
            if touched is not None:
                self._render_sig = sig
                self._pending_scroll = (at_bottom, old_value)
                if touched:
                    # ★ 需求 6：这条路径只有这两张卡变了，refit 它们就够
                    self._pending_refit = touched
                    QTimer.singleShot(0, self._after_render)
                return

        self._clear_chat()
        self._cards = []
        self._groups = []
        self._live_card = None
        self._reason_card = None
        try:
            self.plan_panel.set_plan(plan)
        except Exception:
            pass

        if not display and not self.running:
            self._stack_welcome()
            self._render_sig = sig
            return

        # ★ v9.20.0：还有更早的消息没铺 → 顶部先放一个"加载更早"的按钮。
        #   它不是卡片，不进 `_cards`（不需要 refit），点一次往前放一批。
        if fold > 0:
            self.chat_layout.addWidget(self._make_load_earlier(fold))

        # ★ v9.18.0（需求 9）：相邻的**过程消息**（工具调用 / 普通思考）合并成
        #   一个 ProcessGroup，默认隐藏，只留一行「⚙ 过程 · N 步」。
        #   带结论的关键中间块（emphasis）仍然单独留在外面可见 —— 兼顾
        #   "过程都藏起来"与"重点结论要留着"这两条要求。
        group = None

        for i, m in enumerate(visible, fold):
            role = m.get("role")
            content = m.get("content", "") or ""
            emphasis = bool(role == "thinking" and agent_mod.is_key_progress(content))
            in_group = bool(role == "tool") or bool(role == "thinking" and not emphasis)

            if in_group:
                if group is None:
                    # ★ 折叠状态键用**消息身份**（id(m)）而不是下标 ——
                    #   删掉/插入一条中间消息时下标会整体平移，用下标会让展开态
                    #   "跑"到别的组上（老 MessageCard 也是这么做的，这里对齐）。
                    gkey = ("grp", id(m), str(role))
                    saved_g = self._card_collapsed.get(gkey)
                    group = ProcessGroup(
                        collapsed=True if saved_g is None else bool(saved_g))
                    group.setContentsMargins(0, 2, 0, 0)
                    group.collapsed_changed.connect(
                        lambda collapsed, k=gkey:
                            self._card_collapsed.__setitem__(k, bool(collapsed)))
                    self.chat_layout.addWidget(group)
                    self._cards.append(group)
                    self._groups.append((group, gkey))
                card = self._build_card(m, i, role, content,
                                        emphasis=False, collapsed=False)
                if card is not None:
                    group.add_card(card)
                    self._cards.append(card)
                continue

            # 正式消息：过程组在这里断开
            group = None
            saved = self._card_collapsed.get(self._card_key(m, i, role))
            card = self._build_card(m, i, role, content,
                                    emphasis=emphasis, collapsed=saved)
            if card is not None:
                self.chat_layout.addWidget(card)
                self._cards.append(card)

        # ★ v9.18.0（需求 17）：正在实时滚动的思维链 —— 放在正文**之上**。
        #   默认展开（用户要的就是"看见它在想什么"），高度封顶 300px，
        #   免得一大段推理把下面的正式答复远远挤出屏幕。
        if reason_shown:
            _rkey = ("reason", "__live__")
            _rsaved = self._card_collapsed.get(_rkey)
            rc = ReasoningCard(
                reason_shown,
                collapsed=False if _rsaved is None else bool(_rsaved),
                max_h=300)
            rc.collapsed_changed.connect(
                lambda collapsed, k=_rkey:
                    self._card_collapsed.__setitem__(k, bool(collapsed)))
            self.chat_layout.addWidget(rc)
            self._cards.append(rc)
            self._reason_card = rc

        if self.running and not self.live_text and not self.pending_tool:
            ind = ThinkingIndicator()
            self.chat_layout.addWidget(ind)
            self._cards.append(ind)
        elif live_shown:
            prose, blocks = markdown_render.render_with_blocks(live_shown)
            card = MessageCard("assistant", prose, blocks, raw_text=live_shown,
                               actions=False)
            self.chat_layout.addWidget(card)
            self._cards.append(card)
            self._live_card = card

        self.chat_layout.addStretch(1)
        # 此刻布局还没最终确定，直接读滚动条最大值不准 —— 延后一帧再恢复位置
        self._pending_scroll = (at_bottom, old_value)
        # ★ 需求 6：全量重建过，所有卡片都要重新撑高（宽度这时才确定）
        self._pending_refit = list(self._cards)
        self._render_sig = sig          # ★ 记住这次渲染的指纹（供上面的短路判断用）
        QTimer.singleShot(0, self._after_render)

    def _build_card(self, m, i, role, content, emphasis=False, collapsed=None):
        """按一条消息造一张卡片（并接好它的各种信号）。造不出来返回 None。"""
        footer = ""
        if role == "assistant" and m.get("_model"):
            footer = "实际使用模型：" + m["_model"]
        label = tool_label(content) if role == "tool" else None
        stats = file_write_stats(content) if role == "tool" else None
        card = MessageCard.from_text(role, content, footer_text=footer,
                                     label=label, index=i,
                                     stats=stats, collapsed=collapsed,
                                     emphasis=emphasis)
        # 分组留白：过程卡片贴紧，正式消息之间留出呼吸感
        card.setContentsMargins(
            0, 1 if role in ("tool", "thinking") else 7, 0, 0)
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
        return card

    def _copy_msg(self, m):
        QGuiApplication.clipboard().setText(m.get("content") or "")
        self.statusBar().showMessage("已复制到剪贴板", 2500)

    def _after_render(self):
        """布局落定后撑开卡片并按需要恢复滚动位置。

        修的是用户反馈的「一弹出来 / 一发新消息就自动跳到最顶端」：
        重建卡片后先让布局重新计算高度，再贴底或还原旧位置。

        ★ v9.18.0（需求 6「模型响应较慢」）：只 refit **这一次真的变过的**卡片。
          · 全量重建 → 所有卡片（宽度这时才确定，必须都过一遍）；
          · 流式增量 → 只有最后那张 live 卡片变了。
          老代码不看场景、几十张卡片每次都全量 `refit()`，长对话里纯属白烧 CPU。
        """
        try:
            self.chat_layout.activate()
            self.chat_container.adjustSize()
        except Exception:
            pass
        targets = getattr(self, "_pending_refit", None)
        if targets is None:
            targets = self._cards
        for c in targets:
            if hasattr(c, "refit"):
                c.refit()
        self._pending_refit = None
        self._restore_scroll()

    def _restore_scroll(self):
        sb = self.chat_scroll.verticalScrollBar()
        # ★ v9.20.0：点了「加载更早」→ 按"距底部距离"还原，视线不跳
        off = getattr(self, "_pending_scroll_off", None)
        if off is not None:
            self._pending_scroll_off = None
            sb.setValue(max(0, sb.maximum() - int(off)))
            return
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
