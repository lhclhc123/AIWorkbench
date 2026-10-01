# -*- coding: utf-8 -*-
"""LLM 调用层：OpenAI 兼容 /chat/completions 流式调用 + 多端点热备。"""
import json
import time
import requests

from . import config

TIMEOUT = 25  # 单次请求超时（秒）

# 这些 HTTP 状态码是「限流 / 网关瞬时错误」，可以退避重试；
# 与 401/403/404/额度用尽（硬错误，见 _is_hard_err）区分开。
_RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class LLMError(Exception):
    pass


def _is_hard_err(e):
    """判断是不是「认证/额度/模型已下线」这类硬错误——本会话内不要再重试该模型。

    与瞬时错误（429 限流、网络抖动、超时）区分开：瞬时错误应该重试。
    """
    msg = str(e)
    return ("HTTP 401" in msg or "HTTP 403" in msg or "HTTP 404" in msg
            or "invalid_model" in msg or "Free quota" in msg
            or "AllocationQuota" in msg or "额度" in msg)


def _default_web_gate():
    """网页通道的默认闸门（只读就绪态，**绝不主动拉起浏览器**）。

    v9.14.0：任何未显式注入 web_gate 的 LLMClient，在 auto 模式下也用它来判定
    "网页版是否已连接"；服务不可用一律视为未就绪，从而静默跳过网页 provider，
    避免去撞一个没启动的本机服务。
    """
    def _gate():
        try:
            from .deepseek_web import service as _svc
            return bool(_svc.instance().auto_ready())
        except Exception:
            return False
    return _gate


def _effective_key(provider, key):
    """挑出发请求实际要用的 key。

    `no_key` 白名单端点（DeepSeek 网页版本机服务）：即使调用方把 key 传成**空串**
    （例如用户在设置页清空了 Key 输入框并保存），也回落到 `config.WEB_PLACEHOLDER_KEY`，
    **绝不发出空 Bearer** —— 本机服务要求 Bearer 非空，否则会 401。

    注意：这是**客户端**兜底；服务端的"回环绑定 + Host 校验 + 要求 Bearer"强度保持不变。
    占位串全项目只在 `config.WEB_PLACEHOLDER_KEY` 定义一处。
    """
    if key:
        return key
    if provider.get("no_key"):
        return config.WEB_PLACEHOLDER_KEY
    return key


