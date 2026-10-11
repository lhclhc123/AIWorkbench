# -*- coding: utf-8 -*-
"""messages[] 与"一段提示词"之间的纯函数转换。

v9.18.0 新增 `conversation_key()` / `loose_key()`：
把"这批 messages 属于哪个 AI 工作台对话"算成一个**跨轮次稳定**的指纹，
让网页侧可以做到「一个工作台对话 == 一个 DeepSeek 网页会话」。

为什么单独成模块（见 02-arch.md 1.2）：
- `messages -> 单段提示词`、`前缀增量判定`、`超长裁剪` 都是**纯函数**，
  不掺网络也不碰 DOM，可以离线单测（QA 友好）。
- DeepSeek 网页版没有 API 参数，只能"一段提示词进、一段原文出"，
  所以必须在客户端把 OpenAI 的 messages[] 摊平。
"""
import hashlib

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
    role = _role_of(msg)
    if role == "system":
        # ★ v9.17.0：system 每轮都在变 —— 我们拼的提示词里带了长期记忆 / 技能清单 /
        #   定时任务 / 计划 / 时间，内容一轮一个样。如果拿它当"指纹"，那么
        #   `diff_tail` 的"严格前缀"判定**每轮都不命中** → 每轮都新建一个网页对话
        #   （正是用户反馈的："同一段对话，能不能不要在 DeepSeek 里换新对话？"）。
        #   网页端本来就靠它自己的会话记忆保住上文，system 只在第一次随全量发过去，
        #   所以这里**只认角色、不认内容**。
        return (role, "")
    return (role, _content_of(msg))


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


# ---------------------------------------------------------------------------
# v9.18.0：对话指纹（"这批 messages 属于哪个工作台对话"）
#
# 为什么需要它：v9.18.0 之前，网页侧**只有一个**"已发送消息"基线，判据是
# "本次 messages 是不是它的严格前缀"。于是任何**不是主对话**的请求
# （记忆提炼 / 对话自动命名 / 后台链路）都会让前缀失配 -> 新建一个 DeepSeek
# 网页会话；等主对话下一轮回来，它又不匹配那个辅助基线 -> **再新建一个**。
# 一来一回，用户只有一条对话，DeepSeek 那边却被建出十几二十条新会话。
#
# 指纹必须**跨轮次稳定**，所以刻意排除这些会变的东西：
#   ✗ system 全文（长期记忆 / 技能清单 / 定时任务 / 时间，每轮都变）
#   ✗ 消息条数、时间戳、工具清单、最近几轮的内容
# 只取"第一条真实用户消息"——它在整个对话生命周期里是不变的。
# ---------------------------------------------------------------------------
# 「宽松指纹」的前缀长度：给**模板型**的旁路调用（记忆提炼 / 自动命名）用。
# 它们的首条 user 是固定模板 + 每轮不同的正文，取前 N 字即可稳定命中同一个桶。
LOOSE_PREFIX_CHARS = 200


def _first_user_text(messages):
    """第一条**非 system**消息的正文（整段对话里最稳定的那一句）。"""
    for msg in messages or []:
        if _role_of(msg) != "system":
            return _content_of(msg)
    return ""


def _anchor_text(messages):
    """指纹锚点文本：首条真实用户消息；没有就退回全量摊平（保证不撞空串）。"""
    text = (_first_user_text(messages) or "").strip()
    if text:
        return text
    return (flatten(messages) or "").strip()


def _digest(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def conversation_key(messages):
    """精确对话指纹（完整首条用户消息）。用于主对话，避免两个对话撞车。"""
    return _digest(_anchor_text(messages))


def loose_key(messages):
    """宽松对话指纹（首条用户消息的前 LOOSE_PREFIX_CHARS 字）。

    只给"模板型旁路调用"兜底：它们的正文每轮换，但模板前缀固定，
    靠这个前缀就能一直复用同一个网页会话，而不是每轮新建。
    """
    return _digest(_anchor_text(messages)[:LOOSE_PREFIX_CHARS])


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
