# 项目背景：把 DeepSeek 网页版接入 AIWorkbench

> 本文件是本次开发的**共享事实底稿**，由主理人在启动工作流前完成调研后写成。
> 所有成员（PM / 架构 / 工程 / QA）请以本文件的事实为准；与代码冲突时以代码为准并回报。

---

## 1. 需求

用户（刘浩辰，初三学生，本机 AIWorkbench 的作者与唯一使用者）看到 GitHub 上的
`DSH-webtokens` 项目，希望**把 DeepSeek 网页版（免 API Key）作为模型接进自己的 AIWorkbench**。

用户原话：
> "DSH-webtokens 看看这个 GitHub 上的开源的这个项目，这个好像就是我说的 Deepseek 的网页版的，
> 就是调用，尝试把它这个装进我的程序里。"

已通过选项确认，用户选定路线：**内嵌驱动 Chrome**（不装 Node 常驻、不装浏览器扩展、不依赖中转站）。

### 为什么这个需求对他有真实价值

`AIWorkbench/app/config.py` 的注释记录了他的处境：他要求"所有模型必须完全永久免费"，
于是 DeepSeek 官方 API 被整体移除（无永久免费额度），阿里百炼的 `deepseek-v4.1-flash`
在 2026-09-30 实测已 `403 AllocationQuota.FreeTierOnly` 额度耗尽，硅基流动只剩
DeepSeek 蒸馏小模型（8B/7B）。**他现在没有可用的强模型，主力只有 GLM-4.7-Flash。**
DeepSeek 网页版对他是"免费 + 强"的唯一现实解。

---

## 2. 外部调研结论

### 2.1 `DSH-webtokens` 生态是什么

| 仓库 | 定位 |
|---|---|
| `xinyuquan985-coder/DSH-webtokens` | **DSH（DeepSeek Harness，`@deepseek-ai/dsh`）的第三方插件**。把已登录的 DeepSeek 网页注册成 DSH 模型菜单里的一项（`deepseek-web`）。依赖 DSH CLI + Chrome 扩展 + 本机 broker。 |
| `mengyunqwq/dsh-webtokens-lite` | **自研重写版（零依赖、Node ≥22）**：扩展只搬运文本，解析交给客户端。作者踩坑经验最丰富，文档质量高。 |
| `studyzy/dsh-web-remote-access` | 让 DSH 支持远程 Web 访问的插件（与本次无关）。 |

### 2.2 共同架构（关键洞察）

```
调用方（任何程序）
   │  HTTP
   ▼
本机 broker (127.0.0.1:3081, Bearer 鉴权)
   │  扩展主动长轮询 /ext/poll  ← 重要：扩展拉任务，broker 不推送
   ▼
Chrome 扩展（MV3, service worker + content script）
   │  操作 DOM
   ▼
chat.deepseek.com 网页标签页（已登录）
```

**决定性事实**：broker 的 `POST /task` 是**纯管道** —— 请求体 `{id?, prompt, timeoutMs, sessionKey?}`，
响应为 **NDJSON 流**（`{"type":"progress",...}` … `{"type":"result","ok":true,"text":"…"}`）。
它**不做任何解析**：扩展只负责"提交提示词 / 观察生成状态 / 把答复**原文**取回"。
JSON 解析、工具参数校验、格式约束全在**调用方**。

→ 对我们的意义：**AIWorkbench 已有自己的 Agent 循环与工具协议，不需要它那套解析，
只需要"提示词进、原文出"的能力。**

### 2.3 它踩过并写进文档的坑（★ 直接吸收，能省大量试错）

1. **输入框不是 `<textarea>`**。新版 DeepSeek 网页的输入框是 **contenteditable 富文本容器**，
   直接赋 `.value` 无效、提交前校验也读不到内容。原作者为此专门写了 `dom.js` 的
   `setComposerText/composerText` 同时兼容两种形态。**我们的注入脚本必须处理这一点。**
   原文注释："以前这里自己写了一份只认 HTMLTextAreaElement 的 setter …… 表现为
   「提示词没有进入网页输入框」直接判死。"
