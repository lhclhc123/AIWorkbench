# -*- coding: utf-8 -*-
"""v9.14.0 打包：新增「DeepSeek 网页版」接入（本机 OpenAI 兼容中转 + CDP 驱动 Chrome）。

【本版核心改动 —— 把 DeepSeek 网页版接成一个模型】
- 新增子包 `app/deepseek_web/`（全部自研，未搬运任何第三方仓库代码）：
    cdp_client   —— Chrome 调试协议（CDP）最小封装
    web_driver   —— DeepSeek 网页驱动 + 状态机（填输入框/发送/判结束/抓原文/防重发）
    prompt       —— messages <-> 单段提示词的纯函数
    local_server —— 本机 OpenAI 兼容服务（/chat/completions SSE、/models、/status…）
    service      —— 单例协调器（起停服务 / 拉与收拾 Chrome / 状态 / 自检 / 通知）
    errors       —— 统一失败类型与中文文案
- `app/config.py` 新增 provider `deepseek_web`（no_key/web 标记、search_style=none）。
- `app/llm_client.py` 仅 3 处小改：key 判空加 `and not provider.get("no_key")`（×2）、
  `chat_auto` 新增 `web_gate` 闸门（网页版未就绪则静默跳过，绝不主动拉浏览器）。
- `app/gui/widgets.py` 的状态行新增 `web` 阶段（"正在等待 DeepSeek 网页输出"）。
- `app/gui/pages.py` 设置页新增「DeepSeek 网页版」区块（状态行 + 三按钮 + 风险说明）。
- `app/gui/main_window.py` 接线：单例、web_gate、按钮、状态轮询、退出收拾。
- 版本号 9.13.1 -> 9.14.0（`PROMPT_VERSION` 保持 24：未改系统提示词）。

⚠️ 零侵入红线：`app/agent.py`、`app/gui/chat_worker.py`、51 个工具、工具调用协议、
   现有 9 个页面结构**一律未改**。

⚠️ 依赖：无新增第三方包（CDP 用已装的 websocket-client；HTTP 服务用标准库 http.server）。

---- 历史版本要点（沿用）----
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

DIST = os.path.join(PROJ, "dist_v9140")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9140_version.txt")


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
