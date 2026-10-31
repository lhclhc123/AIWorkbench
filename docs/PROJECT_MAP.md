# 项目地图

<!-- AUTO:BEGIN -->
### A. 源码文件总表（行数实时统计）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `build_v5.py` | 25 |  |
| `build_v6.py` | 29 |  |
| `build_v7.py` | 41 |  |
| `build_v8.py` | 65 |  |
| `build_v9.py` | 151 |  |
| `build_v910.py` | 181 |  |
| `build_v911.py` | 199 |  |
| `build_v912.py` | 199 |  |
| `build_v913.py` | 233 |  |
| `build_v9131.py` | 247 |  |
| `build_v9140.py` | 178 |  |
| `build_v9141.py` | 179 |  |
| `build_v9142.py` | 216 |  |
| `build_v9150.py` | 201 |  |
| `build_v9151.py` | 202 |  |
| `build_v9160.py` | 221 |  |
| `build_v9170.py` | 203 |  |
| `build_v9180.py` | 259 |  |
| `build_v9190.py` | 259 |  |
| `build_v9191.py` | 262 |  |
| `build_v9200.py` | 257 |  |
| `build_v9210.py` | 268 |  |
| `build_v9211.py` | 268 |  |
| `build_v9212.py` | 268 |  |
| `build_v9220.py` | 268 |  |
| `build_v9221.py` | 268 | PyInstaller 打包脚本（当前版本）：onedir + zip |
| `build_v93.py` | 155 |  |
| `build_v94.py` | 155 |  |
| `build_v95.py` | 152 |  |
| `build_v96.py` | 152 |  |
| `build_v97.py` | 152 |  |
| `build_v971.py` | 161 |  |
| `build_v98.py` | 181 |  |
| `build_v99.py` | 181 |  |
| `main.py` | 505 | 程序入口：解析命令行（--selftest / --ui-engine / --smoke…）、单实例、启动 MainWindow |
| `app/__init__.py` | 0 | 包初始化（保持空，避免循环导入） |
| `app/_keyblobs.py` | 13 | **不进取仓库**的密文密钥（DPAPI 加密） |
| `app/agent.py` | 5074 | Agent 核心：消息编排、提示词拼装、工具调用解析（5 路）、工具执行、完成度判据、计划管理 |
| `app/asr.py` | 445 | 语音识别（多 provider） |
| `app/assistant.py` | 263 | 钉钉助理：轮询、代回、范围控制、定时任务 |
| `app/config.py` | 1055 | **全部静态配置与系统提示词**：模型源、优先级、免费标记、工具清单、超时、PROMPT_VERSION |
| `app/devtools.py` | 805 | 读代码类工具：find_lines / read_file(start,limit) / edit_lines / apply_patch |
| `app/dingtalk.py` | 1371 | 钉钉：dws CLI 的查找与调用（`find_dws` / `_run_dws`）、OAuth、消息读写 |
| `app/docread.py` | 259 | 文档解析（docx/pdf/xlsx/pptx/html） |
| `app/docwrite.py` | 700 | 文档生成（docx/xlsx/pptx/pdf） |
| `app/downloader.py` | 390 | 下载加速底层：镜像候选、并发测速、断点续传、假响应过滤 |
| `app/folders.py` | 208 | 常用文件夹定位（桌面/下载/文档） |
| `app/hotkey.py` | 231 | 全局快捷键 |
| `app/icons.py` | 136 | SVG 单色图标 |
| `app/knowledge.py` | 258 | 知识库切片与检索 |
| `app/llm_client.py` | 717 | 所有模型源的 HTTP 客户端 + `chat_auto()` 自动降级链 + 流式解析 |
| `app/markdown_render.py` | 371 | Markdown → HTML / 代码块切分（`render_flow` 保序、`render_with_blocks` 兼容老路径）+ Pygments 高亮 |
| `app/mcp_client.py` | 273 | MCP 客户端（外部工具服务） |
| `app/memory.py` | 435 | 长期记忆：主记忆 + 本对话记忆的读写与提炼 |
| `app/plan.py` | 165 | 任务计划（update_plan）的数据结构与落盘 |
| `app/ratelimit.py` | 268 | 限流与重试退避 |
| `app/scheduler.py` | 385 | 定时任务调度 |
| `app/security.py` | 604 | 安全闸门：危险命令判定、工作区内部目录保护、确认框回调 |
| `app/single_instance.py` | 320 | 单实例锁（第二次启动激活已有窗口） |
| `app/skills.py` | 341 | 技能（SKILL.md）发现与加载 |
| `app/taskman.py` | 538 | 后台任务池：run_bg / bg_wait / MAX_RUNNING 限流 |
| `app/team.py` | 907 | 多角色团队：成员、任务分解、并行执行、结果汇总 |
| `app/team_presets.py` | 865 | 团队角色预设（pm/arch/dev×N/qa/lead 的提示词） |
| `app/themes.py` | 307 | 主题 token（颜色/字号） |
| `app/tools_more.py` | 2866 | 工具实现集合：文件、命令、网络、文档、图像、剪贴板、进程…（`KNOWN_TOOLS` 的来源之一） |
| `app/trace.py` | 114 | 调用追踪（traces/tool_calls.jsonl） |
| `app/tts.py` | 264 | 语音合成（sapi / edge） |
| `app/updater.py` | 633 | 自动更新：检查 → 多通道下载 → 校验 → 安装 → 重启（阶段文案面向用户） |
| `app/version.py` | 47 | 版本号 / 构建日期 / GitHub 仓库地址 / 版本比较 |
| `app/webcap.py` | 1258 | 网页采集三件套：web_scrape / web_form_batch / github |
| `app/webengine.py` | 635 | 内嵌浏览器内核封装（抓取与搜索） |
| `app/webui_bridge.py` | 245 | Qt ↔ 网页 的桥：QWebChannel 暴露 + 下行业务事件（init/messages/token/tool_*/convs/im_refresh…） |
| `app/webui_host.py` | 193 | Web UI 宿主：装配 QWebEngineView、profile、权限、生命周期 |
| `app/winproc.py` | 169 | **所有子进程的唯一出口**（隐藏控制台 + 记录 spawn 日志）；app/ 下禁止裸 subprocess |
| `app/worklog.py` | 205 | 工作总结索引（原子写 + 自愈 + recent 重试） |
| `app/workspace.py` | 698 | 工作区：目录布局、会话/产物/记忆/日志的落盘位置、`.awb/` 内部目录迁移 |
| `tools/fix_privacy.py` | 101 | 修复隐私扫描问题的辅助脚本 |
| `tools/gen_keyblobs.py` | 108 | 把明文 key 生成密文 blob |
| `tools/gen_project_map.py` | 265 | 本脚本：抽取行号生成交接文档的 AUTO 区块 |
| `tools/privacy_scan.py` | 162 | 隐私/密钥扫描（推送与打包前的硬门槛） |
| `tools/privacy_scan_staged.py` | 102 | 只扫已暂存文件的版本 |
| `tools/push_release.py` | 148 | 一键推送 + 隐私扫描 + 打 tag + 发 Release |
| `app/gui/__init__.py` | 0 |  |
| `app/gui/browser_page.py` | 426 | 内嵌浏览器页（网页采集/预览） |
| `app/gui/chat_worker.py` | 1345 | 一轮对话的后台线程：调模型、解析工具、执行工具、发信号（token/tool_start/tool_result/finished） |
| `app/gui/main_window.py` | 7822 | **主窗口 + 全 Web UI 的 RPC 分发**：对话、深链、更新、钉钉、设置、团队都挂在这里 |
| `app/gui/pages.py` | 3823 | Qt 各功能页（欢迎页 / 记忆 / 追踪 / 技能 / 设置 / 集成 / 关于…） |
| `app/gui/panels.py` | 344 | 右侧计划面板 PlanPanel |
| `app/gui/settings_dialog.py` | 104 | 旧版设置对话框（已被 Web 设置页取代，保留兜底） |
| `app/gui/startup_dialog.py` | 64 | 启动等待框（模型源预热的遮罩） |
| `app/gui/team_bar.py` | 283 | 团队工具条（成员与状态） |
| `app/gui/voice_bar.py` | 208 | 语音输入条 |
| `app/gui/widgets.py` | 1624 | Qt 卡片控件：MessageCard（含 flow 顺序模式）、CodeBlockWidget、ProcessGroup、StatusLine 等 |
| `app/deepseek_web/__init__.py` | 30 |  |
| `app/deepseek_web/api_path.py` | 615 |  |
| `app/deepseek_web/cdp_client.py` | 288 | 极简 CDP WebSocket 客户端（绕过系统代理劫持回环） |
| `app/deepseek_web/embedded.py` | 447 | 内嵌 QWebEngine 引擎（不依赖本机 Chrome） |
| `app/deepseek_web/errors.py` | 122 | 网页版错误枚举 + 人话文案 |
| `app/deepseek_web/local_server.py` | 765 | 对上层伪装成 OpenAI 兼容 HTTP 服务；负责**会话路由**（严格/宽松指纹、绝不每轮新建） |
| `app/deepseek_web/prompt.py` | 182 | 消息摊平与增量差分：`flatten` / `diff_tail` / `conversation_key` / `tight 裁剪` |
| `app/deepseek_web/service.py` | 1089 | 网页版服务门面：启动/复用浏览器、状态机、登录引导、窗口显隐 |
| `app/deepseek_web/web_driver.py` | 1670 | CDP 驱动层：注入 JS、发消息、等答复、取思维链/正文（含 cleanText 摘除 ds-think-content） |

**Python 源码合计 54109 行**（不含 tests/）。


### B. 前端资源（Web UI）

| 文件 | 行数 | 职责 |
|---|---:|---|
| `im.html` | 227 | 钉钉助手独立页（对话/日志/标签/设置四标签）+ CLI 配置进度窗口 |
| `index.html` | 177 | 主界面骨架：侧栏 + 五个 section.page + 对话区 DOM + 模型菜单 |
| `css/base.css` | 45 | 重置与基础排版 |
| `css/chat.css` | 536 | 对话页：消息、工具胶囊、过程组、思维卡、代码块 |
| `css/im.css` | 277 | 钉钉助手页样式 |
| `css/pages.css` | 172 | 各功能页样式 |
| `css/tokens.css` | 79 | 主题变量（明暗两套） |
| `js/app.js` | 182 | 启动、主题、侧栏壳、DeepSeek 连通性弹窗 |
| `js/bridge.js` | 96 | QWebChannel 封装：call / on / start |
| `js/im.js` | 655 | 钉钉助手页逻辑：轮询、状态、CLI 进度窗口、标签与设置 |
| `js/nav.js` | 105 | section.page 路由与切换 |
| `js/store.js` | 35 | 极简全局状态 + subscribe |
| `js/transport-qwebchannel.js` | 105 | QWebChannel 传输层 |
| `js/components/msg.js` | 262 | `<msg-bubble>` 自定义元素（流式分段 + 原地 markdown） |
| `js/components/think-card.js` | 242 | `<think-card>`：思维链 / 过程组共用载体（setProcess/setMarkdownFn） |
| `js/components/tool-capsule.js` | 115 | `<tool-capsule>`：工具胶囊（默认收起，点开看结果） |
| `js/views/about.js` | 399 | 关于页：检查更新、进度条、版本信息 |
| `js/views/bg.js` | 17 | 后台任务视图 |
| `js/views/chat.js` | 928 | 对话流渲染：消息、流式、过程分组 planNodes、思维卡、输入区 |
| `js/views/integrations.js` | 388 | 集成页 |
| `js/views/memory.js` | 94 | 记忆页 |
| `js/views/settings.js` | 475 | 设置页 |
| `js/views/skills.js` | 186 | 技能页 |
| `js/views/tasks.js` | 240 | 并行任务页 |
| `js/views/team.js` | 17 | 团队页 |
| `js/views/tools.js` | 158 |  |

### C. 关键函数明细（行号 = 当前真实行号）


#### `build_v8.py`（65 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|

#### `build_v9.py`（151 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 76–106 |  |
| 函数 | `make_zip` | 134–146 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v910.py`（181 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 107–137 |  |
| 函数 | `make_zip` | 165–177 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v911.py`（199 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 125–155 |  |
| 函数 | `make_zip` | 183–195 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v912.py`（199 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 125–155 |  |
| 函数 | `make_zip` | 183–195 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v913.py`（233 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 159–189 |  |
| 函数 | `make_zip` | 217–229 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9131.py`（247 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 173–203 |  |
| 函数 | `make_zip` | 231–243 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9140.py`（178 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 104–134 |  |
| 函数 | `make_zip` | 162–174 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9141.py`（179 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 105–135 |  |
| 函数 | `make_zip` | 163–175 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9142.py`（216 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 142–172 |  |
| 函数 | `make_zip` | 200–212 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9150.py`（201 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 127–157 |  |
| 函数 | `make_zip` | 185–197 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9151.py`（202 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 128–158 |  |
| 函数 | `make_zip` | 186–198 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9160.py`（221 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 147–177 |  |
| 函数 | `make_zip` | 205–217 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9170.py`（203 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 129–159 |  |
| 函数 | `make_zip` | 187–199 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9180.py`（259 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 150–176 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 179–209 |  |
| 函数 | `make_zip` | 243–255 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9190.py`（259 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 150–176 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 179–209 |  |
| 函数 | `make_zip` | 243–255 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9191.py`（262 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 153–179 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 182–212 |  |
| 函数 | `make_zip` | 246–258 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9200.py`（257 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 148–174 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 177–207 |  |
| 函数 | `make_zip` | 241–253 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9210.py`（268 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 152–178 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 181–211 |  |
| 函数 | `make_zip` | 252–264 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9211.py`（268 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 152–178 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 181–211 |  |
| 函数 | `make_zip` | 252–264 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9212.py`（268 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 152–178 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 181–211 |  |
| 函数 | `make_zip` | 252–264 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9220.py`（268 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 152–178 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 181–211 |  |
| 函数 | `make_zip` | 252–264 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v9221.py`（268 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_qtwebengine_add_data` | 152–178 | 收集 PyQt6-WebEngine 的运行时资源，返回 `--add-data` 参数串列表。 |
| 函数 | `make_version_file` | 181–211 |  |
| 函数 | `make_zip` | 252–264 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v93.py`（155 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 80–110 |  |
| 函数 | `make_zip` | 138–150 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v94.py`（155 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 80–110 |  |
| 函数 | `make_zip` | 138–150 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v95.py`（152 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 78–108 |  |
| 函数 | `make_zip` | 136–148 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v96.py`（152 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 78–108 |  |
| 函数 | `make_zip` | 136–148 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v97.py`（152 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 78–108 |  |
| 函数 | `make_zip` | 136–148 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v971.py`（161 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 87–117 |  |
| 函数 | `make_zip` | 145–157 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v98.py`（181 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 107–137 |  |
| 函数 | `make_zip` | 165–177 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `build_v99.py`（181 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `make_version_file` | 107–137 |  |
| 函数 | `make_zip` | 165–177 | 把 onedir 产物打成 zip，方便直接分发给别人。 |

#### `main.py`（505 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `selftest` | 46–409 | 验证冻结包里各项能力是否真的能导入并跑通。返回退出码。 |
| 函数 | `main` | 412–501 |  |

#### `app/agent.py`（5074 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_coerce` | 196–208 | 把解析出来的 JSON 规范化成 {name, arguments}。 |
| 函数 | `_repair_json` | 218–322 | 把模型写坏的 JSON 转义修回来。 |
| 函数 | `_loads_lenient` | 325–334 | 先严格解析，失败再用 _repair_json 修一次。 |
| 函数 | `normalize_text_content` | 341–368 | 把"换行被双重转义"的内容修回来。 |
| 函数 | `_iter_json_objects` | 371–383 | 在文本里找出所有可解析的 JSON 对象（应对模型不按格式输出的情况）。 |
| 函数 | `_json_objects` | 386–401 | 提取文本中所有能被解析的 {...} 片段（越长的优先）。 |
| 函数 | `_degraded_call` | 404–430 | 退化写法：`工具名` 紧跟一个 JSON 参数对象。 |
| 函数 | `_dsml_name` | 471–479 | DSML 里给的工具名归一成真正会执行的那个（别把别名丢了）。 |
| 函数 | `_dsml_calls` | 482–514 | 解析 DSML 工具调用，返回 [{name, arguments}, ...]；解析不出就返回 []。 |
| 函数 | `strip_tool_text` | 564–568 | 把所有工具调用标记从一段文字里去掉，返回剩下的正文（已 strip）。 |
| 函数 | `_xmlarg_value` | 571–580 | XML 参数值 -> Python 值：能当 JSON 解就解，否则按原文（保留代码缩进）。 |
| 函数 | `_xmlarg_calls` | 583–624 | 解析智谱 XML 风格工具调用，返回 [{name, arguments}, ...]；解析不出返回 []。 |
| 函数 | `parse_tool_call` | 627–642 | 从助手文本里尽力提取工具调用，返回 {name, arguments} 或 None。 |
| 函数 | `_parse_tool_call_once` | 645–721 | 真正的解析逻辑（不含代码块兜底）。 |
| 函数 | `_dedupe` | 826–837 | 去掉连续重复的同一个调用（模型常把同一个调用写两遍）。 |
| 函数 | `parse_tool_calls` | 840–871 | 提取一条回复里的**全部**工具调用。 |
| 函数 | `filename_stem_from_text` | 879–887 | 从用户话里找他想要的文件名主干（"帮我写个 calc.py" -> "calc"）。 |
| 函数 | `synthesize_write_from_fence` | 890–907 | 模型只丢了一个代码块、压根没调 write_file 时，帮它凑一个写入调用。 |
| 函数 | `wants_real_action` | 910–917 | 用户这句话是不是想让 AI 对自己的电脑/文件真的做点什么。 |
| 函数 | `needs_write_action` | 920–929 | 用户这句话是不是要求真正写入/修改文件（用于识别"只读不写"的退缩）。 |
| 函数 | `needs_read_action` | 932–935 | 用户这句话是不是要求真正去读取/列出/查询数据（未执行工具就下结论=编造）。 |
| 函数 | `needs_exe` | 947–958 | 用户这句话是不是明确要打包出 exe / 安装包。 |
| 函数 | `dumped_to_cloud` | 984–987 | 答复是不是把交付甩给了第三方网盘/云盘服务。 |
| 函数 | `needs_web_search` | 1013–1031 | 这一轮该不该让端点自动联网。 |
| 函数 | `honest_ok` | 1034–1061 | 按**真实的执行证据**判定这一轮到底做成没有，返回 (ok, notes)。 |
| 函数 | `looks_like_giveup` | 1064–1067 | 答复里是不是在让用户自己干 / 宣布自己干不了。 |
| 函数 | `_is_meaningless_input` | 1116–1137 | 这句话是不是没有实质内容（误触 / 寒暄 / 纯应付）。 |
| 函数 | `has_cancel_intent` | 1140–1147 | 用户是否明确说"这条不算 / 不要做了"（取消意图）。 |
| 函数 | `_has_substantive_tools` | 1150–1155 | 本轮是否真的动过"实质工具"。 |
| 函数 | `should_nudge_pending_plan` | 1158–1167 | 残留 plan 该不该催办 —— **唯一判据**，看门狗与 chat_worker 共用。 |
| 函数 | `_has_real_request` | 1170–1184 | 本轮用户是否真的提出了一个明确的新请求（= 派活）。 |
| 函数 | `completion_gaps` | 1187–1266 | 系统级「到底做完没有」检查，返回还没做到的事（字符串列表）。 |
| 函数 | `judge_payload` | 1284–1292 | 给裁判用的消息体。 |
| 函数 | `needs_project_flow` | 1301–1315 | 用户这句话是不是「要你独立开发出一个成品」。 |
| 函数 | `verify_failed` | 1373–1388 | 程序代跑的那次验证，到底过没过。 |
| 函数 | `likely_ok` | 1391–1397 | 验证输出是否可判定为「通过」（供闭环决定是否收尾）。 |
| 函数 | `is_key_progress` | 1412–1428 | 中间过程里这一句值不值得**默认展开**给用户看。 |
| 函数 | `count_requests` | 1450–1467 | 粗略统计用户一条消息里提了几个要求（用于检查"是不是只做了一部分"）。 |
| 函数 | `needs_remember` | 1470–1472 |  |
| 函数 | `needs_plan` | 1475–1477 |  |
| 函数 | `gave_code_only` | 1522–1528 | 助手只给了代码（代码块/命令行），没给出任何执行结果。 |
| 函数 | `faked_tool_result` | 1536–1538 | 模型自己编造了「工具返回」内容（没有真的调用），必须打回。 |
| 函数 | `is_meta_talk` | 1562–1566 | 回复对象变成了系统说明本身，而不是用户的问题。 |
| 函数 | `tool_result_feedback` | 1569–1572 | 把工具真实结果包装成回喂消息（措辞刻意不像助手口吻，降低被模仿的概率）。 |
| 函数 | `looks_like_tool_attempt` | 1575–1582 | 文本看起来想调用工具但格式不对 —— 这种情况要纠正重试，而不是当成最终答复。 |
| 函数 | `claims_wrote_file` | 1596–1604 | 模型声称「已经创建/写入了文件」。用它来抓"假成功"。 |
| 函数 | `_decode` | 1615–1626 | Windows 中文命令输出可能是 GBK，按 utf-8 -> gbk -> 容错顺序解码，绝不抛异常。 |
| 函数 | `_unescape` | 1629–1631 | 模型常把 Windows 路径写成 D:\\ 或 D:\\ 之类的怪转义，这里宽容还原。 |
| 函数 | `_decode_html` | 1634–1696 | 把网页字节解成文本，尽量不出乱码。 |
| 函数 | `_looks_mojibake` | 1702–1722 | 粗略判断是不是「UTF-8 被按单字节解码」产生的乱码。 |
| 函数 | `_loose_call` | 1725–1745 | JSON 严格解析失败时的最后兜底：用正则把 name / 参数抠出来。 |
| 函数 | `extract_fence` | 1752–1766 | 取出消息里最大的那个 ```代码块```，返回 (代码, 语言)。 |
| 函数 | `_sniff_lang` | 1788–1803 | 从代码正文猜语言（保守：只认特征非常明确的几种）。返回 _LANG_EXT 的键。 |
| 函数 | `_fix_ext_for_language` | 1806–1829 | 把扩展名纠成与语言一致。返回 (新路径, 说明文字)；没动手则说明为空。 |
| 函数 | `_looks_truncated` | 1839–1874 | 内容看起来是不是「被截断 / 转义坏掉」的半成品。 |
| 函数 | `_salvage_from_fence` | 1877–1910 | 把消息里 ```代码块``` 的真实代码，补进被写坏的长文本参数。 |
| 函数 | `_normalize_rel` | 1913–1929 | 规范化相对路径。 |
| 函数 | `_inside` | 1932–1939 | 判断给定路径是否仍落在工作区 files/ 沙箱内（用于标注"工作区外只读"）。 |
| 函数 | `_safe_path` | 1942–1973 | 把用户给的路径解析为绝对路径。 |
| 函数 | `_danger_reason` | 1976–2005 | 返回 (是否危险, 中文原因)。 |
| 函数 | `_is_dangerous` | 2008–2009 |  |
| 函数 | `window_spawning` | 2053–2059 | 返回这条命令会新开窗口的原因，不开窗则返回空字符串。 |
| 函数 | `is_readonly_command` | 2062–2074 | 这条命令是不是"只看看不动手"——是就不用弹确认框。 |
| 函数 | `_as_error` | 2077–2087 | 把"路径解析/沙箱闸门"返回的说明包装成给模型看的错误串。 |
| 函数 | `_is_system_path` | 2090–2109 | 是不是系统关键位置。 |
| 函数 | `_human_size` | 2112–2120 |  |
| 函数 | `_to_recycle_bin` | 2123–2149 | 把文件/目录移入 Windows 回收站（可恢复）。成功返回 True。 |
| 函数 | `_py_dangerous` | 2165–2166 |  |
| 函数 | `_find_python` | 2169–2196 | 找一个真实可用的 Python 解释器。 |
| 函数 | `_arg_bool` | 2199–2210 | 把模型给的 true/"true"/1/"是" 之类统一成 bool。 |
| 函数 | `_rc_hint` | 2213–2226 | 把子进程的退出码翻译成一句"给模型看的硬提醒"。 |
| 函数 | `_busy_err` | 2229–2233 | 是不是"文件被别的程序占用 / 没权限"这类**值得重试**的错误。 |
| 函数 | `_utf8_env` | 2236–2246 | 子进程统一 UTF-8。 |
| 函数 | `_python_candidates` | 2249–2279 | 按优先级列出本机可能可用的 Python 解释器（去重）。 |
| 函数 | `_python_with_module` | 2282–2292 | 返回第一个能 `import <module>` 成功的解释器路径；都没有则 None。 |
| 函数 | `_toast` | 2295–2328 | 弹一个 Windows 桌面通知。成功返回 True。多级降级，绝不抛异常。 |
| 函数 | `_list_windows` | 2331–2363 | 用 Win32 API 列出当前有标题的可见窗口。 |
| 函数 | `_grab_screen` | 2366–2389 | 截取全屏（或指定区域）保存到 path。成功返回 True。 |
| 函数 | `_screen_size` | 2392–2407 | 返回主屏分辨率字符串，例如 1920x1080。 |
| 函数 | `_image_op` | 2416–2504 | PIL 图片处理。返回结果文字。 |
| 函数 | `_derive_img_path` | 2507–2512 |  |
| 函数 | `_create_chart` | 2515–2608 | 用 PIL 画柱状/折线/饼图（不引入 matplotlib，打包体积小）。 |
| 函数 | `parse_when` | 2616–2656 | 把「10分钟后 / 2小时以后 / 18:30 / 明天9点」解析成时间戳。 |
| 类 | `AgentRunner` | 2659–5074 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.__init__` | 2660–2727 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.files_dir` | 2730–2744 | 当前产物目录 —— **实时**从 UI 层注入的 files_dir_cb 取。 |
| 方法 | &nbsp;&nbsp;`AgentRunner.memory` | 2748–2752 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.conv_memory` | 2755–2778 | ★ v9.17.0 当前对话专属的记忆（`chats/<NN_标题>/MEMORY.md`）。 |
| 方法 | &nbsp;&nbsp;`AgentRunner.kb` | 2781–2785 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.trace` | 2788–2792 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.skills` | 2795–2799 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.scheduler` | 2802–2806 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner.attach_subsystems` | 2808–2846 | 工作区切换后重新挂接回调。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._resolve` | 2848–2853 | 路径解析：绝对路径直接用，相对路径相对工作区 files/。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._note_blocked` | 2855–2864 | 把"沙箱拒绝"如实记进 trace，方便用户事后查（绝不静默）。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._guard_write_path` | 2866–2906 | ★ v9.18.0 统一"写闸门"：返回 None=放行；否则返回**已带前缀**的中文拒绝说明。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._resolve_write` | 2908–2940 | 写入类路径解析 + 沙箱闸门。返回 (路径, 错误信息)。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._system_info` | 2942–2976 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._note_written` | 2978–2984 | 记下「这一轮真的写出来了什么」，供工作总结 / 完成自检使用。 |
| 方法 | &nbsp;&nbsp;`AgentRunner.canonical_name` | 2986–3007 | 把模型给的（可能是别名 / 技能名 / 近似名）工具名归一成真正会执行的那个名字。 |
| 方法 | &nbsp;&nbsp;`AgentRunner.execute` | 3009–3026 | 执行一个工具调用，返回结果字符串；同时写入工具追踪日志（可观测性）。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_web_visit` | 3037–3121 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._bg_ready` | 3131–3134 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_run_bg` | 3136–3250 | 把一个活儿甩到后台跑，立刻返回任务号。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_bg_list` | 3252–3269 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_bg_status` | 3271–3301 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_bg_wait` | 3303–3333 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_bg_cancel` | 3335–3346 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_team_run` | 3348–3379 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_team_status` | 3381–3421 |  |
| 方法 | &nbsp;&nbsp;`AgentRunner._tool_ask_user` | 3424–3455 | 把问题抛给界面，等用户回答了再继续。 |
| 方法 | &nbsp;&nbsp;`AgentRunner._dispatch` | 3457–5074 | 真正干活的派发逻辑。confirm_cb(cmd)->bool 用于命令/删除/杀进程确认。 |

