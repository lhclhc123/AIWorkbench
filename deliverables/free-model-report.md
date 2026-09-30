# AI 工作台 · 免费模型核查与"必须删除付费模型"报告

> **查证日期：2026-09-30**
> **核查对象**：`app/config.py` 中 `PROVIDERS` 全部 6 个端点、共 22 个模型
> **核查方法**：以厂商官方定价页 / 官方公告 / 官方文档为主，第三方汇总为辅；每条结论尽量给出来源 URL
> **免责声明**：AI 厂商免费政策变动非常频繁（历史上智谱 Coding Plan 涨价、Gemini 配额腰斩、腾讯混元旧平台 9/30 停服均为前车之鉴）。本报告结论仅代表 2026-09-30 时点，落地前请工程师/用户再点一次官方页面确认。查不到可靠依据的已在第 8 节如实标注"未查证"。

---

## 1. TL;DR

1. **DeepSeek 官方 API 没有"永久免费"**：官方定价页（2026-09-30 直接抓取）只列 `deepseek-flash` / `deepseek-v4-pro` 两款、全页无免费层；新账号仅送 **500 万 tokens、30 天有效** 的一次性体验额度；且 `deepseek-chat` / `deepseek-reasoner` 两个 id **已于 2026-07-24 下线**。→ 官方 DeepSeek 端点应**整体删除**。
2. **现有 22 个模型里，12 个必须删除**（含整个百度千帆端点、整个 DeepSeek 官方端点）；另 **1 个（`scnet/SCNet-Max`）不是删除而是"模型 id 不存在、必须改名"**——实测 `/models` 返回清单里没有它。删改完后剩 **10 个**可用（8 个长期免费 + 2 个 90 天限时免费）。
3. **真正的"长期免费"主力**其实很稳：智谱 `GLM-4.7-Flash` / `GLM-4-Flash` / `GLM-4V-Flash`、硅基流动 9B 及以下小模型、**腾讯混元 `hunyuan-lite`（唯一明确"永久免费"的大厂主力）**。
4. **推荐免费路线**：**智谱（对话主力）+ 腾讯混元 Lite（永久免费兜底）+ 魔搭 ModelScope（每天 2000 次）+ 硅基流动（小模型）**。全部国内可直连、有正规免费额度。SCNet 可作为"有额度/限时免费"的补充，但务必先换掉错误 id。
5. **用户最想要的"免费全尺寸 DeepSeek-V3 / R1"并不存在**：正规渠道里全尺寸 DeepSeek 一律付费，免费的只有 7B/8B 蒸馏版。若要接近 R1 效果，应改用深度思考类免费模型（智谱 GLM-4.7-Flash、SCNet 上的 DeepSeek-R1-0528 等）。

---

## 1.5 逐题答题卡（对应派单的 8 个问题）

| # | 问题 | 一句话结论 | 详见 |
|---|---|---|---|
| 1 | DeepSeek 官方有无免费额度？ | **无永久免费**；仅新账号一次性送 500 万 tokens/30 天。官方定价页无任何免费层 | §2 |
| 2 | 硅基流动免费 DeepSeek 系准确 id？ | 仅 `DeepSeek-R1-0528-Qwen3-8B`、`DeepSeek-R1-Distill-Qwen-7B` 免费；**`DeepSeek-V3`/`DeepSeek-R1` 是付费的，全尺寸没有免费版** | §3 |
| 3 | 智谱 Flash 是否仍永久免费？ | **是**。`glm-4.7-flash`、`glm-4-flash`、`glm-4v-flash`、`glm-4.6v-flash` 官方定价页均标"免费" | §4 |
| 4 | 千帆哪些永久免费？现有 7 个几个必须付费？ | 永久免费的是 `ernie-speed-8k`/`ernie-lite-8k`/`ernie-3.5-8k`（弱模型）；**现有 7 个全部只有 3 个月试用额度、之后必须付费 → 全删** | §4/§8.4 |
| 5 | `SCNet-Max` 是否仍免费可用？ | **该模型 id 根本不存在**（真实 id 见 §7），必须替换；且 SCNet 免费属"新用户赠送+限时"而非永久 | §4/§8.1 |
| 6 | 百炼 `qwen3.7/3.8-flash` 额度多少？长期吗？ | 各 **100 万 token、90 天有效（自开通起算）**，**不是长期**，90 天后转付费 | §4/§8.3 |
| 7 | 还有哪些长期免费、OpenAI 兼容、国内直连的平台？ | **腾讯混元 Lite（永久免费，最推荐）**、魔搭 ModelScope（2000 次/日）、火山方舟（每日 200 万 tokens）、讯飞星辰（限时） | §7 |
| 8 | 必须删除 / 建议保留 / 建议新增清单 | 见 §5 / §6 / §7 | — |

