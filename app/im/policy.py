# -*- coding: utf-8 -*-
"""IM 助手「要不要回、怎么回」的判定与人设（v9.20.0）。

三件事：
1. `should_reply()` —— 这条消息该不该接？三种模式规则不同，且必须挡住
   "自己回自己"（否则群里会自己跟自己聊起来）。
2. `build_system()` —— 拼系统提示。核心是**说人话**：不客服腔、不列表化、
   不做"作为 AI 助手我很乐意"这种腔调。
3. `build_messages()` —— 把消息流水折成模型能吃的上下文（共享记忆 + 本会话记忆
   + 最近 N 轮），并保证角色交替合法。
"""
import re
import time

from .store import MODES, DEFAULT_MODE

# 「叫我」的默认称呼（会话档案里没写时用）
DEFAULT_ALIASES = ("小助手", "助手", "AI", "ai")

# 秘书/群助手模式下，群里即使没 @ 也当成指令的开头词
_COMMAND_HEADS = (
    "帮我", "帮忙", "给我", "记录一下", "记一下", "记住", "查一下", "查查",
    "提醒", "安排", "统计", "汇总", "整理", "发一下", "发个", "生成", "写个",
    "写一份", "做个", "做一份", "算一下", "翻译", "总结",
)

# 明显是"在跟人说话、不是在跟 AI 说话"的口语（避免瞎插话）
_CHITCHAT_ONLY = ("哈哈", "嗯", "哦", "好的", "收到", "谢谢")

_EMAIL_AT = re.compile(r"@[\w\-.]+")


def clean_text(text, me_name="", aliases=DEFAULT_ALIASES):
    """去掉 @ 前缀与多余空白（@ 是"叫我"，不是正文）。"""
    t = str(text or "")
    t = t.replace("\u2005", " ").replace("\u00a0", " ")
    names = [n for n in ([me_name] + list(aliases)) if n]
    for n in names:
        t = t.replace("@" + n, " ")
    t = _EMAIL_AT.sub(" ", t) if "@" in t and not names else t
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_called(text, me_name="", aliases=DEFAULT_ALIASES):
    """有没有在叫我们（@ 或点名）。"""
    t = str(text or "")
    names = [n for n in ([me_name] + list(aliases)) if n]
    for n in names:
        if n and ("@" + n) in t:
            return True
        if n and n in t:
            return True
    return False


def should_reply(msg, thread, me_name="", aliases=DEFAULT_ALIASES, settings=None):
    """这条消息要不要接。

    `settings` 是界面「自动回复设置」里的那套开关（见 `IM_DEFAULT_SETTINGS`）。
    **传 None 时完全走老逻辑**（老探针仍在用三参数调用）。

    Returns:
        (bool, reason) —— reason 是给日志看的中文原因，方便用户审查"为什么没回"。
    """
    thread = thread or {}
    if not thread.get("enabled", True):
        return False, "该会话已关闭"
    if msg.get("ai"):
        return False, "自己发的（ai_sent）"
    sender = str(msg.get("sender") or "")
    if me_name and sender == me_name:
        return False, "自己发的"
    text = str(msg.get("text") or "").strip()
    if not text:
        return False, "空消息"

    mode = thread.get("mode") or DEFAULT_MODE
    kind = (thread.get("kind") or "group").lower()
    called = is_called(text, me_name, aliases)
    bare = clean_text(text, me_name, aliases)

    # ---- 范围闸门（只有传了 settings 才生效，顺序不能乱）----
    if settings:
        cid = str(msg.get("cid") or msg.get("thread") or "")
        wl = settings.get("whitelist") or []
        if cid and any(str(x.get("id") or "") == cid
                       for x in wl if isinstance(x, dict)):
            return True, "白名单"
        if kind in ("p2p", "dm", "self"):
            if not settings.get("scope_p2p", True):
                return False, "设置里没勾「单聊」"
        else:
            if not settings.get("scope_group", True):
                return False, "设置里没勾「群聊」"
            if settings.get("scope_at_me", True) and not called:
                return False, "设置里勾了「只在群里@我」"

    # 私聊 / 自聊：默认都回（这就是"秘书"最常用的形态）
    if kind in ("p2p", "self", "dm"):
        if mode == "group_bot" and not called and not bare:
            return False, "空正文"
        return True, "私聊"


    # 群聊
    if mode == "secretary":
        if called:
            return True, "群里叫我"
        if any(bare.startswith(h) for h in _COMMAND_HEADS):
            return True, "群里明确派活"
        return False, "秘书模式：群里不闲聊"

    if mode == "colleague":
        if called:
            return True, "群里叫我"
        return False, "同事模式：没叫我就不插话"

    # group_bot（默认）：@ / 点名 / 指令开头 都接
    if called:
        return True, "群里叫我"
    if any(bare.startswith(h) for h in _COMMAND_HEADS):
        return True, "指令开头"
    triggers = [s.strip() for s in str(thread.get("triggers") or "").split(",") if s.strip()]
    for kw in triggers:
        if kw and kw in text:
            return True, f"命中关键词「{kw}」"
    return False, "群助手：没命中触发条件"


# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------
_TONE = """# 你说话的方式（最重要，别违反）
- 你是在钉钉里回消息的**真人**，不是客服机器人。就像同事/朋友发微信那样说话。
- **短**。一般 1~3 句，最多不超过 5 行。能一句话说完就别说两句。
- 不用 Markdown 排版：不要标题、不要 `- ` 列表、不要表格、不要代码块围栏
  （除非对方明确要代码/文件）。钉钉里这些看起来很难看。
- 禁止这些腔调：「作为AI」「很高兴为您服务」「希望对您有帮助」「如需进一步」
  「以下是」「总结一下」「首先/其次/最后」。也不要每句都加表情。
- 对方说人话你就说人话；对方要东西，直接给结果，不要先复述一遍他的需求。
- 不确定就直说"我查一下"或问一句，别硬编。
"""

# 「拿不准」哨兵：模型只吐这一行，hub 据此换成固定话术 / 标记需人工
UNCERTAIN_MARK = "[[UNCERTAIN]]"
_UNCERTAIN = """# 拿不准的时候
如果对方问的事情在你上面的身份背景、记忆、上下文里都查不到，你也推断不出来，
就**不要猜**：整条回复只输出一行 `[[UNCERTAIN]]`，不要输出别的任何文字。
能推断出来（比如按常识、按上下文）就直接回答。"""

_MODE_RULES = {
    "secretary": """# 你的角色：秘书
- 主动：该提醒的事主动提（时间、待办、截止日）。
- 群里不闲聊：只有在被 @ 或对方明确派活时才说话。
- 汇报要具体：说清"做了什么、结果是什么、文件在哪"。
""",
    "colleague": """# 你的角色：同事
- 平时像同事一样正常聊天，可以接梗、可以简短吐槽，但别刷屏。
- 没被叫到就不要插话。
- 被问到专业问题时给结论，不要绕。
""",
    "group_bot": """# 你的角色：群助手
- 盯住群里的指令：被 @、被点名、或以"帮我/记录/提醒/统计…"开头时接话。
- 别人之间正常聊天不要插嘴。
- 一次只回应当前这条，不要把整个群的历史都复述一遍。
""",
}


def build_system(thread, me_name="", shared_memory="", thread_memory="",
                 now=None, extra="", identity=""):
    """拼这一轮的系统提示。

    `identity` 是用户在设置里自己写的"我是谁"（口吻/关系/最近发生的事），
    放在人设/规则之后、记忆之前 —— 它比记忆更该被当成事实。
    """
    thread = thread or {}
    mode = thread.get("mode") or DEFAULT_MODE
    parts = [
        f"# 你的身份\n你叫「{me_name or '小助手'}」，在钉钉的"
        f"「{thread.get('name') or '这个会话'}」里和大家一起工作。\n"
        f"当前会话模式：{MODES.get(mode, mode)}。",
        _MODE_RULES.get(mode, _MODE_RULES["group_bot"]),
        _TONE,
        _UNCERTAIN,
    ]
    if now:
        parts.append("# 现在的时间\n" + str(now))
    else:
        parts.append("# 现在的时间\n" + time.strftime("%Y-%m-%d %H:%M（星期%w）",
                                                     time.localtime()))
    persona = str(thread.get("persona") or "").strip()
    if persona:
        parts.append("# 用户给这个人设的额外要求\n" + persona)
    rules = str(thread.get("rules") or "").strip()
    if rules:
        parts.append("# 这个会话的规矩（必须遵守）\n" + rules)
    ident = str(identity or "").strip()
    if ident:
        parts.append("## 我的身份背景（这是主人自己写的，回答时必须以它为准）\n"
                     + ident[:3000])
    if shared_memory.strip():
        parts.append("# 共享记忆（所有会话通用，别重复问已经知道的事）\n"
                     + shared_memory.strip()[:3000])
    if thread_memory.strip() and thread.get("memory_on", True):
        parts.append("# 这个会话自己的记忆\n" + thread_memory.strip()[:3000])
    if extra:
        parts.append(str(extra))
    return "\n\n".join(p for p in parts if p)


def build_messages(thread, msgs, limit=None, me_name="", shared_memory="",
                   thread_memory="", extra_system="", identity=""):
    """把消息流水折成 [{role, content}]（system 在最前）。

    - `in` 方向（别人说的）→ user
    - `out` 方向（我们/我说的）→ assistant
    - 连续同角色会被合并，保证大模型端点不会因为角色不交替而报错。
    """
    thread = thread or {}
    n = int(limit or thread.get("ctx_limit") or 20)
    rows = list(msgs or [])[-max(1, n):]
    out = [{"role": "system",
            "content": build_system(thread, me_name, shared_memory, thread_memory,
                                    extra=extra_system, identity=identity)}]
    last_role = ""
    for m in rows:
        text = str(m.get("text") or "").strip()
        if not text:
            continue
        role = "assistant" if m.get("dir") == "out" else "user"
        if role == "user":
            who = str(m.get("sender") or "").strip()
            if who and who != me_name:
                text = f"{who}：{text}"
        if role == last_role and out and out[-1]["role"] == role:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": role, "content": text})
            last_role = role
    return out


def reply_style_hint(text):
    """对方这句话是"要东西"还是"闲聊"——给模型的轻量提示。"""
    t = str(text or "")
    if any(t.startswith(h) for h in _COMMAND_HEADS):
        return "对方在派活，直接给结果，别客套。"
    if len(t) <= 6 and t in _CHITCHAT_ONLY:
        return "对方只是应一声，回一两个字就行，别展开。"
    if t.endswith("?") or t.endswith("？") or "吗" in t:
        return "对方在问问题，先给答案，再补一句必要的说明。"
    return ""
