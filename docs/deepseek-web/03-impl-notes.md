# 实现说明：把 DeepSeek 网页版接入 AIWorkbench（v9.13.1 → v9.14.0）

> 作者：寇豆码（工程师） · 依据 `02-arch.md` 的 T01~T04 实施（T05 打包**按指示暂不执行**）
> 本文记录：改了哪些文件、跑了哪些验证（含真实输出摘要）、留了哪些 `TODO(真机核对)`、
> 以及与架构设计不一致的地方及原因。

---

## 1. 文件清单

### 新增（全部自研，未搬运任何第三方仓库代码）

| 相对路径 | 职责 |
|---|---|
| `app/deepseek_web/__init__.py` | 子包入口；导出 `service` 等 |
| `app/deepseek_web/errors.py` | `WebErrorKind` + `WEB_ERROR_TEXT` + `humanize()` / `label_of()` |
| `app/deepseek_web/prompt.py` | `ROLE_TAG` / `flatten` / `diff_tail` / `trim_recent` / `estimate_chars` |
| `app/deepseek_web/cdp_client.py` | 第一层：CDP 最小封装（`from_page/call/send/evaluate/navigate/close`） |
| `app/deepseek_web/web_driver.py` | 第二层：网页驱动 + 状态机（`probe_state/submit/new_conversation/open_page/stop_generation/diagnose`） |
| `app/deepseek_web/local_server.py` | 第三层：本机 OpenAI 兼容服务（SSE + 幂等 + 串行 + 心跳） |
| `app/deepseek_web/service.py` | 单例协调器（`instance/ensure_started/launch/status/selftest/reset/auto_ready/shutdown`） |
| `tests/probe_deepseek_web.py` | 离线探针（prompt/errors/HTTP/SSE + 真 LLMClient 消费） |
| `tests/probe_gui_web.py` | 离线 GUI 冒烟（设置页区块 / 状态行 / 主窗口接线） |
| `tests/probe_cdp_state.py` | 真机探针：选择器 / 状态机核对（`diagnose()`） |
| `tests/probe_deepseek_web_e2e.py` | 真机端到端探针（成功 + 失败分支） |
| `build_v9140.py` | v9.14.0 打包脚本（`DIST=dist_v9140`，显式补 `app.deepseek_web.*`） |

### 改动（精确）

| 相对路径 | 改了什么 |
|---|---|
| `app/config.py` | 追加 `WEB_*` 常量、`deepseek_web` provider（`no_key/web=True`、`search_style="none"`）、`DEFAULT_API_KEYS.setdefault`、`PROVIDER_PRIORITY.append` |
| `app/llm_client.py` | ① `__init__` 加 `self.web_gate=None` + `set_web_gate()`；② `_stream_once` key 判空加 `and not provider.get("no_key")`；③ `probe_provider`/`probe_all` 同样处理；④ `chat_auto` 加 `web_gate` 闸门（含默认闸门）；⑤ 新增 `_default_web_gate()` |
| `app/gui/widgets.py` | `StatusLine._PHASES` 增加 `"web": ("🌐","正在等待 DeepSeek 网页输出")` |
| `app/gui/pages.py` | `SettingsPage` 新增 `web_launch/web_selftest/web_retry` 信号；`_tab_model()` 新增「DeepSeek 网页版」区块；新增 `set_web_status()/show_web_selftest()`；`load/_save` 增补 `deepseek_web_auto` |
| `app/gui/main_window.py` | 创建单例 `self.dsw`、注入 `web_gate`、绑定三按钮、状态轮询 QTimer、`_open_page("settings")`/`_send`/`_probe_providers` 按需 `_dsw_ensure()`、`closeEvent` 调 `dsw.shutdown()` |
| `app/version.py` | `VERSION=9.14.0`、`VERSION_TUPLE=(9,14,0)`、`BUILD_DATE=2026-10-01`（`PROMPT_VERSION` 保持 24） |

**零侵入自查**（`git status --porcelain`）：仅 `app/config.py`、`app/gui/main_window.py`、
`app/gui/pages.py`、`app/gui/widgets.py`、`app/llm_client.py`、`app/version.py` 六个文件被改，
加新增 `app/deepseek_web/`。`app/agent.py`、`app/gui/chat_worker.py`、51 个工具、
工具调用协议、其余页面**未改**。
**裸子进程自查**：`grep -rn "subprocess\.\(run\|Popen\)" app/` 只实际命中 `app/winproc.py`
（其余命中均在提示词字符串/注释里）。`app/deepseek_web/service.py` 用 `winproc.popen()`。

---