#### `app/asr.py`（445 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `ASRError` | 27–28 |  |
| 函数 | `audio_dir` | 32–35 |  |
| 函数 | `new_wav_path` | 38–39 |  |
| 函数 | `wav_duration` | 42–47 |  |
| 函数 | `to_16k_mono` | 50–75 | 把任意 WAV 转成 16k / 单声道 / 16bit，返回新路径。 |
| 函数 | `_resample_pcm16` | 78–96 | 线性插值重采样（够用且无依赖）。 |
| 函数 | `list_input_devices` | 99–119 | 列出可用的录音设备，供设置页选择。 |
| 类 | `Recorder` | 123–250 | 麦克风录音器。 |
| 方法 | &nbsp;&nbsp;`Recorder.__init__` | 132–156 | silence_seconds：静音多久算"说完了"（默认 10 秒）。 |
| 方法 | &nbsp;&nbsp;`Recorder.start` | 159–172 |  |
| 方法 | &nbsp;&nbsp;`Recorder._loop` | 174–195 |  |
| 方法 | &nbsp;&nbsp;`Recorder.seconds` | 198–199 |  |
| 方法 | &nbsp;&nbsp;`Recorder.silence_left` | 201–207 | 距离自动停止还有多久（秒）；界面可以倒计时提示。 |
| 方法 | &nbsp;&nbsp;`Recorder.stop` | 209–237 | 停止录音，返回 WAV 路径（失败返回 None）。 |
| 方法 | &nbsp;&nbsp;`Recorder.cancel` | 239–250 |  |
| 函数 | `_rms` | 253–265 | 16bit PCM 的均方根 -> 0~1 的电平值。 |
| 函数 | `_pick` | 269–285 | 从各家不同的返回结构里取出文本。 |
| 函数 | `_asr_zhipu` | 288–300 |  |
| 函数 | `_asr_siliconflow` | 303–315 |  |
| 函数 | `_dashscope_upload` | 318–336 |  |
| 函数 | `_asr_dashscope` | 339–373 |  |
| 函数 | `available_backends` | 391–397 | 哪些后端当前真的能用（有 Key）。 |
| 函数 | `transcribe` | 400–430 | 识别一段音频。返回 (文本, 后端名)。失败抛 ASRError。 |
| 函数 | `recognize_file_async` | 433–445 | 在后台线程里识别，避免卡住界面。 |

#### `app/assistant.py`（263 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `AssistantService` | 28–263 | 钉钉消息轮询服务。 |
| 方法 | &nbsp;&nbsp;`AssistantService.__init__` | 37–51 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.save` | 54–61 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.running` | 63–64 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.apply_cfg` | 66–80 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.mark_processed` | 82–89 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.start` | 92–103 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.stop` | 105–111 |  |
| 方法 | &nbsp;&nbsp;`AssistantService._scope_name` | 113–115 |  |
| 方法 | &nbsp;&nbsp;`AssistantService._loop` | 117–127 |  |
| 方法 | &nbsp;&nbsp;`AssistantService._self_cid` | 130–149 |  |
| 方法 | &nbsp;&nbsp;`AssistantService.scan_once` | 151–248 | 扫一轮，把新消息 emit 出去。返回新消息条数。 |
| 方法 | &nbsp;&nbsp;`AssistantService._wrap` | 250–263 |  |

#### `app/config.py`（1055 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `is_stream_reset` | 306–308 | 这个增量是不是"作废重来"标记（而不是正文）。 |
| 函数 | `strip_stream_reset` | 315–330 | 清掉正文里残留的「作废重来」标记。 |
| 函数 | `provider_by_name` | 964–968 |  |
| 函数 | `model_label` | 971–975 | 'glm-4-flash · 智谱 Zhipu' |
| 函数 | `resolve_model_choice` | 978–1017 | 把界面上的选择解析成 [(provider_dict, model_id), ...]。 |
| 函数 | `all_models_sorted` | 1020–1036 | 返回模型列表，免费优先，便于下拉框展示。 |
| 函数 | `chat_models_for_provider` | 1039–1042 | 某个端点里适合聊天的模型（免费优先，排除纯视觉模型）。 |
| 函数 | `vision_models` | 1045–1055 | 返回可做图片识别的 (provider_dict, model_id) 列表，按 VISION_PRIORITY 排序。 |

#### `app/devtools.py`（805 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_args` | 83–84 |  |
| 函数 | `_s` | 87–95 | 从 args 里按候选键取第一个非空字符串。 |
| 函数 | `_as_int` | 98–104 |  |
| 函数 | `_as_bool` | 107–117 |  |
| 函数 | `_rel` | 120–128 | 相对 base 的展示用路径（拿不到就返回绝对路径）。 |
| 函数 | `_is_binary` | 131–139 | 二进制判定：有 NUL，或前 8KB 里控制字符超过 30%。 |
| 函数 | `_decode` | 142–156 | bytes -> (text, encoding)；解不开抛 ValueError。 |
| 函数 | `_split` | 159–180 | 拆行（内部统一成 \n）。 |
| 函数 | `_encode_with` | 183–190 | 按原编码写回；原编码编不出来时返回 (bytes, 实际编码, 是否换过编码)。 |
| 函数 | `_atomic_write` | 193–215 | 先写 `.tmp` 再 os.replace —— 中途失败绝不动原文件。 |
| 函数 | `_resolve_read` | 218–228 | 只读路径解析（工作区外允许，相对路径相对当前对话的 files/）。 |
| 函数 | `_resolve_write` | 231–241 | 写入路径解析 + 沙箱闸门（跟 write_file 走同一套）。 |
| 函数 | `_read_source` | 244–272 | 读文件并解码。返回 (lines, nl, ends, encoding, err)。 |
| 函数 | `read_file` | 278–359 |  |
| 函数 | `_skip_dir` | 365–370 |  |
| 函数 | `_match_glob` | 373–380 |  |
| 函数 | `_iter_files` | 383–394 | 列出要扫的文件（跳过 .git / __pycache__ / node_modules / dist* …）。 |
| 函数 | `find_lines` | 397–528 |  |
| 函数 | `_line_range_error` | 534–552 | 行号合法性检查，返回中文错误或 None。 |
| 函数 | `_build_result` | 555–564 | 把 [start,end] 换成 new_text，返回 (new_lines, replaced_count, inserted_count)。 |
| 函数 | `_do_edit` | 567–642 |  |
| 函数 | `edit_lines` | 645–675 |  |
| 函数 | `apply_patch` | 681–772 |  |
| 函数 | `readonly_tools` | 800–801 |  |
| 函数 | `describe_all` | 804–805 |  |

#### `app/dingtalk.py`（1371 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `DingTalkError` | 46–47 |  |
| 函数 | `_throttle` | 50–59 | 简单限流，避免触发钉钉「每分钟 20 条」被禁 10 分钟。 |
| 函数 | `_requests` | 62–68 | 优先用 requests（本机 urllib 会被系统代理拦），拿不到再退回 urllib。 |
| 函数 | `_explain_http_error` | 71–86 | 把钉钉的错误体翻译成人话——否则用户只看到一句 HTTP 400，完全没法排查。 |
| 函数 | `_post_json` | 89–130 |  |
| 函数 | `_get_json` | 133–152 |  |
| 函数 | `_find_node` | 164–176 | 找一个可用的 node 可执行文件（优先 WorkBuddy 自带的托管 node）。 |
| 函数 | `find_dws` | 179–226 | 返回 (node_exe, dws_entry)。 |
| 函数 | `_hide_startup` | 229–233 |  |
| 函数 | `_extract_json` | 236–244 | dws 偶尔会在 JSON 前混一两行日志，从第一个 { 取到最后一个 }。 |
| 类 | `OAuthCallbackServer` | 272–345 | 在 127.0.0.1 起一个一次性 HTTP 服务，接住钉钉回调里的 code。 |
| 方法 | &nbsp;&nbsp;`OAuthCallbackServer.__init__` | 279–288 |  |
| 方法 | &nbsp;&nbsp;`OAuthCallbackServer.redirect_uri` | 291–292 |  |
| 方法 | &nbsp;&nbsp;`OAuthCallbackServer.start` | 294–329 |  |
| 方法 | &nbsp;&nbsp;`OAuthCallbackServer.wait` | 331–336 |  |
| 方法 | &nbsp;&nbsp;`OAuthCallbackServer.stop` | 338–345 |  |
| 类 | `DingTalkClient` | 348–1360 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.__init__` | 349–362 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.update` | 365–366 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.mode` | 369–370 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.configured` | 372–377 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.status_text` | 379–391 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.looks_like_nickname` | 393–406 | 接收人里看起来不像 userId 的项（含中文、空格，或以 self 结尾）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._signed_webhook` | 409–423 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient._decorate` | 425–432 | 关键词安全设置：消息里必须含关键词，否则会被拒。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._app_token` | 435–450 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.send_text` | 453–454 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.send_markdown` | 456–457 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.send` | 459–468 | 统一发送入口。content 为正文，title 非空则发 markdown。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._send_webhook` | 470–484 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.list_user_ids` | 487–520 | 列出本组织里当前应用能看到的 userId。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.lookup_user_name` | 522–533 | 查某个 userId 的真实姓名（用来给用户看"这是谁"）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.auto_fix_users` | 535–551 | 把配置里填错的接收人换成通讯录里真实存在的 userId。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.list_departments` | 554–565 | 列出某个部门下的子部门。dept_id=1 是根部门。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.list_members` | 567–587 | 列出某个部门下的成员（含真实姓名）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.send_work_notice` | 590–620 | 发工作通知（钉钉「工作通知」消息）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.get_group` | 623–630 | 按 chatId 查群信息（群名、群主、成员数）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.send_group` | 632–649 | 按 openConversationId 往群里发消息（需要机器人在该群）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.auth_url` | 652–664 | 拼出钉钉扫码授权页地址（用系统浏览器打开它）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.exchange_user_token` | 666–678 | 用回调里的 code 换用户级 accessToken。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.get_login_user` | 680–684 | 拿授权登录用户的信息（昵称、unionId、头像）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.self_test` | 687–830 | 按顺序真调一遍钉钉接口，返回 [(项目, ok, 说明)]。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._send_app` | 833–871 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.test` | 873–902 | 发一条测试消息，返回 (成功?, 说明)。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.stream_available` | 905–910 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.start_stream` | 912–980 | 启动 Stream 长连接，接收钉钉消息。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._reply_long` | 983–992 | 钉钉单条消息有长度限制，过长自动切段回复。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._handler_reply` | 995–1001 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.stop_stream` | 1003–1009 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_available` | 1013–1015 |  |
| 方法 | &nbsp;&nbsp;`DingTalkClient._run_dws` | 1017–1102 | 统一跑 dws 命令。want_json=True 时解析并返回 Python 对象。cwd 用于 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_auth_status` | 1104–1125 | 返回 dws 登录状态 dict：含 authenticated / user_name / corp_name。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_auth_status_async` | 1127–1147 | 后台线程查 dws 登录状态，结果经 ``callback(st)`` 回调，不阻塞调用线程。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.self_test_async` | 1149–1175 | 后台线程跑 :meth:`self_test`（含 dws 子进程，带超时兜底），不阻塞 UI。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_login_start` | 1177–1224 | 后台启动 dws 授权登录（浏览器或设备流），把输出实时回调给 GUI。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_list_conversations` | 1226–1232 | 列出当前用户的会话（群聊 + 单聊）。返回 [(名称, openConversationId)]。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_recent_conversations` | 1234–1242 | 列出某时间之后活跃的会话。start 形如 '2026-09-01' 或 ISO 时间。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_unread` | 1244–1247 | 列出未读会话（含未读消息）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_read_messages` | 1249–1256 | 读取某个会话的最近消息。返回 [(发送者, 文本, 时间)]。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient._msg_text` | 1259–1269 | 把一条消息压成可读文本（含文件名，便于助理看到「发来一个文件」）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_read_messages_raw` | 1271–1277 | 读某会话最近消息的**原始**结构（带 messageId / 发送者 / AI 发送标记）， |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_send_to_cid` | 1279–1287 | 按 openConversationId 直接回一条消息（群聊、单聊都行）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_at_me` | 1289–1300 | 查最近「@我」的消息（群聊里点名叫我的）。解析失败时返回 []。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_dm` | 1302–1311 | 按姓名给某人发单聊文本消息（dws 自动解析唯一接收人）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_send_group` | 1313–1322 | 按群名或 openConversationId 往群里发文本消息。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_conversation_list_full` | 1324–1327 | 给自检用的：直接返回 dws 原始会话列表 JSON（含完整字段）。 |
| 方法 | &nbsp;&nbsp;`DingTalkClient.dws_send_file` | 1329–1360 | 用 dws 以「我本人」身份把本地文件直接发到钉钉（群聊或单聊）。 |
| 函数 | `config_from_settings` | 1363–1371 | 从 settings.json 里取出钉钉配置（兼容扁平与嵌套两种写法）。 |

#### `app/docread.py`（259 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `ext_of` | 42–43 |  |
| 函数 | `file_kind` | 46–48 | 返回人类可读的文件类型，如 'Word 文档'。 |
| 函数 | `is_image` | 51–52 |  |
| 函数 | `is_supported` | 55–58 |  |
| 函数 | `_clip` | 61–65 |  |
| 函数 | `_read_text` | 68–80 | 按常见中文编码依次尝试读取纯文本。 |
| 函数 | `_read_docx` | 83–105 |  |
| 函数 | `_read_pdf` | 108–133 |  |
| 函数 | `_read_xlsx` | 136–157 |  |
| 函数 | `_read_pptx` | 160–184 |  |
| 函数 | `_read_html` | 187–202 |  |
| 函数 | `read_document` | 205–243 | 解析文档并返回纯文本。失败抛异常（附中文原因）。 |
| 函数 | `describe` | 246–259 | 给 UI 用：返回 (类型标签, 大小可读串)。 |

#### `app/docwrite.py`（700 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `inline_plain` | 26–36 | 把行内标记抹平，得到纯文本（Word 标题、Excel 单元格、PPT 要点都要用）。 |
| 函数 | `inline_runs` | 39–63 | 把一行拆成 [(text, bold, italic, code)] 片段，供 Word 做行内加粗。 |
| 函数 | `_strip_links` | 66–67 |  |
| 函数 | `_split_row` | 70–76 |  |
| 函数 | `parse_blocks` | 79–170 | 把 Markdown 文本切成结构块列表。 |
| 函数 | `_resolve_img` | 173–184 | 把 Markdown 里的图片地址解析成本地绝对路径；不是本地存在的文件则返回 None。 |
| 函数 | `_set_east_asian` | 192–203 |  |
| 函数 | `write_docx` | 206–303 |  |
| 函数 | `write_xlsx` | 308–397 |  |
| 函数 | `write_pptx` | 402–535 |  |
| 函数 | `_pdf_font` | 540–557 | 注册中文字体：优先系统 msyh.ttc，失败则用 reportlab 内置 STSong-Light。 |
| 函数 | `write_pdf` | 560–665 |  |
| 函数 | `supported` | 681–682 |  |
| 函数 | `format_label` | 685–686 |  |
| 函数 | `create` | 689–700 | 按扩展名把 Markdown 内容写成目标文档，返回 (描述, 字节数)。 |

#### `app/downloader.py`（390 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_host_of` | 58–63 |  |
| 函数 | `mirror_prefixes` | 66–78 | 返回全部镜像前缀（内置 + 环境变量 AIWORKBENCH_MIRRORS 追加，去重）。 |
| 函数 | `mirror_candidates` | 81–91 | 返回下载候选通道；GitHub 域名自动追加每个镜像前缀各一次。 |
| 函数 | `_is_mirror` | 94–95 |  |
| 函数 | `describe_channel` | 98–102 | 给上层展示用的通道名：镜像返回其域名，直连返回「直连」。 |
| 函数 | `_looks_like_html` | 105–110 | 过滤掉镜像返回的「请验证/网页版」假响应：真二进制/纯文本不会是 text/html。 |
| 函数 | `_probe` | 113–166 | 测「首字节延迟（秒）+ 吞吐（字节/秒）」。失败返回 None。 |
| 函数 | `_score` | 169–173 | 通道综合评分：吞吐优先，延迟兜底（分数越高越优先）。 |
| 函数 | `pick_fastest` | 176–211 | 并发测速选出最快通道。 |
| 函数 | `_meta_path` | 216–217 |  |
| 函数 | `_read_meta` | 220–227 |  |
| 函数 | `_write_meta` | 230–244 |  |
| 函数 | `_clear_meta` | 247–253 |  |
| 函数 | `_remove_quiet` | 256–261 |  |
| 函数 | `_download_one` | 264–363 | 从单个通道下载到 ``dest``，返回 ``(got, total)``。失败抛异常。 |
| 函数 | `download` | 366–390 | 下载到 ``dest``，多通道自动切换。 |

#### `app/folders.py`（208 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_known_folder` | 54–116 | 用 SHGetKnownFolderPath 拿真实路径；失败返回 None。 |
| 函数 | `known_folders` | 119–126 | 返回 {别名key: 真实路径}（只包含真存在的）。 |
| 函数 | `split_alias` | 129–151 | 把 '桌面\a\b.txt' 拆成 ('desktop', 'a\b.txt')；不是别名返回 (None, raw)。 |
| 函数 | `normalize_drive` | 158–172 | 把"只有盘符"的写法归一成 `X:\`。 |
| 函数 | `resolve` | 175–192 | 把带别名的路径换成绝对路径；不是别名则原样返回。 |
| 函数 | `is_alias` | 195–197 |  |
| 函数 | `hint_text` | 200–208 | 给系统提示词用：告诉模型这些别名会落到哪儿。 |

#### `app/hotkey.py`（231 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `parse_hotkey` | 82–103 | 'ctrl+alt+space' -> (modifiers, vk)。无法解析返回 (None, None)。 |
| 类 | `GlobalHotkey` | 106–231 | 在后台线程里注册一个全局热键，按下时调用 callback()。 |
| 方法 | &nbsp;&nbsp;`GlobalHotkey.__init__` | 109–119 |  |
| 方法 | &nbsp;&nbsp;`GlobalHotkey.available` | 122–123 |  |
| 方法 | &nbsp;&nbsp;`GlobalHotkey.run` | 125–201 |  |
| 方法 | &nbsp;&nbsp;`GlobalHotkey._wndproc` | 203–215 |  |
| 方法 | &nbsp;&nbsp;`GlobalHotkey.start_and_wait` | 217–221 | 启动并等注册结果（这样界面能马上知道热键能不能用）。 |
| 方法 | &nbsp;&nbsp;`GlobalHotkey.stop` | 223–231 | 注意：跨线程不能直接停消息循环，用 PostThreadMessage 唤醒。 |

#### `app/icons.py`（136 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `ic` | 126–130 | 取图标字形。查不到就返回 `default`，再没有就返回 FALLBACK。 |
| 函数 | `render` | 133–136 | 拼成「图标 + 空格 + 文字」。文字为空时只给图标。 |

#### `app/knowledge.py`（258 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `tokenize` | 26–43 | 混合中英文切词，返回 term 列表（中文二元组权重更高，用重复实现加权）。 |
| 函数 | `_chunks` | 46–59 |  |
| 类 | `KnowledgeBase` | 62–238 |  |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.__init__` | 63–67 |  |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.load` | 70–78 |  |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.save` | 80–85 |  |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.stats` | 87–91 |  |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.clear` | 93–99 |  |
| 方法 | &nbsp;&nbsp;`KnowledgeBase._extract` | 102–119 | 把任意支持的文件抽成纯文本。 |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.build` | 121–169 | 扫描 root 下的可读文件，建索引。返回统计 dict。 |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.add_file` | 171–189 | 把单个文件加进索引（已存在则先替换）。 |
| 方法 | &nbsp;&nbsp;`KnowledgeBase.search` | 192–238 | BM25 简化打分检索，返回 [{path, full, score, snippet}]。 |
| 函数 | `_snippet` | 241–258 | 截取命中词附近的片段，便于阅读。 |

