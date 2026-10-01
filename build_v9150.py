# -*- coding: utf-8 -*-
"""v9.15.0 打包：网页采集 + 批量表单查询 + GitHub（基于 v9.14.2）。

【用户反馈】「让他跑一个东西，他根本就不大可以」—— 指的是：
  拿一个查询类网站（易查分）+ 桌面 Excel 里的一批姓名，逐个查宿舍/班级，汇总成表。
  老版本做不到，因为：
    A. `fetch_url` 会把页面 `<script>` 全删掉 -> 模型看不到表单结构和提交接口，
       只能"分析页面源码找接口"，然后就卡死在那儿（真实会话里就是卡死收场）。
    B. `http_request` 没有 Cookie 会话、不支持表单编码、更不能批量循环。
    C. 指望**弱模型自己写爬虫**逐个提交，等于让它自由发挥 —— 实测写不出来。

【本版核心改动 —— 新增 3 个工具 + 1 个内置技能】
1. `web_scrape`（app/webcap.py）
   带 Cookie 会话的 GET/POST；`raw=true` 拿原始 HTML（含 script）；
   `extract=forms` **自动列出页面所有表单字段 + 从页面 JS 里嗅探提交接口**
   （易查分这类站点 form 没有 action，提交地址写在 `$.post("...")` 里）；
   还能 extract=fields/tables/json、按 CSS selector 取内容。
2. `web_form_batch`（★ 本版重点）
   一个网址 + 字段名 + 一串值 -> 自动：预热拿 Cookie -> **自动嗅探真正的提交接口** ->
   逐个提交 -> 若返回 JSON 里有 url 就自动跟进结果页 -> 抠出「字段:值」->
   汇总成表 -> 写成 **Excel/CSV**。支持**断点续查**（重跑跳过已查到的）；
   遇验证码/风控**立即停下并如实报告**（绝不假装查到了）。
   agent 层再加一道保险：模型忘给 save_to 且值 ≥2 个时，**程序自动补一个产出文件名**。
3. `github`
   读仓库文件 / 列目录 / 仓库概况 / 搜索 / issue 与 PR / Release。
   优先走本机已登录的 `gh` CLI（免配置即可读自己的仓库），
   没 gh 时回落 raw.githubusercontent.com + 公开 API；
   设置页新增「GitHub」区块：可填 Token（可选）、可点「检测连接」。
4. 内置技能 `web-batch-query`「网页批量查询」（共 10 个内置技能）
   把上面这条流水线写成 7 步，弱模型照着走也不会跑偏。

⚠️ 零侵入红线：`app/agent.py` 只加工具分支 + 注册表 + 别名；
   51 个老工具、工具调用协议、9 个页面结构**一律未改**。
   `PROMPT_VERSION` 24 -> 25（改了系统提示词，必须 +1）。

⚠️ 依赖：无新增第三方包（requests / bs4 / openpyxl 早就在包里）。

【真机验证】
  · webcap 直测 10/10：表单字段 + 接口嗅探 + 3 人批量查 + 断点续查 + GitHub 三连
  · AgentRunner 接线 15/15：工具名归一、execute 真跑、Excel 真落盘、written 记录、
    自动补 save_to、老工具回归
  · 离线回归：probe_deepseek_web / probe_gui_web / probe_ui_v913 / probe_scroll_stick /
    probe_voice_silence / parser_cases 全绿

---- 历史版本要点（沿用）----
【v9.14.2】修「字填进输入框却没发送 -> 白等超时」+ 免手动（自动拉起 + 后台无头运行）。
【v9.14.0】接入「DeepSeek 网页版」（本机 OpenAI 兼容中转 + CDP 驱动 Chrome）。
【v9.13.1】修界面图标空心方块（字体族补 "Segoe UI Symbol"/"Segoe UI Emoji"）。
【v9.13.0】界面去框（仿 WorkBuddy）+ 输入框上方状态行 + 发送键=停止键。

⚠️ 打包铁律：
- PyInstaller 在 Analysis 阶段就快照源码，**打包期间绝对不能再改代码**。
- 每次重打前必须先把旧 build/ 用 mv 挪走（沙箱同 turn 删 >50 文件会被拦）：
      mv build/ D:////aiworkbench_old_builds////build_$(date +%s)
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
    # v9.15.0：网页采集 / 批量表单查询 / GitHub（agent 里是延迟 import，静态分析抓不到）
    "app.webcap",
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

DIST = os.path.join(PROJ, "dist_v9150")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9150_version.txt")


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
