# -*- coding: utf-8 -*-
"""定时主动消息（每日 / 每周 / 每月）—— v9.20.0。

和「任务页」的定时任务不同：这里发的是**钉钉消息**（到点主动说一句话），
比如"每周一 08:30 在班群里发本周值日安排"。

判定逻辑刻意做成**纯函数**，方便直接拿假时间戳测：

    due_tasks(tasks, now) -> [task...]

规则：
- `freq`: daily / weekly / monthly
- `at`: "HH:MM"（24 小时制，本地时间）
- `weekday`: 0=周一 … 6=周日（weekly 用，**老字段，继续支持**）
- `weekdays`: [0,1,2]（v9.23.0 新增；weekly 用，多个星期几。
  有这个字段时优先于 `weekday`，这样「工作日」= [0,1,2,3,4]）
- `day`: 1~31（monthly 用；当月没有 31 号就落在最后一天）
- 同一天同一条任务只触发一次（靠 `last_run` 的日期比较，而不是"距上次多久"）——
  否则程序重启、睡眠唤醒都会重发。

v9.23.0 增强
------------
新增 ``parse_expr(expr)``，与「任务页」共用同一套表达式语法（只取其中和
"按日历说话"有关的部分）::

    daily@08:30            每天
    workday@07:50          工作日（周一~周五）
    weekly@MO,WE,FR@16:00  每周一/三/五
    monthly@15@08:30       每月 15 号

``every@…`` / ``once@…`` 在这里**不支持**（定时消息是"按日历说一句话"，
没有"每 15 分钟说一次"这种场景），会返回明确的中文错误。
老配置（只有 ``freq``/``at``/``weekday``/``day``）行为**完全不变**。
"""
import time

FREQS = ("daily", "weekly", "monthly")

# ISO 星期（1=周一 … 7=周日）-> Python tm_wday（0=周一 … 6=周日）
_ISO_TO_TMWDAY = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}

_WEEKDAY_TOKENS = {
    "MO": 1, "MON": 1, "TU": 2, "TUE": 2, "WE": 3, "WED": 3,
    "TH": 4, "THU": 4, "FR": 5, "FRI": 5, "SA": 6, "SAT": 6,
    "SU": 7, "SUN": 7, "日": 7, "天": 7,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
}


def _hhmm_strict(text):
    """严格解析 ``HH:MM``；不合法返回 None。"""
    s = str(text or "").strip().replace("：", ":")
    s = s.replace("点", ":").replace("时", ":").replace("分", "")
    s = s.strip(": ")
    parts = s.split(":")
    if len(parts) < 2 or len(parts) > 3:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if parts[1] != "" else 0
    except Exception:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def _parse_days(text):
    """``MO,WE`` / ``周一,周三`` / ``1,3`` -> tm_wday 列表；坏 token 返回 None。"""
    s = str(text or "").strip().replace("，", ",").replace(" ", "")
    if not s:
        return None
    for cn, num in (("周一", "1"), ("周二", "2"), ("周三", "3"), ("周四", "4"),
                    ("周五", "5"), ("周六", "6"), ("周日", "7"), ("周天", "7")):
        s = s.replace(cn, num)
    iso = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        up = part.upper()
        if up in _WEEKDAY_TOKENS:
            iso.append(_WEEKDAY_TOKENS[up])
            continue
        try:
            n = int(part)
        except Exception:
            return None
        if not 1 <= n <= 7:
            return None
        iso.append(n)
    if not iso:
        return None
    return sorted({_ISO_TO_TMWDAY[d] for d in iso})


