# -*- coding: utf-8 -*-
"""通知类消息中心：识别 + 置顶清单 + 投屏通道（v9.22.12）。

用户需求（原话）
----------------
「完善通知类消息处理：将此类消息置顶并投送到电脑屏幕直接显示，需点击确认后
才能关闭。」

使用场景是学校中层（主任）：家长请假、上级临时通知、调课/检查安排这类消息
**漏一条就出事**，所以设计取向是「不点确认就一直在屏幕上盯着你」。误报的代价
只是多点一次确认，漏报的代价是事故 —— 因此识别策略刻意**宁可多认**。

三个职责
--------
1. `classify(text, keywords=None)` —— 判断一条消息算不算通知，并判出类别。
2. `NotifyStore(path)` —— 待确认清单落盘（`<工作区>/.awb/notify_pending.json`）。
3. `set_sink() / deliver()` —— 把已落盘的通知交给 GUI 投屏。

**本模块绝不 import PyQt6**
--------------------------
钉钉子/无界面进程也会 import 它（`app/im/pipeline.py` 的依赖链）。Qt 类型全部
在 `app/gui/notify_popup.py` 里，这边只留一个 `emit_sink` 回调钩子：

    _SINK = None
    def set_sink(fn): ...     # fn(dict) 在**调用线程**被回调
    def deliver(rec): ...     # 调 _SINK；没设就只落盘不投屏

`deliver()` 不阻塞调用线程，且**异常全部吞掉写日志** —— 钉钉后台线程不能被
UI 拖死，通知链路出问题也绝不能让正常回复挂掉。
"""
import json
import os
import re
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# 类别
# ---------------------------------------------------------------------------
KINDS = ("leave", "meeting", "inspect", "adjust", "task", "notice")

#: 类别中文名（GUI / 日志共用）
KIND_NAMES = {
    "leave": "请假",
    "meeting": "会议教研",
    "inspect": "检查迎检",
    "adjust": "调课代课",
    "task": "交办上报",
    "notice": "通知",
}

#: 排序权重：请假/会议/检查最要紧，其次调课/交办，最后是普通通知
KIND_WEIGHT = {
    "leave": 5,
    "meeting": 5,
    "inspect": 5,
    "adjust": 4,
    "task": 3,
    "notice": 1,
}

# 判 kind 时的优先级（命中数相同时按这个顺序取）
_KIND_ORDER = ("leave", "meeting", "inspect", "adjust", "task", "notice")

# ---------------------------------------------------------------------------
# 默认关键词表（分「强 / 弱 / 排除」三级）
# ---------------------------------------------------------------------------
#: 强通知：命中任意一个即判定为通知
DEFAULT_STRONG = {
    "leave": ("请假", "请假条", "病假", "事假", "销假"),
    "meeting": ("开会", "会议", "教研", "例会", "碰头会", "备课会", "家长会"),
    "inspect": ("检查", "迎检", "验收", "抽查", "督导", "巡查", "推门课"),
    "adjust": ("调课", "调休", "代课", "换课", "补课", "顶课"),
    "task": ("报备", "审批", "签字", "交材料", "收材料", "上报", "统计上报",
             "交表", "填表", "汇总", "打卡", "签收"),
    "notice": ("临时通知", "紧急", "务必", "请回复", "收到请回复", "尽快回复",
               "立即", "马上到", "重要通知"),
}

#: 弱通知：单个不足为凭，需要 ≥2 个不同词，或与「时间/地点/人物」句式同现
DEFAULT_WEAK = (
    "通知", "安排", "家长", "学生", "班级", "年级", "明天", "今天下午",
    "上午", "下午", "放学前", "班主任", "各班", "全体",
)

#: 排除：纯表情 / 纯问候 / 纯应答，这些绝不弹窗打扰
DEFAULT_EXCLUDE = (
    "哈哈", "哈哈哈", "哈哈哈哈", "呵呵", "嘿嘿", "嘻嘻",
    "好的", "好", "好嘞", "行", "可以", "收到", "嗯", "嗯嗯", "哦", "哦哦",
    "谢谢", "谢谢你", "多谢", "辛苦了", "辛苦", "麻烦了",
    "ok", "OK", "OKK", "好滴", "在吗", "在么", "早", "早上好", "晚安",
)