---

## 2. DeepSeek 免费额度核查

### 结论
| 项目 | 结论 |
|---|---|
| 是否有永久免费额度 | **没有**。API 为按量付费（充多少用多少） |
| 新用户赠送 | **有**：注册即送 **500 万 tokens（输入+输出合计）、30 天有效、无需绑卡/自动到账** |
| 官网/APP 对话 | 个人用户永久免费（但这是网页/APP，**不是 API**，不能用爬虫去蹭） |
| 现有两个模型 id 状态 | `deepseek-chat`、`deepseek-reasoner` **已于 2026-07-24 15:59 UTC 下线**，之后请求直接返回错误、无静默回退 |

### 依据来源
- **【官方一手，2026-09-30 直接抓取】DeepSeek API 官方《模型 & 价格》页**：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
  > 页面上只列了 `deepseek-flash`（DeepSeek-V4.1-Flash）与 `deepseek-v4-pro`（DeepSeek-V4-Pro-0813）两款，**全页无任何"免费额度/免费层"字样**；仅提"费用将从**充值余额或赠送余额**中扣减"。同时原文注明："旧模型名 `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` 仍可调用，但对应模型已下线"。→ **官方 API 无永久免费额度，且模型 id 换代极快。**
- 新用户赠送口径（多个第三方来源一致）：https://felloai.com/deepseek-pricing/ 、https://www.techradar.com/pro/deepseek-ai-review
  > "New API accounts receive 5M free tokens, valid for 30 days … no credit card"，与官方页"赠送余额"机制吻合。
- 模型退役佐证（`deepseek-chat`/`deepseek-reasoner` 于 2026-07-24 15:59 UTC 下线，之后直接报错、无回退）：https://deepseeksr1.com/platform/

### ⚠️ 存在矛盾的说法（如实标注）
有一家第三方站点称"DeepSeek 官方**没有**文档化的免费赠送额度"（https://seek-chat.com/guide/is-deepseek-ai-free ，查证于 2026-09-19）。综合 4 家以上来源，主流结论是"新账号确实有 500 万/30 天一次性赠送"。**无论如何，DeepSeek 都没有"长期免费"，且两个模型 id 已失效，因此对"只用免费模型"的目标而言，该端点都应删除。**

---

## 3. 硅基流动 SiliconFlow 免费模型清单（base_url `https://api.siliconflow.cn/v1`）

