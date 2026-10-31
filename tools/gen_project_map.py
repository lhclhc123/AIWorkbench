# -*- coding: utf-8 -*-
"""gen_project_map.py —— 从真实代码里抽取「文件 / 类 / 函数 + 起止行号」，生成交接文档的自动段落。

为什么要有这个脚本：
    手写的交接文档只要代码一动，行号就全错，下一次接手的人反而被误导。
    这里用 ast 直接读源码，行号永远等于当前真实行号。

用法：
    python tools/gen_project_map.py            # 只打印
    python tools/gen_project_map.py --write     # 把结果写回 docs/PROJECT_MAP.md 的 AUTO 区块

约定：`docs/PROJECT_MAP.md` 里必须有这两行标记，脚本只替换它们之间的内容：
    <!-- AUTO:BEGIN -->
    <!-- AUTO:END -->
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "PROJECT_MAP.md")
BEGIN = "<!-- AUTO:BEGIN -->"
END = "<!-- AUTO:END -->"

# 每个源文件的一句话职责（人工维护的**稳定**描述；代码结构变了也不影响这句话）
FILE_ROLES = {
    "main.py": "程序入口：解析命令行（--selftest / --ui-engine / --smoke…）、单实例、启动 MainWindow",
    "app/__init__.py": "包初始化（保持空，避免循环导入）",
    "app/version.py": "版本号 / 构建日期 / GitHub 仓库地址 / 版本比较",
    "app/config.py": "**全部静态配置与系统提示词**：模型源、优先级、免费标记、工具清单、超时、PROMPT_VERSION",
    "app/agent.py": "Agent 核心：消息编排、提示词拼装、工具调用解析（5 路）、工具执行、完成度判据、计划管理",
    "app/llm_client.py": "所有模型源的 HTTP 客户端 + `chat_auto()` 自动降级链 + 流式解析",
    "app/deepseek_web/": "DeepSeek **网页版**通道（CDP 驱动本机 Chrome / 内嵌 WebEngine），见该目录 README 注释",
    "app/deepseek_web/service.py": "网页版服务门面：启动/复用浏览器、状态机、登录引导、窗口显隐",
    "app/deepseek_web/web_driver.py": "CDP 驱动层：注入 JS、发消息、等答复、取思维链/正文（含 cleanText 摘除 ds-think-content）",
    "app/deepseek_web/local_server.py": "对上层伪装成 OpenAI 兼容 HTTP 服务；负责**会话路由**（严格/宽松指纹、绝不每轮新建）",
    "app/deepseek_web/prompt.py": "消息摊平与增量差分：`flatten` / `diff_tail` / `conversation_key` / `tight 裁剪`",
    "app/deepseek_web/embedded.py": "内嵌 QWebEngine 引擎（不依赖本机 Chrome）",
    "app/deepseek_web/cdp_client.py": "极简 CDP WebSocket 客户端（绕过系统代理劫持回环）",
    "app/deepseek_web/errors.py": "网页版错误枚举 + 人话文案",
    "app/gui/main_window.py": "**主窗口 + 全 Web UI 的 RPC 分发**：对话、深链、更新、钉钉、设置、团队都挂在这里",
    "app/gui/chat_worker.py": "一轮对话的后台线程：调模型、解析工具、执行工具、发信号（token/tool_start/tool_result/finished）",
    "app/gui/widgets.py": "Qt 卡片控件：MessageCard（含 flow 顺序模式）、CodeBlockWidget、ProcessGroup、StatusLine 等",
    "app/gui/pages.py": "Qt 各功能页（欢迎页 / 记忆 / 追踪 / 技能 / 设置 / 集成 / 关于…）",
    "app/gui/panels.py": "右侧计划面板 PlanPanel",
    "app/gui/browser_page.py": "内嵌浏览器页（网页采集/预览）",
    "app/gui/team_bar.py": "团队工具条（成员与状态）",
    "app/gui/voice_bar.py": "语音输入条",
    "app/gui/startup_dialog.py": "启动等待框（模型源预热的遮罩）",
    "app/gui/settings_dialog.py": "旧版设置对话框（已被 Web 设置页取代，保留兜底）",
    "app/webui_bridge.py": "Qt ↔ 网页 的桥：QWebChannel 暴露 + 下行业务事件（init/messages/token/tool_*/convs/im_refresh…）",
    "app/webui_host.py": "Web UI 宿主：装配 QWebEngineView、profile、权限、生命周期",
    "app/workspace.py": "工作区：目录布局、会话/产物/记忆/日志的落盘位置、`.awb/` 内部目录迁移",
    "app/worklog.py": "工作总结索引（原子写 + 自愈 + recent 重试）",
    "app/memory.py": "长期记忆：主记忆 + 本对话记忆的读写与提炼",
    "app/plan.py": "任务计划（update_plan）的数据结构与落盘",
    "app/tools_more.py": "工具实现集合：文件、命令、网络、文档、图像、剪贴板、进程…（`KNOWN_TOOLS` 的来源之一）",
    "app/devtools.py": "读代码类工具：find_lines / read_file(start,limit) / edit_lines / apply_patch",
    "app/dingtalk.py": "钉钉：dws CLI 的查找与调用（`find_dws` / `_run_dws`）、OAuth、消息读写",
    "app/assistant.py": "钉钉助理：轮询、代回、范围控制、定时任务",
    "app/markdown_render.py": "Markdown → HTML / 代码块切分（`render_flow` 保序、`render_with_blocks` 兼容老路径）+ Pygments 高亮",
    "app/updater.py": "自动更新：检查 → 多通道下载 → 校验 → 安装 → 重启（阶段文案面向用户）",
    "app/downloader.py": "下载加速底层：镜像候选、并发测速、断点续传、假响应过滤",
    "app/security.py": "安全闸门：危险命令判定、工作区内部目录保护、确认框回调",
    "app/winproc.py": "**所有子进程的唯一出口**（隐藏控制台 + 记录 spawn 日志）；app/ 下禁止裸 subprocess",
    "app/single_instance.py": "单实例锁（第二次启动激活已有窗口）",
    "app/scheduler.py": "定时任务调度",
    "app/skills.py": "技能（SKILL.md）发现与加载",
    "app/team.py": "多角色团队：成员、任务分解、并行执行、结果汇总",
    "app/team_presets.py": "团队角色预设（pm/arch/dev×N/qa/lead 的提示词）",
    "app/taskman.py": "后台任务池：run_bg / bg_wait / MAX_RUNNING 限流",
    "app/trace.py": "调用追踪（traces/tool_calls.jsonl）",
    "app/mcp_client.py": "MCP 客户端（外部工具服务）",
    "app/webcap.py": "网页采集三件套：web_scrape / web_form_batch / github",
    "app/webengine.py": "内嵌浏览器内核封装（抓取与搜索）",
    "app/docread.py": "文档解析（docx/pdf/xlsx/pptx/html）",
    "app/docwrite.py": "文档生成（docx/xlsx/pptx/pdf）",
    "app/asr.py": "语音识别（多 provider）",
    "app/tts.py": "语音合成（sapi / edge）",
    "app/hotkey.py": "全局快捷键",
    "app/icons.py": "SVG 单色图标",
    "app/themes.py": "主题 token（颜色/字号）",
    "app/ratelimit.py": "限流与重试退避",
    "app/folders.py": "常用文件夹定位（桌面/下载/文档）",
    "app/knowledge.py": "知识库切片与检索",
    "app/_keyblobs.py": "**不进取仓库**的密文密钥（DPAPI 加密）",
    "tools/gen_project_map.py": "本脚本：抽取行号生成交接文档的 AUTO 区块",
    "tools/push_release.py": "一键推送 + 隐私扫描 + 打 tag + 发 Release",
    "tools/privacy_scan.py": "隐私/密钥扫描（推送与打包前的硬门槛）",
    "tools/privacy_scan_staged.py": "只扫已暂存文件的版本",
    "tools/gen_keyblobs.py": "把明文 key 生成密文 blob",
    "tools/fix_privacy.py": "修复隐私扫描问题的辅助脚本",
    "build_v9221.py": "PyInstaller 打包脚本（当前版本）：onedir + zip",
}


def py_files():
    out = []
    for base in ("", "app", "tools", os.path.join("app", "gui"),
                 os.path.join("app", "deepseek_web")):
        d = os.path.join(ROOT, base) if base else ROOT
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                continue
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel.startswith(("tests/", ".venv")) or "__pycache__" in rel:
                continue
            if rel not in out:
                out.append(rel)
    return out


def first_docline(node, src):
    doc = ast.get_docstring(node) or ""
    for ln in doc.splitlines():
        ln = ln.strip()
        if ln:
            return ln[:90]
    return ""


def spans(path):
    """返回 [(kind, name, start, end, doc)]，kind ∈ {class, def, method}。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [("error", "语法错误", 0, 0, str(exc))], len(src.splitlines())
    out = []

    def walk(node, prefix=""):
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                out.append(("class", prefix + child.name, child.lineno,
                            getattr(child, "end_lineno", child.lineno),
                            first_docline(child, src)))
                walk(child, prefix + child.name + ".")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if prefix else "def"
                out.append((kind, prefix + child.name, child.lineno,
                            getattr(child, "end_lineno", child.lineno),
                            first_docline(child, src)))

    walk(tree)
    return out, len(src.splitlines())