#: AI 自己在说话的内容，不能把自己发的当成通知
_SELF_SPEECH = (
    "此消息由ai", "由ai代回", "ai自动回复", "我是ai", "ai助手",
    "机器人助手", "自动回复：", "本条消息由",
)

# 句式特征：出现「时间 / 地点 / 人物」时，单个弱词也足以判定
_TIME_PAT = re.compile(
    r"(\d{1,2}\s*[:：]\s*\d{2})"                       # 14:30
    r"|(\d{1,2}\s*[点时])"                             # 3点 / 9时
    r"|(上午|下午|中午|晚上|早上|傍晚|明早|明晚)"
    r"|(今天|今日|明天|明日|后天|大后天|昨天)"
    r"|(周[一二三四五六日天]|星期[一二三四五六日天]|下周|本周|这周)"
    r"|(放学后|放学前|课前|课后|第一节课|第二节课|第三节课|第四节课)"
)
_PLACE_PAT = re.compile(
    r"(会议室|办公室|教室|操场|校门口|报告厅|阶梯教室|食堂|体育馆|门卫"
    r"|教务处|德育处|年级组|群里|楼下|楼上|多功能厅"
    r"|[\d一二三四五六七八九十]+\s*(楼|号楼|层|班|号))"
)
_PERSON_PAT = re.compile(
    r"(班主任|老师|主任|校长|副校长|书记|家长|同学|组长|教研员|各位|全体)"
)

# 清理用于「排除」判断的字符：去空白 / 标点 / 表情
_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)
_LAUGH = re.compile(r"^[哈嘿呵嘻]{2,}$")

MAX_TEXT = 2000        # 判定只看前 2000 字（长文没必要全扫）
MAX_ACKED = 200        # 已确认记录最多留 200 条
MAX_BODY = 300         # 弹窗摘要字数
LOG_MAX_BYTES = 256 * 1024


# ---------------------------------------------------------------------------
# 关键词表
# ---------------------------------------------------------------------------
def _as_tuple(value):
    """把 str / list / tuple / set 统一成去空去重的 tuple（保持原顺序）。"""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    out = []
    for item in value or ():
        word = str(item or "").strip()
        if word and word not in out:
            out.append(word)
    return tuple(out)


def normalize_keywords(keywords=None):
    """把调用方传来的关键词表规范化成 `(strong, weak, exclude)`。

    支持四种写法（设置页可以只传其中一部分）：

    Args:
        keywords: 可以是
            * None —— 用内置默认表；
            * dict，键 `strong` / `weak` / `exclude` / `kinds`，
              缺哪个用默认值补齐（`kinds` 是 `{kind: [词...]}` 的强词表，
              与 `strong` 等价，两种写法都收）；
            * dict 且键全是 kind 名 —— 等价于 `{"kinds": keywords}`；
            * list / tuple / set / str —— **整体替换强通知词**，弱词与排除词
              仍用默认（这是"我知道要比默认更敏感"的常见写法）。

    Returns:
        tuple(dict[str, tuple[str, ...]], tuple[str, ...], tuple[str, ...])：
        `(strong_map, weak, exclude)`，其中 `strong_map` 是 `kind -> 词表`。
    """
    if keywords is None:
        strong_map = {k: tuple(v) for k, v in DEFAULT_STRONG.items()}
        return strong_map, tuple(DEFAULT_WEAK), tuple(DEFAULT_EXCLUDE)

    if isinstance(keywords, dict):
        # ① 纯 {kind: [词...]} 写法
        keys = [str(k) for k in keywords.keys()]
        if keys and all(k in KINDS for k in keys) and "strong" not in keys:
            return (_merge_strong(None, keywords),
                    tuple(DEFAULT_WEAK), tuple(DEFAULT_EXCLUDE))

        strong_map = _merge_strong(keywords.get("strong"), keywords.get("kinds"))
        weak = keywords.get("weak", DEFAULT_WEAK)
        exclude = keywords.get("exclude", DEFAULT_EXCLUDE)
        return strong_map, _as_tuple(weak), _as_tuple(exclude)

    # ② 列表 / 字符串：整体替换强词
    return _merge_strong(keywords, None), tuple(DEFAULT_WEAK), tuple(DEFAULT_EXCLUDE)