#### `app/llm_client.py`（717 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `LLMError` | 20–21 |  |
| 类 | `_StreamGuard` | 24–54 | 包一层 on_token，并在"换端点/模型重试"前把已发出的半截内容作废。 |
| 方法 | &nbsp;&nbsp;`_StreamGuard.__init__` | 37–39 |  |
| 方法 | &nbsp;&nbsp;`_StreamGuard.reset_if_needed` | 41–48 | 在开始**下一次**尝试前调用：上一轮吐过字就先让界面作废。 |
| 方法 | &nbsp;&nbsp;`_StreamGuard.__call__` | 50–54 |  |
| 函数 | `_is_hard_err` | 57–65 | 判断是不是「认证/额度/模型已下线」这类硬错误——本会话内不要再重试该模型。 |
| 函数 | `_default_web_gate` | 68–81 | 网页通道的默认闸门（只读就绪态，**绝不主动拉起浏览器**）。 |
| 函数 | `_effective_key` | 84–98 | 挑出发请求实际要用的 key。 |
| 类 | `LLMClient` | 101–717 |  |
| 方法 | &nbsp;&nbsp;`LLMClient.__init__` | 102–131 |  |
| 方法 | &nbsp;&nbsp;`LLMClient.cancel` | 133–150 | 用户点了停止：尽快掐断正在进行中的流式请求（需求 15）。 |
| 方法 | &nbsp;&nbsp;`LLMClient._reset_cancel` | 152–158 | 开始新的一轮对话前把取消开关复位（否则一次停止会让后面全发不出去）。 |
| 方法 | &nbsp;&nbsp;`LLMClient._sleep_cancelable` | 160–172 | 退避等待，但**可被用户点“停止”立刻打断**。 |
| 方法 | &nbsp;&nbsp;`LLMClient._wait_rate_limit` | 174–181 | 被限流后的统一处理：报告风控 → 等退避 → 返回后调用方重试。 |
| 方法 | &nbsp;&nbsp;`LLMClient.set_web_gate` | 183–185 | 注入"网页通道是否就绪"的判断函数（供 chat_auto 使用）。 |
| 方法 | &nbsp;&nbsp;`LLMClient.set_keys` | 187–189 |  |
| 方法 | &nbsp;&nbsp;`LLMClient._mark_used` | 191–193 |  |
| 方法 | &nbsp;&nbsp;`LLMClient.last_used_label` | 195–201 | 返回实际使用的模型，如 'glm-4-flash · 智谱 Zhipu'。 |
| 方法 | &nbsp;&nbsp;`LLMClient.reset_used` | 203–208 |  |
| 方法 | &nbsp;&nbsp;`LLMClient._build_payload` | 211–243 |  |
| 方法 | &nbsp;&nbsp;`LLMClient._backoff` | 246–255 | 退避秒数：优先用服务端 Retry-After，否则指数 2/4/8s，上限 10s。 |
| 方法 | &nbsp;&nbsp;`LLMClient._stream_once` | 257–463 | 对单个 (provider, model) 发起流式请求，逐 token 回调 on_token。 |
| 方法 | &nbsp;&nbsp;`LLMClient.stream_specific` | 466–472 |  |
| 方法 | &nbsp;&nbsp;`LLMClient.chat_auto` | 474–523 | 自动模式：按 PROVIDER_PRIORITY 顺序逐个端点、逐个模型尝试，免费优先。 |
| 方法 | &nbsp;&nbsp;`LLMClient.chat` | 525–582 | 统一入口。 |
| 方法 | &nbsp;&nbsp;`LLMClient.probe_provider` | 585–613 | 测试某个端点是否可用，顺带列出这个 Key 真正能用的模型。 |
| 方法 | &nbsp;&nbsp;`LLMClient.probe_all` | 615–624 | 逐个端点体检，返回 [(provider_label, ok, message)]。 |
| 方法 | &nbsp;&nbsp;`LLMClient.web_search` | 627–663 | 把"联网搜索"做成一个可以单独调用的能力。 |
| 方法 | &nbsp;&nbsp;`LLMClient._vision_once` | 666–689 |  |
| 方法 | &nbsp;&nbsp;`LLMClient.vision_extract` | 691–717 | 让多模态模型识别本地图片（OCR / 描述）。按 VISION_PRIORITY 依次尝试。 |

#### `app/markdown_render.py`（371 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_h` | 61–63 | 正文 -> 缓存键（md5 十六进制）。见上面为什么不用原文当 key。 |
| 函数 | `_cache_get` | 66–70 |  |
| 函数 | `_cache_put` | 73–77 |  |
| 函数 | `clear_caches` | 80–84 | 清空渲染缓存（测试与主题/字体变更后调用）。 |
| 函数 | `_strip_tool_call` | 87–97 | 把工具调用块从正文里剔掉（工具活动由专用 UI 展示）。 |
| 函数 | `_inline_pygments` | 107–134 | 语法高亮成内联样式的 HTML 片段（带 LRU 缓存，见模块头注释）。 |
| 函数 | `_norm_lang` | 140–148 | 围栏信息串 -> 语言名（小写、截断到合法字符集）。 |
| 函数 | `_placeholder` | 151–152 |  |
| 函数 | `_split_code_blocks` | 155–214 | 把 ``` 代码块摘出来，正文里换成占位符。 |
| 函数 | `render_with_blocks` | 217–244 | 把 Markdown 拆成「正文 HTML」+「代码块列表」。 |
| 函数 | `render_flow` | 247–345 | 按**原文顺序**把 Markdown 拆成「正文段 / 代码块」交替的分段列表。 |
| 函数 | `render_markdown` | 348–371 |  |

#### `app/mcp_client.py`（273 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_config_paths` | 32–37 |  |
| 函数 | `load_config` | 40–55 | 读取 MCP 服务器配置，返回 {name: cfg}。配置坏了不影响程序运行。 |
| 类 | `MCPServer` | 58–206 | 一个 MCP 子进程 + 它的工具表。所有异常都被吞掉，只返回错误字符串。 |
| 方法 | &nbsp;&nbsp;`MCPServer.__init__` | 61–70 |  |
| 方法 | &nbsp;&nbsp;`MCPServer.start` | 73–110 |  |
| 方法 | &nbsp;&nbsp;`MCPServer._reader` | 112–125 |  |
| 方法 | &nbsp;&nbsp;`MCPServer._err_reader` | 127–132 |  |
| 方法 | &nbsp;&nbsp;`MCPServer.close` | 134–145 |  |
| 方法 | &nbsp;&nbsp;`MCPServer._write` | 148–152 |  |
| 方法 | &nbsp;&nbsp;`MCPServer._request` | 154–174 |  |
| 方法 | &nbsp;&nbsp;`MCPServer._notify` | 177–178 |  |
| 方法 | &nbsp;&nbsp;`MCPServer.refresh_tools` | 181–188 |  |
| 方法 | &nbsp;&nbsp;`MCPServer.call_tool` | 190–206 |  |
| 类 | `MCPManager` | 209–273 | 管理所有 MCP 服务器；未配置时所有方法安全返回空。 |
| 方法 | &nbsp;&nbsp;`MCPManager.__init__` | 212–216 |  |
| 方法 | &nbsp;&nbsp;`MCPManager.configured` | 218–219 |  |
| 方法 | &nbsp;&nbsp;`MCPManager.reload` | 221–225 |  |
| 方法 | &nbsp;&nbsp;`MCPManager.server` | 227–237 |  |
| 方法 | &nbsp;&nbsp;`MCPManager.list_all_tools` | 239–253 | 返回 [{'server','name','description','schema'}]。 |
| 方法 | &nbsp;&nbsp;`MCPManager.call` | 255–264 |  |
| 方法 | &nbsp;&nbsp;`MCPManager.close_all` | 266–273 |  |

#### `app/memory.py`（435 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `MemoryStore` | 32–258 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.__init__` | 33–36 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.exists` | 39–40 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.load_text` | 42–49 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.save_text` | 51–55 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.parse` | 58–90 | 解析成 {分类: [条目, ...]}，保持顺序。 |
| 方法 | &nbsp;&nbsp;`MemoryStore.dump` | 92–102 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore._norm` | 105–106 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.remember` | 108–123 | 记一条。完全重复的条目会被忽略，返回 (是否新增, 分类)。 |
| 方法 | &nbsp;&nbsp;`MemoryStore.forget` | 125–138 | 删除包含关键词的条目，返回删除条数。 |
| 方法 | &nbsp;&nbsp;`MemoryStore.search` | 140–159 | 按关键词检索条目，返回 [(分类, 条目)]。 |
| 方法 | &nbsp;&nbsp;`MemoryStore.clear` | 161–162 |  |
| 方法 | &nbsp;&nbsp;`MemoryStore.prompt_block` | 165–247 | 生成要拼进系统提示词的一段文字；没有记忆则返回空串。 |
| 方法 | &nbsp;&nbsp;`MemoryStore.summary_text` | 249–258 | 给界面用的一段纯文本摘要。 |
| 函数 | `build_default_memory_file` | 261–278 | 首次使用时生成一个带说明的空记忆文件。 |
| 函数 | `auto_candidates` | 311–363 | 从用户的话里挑出值得长期记住的短句，返回 [(分类, 文本)]。 |
| 函数 | `auto_log_path` | 366–369 |  |
| 函数 | `log_auto` | 372–383 | 把「自动写入了什么」记一笔，界面可以展示、用户可追溯。 |
| 函数 | `recent_auto_log` | 386–404 |  |
| 函数 | `extract_json_facts` | 407–434 | 从模型返回的文本里抠出 [{'category':..,'text':..}]。 |

#### `app/plan.py`（165 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `normalize_status` | 23–25 |  |
| 函数 | `normalize_steps` | 28–51 | 把模型给的各种写法统一成 [{'text':..., 'status':...}]。 |
| 函数 | `_strip_prefix` | 54–64 |  |
| 函数 | `build` | 67–74 | 生成计划 dict。 |
| 函数 | `apply_update` | 77–106 | 根据 AI 传来的新 steps 更新计划。 |
| 函数 | `_looks_plain` | 109–118 |  |
| 函数 | `_key` | 121–123 |  |
| 函数 | `progress` | 126–131 |  |
| 函数 | `to_markdown` | 134–145 | 给 AI 看 / 给界面看的一份清单文本。 |
| 函数 | `to_html` | 148–161 |  |
| 函数 | `_esc` | 164–165 |  |

#### `app/ratelimit.py`（268 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_text_of` | 78–106 | 把异常 / 响应 / 字符串统一成一段可匹配的文本（取不到就空串）。 |
| 函数 | `is_rate_limit` | 109–129 | 判断是不是「调用太快」类错误（大小写不敏感、中英都认）。 |
| 函数 | `backoff` | 132–144 | 第 n 次重试该等多久：`base * 2**(n-1)`，封顶 `cap`，±20% 抖动。 |
| 函数 | `acquire` | 147–208 | 阻塞到"现在可以发请求"为止，返回实际等了多久（秒）。 |
| 函数 | `release` | 211–216 | 归还 `acquire()` 拿到的并发名额（必须与 acquire 配对调用）。 |
| 函数 | `note_rate_limit` | 219–236 | 报告「刚被风控了」：进入全局退避期，返回这次应该等多少秒。 |
| 函数 | `reset` | 239–255 | 测试用：清空统计、档期、退避期，并重建信号量。 |
| 函数 | `stats` | 258–268 | 当前节流统计（给界面/探针看）： |

#### `app/scheduler.py`（385 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_hhmm` | 44–59 | 解析 'HH:MM' / '9点' / '9:5' 这类写法。 |
| 函数 | `_weekdays` | 62–96 | '周一到周五' / '1,3,5' / '周一,周三' -> [1,3,5] |
| 函数 | `compute_next` | 99–142 | 算出下一次触发时间戳；已过期或不可计算返回 None。 |
| 函数 | `describe` | 145–160 | 一句话说明这个任务什么时候跑（界面直接显示）。 |
| 函数 | `next_text` | 163–170 |  |
| 类 | `Scheduler` | 173–385 | 定时任务调度器。线程安全（内部一把锁）。 |
| 方法 | &nbsp;&nbsp;`Scheduler.__init__` | 176–186 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.load` | 189–202 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.save` | 204–214 |  |
| 方法 | &nbsp;&nbsp;`Scheduler._normalize` | 216–234 |  |
| 方法 | &nbsp;&nbsp;`Scheduler._log` | 237–242 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.add` | 245–273 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.update` | 275–296 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.remove` | 298–305 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.get` | 307–311 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.list_all` | 313–315 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.start` | 318–324 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.stop` | 326–327 |  |
| 方法 | &nbsp;&nbsp;`Scheduler._loop` | 329–335 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.due` | 337–343 | 返回当前到期的任务（不改状态）。 |
| 方法 | &nbsp;&nbsp;`Scheduler.run_now` | 345–350 | 立即执行一次（手动触发），不改动排期。 |
| 方法 | &nbsp;&nbsp;`Scheduler._invoke` | 352–376 |  |
| 方法 | &nbsp;&nbsp;`Scheduler.tick` | 378–385 | 扫一遍到期任务并执行。返回本次执行的任务 id 列表。 |

#### `app/security.py`（604 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_keystream` | 24–30 |  |
| 函数 | `obf` | 33–40 | 明文 -> 密文（写进源码用）。 |
| 函数 | `deobf` | 43–54 | 密文 -> 明文。 |
| 类 | `_BLOB` | 58–60 |  |
| 函数 | `_to_blob` | 63–65 |  |
| 函数 | `_from_blob` | 68–72 |  |
| 函数 | `_crypt32` | 75–76 |  |
| 函数 | `_kernel32` | 79–80 |  |
| 函数 | `dpapi_encrypt` | 92–101 |  |
| 函数 | `dpapi_decrypt` | 104–113 |  |
| 函数 | `seal` | 119–131 | 把字符串加密成可安全落盘的文本。失败则返回 None（调用方决定怎么办）。 |
| 函数 | `unseal` | 134–153 | 解析 seal() 产出的文本；遇到旧的明文值原样返回。 |
| 函数 | `_xor_bytes` | 156–158 |  |
| 函数 | `seal_dict` | 161–169 | 把 dict 的每个值加密（用于 api_keys 这类敏感映射）。 |
| 函数 | `unseal_dict` | 172–173 |  |
| 函数 | `is_sealed` | 176–177 |  |
| 函数 | `looks_plaintext_key` | 180–187 | 粗略判断一个值是不是「看起来就是明文密钥」，用于提示用户迁移。 |
| 函数 | `mask` | 190–197 | 打码显示：sk-ws-H.PLL...c9f2 |
| 函数 | `_env` | 216–222 |  |
| 函数 | `system_protected_dirs` | 225–259 | 返回一批"受保护目录"（写操作不得落进去）。 |
| 函数 | `_strip_ns_prefix` | 269–275 | 去掉 Windows 命名空间前缀：`\\?\C:\x` -> `C:\x`；`\\?\UNC\s\v` -> `\\s\v`。 |
| 函数 | `normalize_path` | 278–306 | 把路径字符串做"完整展开"，返回绝对、规范化后的路径。 |
| 函数 | `real_path` | 309–324 | 解析符号链接 / junction / 8.3 短名，返回"真实最终位置"。 |
| 函数 | `_cmp` | 327–336 | 比较用的规范形式：真实路径 + 去尾分隔符 + 小写（Windows 大小写不敏感）。 |
| 函数 | `is_within` | 339–355 | target 是否就是 base 或在 base 之下。 |
| 函数 | `same_path` | 358–359 |  |
| 函数 | `is_system_protected` | 365–384 | 写这个位置算不算"动系统"（Windows / 程序目录 / AppData / 启动项…）。 |
| 函数 | `is_workspace_internal` | 387–412 | 是不是工作区里的"机器内部数据"（.awb/ 配置轨迹记忆、settings.json…）。 |
| 函数 | `protected_reason` | 415–421 | 受保护目标的拒绝说明（中文，说清"为什么 + 怎么办"）。 |
| 函数 | `outside_reason` | 424–429 | 超出产物目录的写入拒绝说明（中文）。 |
| 函数 | `outside_read_note` | 432–434 | 读工作区外文件时的如实标注（读放宽，但要说清）。 |
| 函数 | `split_command_segments` | 498–529 | 按 `& && \| \|\| ; 换行` 拆成若干段（**不含引号内的分隔符**）。 |
| 函数 | `_unwrap_segments` | 532–567 | 把一段命令里的"壳"剥开：cmd /c X -> X；powershell -Command X -> X。 |
| 函数 | `audit_command` | 570–596 | 审计一条命令，返回 (是否危险, 原因中文)。 |
| 函数 | `is_dangerous_command` | 599–600 |  |

#### `app/single_instance.py`（320 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `current_user` | 27–29 | 当前 Windows 用户名（拿不到就退回 USER / user）。 |
| 函数 | `server_name` | 32–34 | 管道名的最终形态：带用户维度。 |
| 函数 | `_warn` | 37–44 | 降级只留一行痕迹。打包版是 --windowed，stderr 可能是 None。 |
| 类 | `InstanceGuard` | 47–309 | 单实例守卫。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard.__init__` | 61–72 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard.name` | 76–77 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard.is_primary` | 80–81 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard.degraded` | 84–86 | True = 单实例保护没起来（调用方应当照常启动，别拦着用户）。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard.pending_count` | 89–90 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard._name_error` | 93–102 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard._alive` | 104–126 | 现有实例还在吗？能连上就算在（只连不发，不计入「又点了一次」）。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard.try_acquire` | 128–178 | 抢占主实例身份。True=可以继续建窗口；False=已有实例在跑。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard.notify_existing` | 181–212 | 通知已有实例「用户又点了一次」。True=送达到了。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard.take_pending` | 215–226 | 取走「又点了一次」的记录（取完清零）。没有就返回空串。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard.release` | 228–245 | 放开管道名（正常退出/自检结束时用；进程被杀时系统会自动回收）。 |
| 方法 | &nbsp;&nbsp;`InstanceGuard._on_new_connection` | 250–269 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard._read_socket` | 271–294 |  |
| 方法 | &nbsp;&nbsp;`InstanceGuard._consume` | 296–309 |  |
| 函数 | `guard` | 312–320 | 一行式入口：`ok, g = guard()`。 |

#### `app/skills.py`（341 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_parse_front` | 219–231 |  |
| 类 | `SkillStore` | 234–341 | 技能仓库：内置 + 工作区用户技能。 |
| 方法 | &nbsp;&nbsp;`SkillStore.__init__` | 237–239 |  |
| 方法 | &nbsp;&nbsp;`SkillStore.list_all` | 242–253 | 返回全部技能（内置在前，用户在后），每项含 slug/name/description/when/source。 |
| 方法 | &nbsp;&nbsp;`SkillStore._user_skills` | 255–280 |  |
| 方法 | &nbsp;&nbsp;`SkillStore.find` | 282–298 | 按 slug 或名称模糊查找（大小写不敏感、支持中文包含匹配）。 |
| 方法 | &nbsp;&nbsp;`SkillStore.body_of` | 301–319 | 返回技能正文（步骤列表拼成 Markdown）。 |
| 方法 | &nbsp;&nbsp;`SkillStore.install` | 322–332 | 把一份 SKILL.md 内容装进工作区 skills/<slug>/SKILL.md。 |
| 方法 | &nbsp;&nbsp;`SkillStore.summary_lines` | 334–341 | 给提示词用的极简清单（只有名字和适用场景，正文不进上下文）。 |

#### `app/taskman.py`（538 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_now` | 51–52 |  |
| 函数 | `find_python` | 55–68 | 找一个能用的 python.exe（给后台 python 任务用）。 |
| 类 | `Task` | 74–197 | 一个后台任务。字段改动都发生在任务线程里，外部读请用 snapshot()。 |
| 方法 | &nbsp;&nbsp;`Task.__init__` | 77–96 |  |
| 方法 | &nbsp;&nbsp;`Task.cancelled` | 100–101 |  |
| 方法 | &nbsp;&nbsp;`Task.cancel` | 103–116 | 请求取消：置事件 + 杀掉正在跑的子进程。任务函数应尽快检查 cancelled。 |
| 方法 | &nbsp;&nbsp;`Task.log_line` | 119–132 | 追加一行日志（自动去重连续重复行，避免刷屏）。 |
| 方法 | &nbsp;&nbsp;`Task.set_progress` | 134–136 |  |
| 方法 | &nbsp;&nbsp;`Task.add_file` | 138–153 | 登记一个产物文件（相对/绝对路径都行，统一存绝对路径）。 |
| 方法 | &nbsp;&nbsp;`Task._touch` | 155–161 | 通知订阅者。light=True 时对落盘做节流（日志高频）。 |
| 方法 | &nbsp;&nbsp;`Task.snapshot` | 164–182 | 给 UI / 模型的只读快照（避免跨线程读到半成品）。 |
| 方法 | &nbsp;&nbsp;`Task.to_dict` | 184–187 |  |
| 方法 | &nbsp;&nbsp;`Task.summary` | 189–197 | 一行摘要（给模型看的任务列表）。 |
| 类 | `TaskManager` | 203–445 | 全局后台任务池。一个工作区一个实例。 |
| 方法 | &nbsp;&nbsp;`TaskManager.__init__` | 206–218 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.subscribe` | 221–225 | 订阅变更：cb(task, light)。UI 侧把它转成 Qt 信号再刷新界面。 |
| 方法 | &nbsp;&nbsp;`TaskManager._notify` | 227–237 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.submit` | 240–275 | 提交一个后台任务，**立刻返回 Task**（绝不阻塞调用方）。 |
| 方法 | &nbsp;&nbsp;`TaskManager._pump` | 277–296 | 有空闲槽就起下一个排队的任务。 |
| 方法 | &nbsp;&nbsp;`TaskManager._notify_queue` | 298–302 |  |
| 方法 | &nbsp;&nbsp;`TaskManager._run` | 304–330 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.get` | 333–336 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.list` | 338–350 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.running_count` | 352–355 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.wait` | 357–364 | 等一个任务结束（给 bg_wait 工具用）。返回 Task 或 None（超时）。 |
| 方法 | &nbsp;&nbsp;`TaskManager.cancel` | 366–373 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.cancel_all` | 375–381 |  |
| 方法 | &nbsp;&nbsp;`TaskManager.clear_finished` | 383–400 | 清掉已结束任务的记录（保留正在跑的）。 |
| 方法 | &nbsp;&nbsp;`TaskManager._persist` | 403–411 |  |
| 方法 | &nbsp;&nbsp;`TaskManager._load_history` | 413–445 | 重启后把上次的任务记录读回来（只看历史，不会自动重跑）。 |
| 函数 | `make_command_fn` | 451–490 | 造一个"跑 shell 命令"的后台任务函数（流式收集输出）。 |
| 函数 | `make_python_fn` | 493–538 | 造一个"跑一段 Python 代码"的后台任务函数。 |

#### `app/team.py`（907 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_role_title` | 82–89 | 把角色 key 翻成「emoji 名称」；兼容 key 里的 ':' / '#' 后缀。 |
| 函数 | `track_client` | 100–111 | 把成员线程里新建的 LLMClient 挂到当前 TeamRun 上（供"停止"掐断）。 |
| 函数 | `untrack_client` | 114–121 |  |
| 函数 | `delta` | 124–131 | 由成员的执行线程调用，把一段流式输出回传给当前运行的团队（可选能力）。 |
| 函数 | `parse_tasks` | 137–172 | （兼容用）从一段文本里抠出 2~4 个任务。解析失败返回 []，绝不抛异常。 |
| 类 | `TeamRun` | 178–864 | 一次团队执行。**同步 run()**，由调用方丢进后台线程跑（不阻塞界面）。 |
| 方法 | &nbsp;&nbsp;`TeamRun.__init__` | 188–220 |  |
| 方法 | &nbsp;&nbsp;`TeamRun.cancelled` | 224–225 |  |
| 方法 | &nbsp;&nbsp;`TeamRun.track_client` | 227–230 | 登记一个正在为成员干活的 LLMClient，供 cancel() 一并中断。 |
| 方法 | &nbsp;&nbsp;`TeamRun.untrack_client` | 232–234 |  |
| 方法 | &nbsp;&nbsp;`TeamRun.budget_left` | 236–240 | 距离团队总预算截止还剩几秒（没启动过则返回总预算）。 |
| 方法 | &nbsp;&nbsp;`TeamRun.cancel` | 242–259 | 请求取消：置事件 + 掐断在跑的模型请求 + 连带取消子任务。 |
| 方法 | &nbsp;&nbsp;`TeamRun._touch` | 261–266 |  |
| 方法 | &nbsp;&nbsp;`TeamRun._emit` | 268–278 | 向界面抛一个过程事件。**异常一律吞掉**，绝不让回调影响团队执行。 |
| 方法 | &nbsp;&nbsp;`TeamRun._card` | 280–289 |  |
| 方法 | &nbsp;&nbsp;`TeamRun._warn` | 291–299 | 记一条「本轮不太对」的警告（会进 snapshot，界面/报告都能回看）。 |
| 方法 | &nbsp;&nbsp;`TeamRun.snapshot` | 301–312 |  |
| 方法 | &nbsp;&nbsp;`TeamRun._work_dir` | 315–318 |  |
| 方法 | &nbsp;&nbsp;`TeamRun._run_member` | 320–463 | 跑一个成员。返回它的产出文本（失败/超时/无有效产出返回空串）。 |
| 方法 | &nbsp;&nbsp;`TeamRun._normalize_tasks` | 466–491 | 把派单表里不合法的 member 兜底成能干活的一线成员；依赖下标越界的去掉。 |
| 方法 | &nbsp;&nbsp;`TeamRun._dispatch_retry_block` | 494–510 | 给总监的「重派」提示：把上一轮的问题说清楚，并再次强调合法 id。 |
| 方法 | &nbsp;&nbsp;`TeamRun._dispatch_with_retry` | 512–545 | 解析派单表；成员 id 不合法或没解析出 JSON 时，让总监**重派一次**（有界）。 |
| 方法 | &nbsp;&nbsp;`TeamRun._exec_batch` | 548–583 | 并行跑一批任务；依赖任务的产出会作为「前置产出」注入下批成员的提示。 |
| 方法 | &nbsp;&nbsp;`TeamRun._skip_tasks` | 586–602 | 把一批任务如实标成"未执行"（不是失败、也不是完成）。 |
| 方法 | &nbsp;&nbsp;`TeamRun._plain_report` | 604–643 | 不调模型也能交出的交付说明（预算用尽 / 总监汇总失败时的兜底）。 |
| 方法 | &nbsp;&nbsp;`TeamRun._failure_banner` | 645–664 | 汇总失败/部分完成的成员，生成报告顶部的醒目横幅（P0-4）。 |
| 方法 | &nbsp;&nbsp;`TeamRun._final_status` | 666–695 | 根据卡片真实状态汇总整体状态（done / partial / failed / cancelled）。 |
| 方法 | &nbsp;&nbsp;`TeamRun.run` | 698–830 | 跑完整支团队（同步）。调用方负责把它放到后台线程里。 |
| 方法 | &nbsp;&nbsp;`TeamRun._save` | 833–841 |  |
| 方法 | &nbsp;&nbsp;`TeamRun._write_report` | 843–864 |  |
| 类 | `TeamManager` | 870–907 | 管理所有团队执行（内存 + 落盘）。 |
| 方法 | &nbsp;&nbsp;`TeamManager.__init__` | 873–879 |  |
| 方法 | &nbsp;&nbsp;`TeamManager.subscribe` | 881–883 |  |
| 方法 | &nbsp;&nbsp;`TeamManager._notify` | 885–890 |  |
| 方法 | &nbsp;&nbsp;`TeamManager.create` | 892–901 | 新建一次团队执行，**立刻返回 TeamRun**（真正执行在 run() 里，由调用方丢后台）。 |
| 方法 | &nbsp;&nbsp;`TeamManager.get` | 903–904 |  |
| 方法 | &nbsp;&nbsp;`TeamManager.list` | 906–907 |  |