**免费政策**：模型广场中标注 **¥0** 的模型**永久免费**，但有固定速率限制（约 1000 RPM / 50K TPM）；**需实名认证**。新用户另有 14 元（≈2000 万 tokens）注册赠送（⚠️ 2025-11-30 后发放形式已改为"代金券"，金额口径见第 8 节）。
- **【2026-09-30 复核】多个独立站点一致确认：`deepseek-ai/DeepSeek-V3` 与 `deepseek-ai/DeepSeek-R1` 在硅基流动上是付费的**，免费集里只有 9B 及以下小模型：
  - itsfree.ai（2026 复核）：https://itsfree.ai/provider/siliconflow → 原文 "DeepSeek-R1 and DeepSeek-V3 on this host are **paid**. The standing free set is smaller open weights after KYC."，其列出的免费集仅 `Qwen/Qwen3-8B`、`Qwen/Qwen2.5-7B-Instruct`、`THUDM/glm-4-9b-chat` 三个。
  - free-model.com（2026）：https://free-model.com/providers/siliconflow → 列出 `DeepSeek-R1-0528-Qwen3-8B`、`DeepSeek-R1-Distill-Qwen-7B`、`THUDM/glm-4-9b-chat`、`THUDM/GLM-4.1V-9B-Thinking`、`deepseek-ai/DeepSeek-OCR` 等。
  - ⚠️ **两家第三方站点对"到底有几个免费模型"口径不一致（3 个 vs 6 个）**，官方定价页需登录才能看。**结论：免费的 DeepSeek 系只有 7B/8B 蒸馏小模型，没有免费的全尺寸 DeepSeek-V3 / DeepSeek-R1**（这正是用户最想要但拿不到的）。
- 依据：https://www.getmodelkey.com/guides/silicon-flow-models-and-pricing （"Models marked free are billed at zero within fixed rate limits … identity verification is required to use all free models"，查证 2026-08-21）
- 依据：https://github.com/peter123023/awesome-free-llm-api （"永久免费（指定模型 ¥0 列表），注册即用"，核实 2026-08-31）

