"""v9.22.6 打包：自动更新改成「弹出来 + 有进度条 + 下载特别快」。

【本版做了什么】
★★★ 用户三条原话：
   「一进去之后要检测有没有新版本，现在能检测到，但是不会自动弹出下载安装的呀。」
   「还要有命令的那个，就是进度条啊，我现在只有点更新才行，而且也是没有弹出进度条。」
   「要想尽一切办法保证它在任何电脑上的安装都是特别特别快的。」

① 自动更新弹窗搬进外壳（app/webui/js/app.js）：
   以前「发现新版本」在 Web 模式下只写状态栏一行字，用户停在对话页啥也看不见。
   现在后端 _after_update_check() 推 update_available 事件，外壳弹全局对话框：
   版本号 / 更新包大小 / 「立即下载并安装」「稍后」「打开发布页」。
② 全局悬浮进度卡：update_progress 事件 → 右上角固定卡片，**任何页面都可见**
   （以前只有「关于」页里那条，用户点进来才有）。含阶段文案（下载/解压/安装/
   重启）、百分比、已下载/总大小；拿不到总长时走不定长动画，不会像卡死。
   失败时卡片变红 + 弹「更新没有成功」并附发布页兜底。
③ 启动静默检查不再冻界面：updater.check() 原来在 UI 线程跑 GitHub 请求
   （国内 3~8s），现在放后台线程，结果交 _after_update_check 统一落地。
④ 下载提速（用户：「现在下载好像还是挺慢的」）：
   · 内核仍是「直连 + 4 个镜像」全部当候选，**并发测延迟+吞吐选最快**；
   · 大文件按 8 段并发（Range 分段写同一个 .part，省一次全盘 IO）；
   · 单连接连续 5s < 200KB/s 自动换镜像；某镜像 429/SSL 失败自动跳下一个；
   · 实测本机：单连接 3.53MB/s、8 段 3.42MB/s —— 瓶颈是**单个 236MB 文件**本身。
⑤ 真正的快：**增量包（patch）**。实测每版只有 AIWorkbench.exe（15MB，
   PyInstaller 把 PYZ 编在 exe 里）和 _internal/app/webui/** 会变，
   增量包压缩后几 MB，下载从 70 秒降到几秒。安装侧沿用原有覆盖安装逻辑，
   打完补丁后用 manifest 的 sha256 逐文件校验；patch 失败自动退回整包。
   旧版（没有 manifest）自动走整包，不会因为没 manifest 就装不了。

【打包铁律】
- PyInstaller 在 Analysis 阶段就快照源码，**打包期间绝对不能再改代码**（改 main.py 也算改源码）。
- 每次重打前先把旧 build/ 用 mv 挪走（沙箱同 turn 删 >50 文件会被拦）。
- PyInstaller 本体不进包。
- 打完后自证：源码 mtime 必须**早于** build/AIWorkbench/PYZ-00.pyz。
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

DIST = os.path.join(PROJ, "dist_v9227")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9180_version.txt")


def _qtwebengine_add_data():
    """收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。

    不凭猜写路径：先从本机 PyQt6 包里定位真实目录，只把**确实存在**的项加进去。
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


MANIFEST_DIR = os.path.join(PROJ, "manifests")


def _dist_root():
    return os.path.join(DIST, "AIWorkbench")


def write_manifest():
    """给本次产物写 manifest.json（安装根目录）+ 存一份到仓库 manifests/。

    manifest 就是「每个文件的 sha256 + 大小」，后面据此 diff 出增量包，
    客户端也靠它判断「我这个安装是不是原封不动的 vX」，决定能不能用增量包。
    """
    import shutil
    from app import incremental as inc

    root = _dist_root()
    if not os.path.isdir(root):
        print("打包目录不存在，跳过 manifest:", root)
        return None
    path = inc.write_manifest(root, ver.VERSION)
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    keep = os.path.join(MANIFEST_DIR, "v%s.json" % ver.VERSION)
    shutil.copyfile(path, keep)
    m = inc.read_manifest(path) or {}
    print(f"MANIFEST: {keep}  ({os.path.getsize(path) // 1024} KB, "
          f"{len(m.get('files') or {})} 个文件)")
    return keep


def _ver_key(path):
    """把 manifests/v9.22.5.json 变成可比较的版本元组（字符串比较会把 9.22.10 排到 9.22.9 前面）。"""
    import re
    m = re.search(r"v(\d+(?:\.\d+)*)\.json$", os.path.basename(path))
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def make_patch(manifest_path):
    """按「上一版 manifest → 本版 manifest」生成增量包。

    只装变化的文件，体积从 236MB 掉到几 MB。没有上一版（首次发增量）就跳过，
    客户端拿不到 patch 会自动走整包，不影响更新。
    """
    import glob
    from app import incremental as inc

    root = _dist_root()
    if not (manifest_path and os.path.isdir(root)):
        return None
    olds = [p for p in glob.glob(os.path.join(MANIFEST_DIR, "v*.json"))
            if os.path.basename(p) != "v%s.json" % ver.VERSION]
    olds = [p for p in olds if _ver_key(p) < tuple(ver.VERSION_TUPLE)]
    if not olds:
        print("没有上一版 manifest，跳过增量包")
        return None
    old_path = sorted(olds, key=_ver_key)[-1]
    base_ver = _ver_key(old_path)
    base_txt = ".".join(str(x) for x in base_ver)
    out_zip = os.path.join(
        DIST, f"AIWorkbench_patch_v{base_txt}_to_v{ver.VERSION}.zip")
    info = inc.make_patch(root, out_zip,
                          old_manifest=inc.read_manifest(old_path),
                          version=ver.VERSION)
    print(f"增量包: {out_zip}  ({info['bytes'] / 1048576:.2f} MB，"
          f"变化 {info['changed']} 个文件，删除 {info['removed']} 个)")
    return out_zip


# ⚠ 顺序不能动：manifest 必须在打 zip **之前**写进产品目录，
#   否则整包里没有 manifest.json，新装的用户下次永远只能整包更新。
manifest_out = write_manifest()
zip_out = make_zip()
patch_out = make_patch(manifest_out)
print("=" * 60)
print("整包:", zip_out)
print("增量包:", patch_out)
sys.exit(0)