2. **后台标签页渲染节流**。标签页不在前台时，页面渲染时钟被节流，答复变慢/卡住且**无报错**。
   上游用 `clock.js`（注入 MAIN world，任务期间维持渲染时钟）缓解；
   有趣的是 lite 版文档承认那段补丁一度是**死代码**（没人写 `data-dsh-own-active` 属性）。
   → 我们的对策首选**让窗口可见**（我们本来就打算显示这个窗口），必要时再加兜底。
3. **生成结束判定不能写死时间**。lite 版规则：
   - a) 停止按钮消失 **且** 答复文本 ≥800ms 未变化 → 取回
   - b) 停止按钮消失 **且** 答复文本看起来是完整 JSON 对象 → 取回
   上游曾写死 5 秒 + 45 秒阈值，体验差。
4. **绝不能重复发送**。发送前先在 `sessionStorage` 落标记；若页面在"已提交、未拿到结果"时被刷新，
   醒过来**只报错、绝不重发**。理由（原文）：否则用户账号里会出现两条一模一样的提问，
   "正是这套机制要拦的第一号事故"。
5. **12 秒内没等到"网页确认收到"**（输入框被清空 或 出现停止按钮）就判失败，同样不重发。
6. **单 worker 串行**：一个网页会话一次只跑一轮，并发要在调用侧做准入控制。
7. 其他安全性质：Bearer token（43 字符 base64url）、Host 校验（挡 DNS rebinding）、
   任务 lease（防旧实例把结果写进新任务）、action 白名单。

### 2.4 ★ 合规结论（主理人已核实，全员必须遵守）

| 仓库 | 许可证 | 结论 |
|---|---|---|
| `xinyuquan985-coder/DSH-webtokens` | **无 LICENSE（404）** | 保留所有权利。**禁止复制其任何代码** |
| `mengyunqwq/dsh-webtokens-lite` | **MIT** | 技术上可用，但需保留版权声明 |

**本项目的决定：全部自研，不复制任何一方的代码。**
只允许借鉴**公开文档中记录的思路与踩坑经验**（技术思路不受版权保护）——
即本文件第 2.3 节里已经提炼过的那七条。理由：
1. 我们要做的事（CDP 驱动 + DOM 操作）本身不复杂，自研成本低；
2. 无许可仓库的代码一旦混入，整个项目的分发就带上了法律瑕疵；
3. 用户的项目是公开仓库（GitHub `lhclhc123/AIWorkbench`），不能埋这种雷。

**落地要求**：新写的模块里不得出现任何从上述仓库逐字搬运的代码段、注释或标识符命名；
不得引入其 Chrome 扩展文件、`dom.js`、`protocol.mjs` 等产物。

---

## 3. 采用的技术路线（主理人已用 PoC 验证）

**不再走 broker + 扩展，改为 AIWorkbench 自己用 CDP 驱动本机 Chrome。**

理由：
- 零外部组件：不需要 Node 常驻、不需要手动"加载解压的扩展"、不需要中转站；
- 可直接打进 exe（`websocket-client` 已在环境中，体积增量极小）；
- 用户机器上 Chrome 已安装，登录态稳定。

### 3.1 PoC 结果（已验证，2026-10-01）

探针脚本：`tests/probe_cdp_poc.py`（已在仓库内，可直接复跑）

```
1) Chrome 已连接: Chrome/149.0.7827.115 | Protocol 1.3
3) CDP 握手成功，导航 -> https://chat.deepseek.com/
4) 页面就绪: True | readyState = interactive
5) url = https://chat.deepseek.com/sign_in
   title = DeepSeek - 探索未至之境
   bodyLen = 108  loginHint = True
   正文 = +86 发送验证码 注册登录即代表已阅读并同意... 微信扫码登录
RESULT=OK
```

即：**拉起 Chrome → 连 CDP → 执行 JS → 打开 DeepSeek 网页，全链路已通**
（当前是登录页，因为专用 profile 尚未登录）。

### 3.2 目标架构

