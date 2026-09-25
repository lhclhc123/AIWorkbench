# AI 工作台 v9（本地 AI Agent 桌面应用）

一个**对标 WorkBuddy** 的本地 AI Agent 桌面程序 —— 流式对话、**47 个真实可用工具**、**技能系统**、**定时任务自动执行**、**子代理委派**、**语音输入 + 语音播报**、**钉钉接入**、**自动检查更新**、文档读写、长期记忆、任务计划面板、MCP 扩展。全部用你自己的 API 密钥，**数据不出本机**，**全免费**。

> v9 主题：从「会调工具的聊天框」升级为「能自己排班干活的助手」。
> 密钥不再明文落盘 —— 内嵌混淆密文 + Windows DPAPI 加密。

---

## 目录

- [v9 新增了什么](#v9-新增了什么)
- [全部功能](#全部功能)
- [47 个内置工具](#47-个内置工具)
- [技能系统](#技能系统)
- [定时任务](#定时任务)
- [键盘快捷键](#键盘快捷键)
- [目录结构](#目录结构)
- [运行（开发模式）](#运行开发模式)
- [打包为 exe](#打包为-exe)
- [上架 GitHub + 自动打包发布](#上架-github--自动打包发布)
- [API 密钥安全](#api-密钥安全)
- [工作区里都有什么](#工作区里都有什么)
- [MCP 配置示例](#mcp-配置示例)
- [安全说明](#安全说明)
- [已知限制](#已知限制)

---

## v9 新增了什么

| 能力 | 说明 |
| --- | --- |
| 🧩 **技能系统（Skills）** | 仿 Claude Skills / WorkBuddy 技能：`SKILL.md` + YAML 头（name / description / when）。内置 **9 个**开箱即用技能，工作区可自建，AI 通过 `list_skills` / `use_skill` 主动加载并按步骤执行。顶部「技能」页可视化管理与新建。 |
| ⏰ **定时任务** | 每天 / 每周 / 每小时 / 按间隔 / 仅一次；用自然语言写时间（"每天 8:00"、"工作日 9 点"、"每 2 小时"、"周五 18:00"）。到点**后台无人值守跑完整 Agent 循环**，结果可推送到钉钉。顶部「定时」页管理，支持「立即跑一次」。 |
| 👥 **子代理委派** | `spawn_agent` 开一个**独立上下文**的子代理去干子任务（如"先把这三个文件读完再汇报"），只把结论带回主对话，主上下文不被中间过程污染。 |
| 🎙 **语音输入** | 麦克风录音 → 走 `glm-asr-2512` 识别成文字填进输入框。全局热键 `Ctrl+Alt+V` 一键说话。 |
| 🔊 **语音播报** | `speak_text` 工具 + 界面「朗读」按钮，优先 Windows SAPI，可选 edge-tts（音色更好）。 |
| 📎 **附件条** | 拖拽或按钮添加文件，显示成可删除的卡片；发送时自动附带文本；消息里标「📎 已附」。 |
| ✏️ **消息编辑 / 重答 / 删除** | 用户消息可「编辑」后重发；任意 AI 回复可「重新生成」；消息可删除。 |
| 🖥 **系统托盘** | 关闭窗口默认收进托盘继续后台跑定时任务；托盘菜单可呼出主窗口 / 立即语音 / 退出。 |
| ⌨️ **全局热键** | `Ctrl+Alt+Space` 任意界面呼出主窗口；`Ctrl+Alt+V` 直接开始语音输入。无需安装任何键盘钩子。 |
| 🔄 **自动检查更新** | 走 GitHub Releases（`releases.atom` / `releases/latest`），**不需要 token**，启动后静默检查，也可用 `check_update` 工具随时查。 |
| 🔐 **密钥无明文** | 内嵌密文（XOR 混淆）+ 落盘 DPAPI 加密；`tests/check_nosecrets.py` 自动扫描产物确认无明文。 |
| 🎯 **模型路由修复** | 严格模式：**选哪个模型就调哪个模型**，不再静默切到别的模型；界面会标注本次真实响应的模型。 |
| 🛡 **工具名自动纠正** | 模型把技能 slug / 别名当工具名调（`weekly-report`、`skill`、`cron`…）时自动路由到正确工具，而不是直接报错摆烂。 |

---

## 全部功能

### 对话与工作台
- 💬 **实时流式聊天**：AI 回复逐字显示（SSE），带"正在思考…"动画。
- 🫧 **气泡式消息卡片**：用户靠右蓝色气泡 / AI 靠左灰色气泡 / 工具结果黄色卡片。
- 📋 **一键复制**：每条消息可复制原始 Markdown。
- 🔧 **工具结果可折叠**：长内容默认折叠，头部标注这一步做了什么。
- 🎨 **8 个页面**：对话 / 技能 / 定时 / 记忆 / 工具记录 / 集成 / 设置 / 关于（侧边导航）。
- 🌗 **浅色 / 深色主题**：一键切换，深色模式强制调色板保证文字可读。
- 🗂 **多会话**：左侧列表管理多个对话，互不干扰。
- 📝 **Markdown + 代码高亮**：代码块语法高亮、可复制。
- ⬇ **导出对话**：一键导出 `.md` 到工作区 `exports/`。
- 📁 **工作区强制自选**：启动必须选一个有效工作区；系统临时目录（TEMP/TMP）一律拒绝。
- 🏷 **显示实际使用的模型**：每条回复左下角标注本次真正响应的模型（如 `glm-4-flash · 智谱 Zhipu`）。

### 文档与文件
- 🖊 **文档生成**：`create_document` —— 正文用 Markdown 写，直接生成 **Word / Excel / PPT / PDF**。
  - Excel 每张 Markdown 表格进独立工作表并自动列宽；PPT 每个 `# 一级标题` 开一页；PDF 自动注册中文字体（不出方块）。
  - 例：**"帮我做一份本周周报，存成 Word 放在桌面"** → 真的生成 `.docx` 并给出完整路径。
- 📄 **文件理解**：点「📎 添加文件」或**直接拖进窗口**，本地解析后交给 AI。
  - Word / PDF / Excel / PPT / HTML / CSV / JSON / 各种文本代码 —— 本地提取（含表格），不联网。
  - **图片**（png/jpg/bmp/webp/gif）—— 走多模态视觉模型做 OCR 与画面理解（免费 `GLM-4V-Flash` 优先）。
- 🖼 **图片处理**：`image_op` 缩放 / 裁剪 / 转格式 / 拼图 / 加水印。
- 📊 **图表**：`create_chart` 直接生成柱状 / 折线 / 饼图 PNG。

### 智能与自动化
- 🧠 **跨会话长期记忆**：工作区 `MEMORY.md` 就是 AI 的记忆，`remember` / `recall` 读写，每轮自动注入提示词；顶部「记忆」页可直接查看 / 编辑 / 清空。
- 🗒 **任务计划面板**：多步任务 AI 先用 `update_plan` 列计划，输入框上方实时显示每步状态（○ ◔ ● ✗）与完成百分比。
- 🧩 **技能系统**：见[下方专节](#技能系统)。
- ⏰ **定时任务**：见[下方专节](#定时任务)。
- 👥 **子代理**：`spawn_agent` 独立上下文跑子任务。
- 🌐 **联网搜索**：`web_search` 只走真支持联网的端点（智谱 / 百炼 / 超算 / 千帆），要求标注来源。
- 🔎 **历史对话检索**：`search_history` 按关键词翻本工作区历史对话。
- 📚 **本地知识库**：`build_index` 建倒排索引（中英文分词 + BM25），`search_knowledge` 按相关度检索。

### 系统与集成
- 🐍 **代码执行**：`run_python` / `run_command` 真跑并返回真实输出；破坏性操作先弹确认。
- 🖥 **系统工具**：`screenshot` 截屏、`clipboard` 剪贴板、`list_processes` / `kill_process` 进程、`notify` 通知、`window_list` 窗口、`now` 精确时间、`system_info`、`archive` 压缩解压、`download_file` 下载、`http_request` 任意 HTTP。
- 🔔 **本机提醒**：`reminder` 到点弹通知。
- 🔌 **MCP 扩展**：支持现成 MCP 服务器（GitHub、数据库、自建服务…），工作区放 `mcp.json` 即可，「集成」页可扫描列出工具。
- 🧭 **工具调用记录**：每次调用记 **名称 / 参数 / 耗时 / 成功失败 / 结果摘要**（`工作区\traces\tool_calls.jsonl`），「工具记录」页可视化查看。
- 📨 **钉钉接入**：支持 **webhook 加签** / **企业内部应用** / **Stream 长连接**三种模式，可推送消息、查询状态。

### Agent 质量保障（v8 起持续打磨）
- 模型写错调用格式（只写工具名 / 只丢 JSON / `[工具 xxx {...}]` / Windows 路径转义写坏）→ 自动纠正重试。
- 用户一条消息提多件事、AI 只做一部分（或该 `remember` / `update_plan` 却没调）→ **催办做全**。
- 要求"真查电脑"而 AI 只贴代码不执行 → 强制真跑一次工具。
- 工具调用失败后还硬给"结果"、或**编造**工具返回值 → 识别并打回，绝不让你看到假结果。
- 要求写/改文件或生成文档，AI 说"模拟环境无法修改" → 强制真正落盘。
- AI 不去答问题反而对系统说明"表态" → 打回要求正面作答；真改不过来就用**真实工具结果**收尾。
- 界面上**不会**出现 `<tool_call>` 标签、纠正提示、工具回喂等内部文字。
- 一条回复里多个工具调用会全部依次执行。
- 可读写任意（非系统）目录；仅系统关键目录（Windows / Program Files / ProgramData / 本程序安装目录）禁止写入。
- 删除只移入回收站（可恢复）且必须确认；命令执行 / 结束进程 / 破坏性 Python 前必须确认；危险命令（格式化 / 关机）直接拦截。
- 写过 / 生成过文件后自动做「完成自检」，确认真的落盘。

---

## 47 个内置工具

| 分类 | 工具 |
| --- | --- |
| 读 / 理解（10） | `read_file` `list_dir` `read_document` `read_image` `search_files` `fetch_url` `system_info` `now` `search_history` `window_list` |
| 写 / 生成（5） | `write_file` `create_document` `file_op` `open_path` `archive` |
| 执行 / 系统（7） | `run_command` `run_python` `list_processes` `kill_process` `clipboard` `screenshot` `notify` |
| 联网（3） | `web_search` `download_file` `http_request` |
| 记忆 / 计划 / 知识库（5） | `remember` `recall` `update_plan` `build_index` `search_knowledge` |
| 语音（2） | `transcribe_audio` `speak_text` |
| 钉钉（2） | `dingtalk_push` `dingtalk_status` |
| 图片 / 图表（2） | `image_op` `create_chart` |
| 提醒 / 更新（2） | `reminder` `check_update` |
| 技能 / 定时 / 子代理（7） | `list_skills` `use_skill` `schedule_task` `list_tasks` `cancel_task` `run_task` `spawn_agent` |
| MCP（2） | `mcp_list` `mcp_call` |

---

## 技能系统

技能 = 一份 Markdown 说明书（`SKILL.md`）+ YAML 头，告诉 AI「遇到这类任务该按什么步骤做」。

**内置 9 个**：

| slug | 用途 |
| --- | --- |
| `weekly-report` | 周报 / 日报生成（汇总本周工作成 Word） |
| `doc-typeset` | 文档排版美化（标题层级 / 表格 / 目录） |
| `data-analysis` | 数据分析（读表 → 统计 → 出图表 → 出结论） |
| `code-review` | 代码审查（读代码 → 找问题 → 给修改建议） |
| `meeting-notes` | 会议纪要（录音/文字 → 待办 + 决议） |
| `deep-research` | 深度调研（多轮检索 → 交叉验证 → 出报告） |
| `sheet-clean` | 表格清洗（去重 / 补全 / 格式统一） |
| `ppt-outline` | PPT 大纲（主题 → 每页要点 → 生成 pptx） |
| `bug-hunt` | 排查 Bug（复现 → 定位 → 修复 → 验证） |

**用起来**：直接说"用周报技能帮我写一份"，或 AI 自己判断后调 `use_skill`。若模型偷懒把技能名当工具名直接调（`{"name":"weekly-report"}`），会被**自动路由**成 `use_skill`，不会报错摆烂。

**自建技能**：在 `工作区/skills/<你的技能>/SKILL.md` 写：

```markdown
---
name: 我的技能
description: 一句话说明它是干嘛的
when: 什么时候该用它
---

## 步骤
1. 先做 A
2. 再做 B
3. 最后 C
```

保存后「技能」页点「重新加载」即可，或让 AI 调 `list_skills` 看到它。

---

## 定时任务

「定时」页新建任务，填三样：**名称**、**要 AI 做的事（自然语言）**、**什么时候跑**。

支持的时间写法（自然语言，自动解析）：

| 写法 | 含义 |
| --- | --- |
| `每天 8:00` / `每天 9点` | 每天固定时刻 |
| `工作日 9:00` / `周一到周五 18:00` | 仅工作日 |
| `周末 10:00` | 周六周日 |
| `每周一 9:00` / `周一,周三,周五 8:30` | 指定星期 |
| `每 2 小时` / `每 30 分钟` | 固定间隔 |
| `每小时` | 整点每小时 |
| `2026-10-01 09:00` / `明天 9点` | 仅执行一次 |

到点后**在后台跑完整的 Agent 循环**（可用全部 47 个工具），结果记录在「工具记录」页，可勾选**推送到钉钉**。支持「立即跑一次」验证效果。

> 定时任务用的是**独立会话上下文**，不会污染你正在聊的对话。

---

## 键盘快捷键

| 快捷键 | 作用 |
| --- | --- |
| `Ctrl+Alt+Space` | 全局呼出 / 隐藏主窗口（任意界面可用） |
| `Ctrl+Alt+V` | 全局直接开始语音输入 |
| `Ctrl+Enter` | 发送消息 |
| `Esc` | 停止当前生成 |

（热键可在「设置」页修改或关闭。）

---

## 目录结构

```
AIWorkbench/
├─ main.py                  # 入口（--selftest 自检）
├─ build_v9.py              # PyInstaller 打包脚本（v9）
├─ installer.iss            # Inno Setup 安装包脚本
├─ requirements.txt
├─ .github/workflows/
│  └─ build-release.yml     # 打 tag 自动打包 + 自检 + 发 Release
├─ tools/
│  └─ gen_keyblobs.py       # 从 env/json 生成混淆密钥密文（本地运行，产物不进仓库）
├─ app/
│  ├─ config.py             # 端点 / 模型 / 免费标注 / 系统提示词（v10）
│  ├─ llm_client.py         # 流式调用 + 多端点热备 + 严格模型路由 + 视觉 + 搜索
│  ├─ agent.py              # 47 个工具的真实执行 + 技能/定时/子代理 + 别名路由
│  ├─ skills.py             # 技能系统（内置 9 个 + 工作区自建）
│  ├─ scheduler.py          # 定时任务（自然语言时间解析 + 后台线程调度）
│  ├─ hotkey.py             # 全局热键（RegisterHotKey，无键盘钩子）
│  ├─ asr.py / tts.py       # 语音识别 / 语音合成
│  ├─ dingtalk.py           # 钉钉（webhook / 企业内部应用 / Stream）
│  ├─ updater.py            # GitHub Releases 检查更新
│  ├─ security.py           # DPAPI 落盘加密 + 混淆
│  ├─ docread.py            # 文档解析
│  ├─ docwrite.py           # 文档生成
│  ├─ memory.py             # 长期记忆
│  ├─ plan.py               # 任务计划
│  ├─ knowledge.py          # 本地知识库（倒排索引 + BM25）
│  ├─ trace.py              # 工具调用追踪
│  ├─ mcp_client.py         # 最小 MCP 客户端（stdio + JSON-RPC）
│  ├─ workspace.py          # 设置 / 对话持久化
│  ├─ themes.py             # 主题
│  ├─ markdown_render.py    # Markdown -> 高亮 HTML
│  └─ gui/
│     ├─ main_window.py     # 主窗口（托盘 / 热键 / 附件 / 后台 Agent 通道）
│     ├─ pages.py           # 8 个页面（含技能页 / 定时页）
│     ├─ chat_worker.py     # 前台 Agent 多轮循环
│     ├─ voice_bar.py       # 语音条
│     ├─ widgets.py         # 消息卡片等
│     ├─ panels.py          # 计划面板 / 记忆 / 工具记录 / MCP
│     └─ settings_dialog.py
└─ tests/                   # 自动化测试（开发用，见下）
```

---

## 运行（开发模式）

依赖 Python 3.12 / 3.13 + 以下包：

```powershell
pip install PyQt6 requests markdown Pygments pyinstaller ^
            python-docx openpyxl pdfminer.six beautifulsoup4 lxml ^
            python-pptx reportlab psutil pyperclip mss pillow ^
            pyaudio edge-tts dingtalk-stream websocket-client pywin32
python main.py
```

> - 文档**解析**：python-docx / openpyxl / pdfminer / beautifulsoup4 / lxml（本地）
> - 文档**生成**：python-docx / openpyxl / python-pptx / reportlab（本地）
> - 图片识别：多模态视觉模型（需联网，走你配的智谱 / 百炼 key）
> - 语音：pyaudio 录音 + `glm-asr-2512` 识别；播报用 SAPI 或 edge-tts

首次启动会让你选「工作区」目录（对话历史、文件、设置都存这里）。

自检（不开界面，快速验证全部模块可加载）：

```powershell
python main.py --selftest
```

测试套件：

```powershell
python tests/test_v91.py         # 单元测试：技能/定时/子代理/别名路由/提示词
python tests/smoke_gui_v9.py     # GUI 冒烟：8 个页面能否构建
python tests/smoke_gui_v91.py    # GUI 冒烟：技能页/定时页/附件/编辑/热键/托盘
python tests/live_v91.py         # 真实模型 E2E（需联网 + 已配 key）
python tests/check_nosecrets.py  # 扫描产物：确认没有明文密钥
```

---

## 打包为 exe

```powershell
python build_v9.py
```

产物在 `dist_v9\AIWorkbench\AIWorkbench.exe`（onedir 模式，附带依赖），并在 `dist_v9\` 生成 `AIWorkbench_v9.zip`。

> **打包注意**：
> - `python-docx` / `python-pptx` 依赖包内模板文件，`reportlab` 依赖字体数据 —— 脚本已用 `--collect-data` / `--collect-submodules` 显式收进包。
> - 本机若同时装有 PyQt5 / PySide，会导致 PyInstaller abort —— 脚本已显式 `--exclude-module`。
> - exe 带版本信息（公司名 / 描述 / 版本号），可在文件属性里看到。

打包完成后验证：

```powershell
dist_v9\AIWorkbench\AIWorkbench.exe --selftest
```

---

## 上架 GitHub + 自动打包发布

仓库里带了 `.github/workflows/build-release.yml`：

1. 在 GitHub 上建一个空仓库，把本目录推上去（`main` 分支）。
2. 打一个 tag 推上去：

   ```bash
   git tag v9.0.0
   git push origin v9.0.0
   ```

3. Actions 自动：装依赖 → 跑自检 → 跑单元测试 → 跑 `check_nosecrets.py` 验无明文密钥 → PyInstaller 打包 → 打 zip → 上传为 **Release 附件**。

程序端「关于」页和 `check_update` 工具会读 `releases/latest` 提示新版本（**不需要 token**）。

> 密钥怎么进 exe？见下一节 —— **用 GitHub Secret 注入，仓库里永远没有明文**。

---

## API 密钥安全

要求：**密钥必须随 exe 交付给用户直接可用，但不得有任何形式的明文存储。**

实现方式（三层）：

1. **内嵌混淆密文** —— `tools/gen_keyblobs.py` 把明文 key 用随机 nonce 做 XOR 混淆，生成 `app/_keyblobs.py`（内容是一串乱码字节）。
   - `app/_keyblobs.py` **已加入 `.gitignore`**，不进仓库。
   - 运行时 `app/config.py` 用同样的混淆逻辑还原到内存，进程退出即消失，**磁盘上永远只有乱码**。
2. **落盘 DPAPI 加密** —— 用户在工作区改过的 key，用 Windows DPAPI（`CryptProtectData`）加密后写进 `settings.json`，只有本机本用户能解密。
3. **自动验证** —— `tests/check_nosecrets.py` 扫描产物目录与 `app/` 源码，确认没有出现任何明文密钥字符串；CI 里作为**卡点**，不通过就不发 Release。

本地生成密文：

```powershell
# 方式一：环境变量
set AIWB_ZHIPU_KEY=xxxx
set AIWB_BAILIAN_KEY=xxxx
python tools/gen_keyblobs.py --from-env

# 方式二：从一个 json 读
python tools/gen_keyblobs.py --from-json D:\secret\keys.json
```

> ⚠️ 请勿把 `app/_keyblobs.py` 或 `keys.json` 提交到公开仓库。CI 打包时从 GitHub Secrets 读取并即时生成。

---

## 工作区里都有什么

| 文件 / 目录 | 作用 |
| --- | --- |
| `settings.json` | API 密钥（DPAPI 加密）、系统提示词、所选模型、主题、托盘/热键配置等 |
| `conversations/` | 每个对话一个 json（含任务计划、附件信息） |
| `files/` | AI 的默认文件沙箱（相对路径都落这里） |
| `skills/` | **自建技能**（`<技能名>/SKILL.md`） |
| `tasks.json` | **定时任务**定义与上次执行记录 |
| `MEMORY.md` | **长期记忆**，可直接编辑 |
| `.index/knowledge.json` | 本地知识库索引（`build_index` 生成） |
| `traces/tool_calls.jsonl` | **工具调用记录**（名称 / 参数 / 耗时 / 结果） |
| `mcp.json` | （可选）MCP 服务器配置，格式同 Claude / WorkBuddy |
| `exports/` | 导出的对话 markdown |
| `updates.log` | 检查更新的记录 |

---

## MCP 配置示例

在工作区建 `mcp.json`：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\"]
    },
    "my-server": {
      "command": "python",
      "args": ["D:\\my\\mcp_server.py"],
      "env": {"TOKEN": "xxx"}
    }
  }
}
```

保存后点「集成」→「重新载入配置」→「扫描并列出工具」。之后 AI 就能用 `mcp_list` / `mcp_call` 调用这些外部工具（服务器进程在程序退出时自动关闭）。

---

## 安全说明

- **密钥不留明文**：内嵌密文 + DPAPI 落盘；`settings.json` 只在工作区目录，不上传。请勿把工作区提交到公开仓库。
- **写入范围**：可读写任意非系统目录；系统关键目录（Windows / Program Files / ProgramData / 本程序安装目录）禁止写入。
- **删除可恢复 + 需确认**：删除只移入 Windows 回收站且必须确认；命令执行、结束进程、含删除的 Python 代码前必须确认；格式化 / 关机等危险命令直接拒绝。
- **代码执行有护栏**：`run_python` 在子进程里跑，工作目录是工作区 `files/`，有超时限制；含 `shutil.rmtree` / `os.remove` / 注册表 / `taskkill` 等破坏性模式的代码会先弹确认。
- **图片识别会联网**：图片转 base64 发给视觉模型（智谱 / 百炼）；文档解析与生成全部在本机完成，不上传。
- **语音识别会联网**：录音会发给语音识别端点（智谱）转文字。
- **钉钉推送**：只有在你配置了钉钉并主动勾选「推送」时才会外发内容。
- **MCP** 会启动你在 `mcp.json` 里配置的本地进程 —— 只配置你信任的服务器，它拥有和你同等的权限。
- **长期记忆 / 工具记录都在本地**：`MEMORY.md` 与 `traces/` 只写在工作区目录，随时可查看、编辑、清空。
- **全局热键不装键盘钩子**：只用 Windows 官方 `RegisterHotKey`，不监听你的全部按键。

---

## 已知限制

- 代码高亮基于 Pygments，少数冷门语言可能回退为纯文本（不影响功能）。
- 免费端点有额度限制，超额会自动切换到下一个可用端点（热备）；**严格模式下不会静默换模型**，会明确提示。
- 本机无图形界面的服务器环境无法运行 GUI（需 Windows 桌面）。
- `web_search` 依赖服务商联网能力；硅基流动 / DeepSeek 官方端点本身不支持联网，会自动跳过。
- 本地知识库是**关键词 / BM25** 检索，不是向量语义检索；用词差异大时可能漏召回（换几个关键词再试）。
- 语音识别与播报需要联网与对应端点额度；SAPI 播报为离线。
- 自动检查更新读的是 **GitHub Releases**，本机网络若无法访问 GitHub 会静默跳过（不影响主功能）。

---

**License**：个人自用 / 学习用途。使用的各家模型 API 需遵守对应服务商条款。
