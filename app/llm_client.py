# -*- coding: utf-8 -*-
"""LLM 调用层：OpenAI 兼容 /chat/completions 流式调用 + 多端点热备。"""
import json
import time
import requests

from . import config

TIMEOUT = 25  # 单次请求超时（秒）


class LLMError(Exception):
    pass


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
            elif style == "baidu":
                payload["enable_web_search"] = True
            elif style in (None, "", "none"):
                pass  # 该端点没有联网搜索能力（如 DeepSeek 官方 / 硅基流动）
            else:  # dashscope
                payload["enable_search"] = True
                payload["search_options"] = {
                    "forced_search": True,
                    "search_strategy": "pro",
                    "enable_source": True,
                }
        return payload

    # ---------- 单次流式调用 ----------
    def _stream_once(self, provider, model, messages, enable_search, on_token, timeout):
        """对单个 (provider, model) 发起流式请求，逐 token 回调 on_token。
        成功返回完整文本；失败抛出异常。"""
        key = self.keys.get(provider["name"], "")
        if not key:
            raise LLMError(f"[{provider['label']}] 缺少 API Key")
        url = provider["base_url"].rstrip("/") + "/chat/completions"
        payload = self._build_payload(provider, model, messages, enable_search, stream=True)
        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        }
        resp = self.session.post(url, json=payload, headers=headers,
                                 stream=True, timeout=(10, timeout))
        if resp.status_code != 200:
            body = resp.read(500) if hasattr(resp, "read") else b""
            raise LLMError(f"HTTP {resp.status_code} {body.decode('utf-8', 'ignore')[:200]}")

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
                    delta = obj["choices"][0]["delta"].get("content") or ""
                except Exception:
                    delta = ""
                if delta:
                    full.append(delta)
                    if on_token:
                        on_token(delta)
        if not got_sse:
            # 服务端未返回 SSE，尝试把整段当 JSON 解析（非流式兜底）
            text = buf.decode("utf-8", "ignore")
            try:
                obj = json.loads(text)
                content = obj["choices"][0]["message"]["content"]
                if on_token:
                    on_token(content)
                return content
            except Exception:
                raise LLMError("响应既不是 SSE 也不是合法 JSON")
        return "".join(full)

    # ---------- 对外接口 ----------
    def stream_specific(self, provider_name, model, messages, enable_search=False,
                        on_token=None, timeout=TIMEOUT):
        provider = config.provider_by_name(provider_name)
        if not provider:
            raise LLMError(f"未知端点 {provider_name}")
        return self._stream_once(provider, model, messages, enable_search, on_token, timeout)

    def chat_auto(self, messages, enable_search=False, on_token=None, timeout=TIMEOUT):
        """自动模式：按 PROVIDER_PRIORITY 顺序逐个端点、逐个模型尝试，免费优先。"""
        tried = []
        plan = []
        for pname in config.PROVIDER_PRIORITY:
            p = config.provider_by_name(pname)
            if not p:
                continue
            if not self.keys.get(p["name"]):
                continue          # 没配 Key 的端点直接跳过，不要浪费一次"缺少 Key"的错误
            for m in config.chat_models_for_provider(p):
                plan.append((p, m["id"]))
        last_err = None
        for provider, model in plan:
            tried.append(f"{provider['label']}/{model}")
            try:
                text = self._stream_once(provider, model, messages, enable_search, on_token, timeout)
                if text:
                    return text
            except Exception as e:
                last_err = e
                continue
        raise LLMError("全部端点失败。尝试过：" + ("、".join(tried) or "（没有任何配置了 API Key 的端点）") +
                       (f"；最后错误：{last_err}" if last_err else ""))

    def chat(self, messages, model_sel="auto", enable_search=False, on_token=None,
             timeout=TIMEOUT, policy=None):
        """统一入口。

        关键行为（修掉"选了一个模型却调用了另一个"的老毛病）：
        - 用户明确选了模型 -> 默认只认这一个，失败就原样报错，**绝不静默换模型**；
          只有在设置里把 MODEL_FAILURE_POLICY 设成 "fallback" 时才允许回退，
          而且回退后 self.notice 会写明"已从 X 切到 Y"，界面必须显示出来。
        """
        policy = policy or self.policy or config.MODEL_FAILURE_POLICY
        if not model_sel or model_sel == "auto":
            return self.chat_auto(messages, enable_search, on_token, timeout)

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
                                         on_token, timeout)
            except Exception as e:
                last_err = e
                continue

        # 走不通了
        detail = f"你选择的模型「{self.asked_label}」调用失败：{last_err}"
        if policy == "fallback":
            self.notice = (f"⚠ 你选择的模型不可用（{last_err}），已自动改用其它模型继续。"
                           f"如需严格只用指定模型，请在「设置 → 模型」里把"
                           f"「指定模型失败时」改为「严格模式」。")
            text = self.chat_auto(messages, enable_search, on_token, timeout)
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
        if not key:
            return False, "没有配置 API Key", []
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
            if not self.keys.get(p["name"]):
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