def parse_expr(expr):
    """解析调度表达式 -> 定时消息字段。

    返回 ``{"ok", "error", "expr", "freq", "at", "weekday", "weekdays",
    "day", "label"}``，**永不抛异常**。
    """
    raw = str(expr or "").strip()
    empty = {"ok": False, "error": "", "expr": raw, "freq": "", "at": "",
             "weekday": 0, "weekdays": [], "day": 1, "label": ""}
    if not raw:
        empty["error"] = "调度表达式为空（示例：daily@08:30）"
        return empty
    if "@" not in raw:
        empty["error"] = (f"无法识别「{raw}」：缺少「@」，"
                          f"示例 daily@08:30 / weekly@MO,WE@16:00")
        return empty
    head, _, tail = raw.partition("@")
    kind = head.strip().lower()
    tail = tail.strip()

    if kind in ("daily", "workday"):
        at = _hhmm_strict(tail)
        if not at:
            empty["error"] = f"「{tail}」不是合法时间（应写成 08:30 这种）"
            return empty
        out = {"ok": True, "error": "", "expr": raw, "freq": "daily", "at": at,
               "weekday": 0, "weekdays": [], "day": 1, "label": "每天 " + at}
        if kind == "workday":
            out["freq"] = "weekly"
            out["weekdays"] = [0, 1, 2, 3, 4]
            out["weekday"] = 0
            out["label"] = "工作日 " + at
        return out

    if kind == "weekly":
        days_part, sep, time_part = tail.rpartition("@")
        if not sep:
            empty["error"] = (f"「{raw}」格式应为 weekly@MO,WE@16:00"
                              f"（星期与时间之间要有 @）")
            return empty
        days = _parse_days(days_part)
        if not days:
            empty["error"] = (f"「{days_part}」不是合法的星期"
                              f"（用 MO,TU,WE,TH,FR,SA,SU 或 1~7）")
            return empty
        at = _hhmm_strict(time_part)
        if not at:
            empty["error"] = f"「{time_part}」不是合法时间（应写成 16:00 这种）"
            return empty
        names = ["一", "二", "三", "四", "五", "六", "日"]
        label = "、".join("周" + names[d] for d in days) + " " + at
        return {"ok": True, "error": "", "expr": raw, "freq": "weekly",
                "at": at, "weekday": days[0], "weekdays": list(days),
                "day": 1, "label": label}

    if kind == "monthly":
        day_part, sep, time_part = tail.rpartition("@")
        if not sep:
            empty["error"] = (f"「{raw}」格式应为 monthly@15@08:30"
                              f"（日期与时间之间要有 @）")
            return empty
        try:
            day = int(str(day_part).strip().replace("号", "").replace("日", ""))
        except Exception:
            empty["error"] = f"「{day_part}」不是合法的日期（1~31）"
            return empty
        if not 1 <= day <= 31:
            empty["error"] = f"「{day_part}」超出范围（1~31）"
            return empty
        at = _hhmm_strict(time_part)
        if not at:
            empty["error"] = f"「{time_part}」不是合法时间（应写成 08:30 这种）"
            return empty
        return {"ok": True, "error": "", "expr": raw, "freq": "monthly",
                "at": at, "weekday": 0, "weekdays": [], "day": day,
                "label": f"每月 {day} 号 {at}"}

    if kind in ("every", "once"):
        empty["error"] = (f"定时消息不支持「{kind}」：它按日历触发，"
                          f"请用 daily / workday / weekly / monthly")
        return empty
    empty["error"] = (f"不支持的调度类型「{head}」"
                      f"（可用 daily / workday / weekly / monthly）")
    return empty


def apply_expr(task, expr):
    """把表达式写进任务 dict；成功返回 ""，失败返回中文错误。"""
    p = parse_expr(expr)
    if not p.get("ok"):
        return p.get("error") or "调度表达式无效"
    task["freq"] = p["freq"]
    task["at"] = p["at"]
    task["weekday"] = int(p["weekday"])
    task["weekdays"] = list(p["weekdays"])
    task["day"] = int(p["day"])
    task["expr"] = p["expr"]
    return ""



def _parse_hm(at, default=(8, 30)):
    s = str(at or "").strip()
    if ":" not in s:
        return default
    try:
        h, m = s.split(":", 1)
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except Exception:
        return default


def _day_of_month(year, month, day):
    """取当月第 day 天；超出当月天数就取最后一天。"""
    if month == 12:
        nxt = time.mktime((year + 1, 1, 1, 0, 0, 0, 0, 0, -1))
    else:
        nxt = time.mktime((year, month + 1, 1, 0, 0, 0, 0, 0, -1))
    last = time.localtime(nxt - 86400).tm_mday
    return min(max(1, int(day)), last)


