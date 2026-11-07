"""v9.22.5 打包：钉钉页完全不可用的真正根因修复 + 打标签准确率重做。

【本版做了什么】
★★★ 唯一的、也是致命的根因：app/webui/js/im.js 从来没调用过 bridge.start()。
   钉钉页（im.html）是独立页面，有自己独立的 QWebChannel 和独立的 bridge 模块
   实例。bridge.start() 才会把后端的 webui_event 信号接到 dispatch 上，
   rpc_result 也才能派发回 rpc() 的 Promise。v9.21.2「全 Web 化」重写时漏了
   这一行，导致：上行 rpc 能到后端干活，下行结果永远收不到 → 每个 rpc 都在
   30 秒后抛「RPC 超时」。
   用户看到的就是：一进钉钉页状态一直「检查中」然后变「状态未知」、
   一键配置一直卡住最后提示超时、浏览器里登录成功了页面毫无反应。
   实测同一页面注入 start() 后：im_get_state 从 30000ms 超时 → 5ms 返回。

其余配套修正：
① 状态轮询不再「永久放弃」：以前超过 20 次就 return，轮询链彻底断掉，
   页面永远挂着「状态未知」除非用户手点。现在超限只降频到 15s 兜底重试；
   页面隐藏时也不再停表（改为 30s 慢速），切回来立刻强制重查。
② 「检查中」节奏 1.5s→0.8s，占位文案改成「正在检查钉钉登录状态…」。
③ RPC 失败不再静默：catch 里把「连接后端失败，点这里重试」写进页头，
   CLI 那格也不再停在前端假状态「正在配置…」上。
④ 登录措辞区分：标题从「配置完成」改为「登录成功 / 等待钉钉授权 /
   登录未完成」，用户终于知道自己登没登上。
⑤ 从未跑过配置时，CLI 那格不再写「尚未检测」，改为基于文件探测的
   「dws 已安装（未自检）。点「一键配置」校验，或点「登录」授权」。
⑥ 登录态缓存加「代际」保护：一个在登录前发起的旧探测晚到，不会再把刚登录
   成功的结果盖成「还没登录」并缓存 60s。
⑦ 主题同步：im 页以前的 init 也收不到（同 ① 根因），现在跟随主程序深浅色。

【打标签重做（用户：标签不准 / 全是未知 / 五人群偏题）】
① 分类：改用 dws `chat +chat-list --types group,p2p`。老接口
   `+conversation-list` 根本没有类型字段 → 60 个会话全被存成 group。
   现在按 chatMode/groupType/memberCount 判定，实测 19 群 + 41 单聊。
   排序：群聊在上、单聊在下，同类按最近消息时间倒序。
② 昵称：标签列表不再显示 openConversationId，显示群名 / 对方昵称。
③ 采样 20 → 40 → **80 条**（实测多 0.7s/会话）。关键是 40 条时常把
   「我」称呼对方的那句（「肖老师，帮我打一份」）截掉，导致一批单聊被打成未知。
④ 以前把「我」发的消息整条丢掉，只剩对方半句「嗯」「好的」——这是
   「全是未知」的主因。现在保留并标成「我」。
⑤ 提示词重写 11 条：只认三种直接证据，其中「我怎么称呼对方」是最硬的；
   **方向纠正**：对方叫我「家长」只说明我是家长，不许拿它给对方打「家长」。
⑥ 幻觉闸门 guard_tags：「大学老师」→「老师」（材料里没有「大学」就剥掉）；
   「某某班」必须原文出现（修掉「蜊江中学 → 初四十二班」）；去噪声词、
   去与会话名相同的标签、去互为子串的重复；群聊里「爸爸」这类成员称谓
   一律改「家人群」（修掉五人群被打成「爸爸」）。
⑦ 上限 3 → 5；空结果也落一条「证据不足」，界面上能看到会话、可手动补。
⑧ 并发：读消息 4 路，调模型 8 路（写回仍串行，避免并发写坏 threads.json）。
⑨ 消息文本清洗：去掉 mediaId/fileId 长串、纯媒体占位不占样本名额。
⑩ 模型优先超算 SCNet-Max（用户明确要求不再用 GLM）：SCNet-Max → glm-4-flash → auto。
实测：94 个会话，打上标签 37 → 65，未知 57 → 29，0 错误，耗时 563s。

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

DIST = os.path.join(PROJ, "dist_v9225")

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


make_zip()
sys.exit(0)
