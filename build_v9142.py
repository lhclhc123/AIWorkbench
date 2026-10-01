# -*- coding: utf-8 -*-
"""v9.14.2 打包：修「字填进输入框却没发送 -> 白等超时」+ 免手动（基于 v9.14.1）。

【用户报的 bug】DeepSeek 网页版有时把词填进输入框却不发送，然后报超时。
根因（真机逐层定位，三层叠加）：
  A. `findSend()` 最后有一句兜底 `return circles[circles.length-1]` ——
     当发送键处于**禁用态**时它照样返回并 `.click()`，空点击却报 `'button'`，
     日志里记为"已提交提示词"= **假成功**。
  B. **`document.visibilityState === 'hidden'` 时浏览器会静默丢弃点击与真实按键** ——
     窗口最小化 / 被挪到屏幕外时，字能写进输入框、发送键状态也是 ready，
     但点了没反应、真键盘 Enter 也没反应。这是"填进去没发送"的直接原因。
  C. 旧版"enter 兜底"用 `dispatchEvent(new KeyboardEvent(...,{isTrusted:false}))`，
     React 的 onKeyDown 不认可这种合成事件 —— 日志里那条 `发送方式=enter` 是空炮。

【本版修复】
1. `web_driver.py`
   - `findSend()` **删掉危险兜底**，禁用态一律返回 null；候选全部加 `!hasDisabled(el)` 过滤。
   - `hasDisabled()` 判定五条化：class 含 disabled / aria-disabled / el.disabled /
     pointer-events:none / opacity<0.6。
   - 新增 `sendState()`（ready/disabled/missing/no-composer）、`clickSend()` 禁用即拒绝、
     `hasComposer()`、`pageHref()`、`visibility()`。
   - 新增 `ensure_interactive()`：发送**之前**强制把窗口恢复正常并轮询到 visible，
     改不回来就抛 `WINDOW_HIDDEN`（宁可明确报错，也不假装发出去了）。
   - 新增 `_confirm_sent()` 五判据（生成中 / 答复块数↑ / 答复文本变化 / 输入框被清空 /
     URL 变成新会话），确认没生效才补发**真键盘** `Input.dispatchKeyEvent`。
   - `submit()` 日志由"已提交提示词"改为"点击发送键：mode=…，发送键状态=…"+"网页已确认收到本次提问"。
   - 答复提取修两处误判：多行 UI 文案（深度思考/智能搜索）不再被当正文（逐行判定）；
     剔除页面底部「内容由 AI 生成，请仔细甄别」。
   - `AGENT_VERSION` 6 -> 8；并改为 `JS_AGENT_TEMPLATE` + `agent_script()` **单一来源**注入
     （原先脚本里另有一份 `__v` 字面量，导致版本校验永远失败、页面常驻旧脚本）。
2. `errors.py`：新增 `WINDOW_HIDDEN` 错误类型 + 中文文案 + 状态标签。
3. `service.py`：
   - 新增 **`ensure_page()`** —— 免手动入口：端口在线就接入，不在线就**自己拉起浏览器**；
     未登录自动亮出窗口让用户登一次，之后长期免登（登录态在专用 profile）。
   - **后台改为真无头**（`--headless=new`）：桌面零窗口，但实测 `visibilityState` 仍是
     `visible`，发送/收答复完全正常，且能复用登录态。
     ★ 不再用 `--window-position=-32000,-32000` 那种"挪到屏幕外"的土办法（那就是 bug A/B 的来源）。
   - 新增 `_switch_mode()`：有窗口 <-> 无头 互切（重启浏览器，登录态不丢），
     供「显示网页窗口」/「收起窗口」两个按钮使用。
   - 无头下用 `Emulation.setUserAgentOverride` 去掉 UA 里的 `HeadlessChrome` 标记
     （版本号从 `Browser.getVersion` 实时取，不硬编码）。
4. `config.py` / `workspace.py`：`WEB_SILENT_WINDOW` 默认改 **True**（后台无头）。
5. `gui/pages.py` / `gui/main_window.py`：新增「显示网页窗口」「收起窗口」；文案重写；
   新增「后台无头运行」复选框；启动/切模型/改设置时自动准备通道。
6. `local_server.py`：修 `start(0)` 会被 `0 or 默认端口` 吞掉、跑去抢 18921 的语义 bug。

⚠️ 零侵入红线：`app/agent.py`、`app/gui/chat_worker.py`、51 个工具、工具调用协议
   **一律未改**；9 个页面结构未改；`PROMPT_VERSION` 保持 24（未改系统提示词）。

⚠️ 依赖：无新增第三方包。

【真机验证证据（v9.14.2，本轮实测）】
  - `probe_send_e2e_v9142`     RESULT=ALL_OK          连续 2 轮发送均成功取答复
  - `probe_minimized_recover`  RESULT=RECOVER_OK      人为最小化 -> 自动恢复 -> 发出 '15'
  - `probe_offscreen_recover`  RESULT=OFFSCREEN_OK    离屏 hidden -> 挪回 visible -> 发出 '81'
  - `probe_autolaunch_v9142`   RESULT=AUTOLAUNCH_OK   冷启动自拉起 2.6s + 免重登 -> 发出 '42'
  - `probe_headless_e2e_v9142` RESULT=HEADLESS_E2E_OK 无头/有窗口/无头 三段各发一条全通
  - 离线回归：probe_deepseek_web / probe_gui_web / probe_ui_v913 / probe_scroll_stick(5/5) /
    probe_voice_silence(7/7) / parser_cases 全绿

---- 历史版本要点（沿用）----
【v9.14.1】no_key provider 空 Key 兜底；网页自身报错时快速失败（不再空等 300 秒）。
【v9.14.0】接入「DeepSeek 网页版」（本机 OpenAI 兼容中转 + CDP 驱动 Chrome）。
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

DIST = os.path.join(PROJ, "dist_v9142")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9142_version.txt")


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
