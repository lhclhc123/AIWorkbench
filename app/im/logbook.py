# -*- coding: utf-8 -*-
"""日志本：从聊天里抽出「谁 · 哪天 · 干什么」（v9.20.0）。

用户原话：「他支持钉钉，能记录日志吗？就比如说哪天是谁值日着，让他去帮我
抓取一下记录一下，就是哪天是谁干什么的，然后能查看管理及审查」。

做法：**规则优先**（快、离线、可解释），抽不出来再交给模型（可选）。
规则覆盖的常见写法：

    明天值日：张三、李四
    今天值日 张三
    周三 擦黑板：王五
    张三 明天 倒垃圾
    本周值日 张三 李四 王五
    下周一 扫地 赵六

抽出来的结构：`{who, what, day, day_text}`，`day` 是解析后的 `YYYY-MM-DD`
（解析不出来就留空，只保留 `day_text` 原文 —— 宁可少记，不要瞎猜）。
"""
import re
import time

# 相对日
_REL_DAYS = {
    "今天": 0, "今日": 0, "本日": 0,
    "明天": 1, "明日": 1, "明儿": 1,
    "后天": 2, "大后天": 3,
    "昨天": -1, "昨日": -1, "前天": -2,
}
# 星期（Python: 周一=0）
_WEEK_DAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
              "日": 6, "天": 6, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
              "6": 5, "7": 6}

# 常见"干什么"的词（命中任意一个就认为这是任务描述）
_TASK_WORDS = (
    "值日", "打扫", "扫地", "拖地", "擦黑板", "倒垃圾", "擦窗", "擦桌子",
    "搬水", "打水", "浇水", "整理", "收作业", "发作业", "检查", "巡查",
    "站岗", "执勤", "升旗", "领读", "带读", "背书", "默写", "考试", "开会",
    "复盘", "交表", "填表", "签到", "值班", "锁门", "开门", "关灯", "关窗",
    "消毒", "报修", "维修", "采购", "布置", "排练", "彩排",
)

_NAME_STOP = set("的了和跟与及、,，。.;；:：!！?？()（）[]【】 \t\n\r\"'“”‘’")


def _date_of(now, offset_days):
    return time.strftime("%Y-%m-%d", time.localtime(now + offset_days * 86400))


def parse_day(text, now=None):
    """从一句话里找"哪天"。返回 (day_str|"", day_text)。"""
    now = time.time() if now is None else float(now)
    t = str(text or "")
    for w, off in _REL_DAYS.items():
        if w in t:
            return _date_of(now, off), w
    m = re.search(r"(下下|下|本|这)?(?:周|星期|礼拜)([一二三四五六日天1-7])", t)
    if m:
        prefix = m.group(1) or ""
        target = _WEEK_DAYS.get(m.group(2), 0)
        lt = time.localtime(now)
        cur = lt.tm_wday
        if prefix == "下下":
            delta = (target - cur) % 7 + 7
        elif prefix == "下":
            # "下周三"：下一个自然周里的周三
            delta = (target - cur) % 7
            if delta == 0:
                delta = 7
        else:
            delta = (target - cur) % 7
        return _date_of(now, delta), m.group(0)
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        try:
            y = time.localtime(now).tm_year
            return time.strftime("%Y-%m-%d", (y, int(m.group(1)), int(m.group(2)),
                                              0, 0, 0, 0, 0, -1)), m.group(0)
        except Exception:
            return "", m.group(0)
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        try:
            return time.strftime("%Y-%m-%d", (int(m.group(1)), int(m.group(2)),
                                              int(m.group(3)), 0, 0, 0, 0, 0, -1)), m.group(0)
        except Exception:
            return "", m.group(0)
    return "", ""


def _find_task(text):
    t = str(text or "")
    for w in _TASK_WORDS:
        if w in t:
            return w
    return ""


def _split_names(chunk):
    """把 `张三、李四 王五,赵六` 切成名字列表。"""
    s = str(chunk or "")
    for sep in ("、", "，", ",", "和", "跟", "与", "及", "/", " ", "\t"):
        s = s.replace(sep, "|")
    out = []
    for p in s.split("|"):
        p = p.strip(" " + "".join(_NAME_STOP))
        # 名字一般 2~4 个汉字
        if 2 <= len(p) <= 4 and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z·]{2,4}", p):
            if p not in out:
                out.append(p)
    return out


def extract(text, now=None):
    """从一段话里抽记录。返回 [{who, what, day, day_text}]。"""
    t = str(text or "").strip()
    if not t or len(t) > 500:
        return []
    what = _find_task(t)
    # 「干什么」必须能认出来，否则不记（避免把闲聊当日志）
    if not what:
        return []
    day, day_text = parse_day(t, now)

    # ① `明天值日：张三、李四` / `周三 擦黑板：王五` —— 冒号后是名单
    m = re.search(r"[:：]\s*([^。;；\n]{2,80})", t)
    names = []
    if m:
        names = _split_names(m.group(1))
    # ② `今天值日 张三` —— 任务词后面直接跟名单
    if not names:
        idx = t.find(what)
        tail = t[idx + len(what):] if idx >= 0 else ""
        names = _split_names(tail)
    # ③ `张三 明天 倒垃圾` —— 任务词前面是名单
    if not names:
        idx = t.find(what)
        head = t[:idx] if idx > 0 else ""
        head = re.sub(r"(今天|明天|后天|大后天|昨天|前天)", " ", head)
        head = re.sub(r"(下下|下|本|这)?(?:周|星期|礼拜)[一二三四五六日天1-7]", " ", head)
        names = _split_names(head)
    if not names:
        return []
    return [{"who": n, "what": what or "值日", "day": day, "day_text": day_text}
            for n in names]


def record(store, thread_id, text, now=None):
    """抽取并落盘，返回新增条数。"""
    got = extract(text, now)
    for r in got:
        store.add_log(r["who"], r["what"], r["day"] or r["day_text"],
                      thread_id=thread_id, raw=text)
    return len(got)


def render(rows, limit=50, title="日志本"):
    """给界面看的纯文本视图。"""
    rows = list(rows or [])[:max(1, int(limit or 50))]
    if not rows:
        return f"# {title}\n（还没有记录）"
    lines = [f"# {title}（{len(rows)} 条）"]
    by_day = {}
    for r in rows:
        by_day.setdefault(str(r.get("day") or "未标注日期"), []).append(r)
    for day in sorted(by_day.keys(), reverse=True):
        lines.append(f"\n## {day}")
        for r in by_day[day]:
            lines.append(f"- {r.get('who')}：{r.get('what')}")
    return "\n".join(lines)


def who_on(store, day=None, now=None):
    """某天谁干什么（回答"哪天谁值日"）。"""
    day = day or time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    rows = store.query_log(day=day)
    return rows
