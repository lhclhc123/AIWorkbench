# v9.19.1：DeepSeek「接口直发」通道 + 对话/记忆隔离

> 起因（用户原话）：
> ①「deepseek 的调用能不能尽量模拟浏览器直接发起请求，不要走这种类似网页点击的」
> ②「在切换不同对话的时候会导致对话记录、记忆混乱、错乱，非常严重，必须要改」

---

## 一、为什么要换掉"模拟点击"

老链路（`web_driver.submit`）是**模拟人手**：

```
往 contenteditable 里写 JS 塞字
  → 等发送键从 disabled 变 available（最多 6s）
  → JS .click()
  → 5s 内轮询"输入框清空了吗 / 出现停止键了吗 / 多出答复块了吗"
  → 没确认到 → 真键盘 Enter 补发
  → 还没确认 → CDP 真实输入重敲一遍再点
  → 轮询页面文本，直到 0.8s 无变化
```

这条路有三类天生毛病：

| 毛病 | 表现 |
|---|---|
| **脆** | DeepSeek 一改版（class 名、按钮位置、React 渲染时序）就废 |
| **慢** | 每轮固定多花 1~3 秒在"填字 + 等按钮 + 等确认"上 |
| **脏** | 点不准会重复发送，或把半截话留在输入框里；窗口不可见时**静默丢点击** |

用户实际踩到的："一条对话调了十几条新对话"、"问 '你是谁' 结果吐出一堆历史消息"。

## 二、新链路：在页面上下文里 fetch

```
注入 window.__AWBAPI__（一次）
  → POST /api/v0/chat_session/create            （需要时才建）
  → POST /api/v0/chat/create_pow_challenge      （拿 PoW 挑战）
  → 自己算 nonce（32 位 Keccak，见下）
  → POST /api/v0/chat/completion  带 x-ds-pow-response
  → 读 SSE 流，累积到 window.__AWBAPI__.buf
  → Python 每 250ms 取快照算增量 → on_progress 原样回调
```

**它和"用户按 F12 在控制台敲一条 fetch"完全是一回事**：真实 Cookie、同源、
真实 UA、真实 TLS 指纹。不碰 DOM，因此：

- 不需要窗口可见（后台/最小化都能跑）；
- 不会重复发送（没有"点了没反应再点一次"这回事）；
- 不会多开网页对话（会话 id 自己管，`new_conversation()` 只是清掉 id）；
- 页面改版只要接口没变就不受影响。

## 三、PoW 为什么必须自己做

DeepSeek 要求请求头 `x-ds-pow-response`：

```
base64(JSON{
  algorithm: "DeepSeekHashV1",
  challenge, salt, signature, target_path,
  answer: <nonce>
})
```

`answer` 要满足

```
sha3_256(salt + "_" + expire_at + "_" + nonce) < 2^256 / difficulty
```

`difficulty` 是**数字**（实测约 144000），所以判据是**数值比较**，不是"前导零个数"。
页面里那份 `wasm_sha3` 的 ABI 不对外，所以本项目自带一份 **32 位 Keccak-f[1600]**。

### 实测数据

| 项 | 结果 |
|---|---|
| SHA3-256 vs `hashlib.sha3_256` | 空串 / `abc` / 中文 / 135B / 136B / 300B **全部逐字节一致** |
| difficulty=144000 求解 | **859ms**（74,130 次哈希，约 8.6 万次/秒） |

> 第一版每轮都转 hex 字符串 + 新建 `Uint8Array`，只有 3 万次/秒（4 秒）。
> 改成"预分配缓冲区 + 只改 nonce 字节 + 按字节比阈值"后提速到 8.6 万次/秒。

## 四、**绝不硬上**：失败即永久降级

```python
if self.api_mode == "auto":
    try:
        return self._submit_api(...)          # 直发
    except Exception as exc:
        self.api_mode = "off"                 # 本会话不再试
        self._api_fail_note = str(exc)[:300]
        self._logf("warn", f"接口直发不可用（{exc}）—— 改用模拟点击链路")
# ↓ 原样继续老的点击链路，一行没改
```