| 模型 id | 是否免费 | 备注 |
|---|---|---|
| `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | ✅ 永久免费 | 推理蒸馏 8B（约 33K 上下文） |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | ✅ 永久免费 | R1 蒸馏 7B，带思维链 |
| `Qwen/Qwen3-8B` | ✅ 永久免费 | 通用对话，131K 上下文 |
| `THUDM/glm-4-9b-chat` | ✅ 永久免费 | GLM-4 9B 对话（⚠️ 部分新快照写作 `THUDM/GLM-4-9B-0414`，见第 8 节） |
| `deepseek-ai/DeepSeek-OCR` | ✅ 永久免费 | 视觉 OCR（可补作图片识别兜底） |
| `THUDM/GLM-4.1V-9B-Thinking` | ✅ 永久免费 | 视觉+文本推理（可补作图片识别兜底） |
| `Qwen/Qwen2.5-7B-Instruct`、`Qwen/Qwen2.5-Coder-7B-Instruct`、`THUDM/GLM-Z1-9B-0414`、`tencent/Hunyuan-MT-7B` 等 | ✅ 永久免费 | 9B 及以下小模型，可按需补充 |
| **`deepseek-ai/DeepSeek-V3`** | ❌ **付费** | 2 元/百万 输入、8 元/百万 输出 |
| **`deepseek-ai/DeepSeek-R1`** | ❌ **付费** | 4 元/百万 输入、16 元/百万 输出 |

- 付费/免费价格明细来源：https://www.huasheng.ai/insights/siliconflow-api-guide （2026-02-08 调研，含 V3/R1 付费价）

> **关于 function calling / 工具调用**：SiliconFlow 支持 OpenAI 兼容 function calling，官方称 `DeepSeek 系列、Qwen2.5 系列、GLM-4 系列` 等支持。来源：https://therouter.ai/blog/siliconflow-api-complete-guide/
> 但请注意：**本项目并非使用原生 function calling API**——`llm_client.py::_build_payload` 只传 `model/messages/temperature/stream`（联网时才加 `tools`），工具调用靠系统提示词里的 `<tool_call>{...}</tool_call>` **纯文本协议**。所以只要模型"指令遵循 + 能输出合法 JSON"即可，完全可行的候选比"官方支持 function calling 列表"更宽。

---

## 4. 现有配置逐项核查表

> 判定口径：**长期免费**=可保留；**限时/有免费额度**=可保留但需注明有效期；**必须删除**=无免费额度必须付费；**已失效**=模型 id 已下线/更名。

| 端点 | 模型 id | 判定 | 依据 URL | 处置建议 |
|---|---|---|---|---|
| zhipu | `glm-4-flash` | 长期免费 | https://blog.csdn.net/k0933/article/details/161116701 ；https://damodev.csdn.net/6ab0a05505257b085711f1ca.html | **保留**（可另补 `glm-4-flash-250414`） |
| zhipu | `glm-4.7-flash` | 长期免费 | https://www.zhipuai.cn/zh/news/148 （官方："供免费调用"，200K）；https://docs.bigmodel.cn/cn/guide/start/pricing （官方定价页标注 免费/免费） | **保留** |
| zhipu | `glm-4v-flash` | 长期免费（视觉） | https://docs.bigmodel.cn/cn/guide/start/pricing （官方定价页仍列 `GLM-4V-Flash 4K 免费 免费`，**未下线**） | **保留**（可另补 `glm-4.6v-flash`，128K 上下文，同为官方免费） |
| zhipu | `glm-4v` | **必须删除** | https://doc.zhizengzeng.com/doc-3979947 （GLM-4V 系列为付费；glm-4v-plus 4 元/百万） | 删除 |
| scnet | `SCNet-Max` | **❌ 模型 id 不存在（须替换）** | 真实用户 `curl $BASEURL/models` 返回的完整清单中**没有 `SCNet-Max`**：https://v2ex.com/t/1209689 ；第三方模型库实列 19 个真实 id：http://models.opencode.ai/providers/scnet-token-plan | **必须替换**为官方明列 id（`DeepSeek-V4-Flash` / `DeepSeek-R1-0528` / `Qwen3-30B-A3B` 等，见第 7 节） |
| baidu | `ernie-4.5-turbo-32k` | **必须删除** | https://cloud.baidu.com/doc/qianfan-docs/s/Jm8r1826a （输入 0.0008 元/千、输出 0.0032 元/千；免费仅 2–3 月批量限时优惠） | 删除 |
| baidu | `ernie-4.5-turbo-128k` | **必须删除** | 同上 | 删除 |
| baidu | `ernie-5.0` | **必须删除** | https://qianfan.cloud.baidu.com/ （ernie-5.0 输入 ¥0.006–0.01/千、输出 ¥0.024–0.04/千） | 删除 |
| baidu | `ernie-x1.1` | **必须删除** | https://qianfan.cloud.baidu.com/ （ernie-x1.1-preview 输入 ¥0.001/千、输出 ¥0.004/千） | 删除 |
| baidu | `deepseek-v4-flash` | **必须删除** | https://intl.cloud.baidu.com/en/doc/qianfan/s/Jm8r1826a-intl-en （千帆上 DeepSeek-V4-Flash 0.14/0.28 $/M，付费） | 删除 |
| baidu | `glm-5.2` | **必须删除** | https://intl.cloud.baidu.com/en/doc/qianfan/s/Jm8r1826a-intl-en （千帆上 GLM-5.2 1.4/4.4 $/M，付费） | 删除 |
| baidu | `qwen3.5-27b` | **必须删除** | 千帆定价页免费清单中无此模型；同类 qwen 均按量付费（https://cloud.baidu.com/doc/qianfan-docs/s/Jm8r1826a ） | 删除 |
| dashscope | `qwen3.7-flash` | 限时免费（100 万 token / 90 天） | https://docs.bailian.console.aliyun.com/zh/model-studio/model-pricing （免费额度 100 万 token）；https://developer.aliyun.com/article/1761458 | **保留**，界面注明"新用户 100 万 token / 90 天" |
| dashscope | `qwen3.8-flash` | 限时免费（100 万 token / 90 天） | https://docs.bailian.console.aliyun.com/zh/model-studio/model-pricing | **保留**，同上注明 |
| siliconflow | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 长期免费 | https://www.getmodelkey.com/guides/silicon-flow-models-and-pricing | **保留** |
| siliconflow | `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | 长期免费 | 同上；https://www.huasheng.ai/insights/siliconflow-api-guide | **保留** |
| siliconflow | `deepseek-ai/DeepSeek-V3` | **必须删除** | https://www.huasheng.ai/insights/siliconflow-api-guide （付费 2/8 元/百万） | 删除 |
| siliconflow | `deepseek-ai/DeepSeek-R1` | **必须删除** | 同上（付费 4/16 元/百万） | 删除 |
| siliconflow | `Qwen/Qwen3-8B` | 长期免费 | https://www.getmodelkey.com/guides/silicon-flow-models-and-pricing | **保留** |
| siliconflow | `THUDM/glm-4-9b-chat` | 长期免费 | https://github.com/Franming/awesome-free-llm-apis ；https://free-model.com/providers/siliconflow | **保留**（建议实测；如失效替换为 `THUDM/GLM-4-9B-0414`） |
| deepseek | `deepseek-chat` | **已失效（须删）** | https://deepseeksr1.com/platform/ （2026-07-24 下线） | 删除 |
| deepseek | `deepseek-reasoner` | **已失效（须删）** | 同上 | 删除 |