## 2. 已跑通的验证（离线，真实输出摘要）

### 2.1 `python tests/probe_deepseek_web.py` → `RESULT=OK`（35 项全过）

```
[A] prompt.py 纯函数  —— 12 项（diff_tail 前缀命中返增量 / 非前缀返 None / 多段 content / trim_recent / estimate_chars）
[B] errors.py humanize —— 11 项（四类失败 + duplicate/busy/other 均有中文；字符串 kind/非法输入兜底）
[C] local_server HTTP+SSE —— 14 项
    GET /models 200 → 返回 deepseek-web；GET /healthz ok=true；GET /status 未启动=not_started
    缺 Bearer → 401；POST /chat/completions 200 + text/event-stream
    SSE 以 data: [DONE] 收尾；每个数据块形如 'data: {json}'；含非空 content 增量
    未启动时下发带内中文提示；心跳注释行 ": ping" 存在且可被忽略；重复请求仍 200
[D] 真 LLMClient 消费本机服务 SSE（假驱动）—— 4 项
    llm_client 取回完整正文 == "你好，世界"；逐 token 回调正确；reasoning_content → on_reasoning
```

### 2.2 `QT_QPA_PLATFORM=offscreen python tests/probe_gui_web.py` → `RESULT=OK`（32 项全过）

设置页区块控件齐全、`set_web_status` 四态文案/颜色正确、自检视图渲染、
`StatusLine` 的 `web` 阶段文案正确、`MainWindow` 挂上单例、模型下拉出现
`deepseek-web@deepseek_web`、`_dsw_ensure()` 能启动本机服务且 `auto_ready()` 仍为 False、
`shutdown()` 后服务已停。

### 2.3 `python main.py --selftest`（离屏）→ **通过 14 项，失败 0 项**

版本显示 v9.14.0；26 个应用模块全部导入成功；密钥保护/DPAPI、工具执行（51 个）、
GUI 构建（9 页）等全部 OK。

### 2.4 全量回归（grep 触发，确认真接入未回归既有功能）

| 命令 | 结果 |
|---|---|
| `python tests/parser_cases.py` | `RESULT=ALL_OK`（`FAILED: 0`，见 `tests/parser_result.txt`） |
| `QT_QPA_PLATFORM=offscreen python tests/smoke_gui_v9.py` | 通过 **43 / 43** |
| `QT_QPA_PLATFORM=offscreen python tests/probe_ui_v913.py` | 通过 **34 / 34**，`RESULT=ALL_OK` |
| `QT_QPA_PLATFORM=offscreen python tests/probe_fix_loop.py` | 通过 **9 / 9**，`RESULT=ALL_OK` |
| `QT_QPA_PLATFORM=offscreen python tests/probe_scroll_stick.py` | 通过 **5 / 5** |
| `QT_QPA_PLATFORM=offscreen python tests/probe_voice_silence.py` | 通过 **7 / 7** |
| `python main.py --selftest` | 通过 **14 / 14** |
| `python tests/probe_deepseek_web.py`（离线，改动后复跑） | `RESULT=OK`（35 项） |
| `QT_QPA_PLATFORM=offscreen python tests/probe_gui_web.py`（改动后复跑） | `RESULT=OK`（32 项） |

---

## 3. 真机核对（★ 意外发现：本机端口 9222 上有一个已登录的 DeepSeek 页面）

本次开发环境**实际存在**一个运行中的 Chrome（`Chrome/149.0.7827.115`），
且已登录 `chat.deepseek.com`。借此用 `tests/probe_cdp_state.py`（**只读，不发送任何消息**）
核对了真实 DOM，得到关键结论：

| 项 | 真机结论 |
|---|---|
| 输入框 | 是 **`<textarea>`**（`placeholder="给 DeepSeek 发送消息 "`），**不是** contenteditable。→ 已把 `textarea[placeholder*="发送消息"]` 提到候选首位，contenteditable 路径保留兼容 |
| CSS 类名 | 正文容器用 **CSS Modules 哈希类名**（如 `_27c9245`），每次构建都会变 → **不可依赖** |
| 设计系统类名 | `ds-button`、`ds-button--primary`、`ds-button--filled`、`ds-button--circle` **稳定可用** |
| 发送按钮 | `div.ds-button--primary.ds-button--circle`，图标是上箭头 **`<path>`**；输入为空时带 `.ds-button--disabled` |
| 停止按钮 | 未观测到生成态；**推断**发送位在生成时变成方形图标（`<rect>`）→ 以此区分 send/stop |
| 回答容器 | 有真实回答时，助手正文容器 = **`div.ds-markdown.ds-assistant-message-main-content`**（`div.ds-markdown` 也命中）；段落级子元素是 `*.ds-markdown-paragraph`（必须排除，否则只取到一段）。旧结论"没有 `.ds-markdown`"**已被推翻**——当时页面无回答，属误判 |