触发降级的情形（都算"硬失败"，不重试）：

| 错误 | 含义 |
|---|---|
| `not-logged-in` | `localStorage.userToken` 为空（网页没登录） |
| `pow-challenge-failed` | 挑战接口返回结构变了 |
| `session-create-failed` | 建会话失败 |
| `completion-code-<n>` | 服务端业务错误码（如 40003 INVALID_TOKEN） |
| `completion-http-401/403` | 鉴权被拒 |

排查时可用环境变量 `AWB_DSW_API=off` 强制走老链路。

## 五、对话 / 记忆隔离（第二件事）

实测事故：A 对话跑到一半切到 B，A 的产物、记忆、团队过程提示全落进了 B。

根因是**一堆"全局唯一"的状态**：

| 原来 | 现在 |
|---|---|
| `workspace.files_dir` 读全局 `_cur_folder` | 运行中用 `files_dir_for(cid)` |
| `self.runner` 全进程一份 | `ConvRun.runner`，每个对话一份 |
| `_build_system_prompt()` 读全局对话记忆 | `_build_system_prompt(cid)` |
| 提示词里「文件沙箱：…」也是全局 | 同样按 `cid` |
| `_append_system_note(text)` 写 `self.conv` | `_append_system_note(text, cid)` |
| `_team_evt` 只有 (kind, payload) | 加第三个参数 `cid` |
| `team_bar.set_team()` 无条件 emit | `set_team(..., silent=True)` 回填时不广播 |

顺带把老 bug 写进去的脏数据也洗掉（`_sanitize_conv`）：

- 清 `␜AWB-RESET␝` 及单边残骸（那是"作废重来"的内部信号，被 DeepSeek 网页通道
  当正文塞进了历史，模型看到会照着抄）；
- 重复的「⬡ 本对话已切回普通模式」压成 1 条（实测一个对话刷了 7 遍）——
  这条还会作为 `role=tool` 喂给模型，导致模型"复述历史消息"；
- 空的过程块丢弃（只清 `tool`/`thinking`，`assistant` 空着也不动）。

## 六、验收

| 探针 | 结果 |
|---|---|
| `tests/probe_conv_isolation_v919.py` | **29/29** |
| `tests/probe_ds_api_path_v919.py` | **39/39** |
| `tests/probe_multi_conv_v9180.py` | **19/19** |
| 全量回归 `tests/run_all_v9180.py` | 见 `tests/run_all_v9191.log` |

## 七、已知限制 / 待真机确认

1. **SSE 结构**：解析器同时兼容老版 `{"v":{"response":{"content":…}}}` 和
   新版 JSON Patch 风 `{"p":"response/content","o":"APPEND","v":"…"}`，
   但**没有在已登录的真机上跑过一次完整回合**（本机 profile 的
   `localStorage.userToken` 为空，跳到了 `/sign_in`）。
   万一解析不出来，表现是"直发拿到 0 字"→ 立刻降级回点击链路，不会卡死。
2. **`ref_file_ids`**：直发通道目前不传附件（老链路也没有附件能力，属同一水平）。
3. **`thinking_enabled` / `search_enabled`**：默认 false，可用
   `driver.api_thinking = True` / `driver.api_search = True` 打开。

---

## 八、v9.22 现状：直发已被官方加固，**默认关闭**

真机核对（2026-10-31，内嵌 QtWebEngine / Chrome 134，已登录账号）结论：
**这条接口直发通道在当前 DeepSeek 网页版本上 100% 不可用**，因此 v9.22 起
`WebDriver.api_mode` 的**默认值改为 `off`**，主链路回到「驱动真实界面点击」。

### 8.1 实测现象