---

## 5. 必须删除清单（可直接交给工程师执行）

**A. 删除模型（12 个）**

```
zhipu        : glm-4v
siliconflow  : deepseek-ai/DeepSeek-V3 , deepseek-ai/DeepSeek-R1
baidu   (全删): ernie-4.5-turbo-32k , ernie-4.5-turbo-128k , ernie-5.0 ,
                ernie-x1.1 , deepseek-v4-flash , glm-5.2 , qwen3.5-27b
deepseek(全删): deepseek-chat , deepseek-reasoner
```
> 注：`scnet/SCNet-Max` 不在"删除"清单里，而是**必须改名**（id 不存在），见 §7-1。

**B. 删除端点（2 个，删完模型后为空，应整体移除）**
- `baidu`（百度千帆）——`free: False`，无免费模型
- `deepseek`（DeepSeek 官方）——`free: False`，且模型 id 已下线

**C. 同步清理引用（关键，漏了会报错或空转）**
`app/config.py`：
1. `PROVIDERS` 列表：删除 `baidu`、`deepseek` 两个 dict。
2. `PROVIDER_PRIORITY`：从 `["zhipu","scnet","siliconflow","dashscope","baidu","deepseek"]` 改为 `["zhipu","scnet","siliconflow","dashscope"]`。
3. `SEARCH_PRIORITY`：从 `["zhipu","dashscope","scnet","baidu"]` 改为 `["zhipu","dashscope","scnet"]`。
4. `VISION_PRIORITY`：`["zhipu","dashscope"]` 不变（未含被删端点）。
5. `NON_CHAT_MODELS`：`{"glm-4v-flash","glm-4v","qwen3.8-flash"}` 中 `glm-4v` 已不存在，建议改为 `{"glm-4v-flash","qwen3.8-flash"}`（也可保留旧字符串，无副作用）。
6. `_KEY_BLOBS` / `DEFAULT_API_KEYS`：`baidu`、`deepseek` 两条可一并删除（不再有对应端点）。

> 注：`MODEL_INDEX` / `MODEL_PROVIDERS` / `all_models_sorted()` / `chat_models_for_provider()` 等都是**从 PROVIDERS 动态生成**的，改完 PROVIDERS 即自动生效，无需手改。

---

## 6. 建议保留清单（10 个）

```
# 长期免费（8）
zhipu/glm-4-flash , zhipu/glm-4.7-flash , zhipu/glm-4v-flash(视觉)
siliconflow/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B
siliconflow/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B
siliconflow/Qwen/Qwen3-8B , siliconflow/THUDM/glm-4-9b-chat
scnet/<真实id>     # ⚠️ 原 SCNet-Max 须改为 DeepSeek-V4-Flash 等（§7-1）
# 限时免费（2，界面须注明 90 天有效期）
dashscope/qwen3.7-flash , dashscope/qwen3.8-flash(视觉)
```