def _merge_strong(strong, kinds):
    """把 `strong`（列表，可写 `leave:请假` 前缀）与 `kinds`（kind->词表）合成表。"""
    out = {}

    def _put(kind, word):
        kind = str(kind or "notice")
        if kind not in KINDS:
            kind = "notice"
        word = str(word or "").strip()
        if not word:
            return
        bucket = out.setdefault(kind, [])
        if word not in bucket:
            bucket.append(word)

    if isinstance(kinds, dict):
        for kind, words in kinds.items():
            for word in _as_tuple(words):
                _put(kind, word)

    for word in _as_tuple(strong):
        if ":" in word or "：" in word:
            head, _, tail = word.replace("：", ":").partition(":")
            if head.strip() in KINDS and tail.strip():
                _put(head.strip(), tail)
                continue
            # 不是 kind 前缀 → 整条当普通词（比如「通知：xxx」这种真实词）
        _put("notice", word)

    # 空表时退回默认，避免"传了空 dict 就什么都认不出"
    return {k: tuple(v) for k, v in out.items()} or {
        k: tuple(v) for k, v in DEFAULT_STRONG.items()
    }


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------
def _hit(word, text):
    """`word` 是否出现在 `text` 里（大小写不敏感，兼容全角空格）。"""
    if not word:
        return False
    return word.lower() in text


def _excluded(clean, exclude=None):
    """纯表情 / 纯问候 / 纯应答 → True（这些不打扰用户）。"""
    if not clean:
        return True
    if len(clean) <= 1:
        return True
    if _LAUGH.match(clean):
        return True
    low = clean.lower()
    for word in (exclude or DEFAULT_EXCLUDE):
        if low == str(word).lower():
            return True
    return False


