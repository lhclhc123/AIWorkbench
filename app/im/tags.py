# -*- coding: utf-8 -*-
"""会话扫描 + AI 关系标签（v9.20.0）。

这一层只做**纯逻辑**：
  * 不 import PyQt6、不 import requests、不自己发网络请求；
  * 钉钉能力、模型能力全部由调用方注入（`ding` / `llm_fn`），方便离线自证；
  * 采集阶段把「我自己发的」「AI 自己发的」消息剔掉 —— 否则模型会把
    助手自己的套话当成对方的语气，标签必然跑偏。

流程：`collect()` 采样本 → `build_prompt()` 拼提示 → `parse_tag()` 严格解析
→ `scan_and_tag()` 编排并写回 `store.set_tag()`。

标签写回约定：`store.set_tag(cid, tag, note="AI 扫描", auto=True)`。
**人工标签永不覆盖**（`tag` 非空且 `tag_auto` 为假就直接跳过）。
"""
import re

MAX_SAMPLE_PER_CONV = 20      # 每个会话最多取多少条做样本
MAX_CONVS = 40                # 最多扫多少个会话

TAG_MAX_LEN = 12              # 标签最长多少个字（模型啰嗦时硬截断）

# 解析标签时要去掉的包裹符号（首尾都去）
_PUNCT = " \t\r\n\u3000。．.,，、:：;；!！?？\"'“”‘’「」『』【】[]()（）《》<>"

# 模型常见的"前缀"（有时会自带）
_PREFIX_RE = re.compile(r"^(标签|关系|答案)\s*[:：]\s*")

# 这些词出现就代表"模型自己也拿不准"，一律不打标签
_UNKNOWN_WORDS = ("不知道", "不确定", "无法判断", "未知")