def _fire_ts(task, now):
    """算出"本周期"的计划触发时间点（**可能在未来**）。

    ★ 绝不往回找"上一个周期"：老写法在"本月 26 号 10:00、而现在是 26 号 09:00"
      时会算出上个月 26 号 —— 结果是**还没到点就先把上个月的发了**。
      现在的约定：只算本周期；过点太久的（超过 `grace`）当作错过，不补发。
    """
    lt = time.localtime(now)
    h, m = _parse_hm(task.get("at"))
    freq = str(task.get("freq") or "daily").lower()
    # v9.23.0：有 weekdays（多个星期几）时优先按它算，返回**今天或之前最近**
    # 的一个计划点。老配置没有这个字段，下面的 weekday/daily/monthly 分支不变。
    days = task.get("weekdays")
    if freq == "weekly" and isinstance(days, (list, tuple)) and days:
        try:
            want = {int(d) for d in days if 0 <= int(d) <= 6}
        except Exception:
            want = set()
        if want:
            today = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
            for back in range(0, 8):
                ts = today - back * 86400
                if time.localtime(ts).tm_wday in want:
                    return ts
            return today
    if freq == "weekly":
        wd = int(task.get("weekday", 0) if task.get("weekday") is not None else 0)
        delta = (wd - lt.tm_wday) % 7          # 本周内还没到 -> 正数，今天 -> 0
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1)) \
            + delta * 86400
    if freq == "monthly":
        day = _day_of_month(lt.tm_year, lt.tm_mon, task.get("day") or 1)
        return time.mktime((lt.tm_year, lt.tm_mon, day, h, m, 0, 0, 0, -1))
    # daily
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


def due_tasks(tasks, now=None, grace=None):
    """返回**这一刻应该发**的任务列表（不含已发的）。

    `grace`：计划点过了多少秒还算"这一轮"（默认 6 小时）。超过就跳过，
    避免程序中午才打开时把早上 7 点的"起床提醒"补发出去。
    """
    now = time.time() if now is None else float(now)
    out = []
    for t in tasks or []:
        if not isinstance(t, dict) or not t.get("enabled", True):
            continue
        try:
            g = float(t.get("grace") if t.get("grace") is not None
                      else (3600 * 6 if grace is None else grace))
        except Exception:
            g = 3600 * 6
        fire = _fire_ts(t, now)
        if fire > now:
            continue                        # 本周期还没到点
        if now - fire > g:
            continue                        # 错过太久，不补发
        last = float(t.get("last_run") or 0)
        if last >= fire:
            continue                        # 这个计划点已经发过
        out.append(dict(t, _fire_ts=fire))
    return out


def describe(task):
    """人话描述（界面/日志用）。"""
    freq = str(task.get("freq") or "daily").lower()
    at = str(task.get("at") or "08:30")
    days = task.get("weekdays")
    if freq == "weekly" and isinstance(days, (list, tuple)) and days:
        wd = ["一", "二", "三", "四", "五", "六", "日"]
        try:
            ds = sorted({int(d) for d in days if 0 <= int(d) <= 6})
        except Exception:
            ds = []
        if ds:
            if ds == [0, 1, 2, 3, 4]:
                return f"工作日 {at}"
            return "、".join("周" + wd[d] for d in ds) + f" {at}"
    if freq == "weekly":
        wd = ["一", "二", "三", "四", "五", "六", "日"]
        try:
            i = int(task.get("weekday", 0))
        except Exception:
            i = 0
        return f"每周{wd[max(0, min(6, i))]} {at}"
    if freq == "monthly":
        return f"每月 {task.get('day') or 1} 号 {at}"
    return f"每天 {at}"


def make_task(title, text, thread_id, freq="daily", at="08:30",
              weekday=0, day=1, enabled=True, expr="", weekdays=None):
    """构造一条任务（id 由 store 补）。``expr`` 给了就以表达式为准（v9.23.0）。"""
    freq = str(freq or "daily").lower()
    task = {
        "title": str(title or "").strip() or "定时消息",
        "text": str(text or "").strip(),
        "thread": str(thread_id or ""),
        "freq": freq if freq in FREQS else "daily",
        "at": str(at or "08:30"),
        "weekday": int(weekday or 0),
        "day": int(day or 1),
        "enabled": bool(enabled),
        "last_run": 0.0,
        "created": time.time(),
    }
    if weekdays:
        task["weekdays"] = [int(x) for x in weekdays]
    if expr:
        err = apply_expr(task, expr)
        if err:
            raise ValueError(err)
    elif task["freq"] not in FREQS:
        task["freq"] = "daily"
    return task