---

## 7. 建议新增清单

> 优先补"国内可直连 + 有正规免费额度 + 指令遵循/工具调用可用"的端点，弥补删掉 baidu/deepseek 后的能力缺口。

| # | 模型 id / 端点 | 免费额度 | 工具调用 | 申请入口 | 依据 |
|---|---|---|---|---|---|
| 1 | **超算互联网 SCNet（端点已在配置内，但 id 必须换）**：真实可用 id —— `DeepSeek-V4-Flash`、`DeepSeek-V4-Pro`、`DeepSeek-R1-0528`、`DeepSeek-V3.2`、`Qwen3-30B-A3B`、`Qwen3.6-Flash`、`GLM-5.1`、`Kimi-K2.6`、`MiniMax-M2.5`、`QwQ-32B` 等 19 个 | 套餐内按 **$0/$0** 计费；免费路径＝新用户赠 1000 万 tokens + 限时免费体验（**非永久无条件免费，需实测**） | ✅ 原生 Tool Calling | 控制台 → 模型 API → 创建 API Key（服务类型选"模型"） | https://v2ex.com/t/1209689 ；http://models.opencode.ai/providers/scnet-token-plan ；https://www.scnet.cn/ac/openapi/doc/2.0/moduleapi/tools/openclaw.html |
| 2 | **魔搭 ModelScope**：`Qwen/Qwen3.5-27B`、`deepseek-ai/DeepSeek-V4-Flash-0731`、`GLM-5.1` 等；base_url `https://api-inference.modelscope.cn/v1` | **每天 2000 次**（单模型 ≤500/日，0 点重置，长期有效） | ✅ 支持 | modelscope.cn → Access Tokens（需阿里云实名/绑手机） | https://free-model.com/providers/modelscope |
| 3 | **火山方舟（字节豆包）**：`Doubao-Lite`（永久免费）+ 协作奖励计划每日额度；base_url `https://ark.cn-beijing.volces.com/api/v3` | **每日 200 万 tokens（按天重置）**；Doubao-Lite 永久免费（QPS 2） | ✅ 支持 | 火山引擎控制台 → 开通方舟 → 加入"协作奖励计划"（需手机号实名） | https://blog.51cto.com/16099278/14652039 |
| 4 | **硅基流动补充免费视觉**：`deepseek-ai/DeepSeek-OCR`、`THUDM/GLM-4.1V-9B-Thinking` | 永久免费（1000 RPM） | 文本对话为主 | cloud.siliconflow.cn（需实名） | https://www.huasheng.ai/insights/siliconflow-api-guide |
| 5 | **腾讯混元 Lite（推荐，唯一明确"永久免费"的大厂）**：`hunyuan-lite`；base_url `https://api.hunyuan.cloud.tencent.com/v1`（国际站 `https://tokenhub-intl.tencentcloudmaas.com/v1/chat/completions`） | **Hunyuan-Lite 永久免费**；首次开通另赠 100 万 token（1 年）+100 万 Embedding token | ✅ 完全兼容 OpenAI `/v1/chat/completions` | 腾讯云混元控制台 → 开通 → 生成 sk- Key | https://blog.51cto.com/u_16099244/14652067 ；https://cloud.tencent.com/developer/article/2708952 |
| 6 | **讯飞星辰 MaaS**：`xopqwen36v35b`（Qwen3.6-35B-A3B）、`xopqwen35v35b`（Qwen3.5-35B-A3B）；base_url `https://maas-api.cn-huabei-1.xf-yun.com/v2` | **限时免费、token 不限量**（后台显示 ∞，活动有截止期，**非永久**） | ✅ 兼容 OpenAI 格式 | maas.xfyun.cn 模型市集 → 选"限时免费"模型 → 创建应用 → 取 API Key | https://blog.csdn.net/qing_gee/article/details/161789221 ；https://iplaysoft.com/xunfei-coding-plan.html |