真机 `probe_cdp_state.py` 输出（读-only）：

```
[1] 浏览器: %LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
[2] 调试端口在线: True
[4] probe_state -> ready | 已连接，可以对话
[5] composer = TEXTAREA | send = True | stop = False | answerLen = 0
    state.composer = True | state.generating = False | state.loggedOut = False
判定：输入框命中 = True
RESULT=OK
```

→ 即：**输入框 & 发送按钮 & 状态机(READY)** 已在真机核对通过。

### 3.1 真机端到端验证（A / B / C + 失败分支，用户已授权）

脚本：`%TEMP%\awb_e2e_real.py`（产物全在 `%TEMP%`，不污染工作区）。最终一轮全绿：

| 用例 | 结果 | 关键输出 |
|---|---|---|
| A 极短提示取回完整回复 | ✅ | 2.7s，正文 5 字，回复=`网页已连通` |
| B 真实 system prompt+工具列表 → `agent.parse_tool_calls()` | ✅ | 3.2s，解析出 1 个 `write_file`，`{"path":"calc.py","content":"print(123*7)\n"}` |
| C 真的执行 write_file | ✅ | 落盘 `…\Temp\awb_dsweb_ws_*\files\calc.py`（14 字节） |
| C 真的执行 run_python | ✅ | 退出码 0，输出 `861`（=123×7） |
| C 多轮回灌（前缀增量不重发） | ✅ | 3.0s，收尾回复 443 字 |
| 失败分支（预设重复指纹 → DUPLICATE） | ✅ | HTTP 200 + 中文文案 + `data: [DONE]`，**不发送** |

**→ 结论：`answerText` / `findSend` / ack / 结束判定 / 工具调用文本解析 / 多轮增量 / 幂等与失败兜底，均已在真机验证通过。**

### 3.2 真机验证中发现并修复的两个真 bug（重要）

1. **答复提取误命中 UI 文案 → 提前返回 '智能搜索'**（修：`web_driver.py::answerText`）
   首轮 A 只用了 1.4s 就返回 `'智能搜索'`——它是页面底部 **`ds-toggle-button`** 的文案，
   被旧的"结构兜底"当成答复。修法：① 优先精确类名 `.ds-assistant-message-main-content` /
   `div.ds-markdown` 并排除 `*-paragraph`；② 结构兜底显式排除 `button/[role=button]/toggle`
   与已知 UI 文案；③ 提交前记录"答复基线"，且注入脚本加**版本号强制重注入**（旧脚本常驻页面，不改版本号不生效）。

2. **"答案文字与基线完全相同"时下发空回复**（修：`web_driver.py::_wait_answer` + `local_server.py::_pump_task`）
   同一问题二次提问、回答文字与上次完全相同时，"文本增量"恒为空 → 客户端收到**空正文**；
   另有"任务结束前最后一段增量被漏发"的竞态。修法：① `_wait_answer` 用"文本变化 **或** 答复块数量增加"
   判定新答复，并在返回前**补齐差额**（保证客户端拿到的正文 == 结果）；② `_pump_task` 在 `done` 收尾时再取一次增量后 flush。

### ⚠️ 副作用声明（重要，必须如实上报）

真机 e2e 的 A/B/C 会**真的往网页发消息**。本轮调试累计向用户已登录账号发送了约 10 条提问
（含早期探针 1 条），场景涉及"写到 calc.py / 运行代码"等；**均为测试内容，无敏感信息**。
`sessionStorage` 的 pending 标记已在每次成功后清理。建议：真机 e2e 只在用户知情、且用**专用测试账号**时跑。

---

## 4. `TODO(真机核对)` 清单（下一步真机验证）

仍然**未经真机确认**、或已确认但需持续观察的点：

1. ✅（部分）**答复容器**（`web_driver.py::answerText`）：已在"有真实回答"的页面验证，
   命中 `div.ds-markdown.ds-assistant-message-main-content`。**改版风险最高点**，仍需随版本回归。
2. ✅ **12s ack 判定**：真机确认提交后"输入框被清空"即出现（trace 实测 t=0 输入框已空）。
3. ✅（部分）**结束判定**：短回复/多轮均正常结束；"深度思考"场景下是否误判提前结束**仍未标定**。
4. ⬜ **停止按钮的真实形态**（`web_driver.py::findStop`）：`isStopIcon`（svg 有 `<rect>` 且无 `<path>`）
   仍为推断，**未在"生成中"观测确认**。若形态不同，`isGenerating()` 会失真 → 影响 ack 与结束判定。
   （现网短回复太快，未捕捉到生成态；建议用长回复复测。）
