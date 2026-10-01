# -*- coding: utf-8 -*-
"""v9.15.1 打包：修「模型用原生格式发工具调用 -> 被当成最终答复 -> 整轮啥也没干」。

【用户反馈】「现在这个还是不行啊……我让他给我跑一个东西，他根本就不大可以」
  -> 用他的真实案例（易查分 + 桌面名单 Excel 批量查宿舍/班级）跑 **真模型 e2e**，
     抓到真凶：**不是工具不行，是解析器认不全模型的输出格式**。

【真凶（2026-10-01 探针实录）】
  glm-4.7-flash 第 3 轮输出的不是 `<tool_call>{"name":...}</tool_call>`，而是它
  **训练时的原生函数调用格式**（智谱 XML 参数风）：
      <tool_call>run_python<arg_key>code</arg_key><arg_value>...</arg_value></tool_call>
  老解析器只认花括号 JSON，于是整段被判定为「模型的最终答复」-> agent 循环**当场结束**，
  用户看到的就是「他说要干、实际一步没干」。这就是"跑不动"的真相。

【本版改动（三处）】
1. `agent._xmlarg_calls()`：新增第 5 种工具调用格式解析（排在标准标签之后）：
   · `<tool_call>名字<arg_key>k</arg_key><arg_value>v</arg_value>…</tool_call>`（多参数）
   · 省略 arg_key 的整包 JSON：`<tool_call>list_dir<arg_value>{...}</arg_value></tool_call>`
   · 零参数：`<tool_call>system_info</tool_call>`
   值先试 JSON（数字/bool/对象），解不开就按原文收下（保留代码缩进）。
   `parse_tool_calls`（一轮多调用）同步支持。
   ⚠️ 守卫只能看 `<tool_call>`：写成 `"arg_value" not in text` 会把**零参数**写法挡在门外
      （写完立刻被 parser_cases 打出来，已修）。
2. 提示词补一句：「名单别自己读出来再用 run_python 抠成列表，直接把 values_from
   指向那个 Excel/CSV」——省模型一整轮，也少一次抠错的机会。
   `PROMPT_VERSION` 25 -> 26（改了系统提示词，必须 +1）。
3. `markdown_render`：模型**漏写 `</tool_call>`** 时（XML 参数风经常漏），原来标签会
   原样显示在气泡里。现补一条只吃「调用本体」的兜底：
   ⚠️ 不能写成 `<tool_call>.*$` —— 调用夹在正文中间时会**把后半段结论一起吃掉**，
      气泡直接变空；只吃「名字 + 若干 arg 对」才不会误伤（已用 6 个样例复验）。

⚠️ 零侵入红线：只动 `agent.py`（解析器 + 提示词）与 `markdown_render.py`（纯显示层），
   54 个工具、工具调用协议、9 个页面、界面结构**一律未改**。
⚠️ 依赖：无新增第三方包。

【真机验证】
  · **真实模型 e2e（用户原案例，真模型 + 真工具，7 轮上限）**：
      web_scrape(extract=forms) 探到 s_xingming -> web_form_batch(values_from=桌面 Excel)
      -> 真落盘 -> 模型自己复制到桌面 -> 给结论
      → **RESULT=MODEL_RUNS_IT**（真产出文件；上一版同一探针是 STILL_STUCK）
  · parser_cases **73/73 ALL_OK**（新增 4 条智谱 XML 格式用例）
  · 其余离线回归：probe_deepseek_web / probe_gui_web / probe_ui_v913 /
    probe_scroll_stick / probe_voice_silence / probe_webcap / probe_agent_webcap 全绿

---- 历史版本要点（沿用）----
【v9.15.0】新增 web_scrape / web_form_batch / github 三工具 + 内置技能 web-batch-query。
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

DIST = os.path.join(PROJ, "dist_v9151")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9151_version.txt")


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