```
AIWorkbench Agent 循环 (app/agent.py —— 不改动)
   │  messages[]
   ▼
app/llm_client.py  LLMClient._stream_once()   ← 不改动，OpenAI 兼容 SSE 客户端
   │  POST {base_url}/chat/completions  (stream=true)
   ▼
【新增】本机 OpenAI 兼容中转服务（127.0.0.1: 随机端口，仅监听回环）
   │  ① messages → 单段提示词   ② 结果 → SSE chunk 流
   ▼
【新增】DeepSeek 网页驱动（CDP over WebSocket）
   │  填输入框 → 点发送 → 等生成结束 → 抓答复原文
   ▼
【新增】CDP 客户端（websocket-client）
   ▼
本机 Chrome（专用 user-data-dir，--app 模式，已登录 chat.deepseek.com）
```

### 3.3 为什么选"本机 OpenAI 兼容服务"这一层

`app/llm_client.py:131`：`url = provider["base_url"].rstrip("/") + "/chat/completions"`，
`app/llm_client.py:329`：`probe_provider` 走 `{base_url}/models`。

→ **只要提供这两个端点，`config.py` 里加一个 provider 就能接入，
`agent.py` / `chat_worker.py` / 工具调用协议全部零改动。** 这是最小侵入的接入点。

### 3.4 ★ 接入点硬约束（主理人已读 `app/llm_client.py` 原文核实）

这些是**事实约束**，设计时必须逐条满足：

| # | 约束 | 出处 / 说明 |
|---|---|---|
| 1 | **provider 必须非空 API Key** | `_stream_once` 第 128-130 行：`key = self.keys.get(provider["name"], "")`，为空直接 `raise LLMError("缺少 API Key")`。本机服务不需要真密钥 → **必须给一个占位 key**（如 `"local"`），或把本机 provider 加入"免密钥"白名单。**不改这里，选中该模型会立刻报"缺少 API Key"。** |
| 2 | **SSE 格式必须严格** | 解析器只认 `data:` 前缀行（第 182 行），正文取 `obj["choices"][0]["delta"]["content"]`（第 193-200 行），流结束认 `data: [DONE]`（第 186 行）。 |
| 3 | **非流式也可兜底** | 若整段不是 SSE，会尝试当普通 JSON 解析，取 `choices[0].message.content`（第 205-218 行）。 |
| 4 | **`delta.reasoning_content` 是现成钩子** | 第 197-199 行会把思维链单独回调，界面已能渲染成"深度思考"块。**DeepSeek 网页版的"深度思考"输出正好可以映射到这里**，是本方案的一个加分点。 |
| 5 | **请求体字段** | `_build_payload`（第 72-104 行）发出 `model / messages / temperature / stream`；联网时还会按 `search_style` 追加字段。→ 新 provider 建议设 `"search_style": "none"`（第 89-90 行会跳过追加），本机服务对多余字段也必须容错。 |
| 6 | **必须实现 `/models`** | `probe_provider`（第 318-345 行）请求 `{base_url}/models` 做连通性自检，界面"测试端点"按钮依赖它。 |
| 7 | **超时语义** | `_stream_once` 用 `timeout=(10, timeout)`（连接 10s / 读取 timeout）。网页生成可能较慢 → 本机服务应以**心跳 SSE 注释行或空 delta** 保持连接不被读超时切断，或在 provider 侧支持更长 timeout。 |
| 8 | **重试语义** | 429 / 5xx 会被 `_RETRYABLE_HTTP` 判为可重试并**重发同一次请求**。网页驱动必须保证"同一次请求重发不会在用户账号里产生两条提问"（见第 2.3 节第 4 条：宁可报错也不重发）。**这是设计上必须正面回答的问题。** |

---

## 4. 本机环境硬事实（已实测）

| 项 | 值 |
|---|---|
| Chrome | `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`（实测 Chrome/149.0.7827.115） |
| Edge（备用） | `%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe` |
| 唯一可用 Python | `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`（**只有它装了 PyQt6 + PyInstaller**） |
| websocket-client | **1.9.2 已安装** ✅ |
| websockets | 17.1 已安装 |
| PyQt6-WebEngine | **未安装**（本方案不使用，避免 +150MB 体积） |
| Node | 22.22.2（本方案不使用） |

### 沙箱/命令注意事项
- Bash 前必须 `export PATH="/usr/bin:/bin:/c/Windows/System32:/c/Windows:$PATH"`（PATH 有时被破坏）。
- Bash 工具偶尔报 `sandbox-center cmd decisionRecord missing actual resource subject` → 直接重试。
- 中文界面截图必须设 `QT_QPA_FONTDIR="C:/Windows/Fonts"`，否则离屏字体失真。