**建议**：新增端点时同步补进 `PROVIDERS`、`PROVIDER_PRIORITY`（免费端点在前）；若该端点支持联网还需按类型在 `search_style` 里加对应分支（见 `llm_client.py::_build_payload`）。

---

## 8. 不确定项与风险提示

### 未完全查证 / 存疑
1. **`scnet/SCNet-Max` —— 已确认「模型 id 不存在」，不是"存疑"了**：真实用户在 `https://api.scnet.cn/api/llm/v1/models` 拉到的完整清单为 `DeepSeek-R1-0528`、`DeepSeek-R1-Distill-Llama-70B`、`DeepSeek-R1-Distill-Qwen-32B`、`DeepSeek-R1-Distill-Qwen-7B`、`DeepSeek-V3.2`、`DeepSeek-V4-Flash`、`DeepSeek-V4-Pro`、`GLM-5.1`、`Kimi-K2.6`、`MiniMax-M2.5`、`OCR`、`Qwen3-235B-A22B`、`Qwen3-235B-A22B-Thinking-2507`、`Qwen3-30B-A3B`、`Qwen3-30B-A3B-Instruct-2507`、`Qwen3.6-Flash/Max/Plus`、`Qwen3-Embedding-8B`、`QwQ-32B`——**其中没有 `SCNet-Max`**（来源：https://v2ex.com/t/1209689 ）。第三方模型库亦仅收录上述 id（http://models.opencode.ai/providers/scnet-token-plan ）。→ **`SCNet-Max` 必须替换为真实 id**，否则该端点必然 404。
   > 另需注意：SCNet 的"SCNet Token Plan / Coding Plan"是**订阅套餐**（套餐内模型按 $0 计费），免费路径是"新用户赠 1000 万 tokens + 限时免费体验"，**并非对该账号永久无条件免费**。是否属于用户要求的"全免费"，建议工程师实测后确认（详见第 7 节）。
2. **`siliconflow/THUDM/glm-4-9b-chat`**：新旧快照并存——有的列表写 `THUDM/glm-4-9b-chat`，有的写 `THUDM/GLM-4-9B-0414`。建议以硅基流动控制台"模型广场"实测为准，两者任一可用即保留。
3. **百度千帆的免费口径**：`ERNIE-Speed` 系列在 2026 年由"有限免费"改为"完全免费不限量"，且第三方汇总口径不一。若要保留百度，落地前请以千帆控制台"模型广场"当日标注为准。
4. **未查证项**：① 硅基流动官方免费模型**准确数量**（官方定价页需登录，第三方站点给的是 3 个 / 6 个两种口径）；② 火山方舟"每日 200 万 tokens"仅有第三方汇总口径，未见官方原文；③ 腾讯混元 Lite 的**并发/QPS 上限**未查到官方数值。以上均已在正文标注来源，未编造。

### 政策变动风险（务必在报告/界面标注）
- **DeepSeek 官方**：5M/30 天赠送为一次性；模型 id 迭代极快（V3→V4→V4.1），`deepseek-chat`/`deepseek-reasoner` 2026-07-24 下线即是活例——任何硬编码模型 id 都可能随时失效。
- **限时额度 + 实名门槛**：智谱新用户 2000 万 token、阿里百炼 100 万/90 天、SCNet 赠送 tokens 都是**限时/一次性**，不能当长期供给；硅基流动、智谱、魔搭、火山方舟均需手机号/实名，落地前须确认账号状态。另有第三方称硅基流动"新用户送 ¥14"已改为代金券，旧数字可能已过时。
- **速率限制会打爆 Agent**：免费模型普遍有 RPM/并发/每日次数上限（硅基 1000 RPM、魔搭 500 次/模型/日、火山 QPS 2），本项目 Agent 会循环调工具，**极易触发 429**；需在 `llm_client.py` 做好限流重试与端点回退（注意本项目 `MODEL_FAILURE_POLICY="strict"`，回退需显式标注）。