def classify(text, keywords=None):
    """判断一条消息是不是「需要置顶 + 必须确认」的通知。

    策略：**保守但宁可多认** —— 这是"怕漏"的场景，误报的代价（多点一次确认）
    远小于漏报的代价（错过请假/检查/调课）。

    判定顺序：
        1. 空文本 / 纯表情 / 纯问候 / 纯应答 → 不是通知
        2. AI 自己说的话（"此消息由AI代回"等）→ 不是通知
        3. 命中任一「强通知」词 → **是通知**（并按类别计分取最高的 kind）
        4. 命中 ≥2 个不同「弱通知」词 → 是通知
        5. 命中 1 个弱词，且同时有「时间 / 地点 / 人物」句式 → 是通知
        6. 其余 → 不是通知

    Args:
        text: 消息正文。
        keywords: 关键词表覆盖，写法见 `normalize_keywords()`。

    Returns:
        dict:
            is_notify  bool   —— 要不要置顶+投屏
            kind       str    —— leave / meeting / inspect / adjust / task / notice
            score      float  —— 0.0~1.0 的置信度（给界面排序/显示用）
            hits       list   —— `[{"word","kind","level"}]`，命中了哪些词
    """
    raw = str(text or "").strip()
    body = raw[:MAX_TEXT]
    low = body.lower()

    def _no(reason=""):
        return {"is_notify": False, "kind": "notice", "score": 0.0,
                "hits": [], "reason": reason}

    if not body:
        return _no("空消息")

    for mark in _SELF_SPEECH:
        if mark in low:
            return _no("AI 自己发的")

    clean = _NON_WORD.sub("", body)
    strong_map, weak, exclude = normalize_keywords(keywords)
    if _excluded(clean, exclude):
        return _no("问候/应答/表情")

    # ---- 强词：命中即判定 ----
    hits = []
    kind_count = {}
    for kind in _KIND_ORDER:
        for word in strong_map.get(kind, ()):  # 按 kind 顺序扫，便于日志可读
            if _hit(word, low):
                hits.append({"word": word, "kind": kind, "level": "strong"})
                kind_count[kind] = kind_count.get(kind, 0) + 1
    if hits:
        # 命中数最多者胜；打平按 _KIND_ORDER（请假 > 会议 > 检查 > 调课 > 交办 > 通知）
        best = max(_KIND_ORDER,
                   key=lambda k: (kind_count.get(k, 0),
                                  -_KIND_ORDER.index(k)))
        score = min(1.0, 0.80 + 0.05 * (len(hits) - 1))
        return {"is_notify": True, "kind": best, "score": round(score, 3),
                "hits": hits, "reason": "命中强通知词：%s" % hits[0]["word"]}

    # ---- 弱词 ----
    weak_hits = []
    for word in weak:
        if _hit(word, low) and word not in weak_hits:
            weak_hits.append(word)
        if len(weak_hits) >= 8:      # 够了，别在超长文里刷出几百个
            break
    if not weak_hits:
        return _no("没有通知特征")

    has_sentence = bool(_TIME_PAT.search(body) or _PLACE_PAT.search(body)
                        or _PERSON_PAT.search(body))

    for word in weak_hits:
        hits.append({"word": word, "kind": "notice", "level": "weak"})

    kind = _guess_kind_from_weak(weak_hits)
    if len(weak_hits) >= 2:
        score = min(0.75, 0.55 + 0.05 * (len(weak_hits) - 2))
        return {"is_notify": True, "kind": kind, "score": round(score, 3),
                "hits": hits, "reason": "命中 %d 个弱通知词" % len(weak_hits)}
    if has_sentence:
        return {"is_notify": True, "kind": kind, "score": 0.50,
                "hits": hits, "reason": "弱通知词 + 时间/地点/人物句式"}
    return {"is_notify": False, "kind": kind, "score": 0.20,
            "hits": hits, "reason": "只有一个弱词，且无句式支撑"}


def _guess_kind_from_weak(weak_hits):
    """弱词判不出细分类别 —— 只能落在「其它通知」里。

    这是刻意的：弱词（通知/安排/家长/明天…）本身没有语义指向，"家长"既可能
    对应请假也可能对应家长会。与其瞎猜一个高优先级类别（会打乱排序权重），
    不如老实记成 `notice`；等强词出现时自然会被判成正确的类别。
    """
    return "notice"


def is_notify(text, keywords=None):
    """只想知道「要不要弹」时的简写。"""
    return bool(classify(text, keywords).get("is_notify"))


def summary(text, limit=MAX_BODY):
    """弹窗摘要：压掉多余空白，超长截断加省略号。"""
    body = re.sub(r"[ \t\u3000]+", " ", str(text or "")).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# 落盘：待确认清单
# ---------------------------------------------------------------------------
_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path):
    """同一个 JSON 文件在**同一进程内**共用一把可重入锁（多实例也不会互相覆盖）。"""
    key = os.path.normcase(os.path.abspath(str(path)))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def default_store_path(workspace=None):
    """通知清单的默认位置：`<工作区>/.awb/notify_pending.json`。

    Args:
        workspace: 工作区根目录；不传则按 环境变量 AIWORKBENCH_WORKSPACE →
            `app.workspace.get_last_workspace()` → 用户目录 的顺序退让。
    """
    root = str(workspace or "").strip()
    if not root:
        root = str(os.environ.get("AIWORKBENCH_WORKSPACE") or "").strip()
    if not root:
        try:
            from ..workspace import get_last_workspace  # 延迟导入，避免循环依赖
            root = str(get_last_workspace() or "").strip()
        except Exception:
            root = ""
    if not root:
        root = os.path.expanduser("~")
    return os.path.join(root, ".awb", "notify_pending.json")