#### `app/team_presets.py`（865 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `get_preset` | 377–382 | 按 id 取团队定义；取不到就返回默认团队（绝不抛异常，避免界面崩）。 |
| 函数 | `list_presets` | 385–404 | 列出所有团队的**摘要**（含成员名片，方便界面直接渲染选择列表）。 |
| 函数 | `iter_members` | 407–409 | 返回团队的成员列表（顺序即展示顺序）。 |
| 函数 | `get_member` | 412–418 | 按 id 取成员；取不到返回 None。 |
| 函数 | `get_lead` | 421–424 | 取团队的总监成员。 |
| 函数 | `get_validator` | 427–431 | 取团队的独立校验成员（可能为 None）。 |
| 函数 | `spawnable_ids` | 434–436 | 能被派单的一线成员 id（can_spawn=True）。 |
| 函数 | `fallback_member_id` | 439–466 | 派单缺 id / id 非法时的兜底接单人。**绝不硬编码具体 id**。 |
| 函数 | `dispatch_ids` | 469–471 | 派单表里**允许**出现的成员 id（= 可派单的一线成员）。 |
| 函数 | `invalid_dispatch_members` | 474–486 | 挑出派单表里指向「不存在 / 不可派单」成员的任务下标。 |
| 函数 | `strip_team_noise` | 548–573 | 剥掉模型正文里的工具调用标记（DSML / tool_call / 工具 JSON 残片）。 |
| 函数 | `build_member_roster` | 579–610 | 生成给总监看的成员名册（id｜姓名｜职务｜能派什么活）。 |
| 函数 | `build_lead_dispatch_system` | 613–617 | 给总监派单用的完整 system（人设 + 用户需求 + 成员名册 + 派单表规范）。 |
| 函数 | `build_member_system` | 623–681 | 拼出某成员这一轮的完整 system 提示。 |
| 函数 | `_extract_json_arrays` | 687–698 | 从模型输出里尽量抠出候选 JSON 数组文本（先围栏，再全文）。 |
| 函数 | `_loads_lenient` | 701–730 | 尽最大努力把一段「像 JSON 的文本」解析成 Python 对象。 |
| 函数 | `_coerce_depends` | 733–753 | 把 depends_on 归一成 list[int]。 |
| 函数 | `_normalize_item` | 756–775 | 把数组里的一项规整成统一结构；拿不到任务说明的丢弃（返回 None）。 |
| 函数 | `_regex_items` | 778–800 | 最后兜底：拿正则把每个 {...} 里的键值对抠出来（应对严重残缺的 JSON）。 |
| 函数 | `parse_dispatch` | 803–834 | 从总监的输出里解析「派单表」→ list[dict]。 |
| 函数 | `topo_batches` | 840–865 | 按 depends_on 把任务排成若干批：同批内的任务可以并行。 |

#### `app/themes.py`（307 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `tokens` | 70–71 |  |
| 函数 | `set_theme` | 74–76 |  |
| 函数 | `set_font_scale` | 79–84 |  |
| 函数 | `fs` | 87–92 | 按界面缩放换算字号字符串，例如 fs(16) -> '17.6px'。 |
| 函数 | `qss` | 95–277 |  |
| 函数 | `apply` | 280–307 | 应用主题：Fusion 风格 + 显式调色板 + QSS。 |

