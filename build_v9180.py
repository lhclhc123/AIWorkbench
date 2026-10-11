"""v9.18.0 打包：全部图标换单色字形 + 思维链实时呈现 + 内嵌浏览器内核 / 关键词定向搜索
          + 团队功能进对话 + 停止快速收尾 + 复盘包驱动的两个真 bug 修复。

【本版做了什么】
① 需求 15 —— 点「停止」要**快速收尾**
   · `LLMClient.cancel()` 现在会把底层响应句柄 `close()` 掉：读 SSE 的循环阻塞在
     `iter_lines()` 上，只置标志位它不会醒，必须掐断连接才能**立刻**退出
     （把"点了停止还要等十几秒"压到几乎瞬时）。新增 `_reset_cancel()` 复位。
   · 点了停止后**绝不再调模型**：老代码会再发一次 LLM 请求去"补一条最终答复"，
     模型往往又滔滔不绝几十秒。改成从已有内容里把**最近一段够大的结论**捞出来当
     最终答复（`ChatWorker._salvage_conclusion`）。
② 需求 16 —— **全部图标换成单色字形**
   · 起因：emoji 在 Windows 上是**彩色的**，和界面风格不搭。
   · 新增 `app/icons.py` 作为字形的**单一来源**（`ICON` 表 + `ic()/render()`），
     `app/` 下 UI 文案一律走 `ic("名字")`。
③ 需求 17 —— **思维链实时呈现**
   · 新增 `ReasoningCard`（`app/gui/widgets.py`）：带思维链的模型（DeepSeek-V4.x /
     Qwen3.x 等）在正式回答前会流式吐 `reasoning_content`，以前只是悄悄收着、整轮
     结束才折成一条 —— 用户对着转圈干等。现在**边想边显示**（`_on_reason` 增量 +
     实时卡片，和正文一样参与渲染指纹）。
④ 需求 2 / 3 —— **内嵌浏览器内核** + **关键词定向网页搜索**
   · 新增 `app/webengine.py`（离屏渲染 `fetch_rendered` + 抓包嗅探 `_Sniffer` /
     `find_api_requests`）+ `app/gui/browser_page.py`（对话内浏览器页 `BrowserPane`）。
   · `webcap.web_search` / `web_search_multi` 支持 `site:` 限定（定向站内搜索）。
   · ★★ 本版**第一次把 PyQt6-WebEngine 的浏览器内核打进包**（见下"打包铁律"）。
⑤ 需求 13 —— **团队功能改成对话内选团队**
   · 新增 `app/team_presets.py`（预置团队人设：软件小队 / 研究小队 + 派单引擎）+
     `app/gui/team_bar.py`（对话内团队条 `TeamBar`，不再是独立页面）。
⑥ 复盘包驱动的两个真 bug 修复
   · **`write_file` 空内容硬闸**：原来只给 path（"两步写法"只做了第 1 步）会一路
     走到 `open(path,"w")` 写个 0 字节文件，还回一句「已写入 0 字符……（已确认落盘）」
     —— 谎报成功 + 静默截断已有文件。现在内容为空**直接拒绝**并告诉模型怎么补。
   · **「一句话都没说出口」硬保险**：整轮没产出一条非空助手正文时，把最近一段够大
     的结论捞出来顶上（`_salvage_conclusion(stopped=False)`），哪怕退到工具结果。
⑦ 需求 8 / 12 —— 工作区每对话独立文件夹只留产物；正文与**思维链**强制简体中文。

【打包铁律】
- PyInstaller 在 Analysis 阶段就快照源码，**打包期间绝对不能再改代码**（改 main.py 也算改源码）。
- 每次重打前先把旧 build/ 用 mv 挪走（沙箱同 turn 删 >50 文件会被拦）。
- PyInstaller 本体不进包。
- 打完后自证：源码 mtime 必须**早于** build/AIWorkbench/PYZ-00.pyz。
- 打完后自证：`tests/check_pyz_v9180.py`（解 PYZ 验特征串）+ `tests/probe_pack_v9180.py`
  （打包前静态自检）。

⚠️ **WebEngine 运行时依赖（本版关键）**
- 打包机实测的版本组合（**已验证可用，别再当"错配"处理**）：
  `PyQt6 6.10.2 / PyQt6-Qt6 6.10.2 / PyQt6-WebEngine 6.11.0 / PyQt6-WebEngine-Qt6 6.10.2`。
  2026-10-11 用这个组合真跑过：`QApplication` 起来、`QWebEngineView` 类拿到、
  `webengine.engine_available()` 为真、真建出视图（`probe_browser_wire_v9180.py` 30/30）。
  ⚠ 注意 `PyQt6-WebEngine` 的 Python 包号（6.11.0）和它内部 Qt 库的包号
  （`PyQt6-WebEngine-Qt6` 6.10.2）本来就**不同步**——这是官方发行方式，
  不要因为"两个号不一样"就以为坏了。**判定标准只有一条：能不能真建出视图。**
- 本版**不升级** WebEngine（6.10.2 的 Qt6 运行时是已验组合，升级要重新验一遍）。
- 浏览器内核的运行时资源必须收进包，否则运行起来**白屏 / 起不来**：
  `QtWebEngineProcess.exe`、`resources/`、`translations/`（含 `qtwebengine_locales/`）。
  下面 `_qtwebengine_add_data()` 按**本机实测真实路径**（`PyQt6/Qt6/...`）动态收集，
  目标前缀与 PyInstaller 的 PyQt6 hook 的 `qt_rel_dir` 一致。
  打包后预期落点：`dist_v9180/AIWorkbench/_internal/PyQt6/Qt6/...`。
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
    # v9.18.0：内嵌浏览器内核（需求 2）—— 以前是 exclude 掉的，本版必须进包
    #   ⚠ QtNetwork / QtPrintSupport 是 QtWebEngine 的运行时依赖，一并显式补上。
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineQuick",
    "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
    "PyQt6.QtPrintSupport",
    "app.webengine",
    # v9.18.0：新模块（同样是延迟 import，静态分析抓不到）
    "app.team_presets",
    "app.gui.team_bar",
    "app.gui.browser_page",
    "app.icons",
    # 注意：build_exe 工具是靠「外部解释器 python -c "import PyInstaller"」探测的，
    # PyInstaller **不需要**打进本包（打进反而会拖进一堆开发依赖）-> 见下方 exclude。
]

collect_data = ["docx", "pptx", "reportlab", "pdfminer", "lxml"]
collect_sub = ["pdfminer", "reportlab", "pptx", "docx", "openpyxl",
               "edge_tts", "dingtalk_stream", "win32com"]

exclude = [
    "numpy", "matplotlib", "cv2", "tkinter", "moviepy", "imageio",
    "pandas", "scipy", "IPython", "notebook",
    # ★ v9.18.0：从本版起**不再排除 PyQt6.QtWebEngineCore**（内嵌浏览器内核，需求 2）。
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

DIST = os.path.join(PROJ, "dist_v9180")

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
# ★ v9.18.0：把浏览器内核运行时资源显式收进包（防 hook 漏收 / 白屏）
for a in _qtwebengine_add_data():
    args += ["--add-data", a]
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