# 会话的时间字段，按优先级
_TS_KEYS = ("lastMessageTime", "lastActiveTime", "updateTime", "createTime")


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _to_ts(v):
    """把一个时间字段转成秒级 float。转不了返回 None（注意 0 是合法值）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        num = float(v)
    else:
        s = str(v or "").strip()
        if not s:
            return None
        try:
            num = float(s)
        except Exception:
            return None
    if num > 1e11:           # 毫秒时间戳
        num = num / 1000.0
    return num


def _conv_ts(conv):
    """会话的「最近对话时间」：按 _TS_KEYS 找第一个能转成数字的。"""
    if not isinstance(conv, dict):
        return 0.0
    for k in _TS_KEYS:
        ts = _to_ts(conv.get(k))
        if ts is not None:
            return ts
    return 0.0


def _first_line(text):
    """取第一行**有内容**的行（模型常先输出空行）。"""
    for line in str(text or "").splitlines():
        if line.strip():
            return line
    return ""


def _sender_of(msg):
    s = msg.get("sender")
    if isinstance(s, dict):
        s = s.get("name") or s.get("nick") or s.get("nickName") or ""
    if not s:
        s = msg.get("senderName") or msg.get("senderNick") or ""
    return str(s or "").strip()


def _msg_text(ding, msg):
    """优先用钉钉对象自己的压缩方法（带附件名），失败再退回裸字段。"""
    try:
        t = ding._msg_text(msg)
    except Exception:
        t = ""
    if not t:
        t = msg.get("text") or msg.get("content") or ""
    return str(t or "").strip()


def _ai_sent(msg):
    v = msg.get("messageAiSendFlag")
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v or "").strip().lower() in ("1", "true", "yes", "y")


# ----------------------------------------------------------------------
# 会话分类 / 排序
# ----------------------------------------------------------------------
def conv_kind(conv):
    """判断会话是 "group" 还是 "p2p"。先命中先返回，字段缺失也能容错。"""
    if not isinstance(conv, dict):
        return "group"

    # 1. 明确是单聊
    single = conv.get("singleChat")
    if single is True or (isinstance(single, (int, str))
                          and str(single).strip().lower() in ("1", "true", "yes")):
        return "p2p"
    ct = str(conv.get("conversationType") or "").strip().lower()
    if ct in ("1", "single", "p2p"):
        return "p2p"

    # 2. 明确是群聊
    if ct in ("2", "group", "groupchat"):
        return "group"

    # 3. dws 里"自己"那个会话的名字形如「张三(self)」/「张三(SELF)」
    name = str(conv.get("conversationName") or conv.get("name") or "").strip()
    nm = name.lower().rstrip(")）]】》 \t")
    if nm.endswith("self"):
        return "p2p"

    # 4. 兜底
    return "group"


def sort_convs(items):
    """先群聊后单聊；同类里按最近对话时间倒序。返回新 list，不改原对象。"""
    out = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        d = dict(it)                      # 浅拷贝，外部对象不动
        d["last_ts"] = _conv_ts(d)
        out.append(d)
    # key: (类型优先级, 负时间) —— group=0 排前面
    out.sort(key=lambda c: (0 if conv_kind(c) == "group" else 1, -c["last_ts"]))
    return out


# ----------------------------------------------------------------------
# 采样
# ----------------------------------------------------------------------
def collect(ding, max_convs=MAX_CONVS, per_conv=MAX_SAMPLE_PER_CONV):
    """扫会话 + 采样本。**绝不抛异常**，全部兜成 ok=False + reason。"""
    empty = {"ok": False, "reason": "", "me": "", "convs": []}
    try:
        max_convs = max(1, int(max_convs))
    except Exception:
        max_convs = MAX_CONVS
    try:
        per_conv = max(1, int(per_conv))
    except Exception:
        per_conv = MAX_SAMPLE_PER_CONV

    # ---- 1. 登录状态
    try:
        st = ding.dws_auth_status() or {}
    except Exception as e:
        r = dict(empty)
        r["reason"] = "读取 dws 登录状态失败：%s: %s" % (type(e).__name__, e)
        return r
    if not isinstance(st, dict):
        st = {}
    if not st.get("authenticated"):
        r = dict(empty)
        r["reason"] = "dws 未登录，请先到「集成 → 钉钉」完成授权"
        return r
    me = str(st.get("user_name") or "").strip()

    # ---- 2. 会话列表
    try:
        data = ding.dws_conversation_list_full(max_convs) or {}
    except Exception as e:
        r = dict(empty)
        r["me"] = me
        r["reason"] = "读取会话列表失败：%s: %s" % (type(e).__name__, e)
        return r
    items = []
    if isinstance(data, dict):
        items = data.get("conversations") or []
    elif isinstance(data, list):
        items = data
    items = [c for c in items
             if isinstance(c, dict) and str(c.get("openConversationId") or "").strip()]
    if not items:
        r = dict(empty)
        r["me"] = me
        r["reason"] = "没读到任何会话（dws 返回空列表）"
        return r

    # ---- 3. 排序 + 截断
    items = sort_convs(items)[:max_convs]

    # ---- 4. 逐会话采样本
    convs = []
    seen = set()
    for c in items:
        cid = str(c.get("openConversationId") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        name = str(c.get("conversationName") or c.get("name") or "").strip() \
            or "（未命名会话）"
        conv = {
            "cid": cid,
            "name": name,
            "kind": conv_kind(c),
            "last_ts": c.get("last_ts") or _conv_ts(c),
            "samples": [],
        }
        # 单个会话读失败不影响其它会话
        try:
            raw = ding.dws_read_messages_raw(cid, per_conv) or []
        except Exception:
            raw = []
        if not isinstance(raw, list):
            raw = []
        samples = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            if me and _sender_of(m) == me:      # 我自己发的，不能当"对方语气"
                continue
            if _ai_sent(m):                     # AI 自己发的套话
                continue
            text = _msg_text(ding, m)
            if not text:
                continue
            ts = None
            for k in ("createTime", "sendTime", "time", "msgTime"):
                ts = _to_ts(m.get(k))
                if ts is not None:
                    break
            samples.append({
                "sender": _sender_of(m) or "（未知）",
                "text": text[:120],
                "ts": float(ts or 0.0),
            })
        samples = samples[:per_conv]
        samples.sort(key=lambda s: s["ts"])     # 时间正序（全 0 时保持原序）
        conv["samples"] = samples
        convs.append(conv)

    if not convs:
        r = dict(empty)
        r["me"] = me
        r["reason"] = "会话列表里没有可用的 openConversationId"
        return r

    return {"ok": True, "reason": "", "me": me, "convs": convs}


# ----------------------------------------------------------------------
# 提示词
# ----------------------------------------------------------------------
_SYSTEM = """你在帮一个人整理他的钉钉联系人关系。给他一段聊天记录，你判断【对方是谁 / 跟他什么关系】。

