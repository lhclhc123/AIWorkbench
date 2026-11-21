# -*- coding: utf-8 -*-
"""IM 助手「要不要回、怎么回」的判定与人设（v9.20.0）。

五件事：
1. `should_reply()` —— 这条消息该不该接？**群聊只回 @ 我的消息（v9.22.8
   用户硬性要求，与 mode / 白名单 / scope_* 无关）**，私聊默认都回；同时必须
   挡住"自己回自己"（否则群里会自己跟自己聊起来）。
2. `confidence_of()` —— ★ v9.22.12：模型这段回复**敢不敢外发**（明确知道 vs
   拿不准）。拿不准的一律不许发到钉钉，这是主任场景的安全闸门。
3. `decide()` —— ★ v9.22.12：把「回复模式 / 通知类回执 / 置信度 / 兜底话术」
   合成唯一一个决策：`reply` / `fallback` / `ack` / `silent`。
4. `build_system()` —— 拼系统提示。核心是**说人话**：不客服腔、不列表化、
   不做"作为 AI 助手我很乐意"这种腔调。
5. `build_messages()` —— 把消息流水折成模型能吃的上下文（共享记忆 + 本会话记忆
   + 最近 N 轮），并保证角色交替合法。
"""
import re
import time

from .store import (MODES, DEFAULT_MODE, IM_REPLY_MODES, DEFAULT_REPLY_MODE,
                    IM_DEFAULT_SETTINGS, DEFAULT_FALLBACK_TEXT, DEFAULT_ACK_TEXT)


# 「叫我」的默认称呼（会话档案里没写时用）
DEFAULT_ALIASES = ("小助手", "助手", "AI", "ai")

# ⚠ v9.22.8 起这组词**只用于 `reply_style_hint()` 判断该用什么口吻回复**，
#   不再参与「要不要回」的判定（群聊只认 @ 我，见 `is_mentioned()`）。
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


def is_mentioned(text, me_name="", aliases=DEFAULT_ALIASES):
    """严格判定「@ 我」或「开头点名」。

    ★ v9.22.8（用户硬性要求：群聊只有 @ 我才回，否则一律全都不回）：
    不能用 `is_called` 的宽松版 —— 它只要正文里**任何位置**出现称呼就算
    （例如别名里有 "AI"，一句 "这个 ai 挺好用" 就会被当成在叫我，群里
    就会瞎插话）。群聊只认两种形态：
      1. 文本里出现 ``@称呼``（钉钉 @ 人就是把 "@名字" 写进正文）；
      2. 文本**以称呼开头**，且后面紧跟空白 / 标点 / 结束（"小助手，帮我看下"）。
    句中出现的不算。
    """
    t = str(text or "")
    if not t:
        return False
    names = [n for n in ([me_name] + list(aliases)) if n]
    # 1) 显式 @（长的名字优先，避免 "小助手" 被 "助手" 抢先匹配后残留字）
    for n in sorted(names, key=len, reverse=True):
        if ("@" + n) in t:
            return True
        # 钉钉有时写成 "@ 小助手"（@ 与名字之间有空白）
        if ("@ " + n) in t or ("@\u2005" + n) in t or ("@\u00a0" + n) in t:
            return True
    # 2) 开头点名
    head = t.lstrip(" \u2005\u00a0")
    for n in sorted(names, key=len, reverse=True):
        if not head.startswith(n):
            continue
        rest = head[len(n):]
        if not rest or rest[0] in " \u2005\u00a0,，:：!！?？、\n\t":
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
    mentioned = is_mentioned(text, me_name, aliases)
    bare = clean_text(text, me_name, aliases)
    is_group = kind not in ("p2p", "dm", "self")
    no_at = "群里没@我（v9.22.8 起群聊只回 @ 我的消息）"

    # ---- 范围闸门（只有传了 settings 才生效，顺序不能乱）----
    if settings:
        cid = str(msg.get("cid") or msg.get("thread") or "")
        wl = settings.get("whitelist") or []
        wl_hit = bool(cid) and any(str(x.get("id") or "") == cid
                                   for x in wl if isinstance(x, dict))
        if is_group:
            # ★ 铁律先行：群聊必须先 @ 我。白名单 / scope_* 都不能把它放行
            #   （老版本 `whitelist` 是"命中就 return True"，等于把整个群的
            #   每条闲聊都放进来，这正是 2026-11-14 误发的同类根因之一）。
            if not mentioned:
                return False, no_at
            if wl_hit:
                return True, "白名单"
            if not settings.get("scope_group", True):
                return False, "设置里没勾「群聊」"
        else:
            if wl_hit:
                return True, "白名单"
            if not settings.get("scope_p2p", True):
                return False, "设置里没勾「单聊」"
    elif is_group and not mentioned:
        # 没传设置（老探针 / 老调用方）也照样守这条铁律
        return False, no_at

    # 私聊 / 自聊：默认都回（这就是"秘书"最常用的形态）
    if not is_group:
        if mode == "group_bot" and not called and not bare:
            return False, "空正文"
        return True, "私聊"

    # 群聊：能走到这里说明已经 @ 我了（上面闸门挡过），直接放行。
    # 三种 mode 的区别只在「人设/口吻」，不再影响「接不接」。
    if mode == "secretary":
        return True, "群里叫我"
    if mode == "colleague":
        return True, "群里叫我"
    return True, "群里叫我"



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


