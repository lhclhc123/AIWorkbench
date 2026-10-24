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
