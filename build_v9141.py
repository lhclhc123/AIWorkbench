# -*- coding: utf-8 -*-
"""v9.14.1 打包：修 2 处体验问题（基于 v9.14.0，不覆盖 build_v9140.py）。

【本版核心改动 —— 修 04-qa §6 的两条非阻塞问题】
1. 【低危】no_key provider 的 Key 被清空后仍能工作（不再 401）：
   - `app/config.py` 抽出**唯一**占位串常量 `WEB_PLACEHOLDER_KEY`，`DEFAULT_API_KEYS` 引用它。
   - `app/llm_client.py`：`_stream_once` 与 `probe_provider` 在 `provider.get("no_key")`
     且 key 为空时回落到该占位串（客户端兜底）。**服务端鉴权强度不变**
     （本机服务仍是"回环绑定 + Host 校验 + 要求 Bearer 非空"）。
2. 【中低危·体验】网页自身报错时不再空等 300 秒（快速失败）：
   - `app/deepseek_web/errors.py` 新增 `WebErrorKind.SERVER_ERROR` + 中文文案 + 状态标签。
   - `app/deepseek_web/web_driver.py` 的 `_wait_answer` 新增两条快速失败（**只报错、绝不重发**）：
     A. 页面**空闲**时出现「服务器暂时不可用 / 已停止」等错误文案并持续 ~3s -> SERVER_ERROR；
     B. 连续 60s 页面毫无活动（答复文本 / 答复块数 / 正文长度都不变）-> TIMEOUT（提示已等待秒数）。
   - 新增注入 JS：`serverError()`（多候选文案，实测项与推断项均标 TODO）、`pageTextLen()`（活动信号）。
   - 注入脚本版本 AGENT_VERSION 4 -> 5（页面常驻旧脚本 -> 强制重注入）。

⚠️ 零侵入红线：`app/agent.py`、`app/gui/chat_worker.py`、51 个工具、工具调用协议、
   现有 9 个页面结构**一律未改**；`PROMPT_VERSION` 保持 24（未改系统提示词）。

⚠️ 依赖：无新增第三方包。

---- 历史版本要点（沿用）----
【v9.14.0】接入「DeepSeek 网页版」（本机 OpenAI 兼容中转 + CDP 驱动 Chrome）：
    新增 `app/deepseek_web/`（errors/prompt/cdp_client/web_driver/local_server/service），
    `config.py` 加 provider `deepseek_web`，`llm_client.py` 3 处小改，GUI 加状态行 web 阶段 + 设置页区块。
【v9.13.1】修界面图标空心方块（字体族补 "Segoe UI Symbol"/"Segoe UI Emoji"）。
【v9.13.0】界面去框（仿 WorkBuddy）+ 输入框上方状态行 + 发送键=停止键。
【更早】v9.11 让 AI 学会「用 Python 自己验证」；模型清单治理（只留免费额度模型）。

⚠️ 打包铁律：
- PyInstaller 在 Analysis 阶段就快照源码，**打包期间绝对不能再改代码**。
- 每次重打前必须先把旧 build/ 用 mv 挪走（沙箱同 turn 删 >50 文件会被拦）：
      mv build/ D:\\aiworkbench_old_builds\\build_$(date +%s)
- PyInstaller 本体不进包（build_exe 工具靠外部解释器探测）。
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

DIST = os.path.join(PROJ, "dist_v9141")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9141_version.txt")


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