#### `app/tools_more.py`（2866 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_resolve_python` | 72–92 | 解析用来跑脚本的 Python 解释器（不写死任何用户目录）。 |
| 函数 | `_resolve_git` | 95–101 | 解析 git 可执行文件：优先 PATH，找不到回落字面值 'git' 交给系统找。 |
| 函数 | `_err` | 112–114 | 统一的中文错误文本（模型看到 ``[错误]`` 就知道这一步没成）。 |
| 函数 | `_ok` | 117–118 |  |
| 函数 | `_s` | 121–132 | 把任意值安全转成字符串。 |
| 函数 | `_arg` | 135–141 | 按多个别名取参数（中英文都认），空串/None 视为没给。 |
| 函数 | `_arg_list` | 144–162 | 取一个「字符串列表」参数：支持 list / JSON 字符串 / 逗号或顿号分隔。 |
| 函数 | `_arg_int` | 165–174 | 取一个整数参数（容忍 "10" / "10 秒" / "1e3" 这类写法）。 |
| 函数 | `_arg_bool` | 177–189 | 取一个布尔参数（中英文真值都认）。 |
| 函数 | `_clip` | 192–197 | 长文本截断，并在尾部注明还剩多少。 |
| 函数 | `_human` | 200–210 | 字节数 -> 人类可读。 |
| 函数 | `_dec` | 213–215 | 子进程输出字节 -> 字符串（中文 Windows 可能是 GBK）。 |
| 函数 | `_run` | 218–239 | 跑一个子进程并抓输出。**唯一入口 winproc.run**（不弹窗）。 |
| 函数 | `_which` | 242–256 | 在 PATH 与若干常见安装位置里找一个可执行文件；找不到返回 None。 |
| 函数 | `_ctx_base` | 259–272 | 从 ctx（Agent 实例）里猜一个默认工作目录，猜不到就用当前目录。 |
| 函数 | `_abs_in` | 275–284 | 把相对路径接到 base 上；空则返回 default 的绝对路径。 |
| 函数 | `_ensure_parent` | 287–290 |  |
| 函数 | `_rmtree` | 293–320 | 健壮地删临时目录：Windows 上 .git 对象等是只读文件， |
| 函数 | `_safe_out_dir` | 323–335 | 危险动作的第二道保险：拒绝把产物写进系统关键目录。 |
| 函数 | `_json_or_text` | 338–348 |  |
| 函数 | `excel_op` | 354–379 | Excel 表格处理：读成文本、多表合并、按列去重、分组统计、写新表。 |
| 函数 | `_xl_load_rows` | 382–418 | 读一张表 -> (表头 list[str], 数据行 list[list[str]], 表名)。支持 xlsx/xlsm/csv。 |
| 函数 | `_xl_read` | 421–440 |  |
| 函数 | `_xl_resolve_cols` | 443–468 | 把「列名 / 1 基序号 / 列字母」统一解成 0 基下标列表。 |
| 函数 | `_xl_write_rows` | 471–493 | 写 xlsx（表头加粗底色、冻结首行、列宽自适应）。 |
| 函数 | `_xl_merge` | 496–531 |  |
| 函数 | `_xl_dedupe` | 534–567 |  |
| 函数 | `_xl_group` | 570–614 |  |
| 函数 | `_xl_write` | 617–643 |  |
| 函数 | `_pdf_lib` | 649–651 | 取 PDF 读写库（pypdf 优先，其次已装的 PyPDF2）；都没有返回 ``(None,None,None)``。 |
| 函数 | `_pdf_pages` | 654–674 | 用 pdfminer 数页数（只读，最稳）。失败返回 None。 |
| 函数 | `_parse_page_spec` | 677–704 | 把 "1-3,5" / [1,3,5] / "2-" 解析成 0 基页号列表。 |
| 函数 | `pdf_op` | 707–732 | PDF 处理：合并、拆分、抽页、加页码、图片转 PDF、看页数。 |
| 函数 | `_pdf_info` | 735–746 |  |
| 函数 | `_pdf_merge` | 749–783 |  |
| 函数 | `_pdf_split` | 786–817 |  |
| 函数 | `_pdf_pages_op` | 820–850 |  |
| 函数 | `_pdf_pagenum` | 853–901 |  |
| 函数 | `_pdf_img2pdf` | 904–941 |  |
| 函数 | `_iter_images` | 947–958 | 凑齐要处理的图片：显式 paths 优先，否则扫描 dir 目录。 |
| 函数 | `_parse_size` | 965–982 | 把 "800x600" / "800" / "50%" / "800x" 解析成 (w,h)。 |
| 函数 | `image_batch` | 985–1008 | 批量图片处理：压缩、缩放、转格式、加水印文字、拼长图。 |
| 函数 | `_img_out_dir` | 1011–1017 |  |
| 函数 | `_img_compress` | 1020–1069 |  |
| 函数 | `_img_resize` | 1072–1113 |  |
| 函数 | `_img_convert` | 1116–1165 |  |
| 函数 | `_img_watermark` | 1168–1228 |  |
| 函数 | `_img_montage` | 1231–1271 |  |
| 函数 | `_drive_roots` | 1290–1300 | 本机存在的盘符根路径，例如 ['C:\', 'D:\', ...]。 |
| 函数 | `_extra_paths` | 1303–1360 | 常见安装位置的候选 exe 绝对路径（Windows，跨盘扫描，可移植）。 |
| 函数 | `_scan_software_dirs` | 1363–1400 | 候选路径没命中时的兜底：只在各盘「软件 / Program Files / Dev-Cpp / MinGW |
| 函数 | `_compiler_version` | 1403–1418 | 取编译器首行版本信息（缓存）。拿不到返回空串。 |
| 函数 | `_find_compiler` | 1421–1437 |  |
| 函数 | `_lang_dir` | 1440–1441 |  |
| 函数 | `compile_run` | 1444–1528 | 真编译并真运行一段代码（C / C++ / Java / Go / Rust / Python）。 |
| 函数 | `_fmt_run` | 1531–1543 |  |
| 函数 | `_run_python` | 1546–1560 |  |
| 函数 | `_compile_and_run` | 1563–1592 |  |
| 函数 | `_cr_c_cpp` | 1595–1629 |  |
| 函数 | `_cr_java` | 1632–1666 |  |
| 函数 | `_cr_go` | 1669–1675 |  |
| 函数 | `_cr_rust` | 1678–1684 |  |
| 函数 | `disk_usage` | 1690–1763 | 分析目录与磁盘占用：各子目录体积 Top N、最大文件 Top N、磁盘剩余。 |
| 函数 | `_file_sig` | 1769–1780 |  |
| 函数 | `find_duplicate` | 1783–1858 | 按内容哈希找出重复文件：给一个或多个目录，输出「同组文件 + 可回收空间」。 |
| 函数 | `file_hash` | 1864–1919 | 计算文件的哈希值（md5 / sha1 / sha256，默认 sha256），支持多个文件与比对期望值。 |
| 函数 | `_read_text_any` | 1925–1938 | v 若像文件路径就读文件，否则当成文本本身。返回 (文本, 来源说明)。 |
| 函数 | `_find_arg_path` | 1941–1943 | 把绝对路径/相对路径字符串提取出来（用于 text_diff 判断参数是不是文件名）。 |
| 函数 | `text_diff` | 1946–2017 | 对比两个文件或两段文本的逐行差异（统一 diff 风格，带行号与上下文）。 |
| 函数 | `pip_manage` | 2023–2105 | Python 包管理：list（已装包）/ show / check / install / uninstall。 |
| 函数 | `_git_exe` | 2111–2114 |  |
| 函数 | `_git` | 2117–2131 |  |
| 函数 | `git_op` | 2134–2227 | 常用 git 操作：status / log / diff / branch / add / commit / push / pull / init。 |
| 函数 | `_ipconfig_text` | 2233–2235 |  |
| 函数 | `net_info` | 2238–2272 | 本机网络诊断：网卡 IP/掩码/MAC、默认网关、DNS、监听端口、主机连通性。 |
| 函数 | `_net_adapters` | 2275–2323 |  |
| 函数 | `_net_ports` | 2326–2360 |  |
| 函数 | `_net_ping` | 2363–2397 |  |
| 函数 | `_read_text` | 2406–2428 | 读文本：指定编码优先，否则依次试常见编码。返回 (文本, 实际编码)。 |
| 函数 | `_text_read_fail` | 2431–2437 | 统一的"文本读不到"提示（区分二进制 / 编码问题）。 |
| 函数 | `text_op` | 2440–2466 | 文本文件处理：统计(stats) / 查找(grep) / 替换(replace) / 按行切片(split) / |
| 函数 | `_txt_paths` | 2469–2471 |  |
| 函数 | `_txt_stats` | 2474–2496 |  |
| 函数 | `_txt_grep` | 2499–2547 |  |
| 函数 | `_txt_replace` | 2550–2602 |  |
| 函数 | `_txt_split` | 2605–2637 |  |
| 函数 | `_txt_join` | 2640–2665 |  |
| 函数 | `_txt_convert` | 2668–2698 |  |
| 函数 | `resolve_flags` | 2830–2852 | 按 ``action`` 动态给出 ``(readonly, danger)``。 |
| 函数 | `readonly_tools` | 2855–2857 | 注册表里默认只读（可丢进 _PARALLEL_SAFE_TOOLS）的工具名列表。 |
| 函数 | `describe_all` | 2860–2866 | 把 12 个工具的「名字：说明」拼成一段中文，方便塞进系统提示词。 |

#### `app/trace.py`（114 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `TraceLog` | 21–98 |  |
| 方法 | &nbsp;&nbsp;`TraceLog.__init__` | 22–31 |  |
| 方法 | &nbsp;&nbsp;`TraceLog.add` | 33–51 |  |
| 方法 | &nbsp;&nbsp;`TraceLog._trim` | 53–65 |  |
| 方法 | &nbsp;&nbsp;`TraceLog.tail` | 67–81 |  |
| 方法 | &nbsp;&nbsp;`TraceLog.stats` | 83–91 |  |
| 方法 | &nbsp;&nbsp;`TraceLog.clear` | 93–98 |  |
| 函数 | `_short_args` | 101–114 | 参数太长（例如 write_file 的整篇 content）只留摘要，避免日志爆炸。 |

#### `app/tts.py`（264 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_split_text` | 36–62 | 按句子切分，便于边合成边播放、也避免单次文本过长。 |
| 类 | `Speaker` | 65–204 | 统一的朗读控制器：同一时刻只会有一路声音。 |
| 方法 | &nbsp;&nbsp;`Speaker.__init__` | 68–80 |  |
| 方法 | &nbsp;&nbsp;`Speaker.available_engines` | 83–88 |  |
| 方法 | &nbsp;&nbsp;`Speaker._get_sapi` | 91–97 |  |
| 方法 | &nbsp;&nbsp;`Speaker._speak_sapi` | 99–115 |  |
| 方法 | &nbsp;&nbsp;`Speaker._speak_edge` | 118–133 |  |
| 方法 | &nbsp;&nbsp;`Speaker._edge_render` | 136–140 |  |
| 方法 | &nbsp;&nbsp;`Speaker._mci_play` | 143–150 |  |
| 方法 | &nbsp;&nbsp;`Speaker._mci_close` | 152–156 |  |
| 方法 | &nbsp;&nbsp;`Speaker.speak` | 159–189 | 朗读一段文本。blocking=True 时在当前线程读完。 |
| 方法 | &nbsp;&nbsp;`Speaker.stop` | 191–200 |  |
| 方法 | &nbsp;&nbsp;`Speaker.speaking` | 203–204 |  |
| 函数 | `_mci` | 208–216 | 调用 Windows MCI 接口（不需要额外库就能放 mp3）。 |
| 函数 | `_has_edge` | 219–224 |  |
| 函数 | `_resolve_voice` | 227–228 |  |
| 函数 | `list_sapi_voices` | 231–245 | 列出本机 SAPI 音色（离线可用）。 |
| 函数 | `save_wav` | 248–264 | 用 SAPI 把文本合成为 WAV 文件（离线，供"导出语音"用）。 |

#### `app/updater.py`（633 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_log` | 51–56 | 内部诊断信息只写日志（通道名 / 域名 / 异常详情都走这里）。 |
| 函数 | `_scrub` | 59–79 | 兜底清洗：把混进文案里的内部词汇与网址抹掉。 |
| 函数 | `_percent` | 82–99 | 算 0~100 的整数百分比；算不出来返回 ``None``（界面显示不确定态进度条）。 |
| 函数 | `_stage_label` | 102–136 | 把内部阶段 key 翻成一句给用户看的人话（唯一的文案出口）。 |
| 函数 | `_emit_stage` | 139–159 | 把阶段翻译成文案后回调给界面。 |
| 函数 | `human_error` | 162–222 | 把技术异常压成一句用户能看懂的原因（失败提示的唯一出口）。 |
| 类 | `NotFound` | 225–226 | 仓库 / Release 不存在（还没发布过）。 |
| 函数 | `_req` | 229–254 | 发一个 GET。优先 requests（对系统代理/重定向兼容更好）， |
| 函数 | `_normalize` | 257–279 | 把 GitHub release JSON 整理成统一结构。 |
| 函数 | `_check_api` | 282–289 |  |
| 函数 | `_check_atom` | 300–327 | 兜底：解析 releases.atom（github.com 域，通常可达）。 |
| 函数 | `_check_redirect` | 330–344 | 再兜底：跟随 /releases/latest 重定向，从最终 URL 里抠出 tag。 |
| 函数 | `_unescape` | 350–353 |  |
| 函数 | `_strip_html` | 356–361 |  |
| 函数 | `check` | 364–396 | 检查更新。 |
| 函数 | `pick_asset` | 399–406 | 从 releases 里挑一个最适合下载的附件。 |
| 函数 | `download` | 409–434 | 下载更新包（内部多线路 + 续传，对外只暴露「一句人话」的阶段提示）。 |
| 函数 | `_download_impl` | 437–510 | download() 的真实实现（异常交给 download() 统一翻译成一句人话）。 |
| 函数 | `install_package` | 513–540 | 把下载好的更新包解压、覆盖安装目录并安排本程序退出后重启。 |
| 函数 | `_install_impl` | 543–627 | install_package() 的真实实现（异常交给 install_package 翻译成人话）。 |
| 函数 | `format_notes` | 629–633 |  |

#### `app/webcap.py`（1258 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_clean` | 48–51 |  |
| 函数 | `_session` | 54–85 | 取（或建）一个会话。name 相同 -> 复用同一份 Cookie。 |
| 函数 | `reset_session` | 88–93 | 丢掉会话（比如要重新登录/换账号）。 |
| 函数 | `_pick_encoding` | 96–109 |  |
| 函数 | `_text_of` | 112–120 |  |
| 函数 | `_soup` | 123–128 |  |
| 函数 | `page_text` | 131–145 | 正文纯文本（去 script/style，去噪声行）。 |
| 函数 | `extract_pairs` | 148–204 | 从结果页抠出「字段 -> 值」对。 |
| 函数 | `form_list` | 207–248 | 列出页面上的表单结构（字段名 / 类型 / 隐藏值 / action）——排查接口时最有用。 |
| 函数 | `sniff_endpoints` | 265–304 | 从页面 HTML/JS 里嗅探「表单提交接口」。 |
| 函数 | `extract_tables` | 307–326 | 把页面里的表格转成 Markdown 表格。 |
| 函数 | `_load_values` | 329–374 | 把「一串值」凑齐：可以直接给列表，也可以从 Excel/CSV/txt 第一列读。 |
| 函数 | `scrape` | 380–476 | 带会话抓取一个网址，按需返回正文/原始HTML/表单结构/表格。 |
| 函数 | `resp_url` | 479–480 |  |
| 函数 | `form_batch` | 486–675 | 批量往一个表单里灌不同的值，收集每次的结果。 |
| 函数 | `_find_result_url` | 678–692 | 从返回 JSON 里找出「结果页地址」字段。 |
| 函数 | `_read_done` | 695–738 | 读回上次的结果（支持 csv / xlsx），用于断点续查。 |
| 函数 | `_save_rows` | 741–775 | 把汇总表写成 csv 或 xlsx。返回 (路径, 字节数)。 |
| 函数 | `_find_gh` | 789–795 |  |
| 函数 | `_gh` | 798–815 |  |
| 函数 | `_gh_ready` | 818–822 |  |
| 函数 | `github` | 825–911 | GitHub 操作。action: read / list / repo / search / issues / pr / releases。 |
| 函数 | `_fmt_gh_repo` | 914–923 |  |
| 函数 | `_fmt_gh_search` | 926–944 |  |
| 函数 | `_fmt_gh_contents` | 947–967 |  |
| 函数 | `_fmt_gh_issues` | 970–981 |  |
| 函数 | `_http_github` | 984–1000 | 无 gh 时的公开 API 兜底。 |
| 函数 | `_http_github_contents` | 1003–1022 |  |
| 函数 | `_http_github_search` | 1025–1030 |  |
| 函数 | `_urlq` | 1033–1035 |  |
| 函数 | `_search_fetch` | 1051–1062 | 抓搜索结果页的 HTML。 |
| 函数 | `_search_url` | 1065–1074 | 把关键词拼成对应引擎的结果页 URL。 |
| 函数 | `_with_site` | 1077–1085 | 把 ``site:域名`` 拼进查询（已经有了就不重复拼）。 |
| 函数 | `_parse_search` | 1088–1158 | 从搜索结果页抠出 [{title, url, snippet}]。解析不稳时用正则兜底。 |
| 函数 | `_format_results` | 1161–1169 |  |
| 函数 | `web_search` | 1172–1205 | 关键词定向网页搜索，返回**结构化中文文本**（不是 JSON）。 |
| 函数 | `web_search_multi` | 1208–1258 | 多关键词批量搜索：逐个搜、按「域名 + 标题」去重后合并成一份结果。 |

#### `app/webengine.py`（635 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_gpu_flags` | 63–78 | 按运行场景决定是否开 GPU 加速。 |
| 函数 | `enable_remote_debugging` | 98–112 | 打开 QtWebEngine 的 CDP 调试口；返回最终生效的端口。 |
| 函数 | `remote_debug_port` | 115–120 | 当前生效的 CDP 端口（供 deepseek_web 使用）。 |
| 函数 | `engine_available` | 169–171 | WebEngine 内核是否可用（不抛异常，只回 True/False）。 |
| 函数 | `_in_gui_thread` | 177–182 | 当前线程是不是 ``app`` 所在的主线程。 |
| 函数 | `_ensure_app` | 185–200 | 拿到（必要时自建）QApplication；没有 GUI 时自建的一个让整块能独立跑。 |
| 类 | `_Dispatcher` | 203–245 | 把可调用对象丢到 Qt 主线程执行，并把结果/异常带回来。 |
| 方法 | &nbsp;&nbsp;`_Dispatcher.call` | 207–245 |  |
| 类 | `_Sniffer` | 260–297 | 记录页面发出的网络请求（URL / method / resourceType）。 |
| 方法 | &nbsp;&nbsp;`_Sniffer.__init__` | 263–265 |  |
| 方法 | &nbsp;&nbsp;`_Sniffer.set_sink` | 267–269 | 开始/停止记录；sink 是个 list 或 None。 |
| 方法 | &nbsp;&nbsp;`_Sniffer.interceptRequest` | 271–297 |  |
| 函数 | `_install_sniffer` | 300–319 | 把拦截器装上默认 Profile（只装一次）。 |
| 函数 | `_render_on_main` | 325–404 | 在 Qt 主线程里真实加载 URL 并取回结果（内部使用，勿直接调用）。 |
| 函数 | `_host_of` | 407–412 |  |
| 函数 | `_same_site` | 415–420 | u 的域名是不是 base（含子域）。 |
| 函数 | `_filter_by_host` | 423–437 | 只保留与目标站点相关的请求。 |
| 函数 | `_static_fetch` | 443–482 | 回落抓取：普通 HTTP GET，拿不到 JS 渲染后的内容。 |
| 函数 | `_blank` | 485–490 |  |
| 函数 | `fetch_rendered` | 496–542 | 用真实浏览器内核打开 ``url``，等 JS 渲染完，取回 DOM 与页面发出的请求。 |
| 函数 | `extract_text` | 545–574 | HTML -> 可读正文：去掉 script/style/nav/header/footer，压掉多余空白。 |
| 函数 | `find_api_requests` | 585–635 | 从嗅探到的请求里挑出疑似「数据接口」的，供模型照着复现。 |

#### `app/webui_bridge.py`（245 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `WebUIBridge` | 34–245 | 网页 <-> Python 的通信桥。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.__init__` | 67–79 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.call` | 86–126 | 前端上行命令入口。name 是命令名，args_json 是参数 JSON 字符串。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.emit_event` | 131–139 | 通用下行：把 (type, payload) 通过 ``webui_event`` 信号推给前端。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_init` | 143–144 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_messages` | 146–147 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_convs` | 149–156 | 会话列表变了（新建 / 删除 / 改名 / 切换）→ 让网页左栏重画。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_token` | 158–162 | 流式 token：只往 buffer 追加，50ms 由定时器 flush。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_reason` | 164–168 | 思维链 reason：同 token 合批。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_tool_start` | 170–171 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_tool_result` | 173–174 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_phase` | 176–177 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_finished` | 179–181 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_error` | 183–185 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_attachments` | 187–188 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_theme` | 190–191 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.send_rpc_result` | 193–199 | 通用 RPC 的返回通道。前端 call('rpc', {reqId}) 后据此关联结果。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge._ensure_flush` | 204–206 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge._flush` | 208–218 | 把累计的 token / reason 一次性派发，并停掉定时器。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.flush` | 220–222 | 对外提供的立即 flush（主窗口在切换对话等时机可主动调用）。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.attach` | 227–239 | 把桥注册进指定页面。主窗口须在 ``loadFinished`` 后调用。 |
| 方法 | &nbsp;&nbsp;`WebUIBridge.page` | 241–242 |  |
| 方法 | &nbsp;&nbsp;`WebUIBridge.view` | 244–245 |  |

#### `app/webui_host.py`（193 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `register_scheme` | 64–69 | 注册 ``awb://`` scheme（必须在 ``QApplication`` 创建之前调用）。 |
| 函数 | `_resource_dir` | 72–81 | 返回 webui 静态资源根目录，兼容开发态与 PyInstaller 打包态。 |
| 函数 | `_mime_for` | 84–89 |  |
| 函数 | `_resolve` | 92–101 | 把请求里的相对路径解析成磁盘绝对路径；非法路径（目录穿越）返回 None。 |
| 函数 | `_read_qwebchannel_js` | 104–113 | 从 Qt 资源读出 qwebchannel.js；读不到返回空 bytes。 |
| 函数 | `_read_static` | 116–122 | 读静态文件字节；不存在返回 None。 |
| 函数 | `_reply` | 125–132 | 把 bytes 作为响应吐给请求 job。 |
| 类 | `AWBUrlSchemeHandler` | 135–173 | 把 ``awb://ui/<path>`` 映射到 ``app/webui/<path>``，含 404 与 qwebchannel 特殊路由。 |
| 方法 | &nbsp;&nbsp;`AWBUrlSchemeHandler.requestStarted` | 138–163 |  |
| 方法 | &nbsp;&nbsp;`AWBUrlSchemeHandler._reply_404` | 166–173 |  |
| 函数 | `_ensure_handler` | 180–185 |  |
| 函数 | `create_view` | 188–193 | 返回一个配好 scheme handler、加载 ``awb://ui/index.html`` 的 ``QWebEngineView``。 |

#### `app/winproc.py`（169 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `ensure_hidden_console` | 29–63 | 给本进程挂一个「隐藏的控制台」，让所有子孙进程都继承它而不是新建可见窗口。 |
| 函数 | `_has_console` | 66–74 | 本进程现在有没有控制台（隐藏控制台也算）。 |
| 函数 | `startupinfo` | 77–87 | 让子进程窗口隐藏的 STARTUPINFO；非 Windows 返回 None。 |
| 函数 | `no_window_kwargs` | 90–103 | 给 subprocess.run / Popen 用的"不弹窗"参数。 |
| 函数 | `_log_spawn` | 106–120 | 把每次子进程生成记到 %TEMP%/aiworkbench_spawns.log。 |
| 函数 | `run` | 123–129 | 等价 subprocess.run，但不弹窗。调用方显式传的 kw 优先级更高。 |
| 函数 | `popen` | 132–138 | 等价 subprocess.Popen，但不弹窗。 |
| 函数 | `kill_tree` | 141–169 | 杀掉一个子进程**连同它的所有子孙**，返回是否成功。 |

#### `app/worklog.py`（205 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_dir` | 25–30 |  |
| 函数 | `day_path` | 33–35 |  |
| 函数 | `_hhmm` | 38–41 |  |
| 函数 | `_one_line` | 44–48 |  |
| 函数 | `append` | 51–96 | 追加一条工作总结，返回写进去的 markdown 片段（也返回记录了没）。 |
| 函数 | `_index_add` | 99–142 | 维护一个轻量索引，方便界面列表展示（没有也能跑）。 |
| 函数 | `list_days` | 145–159 | 返回 [(日期, 字节数), ...]，新的在前。 |
| 函数 | `read_day` | 162–169 |  |
| 函数 | `recent` | 172–196 | 返回最近 n 条总结（解析 index.json）。 |
| 函数 | `today_stats` | 199–205 | 今天干了多少件事 / 几次成功。 |

#### `app/workspace.py`（698 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `internal_path` | 25–37 | 给「只拿到工作区路径字符串」的模块（trace / worklog / taskman / memory）用。 |
| 函数 | `internal_dir` | 40–53 | 同 `internal_path`，但保证是一个**目录**（不存在就建新的）。 |
| 类 | `Workspace` | 108–648 |  |
| 方法 | &nbsp;&nbsp;`Workspace.__init__` | 112–129 |  |
| 方法 | &nbsp;&nbsp;`Workspace.internal` | 132–136 | 返回 `<工作区>/.awb/<name>`，并确保该目录存在。 |
| 方法 | &nbsp;&nbsp;`Workspace._internal_path` | 138–140 | 新位置(.awb) 优先，老位置(根目录) 兜底，都没有则给新位置。 |
| 方法 | &nbsp;&nbsp;`Workspace._internal_dir` | 142–144 | 内部目录：`.awb/` 优先后回退根目录；都没有就建 `.awb/`。 |
| 方法 | &nbsp;&nbsp;`Workspace.conv_dir` | 147–148 |  |
| 方法 | &nbsp;&nbsp;`Workspace.exports_dir` | 151–152 |  |
| 方法 | &nbsp;&nbsp;`Workspace.traces_dir` | 155–156 |  |
| 方法 | &nbsp;&nbsp;`Workspace.worklog_dir` | 159–160 |  |
| 方法 | &nbsp;&nbsp;`Workspace.bgtasks_dir` | 163–164 |  |
| 方法 | &nbsp;&nbsp;`Workspace.team_dir` | 167–168 |  |
| 方法 | &nbsp;&nbsp;`Workspace.backups_dir` | 171–172 |  |
| 方法 | &nbsp;&nbsp;`Workspace.assistant_file` | 175–177 | 助手状态文件（原来在根目录 assistant.json）→ `.awb/assistant.json`。 |
| 方法 | &nbsp;&nbsp;`Workspace.memory_auto_file` | 180–182 | 自动记忆流水（原来在根目录 memory_auto.jsonl）→ `.awb/memory_auto.jsonl`。 |
| 方法 | &nbsp;&nbsp;`Workspace.files_root` | 185–187 | 没有当前对话时的产物兜底目录 —— 指向历史对话文件夹，绝不落工作区根。 |
| 方法 | &nbsp;&nbsp;`Workspace.ensure` | 189–201 |  |
| 方法 | &nbsp;&nbsp;`Workspace._migrate_layout` | 204–271 | 把老工作区根目录下的机器内部数据搬进 `.awb/`。 |
| 方法 | &nbsp;&nbsp;`Workspace._merge_dir` | 273–296 | 两边都有同名目录：只把新位置缺的条目补搬过去，老位置不动、不删。 |
| 方法 | &nbsp;&nbsp;`Workspace._migrate_legacy_files` | 298–349 | 老 `<工作区>/files/` 里的东西 → `chats/00_历史文件/files/`。 |
| 方法 | &nbsp;&nbsp;`Workspace.files_dir` | 354–372 | 当前对话的产物目录（**永远**落在 chats/ 里，绝不回落到工作区根）。 |
| 方法 | &nbsp;&nbsp;`Workspace.files_dir_for` | 374–396 | **指定对话**的产物目录（不改变"当前对话"）。 |
| 方法 | &nbsp;&nbsp;`Workspace._safe_name` | 399–401 |  |
| 方法 | &nbsp;&nbsp;`Workspace._load_index` | 403–409 |  |
| 方法 | &nbsp;&nbsp;`Workspace._save_index` | 411–417 |  |
| 方法 | &nbsp;&nbsp;`Workspace._title_of` | 419–424 |  |
| 方法 | &nbsp;&nbsp;`Workspace._next_seq` | 426–451 | 给新对话分配一个没被占用的文件夹序号。 |
| 方法 | &nbsp;&nbsp;`Workspace.conv_folder` | 453–487 | 这个对话的专属目录（`chats/<NN_标题>`）的绝对路径。 |
| 方法 | &nbsp;&nbsp;`Workspace.set_current` | 489–494 | 切换"当前对话"——决定之后产物和对话记忆落到哪个文件夹。 |
| 方法 | &nbsp;&nbsp;`Workspace.current_folder` | 496–497 |  |
| 方法 | &nbsp;&nbsp;`Workspace.conv_memory_file` | 499–505 | 这个对话的记忆文件（`chats/<NN_标题>/MEMORY.md`）。 |
| 方法 | &nbsp;&nbsp;`Workspace._protect` | 509–518 | 落盘前把敏感字段加密（api_keys / github_token）。 |
| 方法 | &nbsp;&nbsp;`Workspace._unprotect` | 521–537 | 读盘后把敏感字段解出来；同时兼容老版本的明文写法。 |
| 方法 | &nbsp;&nbsp;`Workspace.load_settings` | 539–562 |  |
| 方法 | &nbsp;&nbsp;`Workspace.save_settings` | 564–567 |  |
| 方法 | &nbsp;&nbsp;`Workspace._conv_path` | 570–571 |  |
| 方法 | &nbsp;&nbsp;`Workspace.list_conversations` | 573–590 |  |
| 方法 | &nbsp;&nbsp;`Workspace.load_conversation` | 592–597 |  |
| 方法 | &nbsp;&nbsp;`Workspace.save_conversation` | 599–618 |  |
| 方法 | &nbsp;&nbsp;`Workspace.delete_conversation` | 620–623 |  |
| 方法 | &nbsp;&nbsp;`Workspace.new_conversation` | 625–630 |  |
| 方法 | &nbsp;&nbsp;`Workspace.export_conversation` | 633–648 |  |
| 函数 | `_launcher_file` | 652–656 |  |
| 函数 | `_is_temp_path` | 659–667 | 系统临时目录里的路径绝不能被当成工作区：一清理就没了。 |
| 函数 | `get_last_workspace` | 670–679 | 只在记住的是一个真实、持久存在的目录时才返回它。 |
| 函数 | `set_last_workspace` | 682–690 | 绝不把临时目录记成工作区（避免测试/异常把用户引导到 TMP）。 |
| 函数 | `default_workspace_suggestion` | 693–698 | 默认建议放在「文档\AI工作台」，而不是任何临时目录。 |

#### `tools/fix_privacy.py`（101 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_degenerate` | 68–71 | 没给姓名时，替换片段会退化成空壳（如 AppPublisher= 或「」），这种要跳过。 |
| 函数 | `main` | 74–97 |  |

#### `tools/gen_keyblobs.py`（108 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `write` | 51–57 |  |
| 函数 | `from_env` | 60–66 |  |
| 函数 | `main` | 69–104 |  |

#### `tools/gen_project_map.py`（265 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `py_files` | 101–119 |  |
| 函数 | `first_docline` | 122–128 |  |
| 函数 | `spans` | 131–155 | 返回 [(kind, name, start, end, doc)]，kind ∈ {class, def, method}。 |
| 函数 | `build_auto` | 158–234 |  |
| 函数 | `main` | 237–261 |  |

#### `tools/privacy_scan.py`（162 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `iter_files` | 64–74 |  |
| 函数 | `real_secret_values` | 77–89 | 把内存里还原出来的真实 Key 也拿来搜（防明文混进代码）。 |
| 函数 | `main` | 92–158 |  |

#### `tools/privacy_scan_staged.py`（102 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `staged_files` | 49–55 |  |
| 函数 | `all_files` | 58–65 |  |
| 函数 | `main` | 68–98 |  |

#### `tools/push_release.py`（148 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `find_git` | 43–51 |  |
| 函数 | `run` | 57–68 |  |
| 函数 | `run_retry` | 71–89 | 本机代理对 github.com 的 CONNECT 是间歇性 502（实测前 4 次失败、第 5 次成功）， |
| 函数 | `main` | 92–144 |  |

#### `app/gui/browser_page.py`（426 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_is_private_host` | 37–50 |  |
| 函数 | `normalize_url` | 53–80 | 把用户输入变成可加载的 QUrl；非法/不支持返回 ``None``。 |
| 函数 | `_argv_ready` | 83–95 | 当前进程的 QApplication 是否带着 argv[0]。 |
| 函数 | `_make_profile` | 98–135 | 给内嵌浏览器一个**持久化** profile（登录态能留住）。 |
| 类 | `_FetchWorker` | 141–158 | 在后台线程里调 fetch_rendered，免得点按钮时界面卡住。 |
| 方法 | &nbsp;&nbsp;`_FetchWorker.__init__` | 146–148 |  |
| 方法 | &nbsp;&nbsp;`_FetchWorker.run` | 150–158 |  |
| 类 | `BrowserPane` | 164–426 | 地址栏 + 导航按钮 + 内嵌浏览器 + 「交给 AI 分析当前页」。 |
| 方法 | &nbsp;&nbsp;`BrowserPane.__init__` | 169–235 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._build_view` | 238–262 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._wire` | 264–278 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane.load` | 281–292 | 让内嵌浏览器打开某个网址（自动补协议、校验合法性）。 |
| 方法 | &nbsp;&nbsp;`BrowserPane.current_url` | 294–300 | 当前地址（优先取浏览器实际地址，其次取地址栏文本）。 |
| 方法 | &nbsp;&nbsp;`BrowserPane.analyze_current` | 302–325 | 抓取当前页并交给 AI（结果走 ``fetched`` 信号）。 |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_html_ready` | 327–351 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_url_changed` | 354–358 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_progress` | 360–362 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_title` | 364–366 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_load_finished` | 368–375 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_render_terminated` | 377–379 |  |
| 方法 | &nbsp;&nbsp;`BrowserPane._sync_nav` | 381–390 | 前进/后退按钮是否可用，跟着实际浏览历史走。 |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_download` | 392–417 | 页面内点下载链接时，选保存位置并开始下载。 |
| 方法 | &nbsp;&nbsp;`BrowserPane._on_fetched` | 419–426 |  |

#### `app/gui/chat_worker.py`（1345 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_has_real_proof` | 20–23 | 答复里有没有贴出真实的验证证据（而不是只说"已完成"）。 |
| 函数 | `compact_history` | 36–73 | 把过长的对话历史压成「系统摘要 + 最近若干条」。 |
| 函数 | `_plan_parallel` | 110–136 | 挑出这一轮里可以并行执行的调用下标。 |
| 函数 | `_write_preview` | 139–171 | 把写入类工具的正文做成 Markdown 代码块，供界面展开查看。 |
| 函数 | `_compact_args` | 174–184 | 把参数压成**一行紧凑字符串**（args 不是 dict 时的兜底）。 |
| 函数 | `_tool_msg` | 187–217 | 造一条给界面看的工具消息（role=tool）。 |
| 类 | `ChatWorker` | 220–1345 |  |
| 方法 | &nbsp;&nbsp;`ChatWorker.__init__` | 235–266 |  |
| 方法 | &nbsp;&nbsp;`ChatWorker.confirm` | 268–270 |  |
| 方法 | &nbsp;&nbsp;`ChatWorker.ask_answer` | 273–276 | 界面上用户答完了（或点了跳过 / 直接关掉）→ 唤醒正在等答案的工具线程。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._ask_cb_impl` | 278–295 | 工具线程里被 `ask_user` 调用：把问题送回 UI 线程，等用户回答。 |
| 方法 | &nbsp;&nbsp;`ChatWorker.abort` | 297–328 | 用户点了右下角的方形「停止」：立刻中断本轮。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._run_parallel_calls` | 331–353 | 并行跑一批只读工具，返回 {下标: 结果文本}。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._clean_for_show` | 356–362 | 把工具调用标记从一段文本里剥掉（捞结论时用，免得答复里全是 <tool_call>）。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._salvage_conclusion` | 364–423 | 把"最近一段够大的结论"捞出来当最终答复。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._collect_reason` | 425–435 | 收集思维链增量（默认 None 的模型不会调用）。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._take_reason` | 447–467 | 取走并清空思维链缓冲。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._search_cb` | 470–476 | 联网搜索工具的真实实现（走支持联网的端点）。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._plan_cb` | 478–485 | update_plan 工具的真实实现：更新计划并通知界面刷新。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._on_token` | 487–494 |  |
| 方法 | &nbsp;&nbsp;`ChatWorker._after_chat` | 496–504 | 把 LLM 客户端攒下的提示（模型被切换等）转给界面，然后清掉。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._maybe_compact` | 506–537 | 历史对话太长时自动压缩：把较早的消息摘要成一段，只保留最近若干条。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._clean` | 539–544 | 去掉工具调用标记，只留下要给用户看的正文。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._ask_judge` | 546–574 | 让模型自己当一次「完成度裁判」。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._auto_verify` | 576–639 | 模型死活不肯自己跑验证时，由程序代跑一遍冒烟测试。 |
| 方法 | &nbsp;&nbsp;`ChatWorker._fallback_summary` | 641–657 | 模型只回了一堆"感谢提醒 / 我明白了"式废话时， |
| 方法 | &nbsp;&nbsp;`ChatWorker._emit_wrapup` | 659–696 | 把本轮的真实执行情况交给主窗口：写工作总结 + 自动补记忆。 |
| 方法 | &nbsp;&nbsp;`ChatWorker.run` | 698–708 |  |
| 方法 | &nbsp;&nbsp;`ChatWorker._run_impl` | 710–1345 |  |

#### `app/gui/main_window.py`（7822 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `TitleWorker` | 79–104 | 后台用免费模型给对话生成简短标题。 |
| 方法 | &nbsp;&nbsp;`TitleWorker.__init__` | 84–88 |  |
| 方法 | &nbsp;&nbsp;`TitleWorker.run` | 90–104 |  |
| 类 | `TeamJob` | 107–139 | 在后台线程里跑完整支团队（v9.18.0，需求 13）。 |
| 方法 | &nbsp;&nbsp;`TeamJob.__init__` | 123–126 |  |
| 方法 | &nbsp;&nbsp;`TeamJob.run` | 128–139 |  |
| 类 | `ConvRun` | 142–188 | **一个对话自己的**运行时状态（v9.18.0，需求 7）。 |
| 方法 | &nbsp;&nbsp;`ConvRun.__init__` | 161–188 |  |
| 类 | `MainWindow` | 191–7813 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._conv_cid` | 244–248 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._run` | 250–263 | 取（必要时新建）某个对话的运行时状态。cid 缺省 = 当前对话。 |
| 方法 | &nbsp;&nbsp;`MainWindow._is_current` | 265–267 | 这个回调是不是来自"当前正显示的那个对话"（不是就别去动界面）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._any_running` | 269–270 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.running` | 273–274 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.running` | 277–278 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.live_text` | 281–282 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.live_text` | 285–286 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.pending_tool` | 289–290 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.pending_tool` | 293–294 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.worker` | 297–298 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.worker` | 301–302 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.runner` | 305–317 | **当前对话自己的** AgentRunner（v9.19.0，按需创建）。 |
| 方法 | &nbsp;&nbsp;`MainWindow.runner` | 320–321 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._live_card` | 324–325 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._live_card` | 328–329 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.live_reason` | 332–333 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.live_reason` | 336–337 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._reason_card` | 340–341 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._reason_card` | 344–345 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._last_model_label` | 348–349 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._last_model_label` | 352–353 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_selected` | 356–357 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_selected` | 360–361 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.aborted` | 364–366 | 当前对话这一轮是否已被用户打断（修「点取消还显示思考中」）。 |
| 方法 | &nbsp;&nbsp;`MainWindow.aborted` | 369–370 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.__init__` | 372–535 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._want_web_engine` | 538–555 | 对话页是否用 Web 引擎。**默认 web**，三重回退任一「关」都回 Qt 版。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_sync_prefs` | 558–563 | 把设置里的偏好同步给服务层（后台无头 / 是否自动拉起）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_auto_on` | 565–566 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_is_web_model` | 568–575 | 当前选中的模型是不是 DeepSeek 网页版。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_boot` | 577–592 | 启动时准备通道。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_auto_ensure` | 594–607 | 后台自动确保网页可用（不在线就拉起浏览器）。绝不阻塞界面。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_ensure` | 609–617 | 确保本机 HTTP 服务在跑 + 状态轮询定时器在跑（幂等、无浏览器副作用）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._poll_deepseek_web` | 619–652 | 状态轮询：刷新设置页状态行 + 取走网页层推来的提示。 |
| 方法 | &nbsp;&nbsp;`MainWindow._ds_health_tick` | 656–686 | 选了 DeepSeek 网页版时主动自检连通性。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_do_show` | 688–705 | 「显示网页窗口」：亮出网页窗口（无头后台模式下会重启成显示窗口模式）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_do_hide` | 707–720 | 「收起窗口」：收回后台（切到无头模式，桌面零窗口，发送照常）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._gh_do_test` | 722–764 | 「检测连接」：看 GitHub 通不通（优先本机 gh 登录态，其次 Token）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_gh_status_ready` | 766–770 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_do_launch` | 772–784 | 「启动/打开网页」：拉起（或复用）Chrome 并接入网页。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_on_launch` | 786–792 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_do_selftest` | 794–806 | 「连通性自检」：后台跑三项自检，结果回到设置页。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_on_selftest` | 808–813 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dsw_do_retry` | 815–831 | 「重试」：重连 CDP / 重载网页（**不重发**历史提问）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._setup_tray` | 834–862 | 系统托盘常驻：关窗口不退出，点托盘图标随时叫回来。 |
| 方法 | &nbsp;&nbsp;`MainWindow._setup_hotkey` | 864–880 | 注册全局热键（Windows RegisterHotKey，不装钩子）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._hotkey_show` | 882–884 | 全局热键触发（后台线程）-> 转成 Qt 信号，在界面线程里执行。 |
| 方法 | &nbsp;&nbsp;`MainWindow._hotkey_voice` | 886–887 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_hotkey` | 889–894 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._toggle_window` | 896–900 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._show_window` | 902–909 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_tray_activated` | 911–914 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._tray_voice` | 916–918 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._tray_dingtalk` | 920–936 | 托盘里快速往钉钉发一句（不打断当前对话）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._quit_app` | 938–940 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.closeEvent` | 942–981 | 默认「关窗口 = 收进托盘」，真正退出走托盘菜单的退出。 |
| 方法 | &nbsp;&nbsp;`MainWindow._apply_theme` | 984–989 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._toggle_theme` | 991–995 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._refresh_all` | 997–1010 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._build_shell` | 1013–1239 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._build_chat_page` | 1242–1423 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._build_chat_page_web` | 1426–1456 | 建一个 QWebEngineView 承载的对话页（方案 B），返回该 view。 |
| 方法 | &nbsp;&nbsp;`MainWindow._swap_in_web_page` | 1458–1470 | 用网页版对话页替换 stack 里 index 0 的 Qt 版对话页。 |
| 方法 | &nbsp;&nbsp;`MainWindow._build_im_page_web` | 1472–1491 | 建一个承载 awb://ui/im.html 的 QWebEngineView 作为钉钉助手页。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_on_loaded` | 1493–1498 | im 页加载完成 → attach（无条件，软件渲染 ok 常为 False）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_push_init` | 1500–1506 | im 页前端连接成功后，推一个带主题的 init（数据由前端 rpc 主动拉）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_on_loaded` | 1508–1517 | 网页加载完成 → 注册桥 + 推送初始状态。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_conv_list` | 1521–1534 | 左栏会话列表的统一构造（init 与 convs 推送共用同一份口径）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_ds_health_check` | 1536–1562 | 网页「立即重试」：后台重新拉一次 DeepSeek 网页版，立刻返回状态快照。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_ds_show_window` | 1564–1572 | 网页「打开登录窗口」：把 DeepSeek 网页亮出来（后台线程，不卡界面）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_push_convs` | 1574–1589 | 把会话列表推给网页（新建/删除/改名/切换后调用）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_push_init` | 1591–1618 | 把初始状态推给网页：主题 / 会话列表 / 模型列表。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_send` | 1620–1627 | 网页发来的消息：复用 _send 核心，但文本来自桥参数（网页没有 Qt 输入框）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_regenerate` | 1629–1631 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_set_model` | 1633–1641 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_set_theme` | 1643–1647 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_select_conv` | 1649–1676 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_conv_messages` | 1678–1722 | 把 cid 对话的消息列表转成网页渲染用的格式。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_handle_rpc` | 1725–1747 | 通用 RPC 分发：前端 call('rpc', {method, args, reqId}) 到这里。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_rpc_dispatch` | 1749–2328 | 按 method 分发到具体实现。返回结果 dict（会被 send_rpc_result 打包）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._notify_im_refresh` | 2331–2345 | 从任意线程通知 web 钉钉页刷新（失败静默，绝不影响业务）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_state_get` | 2347–2360 | CLI 一键配置/登录的异步状态（前端轮询 im_get_state 拿）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_state_set` | 2362–2367 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_steps_init` | 2377–2385 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_step` | 2387–2399 | 把某一步置为 running / done / fail，并可选更新总进度。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_log` | 2401–2414 | 追加一行实时日志（前端滚动展示）。creep=(from,to) 时按日志条数缓慢推进。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_run_npm` | 2416–2466 | 流式跑 npm：边跑边把输出推进进度窗口，返回 (rc, 最后几行文本)。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_state` | 2468–2497 | 钉钉助手页全量状态：消息双栏 + 设置 + 标签 + 钉钉登录态。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_set_settings` | 2499–2519 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_whitelist` | 2521–2531 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_set_tag` | 2533–2542 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_delete` | 2544–2549 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_cli_setup` | 2551–2587 | 一键配置钉钉 CLI（异步）：立即返回，检测/安装放后台线程。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_cli_setup_worker` | 2589–2708 | 真正干活：检测 → 缺 node/dws 就自动装 → 校验 → 看登录态。 |
| 方法 | &nbsp;&nbsp;`MainWindow._web_im_cli_login` | 2710–2766 | 发起钉钉 CLI 登录（异步）：立即返回，后台等授权结果。 |
| 方法 | &nbsp;&nbsp;`MainWindow._style_input_area` | 2768–2799 | 按当前主题给输入卡片与图标按钮上色（跟随浅色 / 深色切换）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._wire_shortcuts` | 2801–2807 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._zoom` | 2809–2815 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._toggle_conv_list` | 2817–2823 | 顶栏「☰ 列表」：只收放侧栏里的**对话列表**那一块。 |
| 方法 | &nbsp;&nbsp;`MainWindow._update_page_title` | 2825–2834 | 顶栏只显示当前会话标题（极简）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._open_page` | 2837–2885 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_browser_fetched` | 2887–2916 | 内嵌浏览器「交给 AI 分析当前页」的回执（v9.18.0 需求 2）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._make_runner` | 2919–2957 | 建一个 AgentRunner。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_wrapup` | 2960–2964 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._wrapup` | 2966–3026 | 一轮结束后的自动归档：写工作总结 + 自动补长期记忆。 |
| 方法 | &nbsp;&nbsp;`MainWindow._wrapup_llm_async` | 3028–3052 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._extract_memories` | 3054–3075 | 用便宜的免费模型从这一轮里提炼值得长期记住的事实。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_wrapup_notice` | 3077–3086 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_bg_changed` | 3098–3113 | 后台线程回调：转成 Qt 信号（队列连接），回到 UI 线程再刷新。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_team_changed` | 3115–3119 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._refresh_bg_pages` | 3121–3130 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._refresh_team_pages` | 3132–3138 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_bg_notice` | 3140–3144 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_cancel_one` | 3147–3149 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_cancel_all` | 3151–3154 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_clear_done` | 3156–3158 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_set_allow_cmd` | 3160–3168 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_agent_confirm` | 3171–3187 | 后台任务里的命令确认。 |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_agent_run` | 3189–3365 | 在**当前线程**（后台任务线程）里跑一条独立 AI 链路，返回 {"text","files"}。 |
| 方法 | &nbsp;&nbsp;`MainWindow._team_member_run` | 3368–3387 | `TeamRun` 的**成员执行器**（v9.18.0 需求 13 接线）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._team_start` | 3389–3422 | 起一支团队：立刻返回 TeamRun，真正的执行在后台任务里。 |
| 方法 | &nbsp;&nbsp;`MainWindow._team_start_sync` | 3427–3428 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._team_cancel` | 3430–3435 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_team_choice` | 3438–3458 | 用户在对话里换了一支队伍：记在**这个对话**上，不是全局设置。 |
| 方法 | &nbsp;&nbsp;`MainWindow._sync_team_bar` | 3460–3475 | 把**当前对话**记着的团队选择回填到团队条（切对话 / 新建对话时调）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_team_evt` | 3477–3509 | 团队过程事件 —— **已经在 UI 线程**（经 `_team_evt` 信号转过来）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._send_team` | 3511–3555 | 把这一轮交给预置团队去做（v9.18.0 需求 13）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_team_finished` | 3557–3602 | 团队跑完：把总监汇总写成**这一轮**的 assistant 回复（v9.18.0 需求 13）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._plain_runner` | 3604–3630 | 给后台线程用的 AgentRunner：只挂不碰界面的回调。 |
| 方法 | &nbsp;&nbsp;`MainWindow._agent_run_once` | 3632–3734 | 在两线程里跑一轮完整 Agent 循环（不碰界面），返回最终文字。 |
| 方法 | &nbsp;&nbsp;`MainWindow._bg_wrapup` | 3736–3755 | 后台线程里的收尾归档（工作总结 + 自动记忆），不碰界面控件。 |
| 方法 | &nbsp;&nbsp;`MainWindow._run_subagent` | 3757–3764 | spawn_agent 工具的实现：独立上下文跑完，只把结论带回主对话。 |
| 方法 | &nbsp;&nbsp;`MainWindow._sched_log` | 3766–3771 | 调度器日志 -> 界面（跨线程，用信号）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._run_scheduled` | 3773–3796 | 定时任务到期后的真实执行（在调度器线程里跑）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._run_task_now` | 3798–3822 | 「立即执行一次」按钮：丢到后台线程跑，别卡界面。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_task_job_done` | 3824–3828 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_sched_done` | 3830–3836 | 调度器到点自动跑完后的界面回报。 |
| 方法 | &nbsp;&nbsp;`MainWindow._conv_obj` | 3838–3851 | 取某个对话的记录对象（当前显示的那个优先，否则从磁盘读进来）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._append_system_note` | 3853–3870 | 把后台发生的事写进**对应对话**（用户看得见）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._open_workspace` | 3872–3969 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._load_conversations` | 3971–3985 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._new_conversation` | 3987–4016 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._sanitize_conv` | 4019–4069 | 清掉历史记录里由 bug 写进去的垃圾（v9.19.0）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_conv_selected` | 4071–4124 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._render_conv_async` | 4126–4130 | 延后一帧真正渲染刚才选中的对话（见 `_on_conv_selected`）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._show_loading` | 4132–4147 | 切到长对话时的轻量占位：只清空 + 一行灰字，绝不建卡片。 |
| 方法 | &nbsp;&nbsp;`MainWindow._delete_conversation` | 4149–4157 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._fill_model_combo` | 4160–4183 | （重建）模型下拉框：屏蔽信号，避免 addItem 时把用户选择覆盖成 auto。 |
| 方法 | &nbsp;&nbsp;`MainWindow._sync_toolbar` | 4185–4196 | 把设置里的模型选择同步到输入区下拉框。 |
| 方法 | &nbsp;&nbsp;`MainWindow._model_label_short` | 4198–4203 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._refresh_chips` | 4205–4248 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._update_status` | 4250–4251 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_input_changed` | 4253–4256 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_transcribed` | 4259–4265 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_voice_error` | 4267–4269 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._toggle_recording` | 4271–4272 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._speak` | 4274–4277 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._stop_speaking` | 4279–4281 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._tts_test` | 4283–4288 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._ding_cfg` | 4291–4292 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_api` | 4295–4474 | 供 Agent 的 dingtalk 工具调用。action 决定做什么，返回给人看的文本。 |
| 方法 | &nbsp;&nbsp;`MainWindow._save_dingtalk` | 4476–4485 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._test_dingtalk` | 4487–4492 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_selftest` | 4495–4510 | 逐项真调钉钉接口，把结果摊在界面上（后台线程，别卡 UI）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_dingtalk_selftest` | 4512–4518 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_contacts` | 4520–4547 | 列出部门与成员（真实姓名 + userId），可直接填成接收人。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_dingtalk_contacts` | 4549–4558 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_login` | 4560–4604 | 弹浏览器让用户扫码授权登录（本地起 127.0.0.1 回调）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_dingtalk_login_done` | 4606–4635 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_group_send` | 4637–4647 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_dws_login` | 4650–4685 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_dws_conversations` | 4687–4706 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_dws_messages` | 4708–4721 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_dws_send` | 4723–4738 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._toggle_dingtalk_stream` | 4740–4780 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._maybe_start_dingtalk_stream` | 4782–4785 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_log` | 4787–4795 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk_on_message` | 4797–4815 | 钉钉来的消息 -> 用本地 Agent 处理 -> 回复（在 SDK 线程里执行，注意别碰 UI）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._dingtalk` | 4817–4839 | AgentRunner 的钉钉回调。title == '__status__' 时只返回状态。 |
| 方法 | &nbsp;&nbsp;`MainWindow._add_reminder` | 4842–4847 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._check_reminders` | 4849–4866 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._notify` | 4869–4873 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_toggle` | 4876–4881 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._init_im` | 4884–4939 | 换工作区时重建 IM 仓库与编排核心。 |
| 方法 | &nbsp;&nbsp;`MainWindow._safe_asst_log` | 4941–4945 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_on_outgoing` | 4947–4964 | 我自己本人在钉钉里发出去的消息 —— 只记流水，不进处理队列。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_send` | 4966–4974 | 把一句话发回某个钉钉会话（hub 用）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_llm` | 4976–4990 | hub 用的模型调用（同步）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_tick` | 4992–5010 | 定时消息 + 代回时序的心跳（每 10 秒）。失败绝不影响界面。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_refresh_views` | 5012–5028 | 只刷新「消息」双栏（每收到一条、每回一条都会走这里）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_refresh_all` | 5030–5055 | 全量刷新：消息双栏 + 设置 + 标签 + 更多管理对话框的数据。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_ding_status_text` | 5058–5098 | 读登录状态（带 60 秒缓存 —— `dws auth status` 是一次子进程，别每次刷都跑）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_ding_login` | 5100–5114 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_ding_convs` | 5116–5140 | 刷新会话列表（后台读，回来填下拉框）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_ding_convs_back` | 5142–5148 | （UI 线程）会话列表回来了。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_ding_test` | 5150–5160 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_settings_saved` | 5163–5204 | 设置页保存：范围 / 白名单 / 延时 / 不确定时是否回 / 身份背景。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_delete_msgs` | 5206–5234 | 从两栏里删掉选中消息（落盘标记，重启也不会再冒出来）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_whitelist_add` | 5236–5264 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_whitelist_remove` | 5266–5274 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_tag_scan` | 5277–5320 | 首次全量扫描：读全部会话 → 让 AI 一个个打关系标签。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_tag_scan_done` | 5322–5339 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_tag_saved` | 5341–5350 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_open_manager` | 5353–5359 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_thread_saved` | 5361–5372 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_task_added` | 5374–5383 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_task_deleted` | 5385–5393 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_task_toggled` | 5395–5403 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._im_memory_saved` | 5405–5413 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_save_cfg` | 5415–5421 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_boot` | 5423–5431 | 启动时若上次助理是开着的，自动恢复。 |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_on_incoming` | 5433–5481 | 服务收到新消息：落盘 → 显示 → 通知 → 按需排队处理。 |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_process_one` | 5483–5496 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_process_all` | 5498–5509 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_check_stuck` | 5511–5569 | 看门狗：这一轮太久没结果 -> 兜底，并把状态复位。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_pipeline_pump` | 5572–5588 | 有会话需要预取就拉一次（串行，不跟前台对话/其它助理任务抢）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_fallback_text` | 5590–5601 | 「AI 拿不准 / 预取失败」时该回的那句话；返回 None = 按设置不回。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_prefetch` | 5603–5676 | 立刻去拉这一批的回复 —— 「先拉后等」，到点就能发，用户零等待。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_prefetch_proc` | 5678–5690 | （后台线程）走独立子进程拿回复，结果通过信号回 UI 线程。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_prefetch_done` | 5692–5706 | 进程内预取回来了：抽正文 → 交给 `_asst_prefetch_store`。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_prefetch_store` | 5708–5738 | 预取结束（两条路径共用）：存成草稿，**先不发**（等时间到再判定）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_pipeline_due` | 5740–5797 | 到点了：该发的发，该跳过的跳过。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_send_bg` | 5799–5808 | 后台把代回内容发出去，结果回 UI 线程再写状态（跨线程只走信号）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_sent_back` | 5810–5829 | （UI 线程）代回结果落地：写状态 + 刷界面。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_pump` | 5831–5862 | 串行处理队列：一次只跑一条，避免和前台对话抢 runner。 |
| 方法 | &nbsp;&nbsp;`MainWindow._im_context_for` | 5864–5890 | 取这个会话的（系统提示, 历史消息列表）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_start` | 5892–5956 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_recover` | 5958–5986 | 处理失败/卡住时的兜底：**一定要给对方回一句话**。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_done` | 5988–6002 | 不发送地收尾：复位状态 + 刷新列表 + 继续下一条。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_error` | 6004–6012 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_finish` | 6014–6085 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_reply` | 6087–6113 | 把最终结果（+ 产出文件）发回钉钉；后台线程，不卡界面。 |
| 方法 | &nbsp;&nbsp;`MainWindow._asst_log_ui` | 6115–6120 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._assistant_resend` | 6122–6129 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._ding_self_name` | 6131–6162 | 钉钉里「我自己」的显示名（dws 登录态里有）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._wants_dingtalk` | 6165–6170 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._ding_sent` | 6172–6174 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._auto_dingtalk_send` | 6176–6213 | AI 没发钉钉时的兜底：有文件就发文件，没文件就把结论原话发过去。 |
| 方法 | &nbsp;&nbsp;`MainWindow._ding_self_cid` | 6215–6220 | 「自己发给自己」那个会话的 cid（发文件/回消息都用它最稳）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._save_update_settings` | 6223–6226 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._update_text` | 6228–6245 | 给 AgentRunner 用的 check_update 工具。 |
| 方法 | &nbsp;&nbsp;`MainWindow._do_update_check` | 6247–6294 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._update_apply` | 6297–6344 | 下载新版本压缩包并自动应用。UI 线程只管弹进度，下载在后台线程。 |
| 方法 | &nbsp;&nbsp;`MainWindow._update_on_progress` | 6346–6352 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._update_on_stage` | 6354–6369 | 下载 / 安装的阶段提示：让用户知道现在在干嘛，而不是干等一个进度条。 |
| 方法 | &nbsp;&nbsp;`MainWindow._update_on_fail` | 6371–6378 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._update_on_done` | 6380–6390 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._finish_update` | 6392–6418 | 安装更新包：解压 → 覆盖 → 重启，全程带进度提示（v9.22.0）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._update_on_install_progress` | 6420–6427 | 解压进度：进度框按字节百分比推进，文案带上 MB 数（用户看得懂）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._update_on_install_done` | 6429–6449 | install_package 返回（已安排好覆盖+重启）：关进度框并退出本进程。 |
| 方法 | &nbsp;&nbsp;`MainWindow._info` | 6451–6460 | 统一的信息提示：正常运行时是弹窗，自动化测试/web 模式只写状态栏。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_settings_saved` | 6463–6518 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_memory_saved` | 6520–6522 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._probe_providers` | 6524–6529 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._web_search` | 6532–6533 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._vision` | 6535–6536 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._pick_file` | 6538–6545 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.dragEnterEvent` | 6547–6549 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.dropEvent` | 6551–6559 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._attach_file` | 6561–6594 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._refresh_attach_bar` | 6596–6633 | 把附件画成小卡片；没有附件时整条隐藏。 |
| 方法 | &nbsp;&nbsp;`MainWindow._remove_attachment` | 6635–6638 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._pending_attachment_text` | 6640–6643 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._attachment_note` | 6645–6648 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._api_messages` | 6652–6678 | 把会话历史整理成 API 消息。 |
| 方法 | &nbsp;&nbsp;`MainWindow._build_system_prompt` | 6680–6768 | 拼这一轮的系统提示。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_plan_changed` | 6770–6793 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_model_change` | 6795–6804 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._send` | 6806–6902 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_send_clicked` | 6904–6909 | 右下角按钮：空闲=发送；生成中=停止（打断模型）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._interrupt` | 6911–6960 | 打断**当前对话**的生成（别的对话在跑不受影响）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._set_stop_mode` | 6962–6979 | 切换右下角按钮的形态：生成中=红色方形停止键，空闲=蓝色圆形发送键。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_phase` | 6981–6994 | 模型阶段变化 -> 状态行（只关心当前显示的这个对话）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_notice` | 6996–6999 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_token` | 7001–7036 | 流式增量：写进**它自己**那个对话的运行态；只有它是当前显示时才刷界面。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_reason` | 7038–7064 | 思维链增量（v9.18.0，需求 17）—— 边想边显示。 |
| 方法 | &nbsp;&nbsp;`MainWindow._update_live` | 7066–7081 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._update_live_cards` | 7083–7109 | 流式期间**原地**刷新「正文卡」和「思考卡」（需求 6 + 需求 17 共用）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._tune_live_interval` | 7111–7128 | 按上一次渲染的真实耗时，自适应调整流式刷新节拍（需求 6）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_tool_start` | 7130–7137 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_tool_result` | 7139–7164 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_finished` | 7166–7226 | 一轮跑完：结果写回**它自己的**对话（哪怕用户已经切走了）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_error` | 7228–7242 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._finish_run` | 7244–7266 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._refresh_run_ui` | 7268–7287 | 切换对话后：把界面控件（停止键 / 状态行 / 输入框）同步成**这个对话**的样子。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_ask_user` | 7290–7315 | AI 通过 `ask_user` 提问 → 弹一张卡片，把答案回传给正在等的工具线程。 |
| 方法 | &nbsp;&nbsp;`MainWindow._on_confirm` | 7317–7351 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._regenerate` | 7354–7371 | 把最后一条用户消息之后的内容丢掉，重新生成一次。 |
| 方法 | &nbsp;&nbsp;`MainWindow._visible_user_text` | 7374–7378 | 去掉「附件的标注行」，只留用户真正打的字。 |
| 方法 | &nbsp;&nbsp;`MainWindow._edit_message` | 7380–7403 | 编辑某条用户消息：截断到它之前，把原文放进输入框重发。 |
| 方法 | &nbsp;&nbsp;`MainWindow._delete_message` | 7405–7416 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._speak_message` | 7418–7419 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._maybe_rename` | 7422–7436 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_title` | 7438–7444 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._render` | 7447–7448 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._clear_chat` | 7450–7468 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._scroll_to_bottom` | 7470–7473 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._card_key` | 7475–7484 | 折叠状态键：与会变动的下标解耦。 |
| 方法 | &nbsp;&nbsp;`MainWindow._page_fold_for` | 7487–7501 | 算这次渲染要在开头折掉多少条（0 = 全部显示）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._make_load_earlier` | 7503–7522 | 顶部「△ 加载更早的 N 条」按钮（折起来的消息数 = fold）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._load_earlier` | 7524–7534 | 往前再铺一批，并**保持当前看到的位置**（不能跳到顶）。 |
| 方法 | &nbsp;&nbsp;`MainWindow._render_full` | 7536–7694 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._build_card` | 7696–7724 | 按一条消息造一张卡片（并接好它的各种信号）。造不出来返回 None。 |
| 方法 | &nbsp;&nbsp;`MainWindow._copy_msg` | 7726–7728 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._after_render` | 7730–7753 | 布局落定后撑开卡片并按需要恢复滚动位置。 |
| 方法 | &nbsp;&nbsp;`MainWindow._restore_scroll` | 7755–7767 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._stack_welcome` | 7769–7776 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._on_quick` | 7778–7781 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._export` | 7784–7788 |  |
| 方法 | &nbsp;&nbsp;`MainWindow._switch_workspace` | 7790–7794 |  |
| 方法 | &nbsp;&nbsp;`MainWindow.closeEvent` | 7796–7813 |  |
| 函数 | `_asr_ok` | 7816–7822 | 当前配置下语音识别是否可用（有任一可用后端的 Key）。 |

#### `app/gui/pages.py`（3823 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `NavRail` | 89–161 | 侧栏上方的导航区：第一行「对话」，下面 3 列小格子放其余功能页。 |
| 方法 | &nbsp;&nbsp;`NavRail.__init__` | 94–137 |  |
| 方法 | &nbsp;&nbsp;`NavRail.select` | 139–147 | 切到某一页。`NAV_SKIP` 里的页（如「工具」）没有格子，照常派发切换。 |
| 方法 | &nbsp;&nbsp;`NavRail.set_current` | 149–158 | 只改高亮、不派发切换（供主窗口在代码里直接翻页后同步高亮）。 |
| 方法 | &nbsp;&nbsp;`NavRail.current` | 160–161 |  |
| 类 | `WelcomeView` | 164–220 | 空对话时的欢迎页：快捷入口卡片。 |
| 方法 | &nbsp;&nbsp;`WelcomeView.__init__` | 178–220 |  |
| 函数 | `_dim` | 223–224 |  |
| 类 | `MemoryPage` | 230–424 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.__init__` | 233–313 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.bind` | 315–321 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.refresh` | 324–325 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.reload` | 327–332 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.reload_auto` | 334–357 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.auto_items` | 359–360 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage.reload_worklog` | 362–376 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage._worklog_dir` | 378–387 | 工作总结目录（★ v9.18.0 需求 8：已收进工作区的隐藏内部目录）。 |
| 方法 | &nbsp;&nbsp;`MemoryPage._open_worklog` | 389–397 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage._update_hint` | 399–408 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage._save` | 410–415 |  |
| 方法 | &nbsp;&nbsp;`MemoryPage._clear` | 417–424 |  |
| 类 | `TracePage` | 430–503 |  |
| 方法 | &nbsp;&nbsp;`TracePage.__init__` | 431–450 |  |
| 方法 | &nbsp;&nbsp;`TracePage._btn` | 453–458 |  |
| 方法 | &nbsp;&nbsp;`TracePage.bind` | 460–462 |  |
| 方法 | &nbsp;&nbsp;`TracePage.reload` | 464–495 |  |
| 方法 | &nbsp;&nbsp;`TracePage._clear` | 497–503 |  |
| 函数 | `_short` | 506–508 |  |
| 函数 | `_esc` | 511–512 |  |
| 函数 | `_key_hint` | 523–524 |  |
| 函数 | `_tok` | 550–554 | 主题 token（补一个 card 别名，老主题里叫 card_bg）。 |
| 函数 | `_spin_set` | 557–561 |  |
| 类 | `AssistantPage` | 564–1328 | 钉钉助手主界面：左边待回、右边已处理，三标签（消息 / 设置 / 标签）。 |
| 方法 | &nbsp;&nbsp;`AssistantPage.__init__` | 598–636 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._build_msgs_tab` | 641–690 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._make_col` | 692–718 | 一个栏：标题 + 可滚动的卡片容器。 |
| 方法 | &nbsp;&nbsp;`AssistantPage._build_ding_group` | 723–767 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._send_ding_test` | 769–776 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.set_ding_status` | 778–804 | 主窗口回填钉钉登录状态；`convs` 不为 None 时顺便重建会话下拉。 |
| 方法 | &nbsp;&nbsp;`AssistantPage._build_settings_tab` | 806–929 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._build_tags_tab` | 934–970 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.set_running` | 975–978 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.load_cfg` | 980–985 | 老接口：设置项已搬到「设置」标签，这里只同步启停状态。 |
| 方法 | &nbsp;&nbsp;`AssistantPage.add_message` | 987–1008 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.update_status` | 1010–1018 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.log` | 1020–1024 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.selected_msgid` | 1026–1038 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.row_msgids` | 1040–1041 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.set_im_data` | 1043–1062 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.set_scan_state` | 1064–1068 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage.show_manager` | 1070–1088 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._merge_rows` | 1093–1124 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._fill_settings` | 1126–1147 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._set_whitelist` | 1149–1162 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._rebuild` | 1164–1180 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._fill` | 1182–1194 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._on_card_toggled` | 1196–1202 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._sync_del_btn` | 1204–1208 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._delete_checked` | 1210–1222 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._on_toggle` | 1224–1225 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._toggle_log` | 1227–1230 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._on_more` | 1232–1237 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._emit_settings` | 1239–1256 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._add_whitelist` | 1259–1269 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._remove_whitelist` | 1271–1276 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._set_tags` | 1279–1300 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._tag_item` | 1302–1307 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._on_tag_selected` | 1309–1312 |  |
| 方法 | &nbsp;&nbsp;`AssistantPage._save_tag` | 1314–1328 |  |
| 类 | `_MsgCard` | 1331–1400 | 一条消息卡片：勾选框 + 名称/时间/状态 + 正文（已处理的还带 AI 回复）。 |
| 方法 | &nbsp;&nbsp;`_MsgCard.__init__` | 1336–1392 |  |
| 方法 | &nbsp;&nbsp;`_MsgCard.mousePressEvent` | 1394–1400 |  |
| 函数 | `_trim` | 1403–1405 |  |
| 类 | `IntegrationPage` | 1411–2031 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.__init__` | 1428–1443 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._build_dingtalk` | 1446–1676 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.load_dingtalk` | 1678–1696 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._collect_dingtalk` | 1698–1714 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.set_probe_html` | 1717–1718 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.show_probe_items` | 1720–1736 | items = [(项目, ok, 说明)] |
| 方法 | &nbsp;&nbsp;`IntegrationPage.show_probe_text` | 1738–1742 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._save_dingtalk` | 1744–1748 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._find_dingtalk_users` | 1750–1779 | 去通讯录查真实可用的 userId（后台线程，别卡界面）。 |
| 方法 | &nbsp;&nbsp;`IntegrationPage._on_dingtalk_users_found` | 1781–1785 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._copy_found_user` | 1787–1792 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.set_dingtalk_status` | 1794–1795 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._dws_open_conversation` | 1798–1801 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._dws_send` | 1803–1810 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.show_dws_conversations` | 1812–1822 | convs = [(会话名, openConversationId), ...] |
| 方法 | &nbsp;&nbsp;`IntegrationPage.show_dws_messages` | 1824–1840 | msgs = [(发送者, 文本, 时间), ...] |
| 方法 | &nbsp;&nbsp;`IntegrationPage.show_dws_login_log` | 1842–1847 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._build_update` | 1850–1892 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.load_update` | 1894–1897 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._save_update` | 1899–1904 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.show_update_result` | 1908–1942 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._build_mcp` | 1945–1966 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._btn2` | 1969–1974 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage.bind_mcp` | 1976–1980 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._open_mcp_file` | 1982–1993 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._reload_mcp` | 1995–2008 |  |
| 方法 | &nbsp;&nbsp;`IntegrationPage._scan_mcp` | 2010–2031 |  |
| 类 | `SettingsPage` | 2037–2527 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage.__init__` | 2051–2074 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._tab_general` | 2077–2132 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._tab_model` | 2135–2253 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage.set_gh_status` | 2255–2260 | 主窗口回填 GitHub 检测结果（v9.15.0）。 |
| 方法 | &nbsp;&nbsp;`SettingsPage.set_web_status` | 2262–2296 | 更新「DeepSeek 网页版」状态行（圆点颜色 + 文案）。kind 为 WebErrorKind 值或短名。 |
| 方法 | &nbsp;&nbsp;`SettingsPage.show_web_selftest` | 2298–2314 | 把自检结果渲染进区块（逐项 ✓ / ✗ + 中文说明）。 |
| 方法 | &nbsp;&nbsp;`SettingsPage.show_probe` | 2316–2331 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._tab_voice` | 2334–2409 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._tts_engine` | 2411–2412 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._tab_keys` | 2415–2436 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._tab_prompt` | 2439–2455 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage.load` | 2458–2496 |  |
| 方法 | &nbsp;&nbsp;`SettingsPage._save` | 2498–2527 |  |
| 类 | `QDoubleSpinBoxCompat` | 2530–2549 | 浮点秒数输入框（录音静音的等待时间等）。 |
| 方法 | &nbsp;&nbsp;`QDoubleSpinBoxCompat.__init__` | 2533–2540 |  |
| 方法 | &nbsp;&nbsp;`QDoubleSpinBoxCompat.value` | 2542–2543 |  |
| 方法 | &nbsp;&nbsp;`QDoubleSpinBoxCompat.setValue` | 2545–2549 |  |
| 类 | `AboutPage` | 2555–2630 |  |
| 方法 | &nbsp;&nbsp;`AboutPage.__init__` | 2558–2614 |  |
| 方法 | &nbsp;&nbsp;`AboutPage._copy_diag` | 2616–2630 |  |
| 类 | `SkillNewDialog` | 2636–2681 | 新建技能：填 slug + 名称 + 说明 + 正文（Markdown）。 |
| 方法 | &nbsp;&nbsp;`SkillNewDialog.__init__` | 2639–2670 |  |
| 方法 | &nbsp;&nbsp;`SkillNewDialog.payload` | 2672–2681 |  |
| 类 | `SkillsPage` | 2684–2830 | 技能库：看内置技能、建自己的技能。 |
| 方法 | &nbsp;&nbsp;`SkillsPage.__init__` | 2689–2742 |  |
| 方法 | &nbsp;&nbsp;`SkillsPage.bind` | 2745–2747 |  |
| 方法 | &nbsp;&nbsp;`SkillsPage.reload` | 2749–2764 |  |
| 方法 | &nbsp;&nbsp;`SkillsPage._show_body` | 2766–2780 |  |
| 方法 | &nbsp;&nbsp;`SkillsPage._new` | 2783–2800 |  |
| 方法 | &nbsp;&nbsp;`SkillsPage._open_dir` | 2802–2809 |  |
| 方法 | &nbsp;&nbsp;`SkillsPage._delete` | 2811–2830 |  |
| 类 | `TaskDialog` | 2836–2894 | 新建定时任务。 |
| 方法 | &nbsp;&nbsp;`TaskDialog.__init__` | 2839–2882 |  |
| 方法 | &nbsp;&nbsp;`TaskDialog.payload` | 2884–2894 |  |
| 类 | `TasksPage` | 2897–3050 | 定时任务：让 AI 在你不看着的时候也自己干活。 |
| 方法 | &nbsp;&nbsp;`TasksPage.__init__` | 2903–2961 |  |
| 方法 | &nbsp;&nbsp;`TasksPage.bind` | 2963–2965 |  |
| 方法 | &nbsp;&nbsp;`TasksPage.reload` | 2967–2989 |  |
| 方法 | &nbsp;&nbsp;`TasksPage._current_id` | 2991–2996 |  |
| 方法 | &nbsp;&nbsp;`TasksPage._new` | 2998–3018 |  |
| 方法 | &nbsp;&nbsp;`TasksPage._run` | 3020–3025 |  |
| 方法 | &nbsp;&nbsp;`TasksPage._toggle` | 3027–3036 |  |
| 方法 | &nbsp;&nbsp;`TasksPage._delete` | 3038–3050 |  |
| 类 | `BgTasksPage` | 3068–3219 | ⟳ 并行任务：所有后台链路一览（跑慢命令、后台 AI、团队子任务都在这里）。 |
| 方法 | &nbsp;&nbsp;`BgTasksPage.__init__` | 3077–3143 |  |
| 方法 | &nbsp;&nbsp;`BgTasksPage.bind` | 3145–3149 |  |
| 方法 | &nbsp;&nbsp;`BgTasksPage.set_allow_cmd` | 3151–3155 | 由主窗口把设置里的值同步到复选框（别触发一轮信号回环）。 |
| 方法 | &nbsp;&nbsp;`BgTasksPage._current_id` | 3157–3163 |  |
| 方法 | &nbsp;&nbsp;`BgTasksPage._cancel` | 3165–3168 |  |
| 方法 | &nbsp;&nbsp;`BgTasksPage.reload` | 3170–3194 |  |
| 方法 | &nbsp;&nbsp;`BgTasksPage._show` | 3196–3219 |  |
| 类 | `TeamPage` | 3222–3373 | ⬡ 团队：一键起一支"产品经理 + 架构师 + 工程师 + 测试 + 组长"的小队。 |
| 方法 | &nbsp;&nbsp;`TeamPage.__init__` | 3234–3301 |  |
| 方法 | &nbsp;&nbsp;`TeamPage.bind` | 3303–3305 |  |
| 方法 | &nbsp;&nbsp;`TeamPage._start` | 3307–3314 |  |
| 方法 | &nbsp;&nbsp;`TeamPage._stop` | 3316–3318 |  |
| 方法 | &nbsp;&nbsp;`TeamPage.reload` | 3320–3337 |  |
| 方法 | &nbsp;&nbsp;`TeamPage._pick` | 3339–3373 |  |
| 类 | `BrowserPage` | 3379–3478 | 内嵌浏览器：像浏览器一样访问网页、自由抓包，再一键交给 AI。 |
| 方法 | &nbsp;&nbsp;`BrowserPage.__init__` | 3407–3451 |  |
| 方法 | &nbsp;&nbsp;`BrowserPage.ensure_pane` | 3454–3474 | 建（并返回）真正的浏览器控件；已经建过就直接返回。 |
| 方法 | &nbsp;&nbsp;`BrowserPage.showEvent` | 3476–3478 |  |
| 类 | `IMDialog` | 3486–3807 | IM 助手的管理面板：审查上下文、管定时任务、看日志、编辑记忆。 |
| 方法 | &nbsp;&nbsp;`IMDialog.__init__` | 3496–3529 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._build_threads_tab` | 3532–3582 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._on_thread_pick` | 3584–3604 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._save_thread` | 3606–3618 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._build_tasks_tab` | 3621–3669 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._sync_freq_row` | 3671–3674 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._add_task` | 3676–3696 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._task_id` | 3698–3700 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._del_task` | 3702–3705 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._toggle_task` | 3707–3713 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._build_log_tab` | 3716–3735 |  |
| 方法 | &nbsp;&nbsp;`IMDialog._build_memory_tab` | 3738–3759 |  |
| 方法 | &nbsp;&nbsp;`IMDialog.set_data` | 3762–3807 |  |
| 函数 | `_im_desc` | 3810–3823 | 定时任务的频率描述（页面内小工具，避免 import 循环）。 |

#### `app/gui/panels.py`（344 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `PlanPanel` | 16–123 | 输入框上方的任务计划条：显示 AI 当前的多步计划与进度。 |
| 方法 | &nbsp;&nbsp;`PlanPanel.__init__` | 19–59 |  |
| 方法 | &nbsp;&nbsp;`PlanPanel._toggle_body` | 61–63 |  |
| 方法 | &nbsp;&nbsp;`PlanPanel._apply_collapsed` | 65–72 |  |
| 方法 | &nbsp;&nbsp;`PlanPanel.refresh_theme` | 74–82 |  |
| 方法 | &nbsp;&nbsp;`PlanPanel.set_plan` | 84–123 |  |
| 函数 | `_esc` | 126–127 |  |
| 类 | `MemoryDialog` | 130–192 | 查看 / 编辑 / 清空长期记忆。 |
| 方法 | &nbsp;&nbsp;`MemoryDialog.__init__` | 133–170 |  |
| 方法 | &nbsp;&nbsp;`MemoryDialog._update_hint` | 172–176 |  |
| 方法 | &nbsp;&nbsp;`MemoryDialog._save` | 178–181 |  |
| 方法 | &nbsp;&nbsp;`MemoryDialog._reload` | 183–185 |  |
| 方法 | &nbsp;&nbsp;`MemoryDialog._clear` | 187–192 |  |
| 类 | `TraceDialog` | 195–262 | 工具调用记录：每一步调了什么、耗时多久、成功还是失败。 |
| 方法 | &nbsp;&nbsp;`TraceDialog.__init__` | 198–224 |  |
| 方法 | &nbsp;&nbsp;`TraceDialog.reload` | 226–256 |  |
| 方法 | &nbsp;&nbsp;`TraceDialog._clear` | 258–262 |  |
| 函数 | `_short` | 265–267 |  |
| 类 | `MCPDialog` | 270–344 | 查看 MCP 服务器配置与已发现的工具。 |
| 方法 | &nbsp;&nbsp;`MCPDialog.__init__` | 273–304 |  |
| 方法 | &nbsp;&nbsp;`MCPDialog._show_config` | 306–317 |  |
| 方法 | &nbsp;&nbsp;`MCPDialog._reload_cfg` | 319–321 |  |
| 方法 | &nbsp;&nbsp;`MCPDialog._scan` | 323–344 |  |

#### `app/gui/settings_dialog.py`（104 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `SettingsDialog` | 12–104 |  |
| 方法 | &nbsp;&nbsp;`SettingsDialog.__init__` | 13–89 |  |
| 方法 | &nbsp;&nbsp;`SettingsDialog.get_settings` | 91–104 |  |

#### `app/gui/startup_dialog.py`（64 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `StartupDialog` | 10–64 |  |
| 方法 | &nbsp;&nbsp;`StartupDialog.__init__` | 11–40 |  |
| 方法 | &nbsp;&nbsp;`StartupDialog.browse` | 42–45 |  |
| 方法 | &nbsp;&nbsp;`StartupDialog.accept` | 47–60 |  |
| 方法 | &nbsp;&nbsp;`StartupDialog.reject` | 62–64 |  |

#### `app/gui/team_bar.py`（283 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `TeamBar` | 39–283 | 对话内的团队选择条。 |
| 方法 | &nbsp;&nbsp;`TeamBar.__init__` | 59–95 |  |
| 方法 | &nbsp;&nbsp;`TeamBar.list_team_ids` | 100–102 | 所有可选团队 id（不含"不使用"）。 |
| 方法 | &nbsp;&nbsp;`TeamBar.selected_team_id` | 104–106 | 当前选中的团队 id；""=不使用团队。 |
| 方法 | &nbsp;&nbsp;`TeamBar.current_team` | 108–112 | 当前团队定义（dict）；未选团队时返回 None。 |
| 方法 | &nbsp;&nbsp;`TeamBar._rebuild_menu` | 114–130 | 重建下拉菜单：先「不使用团队」，再逐个预置团队。 |
| 方法 | &nbsp;&nbsp;`TeamBar.set_team` | 132–151 | 选中某支团队并刷新成员名片。非法/空 id 一律当作"不使用团队"。 |
| 方法 | &nbsp;&nbsp;`TeamBar._update_btn_text` | 153–160 |  |
| 方法 | &nbsp;&nbsp;`TeamBar._clear_members` | 165–172 |  |
| 方法 | &nbsp;&nbsp;`TeamBar._rebuild_members` | 174–187 | 按当前团队重建成员名片（每个成员一张小标签）。 |
| 方法 | &nbsp;&nbsp;`TeamBar.member_state` | 189–191 | 读某成员名片当前状态：idle/queued/run/done/fail。 |
| 方法 | &nbsp;&nbsp;`TeamBar._style_chip` | 193–207 |  |
| 方法 | &nbsp;&nbsp;`TeamBar._set_member_state` | 209–219 |  |
| 方法 | &nbsp;&nbsp;`TeamBar._set_all_states` | 221–223 |  |
| 方法 | &nbsp;&nbsp;`TeamBar.set_running` | 228–229 |  |
| 方法 | &nbsp;&nbsp;`TeamBar.is_running` | 231–233 | 当前是否有团队在跑（由 apply_event 维护）。 |
| 方法 | &nbsp;&nbsp;`TeamBar.set_phase` | 235–236 |  |
| 方法 | &nbsp;&nbsp;`TeamBar.reset_transient` | 238–241 | 一次团队跑完/取消后，把名片状态复位为待命。 |
| 方法 | &nbsp;&nbsp;`TeamBar.apply_event` | 243–268 | 接 team.py 的 on_event(kind, payload)，把协作过程映射到名片状态与状态提示。 |
| 方法 | &nbsp;&nbsp;`TeamBar.refresh_theme` | 273–283 |  |

#### `app/gui/voice_bar.py`（208 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `LevelMeter` | 13–54 | 实时音量条（录音时跳动）。 |
| 方法 | &nbsp;&nbsp;`LevelMeter.__init__` | 16–23 |  |
| 方法 | &nbsp;&nbsp;`LevelMeter.push` | 25–29 |  |
| 方法 | &nbsp;&nbsp;`LevelMeter.reset` | 31–33 |  |
| 方法 | &nbsp;&nbsp;`LevelMeter.paintEvent` | 35–54 |  |
| 类 | `VoiceInput` | 57–208 | 一体化语音输入条。 |
| 方法 | &nbsp;&nbsp;`VoiceInput.__init__` | 67–97 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput.refresh_theme` | 100–102 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput.toggle` | 104–110 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput.start` | 112–135 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput.finish` | 137–160 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput.cancel` | 162–174 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput._tick` | 176–191 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput._ok` | 194–202 |  |
| 方法 | &nbsp;&nbsp;`VoiceInput._err` | 204–208 |  |

#### `app/gui/widgets.py`（1624 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_rgb` | 30–37 |  |
| 函数 | `_shade` | 40–45 | 把颜色整体提亮(delta>0)/压暗(delta<0) —— 用于从 token 推导边框、页头色。 |
| 函数 | `_lum` | 48–52 |  |
| 函数 | `code_surface` | 55–77 | 代码块的一套配色（底色 / 边框 / 页头），**保证和聊天底色拉得开**。 |
| 函数 | `make_icon` | 86–95 | 程序化生成窗口图标（蓝底白字 AI）。 |
| 函数 | `tool_label` | 98–140 | 根据工具返回内容推断简短标签，折叠时也能一眼看出这一步干了什么。 |
| 函数 | `strip_tool_markup` | 143–150 | 去掉流式文本里的 <tool_call>...</tool_call>（含只输出了一半的尾巴）。 |
| 函数 | `file_write_stats` | 164–186 | 解析工具结果，返回 (新增行数, 删除行数) 或 None（非写入卡片）。 |
| 函数 | `tool_mark` | 189–197 | 工具胶囊右侧的状态点：✓ 成功 / ✕ 异常 / ⏭ 跳过。 |
| 函数 | `tool_verb` | 211–213 | 工具名 -> 中文短名（状态行显示「正在执行代码」这种）。 |
| 类 | `StatusLine` | 216–285 | 输入框正上方的一行细状态条：「模型现在正在干什么」。 |
| 方法 | &nbsp;&nbsp;`StatusLine.__init__` | 238–255 |  |
| 方法 | &nbsp;&nbsp;`StatusLine.set_phase` | 257–276 | kind 见 _PHASES；detail 是补充（如工具名）。 |
| 方法 | &nbsp;&nbsp;`StatusLine._tick` | 278–282 |  |
| 方法 | &nbsp;&nbsp;`StatusLine.stop` | 284–285 |  |
| 类 | `ChatInput` | 288–393 | 聊天输入框：Enter 发送，Shift+Enter 换行。 |
| 方法 | &nbsp;&nbsp;`ChatInput.__init__` | 310–319 |  |
| 方法 | &nbsp;&nbsp;`ChatInput._line_h` | 322–323 |  |
| 方法 | &nbsp;&nbsp;`ChatInput._visible_lines` | 325–331 | 文档现在占几行（软换行也算，见 QPlainTextDocument size）。 |
| 方法 | &nbsp;&nbsp;`ChatInput._fit_height` | 333–345 |  |
| 方法 | &nbsp;&nbsp;`ChatInput.changeEvent` | 347–352 | 字体 / 样式换了（主题切换、QSS 字号）→ 旧的行高作废，重算一次。 |
| 方法 | &nbsp;&nbsp;`ChatInput.showEvent` | 354–357 |  |
| 方法 | &nbsp;&nbsp;`ChatInput.sizeHint` | 359–361 |  |
| 方法 | &nbsp;&nbsp;`ChatInput.minimumSizeHint` | 363–365 |  |
| 方法 | &nbsp;&nbsp;`ChatInput._ime_composing` | 367–374 |  |
| 方法 | &nbsp;&nbsp;`ChatInput.inputMethodEvent` | 376–381 |  |
| 方法 | &nbsp;&nbsp;`ChatInput.keyPressEvent` | 383–393 |  |
| 函数 | `fit_prose` | 397–402 | 把 QTextBrowser 按内容高度撑开（不出现内部滚动条）。 |
| 类 | `ThinkingIndicator` | 405–440 | 流式等待时的"正在思考"动画。 |
| 方法 | &nbsp;&nbsp;`ThinkingIndicator.__init__` | 408–433 |  |
| 方法 | &nbsp;&nbsp;`ThinkingIndicator._tick` | 435–437 |  |
| 方法 | &nbsp;&nbsp;`ThinkingIndicator.stop` | 439–440 |  |
| 类 | `CodeBlockWidget` | 443–661 | 独立代码块卡片：语言标签 + 复制/保存按钮 + 高亮代码。 |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget.tag_of` | 496–505 | 语言 -> 左上角标签（'.py' / '.cpp' / '.bash'）。空语言返回 ''。 |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget.__init__` | 507–577 |  |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget._fit_body_height` | 579–607 | 按**文档真实渲染高度**撑开正文区（不再用行数估算 → 不留大片空白）。 |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget._copy` | 609–612 |  |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget._guess_name` | 614–629 | 从代码里猜个文件名（def/class/第一行注释），猜不到就叫 code。 |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget._default_dir` | 631–635 |  |
| 方法 | &nbsp;&nbsp;`CodeBlockWidget._save` | 637–661 |  |
| 类 | `MessageCard` | 664–1177 | 单条消息卡片。 |
| 方法 | &nbsp;&nbsp;`MessageCard.__init__` | 684–895 |  |
| 方法 | &nbsp;&nbsp;`MessageCard.from_text` | 899–920 |  |
| 方法 | &nbsp;&nbsp;`MessageCard.update_live` | 922–949 | 流式期间**原地**更新这张卡片的正文（v9.17.0）。 |
| 方法 | &nbsp;&nbsp;`MessageCard.set_footer` | 951–956 |  |
| 方法 | &nbsp;&nbsp;`MessageCard.is_collapsed` | 958–959 |  |
| 方法 | &nbsp;&nbsp;`MessageCard.set_collapsed` | 961–964 | 外部设置折叠状态（用于 _render_full 重建后恢复用户的展开/收起）。 |
| 方法 | &nbsp;&nbsp;`MessageCard._flow_has_code` | 966–980 | flow 里是否**真的**存在代码段。 |
| 方法 | &nbsp;&nbsp;`MessageCard._rebuild` | 982–1031 |  |
| 方法 | &nbsp;&nbsp;`MessageCard._make_prose_browser` | 1034–1045 | 造一个「和 body 长得完全一样」的正文浏览器（顺序模式用）。 |
| 方法 | &nbsp;&nbsp;`MessageCard._rebuild_flow` | 1047–1089 | 按 `self._flow` 的顺序把「正文段 / 代码块」逐个铺进 `flow_host`。 |
| 方法 | &nbsp;&nbsp;`MessageCard._fit_flow_prose` | 1091–1100 | 把顺序模式里的每个正文段撑到内容高度（不出现内部滚动条）。 |
| 方法 | &nbsp;&nbsp;`MessageCard._set_flow_visible` | 1102–1107 | 顺序模式：整批子控件一起显/隐（照抄 `_apply_height_policy` 对 code_host 的做法）。 |
| 方法 | &nbsp;&nbsp;`MessageCard._apply_height_policy` | 1109–1142 |  |
| 方法 | &nbsp;&nbsp;`MessageCard.set_content` | 1144–1162 | 外部替换正文内容。 |
| 方法 | &nbsp;&nbsp;`MessageCard.refit` | 1164–1172 |  |
| 方法 | &nbsp;&nbsp;`MessageCard._toggle` | 1174–1177 |  |
| 类 | `ProcessGroup` | 1180–1295 | 把连续的过程消息（工具调用 / 深度思考）折成**一条**默认隐藏的摘要行。 |
| 方法 | &nbsp;&nbsp;`ProcessGroup.__init__` | 1195–1234 |  |
| 方法 | &nbsp;&nbsp;`ProcessGroup.add_card` | 1236–1239 |  |
| 方法 | &nbsp;&nbsp;`ProcessGroup.written_files` | 1241–1243 | 这一组里"写入文件"的步数 —— 折起来之后用户最关心的就是这个。 |
| 方法 | &nbsp;&nbsp;`ProcessGroup._head_text` | 1245–1262 | 组头一行摘要。 |
| 方法 | &nbsp;&nbsp;`ProcessGroup.toggle` | 1264–1268 |  |
| 方法 | &nbsp;&nbsp;`ProcessGroup.is_collapsed` | 1270–1271 |  |
| 方法 | &nbsp;&nbsp;`ProcessGroup.set_collapsed` | 1273–1276 |  |
| 方法 | &nbsp;&nbsp;`ProcessGroup.lines` | 1278–1285 | 这一组里出现过的工具标签，用于给用户一个粗略的"都干了啥"。 |
| 方法 | &nbsp;&nbsp;`ProcessGroup.refit` | 1287–1295 | 布局落定后把组内卡片的正文高度重新撑开（和 MessageCard.refit 对齐）。 |
| 类 | `ReasoningCard` | 1298–1502 | 「深度思考」**实时**卡片（v9.18.0，需求 17）。 |
| 方法 | &nbsp;&nbsp;`ReasoningCard.__init__` | 1321–1405 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard._head_text` | 1408–1412 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard._full_text` | 1414–1415 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard._natural_h` | 1417–1424 | 这段思考**不封顶**时真实需要多高（用来判断要不要给「展开全文」）。 |
| 方法 | &nbsp;&nbsp;`ReasoningCard._update_more` | 1426–1436 | 只有「展开着 + 真的被高度截到」才亮出「展开全文」按钮。 |
| 方法 | &nbsp;&nbsp;`ReasoningCard.set_text` | 1438–1451 | 流式更新：只在内容真的变了时才动控件（和 MessageCard.update_live 同一纪律）。 |
| 方法 | &nbsp;&nbsp;`ReasoningCard.text` | 1453–1454 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard.toggle` | 1457–1459 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard.toggle_full` | 1461–1465 | 「展开全文 / 收起全文」：把高度上限放开 / 收回去（不动折叠态）。 |
| 方法 | &nbsp;&nbsp;`ReasoningCard.is_collapsed` | 1467–1468 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard.set_collapsed` | 1470–1478 |  |
| 方法 | &nbsp;&nbsp;`ReasoningCard._fit_body` | 1481–1497 | 按文档真实渲染高度撑开（和 CodeBlockWidget 同款做法）。 |
| 方法 | &nbsp;&nbsp;`ReasoningCard.refit` | 1499–1502 |  |
| 类 | `AskUserDialog` | 1505–1624 | AI 主动向用户提问（v9.17.0，参考 WorkBuddy 的提问卡片）。 |
| 方法 | &nbsp;&nbsp;`AskUserDialog.__init__` | 1521–1606 |  |
| 方法 | &nbsp;&nbsp;`AskUserDialog._pick` | 1608–1610 |  |
| 方法 | &nbsp;&nbsp;`AskUserDialog._submit` | 1612–1624 |  |

#### `app/deepseek_web/api_path.py`（615 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `ApiUnavailable` | 464–465 | 直发通道不可用（未登录 / 接口变了 / PoW 被拒）—— 调用方应降级到点击链路。 |
| 函数 | `is_hard_fail` | 483–485 |  |
| 类 | `ApiPath` | 488–615 | 在页面上下文里直接发 HTTP 请求（不走 DOM 点击）。 |
| 方法 | &nbsp;&nbsp;`ApiPath.__init__` | 499–502 |  |
| 方法 | &nbsp;&nbsp;`ApiPath.ensure` | 505–517 | 注入页面脚本（幂等）。 |
| 方法 | &nbsp;&nbsp;`ApiPath.token_len` | 519–524 |  |
| 方法 | &nbsp;&nbsp;`ApiPath.probe` | 526–534 | 返回 {tokenLen, pow:{...}} 或抛 ApiUnavailable。 |
| 方法 | &nbsp;&nbsp;`ApiPath.chat` | 537–599 | 发一段提示词并等生成结束，返回 (answer, reasoning, session_id)。 |
| 方法 | &nbsp;&nbsp;`ApiPath._snapshot` | 601–606 |  |
| 方法 | &nbsp;&nbsp;`ApiPath.stop` | 608–615 | 尽力打断（页面内的 fetch 无法真正 abort，这里只标记）。 |

#### `app/deepseek_web/cdp_client.py`（288 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `CDPError` | 25–26 | CDP 通道异常（连接、收发、脚本执行）。 |
| 函数 | `local_urlopen` | 54–66 | 访问**本机** HTTP 接口（CDP 调试口），强制绕过系统 / 环境代理。 |
| 函数 | `_http_json` | 69–75 | 向 CDP 的 HTTP 发现口发一个请求并解析 JSON（绕过代理）。 |
| 类 | `CDPClient` | 78–288 | 一个页面 target 的 CDP 连接（同步、带互斥锁）。 |
| 方法 | &nbsp;&nbsp;`CDPClient.__init__` | 81–96 | Args: |
| 方法 | &nbsp;&nbsp;`CDPClient.from_page` | 100–140 | 选一个 `type=='page'` 的 target 并建立 WS 连接。 |
| 方法 | &nbsp;&nbsp;`CDPClient._list_targets` | 143–148 |  |
| 方法 | &nbsp;&nbsp;`CDPClient._pick_page` | 151–159 |  |
| 方法 | &nbsp;&nbsp;`CDPClient.open_new_tab` | 162–166 | 新开一个标签页（Chrome 较新版本要求用 PUT）。 |
| 方法 | &nbsp;&nbsp;`CDPClient._next_id` | 169–171 |  |
| 方法 | &nbsp;&nbsp;`CDPClient.send` | 173–183 | 只发不等回包（用于 Page.enable / Runtime.enable 这类命令）。 |
| 方法 | &nbsp;&nbsp;`CDPClient.call` | 185–220 | 发一条 CDP 命令并同步等回包（按 id 匹配），返回整个消息 dict。 |
| 方法 | &nbsp;&nbsp;`CDPClient.evaluate` | 222–243 | Runtime.evaluate(returnByValue=True) 并取出 result.value。 |
| 方法 | &nbsp;&nbsp;`CDPClient.navigate` | 245–247 | 导航到指定 URL。 |
| 方法 | &nbsp;&nbsp;`CDPClient.enable_domains` | 249–255 | 打开常用域（失败不影响主流程）。 |
| 方法 | &nbsp;&nbsp;`CDPClient.is_alive` | 257–264 | WS 是否仍然可用（已确认死亡 / 底层连接断开都返回 False）。 |
| 方法 | &nbsp;&nbsp;`CDPClient.ping` | 266–281 | 轻量探活：发一条最短的 Runtime.evaluate 看是否还能收到回包。 |
| 方法 | &nbsp;&nbsp;`CDPClient.close` | 283–288 | 关闭 WS（失败静默）。 |

#### `app/deepseek_web/embedded.py`（447 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `EmbeddedHostError` | 48–49 | 内嵌宿主启动 / 连接失败（service 会据此回退到 Chrome）。 |
| 函数 | `debug_port` | 52–54 | 当前进程的 CDP 调试端口（唯一来源是 ``webengine.REMOTE_DEBUG_PORT``）。 |
| 函数 | `app_data_dir` | 57–60 | 应用数据目录：与 Chrome 专用 profile 同一层（%LOCALAPPDATA%\AIWorkbench）。 |
| 函数 | `profile_storage_path` | 63–64 |  |
| 函数 | `list_targets` | 67–78 | 读 ``http://127.0.0.1:<port>/json``，返回 target 列表（失败返回 []）。 |
| 函数 | `_gui_call` | 81–85 | 把只能在 GUI 线程做的事丢过去执行（带超时，绝不永久阻塞）。 |
| 函数 | `pump_events` | 88–106 | 在 GUI 线程上等待时泵一下事件队列。 |
| 函数 | `on_gui_thread` | 109–116 | 当前线程是否是 Qt GUI 线程。 |
| 函数 | `run_off_gui` | 119–167 | 把阻塞型 CDP 流程挪到工作线程；GUI 线程只负责泵事件。 |
| 函数 | `_strip_qt_ua` | 170–176 | 去掉 QtWebEngine 指纹（千万别把 `QtWebEngine/6.10.0` 发给 DeepSeek）。 |
| 类 | `EmbeddedHost` | 184–447 | 一个常驻的离屏 QWebEngineView + 命名 profile。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.__init__` | 197–206 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost._logf` | 209–213 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.port` | 216–217 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.running` | 220–222 | view 是否已建好（不代表页面已就绪）。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.driver` | 225–226 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.is_connected` | 228–235 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.pump` | 237–239 | GUI 线程等待时泵事件（工作线程调用无副作用）。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost._create_view` | 242–280 | **必须在 GUI 线程执行**：建命名 profile + page + view 并加载首页。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.start` | 283–297 | 建 view/profile、加载首页，并等 ``/json`` 出现 DeepSeek 页面 target。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost._start_impl` | 299–326 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.attach` | 328–330 | 连上内嵌页面的 CDP，返回一个 ``WebDriver``（``embedded=True``）。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost._attach_impl` | 332–346 |  |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.show_login` | 349–373 | 把内嵌视图**显示到桌面**（扫码 / 输验证码时用）。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.hide_login` | 375–395 | 把视图收回离屏（桌面无窗口，但页面仍然 visible）。 |
| 方法 | &nbsp;&nbsp;`EmbeddedHost.stop` | 398–447 | 断开 CDP、销毁 view、释放 profile。 |

#### `app/deepseek_web/errors.py`（122 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `WebErrorKind` | 12–27 | DeepSeek 网页通道的状态 / 失败类型。 |
| 函数 | `_as_kind` | 89–98 | 把 WebErrorKind / 字符串 / None 统一成 WebErrorKind。 |
| 函数 | `humanize` | 101–116 | 把失败类型翻译成中文人话；detail 作为补充说明附在后面。 |
| 函数 | `label_of` | 119–122 | 状态行的短标签（设置页圆点旁那行字）。 |

#### `app/deepseek_web/local_server.py`（765 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `WebTask` | 39–56 | 一次网页调用的任务记录（用于幂等与镜像）。 |
| 方法 | &nbsp;&nbsp;`WebTask.__init__` | 45–56 |  |
| 类 | `LocalServer` | 59–386 | 本机 OpenAI 兼容 HTTP 服务。 |
| 方法 | &nbsp;&nbsp;`LocalServer.__init__` | 62–108 | Args: |
| 方法 | &nbsp;&nbsp;`LocalServer.start` | 111–142 | 启动服务；返回实际监听的端口（固定端口被占用则回退随机端口）。 |
| 方法 | &nbsp;&nbsp;`LocalServer.stop` | 144–158 | 停止服务。 |
| 方法 | &nbsp;&nbsp;`LocalServer.port` | 161–162 |  |
| 方法 | &nbsp;&nbsp;`LocalServer.is_started` | 164–165 |  |
| 方法 | &nbsp;&nbsp;`LocalServer.push_notice` | 168–173 | 线程安全地推一条"给用户看"的提示。 |
| 方法 | &nbsp;&nbsp;`LocalServer.take_notices` | 175–180 | 主窗口定时器取走全部提示。 |
| 方法 | &nbsp;&nbsp;`LocalServer._driver` | 183–188 |  |
| 方法 | &nbsp;&nbsp;`LocalServer.status` | 190–218 | 返回状态字典（**不阻塞**：状态由 service 的轮询线程缓存后经 state_provider 取回）。 |
| 方法 | &nbsp;&nbsp;`LocalServer.selftest` | 220–257 | 三项自检：① 网页可达 ② 已登录 ③ 能否取回一条答复。 |
| 方法 | &nbsp;&nbsp;`LocalServer.reset_conversation_state` | 259–270 | 网页被重载 / 新建对话后，忘掉"以为网页已拥有的消息"。 |
| 方法 | &nbsp;&nbsp;`LocalServer.forget_sessions` | 272–279 | 彻底清空会话路由表（用户明确要求"重置网页会话"时才该调）。 |
| 方法 | &nbsp;&nbsp;`LocalServer._lookup_session` | 282–289 | 按 精确指纹 -> 宽松指纹 找已登记的会话；返回 (key, sess) 或 (None, None)。 |
| 方法 | &nbsp;&nbsp;`LocalServer._register_session` | 291–299 | 登记一个**刚新建**的网页会话并置为当前。 |
| 方法 | &nbsp;&nbsp;`LocalServer._set_active` | 301–303 |  |
| 方法 | &nbsp;&nbsp;`LocalServer._active` | 305–307 |  |
| 方法 | &nbsp;&nbsp;`LocalServer._count_new_chat` | 309–331 | 防呆：每次新建都留痕；短时间内反复新建要**喊出来**。 |
| 方法 | &nbsp;&nbsp;`LocalServer.session_stats` | 333–342 | 给 /status 用的会话路由概况（排查"为什么建了这么多会话"）。 |
| 方法 | &nbsp;&nbsp;`LocalServer._chunk_obj` | 345–357 |  |
| 方法 | &nbsp;&nbsp;`LocalServer._fingerprint` | 360–362 |  |
| 方法 | &nbsp;&nbsp;`LocalServer._get_or_create_task` | 364–380 | 返回 (task, mirrored)。mirrored=True 表示这是对同一在途/近期任务的重复请求。 |
| 方法 | &nbsp;&nbsp;`LocalServer._logf` | 382–386 |  |
| 类 | `_Handler` | 392–749 |  |
| 方法 | &nbsp;&nbsp;`_Handler.log_message` | 397–398 |  |
| 方法 | &nbsp;&nbsp;`_Handler.app` | 401–402 |  |
| 方法 | &nbsp;&nbsp;`_Handler._host_ok` | 404–407 |  |
| 方法 | &nbsp;&nbsp;`_Handler._auth_ok` | 409–412 |  |
| 方法 | &nbsp;&nbsp;`_Handler._json` | 414–423 |  |
| 方法 | &nbsp;&nbsp;`_Handler._guard` | 425–432 |  |
| 方法 | &nbsp;&nbsp;`_Handler._read_json` | 434–445 |  |
| 方法 | &nbsp;&nbsp;`_Handler._sse_begin` | 448–455 |  |
| 方法 | &nbsp;&nbsp;`_Handler._flush` | 457–461 |  |
| 方法 | &nbsp;&nbsp;`_Handler._sse_write` | 463–468 |  |
| 方法 | &nbsp;&nbsp;`_Handler._sse_chunk` | 470–472 |  |
| 方法 | &nbsp;&nbsp;`_Handler._sse_ping` | 474–476 |  |
| 方法 | &nbsp;&nbsp;`_Handler._sse_done` | 478–479 |  |
| 方法 | &nbsp;&nbsp;`_Handler.do_GET` | 482–503 |  |
| 方法 | &nbsp;&nbsp;`_Handler.do_POST` | 505–521 |  |
| 方法 | &nbsp;&nbsp;`_Handler._handle_chat` | 524–559 |  |
| 方法 | &nbsp;&nbsp;`_Handler._run_own_task` | 561–587 |  |
| 方法 | &nbsp;&nbsp;`_Handler._mirror_task` | 589–594 | 镜像一个在途 / 近期已完成的任务（不重发）。 |
| 方法 | &nbsp;&nbsp;`_Handler._pump_task` | 596–622 | 把任务的增量转发给当前连接，直到任务结束。owner=True 时本连接负责执行。 |
| 方法 | &nbsp;&nbsp;`_Handler._execute_task` | 624–749 | 真正执行一轮：driver.submit(...)，把增量写入 task.chunks。 |
| 函数 | `_make_server` | 752–765 | 构造 ThreadingHTTPServer 并挂上 app 引用（回环绑定、允许复用地址）。 |

#### `app/deepseek_web/prompt.py`（182 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `_content_of` | 28–45 | 取出消息的纯文本内容，兼容 content 为字符串 / 多段列表两种形态。 |
| 函数 | `_role_of` | 48–51 |  |
| 函数 | `_signature` | 54–65 | 消息的"身份指纹"：角色 + 内容，用于前缀比较。 |
| 函数 | `flatten` | 68–82 | 把 messages[] 摊平成一段给网页的提示词。 |
| 函数 | `estimate_chars` | 85–92 | 保守估算这批消息摊平后的字符数（含角色标签开销）。 |
| 函数 | `diff_tail` | 95–114 | 计算"网页还没见过的那一段"，用于多轮前缀增量下发。 |
| 函数 | `_first_user_text` | 136–141 | 第一条**非 system**消息的正文（整段对话里最稳定的那一句）。 |
| 函数 | `_anchor_text` | 144–149 | 指纹锚点文本：首条真实用户消息；没有就退回全量摊平（保证不撞空串）。 |
| 函数 | `_digest` | 152–153 |  |
| 函数 | `conversation_key` | 156–158 | 精确对话指纹（完整首条用户消息）。用于主对话，避免两个对话撞车。 |
| 函数 | `loose_key` | 161–167 | 宽松对话指纹（首条用户消息的前 LOOSE_PREFIX_CHARS 字）。 |
| 函数 | `trim_recent` | 170–182 | 超长时裁剪：保留全部 system 消息 + 最近 keep_turns 轮（2*keep_turns 条）。 |

#### `app/deepseek_web/service.py`（1089 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 函数 | `instance` | 57–59 | 模块级便捷入口：固定用法 `from app.deepseek_web import service; service.instance()`。 |
| 函数 | `_profile_dir` | 62–65 | 专用 user-data-dir（登录态持久化在这里，不污染日常 Chrome）。 |
| 类 | `DeepSeekWebService` | 68–1089 | DeepSeek 网页通道的进程内单例。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.instance` | 76–80 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.__init__` | 82–110 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._resolve_engine` | 114–121 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.engine` | 124–125 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.set_engine` | 127–156 | 切换宿主引擎（embedded / chrome）。切换会断开当前连接，下次 ensure_page 生效。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.log` | 159–167 | 统一写 %TEMP%/aiworkbench_deepseek_web.log（绝不写用户内容/密钥）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._set_state` | 170–173 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.cached_state` | 175–177 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.auto_ready` | 179–184 | 供 llm_client.chat_auto 的 web_gate 使用：网页已就绪才纳入自动模式候选。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.ensure_started` | 187–199 | 确保本机 HTTP 服务已起（幂等）；返回端口。**不拉起浏览器**。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.is_started` | 201–202 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._write_back_port` | 204–208 | 把真实端口写回 config provider 的 base_url（provider_by_name 返回同一 dict）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._start_poller` | 211–217 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._poll_loop` | 219–225 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._refresh_once` | 227–255 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._try_self_heal` | 262–325 | CDP 已死时尝试自动重连 / 重建宿主；返回 True 表示"已发起恢复"。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._find_browser` | 328–332 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._port_online` | 334–344 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._start_chrome` | 346–389 | 拉起 Chrome（独立 profile + 远程调试端口）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._stop_embedded` | 392–400 | 销毁内嵌宿主（CDP 连接 + view + profile）。失败只记日志。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._start_embedded` | 402–427 | 启动内嵌宿主并接上 CDP；失败直接抛异常，由调用方决定是否回退 Chrome。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._fallback_to_chrome` | 429–451 | 内嵌宿主不可用 -> **自动回退**到原来的 Chrome 路径。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._embedded_alive` | 453–460 | 内嵌宿主的 CDP 连接是否还活着。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._wait_embedded_ready` | 462–482 | 等内嵌页面把输入框/登录页渲染出来；超时再导航一次首页。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._ensure_page_embedded` | 484–492 | embedded 模式的 ensure_page；返回 fallback=1 表示应转 Chrome 路径。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._ensure_page_embedded_impl` | 494–556 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._wait_port` | 558–565 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._connect` | 567–581 | 连接当前宿主下的页面并建立 WebDriver。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._detect_mode` | 583–592 | 探测当前浏览器是不是无头模式（也可能是别处拉起的，别只信自己的标记）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._mask_headless_ua` | 594–615 | 无头模式：把 UA 里的 `HeadlessChrome` 换成 `Chrome`。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.launch` | 618–626 | 用户点「启动/打开网页」：拉起（或复用）Chrome 并接入网页。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.ensure_page` | 629–664 | 确保网页可用：**已在线就接入，没在线就自己拉起浏览器**。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._ensure_page_chrome` | 666–737 | 原 Chrome 路径（v9.18.0 之前的行为，未改变）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._goto_home` | 739–746 | 导航到 DeepSeek 首页（失败只记日志）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._switch_mode` | 748–798 | 在「后台无头」与「显示窗口」之间切换（需重启浏览器）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._window_id` | 800–813 | 取当前页面所在 Chrome 窗口的 windowId（失败返回 None）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.show_window` | 815–859 | 把网页窗口亮出来（用户要扫码 / 手动操作 / 想看页面时用）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.set_window_visible` | 861–891 | 恢复窗口可见性（Page.bringToFront + 取消最小化）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.hide_window` | 893–919 | 收走网页窗口，不占用户屏幕（程序仍照常驱动）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.connect_if_online` | 921–949 | 若调试端口已在线则接入（**绝不主动拉起浏览器**）。返回是否接入成功。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.reset` | 951–959 | 「重试」：重连 CDP / 重载网页（**不重发**历史提问）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._reset_embedded` | 961–964 | 内嵌模式的重试：尽量只重连；宿主没了就重建，重建失败回退 Chrome。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._reset_embedded_impl` | 966–986 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService._reset_chrome` | 988–1013 | 原 Chrome 路径的重试逻辑（行为未改）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.status` | 1015–1034 | 返回给界面用的状态字典（快速、不阻塞）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.selftest` | 1036–1054 | 三项自检（用户点「连通性自检」时调用）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.notify` | 1056–1059 | 推一条给用户看的提示（新对话 / 超长等）。 |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.take_notices` | 1061–1064 |  |
| 方法 | &nbsp;&nbsp;`DeepSeekWebService.shutdown` | 1066–1089 | 退出收拾：停服务、断 CDP、结束我们拉起的那个浏览器实例。 |

#### `app/deepseek_web/web_driver.py`（1670 行）

| 类型 | 名称 | 起止行 | 说明 |
|---|---|---|---|
| 类 | `WebDriverError` | 20–25 | 网页驱动的失败，携带统一的 WebErrorKind。 |
| 方法 | &nbsp;&nbsp;`WebDriverError.__init__` | 23–25 |  |
| 函数 | `agent_script` | 538–540 | 把 AGENT_VERSION 注入页面脚本（单一来源；改版本号只改上面那一行）。 |
| 函数 | `_looks_complete` | 549–560 | 文本是否"看起来已经完整"（当前只认完整 JSON 对象，可扩展）。 |
| 函数 | `bounds_usable` | 578–600 | 窗口位置是否"可用"（够大、且在可视区内）。 |
| 函数 | `compute_delta` | 603–633 | 算这一拍的增量。 |
| 类 | `WebDriver` | 636–1670 | DeepSeek 网页的状态机与操作入口。 |
| 方法 | &nbsp;&nbsp;`WebDriver.__init__` | 665–697 | Args: |
| 方法 | &nbsp;&nbsp;`WebDriver._logf` | 700–704 |  |
| 方法 | &nbsp;&nbsp;`WebDriver._ensure_agent` | 706–735 | 确保页面里已注入**正确版本**的 window.__AWB__；返回是否可用。 |
| 方法 | &nbsp;&nbsp;`WebDriver._js` | 737–744 | 执行一段 JS 并取回值（表达式里通常用到 window.__AWB__）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._read_state` | 746–755 |  |
| 方法 | &nbsp;&nbsp;`WebDriver.probe_state` | 758–796 | 返回 (WebErrorKind, 中文说明)。 |
| 方法 | &nbsp;&nbsp;`WebDriver.open_page` | 799–817 | 导航到 DeepSeek 网页并等待就绪。 |
| 方法 | &nbsp;&nbsp;`WebDriver.new_conversation` | 820–841 | 点「新对话」；找不到入口则退化为重新导航到首页。 |
| 方法 | &nbsp;&nbsp;`WebDriver.session_url` | 844–865 | 当前所在的 DeepSeek 会话 URL（形如 .../a/chat/s/<id>）。 |
| 方法 | &nbsp;&nbsp;`WebDriver.resume_session` | 867–899 | 导航回一个**已存在**的网页会话（**绝不新建**）。 |
| 方法 | &nbsp;&nbsp;`WebDriver.stop_generation` | 901–908 | 点「停止生成」（失败静默）。 |
| 方法 | &nbsp;&nbsp;`WebDriver.ensure_interactive` | 911–981 | 确保网页**可见/可交互**（不可见时发送 100% 失效）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._confirm_sent` | 983–1038 | 网页是否**真的**接受了这次提问（秒级轮询）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._press_enter_real` | 1040–1071 | 用 CDP 的 Input 域发**真实** Enter 键（isTrusted=True）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._retype_via_cdp` | 1073–1100 | 用 CDP 的真实输入管线把内容**重新敲**进输入框（v9.18.0，需求 1）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._session_id_from_url` | 1104–1110 | 从 `/a/chat/s/<id>` 里抠出会话 id（拿不到就返回 ""）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._submit_api` | 1112–1140 | 接口直发（v9.19.0）：在页面上下文里 fetch，**不碰 DOM**。 |
| 方法 | &nbsp;&nbsp;`WebDriver.submit` | 1142–1234 | 把一段提示词发给网页，等生成结束，返回答复原文。 |
| 方法 | &nbsp;&nbsp;`WebDriver._answer_is_rate_limit` | 1237–1245 | 答复正文是不是风控提示。 |
| 方法 | &nbsp;&nbsp;`WebDriver._rate_limited_error` | 1247–1252 | 把"被限流"翻译成人话报给上层（不伪装成超时/网页结构变化）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._clear_pending` | 1254–1263 | 清掉页面上的防重发标记。 |
| 方法 | &nbsp;&nbsp;`WebDriver._is_api_blocked` | 1281–1291 | 直发失败是否属于「DeepSeek 官方加固」而非「未登录 / 网络抖动」。 |
| 方法 | &nbsp;&nbsp;`WebDriver._submit_api_with_retry` | 1293–1351 | 接口直发 + 「输入过快」退避重试。 |
| 方法 | &nbsp;&nbsp;`WebDriver._submit_click_once` | 1353–1496 | 模拟点击链路：发一次，返回答复原文（**不做限流重试**，由 submit 负责）。 |
| 方法 | &nbsp;&nbsp;`WebDriver._wait_answer` | 1498–1629 | 轮询答复文本 / 停止按钮 / 网页错误态，判定生成结束或**快速失败**。 |
| 方法 | &nbsp;&nbsp;`WebDriver.close` | 1631–1636 | 释放 CDP 连接。 |
| 方法 | &nbsp;&nbsp;`WebDriver.diagnose` | 1638–1670 | 真机核对用：报告各选择器 / 错误态 / 活动信号的命中情况（不改变页面状态）。 |
<!-- AUTO:END -->
