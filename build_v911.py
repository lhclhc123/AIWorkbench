# -*- coding: utf-8 -*-
"""v9.11 打包：让 AI 学会「用 Python 自己验证」（没跑过 = 没做完）。

本版核心（用户诉求："我的 AI 现在不大会使 Python…让你的 AI 也学会这个能力"）：

【一、系统提示词升级（config.PROMPT_VERSION 21 -> 22）】
- 「# 三」第 4 步：要求"写一段 Python 测试脚本 / 冒烟测试真的跑一遍，
  把真实输出贴出来…没有真跑通、没有真实输出，就不算完成"。
- 「# 五」自检②：改为"我声称跑过的命令/程序，有没有 Python 脚本的真实输出为证？"
- 新增整节「# 六、用 Python 自己验证（跑得出证据，才叫会干活）」：
  * 核心信条"没跑过 = 没做完"；
  * 4 个可直接套用的 ```python 模板（①文件落盘非空 ②程序能跑通
    ③exe --selftest ④requests 探网页）；
  * "最多 3 轮"迭代闭环：跑 -> 看真实输出 -> 改 -> 再跑；
  * 汇报格式：验证命令 + 真实输出摘要。
- agent.PROJECT_STEP_HINTS["env"] / ["verify"] 各补一句"证据要能跑出真实输出"。
- 提示词总长约 14001 字符（新增约 1209 字符）。

（本版打包脚本自 v9.10 复制，DIST 换 dist_v911；源码沿用 v9.10 的
  钉钉 dws --yes 修复 + 盘符归一化 + 长期记忆去重。）

历史版本要点：

【一、模型清单治理（用户要求：只留有免费额度的模型）】
- **实测**（2026-09-30，用真实 Key 逐个打 /chat/completions，不是查资料）：
  * 百度千帆 7 个模型全部按量计费；其"永久免费"的 ernie-speed-8k /
    ernie-3.5-8k 对这个 Key 返回 401 invalid_model（无权限）
    -> **整个 baidu 端点删除**。
  * DeepSeek 官方无永久免费额度，且 deepseek-chat / deepseek-reasoner 已下线
    -> **整个 deepseek 端点删除**。
  * 智谱 glm-4v（付费）删除；新增官方免费的 glm-4.6v-flash（视觉）。
  * 硅基流动 DeepSeek-V3 / DeepSeek-R1（2/8、4/16 元每百万）删除，只留 ¥0 的 4 个。
  * **重大收获**：阿里百炼账号处于「仅用免费额度」模式，实测
    `deepseek-v4.1-flash` / `deepseek-v4-pro-0813` 返回 200 —— **正规免费 DeepSeek**，
    用户无需注册新账号。另新增免费的 qwen3.8-max。
- PROVIDER_PRIORITY 改为 ["dashscope","zhipu","scnet","siliconflow"]，
  让「自动模式」优先用上免费 DeepSeek，额度耗尽自动退到下一个端点。

【二、聊天界面仿 WorkBuddy】
- 修复"一发送就跳到本轮最上面"；Enter 发送 / Shift+Enter 换行；
- AI 中间过程保留为「深度思考」灰色小字折叠块（生成完自动收起、箭头展开）；
- 工具/写文件卡片收起，写文件卡片带绿色 +N / 红色 -N；
- 去掉聊天页顶部工具条：模型选择移到输入框右下角，右侧依次是语音、发送箭头，
  左下角是 + 号（添加文件）；联网与 Agent 模式默认开启并移入设置页；
- 侧栏精简（PAGES 仍作注册表，侧栏只显示核心子集 + 「更多」菜单）；
- 助理页改为左右分栏、刷新后按 id 保留选中项。

【三、思维链透出】
- llm_client 新增 on_reasoning 回调（默认 None，完全向后兼容），
  把百炼 DeepSeek/Qwen 的 delta.reasoning_content 透出来，
  让「深度思考」有真材实料（实测 deepseek-v4.1-flash 一次 53 块 / 216 字）。

打包坑（与 v9.7.1 相同，已在下文参数里处理）：
- 文档库、win32com、edge_tts、dingtalk_stream 都是函数内动态导入，
  PyInstaller 静态分析会漏 -> 全部显式 --hidden-import；
- python-docx / python-pptx 包内模板必须 --collect-data；
- pyaudio 是二进制扩展必须真正带上；
- 本机同时装了 PyQt5，多 Qt 绑定会 abort -> 全排除；
- PyInstaller 本体不进包（build_exe 工具靠外部解释器探测）；
- **每次重打前必须先把旧 build/ 用 mv 挪走**（沙箱同 turn 删 >50 文件会被拦）。

⚠️ 打包铁律：PyInstaller 在 Analysis 阶段就快照源码，
   **打包期间绝对不能再改代码**，否则包的是旧代码。先改完 -> 跑全回归 -> 才打包。
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

DIST = os.path.join(PROJ, "dist_v911")

# ---- 生成 exe 版本信息（资源管理器里能看到版本号/公司/说明）----
VERSION_FILE = os.path.join(PROJ, "build_v911_version.txt")


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