class LLMClient:
    def __init__(self):
        self.keys = dict(config.DEFAULT_API_KEYS)  # provider -> key
        self.session = requests.Session()
        # 本次实际命中的端点/模型（用于界面显示"到底用的哪个模型"）
        self.last_provider = None
        self.last_model = None
        # 用户选了某个模型、却没跑成，被系统换掉时的说明（界面必须提示）
        self.notice = ""
        self.asked_label = ""
        self.switched = False
        # 模型失败策略，由界面按用户设置注入（strict / fallback）
        self.policy = None
        # 本会话内已知「硬报错」的 (端点名, 模型id)：认证/额度用尽/已下线。
        # auto 模式会跳过它们，避免每次都白撞一次失败请求（如某模型免费额度耗尽 403）。
        self._bad = set()
        # 网页通道闸门（v9.14.0）：由界面注入 callable -> bool。
        # None = 不启用闸门；返回 False 表示"网页版未就绪"，auto 模式静默跳过它
        # （绝不因为 auto 模式就主动拉起浏览器）。
        self.web_gate = None

    def set_web_gate(self, fn):
        """注入"网页通道是否就绪"的判断函数（供 chat_auto 使用）。"""
        self.web_gate = fn

    def set_keys(self, keys: dict):
        if keys:
            self.keys.update(keys)

    def _mark_used(self, provider, model):
        self.last_provider = provider.get("name")
        self.last_model = model

    def last_used_label(self):
        """返回实际使用的模型，如 'glm-4-flash · 智谱 Zhipu'。"""
        if not self.last_model:
            return ""
        p = config.provider_by_name(self.last_provider) if self.last_provider else None
        label = p["label"] if p else (self.last_provider or "")
        return f"{self.last_model} · {label}" if label else self.last_model

    def reset_used(self):
        self.last_provider = None
        self.last_model = None
        self.notice = ""
        self.asked_label = ""
        self.switched = False

    # ---------- 请求体构造 ----------
    def _build_payload(self, provider, model, messages, enable_search, stream):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "stream": stream,
        }
        if enable_search:
            style = provider.get("search_style")
            if style == "zhipu":
                # 智谱联网：工具模式，纯文本，不加 response_format
                payload["tools"] = [{
                    "type": "web_search",
                    "web_search": {"enable": True, "search_result": True},
                }]
            elif style == "scnet":
                payload["enable_search"] = True
            elif style in (None, "", "none"):
                pass  # 该端点没有联网搜索能力（如硅基流动）
            else:  # dashscope
                payload["enable_search"] = True
                payload["search_options"] = {
                    # ⚠️ forced_search 必须是 False！
                    # 曾经是 True —— 那等于**每一轮请求都强制联网搜索**，
                    # 于是做本机诊断/写代码这类不需要外部信息的任务时，
                    # 每轮都被塞进一堆无关搜索结果（实测：本机钉钉诊断那轮，
                    # 模型十几轮都在忽略「Python 教程 / WPS / Planner」之类的噪音）。
                    # 改成 False = 让模型自己决定要不要搜；它想搜就自己调 web_search 工具。
                    "forced_search": False,
                    "search_strategy": "standard",
                    "enable_source": True,
                }
        return payload

    # ---------- 单次流式调用 ----------
    def _backoff(self, attempt, resp=None):
        """退避秒数：优先用服务端 Retry-After，否则指数 2/4/8s，上限 10s。"""
        if resp is not None:
            ra = (resp.headers or {}).get("Retry-After")
            if ra:
                try:
                    return min(float(ra), 10.0)
                except Exception:
                    pass
        return min(2.0 * (2 ** attempt), 10.0)

    def _stream_once(self, provider, model, messages, enable_search, on_token, timeout,
                     on_reasoning=None, _max_attempts=3):
        """对单个 (provider, model) 发起流式请求，逐 token 回调 on_token。
        成功返回完整文本；失败抛出异常。

        on_reasoning：**可选**回调（默认 None = 完全不影响现有行为）。
        部分模型（阿里百炼上的 DeepSeek-V4.1-Flash / DeepSeek-V4-Pro / Qwen3.x）
        会先流式吐思维链 delta.reasoning_content，再吐正文 delta.content。
        这里把思维链单独回调出去，界面可以渲染成"深度思考"块。
        """
        key = self.keys.get(provider["name"], "")
        # 满足 00-context 3.4 硬约束 1：带 no_key 标记的端点（DeepSeek 网页版本机服务）
        # 不需要真密钥，即使 Key 被清空也照常工作。
        if not key and not provider.get("no_key"):
            raise LLMError(f"[{provider['label']}] 缺少 API Key")
        # no_key 端点若 key 为空，回落到统一占位串，避免发出空 Bearer 触发 401（见 04-qa §6.1）
        key = _effective_key(provider, key)
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        payload = self._build_payload(provider, model, messages, enable_search, stream=True)
        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        }
        last_err = None
        for attempt in range(_max_attempts):
            try:
                resp = self.session.post(url, json=payload, headers=headers,
                                         stream=True, timeout=(10, timeout))
            except (requests.Timeout, requests.ConnectionError) as e:
                # 连接层瞬时错误：退避后重试（尚未发出任何 token，安全）
                last_err = e
                if attempt < _max_attempts - 1:
                    time.sleep(self._backoff(attempt))
                    continue
                raise

            if resp.status_code != 200:
                text_body = (resp.text or "")[:500]
                msg = f"HTTP {resp.status_code} {text_body[:200]}"
                try:
                    resp.close()
                except Exception:
                    pass
                # 429 限流 / 5xx 网关瞬时错误：退避后重试，仍用同一个模型
                # （严格模式语义不变——没有偷偷换模型，只是对本模型多等几秒）
                if resp.status_code in _RETRYABLE_HTTP and attempt < _max_attempts - 1:
                    last_err = LLMError(msg)
                    time.sleep(self._backoff(attempt, resp))
                    continue
                raise LLMError(msg)

            # === 以下仅在「请求成功（200）」后执行，不在重试范围 ===
            # 记录本次实际命中的端点与模型
            self._mark_used(provider, model)

            buf = b""
            full = []
            got_sse = False
            for raw in resp.iter_lines(decode_unicode=False):
                if raw is None:
                    continue
                if isinstance(raw, str):
                    raw = raw.encode("utf-8")
                buf += raw + b"\n"
                # 按行解析 SSE
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    got_sse = True
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        return "".join(full)
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    try:
                        dl = obj["choices"][0]["delta"] or {}
                    except Exception:
                        dl = {}
                    # 思维链（DeepSeek-V4.x / Qwen3.x 等）：单独回调，不算正文
                    rc = dl.get("reasoning_content") or ""
                    if rc and on_reasoning:
                        on_reasoning(rc)
                    delta = dl.get("content") or ""
                    if delta:
                        full.append(delta)
                        if on_token:
                            on_token(delta)
            if not got_sse:
                # 服务端未返回 SSE，尝试把整段当 JSON 解析（非流式兜底）
                text = buf.decode("utf-8", "ignore")
                try:
                    obj = json.loads(text)
                    msg = obj["choices"][0]["message"]
                    if on_reasoning and msg.get("reasoning_content"):
                        on_reasoning(msg["reasoning_content"])
                    content = msg["content"]
                    if on_token:
                        on_token(content)
                    return content
                except Exception:
                    raise LLMError("响应既不是 SSE 也不是合法 JSON")
            return "".join(full)

    # ---------- 对外接口 ----------
    def stream_specific(self, provider_name, model, messages, enable_search=False,
                        on_token=None, timeout=TIMEOUT, on_reasoning=None):
        provider = config.provider_by_name(provider_name)
        if not provider:
            raise LLMError(f"未知端点 {provider_name}")
        return self._stream_once(provider, model, messages, enable_search, on_token, timeout,
                                 on_reasoning)

    def chat_auto(self, messages, enable_search=False, on_token=None, timeout=TIMEOUT,
                  on_reasoning=None):
        """自动模式：按 PROVIDER_PRIORITY 顺序逐个端点、逐个模型尝试，免费优先。

        本会话内会记住「硬报错」的 (端点,模型)（401/403/404：额度用尽/已下线/无权限），
        后续不再白撞，直接跳到下一个还活着的模型。
        """
        tried = []
        plan = []
        for pname in config.PROVIDER_PRIORITY:
            p = config.provider_by_name(pname)
            if not p:
                continue
            if not self.keys.get(p["name"]) and not p.get("no_key"):
                continue          # 没配 Key 的端点直接跳过，不要浪费一次"缺少 Key"的错误
            # v9.14.0：网页通道未就绪则静默跳过 —— 绝不因为 auto 模式就去拉起浏览器。
            # web_gate 未显式注入时用默认闸门（查单例 service 的就绪态；不可用即未就绪）。
            if p.get("web"):
                gate = self.web_gate or _default_web_gate()
                try:
                    if not gate():
                        continue
                except Exception:
                    continue
            for m in config.chat_models_for_provider(p):
                if (p["name"], m["id"]) in self._bad:
                    continue      # 本会话已知坏模型，跳过
                plan.append((p, m["id"]))
        last_err = None
        for provider, model in plan:
            tried.append(f"{provider['label']}/{model}")
            try:
                text = self._stream_once(provider, model, messages, enable_search, on_token, timeout,
                                         on_reasoning)
                if text:
                    return text
            except Exception as e:
                last_err = e
                # 硬错误（认证/额度/已下线）→ 本会话记住，别再撞
                if _is_hard_err(e):
                    self._bad.add((provider["name"], model))
                continue
        raise LLMError("全部端点失败。尝试过：" + ("、".join(tried) or "（没有任何配置了 API Key 的端点）") +
                       (f"；最后错误：{last_err}" if last_err else ""))

    def chat(self, messages, model_sel="auto", enable_search=False, on_token=None,
             timeout=TIMEOUT, policy=None, on_reasoning=None):
        """统一入口。

        关键行为（修掉"选了一个模型却调用了另一个"的老毛病）：
        - 用户明确选了模型 -> 默认只认这一个，失败就原样报错，**绝不静默换模型**；
          只有在设置里把 MODEL_FAILURE_POLICY 设成 "fallback" 时才允许回退，
          而且回退后 self.notice 会写明"已从 X 切到 Y"，界面必须显示出来。
        """
        policy = policy or self.policy or config.MODEL_FAILURE_POLICY
        if not model_sel or model_sel == "auto":
            return self.chat_auto(messages, enable_search, on_token, timeout, on_reasoning)

        choices = config.resolve_model_choice(model_sel)
        if not choices:
            # 明确选了、但系统认不出来 —— 也不能偷偷换成别的模型，如实报错。
            raise LLMError(
                f"无法识别你选择的模型「{model_sel}」（可能是版本升级后模型下架了）。\n"
                f"请到「设置 → 模型」重新选一个，或选「[自动] 免费优先」。")

        self.asked_label = " / ".join(
            config.model_label(m, p["name"]) for p, m in choices)
        tried, last_err = [], None
        for provider, model in choices:
            tried.append(f"{provider['label']}/{model}")
            try:
                return self._stream_once(provider, model, messages, enable_search,
                                         on_token, timeout, on_reasoning)
            except Exception as e:
                last_err = e
                continue

        # 走不通了
        detail = f"你选择的模型「{self.asked_label}」调用失败：{last_err}"
        if policy == "fallback":
            self.notice = (f"⚠ 你选择的模型不可用（{last_err}），已自动改用其它模型继续。"
                           f"如需严格只用指定模型，请在「设置 → 模型」里把"
                           f"「指定模型失败时」改为「严格模式」。")
            text = self.chat_auto(messages, enable_search, on_token, timeout, on_reasoning)
            self.switched = True
            self.notice = (f"⚠ 你选择的「{self.asked_label}」不可用（{last_err}），"
                           f"本次实际使用了「{self.last_used_label()}」。")
            return text
        raise LLMError(
            f"{detail}\n"
            f"（严格模式：系统不会偷偷换成别的模型。）\n"
            f"可以这样做：① 在顶部模型下拉框换一个模型；"
            f"② 选「[自动] 免费优先」让它自己挑；"
            f"③ 到「设置 → 模型」点「测试连接」看哪个端点还可用。"
            + (f"\n尝试记录：{'、'.join(tried)}" if tried else ""))

    # ---------- 端点体检 ----------
    def probe_provider(self, provider_name, timeout=15):
        """测试某个端点是否可用，顺带列出这个 Key 真正能用的模型。

        返回 (ok, message, models)
        """
        p = config.provider_by_name(provider_name)
        if not p:
            return False, f"未知端点 {provider_name}", []
        key = self.keys.get(p["name"], "")
        if not key and not p.get("no_key"):
            return False, "没有配置 API Key", []
        # no_key 端点 key 为空时回落到统一占位串（见 04-qa §6.1，避免空 Bearer -> 401）
        key = _effective_key(p, key)
        url = p["base_url"].rstrip("/") + "/models"
        try:
            r = self.session.get(url, headers={"Authorization": "Bearer " + key},
                                 timeout=timeout)
        except Exception as e:
            return False, f"网络不可达：{e}", []
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} {r.text[:120]}", []
        try:
            data = r.json().get("data") or []
            models = [m.get("id") for m in data if isinstance(m, dict)]
        except Exception:
            models = []
        if not models:
            return True, "连接正常（未返回模型列表）", []
        return True, f"连接正常，可用模型 {len(models)} 个", models

    def probe_all(self, timeout=8):
        """逐个端点体检，返回 [(provider_label, ok, message)]。"""
        out = []
        for p in config.PROVIDERS:
            if not self.keys.get(p["name"]) and not p.get("no_key"):
                out.append((p["label"], None, "未配置 Key"))
                continue
            ok, msg, _ = self.probe_provider(p["name"], timeout)
            out.append((p["label"], ok, msg))
        return out

    # ---------- 联网搜索（工具化）----------
    def web_search(self, query, timeout=60):
        """把"联网搜索"做成一个可以单独调用的能力。

        只走真正支持联网的端点（见 config.SEARCH_PRIORITY），
        并且明确要求模型基于搜索结果作答、附上来源链接，避免它拿旧知识硬编。
        """
        msgs = [
            {"role": "system", "content":
                "你是联网搜索助手。请基于**实时检索到的搜索结果**回答，"
                "不要依赖你自己的记忆。要求：\n"
                "1) 直接给出结论，条理清晰，中文；\n"
                "2) 关键事实后用括号标注来源（网站名或链接）；\n"
                "3) 若搜索结果之间冲突，指出冲突；\n"
                "4) 若确实搜不到，如实说「没有检索到相关结果」，绝不编造。\n"
                "5) 末尾用「来源」小节列出你看过的链接。"},
            {"role": "user", "content": query},
        ]
        tried, last_err = [], None
        for pname in config.SEARCH_PRIORITY:
            p = config.provider_by_name(pname)
            if not p or not self.keys.get(p["name"]):
                continue
            for m in sorted(p["models"], key=lambda x: 0 if x["free"] else 1):
                if m.get("vision"):
                    continue
                tried.append(f"{p['label']}/{m['id']}")
                try:
                    text = self._stream_once(p, m["id"], msgs, True, None, timeout)
                    if text and text.strip():
                        return text.strip()
                except Exception as e:
                    last_err = e
                    continue
        raise LLMError("联网搜索失败（没有可用的联网端点）"
                       + ("，尝试过：" + "、".join(tried) if tried else
                          "；请检查是否配置了智谱 / 百炼 / 超算 的 API Key")
                       + (f"；最后错误：{last_err}" if last_err else ""))

    # ---------- 视觉识别（图片 OCR / 理解）----------
    def _vision_once(self, provider, model, messages, timeout):
        key = self.keys.get(provider["name"], "")
        if not key:
            raise LLMError(f"[{provider['label']}] 缺少 API Key")
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        payload = {"model": model, "messages": messages,
                   "temperature": 0.1, "stream": False}
        headers = {"Authorization": "Bearer " + key,
                   "Content-Type": "application/json"}
        resp = self.session.post(url, json=payload, headers=headers,
                                 timeout=(10, timeout))
        if resp.status_code != 200:
            raise LLMError(f"HTTP {resp.status_code} {resp.text[:160]}")
        obj = resp.json()
        try:
            return obj["choices"][0]["message"]["content"]
        except Exception:
            raise LLMError("视觉模型返回格式异常")

    def vision_extract(self, image_path, prompt, timeout=90):
        """让多模态模型识别本地图片（OCR / 描述）。按 VISION_PRIORITY 依次尝试。"""
        import base64
        import mimetypes
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        data_url = f"data:{mime};base64,{b64}"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}]
        tried = []
        last_err = None
        for provider, model in config.vision_models():
            tried.append(f"{provider['label']}/{model}")
            try:
                text = self._vision_once(provider, model, messages, timeout)
                if text:
                    self._mark_used(provider, model)
                    return text
            except Exception as e:
                last_err = e
                continue
        raise LLMError("视觉识别失败（没有可用的视觉模型）"
                       + ("，尝试过：" + "、".join(tried) if tried else "")
                       + (f"；最后错误：{last_err}" if last_err else ""))
