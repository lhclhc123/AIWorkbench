# -*- coding: utf-8 -*-
"""messages[] 与"一段提示词"之间的纯函数转换。

为什么单独成模块（见 02-arch.md 1.2）：
- `messages -> 单段提示词`、`前缀增量判定`、`超长裁剪` 都是**纯函数**，
  不掺网络也不碰 DOM，可以离线单测（QA 友好）。
- DeepSeek 网页版没有 API 参数，只能"一段提示词进、一段原文出"，
  所以必须在客户端把 OpenAI 的 messages[] 摊平。
"""
# 每个角色在摊平提示词里的中文标签。
ROLE_TAG = {
    "system": "系统",
    "user": "用户",
    "assistant": "助手",
    "tool": "工具",
}

# 每条消息在摊平后额外的固定开销（角色标签 + 换行），用于保守估算。
_PER_MESSAGE_OVERHEAD = 8


def _content_of(msg):
    """取出消息的纯文本内容，兼容 content 为字符串 / 多段列表两种形态。"""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _role_of(msg):
    if isinstance(msg, dict):
        return str(msg.get("role", "user") or "user")
    return "user"


def _signature(msg):
    """消息的"身份指纹"：角色 + 内容，用于前缀比较。"""
    return (_role_of(msg), _content_of(msg))


def flatten(messages):
    """把 messages[] 摊平成一段给网页的提示词。

    单条格式：
        【角色】正文
    多条之间空一行。tool 角色（工具结果）也带上，让网页能"看到"真实结果。
    """
    if not messages:
        return ""
    chunks = []
    for msg in messages:
        tag = ROLE_TAG.get(_role_of(msg), _role_of(msg))
        body = _content_of(msg).strip()
        chunks.append(f"【{tag}】{body}" if body else f"【{tag}】")
    return "\n\n".join(chunks)


def estimate_chars(messages):
    """保守估算这批消息摊平后的字符数（含角色标签开销）。"""
    total = 0
    for msg in messages or []:
        total += len(_content_of(msg))
        total += len(ROLE_TAG.get(_role_of(msg), ""))
        total += _PER_MESSAGE_OVERHEAD
    return total


def diff_tail(prev, cur):
    """计算"网页还没见过的那一段"，用于多轮前缀增量下发。

    - prev: 我们认为网页**已经拥有**的消息列表（当前网页会话状态）。
    - cur:  本次请求要发送的完整消息列表。

    Returns:
        str  —— 当 prev 是 cur 的**严格前缀**时，返回新增尾段摊平后的文本；
        None —— 前缀不匹配（换对话 / 历史被压缩 / 编辑重发），
                调用方应据此"新建网页对话"并全量发送。
    """
    if prev is None:
        prev = []
    n = len(prev)
    if len(cur) <= n:
        return None
    for i in range(n):
        if _signature(prev[i]) != _signature(cur[i]):
            return None
    return flatten(cur[n:])


def trim_recent(messages, keep_turns=6):
    """超长时裁剪：保留全部 system 消息 + 最近 keep_turns 轮（2*keep_turns 条）。

    注意：裁剪**必须**伴随对用户的可见提示（由服务层/界面负责），
    这里只做纯计算，绝不静默丢历史。
    """
    if not messages:
        return []
    systems = [m for m in messages if _role_of(m) == "system"]
    others = [m for m in messages if _role_of(m) != "system"]
    keep = max(1, int(keep_turns)) * 2
    tail = others[-keep:] if len(others) > keep else others
    return systems + tail
