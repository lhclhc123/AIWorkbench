# -*- coding: utf-8 -*-
"""会话扫描 + AI 关系标签（v9.20.0）。

这一层只做**纯逻辑**：
  * 不 import PyQt6、不 import requests、不自己发网络请求；
  * 钉钉能力、模型能力全部由调用方注入（`ding` / `llm_fn`），方便离线自证；
  * 采集阶段剔除「AI 自己发的套话」——否则模型会把助手自己的话当成对方的语气。

  ★ v9.22.5：**我自己发的消息要保留**（标注成「我」）。旧版把它们全过滤掉了，
    模型只看到对方的「嗯」「好的」，上下文断裂 → 大量会话只能判「未知」。
    真实对话是成对的：我问「明天几点考」、对方答「八点」——只给后半句谁也看不懂。

流程：`collect()` 采样本 → `build_prompt()` 拼提示 → `parse_tag()` 严格解析
→ `guard_tags()` 过滤幻觉 → `scan_and_tag()` 编排并写回 `store.set_tag()`。

标签写回约定：`store.set_tag(cid, tag, note="AI 扫描", auto=True)`。
**人工标签永不覆盖**（`tag` 非空且 `tag_auto` 为假就直接跳过）。
"""
import re

MAX_SAMPLE_PER_CONV = 80      # 每个会话最多取多少条做样本
#   v9.22.5：20 → 40 → 80。实测 80 条只比 40 条多 0.7 秒/会话（dws 一次分页），
#   但能把「我」对对方的称呼（「肖老师」「周老师」）一起带进来 —— 这是判断
#   单聊关系最硬的证据，40 条时经常刚好被截掉，结果一堆会话被打成「未知」。
MAX_CONVS = 120               # 最多扫多少个会话（v9.22.5：40→120，别漏会话）

MAX_TAGS = 5                  # 每个会话最多几个标签（v9.22.5：用户要求 3→5）

TAG_MAX_LEN = 12              # 标签最长多少个字（模型啰嗦时硬截断）

# 模型爱凭空加的"限定词"：材料里没出现就不许带（v9.22.5）
# 真实案例：会话是「编程老师」，模型输出「大学老师」—— 用户根本没上大学。
_QUALIFIERS = ("大学", "研究生", "博士", "硕士", "高中", "初中", "小学",
               "幼儿园", "中学", "职高", "中专", "本科",
               "大一", "大二", "大三", "大四",
               "一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
               "初一", "初二", "初三", "初四", "高一", "高二", "高三")

# 不是"关系/身份"的词：模型从聊天内容里抄到的职能、流程、事件，一律不算标签
_NOISE_WORDS = ("考勤管理", "客户管理", "假期安排", "年级会", "通讯录好友",
                "新的好友", "群公告", "通知", "工作通知", "消息", "管理员",
                "群管理员", "群主", "超级管理员")

# 通用的"班"说法：不需要在材料里原样出现
_GENERIC_CLASS = ("班级群", "班群", "班级", "班级通知群")

# 家庭成员称谓。**群聊**里出现这些当标签 = 把某个成员当成了群 —— 一律改「家人群」。
# 真实案例（v9.22.5）：五人群（爸爸+妈妈+我）被打成「爸爸」。
# 单聊不受影响（「刘浩辰爸爸」→ 爸爸 是对的）。
_MEMBER_ROLES = ("爸爸", "妈妈", "老爸", "老妈", "父亲", "母亲", "爹", "娘",
                 "儿子", "女儿", "哥哥", "姐姐", "弟弟", "妹妹", "兄弟", "姐妹",
                 "爷爷", "奶奶", "姥姥", "姥爷", "外公", "外婆")

# 解析标签时要去掉的包裹符号（首尾都去）
_PUNCT = " \t\r\n\u3000。．.,，、:：;；!！?？\"'“”‘’「」『』【】[]()（）《》<>"

# 模型常见的"前缀"（有时会自带）
_PREFIX_RE = re.compile(r"^(标签|关系|答案)\s*[:：]\s*")

# 这些词出现就代表"模型自己也拿不准"，一律不打标签
_UNKNOWN_WORDS = ("不知道", "不确定", "无法判断", "无法确定", "不能确定",
                  "没有证据", "证据不足", "未知", "unknown")