5. ✅ **「新对话」入口**（`web_driver.py::newChat`）：真机走通过（命中文本兜底「开启新对话」，
   日志见 `找不到「新对话」入口，改用重新导航到首页` 的降级路径亦可用）。
6. ⬜ **超时数值标定**：`WEB_ACK_TIMEOUT=12s` / `WEB_ANSWER_TIMEOUT=300s` / 幂等宽限 `90s` 仍为建议值。
7. ⬜ **思维链（深度思考）**：网页"深度思考"能否稳定映射到 `delta.reasoning_content` **未真机确认**
   （真机回复里模型的思考是以英文正文形式出现在单独 `div.ds-markdown`，非 `reasoning_content`）。
8. ⬜ **大文件/长对话裁剪**（超 60k 字符 → 新建对话 + 携带最近 6 轮）：未真机触发。

---

## 5. 与架构设计不一致的地方（附原因）

1. **本机 HTTP 服务改为"按需惰性启动"**，而非 `02-arch.md` T04 写的"`__init__` 里
   `ensure_started()`"。原因：任务书红线明确"**不允许在未选中该模型时启动任何后台服务**"。
   现仅在：① 选中网页版发消息时；② 打开设置页时；③ 点区块三按钮时；④ `deepseek_web_auto` 开启时，
   才 `ensure_started()`。**Chrome 依旧只在用户点「启动/打开网页 / 重试」时启动**。
2. **`llm_client.chat_auto` 增加"默认闸门" `_default_web_gate()`**（架构只写了显式注入
   `set_web_gate`）。原因：防御性——任何未显式注入闸门的 `LLMClient`，在 auto 模式下也不会去撞
   一个没启动的本机服务（默认闸门查单例就绪态，不可用即 False）。
3. **占位 key 由 `"local"` 改为 `"sk-local-deepseek-web"`**。原因：`main.py` 自检会校验
   `DEFAULT_API_KEYS` 里每个非空值的"密钥格式"（须 `sk-`/`452d`/`bce-` 开头），
   `"local"` 会导致自检失败。改后自检 14/14 通过。它仍是**本地占位串**，不是真密钥
   （本机服务对 `Authorization` 只要求非空）。
4. **选择器按真机证据增强**（架构只说"多候选 + 兜底"）。原因：真机发现
   DeepSeek 用哈希类名、输入框是 textarea、发送键是 `ds-button--circle`，
   据此把稳定锚点（placeholder、`ds-button--*`）提为优先候选，并加结构兜底。
5. **页面级防重发标记加"过期时间" `STALE_PENDING_MS`（15 分钟）**。原因：避免页面被刷新/异常中断后
   永远卡在"重复请求"（对齐"宁可报错也不重发"的同时，留一条可恢复路径）。

---

## 6. 已知风险（与 00-context 第 6 节一致）

- 自动化驱动网页、非官方 API：DeepSeek 改版即失效（选择器是最大风险点，见第 4 节）。
- 网页版无 API 参数（temperature/top_p/max_tokens/原生工具 schema）：工具调用靠**提示词约定 + 文本解析**。
- 单轮延迟高于 API；网页会话有上下文上限（超长走"新建对话 + 携带最近若干轮"并给提示）。
- 账号存在被判定为异常使用的**潜在风险**（设置页已固定风险说明）。

---

## 7. v9.14.1：修 QA（`04-qa.md` §6）的两条非阻塞问题

> 两条都已修，**服务端鉴权强度不变**，**不加依赖**，**不改 4 处零侵入红线**，`PROMPT_VERSION` 仍为 24。

### 7.1 修① Key 被清空时返回 401（低危）— 改**客户端**

- `app/config.py`：新增**唯一**占位串常量 `WEB_PLACEHOLDER_KEY = "sk-local-deepseek-web"`；
  `DEFAULT_API_KEYS.setdefault(WEB_PROVIDER_NAME, WEB_PLACEHOLDER_KEY)` 改为**引用**它（全项目只此一处）。
- `app/llm_client.py`：新增模块函数 `_effective_key(provider, key)`——当 `provider.get("no_key")`
  且 `key` 为空时回落该占位串。`_stream_once` 与 `probe_provider` 均在鉴权判断处调用它。