| 请求构造 | 服务端响应 |
|---|---|
| `authorization` + `x-ds-pow-response` | HTTP 200，**纯 JSON** `{"code":40300,"msg":"MISSING_HEADER"}` |
| 再补齐 `x-client-bundle-id/-platform/-version/-locale/-timezone-offset`、`x-device-id/-model` | HTTP 200，**纯 JSON** `{"code":40301,"msg":"INVALID_POW_RESPONSE"}` |
| 再补 `x-hif-dliq` / `x-hif-leim`（腾讯防水墙 token） | 仍是 `40301 INVALID_POW_RESPONSE` |

注意：加固后**不再回 SSE**，而是回一行纯 JSON。老解析器只认 `data:` 前缀行，
会把这一行整个跳过 → 表现为"2.9 秒后没拿到任何正文"再默默降级 → 用户只看到
60 秒超时。v9.22 已在读取器里识别这种"非 SSE 的 JSON 拒绝"，立刻抛
`api-blocked-<code>:<msg>`，秒级降级。

### 8.2 40301 不是 PoW 算错了

用 Python `hashlib.sha3_256` 对页面算出的 nonce 做独立复算：

- `salt` / `expireAt` / `nonce` 拼接格式正确；
- `difficulty = 144000` 正常下发；
- `sha3_256(salt_expireAt_nonce) < 2^256 / difficulty` 成立；
- 且该 nonce 是**最小解**（小于它的候选都不满足阈值）。

所以拒绝来自服务端的**额外绑定校验**（challenge 与设备指纹 / 会话绑定，
前端用 `static/sha3_wasm_bg.*.wasm` 生成 + 防水墙 token），不是算法或盐值变更。
要修必须逆向 wasm 与防水墙，成本远超收益 —— **放弃直发，保留点击链路**。

### 8.3 为什么默认关闭（而不是留着"试一下再降级"）

直发是「**先建会话，再发**」：`/api/v0/chat_session/create` 成功后才发 completion。
既然 completion 必然 40301，每次进程启动的第一条消息都会在用户 DeepSeek
侧边栏留下一个空会话（`is_empty: true, title: null`）—— 这是**污染用户账号数据**，
比"省 2.9 秒"重要得多。

开关：

| `AWB_DSW_API` | 行为 |
|---|---|
| 不设 / `off` / `0` / `false` | **默认**：完全不走直发，直接点击链路 |
| `auto` / `on` / `1` / `true` / `yes` | 先试直发，失败永久降级（供以后复测） |

### 8.4 主链路可用性（真机 E2E）

`WebDriver.submit("只回复两个字：好的")` 在已登录的内嵌宿主上
**8.7 秒返回「好的」**，无 10053、无 60 秒干等。点击链路本来就不经过直发，
所以「默认 `off`」不影响正常使用。

### 8.5 一并修掉的 CDP 根因：本地调试口被系统代理劫持

用户报"网页版根本不能用"的真凶不在 DeepSeek，而在**本机 `http_proxy`**：
`urllib` 与 `websocket-client` 默认都读该环境变量，于是
`ws://127.0.0.1:<调试端口>/devtools/...` 被塞进代理隧道，代理一断流就是
`[WinError 10053]`，每 2.5 秒一条，所有 CDP 命令全废。

修复：`cdp_client.local_urlopen()` 用空 `ProxyHandler` 访问本地调试口；
`websocket.create_connection(..., http_no_proxy=["127.0.0.1", ...])`。
另外 `CDPClient` 新增 `_dead` 标记（`.connected` 在对端 abort 后仍为 True，
不能作为存活依据）与 `ping()`，并让 `service` 在连接确实已死时自动重连 / 重建宿主。

### 8.6 回归

| 探针 | 结果 |
|---|---|
| `tests/probe_ds_api_path_v919.py` | **40/40**（含"默认 off"与"显式 auto 能力不变"两条） |
| `tests/probe_ratelimit_v920.py` | **67/67**（含默认 off 断言，限流不降级能力保持不变） |