# 会话的时间字段，按优先级
_TS_KEYS = ("lastMsgCreateAt", "lastMessageTime", "lastActiveTime",
            "updateTime", "createTime")


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
            # ★ v9.22.5：dws `+chat-list` 给的是 ISO 时间（2026-11-07T23:13:57.469+08:00），
            #   以前只认数字 → 排序全按 0 走，看着像"顺序乱了"。
            try:
                import datetime as _dt
                return _dt.datetime.fromisoformat(
                    s.replace("Z", "+00:00")).timestamp()
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


# 钉钉消息里的长串资源 ID：对判断关系毫无信息量，却能把 120 字额度全占满
# （实测「[图片消息](mediaId=$iwEcAqNqcGcDAQTRA...」一条就能吃掉一整行上下文）。
_MEDIA_NOISE = re.compile(r"\(?\s*(?:mediaId|fileId|spaceId|url)\s*[:=]\s*[^)\s]+\)?", re.I)
_MEDIA_KIND = (
    ("[图片消息]", "[图片]"), ("[语音消息]", "[语音]"), ("[视频消息]", "[视频]"),
    ("[文件消息]", "[文件]"), ("[表情消息]", "[表情]"), ("[链接消息]", "[链接]"),
)


def _clean_text(text, limit=120):
    """把消息文本压成模型真正能读的一句话：去资源 ID、缩写媒体占位、限长。"""
    s = str(text or "").strip()
    if not s:
        return ""
    for a, b in _MEDIA_KIND:
        if s.startswith(a):
            s = b + s[len(a):]
            break
    s = _MEDIA_NOISE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if not s:
        return ""
    return s[:limit]


# ----------------------------------------------------------------------
# 会话分类 / 排序
# ----------------------------------------------------------------------
def conv_kind(conv):
    """判断会话是 "group" 还是 "p2p"。

    ★ v9.22.5：`+chat-list` 的真实字段优先（chatMode / conversationType /
    groupType / memberCount）。旧版只认 conversationType/singleChat，而
    `+conversation-list` 这两个字段一个都没有 —— 于是**所有单聊都被当成了群聊**。
    """
    if not isinstance(conv, dict):
        return "group"

    # 0. dws `+chat-list` 的权威字段
    cm = str(conv.get("chatMode") or "").strip().lower()
    if cm in ("p2p", "direct", "single", "one2one"):
        return "p2p"
    if cm in ("group", "groupchat"):
        return "group"
    gt = str(conv.get("groupType") or "").strip().upper()
    if gt == "SINGLE_CHAT":
        return "p2p"
    if gt.endswith("_GROUP"):
        return "group"

    # 1. 明确是单聊
    single = conv.get("singleChat")
    if single is True or (isinstance(single, (int, str))
                          and str(single).strip().lower() in ("1", "true", "yes")):
        return "p2p"
    ct = str(conv.get("conversationType") or "").strip().lower()
    if ct in ("1", "single", "p2p", "direct"):
        return "p2p"

    # 2. 明确是群聊
    if ct in ("2", "group", "groupchat"):
        return "group"

    # 3. dws 里"自己"那个会话的名字形如「张三(self)」/「张三(SELF)」
    name = str(conv.get("conversationName") or conv.get("name") or "").strip()
    nm = name.lower().rstrip(")）]】》 \t")
    if nm.endswith("self"):
        return "p2p"

    # 4. 成员数 = 2 → 单聊（群聊最少 3 人）
    mc = conv.get("memberCount")
    if isinstance(mc, (int, float)) and int(mc) == 2:
        return "p2p"

    # 5. 兜底
    return "group"


