"""v9.17.0 打包：修「弹一堆小窗口 + 一执行任务就卡」+ 不懂就问 + 多语言文件 + 每对话目录/双层记忆
          + DeepSeek 同会话不新开。

【用户原话】（一轮提了 6 条）
  「1. 找到连续弹出好多小窗口的原因了，是内些对话消息，都先创建成了窗口，再烧制到主界面
     上去的……优化一下要不每次一执行一个任务就很卡，弹一堆窗口，又很快消失
    2. 添加向用户提问的功能，有什么不懂的一定不要猜，可以向用户提问（参考 workbuddy）
    3. 不仅可以生成 py 文件，像 cpp 啊等等文件都要能生成，怎么我让他写一段 C++ 程序，给我生成 py 文件？
    4. 工作空间内每个对话都新建一个文件夹，分别存放产物和记忆，还要有主记忆
    5. deepseek 的对话中如果是同一段对话，能不能不要在 deepseek 中换一个新对话？
    6. 上面没完成的任务继续」

【本版六件事】
① 弹小窗 + 卡顿（真凶已实锤）
   · **真凶**：`MessageCard.footer` 在**还没有父窗口**时就调了 `setVisible(True)` ——
     那等价于 `show()`，控件当场变成**真实独立顶层窗口**（用户看到的小窗），
     随后 `addWidget()` 把它收编、窗口才消失 = "弹出来又很快消失"。
     → 纪律：**`setVisible(True)` 永远放在 `addWidget` 之后**。
     （对照实验：老写法 isVisible=True/isWindow=True；新写法全程不是顶层窗口。）
   · 卡顿：`_render_full()` 原来每次把聊天区**所有**卡片删掉重建（流式每秒十几次）。
     → 加**内容指纹短路**（内容没变直接 return）+ 流式只**原地刷新**最后一张卡片
     （`MessageCard.update_live`，失败自动回落全量重建）。
   · `_clear_chat()` 加固为 hide → setParent(None) → deleteLater。
② 不懂就问：新工具 `ask_user`（KNOWN_TOOLS 61 -> 62）
   拿不准就弹框问用户（可给候选点选、也可自由输入、可跳过）；后台无人值守时不干等，
   立即返回 `[跳过]` 并让模型按合理假设继续（结果里注明）。`PROMPT_VERSION` 27 -> 28。
③ 多语言文件：`write_file` 按语言/内容特征纠正扩展名（C++→.cpp、Go→.go、Rust→.rs…），
   只在默认扩展名（.py/.txt/无）上动手，排除 .h/.hpp/.ts/.tsx/.jsx/.vue/.svelte；
   新增内置技能 `multi-language-code`「多语言代码生成」（内置技能 11 -> 12）。
④ 每对话独立文件夹 + 双层记忆
   · `chats/<NN_标题>/`：产物落各自 `files/`，记忆落各自 `MEMORY.md`；
     `chats/_index.json` 记对话↔目录映射，对话改名时**文件夹跟着改名但序号不变**。
   · `Workspace.files_dir` 由实例属性改为 `@property`：有当前对话 → 该对话 `files/`，
     否则回落工作区级 `files/`（**老工作区零破坏**）。
   · 记忆分**主记忆**（工作区根 `MEMORY.md`）+ **本对话记忆**（`chats/<NN>/MEMORY.md`），
     `remember(scope=main)`；`recall` 两边都搜并**标注来源**。
⑤ DeepSeek 同会话不新开：`prompt._signature` 里 **system 只认角色不认内容**
   （记忆/技能/定时/时间每轮都变，老判定每轮都不命中 → 每轮新建网页对话）。
   改后前缀增量命中，只发新增尾段，续用同一个网页对话。
⑥ 完成 v9.16.0 的收尾：打包自证 / 记忆 / 技能文档 / 交付 / 推送。

【真机验证】
  · probe_v9170.py         —— 需求②③④⑤ 联合探针 **34/34**
  · probe_render_v9170.py  —— 渲染修复 13/13（含两组对照：老写法必被检出、新写法必检不出）
  · probe_flicker_v9170.py —— 小窗泄漏扫描 2/2（老写法 1 处泄漏 / 新写法 0 处）
  · 老功能一个没坏：parser_cases 73/73、probe_ui_v913 34/34、probe_scroll_stick 5/5、
    probe_voice_silence 7/7、test_v91 112/112、smoke_gui_v9 45/45、smoke_gui_v91 45/45、
    verify_all、probe_taskman/probe_team/probe_parallel_v9160（v9.16.0 遗留）全绿。

⚠️ 打包铁律：
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

DIST = os.path.join(PROJ, "dist_v9170")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v9170_version.txt")


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