def _norm_item(item):
    """把一条记录补齐成标准字段（读盘/写入共用，防止脏数据扩散）。"""
    item = dict(item or {})
    kind = str(item.get("kind") or "notice").strip()
    if kind not in KINDS:
        kind = "notice"
    try:
        ts = float(item.get("ts") or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        ts = time.time()
    acked_ts = item.get("acked_ts")
    try:
        acked_ts = float(acked_ts) if acked_ts else 0.0
    except Exception:
        acked_ts = 0.0
    return {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "conv_id": str(item.get("conv_id") or item.get("cid") or ""),
        "conv_title": str(item.get("conv_title") or item.get("conv") or ""),
        "sender": str(item.get("sender") or ""),
        "msgid": str(item.get("msgid") or ""),
        "text": str(item.get("text") or ""),
        "ts": ts,
        "kind": kind,
        "score": float(item.get("score") or 0.0),
        "acked": bool(item.get("acked")),
        "acked_ts": acked_ts,
    }


class NotifyStore:
    """待确认通知的持久化清单（JSON，原子写，损坏可容错）。

    文件结构：`{"version": 1, "items": [...]}`；每条记录：
    `id / conv_id / conv_title / sender / msgid / text / ts / kind / score /
    acked / acked_ts`。

    设计要点：
      * **原子写**：先写 `.tmp` 再 `os.replace`，断电/崩溃不会留半个 JSON；
      * **读坏容错**：文件损坏就当空表（并备份成 `.bad`），绝不抛异常；
      * **不丢未确认**：只清理超过 200 条的**已确认**记录，未确认的永不自动清；
      * **进程内多实例安全**：同一路径共用一把 `RLock`，且每次操作前按
        `(mtime_ns, size)` 校验缓存，pipeline 线程与 GUI 各建一个实例也不会
        互相覆盖。
    """

    def __init__(self, path=None):
        self.path = os.path.abspath(str(path or default_store_path()))
        self._lock = _lock_for(self.path)
        self._cache = None
        self._stamp = None
        self._bad_backed = False

    # ---------- 内部：读 ----------
    def _read(self):
        """读盘（调用方必须已持锁）。返回新列表，调用方可随意改。"""
        path = self.path
        try:
            st = os.stat(path)
            stamp = (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            self._cache, self._stamp = [], None
            return []

        if self._cache is not None and stamp == self._stamp:
            return [dict(x) for x in self._cache]

        items = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("items") if isinstance(data, dict) else data
            if isinstance(raw, list):
                for it in raw:
                    if isinstance(it, dict) and str(it.get("text") or "").strip():
                        items.append(_norm_item(it))
        except Exception as exc:
            log("通知清单读取失败（当空表处理）：%s: %s"
                % (type(exc).__name__, exc))
            self._backup_bad()
            items = []

        self._cache, self._stamp = items, stamp
        return [dict(x) for x in items]

    def _backup_bad(self):
        """把损坏的清单另存一份，方便事后排查（只做一次）。"""
        if self._bad_backed:
            return
        self._bad_backed = True
        try:
            if os.path.exists(self.path):
                os.replace(self.path, self.path + ".bad")
        except Exception:
            pass

    # ---------- 内部：写 ----------
    def _write(self, items):
        """原子写（调用方必须已持锁）。"""
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".tmp"
        payload = {"version": 1, "items": list(items)}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, self.path)
        try:
            st = os.stat(self.path)
            self._stamp = (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            self._stamp = None
        self._cache = [dict(x) for x in items]

    @staticmethod
    def _prune(items):
        """只裁掉多余的**已确认**记录（保留最近 200 条），未确认的一条不动。"""
        acked = [it for it in items if it.get("acked")]
        if len(acked) <= MAX_ACKED:
            return list(items)
        acked.sort(key=lambda it: float(it.get("acked_ts") or it.get("ts") or 0),
                   reverse=True)
        keep = set(id(it) for it in acked[:MAX_ACKED])
        keep.update(id(it) for it in items if not it.get("acked"))
        return [it for it in items if id(it) in keep]

    # ---------- 公开 API ----------
    def add(self, rec=None, **kwargs):
        """新增一条待确认通知（同一 `conv_id + msgid` 自动去重）。

        Args:
            rec: 记录 dict（字段见类文档）。
            **kwargs: 追加/覆盖字段（`text` 必填）。

        Returns:
            dict —— 落盘后的记录（含生成的 `id`）；`text` 为空则返回 None。
        """
        return self.add_ex(rec, **kwargs)[0]

    def add_ex(self, rec=None, **kwargs):
        """`add()` 的加强版，额外告诉调用方「这条是不是新建的」。

        Returns:
            tuple(dict|None, bool) —— `(记录, 是否新建)`。`是否新建=False` 表示
            命中了 `conv_id + msgid` 去重（这条以前就投过），调用方**不应该**
            再弹一次窗。
        """
        data = {}
        if isinstance(rec, dict):
            data.update(rec)
        data.update(kwargs)
        if not str(data.get("text") or "").strip():
            return None, False

        with self._lock:
            items = self._read()
            conv_id = str(data.get("conv_id") or data.get("cid") or "")
            msgid = str(data.get("msgid") or "")
            if msgid:
                for it in items:
                    if it.get("msgid") == msgid and it.get("conv_id") == conv_id:
                        return dict(it), False       # 同一条消息重复投递：不重复弹
            item = _norm_item(dict(data, id=data.get("id") or uuid.uuid4().hex))
            items.append(item)
            items = self._prune(items)
            try:
                self._write(items)
            except Exception as exc:
                log("通知清单写入失败（内存已记）：%s: %s"
                    % (type(exc).__name__, exc))
                self._cache = [dict(x) for x in items]
            return dict(item), True

    def pending(self):
        """未确认的通知，按 `kind` 权重 → `ts` 倒序。

        Returns:
            list[dict]：最重要的、最新的排在前面。
        """
        with self._lock:
            items = self._read()
        pend = [it for it in items if not it.get("acked")]
        pend.sort(key=lambda it: (-KIND_WEIGHT.get(it.get("kind"), 1),
                                  -float(it.get("ts") or 0)))
        return pend

    def acked(self):
        """已确认的通知（按确认时间倒序）。"""
        with self._lock:
            items = self._read()
        done = [it for it in items if it.get("acked")]
        done.sort(key=lambda it: -float(it.get("acked_ts") or it.get("ts") or 0))
        return done

    def all(self):
        """全部记录（调试/审查用）。"""
        with self._lock:
            items = self._read()
        return items

    def get(self, nid):
        """按 id 取一条记录，没有就 None。"""
        nid = str(nid or "")
        if not nid:
            return None
        with self._lock:
            for it in self._read():
                if it.get("id") == nid:
                    return dict(it)
        return None

    def ack(self, nid, ts=None):
        """确认一条（幂等）。

        Args:
            nid: 记录 id。
            ts: 确认时间戳，默认 `time.time()`。

        Returns:
            bool —— 是否真的从「未确认」改成了「已确认」。
        """
        nid = str(nid or "")
        if not nid:
            return False
        when = float(ts) if ts else time.time()
        with self._lock:
            items = self._read()
            hit = None
            for it in items:
                if it.get("id") == nid:
                    hit = it
                    break
            if hit is None:
                return False
            if hit.get("acked"):
                return False
            hit["acked"] = True
            hit["acked_ts"] = when
            try:
                self._write(self._prune(items))
            except Exception as exc:
                log("通知确认写盘失败：%s: %s" % (type(exc).__name__, exc))
                self._cache = [dict(x) for x in items]
            return True

    def ack_all(self, ts=None):
        """全部确认。

        Returns:
            int —— 这次确认了多少条。
        """
        when = float(ts) if ts else time.time()
        with self._lock:
            items = self._read()
            n = 0
            for it in items:
                if not it.get("acked"):
                    it["acked"] = True
                    it["acked_ts"] = when
                    n += 1
            if n:
                try:
                    self._write(self._prune(items))
                except Exception as exc:
                    log("通知全部确认写盘失败：%s: %s" % (type(exc).__name__, exc))
                    self._cache = [dict(x) for x in items]
            return n

    def stats(self):
        """统计（界面角标 / 日志用）。

        Returns:
            dict: `total / pending / acked / by_kind / oldest_pending_ts /
            newest_pending_ts / path`。
        """
        with self._lock:
            items = self._read()
        pend = [it for it in items if not it.get("acked")]
        by_kind = {}
        for it in pend:
            by_kind[it.get("kind") or "notice"] = \
                by_kind.get(it.get("kind") or "notice", 0) + 1
        ts_list = [float(it.get("ts") or 0) for it in pend]
        return {
            "total": len(items),
            "pending": len(pend),
            "acked": len(items) - len(pend),
            "by_kind": by_kind,
            "oldest_pending_ts": min(ts_list) if ts_list else 0.0,
            "newest_pending_ts": max(ts_list) if ts_list else 0.0,
            "path": self.path,
        }

    def prune(self):
        """按规则裁剪一次（正常 `add` 时已自动做，这里供外部强制调用）。"""
        with self._lock:
            items = self._read()
            kept = self._prune(items)
            if len(kept) != len(items):
                try:
                    self._write(kept)
                except Exception as exc:
                    log("通知清单裁剪写盘失败：%s: %s" % (type(exc).__name__, exc))
            return len(items) - len(kept)


# ---------------------------------------------------------------------------
# 日志（不能因为写日志失败而影响通知本身）
# ---------------------------------------------------------------------------
_LOG_CB = None
_LOG_PATH_CACHE = {}


def set_log(fn):
    """接一个日志回调（`fn(line)`）；设为 None 恢复写文件。"""
    global _LOG_CB
    _LOG_CB = fn if callable(fn) else None


def _log_path():
    """`<清单目录>/notify.log`，算一次就缓存。"""
    try:
        folder = os.path.dirname(default_store_path())
        key = os.path.normcase(folder)
        if key in _LOG_PATH_CACHE:
            return _LOG_PATH_CACHE[key]
        path = os.path.join(folder, "notify.log")
        _LOG_PATH_CACHE[key] = path
        return path
    except Exception:
        return ""


def log(text):
    """写一行日志。**绝不抛异常**（通知链路的任何故障都不许放大）。"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), str(text))
    cb = _LOG_CB
    if cb is not None:
        try:
            cb(line)
            return
        except Exception:
            pass
    try:
        path = _log_path()
        if not path:
            return
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 投屏通道（emit_sink）
# ---------------------------------------------------------------------------
_SINK = None
_STORE = None
_KEYWORDS = None


def set_sink(fn):
    """设置投屏回调：`fn(rec)` 会在**调用 `deliver()` 的那个线程**里执行。

    GUI 侧必须传一个线程安全的入口（`NotifyCenter.notify_arrived.emit` 这种
    Qt 信号最合适 —— 跨线程会自动排队到主线程）。传 None 表示只落盘不投屏。
    """
    global _SINK
    _SINK = fn if callable(fn) else None
    return _SINK


def get_sink():
    """当前投屏回调（没设就是 None）。"""
    return _SINK


def set_store(store):
    """换掉全局清单实例（设置页/测试用）。"""
    global _STORE
    _STORE = store
    return _STORE


def get_store():
    """全局清单实例（懒创建在默认路径）。"""
    global _STORE
    if _STORE is None:
        try:
            _STORE = NotifyStore(default_store_path())
        except Exception as exc:
            log("通知清单初始化失败：%s: %s" % (type(exc).__name__, exc))
            return None
    return _STORE


def set_keywords(keywords):
    """全局关键词表（设置页改完调一次即可，之后 `deliver` 用它识别）。"""
    global _KEYWORDS
    _KEYWORDS = keywords
    return _KEYWORDS


def get_keywords():
    """当前全局关键词表（None = 内置默认）。"""
    return _KEYWORDS


def make_record(msg, verdict=None):
    """把 pipeline 的原始消息转成清单记录（字段与 `NotifyStore.add()` 对齐）。

    Args:
        msg: `{cid/conv_id, conv/conv_title, sender, text, msgid, recv_ts/ts}`。
        verdict: `classify()` 的结果；不传就现算。

    Returns:
        dict：`conv_id / conv_title / sender / msgid / text / ts / kind /
        score / acked`。
    """
    msg = dict(msg or {})
    text = str(msg.get("text") or "").strip()
    verdict = verdict or classify(text, _KEYWORDS)
    try:
        ts = float(msg.get("recv_ts") or msg.get("ts") or 0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        ts = time.time()
    return {
        "id": uuid.uuid4().hex,
        "conv_id": str(msg.get("conv_id") or msg.get("cid") or ""),
        "conv_title": str(msg.get("conv_title") or msg.get("conv") or ""),
        "sender": str(msg.get("sender") or ""),
        "msgid": str(msg.get("msgid") or ""),
        "text": text,
        "ts": ts,
        "kind": str(verdict.get("kind") or "notice"),
        "score": float(verdict.get("score") or 0.0),
        "acked": False,
        "acked_ts": 0.0,
    }


def deliver(rec, force=False):
    """识别 → 落盘 → 投屏。**绝不抛异常，绝不阻塞调用线程**。

    Args:
        rec: 记录 dict（用 `make_record()` 生成最省事）。
        force: True 表示跳过识别，强制当通知处理（人工置顶用）。

    Returns:
        dict|None —— 落盘后的记录；判定不是通知 / 没有正文 → None。
    """
    try:
        rec = dict(rec or {})
    except Exception:
        return None
    text = str(rec.get("text") or "").strip()
    if not text:
        return None

    try:
        if force:
            verdict = {"is_notify": True,
                       "kind": str(rec.get("kind") or "notice"),
                       "score": float(rec.get("score") or 1.0)}
        elif rec.get("kind") in KINDS and rec.get("is_notify"):
            # 调用方已经判过了（`make_record` 的产物）—— 不再重复识别
            verdict = {"is_notify": True, "kind": rec.get("kind"),
                       "score": float(rec.get("score") or 0.0)}
        else:
            verdict = classify(text, _KEYWORDS)
        if not verdict.get("is_notify"):
            return None
        rec["kind"] = str(verdict.get("kind") or "notice")
        rec["score"] = float(verdict.get("score") or 0.0)
        rec["is_notify"] = True
    except Exception as exc:
        log("通知识别失败：%s: %s" % (type(exc).__name__, exc))
        return None

    # ① 落盘（失败也要继续投屏 —— 屏幕上盯住比落盘更重要）
    saved = None
    created = True
    try:
        store = get_store()
        if store is not None:
            saved, created = store.add_ex(rec)
    except Exception as exc:
        log("通知落盘失败（继续投屏）：%s: %s" % (type(exc).__name__, exc))
    if saved is None:
        try:
            saved = _norm_item(rec)
        except Exception:
            return None
    if not created:
        # 同一条消息以前就投过（同 conv_id + msgid）—— 不重复打扰
        return saved

    # ② 投屏（回调是 Qt 信号 emit，跨线程只会排队，不会阻塞这里）
    sink = _SINK
    if sink is not None:
        try:
            sink(dict(saved))
        except Exception as exc:
            log("通知投屏失败：%s: %s" % (type(exc).__name__, exc))
    else:
        log("通知已落盘（未接 GUI，未投屏）：%s / %s"
            % (saved.get("sender") or "?", summary(saved.get("text"), 60)))
    return saved


def deliver_message(msg, force=False):
    """`deliver()` 的便捷版：直接把 pipeline 的原始消息交进来。"""
    try:
        return deliver(make_record(msg), force=force)
    except Exception as exc:
        log("通知投递失败：%s: %s" % (type(exc).__name__, exc))
        return None


def pending_count():
    """未确认数量（界面角标用）。"""
    try:
        store = get_store()
        return len(store.pending()) if store is not None else 0
    except Exception:
        return 0


def _reset_for_tests():
    """测试用：清空全局单例（不动磁盘）。"""
    global _SINK, _STORE, _KEYWORDS, _LOG_CB
    _SINK = None
    _STORE = None
    _KEYWORDS = None
    _LOG_CB = None
    _LOG_PATH_CACHE.clear()
    return True