---

## 5. AIWorkbench 项目现状与工程约束（★ 必须遵守）

项目根：`<仓库根>/`（本机为 `D:\...\AIWorkbench\`）
当前版本 **v9.13.1**，51 个工具、9 个页面。

完整开发规范见技能文件：`~/.workbuddy/skills/aiworkbench-agent-dev/SKILL.md`
（**动手前先读它**，里面有打包、测试、隐私扫描、界面规范等全部约定。）

必守铁律（摘要）：
1. **版本号** `app/version.py`：本轮完成后 `VERSION` 与 `VERSION_TUPLE` 各 +0.1（→ 9.14.0）。
2. **改提示词必须 `app/config.py` 的 `PROMPT_VERSION += 1`**（当前 24），否则老工作区不刷新。
   —— 本次若动 `DEFAULT_SYSTEM_PROMPT` 就要 +1。
3. **`app/` 下禁止裸 `subprocess.run/Popen` / `os.system`**，一律走 `app/winproc.py`。
   自查：`grep -rn "subprocess\.\(run\|Popen\)" app/` 只应命中 `winproc.py`。
4. **打包**：新建 `build_v9140.py`（从 `build_v9131.py` 复制改名，DIST=`dist_v9140`），
   不覆盖旧脚本。打包前必须冻结源码（改完 → 跑全回归 → 才打包）。
5. **打包前先 `mv build/` 到 `D:\aiworkbench_old_builds\`**（沙箱对同 turn 删除 >50 文件有硬保护）。
6. **推送 GitHub**：`python tools/push_release.py -m "..." [--tag vX.Y.Z --zip dist_vXXXX/AIWorkbench_v9.zip]`
   （自带隐私扫描硬门槛）。本机代理不稳，push 常需重试 4~20 次，属正常。
7. **`tests/` 整个目录不进仓库**（探针含本机路径/调试输出）。可复用工具放 `tools/`。
8. 交付时必须**明确告知用户新版 exe 的完整路径**，并用 `present_files` 交付。
9. **钉钉（dws）逐次授权铁律**：只有用户在**当次对话**里明确说"发"，才发；其余一律不发。

### 界面规范（v9.13 起，仿 WorkBuddy，别改回去）
- 助手消息**无气泡无描边**（透明底直排）；用户消息保留蓝气泡；工具调用/中间过程 = 一行灰胶囊；
  带结论/原因/建议的中间块默认展开 + 左侧强调竖线（判据 `agent.is_key_progress`）。
- `themes.qss()` 的 `QWidget` 字体族**必须含 `"Segoe UI Symbol","Segoe UI Emoji"`，
  且排在 `"Segoe UI"` 之后** —— 否则 ⚙/⏹ 在中英混排下渲染成空心方块。
- `_clear_chat()` 必须 `w.setParent(None)` 再 `deleteLater()`，否则旧卡片和新消息叠着画。

---

## 6. 已知风险（需在交付时向用户如实说明）

1. 这是**自动化驱动网页**，不是官方 API。DeepSeek 若改版页面结构，选择器会失效；
   账号存在被判定为异常使用的**潜在风险**。用户须知情。
2. 网页版**没有 API 参数**（temperature / top_p / max_tokens / 工具 schema 原生支持）。
   工具调用只能靠**提示词约定 + 文本解析**。
3. 单轮延迟约 **2~4 秒**（不含网页生成时间），比 API 慢。
4. 网页会话有上下文长度限制，长对话需要"新建对话 + 携带摘要"。

---

## 7. 验收标准（用户视角）

用户判断成败的唯一标准是"**有没有产物**"。本需求必须做到：
1. AIWorkbench 的模型菜单里出现 **DeepSeek 网页版**，选中它能正常多轮对话。
2. 能**真正调用工具**（真正写文件、真正跑代码），而不是只会聊天。
3. 全程**不需要**安装 Node / 浏览器扩展 / 中转站；只要求用户登录一次 DeepSeek 网页。
4. 打包成 exe 后，在**另一台没装过任何东西**的机器上，只要装了 Chrome 就能用。
5. 有可见的状态提示（网页是否已连接 / 是否已登录 / 正在等待网页出字）。