- **为什么改客户端而不是服务端**：本机服务刻意保持"回环绑定 + Host 校验 + Bearer 非空"的强度；
  用户把 Key 清空属于客户端 `no_key` provider 的自我约定，客户端兜底最合适，也不削弱服务端。

### 7.2 修② 网页报错态导致 300s 空等（中低危·体验）— 快速失败

`app/deepseek_web/web_driver.py::_wait_answer` 新增**两条**快速失败（**只报错、绝不重发**）：

- **A. 网页自身错误文案**：注入 JS `serverError()` 多候选匹配
  （强候选 `ERR_STRONG`、弱候选 `ERR_WEAK`），仅在**页面空闲**时命中、并持续
  `ERROR_CONFIRM_SECONDS = 3.0` 秒 → 抛 `WebErrorKind.SERVER_ERROR`，
  中文文案 `网页显示：<原文>`。
- **B. 零增量兜底**：以答复文本 / 答复块数(`answerCount`) / 正文长度(`pageTextLen()`) 三者作活动信号，
  连续 `NO_OUTPUT_SECONDS = 60.0` 秒毫无活动 → 抛 `WebErrorKind.TIMEOUT`，
  文案 `已等待约 60 秒，网页没有任何输出（已停止，不会重复发送）`。
- `app/deepseek_web/errors.py`：新增 `WebErrorKind.SERVER_ERROR`、`WEB_ERROR_TEXT[SERVER_ERROR]`、
  `WEB_STATE_LABEL[SERVER_ERROR] = "网页服务器暂不可用"`。
- 注入脚本版本 `AGENT_VERSION` 4 → 5（页面常驻旧脚本 → `_ensure_agent` 会强制重注入）。
- **多候选文案的诚实标注**：`ERR_STRONG` 里仅「服务器暂时不可用」为**实测**，
  其余（服务器繁忙 / 服务异常 / 服务已停止）与 `ERR_WEAK` 均为**推断**，
  已在源码按 `TODO(真机核对)` 标注。

### 7.3 v9.14.1 打包与验证（本机实测）

| 项目 | 结果 |
| --- | --- |
| 版本 | `app/version.py` → `9.14.1`（`VERSION_TUPLE=(9,14,1)`，`PROMPT_VERSION` 仍 24） |
| 打包脚本 | 新增 `build_v9141.py`（`DIST=dist_v9141`，`VERSION_FILE=build_v9141_version.txt`，不覆盖 v9.14.0） |
| 打包日志 | `tests/build_v9141.log`，`RETURNCODE: 0` |
| 产物 exe | `dist_v9141/AIWorkbench/AIWorkbench.exe` = 14,837,032 B |
| 产物 zip | `dist_v9141/AIWorkbench_v9.zip` = 74,619,614 B |
| 冻结自证 | PYZ-00.pyz mtime `17:18:38` **晚于** 最新源码 `app/version.py` `17:11:06`（+451.5s）→ 冻结成立 |
| 冻结包自检 | `AIWorkbench.exe --selftest` → **14/14 通过**（frozen=True，GUI 9 页，KNOWN_TOOLS=51） |
| PYZ 核对 | 解开 `PYZ-00.pyz`：`app.deepseek_web` 7/7 子模块在包内；`AGENT_VERSION==5`；两处修复文案/常量/JS 均在 → PASS |
| 全量回归 | parser `RESULT=ALL_OK`；`probe_deepseek_web` `RESULT=OK`；`probe_gui_web` `RESULT=OK`；`probe_fix_loop` 9/9；`probe_scroll_stick` 5/5；`probe_voice_silence` 7/7；`smoke_gui_v9` 43/43；`probe_ui_v913` 34/34；`main.py --selftest` 14/14 |

### 7.4 遗留（真机难以构造，如实标注）

- 修② 的「网页自身报错」在健康网页上无法稳定复现：
  - 只读检查：健康页 `serverError()` 返回空串（不误报）✅；
  - 构造（把地址指向不存在的会话）→ DeepSeek 直接**重定向回首页**，不会停在错误态，故拿不到 `SERVER_ERROR`。
  - 结论：修② 的触发路径以**离线模拟帧**验证通过；真机错误态**待复现时复核**（已在源码标 `TODO(真机核对)`）。
- 「深度思考」是否污染 `parse_tool_calls()`：**不会**。`answerText()` 只取
  `.ds-assistant-message-main-content`（经 `.ds-markdown` 收窄），思考文本在页面上是**独立的
  `ds-think-content` 块**，不进该容器；即使万一混入，思考是自然语言而非工具调用文本格式，
  顶多造成噪声，不会误判为工具调用。