# ===========================================================================
# ★ v9.22.12（主任场景）：明确知道才回 —— 置信闸门 + 三档决策
# ===========================================================================
# 用户原话：
#   「自动回复需遵循规则：AI 明确知道答案才回复，不确定的内容不回复或回复统一
#     兜底消息；在设置中提供针对通知类消息（如家长请假、告知主任事项等）的
#     可选自动回复"好的"选项。」
#
# 这一节的三个函数都是**纯函数**（不读盘、不联网、不依赖 Qt），方便审计和单测：
#   confidence_of(reply, question) -> (level, reason)
#   match_notify(text, keywords)   -> 命中的关键词（没命中返回 ""）
#   decide(msg, cfg, model_reply)  -> {"action", "text", "reason"}
#
# 所有判定用的正则都提升成**模块级常量**：主任/审计想确认"到底哪些话会被拦"，
# 直接看下面这几张表即可，不用读函数体。
# ---------------------------------------------------------------------------

# ---- 表 1：命中任一即「不可外发」（模型在自我否认 / 回避 / 猜） ----
# 每项：(代号, 编译后的正则, 给日志看的中文原因)
LOW_CONFIDENCE_PATTERNS = (
    ("uncertain", re.compile(r"不确定"),
     "回复含『不确定』，判定不可外发"),
    ("unclear", re.compile(r"不清楚"),
     "回复含『不清楚』，判定不可外发"),
    ("dont_know", re.compile(r"不知道"),
     "回复含『不知道』，判定不可外发"),
    ("cannot_determine", re.compile(r"无法确定|无法判断|无法确认|无法回答"),
     "回复含『无法确定』，判定不可外发"),
    ("not_familiar", re.compile(r"不太了解|不了解|不熟悉"),
     "回复含『不太了解』，判定不可外发"),
    ("no_info", re.compile(r"没有相关信息|没有找到相关|查不到|找不到相关|暂无相关"),
     "回复含『没有相关信息』，判定不可外发"),
    ("guess", re.compile(r"我猜|猜测|估计是|估计会"),
     "回复含『我猜』，判定不可外发"),
    ("maybe", re.compile(r"可能是|大概是|应该是|也许是|应该是吧"),
     "回复含『可能是』，判定不可外发"),
    ("suggest_ask", re.compile(r"建议你(自己)?(去)?(问|查|确认)|建议咨询|建议联系"),
     "回复让对方向别人确认，判定不可外发"),
    ("contact", re.compile(r"请联系|请问一下|请咨询"),
     "回复让对方向别人联系，判定不可外发"),
    ("details_please", re.compile(r"具体(情况|细节)?请"),
     "回复让对方补信息，判定不可外发"),
    ("cant", re.compile(r"我无法|我不能|没法回答|帮不上"),
     "回复自称无法处理，判定不可外发"),
    ("apology", re.compile(r"抱歉|对不起"),
     "回复在道歉，判定不可外发"),
    ("as_ai", re.compile(r"作为(一个)?(AI|ai|助手|人工智能)|我只是(一个)?(AI|ai|助手)"),
     "回复自称是 AI，判定不可外发"),
)

