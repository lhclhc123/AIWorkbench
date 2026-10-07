"""v9.16.0 打包：多任务并行 + 团队协作（让慢活儿不再堵住对话）。

【用户原话】
  「给我这个 AI 添加多任务同时执行这种能力。就是说，模型在跑我本地命令的时候
    不影响它模型继续思考，或者说继续弄别的东西，也可以多条链路同时执行。
    再给它增加一个像 WorkBuddy 这样的团队的功能，各个工程师这种什么产品经理啊
    这种独立跑，然后不影响主进程，你把各个东西都协调做好。」

【老版本的病根】
  `run_command` / `run_python` / `spawn_agent` 全是**当场阻塞**的 ——
  模型发一条 3 分钟的打包命令，整条 agent 循环就干等 3 分钟，既不能思考也不能
  干别的，用户看到的就是"它卡在那儿不动"。同一轮里的多个工具也是逐个串行执行。

【本版新增（全部零侵入：老工具签名、界面结构、工具调用协议都没改）】
1. `app/taskman.py` —— 后台任务池（★ 新模块）
   · submit() 立刻返回 Task，绝不阻塞调用方；每个任务一条 daemon 线程；
   · 并发上限默认 4，超出的排队；协作式取消（连子进程一起 taskkill /T）；
   · 状态/日志/产物持久化到 workspace/bgtasks/*.json，重启后还能看到历史；
   · `nested=True` 给"团队派生的子任务"用 —— 否则父任务占着并发槽等子任务、
     子任务在队列里等槽，**会死锁**（max_running=2 时必现）。
2. `app/team.py` —— 多角色团队（★ 新模块）
   产品经理 → 架构师 →（多个工程师 **真并行**）→ 测试 → 组长；
   每个角色独立工作目录、独立会话；产物在 workspace/team/<run_id>/（含 report.md）。
   架构师给的任务 JSON 解析失败时有**兜底任务**，绝不让整支队伍停摆。
3. 七个新工具（KNOWN_TOOLS 54 -> 61）
   run_bg / bg_list / bg_status / bg_wait / bg_cancel / team_run / team_status
   ⚠️ 名字刻意避开 list_tasks / cancel_task / run_task（那是"定时任务"占着的）。
   run_bg 一到就返回任务号，模型可以继续思考 —— 这就是用户要的那件事。
   bg_wait 的等待上限是 120 秒：等太久又把对话堵住了，那就白做了。
4. 一轮内多个工具**并行执行**（chat_worker）
   只读无副作用的（读文件/列目录/抓网页/查系统/github 读）丢线程池并行跑；
   写入类、需确认的、有共享状态的仍然串行；结果按下标存好、仍按原顺序做后处理 ——
   fail_counts / write_done / verified_after_write 的语义**完全没变**。
   两道护栏：同一个显式 session 的网页请求不并行（requests.Session 非线程安全）；
   trace 写入加了模块级锁。
5. 两个新页面：⚡ 并行任务 / 👥 团队（页面数 9 -> 11）
   后台页可看每条链路的实时日志与产物、一键叫停；团队页可看每个角色的卡片与汇总。
   后台页还有「允许后台任务执行本地命令」开关（**默认关**：后台没有界面弹确认框，
   所以默认拒绝 run_command / 删除，要跑代码请用 run_bg kind=python）。
6. 内置技能 `parallel-and-team`「并行任务与团队协作」（内置技能 10 -> 11）
   `PROMPT_VERSION` 26 -> 27。

【真机验证】
  · probe_taskman.py  —— 后台任务池 8/8（含"submit 立即返回"、"两条链路真并行"、
    "超并发上限会排队"、"取消能杀掉子进程"）
  · probe_team.py     —— 团队编排 15/15（两个工程师并行、目录隔离、解析兜底、落盘）
  · probe_parallel_v9160.py —— 离屏全链路 **全绿**：
      run_bg 提交 6 秒命令 < 1.5s 返回 + 期间还能调别的工具 + 真拿到 start/end 输出；
      一轮 3 个只读工具走并行、写入类不并行、同 session 不并行；
      团队 6 个角色卡片齐全、report.md 落盘；后台命令默认被拒 / 勾选后放行。
  · 其余离线回归全绿：parser_cases 73/73、webcap 10/10、agent_webcap 15/15、
    ui_v913、scroll_stick、voice_silence、deepseek_web、gui_web、autolaunch、smoke。

【为什么这一版重打了 3 次（教训，下次直接省掉两轮）】
  第 1 次：功能做完就打包。
  第 2 次：跑**冻结包自检**时发现 `main.py` 的「应用模块导入」清单是**手写**的 ——
          文案写「26 个」、实际列了 27 个，而且漏了 skills / assistant / winproc /
          webcap / hotkey / scheduler 以及本版新增的 taskman / team，
          等于这一项自检**从没验证过**这些模块能不能导入。
          改成「全量清单 + len() 动态计数 + pkgutil 扫描交叉核对」→ 重打。
  第 3 次：新清单立刻抓到一个真问题：**冻结包里没有 `app.gui.settings_dialog`**。
          查证后确认它是**历史残留孤儿**（产品代码零引用，设置界面早就改成
          pages.SettingsPage 了），PyInstaller 不打包它**是对的** —— 是清单列错了。
          加进 `_IGNORE_MODS` → 再重打。
  ⇒ 两条结论：① **动 `main.py` 也属于动源码**，改完必须重打，否则包里和源码不一致；
             ② 「自检通过」不等于「没问题」，也可能是**自检范围不够** ——
                数字/清单与自己的认知不符时，先怀疑自检本身。

⚠️ 打包铁律：
- PyInstaller 在 Analysis 阶段就快照源码，**打包期间绝对不能再改代码**。
- 每次重打前先把旧 build/ 用 mv 挪走（沙箱同 turn 删 >50 文件会被拦）。
- PyInstaller 本体不进包。
"""
import os
import sys
import subprocess

