"""v9.20.0 打包：钉钉助手整体重做（消息双栏 / 设置 / 标签）+ IM 持久化底座。

【本版做了什么】
① 钉钉助手主界面整个重做（用户逐条提的要求，全部落地）
   · 顶部三个标签：**消息 / 设置 / 标签**。
   · 「消息」页：左半区=还没回的（含处理中/失败/需人工），右半区=已回复
     （带 AI 回的原话）。两栏都能竖着滚，每条左边一个常态化的勾选框，
     底部一个**红色删除键**，勾了就删（落盘标记，重启也不会再冒出来）。
     显示的永远是**钉钉里的名字**，不是一串 oc_ 开头的 id。
   · 「设置」页：自动回复范围改成三个勾选（群聊 / 单聊 / 群里@我）+
     白名单（这些会话每条都回）+ 「我多少秒没自己回才由 AI 代回」+
     拿不准时回不回（含可改的兜底话术）+ 身份背景大输入框。
   · 「标签」页：一次全量扫描所有会话（群聊在前、单聊在后、按最近对话排序），
     让 AI 一个个判关系标签；**拿不准就留空，绝不瞎打**，用户可手改。
② 新增 `app/im/` 持久化底座（store / policy / scheduler / logbook / tags / hub）
   · 全部落在 `<工作区>/.awb/im/`：会话档案、消息流水、定时任务、日志本、
     共享记忆 + 每会话记忆、设置、标签。老版本没有这些文件也能照常跑。
   · `rows()` 把流水整理成界面要的两栏数据；`hide_message()` 让"删除"可持久。
③ 「延时回复」与「你自己回过就别代回」
   · 消息进来先压 `delay_seconds` 秒；到点前再看一眼：这期间**我本人**在钉钉里
     回过（`last_manual_out`）就跳过，不代回。
   · 为此 `AssistantService` 新增 `outgoing` 信号：我在别的会话里发的消息也记进
     流水 —— 顺带让 AI 看得到"我本人是怎么说话的"，语气才对得上。
   · ⚠ `apply_cfg` 里加了「监听范围变化 → 重新跳过历史」：否则从"只听自聊"
     切到"所有会话"的那一瞬间，会把过去几个月的聊天记录全当成新任务回一遍。
④ 「AI 拿不准」硬判据
   · 系统提示里固定写死：查不到又推不出来时，整条回复只输出 `[[UNCERTAIN]]`。
     主窗口见到它就按用户设置处理 —— 要么换成兜底话术回一句，要么标「需人工」
     留在左栏等你自己回。**绝不让模型硬编。**
⑤ 仍然保留的老能力：接口直发（v9.19.1）、对话隔离（v9.19.0）、团队/浏览器/后台池。

【打包铁律】
- PyInstaller 在 Analysis 阶段就快照源码，**打包期间绝对不能再改代码**（改 main.py 也算改源码）。
- 每次重打前先把旧 build/ 用 mv 挪走（沙箱同 turn 删 >50 文件会被拦）。
- PyInstaller 本体不进包。
- 打完后自证：源码 mtime 必须**早于** build/AIWorkbench/PYZ-00.pyz。
- 打完后自证：`tests/check_pyz_v9180.py` + `tests/probe_pack_v9180.py`。

⚠️ **WebEngine 运行时依赖（沿用 v9.19.1 已验组合，别动）**
- 打包机实测可用：`PyQt6 6.10.2 / PyQt6-Qt6 6.10.2 / PyQt6-WebEngine 6.11.0 /
  PyQt6-WebEngine-Qt6 6.10.2`。判定标准只有一条：能不能真建出视图。
- 运行时资源必须收进包，否则白屏 / 起不来：`QtWebEngineProcess.exe`、
  `resources/`、`translations/`（含 `qtwebengine_locales/`）。
  打包后预期落点：`dist_v9200/AIWorkbench/_internal/PyQt6/Qt6/...`。
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
    # v9.19.1：内嵌宿主 + 直发通道（都是 service 里延迟 import，静态分析抓不到）
    "app.deepseek_web.embedded",
    "app.deepseek_web.api_path",
    # v9.15.0：网页采集 / 批量表单查询 / GitHub（agent 里是延迟 import，静态分析抓不到）
    "app.webcap",
    # v9.16.0：并行任务池 / 多角色团队（同样是延迟 import）
    "app.taskman",
    "app.team",
    # v9.19.1：内嵌浏览器内核（需求 2）—— 以前是 exclude 掉的，本版必须进包
    #   ⚠ QtNetwork / QtPrintSupport 是 QtWebEngine 的运行时依赖，一并显式补上。
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
    "PyQt6.QtPrintSupport",
    "app.webengine",
    # v9.19.1：新模块（同样是延迟 import，静态分析抓不到）
    "app.team_presets",
    "app.gui.team_bar",
    "app.gui.browser_page",
    "app.icons",
    # v9.20.0：IM 助手底座（主窗口里是延迟 import，静态分析抓不到；漏了会
    #   运行时报 ModuleNotFoundError: app.im）
    "app.im",
    "app.im.store",
    "app.im.policy",
    "app.im.scheduler",
    "app.im.logbook",
    "app.im.tags",
    "app.im.hub",
    # v9.21.0：Web UI 一期（主窗口里延迟 import，静态分析抓不到；漏了会
    #   运行时 ModuleNotFoundError: app.webui_host / app.webui_bridge）
    "app.webui_host",
    "app.webui_bridge",
    # 注意：build_exe 工具是靠「外部解释器 python -c "import PyInstaller"」探测的，
    # PyInstaller **不需要**打进本包（打进反而会拖进一堆开发依赖）-> 见下方 exclude。
]

collect_data = ["docx", "pptx", "reportlab", "pdfminer", "lxml"]
collect_sub = ["pdfminer", "reportlab", "pptx", "docx", "openpyxl",
               "edge_tts", "dingtalk_stream", "win32com"]

exclude = [
    "numpy", "matplotlib", "cv2", "tkinter", "moviepy", "imageio",
    "pandas", "scipy", "IPython", "notebook",
    # ★ v9.19.1：从本版起**不再排除 PyQt6.QtWebEngineCore**（内嵌浏览器内核，需求 2）。
    #   注意：PyQt6.QtWebEngineQuick（QML 版）虽然进了 hidden，但 QtQuick 仍被排除；
    #   本应用只用 QtWebEngineWidgets，不碰 QML，故无碍。
    "PyQt6.QtQuick", "PyQt6.QtMultimedia",
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

DIST = os.path.join(PROJ, "dist_v9222")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9180_version.txt")


def _qtwebengine_add_data():
    """收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。

    **不凭猜写路径**：先从本机 PyQt6 包里定位真实目录，只把**确实存在**的项加进去。
    本机（Python312 / PyQt6-Qt6 6.10.2）实测真实路径：
        <site-packages>/PyQt6/Qt6/bin/QtWebEngineProcess.exe      (774968 字节)
        <site-packages>/PyQt6/Qt6/resources/                       (icudtl.dat / *.pak / v8_context_snapshot.bin)
        <site-packages>/PyQt6/Qt6/translations/                    (qtwebengine_locales/ 等)

    目标前缀写死为 `PyQt6/Qt6/...` —— 与 PyInstaller 的 PyQt6 hook 的 `qt_rel_dir`
    一致；否则 QtWebEngineProcess 找不到内核资源 -> **白屏 / 起不来**。
    """
    import PyQt6  # 只有装了 PyQt6 才能打包，这里必然可导入
    pkg = os.path.dirname(os.path.abspath(PyQt6.__file__))
    qt6 = os.path.join(pkg, "Qt6")
    out = []

    proc = os.path.join(qt6, "bin", "QtWebEngineProcess.exe")
    if os.path.isfile(proc):
        out.append(proc + os.pathsep + "PyQt6/Qt6/bin")

    for sub in ("resources", "translations"):
        d = os.path.join(qt6, sub)
        if os.path.isdir(d):
            out.append(d + os.pathsep + "PyQt6/Qt6/" + sub)

    return out


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
# 把浏览器内核运行时资源显式收进包（防 hook 漏收 / 白屏）
for a in _qtwebengine_add_data():
    args += ["--add-data", a]
# ★ v9.21.0 Web UI 一期：前端静态资源收进包。
#   webui_host._resource_dir() 打包态读 sys._MEIPASS/app/webui，所以目标前缀写 app/webui。
_webui_dir = os.path.join(PROJ, "app", "webui")
if os.path.isdir(_webui_dir):
    args += ["--add-data", _webui_dir + os.pathsep + "app/webui"]
else:
    print("!! 警告：app/webui 目录不存在，跳过（Web UI 会在运行时降级回 Qt）")
args.append("main.py")

print("打包版本:", ver.VERSION)
print("PYINSTALLER 开始:", " ".join(args[:6]), "...")
print("WebEngine 运行时资源（--add-data）：")
for a in _qtwebengine_add_data():
    print("   ", a)
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