# ---- 表 2：可核查锚点（high 至少要命中一个） ----
# 每项：(类别名, 编译后的正则)
ANCHOR_PATTERNS = (
    ("数字", re.compile(r"\d|[一二三四五六七八九十百]+(个|人|天|点|号|次|份|条)")),
    ("日期", re.compile(r"\d{1,4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}"
                        r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号]")),
    ("时间", re.compile(r"\d{1,2}\s*[:：]\s*\d{2}"
                        r"|上午|下午|中午|晚上|早上|今晚|明早"
                        r"|周[一二三四五六日天]|星期[一二三四五六日天]"
                        r"|明天|后天|今天|昨天|下周|本周|这周")),
    ("地点", re.compile(r"会议室|办公室|教室|操场|食堂|图书馆|报告厅|礼堂"
                        r"|校区|门口|门卫|楼上|楼下"
                        r"|\d+\s*楼|[一二三四五六七八九十]楼"
                        r"|[一二三四五六七八九十\d]+(年级|班|组|号楼|栋|室)")),
    ("人名", re.compile(r"[\u4e00-\u9fa5]{1,3}(老师|主任|校长|书记|科长|处长|同学|家长)"
                        r"|@[\w\u4e00-\u9fa5]{2,12}")),
    ("文件", re.compile(r"[A-Za-z]:\\|\.(docx?|xlsx?|pptx?|pdf|txt|csv|zip)\b"
                        r"|/[\w\-.]+/")),
)

# ---- 表 3：一句话里出现这些，说明是**在提问**（通知类不许用「好的」敷衍） ----
QUESTION_MARKS = ("吗", "?", "？", "是否", "能不能", "可不可以",
                  "什么", "怎么", "如何", "哪", "谁", "几", "多少")

# ---- 表 4：内置「通知类」关键词（家长请假 / 告知主任事项 / 学校日常事务） ----
# 注意：这里**自己维护一份轻量表**，不 import 别的模块，避免耦合与循环依赖。
NOTIFY_KEYWORDS = (
    "请假", "销假", "调课", "代课", "补课", "开会", "会议", "通知",
    "检查", "报名", "交表", "材料", "家长会", "值班", "排班", "监考",
    "考试", "培训", "教研", "体检", "缴费", "打卡", "签到", "布置",
    "统计", "汇总", "上报", "审批", "签字", "领取",
    "停课", "停电", "检修", "维修", "演练", "放假", "搬迁", "消毒",
)

# 「整段以问号结尾且不足这么多字」= 它在反问，不是在回答
_PURE_QUESTION_MAX_LEN = 40
# 极短敷衍的门槛（不含任何可核查锚点）
_TOO_SHORT_LEN = 6
# 纯标点 / 纯表情 / 纯空白（中文属于 \w，所以这条不会误伤正常句子）
_RE_PURE_NOISE = re.compile(r"^[\W_]+$", re.UNICODE)
# 关键词分隔符：中英文逗号 / 顿号 / 分号 / 换行 / 竖线
_RE_KEYWORD_SPLIT = re.compile(r"[,，、;；\n\r\t|]+")


def normalize_reply_mode(value):
    """把任意值归一成 `off` / `confident_only` / `always`（认不出就保守取中）。"""
    mode = str(value or "").strip().lower()
    return mode if mode in IM_REPLY_MODES else DEFAULT_REPLY_MODE


def reply_mode_label(mode):
    """给界面/日志看的中文名。"""
    return {
        "off": "关闭（不自动回复）",
        "confident_only": "只回确定的",
        "always": "全部都回",
    }.get(normalize_reply_mode(mode), "只回确定的")


def parse_notify_keywords(raw=""):
    """把界面里那串自定义关键词解析成元组（内置表在前，自定义在后，去重保序）。

    支持逗号 / 顿号 / 分号 / 换行分隔（直接粘一整列也行）。
    """
    out, seen = [], set()
    for word in NOTIFY_KEYWORDS:
        w = str(word or "").strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    for word in _RE_KEYWORD_SPLIT.split(str(raw or "")):
        w = word.strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return tuple(out)


def match_notify(text, extra_keywords=""):
    """这句话像不像「通知类」（家长请假 / 调课 / 检查…）。

    Returns:
        命中的关键词（字符串）；没命中返回 ""。
    """
    t = str(text or "")
    if not t:
        return ""
    for word in parse_notify_keywords(extra_keywords):
        if word and word in t:
            return word
    return ""


def is_question(text):
    """这句话是不是在**提问**（通知类回执必须先过这一关）。"""
    t = str(text or "")
    if not t:
        return False
    return any(mark in t for mark in QUESTION_MARKS)


def _cfg_value(cfg, key):
    """从设置里取一个键；**老配置文件没有这个键时退回默认值**（绝不 KeyError）。"""
    if isinstance(cfg, dict) and cfg.get(key) is not None:
        return cfg[key]
    return IM_DEFAULT_SETTINGS.get(key)


def _norm_for_compare(text):
    """去掉空白与标点，只留字，用来判断"是不是在复述对方的话"。"""
    return re.sub(r"[\s\W_]+", "", str(text or ""), flags=re.UNICODE)


def confidence_of(reply_text, question_text="", force_level=""):
    """这段回复**敢不敢直接发出去**。

    Args:
        reply_text: 模型生成的回复正文（调用方应已剥掉 DSML / 工具调用残片）。
        question_text: 对方原话（用来识别"只是复述问题"这种假回答）。
        force_level: 调用方强制指定 `"high"` / `"low"`（覆盖开关，传了就以它为准）。

    Returns:
        (level, reason)：
            level  ∈ {"high", "low"}
            reason 是**人能看懂的中文短语**，会写进日志给主任查。
    """
    forced = str(force_level or "").strip().lower()
    if forced in ("high", "low"):
        return forced, "由调用方指定（force_level=%s）" % forced

    text = str(reply_text or "").strip()
    if not text:
        return "low", "回复为空，判定不可外发"
    if _RE_PURE_NOISE.match(text):
        return "low", "回复只有标点或表情，判定不可外发"

    # ① 自我否认 / 回避 / 猜 —— 命中任一即不可外发
    for _code, pattern, reason in LOW_CONFIDENCE_PATTERNS:
        if pattern.search(text):
            return "low", reason

    # ② 整段以问号结尾且很短 = 它在反问对方，不是在回答
    if text[-1] in "?？" and len(text) < _PURE_QUESTION_MAX_LEN:
        return "low", "回复是在反问对方（不足%d字），判定不可外发" % _PURE_QUESTION_MAX_LEN

    # ③ 只是把对方的问题复述一遍（常见于模型偷懒）
    q = _norm_for_compare(question_text)
    if q and len(q) >= 4 and _norm_for_compare(text) == q:
        return "low", "回复只是复述对方的问题，判定不可外发"

    # ④ 至少要有 1 个「可核查锚点」才算 high
    hits = [name for name, pattern in ANCHOR_PATTERNS if pattern.search(text)]
    if hits:
        return "high", "回复含可核查信息（%s），判定可外发" % "、".join(hits[:3])

    # ⑤ 没有锚点：短回复算敷衍，长回复算"说了等于没说"，都是 low
    if len(text) < _TOO_SHORT_LEN:
        return "low", "回复过短（不足%d字）且没有可核查信息，判定不可外发" % _TOO_SHORT_LEN
    return "low", "回复没有可核查信息（数字/时间/地点/人名/文件），判定不可外发"


def _sanitize_body(text):
    """外发正文的兜底清洗（只在疑似有标记时才动手，干净文本零开销）。

    正常链路里 `app.im.replyproc` 返回前已经洗过；这里再兜一次是为了保证
    **不管调用方怎么接，`decide()` 都是「先清洗、再判置信度」** —— 顺序反了
    会把「<tool_call>…」这种脏文本判成 high，那就白设闸门了。
    """
    s = str(text or "")
    if not s:
        return ""
    if "<" not in s and "[[uncertain]]" not in s.lower() and "｜" not in s:
        return s
    try:
        from .replyproc import sanitize_reply
        return sanitize_reply(s)
    except Exception:
        return s


def decide(msg, cfg=None, model_reply=None, force_level=""):
    """★ 主任场景的唯一决策入口：这条消息**到底发不发、发什么**。

    优先级（顺序不能改）：
        1. `im_reply_mode == "off"`                  → silent
        2. 命中通知类 + `im_notify_ack_enabled`      → ack（默认「好的」）
           例外：通知里含明确提问 → 走正常回复流程，别用「好的」敷衍
        3. 模型回复置信度 high                       → reply
        4. 置信度 low + `im_fallback_enabled`        → fallback
           置信度 low + 兜底关闭                     → silent

    Args:
        msg: 收到的消息（dict，取 `text`；也允许直接传字符串）。
        cfg: `IMStore.settings()` 那一份设置；缺键自动用默认值。
        model_reply: 模型生成的回复正文（还没生成就传 None）。
        force_level: 透传给 `confidence_of` 的覆盖开关。

    Returns:
        {"action": "reply"|"fallback"|"ack"|"silent", "text": str, "reason": str}
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    if isinstance(msg, dict):
        incoming = str(msg.get("text") or "")
    else:
        incoming = str(msg or "")
    incoming = incoming.strip()

    mode = normalize_reply_mode(_cfg_value(cfg, "im_reply_mode"))

    # ---- 1. 关闭：一个字都不发 ----
    if mode == "off":
        return {"action": "silent", "text": "",
                "reason": "回复模式为「关闭」，不自动回复"}

    # ---- 2. 通知类回执 ----
    ack_on = bool(_cfg_value(cfg, "im_notify_ack_enabled"))
    if ack_on and incoming:
        hit = match_notify(incoming, _cfg_value(cfg, "im_notify_keywords") or "")
        if hit:
            if is_question(incoming):
                # 通知里夹着问题时，「好的」等于没答 —— 交给下面的正常流程
                pass
            else:
                ack_text = str(_cfg_value(cfg, "im_notify_ack_text")
                               or DEFAULT_ACK_TEXT).strip() or DEFAULT_ACK_TEXT
                return {"action": "ack", "text": ack_text,
                        "reason": "命中通知关键词「%s」，自动回执" % hit}

    # ★ 先清洗、再判置信度（顺序不能反）：脏文本里的标签会把 low 判成 high。
    body = _sanitize_body(model_reply).strip()

    # ---- 3. 「全部都回」：模型说什么发什么（没有正文就按兜底处理） ----
    if mode == "always" and body:
        return {"action": "reply", "text": body,
                "reason": "回复模式为「全部都回」，直接发送"}

    # ---- 4. 置信闸门 ----
    level, why = confidence_of(body, incoming, force_level=force_level)
    if level == "high":
        return {"action": "reply", "text": body, "reason": why}

    fallback_on = bool(_cfg_value(cfg, "im_fallback_enabled"))
    if fallback_on:
        ftext = str(_cfg_value(cfg, "im_fallback_text")
                    or DEFAULT_FALLBACK_TEXT).strip() or DEFAULT_FALLBACK_TEXT
        return {"action": "fallback", "text": ftext,
                "reason": why + "；按设置发送兜底消息"}
    return {"action": "silent", "text": "",
            "reason": why + "；兜底已关闭，按设置不发送"}