def build_auto():
    rows = []
    rows.append("### A. 源码文件总表（行数实时统计）\n")
    rows.append("| 文件 | 行数 | 职责 |")
    rows.append("|---|---:|---|")
    detail = []
    total = 0
    for rel in py_files():
        sp, n = spans(os.path.join(ROOT, rel))
        total += n
        role = FILE_ROLES.get(rel, "")
        rows.append("| `%s` | %d | %s |" % (rel, n, role))
        if n < 60 or rel.startswith("tests/"):
            continue
        detail.append("\n#### `%s`（%d 行）\n" % (rel, n))
        detail.append("| 类型 | 名称 | 起止行 | 说明 |")
        detail.append("|---|---|---|---|")
        for kind, name, s, e, doc in sp:
            if kind == "error":
                detail.append("| ! | %s | - | %s |" % (name, doc))
                continue
            name_s = "`%s`" % name
            if kind == "method":
                name_s = "&nbsp;&nbsp;`%s`" % name
            detail.append("| %s | %s | %d–%d | %s |"
                          % ({"class": "类", "def": "函数", "method": "方法"}[kind],
                             name_s, s, e, doc.replace("|", "\\|")))
    rows.append("\n**Python 源码合计 %d 行**（不含 tests/）。\n" % total)

    rows.append("\n### B. 前端资源（Web UI）\n")
    rows.append("| 文件 | 行数 | 职责 |")
    rows.append("|---|---:|---|")
    web = os.path.join(ROOT, "app", "webui")
    web_roles = {
        "index.html": "主界面骨架：侧栏 + 五个 section.page + 对话区 DOM + 模型菜单",
        "im.html": "钉钉助手独立页（对话/日志/标签/设置四标签）+ CLI 配置进度窗口",
        "css/tokens.css": "主题变量（明暗两套）",
        "css/base.css": "重置与基础排版",
        "css/pages.css": "各功能页样式",
        "css/chat.css": "对话页：消息、工具胶囊、过程组、思维卡、代码块",
        "css/im.css": "钉钉助手页样式",
        "js/app.js": "启动、主题、侧栏壳、DeepSeek 连通性弹窗",
        "js/bridge.js": "QWebChannel 封装：call / on / start",
        "js/store.js": "极简全局状态 + subscribe",
        "js/nav.js": "section.page 路由与切换",
        "js/transport-qwebchannel.js": "QWebChannel 传输层",
        "js/views/chat.js": "对话流渲染：消息、流式、过程分组 planNodes、思维卡、输入区",
        "js/views/about.js": "关于页：检查更新、进度条、版本信息",
        "js/views/settings.js": "设置页",
        "js/views/integrations.js": "集成页",
        "js/views/tasks.js": "并行任务页",
        "js/views/team.js": "团队页",
        "js/views/skills.js": "技能页",
        "js/views/memory.js": "记忆页",
        "js/views/bg.js": "后台任务视图",
        "js/components/msg.js": "`<msg-bubble>` 自定义元素（流式分段 + 原地 markdown）",
        "js/components/think-card.js": "`<think-card>`：思维链 / 过程组共用载体（setProcess/setMarkdownFn）",
        "js/components/tool-capsule.js": "`<tool-capsule>`：工具胶囊（默认收起，点开看结果）",
        "js/im.js": "钉钉助手页逻辑：轮询、状态、CLI 进度窗口、标签与设置",
    }
    for dirpath, _dirs, files in os.walk(web):
        for name in sorted(files):
            if not name.endswith((".html", ".js", ".css")):
                continue
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, web).replace("\\", "/")
            if rel.startswith("js/vendor/"):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    n = len(f.read().splitlines())
            except Exception:
                continue
            rows.append("| `%s` | %d | %s |" % (rel, n, web_roles.get(rel, "")))
    rows.append("\n### C. 关键函数明细（行号 = 当前真实行号）\n")
    rows.extend(detail)
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写回 docs/PROJECT_MAP.md 的 AUTO 区块")
    args = ap.parse_args()
    auto = build_auto()
    if not args.write:
        print(auto)
        return 0
    if not os.path.exists(DOC):
        # 没有文档骨架时，生成一个只有 AUTO 区块的骨架，别覆盖人工内容
        with open(DOC, "w", encoding="utf-8") as f:
            f.write("# 项目地图\n\n" + BEGIN + "\n" + END + "\n")
    with open(DOC, "r", encoding="utf-8") as f:
        text = f.read()
    if BEGIN not in text or END not in text:
        print("!! docs/PROJECT_MAP.md 里没有 %s / %s 标记" % (BEGIN, END))
        return 2
    repl = BEGIN + "\n" + auto + "\n" + END
    # 用函数式 repl：避免 auto 里的反斜杠路径（C:\...）被当成转义序列解析
    new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END),
                 lambda m: repl, text, flags=re.S)
    with open(DOC, "w", encoding="utf-8") as f:
        f.write(new)
    print("已更新 %s（AUTO 区块）" % os.path.relpath(DOC, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