def conv_display_name(conv):
    """会话昵称（群名 / 对方昵称）。兼容 dws 的两种字段名。"""
    if not isinstance(conv, dict):
        return ""
    return str(conv.get("name") or conv.get("conversationName") or "").strip()


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
    # ★ v9.22.5：优先 `+chat-list`（带 chatMode/conversationType/groupType，
    #   能真正区分单聊和群聊）；老接口 `+conversation-list` 只有名字和 ID，
    #   拿不到类型 → 会把所有单聊都标成「群聊」。
    items = []
    data = None
    try:
        if hasattr(ding, "dws_chat_list"):
            data = ding.dws_chat_list(types="group,p2p",
                                      page_size=min(100, max(20, max_convs)),
                                      page_all=True)
    except Exception:
        data = None
    if isinstance(data, dict):
        items = data.get("chats") or data.get("items") or []
    elif isinstance(data, list):
        items = data
    if not items:
        try:
            data = ding.dws_conversation_list_full(max_convs) or {}
        except Exception as e:
            r = dict(empty)
            r["me"] = me
            r["reason"] = "读取会话列表失败：%s: %s" % (type(e).__name__, e)
            return r
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
    # ★ v9.22.5：读消息改成 4 路并发。每个会话都是一次独立的 dws 子进程
    #   （约 1 秒），原来是纯串行 —— 30 个会话光采集就要半分钟，这是
    #   「打标签速度慢」的最大头。dws 调用无共享状态，可以安全并发。
    shells = []
    seen = set()
    for c in items:
        cid = str(c.get("openConversationId") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        shells.append((cid, c))

    raw_map = {}

    def _fetch(item):
        _cid, _c = item
        try:
            raw = ding.dws_read_messages_raw(_cid, per_conv) or []
        except Exception:
            raw = []
        return _cid, raw if isinstance(raw, list) else []

    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=4) as ex:
            for _cid, _raw in ex.map(_fetch, shells):
                raw_map[_cid] = _raw
    except Exception:
        # 并发不可用（极端环境）→ 退回串行，功能不受影响
        for item in shells:
            _cid, _raw = _fetch(item)
            raw_map[_cid] = _raw

    convs = []
    for cid, c in shells:
        name = conv_display_name(c) or "（未命名会话）"
        conv = {
            "cid": cid,
            "name": name,
            "kind": conv_kind(c),
            "last_ts": c.get("last_ts") or _conv_ts(c),
            "samples": [],
        }
        raw = raw_map.get(cid) or []
        samples = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            if _ai_sent(m):                     # AI 自己发的套话，会污染判断
                continue
            text = _clean_text(_msg_text(ding, m), 120)
            if not text:
                continue
            # 纯媒体占位（「[图片]」「[语音]」）对判断关系零信息量，别占样本名额
            if text in ("[图片]", "[语音]", "[视频]", "[表情]", "[链接]"):
                continue
            who = _sender_of(m)
            # ★ v9.22.5：我自己说的保留，标成「我」。删掉它就成了半截对话
            #   （只剩对方「嗯」「好的」），模型当然只能判「未知」。
            if me and who == me:
                who = "我"
            ts = None
            for k in ("createTime", "sendTime", "time", "msgTime"):
                ts = _to_ts(m.get(k))
                if ts is not None:
                    break
            samples.append({
                "sender": who or "（未知）",
                "text": text,
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
_SYSTEM = """你在帮一个人整理他的钉钉通讯录。给你一个会话的名称、类型和最近聊天记录，
你判断这个会话对应的【是什么人 / 什么群 / 跟"我"是什么关系】。

输出规则（必须严格遵守）：
1. 最多输出 5 个标签，每个 2~8 个汉字，用顿号「、」分隔。
2. 每个标签都必须在材料里找到**直接、明确的证据**，只有这三种算证据：
   · 会话名称本身直接写明了（例：「三年二班家长群」→ 班级群、家长群）；
   · 聊天记录里明确写了身份 / 关系 / 单位 / 班级 / 职务（例：「我是他妈妈」→ 家长）；
   · **「我」在聊天里对对方的称呼**（例：我说「肖老师，帮我打一份」→ 老师；
     「周老师，这题怎么写」→ 老师；「教练，明天几点」→ 教练）。
     这是单聊最硬的证据，有就必须用。
   注意：只要记录里出现过这类称呼，就**不许**再答「未知」。
3. **注意方向**：你要判断的是**对方**（单聊）或**这个群**（群聊）是什么，
   不是「我」是什么。
   · 「我」怎么称呼对方 → 直接说明**对方**是谁（最强证据，例：「肖老师」→ 老师）；
   · 对方怎么称呼「我」 → 只说明**我**是谁，**不能**拿来给对方打标签。
     反例：老师发通知时管我叫「家长」，这只说明我是家长，**不能**给老师打「家长」；
     正例：对方管我叫「同学」「孩子」，一般说明对方是老师或学长。
4. 只靠"感觉像""可能是""大概"的一律不许写。宁可少打，不可乱打：
   证据只够一个标签就只输出一个，一个都不够就不打。
5. 只有**上面几种证据一条都不成立**时，才输出两个字：未知。
   不要因为「只聊了几句」「看不出职业」就偷懒答未知 —— 先找称呼、找身份词。
6. 聊天记录里「我」是我自己说的话，其它名字是对方。判断关系时要**结合双方对话**
   （我问了什么、对方怎么答），不要只看对方那半句。
7. 不要输出标点、引号、解释、序号，不要输出人名本身（人名不是关系标签）。
8. 不要输出「朋友」「联系人」「同事」这类没有信息量的空标签，除非记录里明确说了。
9. 只输出**关系 / 身份 / 群体类型**（如：家长群、同班同学、班主任、邻居）。
   不要输出事件、流程、职能、通知类词（如：考勤管理、假期安排、客户管理、群公告）。
10. 同一个意思只写一次，不要既写「同学」又写「同班同学」。
11. **群聊要描述「这个群是什么群」**（如：班级群、家长群、家人群、复赛群）；
   单聊才描述「对方是谁 / 什么关系」。不要把群里某个成员的名字当标签，
   **也不要用「爸爸」「妈妈」「哥哥」这类单个家庭成员的称谓当群标签** ——
   成员的家人互称、聊家里事（做饭、缴费、接送），这个群就是「家人群」。
   （例：五人群里是爸爸、妈妈和我在聊，就写「家人群」，不要写「爸爸」。）"""


def _conv_display_name(conv):
    """会话的展示昵称（群名 / 对方昵称）。空则给占位，绝不用会话 ID 冒充。"""
    conv = conv or {}
    name = str(conv.get("name") or "").strip()
    cid = str(conv.get("cid") or "").strip()
    if not name or name == cid:
        return ""
    return name


def build_prompt(conv, me_name=""):
    """给「一个会话」打标签用的 messages（OpenAI 格式）。"""
    conv = conv or {}
    me = str(me_name or "").strip()
    name = _conv_display_name(conv) or "（未命名会话）"
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
    """把模型输出解析成标签列表（0~MAX_TAGS 个）。空则 []。"""
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
    low = cand.replace(" ", "").lower()
    for w in _UNKNOWN_WORDS:
        if w in low:
            return []
    return _split_tags(cand)[:MAX_TAGS]


def parse_tag(text):
    """严格解析模型输出。返回 "" 代表"不打标签"。**永远不会超过 12 字**。"""
    tags = parse_tags(text)
    return tags[0] if tags else ""


# ----------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# 幻觉闸门
# ----------------------------------------------------------------------
def _material_text(conv, me=""):
    """给模型看的全部材料拼成一串（用于校验标签里的限定词有没有出处）。"""
    conv = conv or {}
    parts = [str(me or ""), _conv_display_name(conv)]
    for smp in (conv.get("samples") or []):
        parts.append(str((smp or {}).get("sender") or ""))
        parts.append(str((smp or {}).get("text") or ""))
    return "\n".join(parts)


def guard_tags(tags, material, conv_name="", kind=""):
    """把材料里没有出处的「限定词」从标签里剔掉，防止模型瞎编。

    * 真实案例（v9.22.5）：会话是「编程老师」，模型却输出「大学老师」——
      用户根本没上大学。这里检查每个标签里的学历/学段词，材料里没出现就剥掉：
      「大学老师」→「老师」；剥完为空就丢弃整个标签。
    * 再去掉三类噪声：非关系词（考勤管理…）、和会话名一模一样的标签、
      互为子串的重复标签（「同学」被「同班同学」吃掉）。
    * `kind=="group"` 时，单个家庭成员称谓（「爸爸」）改成「家人群」——
      群是「一群人的属性」，不能拿一个成员的称呼当群标签。
    """
    low = str(material or "")
    name = str(conv_name or "").strip()
    is_group = str(kind or "").strip().lower() == "group"
    out = []
    for t in (tags or []):
        fixed = str(t or "").strip()
        if not fixed:
            continue
        for q in _QUALIFIERS:
            if q in fixed and q not in low:
                fixed = fixed.replace(q, "")
        fixed = fixed.strip(_PUNCT)
        if not fixed:
            continue
        # 群聊：成员称谓 → 家人群（「爸爸」不是群标签）
        if is_group and fixed in _MEMBER_ROLES:
            fixed = "家人群"
        # 纯职能/事件词，不是关系
        if fixed in _NOISE_WORDS:
            continue
        # 标签 == 会话名，没有任何信息量（例：「九年级12班」→ 标签也叫这个）
        if name and fixed == name:
            continue
        # 「某某班」这种班级名必须在材料里原样出现，否则就是编的
        # （实测：会话只叫「蜊江中学」，模型却输出「初四十二班」）。
        # 「班级群 / 班群 / 班级」是通用说法，不适用这条。
        if ("班" in fixed and fixed not in _GENERIC_CLASS
                and fixed not in low):
            continue
        if fixed not in out:
            out.append(fixed)
    # 互为子串的只留更具体的那个（同学 / 同班同学 / 初三同学 → 同班同学）
    kept = [t for t in out
            if not any(t != o and t in o for o in out)]
    return kept[:MAX_TAGS]


def scan_and_tag(store, ding, llm_fn, progress=None, max_convs=MAX_CONVS,
                 workers=8):
    """扫全部会话 → 逐个打标签（同步阻塞，调用方自己放线程里）。

    ``llm_fn(messages, meta) -> str``，meta 是该会话的采集结果（含 cid/name/kind）。

    * v9.22.5：
      - 并发调模型（默认 4 路）。原来是纯串行，30 个会话要等 30 次网络往返，
        用户反馈「打标签的速度好像有点慢」。写回仍是单线程串行，
        避免多线程同时改 threads.json 造成丢写。
      - 没消息样本的会话也照打（只凭会话名），不再静默跳过 ——
        用户要求「确保都要打上标签没有遗漏」。
      - 标签过一遍 guard_tags，杜绝「大学老师」这类凭空限定。
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

    # ---- 1. 筛出真正要打的（人工标签优先，绝不覆盖）
    todo = []
    for conv in convs:
        cid = str(conv.get("cid") or "").strip()
        if not cid:
            continue
        try:
            t = store.thread(cid) or {}
        except Exception:
            t = {}
        if t.get("tag") and not t.get("tag_auto"):
            continue
        todo.append(conv)

    if progress is not None:
        try:
            progress(0, total, "正在并发调用模型…")
        except Exception:
            pass

    # ---- 2. 并发调模型（只读，结果先收在内存里）
    results = {}
    done = [0]

    def _one(c):
        return llm_fn(build_prompt(c, me), c)

    pool = None
    _cf = None
    try:
        import concurrent.futures as _cf_mod
        _cf = _cf_mod
        pool = _cf.ThreadPoolExecutor(max_workers=max(1, int(workers or 1)))
    except Exception:
        pool = None

    if pool is not None:
        futs = {}
        try:
            for conv in todo:
                futs[pool.submit(_one, conv)] = conv
            for fu in _cf.as_completed(list(futs.keys())):
                conv = futs[fu]
                done[0] += 1
                if progress is not None:
                    try:
                        progress(done[0], total, str(conv.get("name") or ""))
                    except Exception:
                        pass
                try:
                    results[str(conv.get("cid"))] = fu.result()
                except Exception as e:
                    errors.append("%s：%s: %s"
                                  % (conv.get("name") or conv.get("cid"),
                                     type(e).__name__, e))
        finally:
            pool.shutdown(wait=True)
    else:
        for i, conv in enumerate(todo, 1):
            if progress is not None:
                try:
                    progress(i, total, str(conv.get("name") or ""))
                except Exception:
                    pass
            try:
                results[str(conv.get("cid"))] = _one(conv)
            except Exception as e:
                errors.append("%s：%s: %s"
                              % (conv.get("name") or conv.get("cid"),
                                 type(e).__name__, e))

    # ---- 3. 串行写回（顺序稳定，也不会并发写同一个 JSON）
    for conv in todo:
        cid = str(conv.get("cid") or "").strip()
        if not cid or cid not in results:
            continue
        name = str(conv.get("name") or "")
        tag_list = guard_tags(parse_tags(results.get(cid)),
                              _material_text(conv, me),
                              conv_display_name(conv),
                              conv.get("kind") or "")
        show_name = _conv_display_name(conv)
        try:
            if not tag_list:
                # 证据不足 → 明确落一条「未知」，界面上能看到这个会话，
                # 用户可以直接手动补标签（而不是凭空猜一个）。
                store.set_tag(cid, "", note="AI 扫描：证据不足，未打标签",
                              auto=True, name=show_name, kind=conv.get("kind"))
                continue
            store.set_tag(cid, tag_list, note="AI 扫描", auto=True,
                          name=show_name, kind=conv.get("kind"))
        except Exception as e:
            errors.append("%s：写标签失败 %s: %s"
                          % (name or cid, type(e).__name__, e))
            continue
        tagged += 1

    return {"ok": True, "reason": "", "tagged": tagged, "total": total,
            "errors": errors}