PROJ = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJ)

sys.path.insert(0, PROJ)
from app import version as ver  # noqa: E402

hidden = [
    "PyQt6.sip",
    "markdown.extensions.tables",
    "markdown.extensions.nl2br",
    # 文档解析（读）
    "docx", "openpyxl", "pdfminer", "pdfminer.high_level",
    "bs4", "lxml", "requests",
    # 文档生成（写）
    "pptx", "reportlab", "reportlab.pdfbase._cidfontdata",
    "reportlab.pdfbase.cidfonts", "reportlab.platypus",
    "reportlab.pdfbase.ttfonts",
    # 系统交互
    "psutil", "pyperclip", "mss", "PIL", "PIL.Image", "PIL.ImageGrab",
    # v9：语音
    "pyaudio", "edge_tts", "edge_tts.communicate", "edge_tts.voices",
    # v9：钉钉
    "dingtalk_stream", "dingtalk_stream.chatbot", "dingtalk_stream.client",
    "websocket",
    # v9：Windows 系统能力（COM / 剪贴板 / DPAPI）
    "win32com", "win32com.client", "win32com.client.dynamic",
    "pythoncom", "pywintypes", "win32api", "win32con", "win32timezone",
    # v9.14.0：DeepSeek 网页版接入（惰性/间接导入，静态分析易漏，全部显式补）
    "app.deepseek_web",
    "app.deepseek_web.errors",
    "app.deepseek_web.prompt",
    "app.deepseek_web.cdp_client",
    "app.deepseek_web.web_driver",
    "app.deepseek_web.local_server",
    "app.deepseek_web.service",
    # v9.15.0：网页采集 / 批量表单查询 / GitHub（agent 里是延迟 import，静态分析抓不到）
    "app.webcap",
    # v9.16.0：并行任务池 / 多角色团队（同样是延迟 import）
    "app.taskman",
    "app.team",
    # 注意：build_exe 工具是靠「外部解释器 python -c "import PyInstaller"」探测的，
    # PyInstaller **不需要**打进本包（打进反而会拖进一堆开发依赖）-> 见下方 exclude。
]

collect_data = ["docx", "pptx", "reportlab", "pdfminer", "lxml"]
collect_sub = ["pdfminer", "reportlab", "pptx", "docx", "openpyxl",
               "edge_tts", "dingtalk_stream", "win32com"]

exclude = [
    "numpy", "matplotlib", "cv2", "tkinter", "moviepy", "imageio",
    "pandas", "scipy", "IPython", "notebook",
    "PyQt6.QtWebEngineCore", "PyQt6.QtQuick", "PyQt6.QtMultimedia",
    # 本机同时装了 PyQt5，一个冻结程序里混入多种 Qt 绑定会 abort
    "PyQt5", "PyQt5.sip", "PySide2", "PySide6", "qtpy",
    # 本机装了 pygame（别的项目用的），会被某个包的可选导入顺手拖进来
    "pygame", "pygame.locals",
    # pywin32 的 IDE 外壳（mfc140u.dll + win32ui.pyd，7MB）
    "Pythonwin", "pythonwin", "pywin32_testutil", "win32ui",
    "win32com.test", "win32com.test.util",
    # ⚠️ PyInstaller 本体绝不能进包（会拖进一堆开发依赖，且没必要）
    "PyInstaller", "pylint", "pytest", "setuptools.command",
]

DIST = os.path.join(PROJ, "dist_v9160")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9160_version.txt")


def make_version_file():
    v = ver.VERSION_TUPLE
    while len(v) < 4:
        v = tuple(v) + (0,)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple(v)},
    prodvers={tuple(v)},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'AIWorkbench (个人项目)'),
        StringStruct('FileDescription', 'AI 工作台 —— 本地 AI Agent 桌面应用'),
        StringStruct('FileVersion', '{ver.VERSION}'),
        StringStruct('InternalName', 'AIWorkbench'),
        StringStruct('LegalCopyright', '免费开源 · 仅供个人学习使用'),
        StringStruct('OriginalFilename', 'AIWorkbench.exe'),
        StringStruct('ProductName', 'AI 工作台'),
        StringStruct('ProductVersion', '{ver.VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    return VERSION_FILE


args = [
    sys.executable, "-m", "PyInstaller",
    "--name", "AIWorkbench",
    "--onedir", "--windowed", "--noconfirm",
    "--distpath", DIST,
    "--version-file", make_version_file(),
]
for d in collect_data:
    args += ["--collect-data", d]
for s in collect_sub:
    args += ["--collect-submodules", s]
for e in exclude:
    args += ["--exclude-module", e]
for h in hidden:
    args += ["--hidden-import", h]
args.append("main.py")

print("打包版本:", ver.VERSION)
print("PYINSTALLER 开始:", " ".join(args[:6]), "...")
r = subprocess.run(args)
print("RETURNCODE:", r.returncode)
if r.returncode != 0:
    sys.exit(r.returncode)


def make_zip():
    """把 onedir 产物打成 zip，方便直接分发给别人。"""
    import shutil
    src = os.path.join(DIST, "AIWorkbench")
    if not os.path.isdir(src):
        print("打包目录不存在，跳过 zip:", src)
        return None
    zip_base = os.path.join(DIST, f"AIWorkbench_v{ver.VERSION.split('.')[0]}")
    out = shutil.make_archive(zip_base, "zip", root_dir=DIST,
                              base_dir="AIWorkbench")
    size = os.path.getsize(out) / 1048576
    print(f"ZIP 产物: {out}  ({size:.1f} MB)")
    return out


make_zip()
sys.exit(0)