规则：
1. 可以输出**1~3 个**标签，每个 2~8 个汉字，多个标签用顿号「、」分隔，例如：同班同学、家长。
2. 群聊就输出这个群是什么群（如：班级群、家长群、项目群、游戏群）。
3. **证据不足就只输出两个字：不知道**。绝对不许瞎猜、不许编。
4. 不要输出标点、引号、解释、序号，只输出标签本身。"""


def build_prompt(conv, me_name=""):
    """给「一个会话」打标签用的 messages（OpenAI 格式）。"""
    conv = conv or {}
    me = str(me_name or "").strip()
    name = str(conv.get("name") or "").strip() or "（未命名会话）"
    kind_txt = "群聊" if conv.get("kind") == "group" else "单聊"
    lines = [
        "我的名字：" + (me or "（未知）"),
        "会话名称：" + name,
        "会话类型：" + kind_txt,
    ]
    samples = conv.get("samples") or []
    if not samples:
        lines.append("最近聊天记录：（无，没有任何可读消息）")
    else:
        lines.append("最近聊天记录（时间正序）：")
        for s in samples:
            who = str((s or {}).get("sender") or "").strip() or "（未知）"
            txt = str((s or {}).get("text") or "").strip()
            if not txt:
                continue
            lines.append("- %s：%s" % (who, txt))
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------
def _split_tags(text):
    """把候选串按顿号/逗号/空格切成标签段，去空、截断 12 字、去重（保持顺序）。"""
    if text is None:
        return []
    parts = re.split(r"[、,，\s]+", str(text))
    out, seen = [], set()
    for p in parts:
        p = p.strip(_PUNCT)
        if not p:
            continue
        p = p[:TAG_MAX_LEN].strip(_PUNCT)
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def parse_tags(text):
    """把模型输出解析成标签列表（0~N 个）。空则 []。"""
    if text is None:
        return []
    cand = _first_line(text).strip(_PUNCT)
    if not cand:
        return []
    # 去掉「标签：」这类前缀（可能连着写了两层）
    for _ in range(2):
        m = _PREFIX_RE.match(cand)
        if not m:
            break
        cand = cand[m.end():].strip(_PUNCT)
    if not cand:
        return []
    low = cand.replace(" ", "")
    for w in _UNKNOWN_WORDS:
        if w in low:
            return []
    return _split_tags(cand)


def parse_tag(text):
    """严格解析模型输出。返回 "" 代表"不打标签"。**永远不会超过 12 字**。"""
    tags = parse_tags(text)
    return tags[0] if tags else ""


# ----------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------
def scan_and_tag(store, ding, llm_fn, progress=None, max_convs=MAX_CONVS):
    """扫全部会话 → 逐个打标签（同步阻塞，调用方自己放线程里）。

    `llm_fn(messages, meta) -> str`，meta 是该会话的采集结果（含 cid/name/kind）。
    """
    data = collect(ding, max_convs)
    if not data.get("ok"):
        return {"ok": False, "reason": data.get("reason") or "扫描失败",
                "tagged": 0, "total": 0, "errors": []}

    me = data.get("me") or ""
    convs = data.get("convs") or []
    total = len(convs)
    tagged = 0
    errors = []

    for i, conv in enumerate(convs, 1):
        name = str(conv.get("name") or "")
        # 进度回调：回调自己的异常必须吞掉，不能影响扫描
        if progress is not None:
            try:
                progress(i, total, name)
            except Exception:
                pass

        cid = str(conv.get("cid") or "").strip()
        if not cid:
            continue
        if not conv.get("samples"):
            continue                      # 没样本 → 界面能看到，但打不了标签

        # 人工标签优先，绝不覆盖
        try:
            t = store.thread(cid) or {}
        except Exception:
            t = {}
        if t.get("tag") and not t.get("tag_auto"):
            continue

        # 调模型（串行，钉钉/模型都有节流）
        try:
            res = llm_fn(build_prompt(conv, me), conv)
        except Exception as e:
            errors.append("%s：%s: %s" % (name or cid, type(e).__name__, e))
            continue

        tag_list = parse_tags(res)
        if not tag_list:
            continue                      # 模型说"不知道" → 不打标签

        try:
            store.set_tag(cid, tag_list, note="AI 扫描", auto=True)
        except Exception as e:
            errors.append("%s：写标签失败 %s: %s" % (name or cid, type(e).__name__, e))
            continue
        tagged += 1

    return {"ok": True, "reason": "", "tagged": tagged, "total": total,
            "errors": errors}
