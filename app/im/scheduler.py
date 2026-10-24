# -*- coding: utf-8 -*-
"""定时主动消息（每日 / 每周 / 每月）—— v9.20.0。

和「任务页」的定时任务不同：这里发的是**钉钉消息**（到点主动说一句话），
比如"每周一 08:30 在班群里发本周值日安排"。

判定逻辑刻意做成**纯函数**，方便直接拿假时间戳测：

    due_tasks(tasks, now) -> [task...]

规则：
- `freq`: daily / weekly / monthly
- `at`: "HH:MM"（24 小时制，本地时间）
- `weekday`: 0=周一 … 6=周日（weekly 用）
- `day`: 1~31（monthly 用；当月没有 31 号就落在最后一天）
- 同一天同一条任务只触发一次（靠 `last_run` 的日期比较，而不是"距上次多久"）——
  否则程序重启、睡眠唤醒都会重发。
"""
import time

FREQS = ("daily", "weekly", "monthly")


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
              weekday=0, day=1, enabled=True):
    """构造一条任务（id 由 store 补）。"""
    freq = str(freq or "daily").lower()
    if freq not in FREQS:
        freq = "daily"
    return {
        "title": str(title or "").strip() or "定时消息",
        "text": str(text or "").strip(),
        "thread": str(thread_id or ""),
        "freq": freq,
        "at": str(at or "08:30"),
        "weekday": int(weekday or 0),
        "day": int(day or 1),
        "enabled": bool(enabled),
        "last_run": 0.0,
        "created": time.time(),
    }
