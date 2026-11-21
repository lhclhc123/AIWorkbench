# -*- coding: utf-8 -*-
"""定时任务（Automation）—— 对标 WorkBuddy 的定时自动化。

任务模型（存 `<工作区>/tasks.json`）：

    {
      "id": "t3f9a1",
      "name": "每天早上发气象简报",
      "prompt": "查今天荣成的天气并整理成简报",   # 到期后交给 Agent 执行的自然语言任务
      "kind": "daily",            # once | interval | hourly | daily | weekly
      "at": "08:00",              # daily / weekly 用
      "weekdays": [1,2,3,4,5],    # weekly 用（1=周一 … 7=周日）
      "interval_min": 60,         # interval 用
      "run_at": "",               # once 用（"2026-09-26T08:00"）
      "enabled": true,
      "notify_dingtalk": false,   # 执行完顺便把结果推到钉钉
      "next_ts": 1790000000.0,    # 下次触发时间戳（自动算）
      "last_ts": 0.0, "last_status": "", "last_result": "",
      "run_count": 0
    }

线程模型：一个 daemon 线程每 20 秒扫一遍；到期就把任务丢给回调（由 UI 层注入，
在 Agent 线程里真跑）。所有写盘都先改内存再落盘，避免并发写坏文件。

v9.23.0 增强（**旧配置完全兼容**，`tasks.json` 里的老字段照旧读）
---------------------------------------------------------------
1. **调度表达式**：``parse_schedule(expr) -> dict``，支持
   ``daily@08:30`` / ``weekly@MO,WE,FR@16:00`` / ``workday@07:50`` /
   ``every@15m`` / ``every@2h`` / ``once@2026-11-20T14:30``。
   它**永不抛异常**，出错返回 ``{"ok": False, "error": "..."}``。
2. **失败重试**：``retry``（默认 2 次，即最多跑 3 遍），间隔 30s / 120s。
   ⚠ 重试是在调度线程里**原地等**的，所以一个任务重试期间其它到期任务会顺延
   （最多 2.5 分钟）。这样最坏情况下整体延迟可接受，且不会出现「同一任务在多处
   同时跑」；要缩短等待给任务加 ``retry_delays`` 字段即可。
3. **执行历史**：每个任务最近 50 条，落 ``<工作区>/.awb/tasks_history.json``，
   原子写（tmp + ``os.replace``），读坏了当空表。
4. **错过补偿**：启动时若某个定时点过去不超过 30 分钟就补跑一次（历史标
   ``catchup: True``），超过就只重排下次时间，避免开机瞬间连环跑。
   为此 ``load()`` **保留已落盘的 next_ts**（不再一律重算），这是补偿能生效的前提。
5. **互斥**：同一任务 id 一把 ``threading.Lock``，抢不到就跳过并记
   ``{"skipped": "running"}``，绝不并发跑两份。
6. **连续失败 3 次自动停用**该任务，并往历史写一条 ``level="error"`` 的告警。
   这里的「失败 3 次」指的是 **3 次执行**（每次执行内部已经把 ``retry`` 用完），
   不是 3 次尝试 —— 否则 ``retry=2`` 时任何一次失败都会立即停用，
   一次网络抖动就会把主任的每日任务悄悄关掉，代价太大。

执行体仍然只走外部注入的 ``run_cb``（UI 层接到 agent 通道），本模块不自己起
子进程、不自己执行命令，安全策略（后台默认禁 ``run_command``）不受影响。
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

TICK = 20.0
KINDS = ("once", "interval", "hourly", "daily", "weekly", "workday",
         "seewo_share")

KIND_LABELS = {
    "once": "仅一次",
    "interval": "按间隔",
    "hourly": "每小时",
    "daily": "每天",
    "weekly": "每周",
    "workday": "工作日",
    "seewo_share": "希沃共享屏幕",
}

# ---- 重试策略 -------------------------------------------------------------
DEFAULT_RETRY = 2                       # 额外重试次数（总尝试 = retry + 1）
RETRY_DELAYS = (30.0, 120.0)            # 第 1 次重试前 / 第 2 次重试前
MAX_CONSEC_FAIL = 3                     # 连续多少次「执行失败」-> 自动停用
                                        # （一次执行内部已含 retry 次重试）

# ---- 执行历史 -------------------------------------------------------------
HISTORY_MAX = 50                        # 每个任务保留多少条
HISTORY_DIRNAME = ".awb"
HISTORY_FILENAME = "tasks_history.json"

# ---- 错过补偿 -------------------------------------------------------------
CATCHUP_WINDOW = 30 * 60                # 迟到不超过 30 分钟才补跑

# 表达式里的星期缩写 -> ISO 星期（1=周一 … 7=周日）
_WEEKDAY_TOKENS = {
    "MO": 1, "MON": 1, "TU": 2, "TUE": 2, "WE": 3, "WED": 3,
    "TH": 4, "THU": 4, "FR": 5, "FRI": 5, "SA": 6, "SAT": 6,
    "SU": 7, "SUN": 7, "日": 7, "天": 7,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
}


def _hhmm(text, default="09:00"):
    """解析 'HH:MM' / '9点' / '9:5' 这类写法。"""
    s = str(text or "").strip().replace("：", ":")
    s = s.replace("点", ":").replace("时", ":").replace("分", "")
    s = s.strip(": ")
    if not s:
        return default
    parts = s.split(":")
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
    except Exception:
        return default
    hh = max(0, min(23, hh))
    mm = max(0, min(59, mm))
    return f"{hh:02d}:{mm:02d}"


def _weekdays(text):
    """'周一到周五' / '1,3,5' / '周一,周三' -> [1,3,5]"""
    if not text:
        return [1, 2, 3, 4, 5, 6, 7]
    if isinstance(text, (list, tuple)):
        raw = text
    else:
        s = str(text)
        for a, b in (("周一", "1"), ("周1", "1"), ("星期一", "1"), ("礼拜一", "1"),
                     ("周二", "2"), ("周2", "2"), ("星期二", "2"),
                     ("周三", "3"), ("周3", "3"), ("星期三", "3"),
                     ("周四", "4"), ("周4", "4"), ("星期四", "4"),
                     ("周五", "5"), ("周5", "5"), ("星期五", "5"),
                     ("周六", "6"), ("周6", "6"), ("星期六", "6"), ("周末", "6,7"),
                     ("周日", "7"), ("周天", "7"), ("周7", "7"), ("星期日", "7")):
            s = s.replace(a, b)
        s = s.replace("到", "-").replace("至", "-")
        raw = []
        for part in s.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    a, b = part.split("-", 1)
                    raw += list(range(int(a), int(b) + 1))
                    continue
                except Exception:
                    pass
            try:
                raw.append(int(part))
            except Exception:
                pass
    out = sorted({int(x) for x in raw if 1 <= int(x) <= 7})
    return out or [1, 2, 3, 4, 5, 6, 7]


# ---------------------------------------------------------------------------
# 调度表达式（v9.23.0）
# ---------------------------------------------------------------------------
# 设计约定：
#   · 解析**永远返回 dict**，绝不抛异常 —— 它会被 UI 的输入框直接调用，
#     一个手滑的表达式不能让界面崩掉。
#   · 返回结构固定（缺省值也给全），调用方不需要再判断键在不在：
#       {"ok", "error", "expr", "kind", "at", "weekdays",
#        "interval_min", "run_at", "label"}
#   · 老格式（tasks.json 里的 kind/at/weekdays）**不经过这里**，行为完全不变。
_SCHEDULE_EXAMPLES = (
    "daily@08:30",
    "weekly@MO,WE,FR@16:00",
    "workday@07:50",
    "every@15m",
    "once@2026-11-20T14:30",
)


def schedule_examples():
    """给界面做占位提示用的一小组示例。"""
    return list(_SCHEDULE_EXAMPLES)


def _empty_schedule(expr="", error=""):
    return {
        "ok": False, "error": error, "expr": str(expr or ""),
        "kind": "", "at": "", "weekdays": [], "interval_min": 0,
        "run_at": "", "label": "",
    }


def _hhmm_strict(text):
    """严格解析时间；不合法返回 None（与容错的 :func:`_hhmm` 分工不同）。

    认 ``8:30`` / ``08:30`` / ``08:30:00`` / ``8点30`` / ``8:5``。
    """
    s = str(text or "").strip().replace("：", ":")
    s = s.replace("点", ":").replace("时", ":").replace("分", "")
    s = s.strip(": ")
    if not s:
        return None
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


def _parse_weekday_tokens(text):
    """``MO,WE,FR`` / ``周一,周三`` / ``1,3,5`` -> ``[1,3,5]``；有坏 token 返回 None。"""
    s = str(text or "").strip()
    if not s:
        return None
    s = s.replace("，", ",").replace(" ", "")
    for cn, num in (("周一", "1"), ("周二", "2"), ("周三", "3"), ("周四", "4"),
                    ("周五", "5"), ("周六", "6"), ("周日", "7"), ("周天", "7")):
        s = s.replace(cn, num)
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        up = part.upper()
        if up in _WEEKDAY_TOKENS:
            out.append(_WEEKDAY_TOKENS[up])
            continue
        if len(up) == 1 and up in _WEEKDAY_TOKENS:
            out.append(_WEEKDAY_TOKENS[up])
            continue
        try:
            n = int(part)
        except Exception:
            return None
        if not 1 <= n <= 7:
            return None
        out.append(n)
    out = sorted(set(out))
    return out or None


_INTERVAL_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(s|sec|秒|m|min|分钟|h|hr|小时)?$", re.I)


def parse_schedule(expr):
    """把调度表达式解析成任务字段（**永不抛异常**）。

    支持：:

        daily@08:30              每天 08:30
        workday@07:50            周一到周五 07:50（法定节假日暂不跳过）
        weekly@MO,WE,FR@16:00    每周一/三/五 16:00
        every@15m / every@2h     每 15 分钟 / 每 2 小时（也认 30s、90分钟）
        once@2026-11-20T14:30    一次性

    返回 ``{"ok": True, "kind": ..., "at": ..., "weekdays": [...],
    "interval_min": ..., "run_at": ..., "label": ..., "error": "", "expr": ...}``；
    表达式有问题时 ``ok=False`` 且 ``error`` 是给人看的中文说明。
    """
    raw = str(expr or "").strip()
    if not raw:
        return _empty_schedule(raw, "调度表达式为空（示例：daily@08:30）")
    if "@" not in raw:
        return _empty_schedule(
            raw, f"无法识别「{raw}」：缺少「@」，示例 daily@08:30 / every@15m")
    head, _, tail = raw.partition("@")
    kind = head.strip().lower()
    tail = tail.strip()
    if not tail:
        return _empty_schedule(raw, f"「{raw}」的 @ 后面是空的，缺少时间或间隔")

    if kind in ("daily", "workday", "hourly"):
        at = _hhmm_strict(tail)
        if not at:
            return _empty_schedule(
                raw, f"「{raw}」里的时间「{tail}」不合法（应写成 08:30 这种）")
        label = {"daily": "每天 ", "workday": "工作日 ", "hourly": "每小时第 "}[kind]
        label = (label + at + " 分") if kind == "hourly" else (label + at)
        return {"ok": True, "error": "", "expr": raw, "kind": kind, "at": at,
                "weekdays": [1, 2, 3, 4, 5] if kind == "workday" else [],
                "interval_min": 0, "run_at": "", "label": label}

    if kind == "weekly":
        days_part, sep, time_part = tail.rpartition("@")
        if not sep:
            return _empty_schedule(
                raw, f"「{raw}」格式应为 weekly@MO,WE,FR@16:00（星期与时间之间要有 @）")
        days = _parse_weekday_tokens(days_part)
        if not days:
            return _empty_schedule(
                raw, f"「{days_part}」不是合法的星期（用 MO,TU,WE,TH,FR,SA,SU 或 1~7）")
        at = _hhmm_strict(time_part)
        if not at:
            return _empty_schedule(
                raw, f"「{time_part}」不是合法时间（应写成 16:00 这种）")
        names = ["一", "二", "三", "四", "五", "六", "日"]
        label = "、".join("周" + names[d - 1] for d in days) + " " + at
        return {"ok": True, "error": "", "expr": raw, "kind": "weekly", "at": at,
                "weekdays": days, "interval_min": 0, "run_at": "", "label": label}

    if kind == "every":
        m = _INTERVAL_RE.match(tail.replace(" ", ""))
        if not m:
            return _empty_schedule(
                raw, f"「{tail}」不是合法间隔（示例 every@15m / every@2h）")
        try:
            num = float(m.group(1))
        except Exception:
            return _empty_schedule(raw, f"「{tail}」里的数字不合法")
        unit = (m.group(2) or "m").lower()
        if num <= 0:
            return _empty_schedule(raw, "间隔必须大于 0")
        if unit in ("s", "sec", "秒"):
            mins = max(1, int((num + 59) // 60))
        elif unit in ("h", "hr", "小时"):
            mins = max(1, int(round(num * 60)))
        else:
            mins = max(1, int(round(num)))
        label = (f"每 {mins} 分钟" if mins < 60 or mins % 60
                 else f"每 {mins // 60} 小时")
        return {"ok": True, "error": "", "expr": raw, "kind": "interval",
                "at": "", "weekdays": [], "interval_min": mins, "run_at": "",
                "label": label}

    if kind == "once":
        s = tail.replace("T", " ").replace("/", "-").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
            except ValueError:
                continue
            return {"ok": True, "error": "", "expr": raw, "kind": "once",
                    "at": "", "weekdays": [], "interval_min": 0,
                    "run_at": dt.strftime("%Y-%m-%d %H:%M"),
                    "label": "仅一次 · " + dt.strftime("%Y-%m-%d %H:%M")}
        return _empty_schedule(
            raw, f"「{tail}」不是合法时间（示例 once@2026-11-20T14:30）")

    return _empty_schedule(
        raw, f"不支持的调度类型「{head}」（可用 daily / workday / weekly / "
             f"every / once / hourly）")


def apply_schedule(task, expr, now=None):
    """把表达式写进任务 dict，并重算 ``next_ts``；失败返回错误字符串，成功返回 ""。"""
    p = parse_schedule(expr)
    if not p.get("ok"):
        return p.get("error") or "调度表达式无效"
    task["kind"] = p["kind"]
    task["expr"] = p["expr"]
    if p.get("at"):
        task["at"] = p["at"]
    if p.get("weekdays"):
        task["weekdays"] = list(p["weekdays"])
    if p.get("interval_min"):
        task["interval_min"] = int(p["interval_min"])
    if p.get("run_at"):
        task["run_at"] = p["run_at"]
    nt = compute_next(task, now=now)
    task["next_ts"] = nt or 0.0
    return ""


def compute_next(task, now=None):
    """算出下一次触发时间戳；已过期或不可计算返回 None。"""
    now = now or datetime.now()
    kind = (task.get("kind") or "once").lower()
    ts0 = now.timestamp()
    if kind == "once":
        s = str(task.get("run_at") or "").strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M",
                    "%Y-%m-%d", "%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                if fmt == "%m-%d %H:%M":
                    dt = dt.replace(year=now.year)
                return dt.timestamp() if dt.timestamp() > ts0 else None
            except ValueError:
                continue
        return None
    if kind == "interval":
        mins = max(1, int(task.get("interval_min") or 60))
        base = float(task.get("last_ts") or 0) or ts0
        return base + mins * 60
    at = _hhmm(task.get("at"), "09:00")
    hh, mm = (int(x) for x in at.split(":"))
    if kind == "hourly":
        dt = now.replace(minute=mm, second=0, microsecond=0)
        if dt.timestamp() <= ts0:
            dt = dt + timedelta(hours=1)
        return dt.timestamp()
    if kind == "daily":
        dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dt.timestamp() <= ts0:
            dt = dt + timedelta(days=1)
        return dt.timestamp()
    if kind in ("weekly", "workday", "seewo_share"):
        # workday = 周一~周五（法定节假日留到以后接假日表再做）；
        # seewo_share 复用同一套「几点 + 哪几天」的算法，没配 weekdays 就是每天。
        if kind == "workday":
            days = [1, 2, 3, 4, 5]
        else:
            days = _weekdays(task.get("weekdays"))
        for off in range(0, 8):
            d = (now + timedelta(days=off))
            if d.isoweekday() not in days:
                continue
            dt = d.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if dt.timestamp() > ts0:
                return dt.timestamp()
        return None
    return None


def describe(task):
    """一句话说明这个任务什么时候跑（界面直接显示）。"""
    kind = (task.get("kind") or "once").lower()
    if kind == "once":
        return "仅一次 · " + (str(task.get("run_at") or "").replace("T", " ") or "未定")
    if kind == "interval":
        return f"每 {int(task.get('interval_min') or 60)} 分钟"
    if kind == "hourly":
        return f"每小时第 {_hhmm(task.get('at')).split(':')[1]} 分"
    if kind == "daily":
        return "每天 " + _hhmm(task.get("at"))
    if kind == "weekly":
        names = ["一", "二", "三", "四", "五", "六", "日"]
        ds = "、".join("周" + names[d - 1] for d in _weekdays(task.get("weekdays")))
        return f"{ds} {_hhmm(task.get('at'))}"
    if kind == "workday":
        return "工作日 " + _hhmm(task.get("at"))
    if kind == "seewo_share":
        names = ["一", "二", "三", "四", "五", "六", "日"]
        days = _weekdays(task.get("weekdays"))
        head = "每天" if days == [1, 2, 3, 4, 5, 6, 7] else \
            "、".join("周" + names[d - 1] for d in days)
        hint = str(task.get("media_hint") or
                   (task.get("payload") or {}).get("media_hint") or "").strip()
        return f"{head} {_hhmm(task.get('at'))} 共享屏幕" + \
            (f"（{hint}）" if hint else "")
    return kind


def next_text(task):
    n = task.get("next_ts")
    if not n:
        return "—"
    try:
        return datetime.fromtimestamp(float(n)).strftime("%m-%d %H:%M")
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# 执行历史（v9.23.0）
# ---------------------------------------------------------------------------
class TaskHistory:
    """每个任务最近 50 条执行记录，落 ``<工作区>/.awb/tasks_history.json``。

    为什么单独一个类
    ----------------
    · **必须在锁里改内存 + 写盘**：20 个线程同时写同一个文件，没有锁必然丢记录
      或者写出半截 JSON。这里用 ``RLock`` 把「追加 + 裁剪 + 落盘」包成原子操作。
    · **原子写**：先写 ``.tmp`` 再 ``os.replace``，中途掉电/被杀也不会留下半个文件
      （读到坏 JSON 时当空表处理，绝不让调度器起不来）。
    · 同一个工作区在进程内只保留**一个实例**（见 :func:`history_for`），
      这样即使上层不小心 new 出多个 Scheduler，历史也不会互相覆盖。
    """

    def __init__(self, path=None, max_entries=HISTORY_MAX):
        self.path = path
        self.max_entries = int(max_entries)
        self._lock = threading.RLock()
        self._data = {}
        self._load()

    # ---------- 读盘 ----------
    def _load(self):
        self._data = {}
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            # 读坏了当空表（可能是上次写到一半掉电 / 手工改坏）
            self._data = {}
            return
        src = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(src, dict):
            return
        for k, v in src.items():
            if isinstance(v, list):
                self._data[str(k)] = [e for e in v if isinstance(e, dict)][
                    -self.max_entries:]

    # ---------- 写 ----------
    def _flush_locked(self):
        if not self.path:
            return
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            pass                        # 历史写不进去不该影响任务本身

    def append(self, task_id, entry):
        """追加一条；返回裁剪后该任务的历史条数。"""
        tid = str(task_id or "")
        if not tid:
            return 0
        with self._lock:
            lst = self._data.setdefault(tid, [])
            lst.append(dict(entry or {}))
            if len(lst) > self.max_entries:
                del lst[:len(lst) - self.max_entries]
            self._flush_locked()
            return len(lst)

    def get(self, task_id, limit=None):
        tid = str(task_id or "")
        lim = self.max_entries if limit is None else max(0, int(limit))
        with self._lock:
            return [dict(e) for e in self._data.get(tid, [])][-lim:] if lim \
                else []

    def all(self):
        with self._lock:
            return {k: [dict(e) for e in v] for k, v in self._data.items()}

    def clear(self, task_id=None):
        with self._lock:
            if task_id is None:
                self._data = {}
            else:
                self._data.pop(str(task_id or ""), None)
            self._flush_locked()

    def reload(self):
        with self._lock:
            self._load()


_HISTORY_REGISTRY = {}
_HISTORY_REG_LOCK = threading.Lock()


def history_path_for(workspace_path):
    """``<工作区>/.awb/tasks_history.json``；没有工作区返回 None。"""
    if not workspace_path:
        return None
    return os.path.join(os.path.abspath(workspace_path), HISTORY_DIRNAME,
                        HISTORY_FILENAME)


def history_for(workspace_path):
    """按工作区取**共享**的历史实例（同一路径在进程内只有一个）。"""
    path = history_path_for(workspace_path)
    key = path or ""
    with _HISTORY_REG_LOCK:
        obj = _HISTORY_REGISTRY.get(key)
        if obj is None:
            obj = TaskHistory(path)
            _HISTORY_REGISTRY[key] = obj
        return obj


class Scheduler:
    """定时任务调度器。线程安全（内部一把锁 + 每任务一把执行锁）。

    ``run_cb(task) -> str``      普通任务的执行体（由 UI 层接到 agent 通道）
    ``share_cb(task, payload)``  ``kind="seewo_share"`` 的执行体（希沃共享屏幕）
    """

    def __init__(self, workspace_path=None, run_cb=None, log_cb=None,
                 share_cb=None):
        self.ws = os.path.abspath(workspace_path) if workspace_path else None
        self.file = os.path.join(self.ws, "tasks.json") if self.ws else None
        # run_cb(task) -> str 结果；由 UI 层注入（在 Agent 里执行）
        self.run_cb = run_cb
        # share_cb(task, payload) -> str；希沃共享屏幕专用（v9.23.0）
        self.share_cb = share_cb
        self.log_cb = log_cb
        self.tasks = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        # 每任务一把「执行中」锁：抢不到就跳过，保证同一任务不并发跑两份
        self._task_locks = {}
        self._locks_guard = threading.Lock()
        # 执行历史（同工作区共享一个实例）
        self.history = history_for(self.ws)
        self._catchup_done = False
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        if not self.file or not os.path.isfile(self.file):
            self.tasks = []
            return self.tasks
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tasks = data if isinstance(data, list) else data.get("tasks", [])
        except Exception:
            self.tasks = []
        # 补齐字段。
        # ★ 注意 recompute 的条件：**只有 next_ts 缺失/不合法时才重算**。
        #   老写法无条件重算会把「上次关程序时错过的定时点」直接抹掉，
        #   导致「错过补偿」（catchup）永远没有机会触发。
        for t in self.tasks:
            try:
                has_next = float(t.get("next_ts") or 0) > 0
            except Exception:
                has_next = False
            self._normalize(t, recompute=not has_next)
        return self.tasks


    def save(self):
        if not self.file:
            return
        try:
            os.makedirs(os.path.dirname(self.file), exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.file)
        except Exception as e:
            self._log(f"任务保存失败：{e}")

    def _normalize(self, t, recompute=False):
        t.setdefault("id", uuid.uuid4().hex[:6])
        t.setdefault("name", "未命名任务")
        t.setdefault("prompt", "")
        t.setdefault("kind", "daily")
        t.setdefault("enabled", True)
        t.setdefault("notify_dingtalk", False)
        t.setdefault("at", "09:00")
        t.setdefault("weekdays", [1, 2, 3, 4, 5, 6, 7])
        t.setdefault("interval_min", 60)
        t.setdefault("run_at", "")
        t.setdefault("last_ts", 0.0)
        t.setdefault("last_status", "")
        t.setdefault("last_result", "")
        t.setdefault("run_count", 0)
        # v9.23.0 新字段（有默认值，老配置读出来行为不变）
        t.setdefault("retry", DEFAULT_RETRY)
        t.setdefault("fail_streak", 0)
        t.setdefault("payload", {})
        t.setdefault("expr", "")
        t.setdefault("disabled_reason", "")
        if recompute or not t.get("next_ts"):
            nt = compute_next(t)
            t["next_ts"] = nt or 0.0
        return t

    # ---------- 日志 ----------
    def _log(self, msg):
        if self.log_cb:
            try:
                self.log_cb(msg)
            except Exception:
                pass

    # ---------- 增删改查 ----------
    def add(self, name, prompt, kind="daily", at="09:00", weekdays=None,
            interval_min=60, run_at="", notify_dingtalk=False,
            expr="", payload=None, retry=DEFAULT_RETRY,
            media_hint=None, **extra):
        """新建任务。

        ``expr`` 给了就用表达式解析出的字段覆盖 kind/at/weekdays/…（v9.23.0）；
        表达式不合法直接 ``ValueError``，消息是能直接展示给用户的中文。
        ``**extra`` 允许上层塞额外字段（如 ``media_hint``），旧调用不受影响。
        """
        if expr:
            p = parse_schedule(expr)
            if not p.get("ok"):
                raise ValueError(p.get("error") or "调度表达式无效")
            kind = p["kind"]
            if p.get("at"):
                at = p["at"]
            if p.get("weekdays"):
                weekdays = list(p["weekdays"])
            if p.get("interval_min"):
                interval_min = p["interval_min"]
            if p.get("run_at"):
                run_at = p["run_at"]
        kind = (kind or "daily").lower()
        if kind not in KINDS:
            raise ValueError(f"不支持的类型：{kind}（可选 {', '.join(KINDS)}）")
        if kind == "interval":
            try:
                interval_min = max(1, int(float(interval_min)))
            except Exception:
                interval_min = 60
        t = self._normalize({
            "id": uuid.uuid4().hex[:6],
            "name": (name or "").strip() or "未命名任务",
            "prompt": (prompt or "").strip(),
            "kind": kind,
            "at": _hhmm(at),
            "weekdays": _weekdays(weekdays),
            "interval_min": interval_min,
            "run_at": str(run_at or "").strip(),
            "notify_dingtalk": bool(notify_dingtalk),
            "created_ts": time.time(),
            "enabled": True,
            "expr": str(expr or ""),
            "payload": dict(payload or {}),
            "retry": max(0, int(retry or 0)),
        }, recompute=True)
        if media_hint is not None:
            t["media_hint"] = str(media_hint)
        for k, v in (extra or {}).items():
            if k not in t:
                t[k] = v
        if kind == "once" and not t["next_ts"]:
            raise ValueError("一次性任务的执行时间必须是将来的时间（格式 2026-09-26T08:00）")
        with self._lock:
            self.tasks.append(t)
            self.save()
        return t

    def update(self, task_id, **fields):
        """改字段并重算排期。``expr`` 给了就按表达式整体改写（解析失败抛 ValueError）。"""
        if "expr" in fields:
            p = parse_schedule(fields.get("expr"))
            if not p.get("ok"):
                raise ValueError(p.get("error") or "调度表达式无效")
            fields = dict(fields)
            fields.pop("expr", None)
            fields["expr"] = p["expr"]
            fields["kind"] = p["kind"]
            if p.get("at"):
                fields["at"] = p["at"]
            if p.get("weekdays"):
                fields["weekdays"] = list(p["weekdays"])
            if p.get("interval_min"):
                fields["interval_min"] = p["interval_min"]
            if p.get("run_at"):
                fields["run_at"] = p["run_at"]
        with self._lock:
            for t in self.tasks:
                if t["id"] != task_id:
                    continue
                for k, v in fields.items():
                    if k in ("kind",):
                        v = str(v).lower()
                    if k == "at":
                        v = _hhmm(v)
                    if k == "weekdays":
                        v = _weekdays(v)
                    if k == "interval_min":
                        try:
                            v = max(1, int(float(v)))
                        except Exception:
                            continue
                    if k == "retry":
                        try:
                            v = max(0, int(v))
                        except Exception:
                            continue
                    t[k] = v
                # 重新启用时清掉失败计数，否则一启用就可能被立刻停用
                if fields.get("enabled") is True:
                    t["fail_streak"] = 0
                    t["disabled_reason"] = ""
                self._normalize(t, recompute=True)
                self.save()
                return t
        return None

    def remove(self, task_id):
        with self._lock:
            n = len(self.tasks)
            self.tasks = [t for t in self.tasks if t["id"] != task_id]
            changed = len(self.tasks) != n
            if changed:
                self.save()
            return changed

    def get(self, task_id):
        for t in self.tasks:
            if t["id"] == task_id:
                return t
        return None

    def list_all(self):
        with self._lock:
            return [dict(t) for t in self.tasks]

    # ---------- 调度 ----------
    def set_share_runner(self, share_cb):
        """接线：``kind="seewo_share"`` 的执行体（v9.23.0）。"""
        self.share_cb = share_cb
        return self

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._catchup_done = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        # ★ 错过补偿只做一次，而且必须在这里（后台线程）做：
        #   start() 是 UI 初始化时同步调用的，把可能耗时几分钟的补跑放在那里
        #   会把主界面的启动卡住。
        if not self._catchup_done:
            self._catchup_done = True
            try:
                self.catchup_scan()
            except Exception as e:
                self._log(f"错过补偿检查失败：{e}")
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self._log(f"调度器异常：{e}")
            self._stop.wait(TICK)

    def due(self, now=None):
        """返回当前到期的任务（不改状态）。"""
        now_ts = (now or time.time())
        with self._lock:
            return [dict(t) for t in self.tasks
                    if t.get("enabled") and t.get("next_ts")
                    and float(t["next_ts"]) <= now_ts]

    def catchup_scan(self, now=None, window=CATCHUP_WINDOW):
        """启动时处理「程序没开时错过的定时点」。返回补跑过的任务 id 列表。

        规则（刻意保守）：
          · 迟到 ≤ ``window``（默认 30 分钟）→ **补跑一次**，历史标 ``catchup``；
          · 迟到更久 → 不补，只把 ``next_ts`` 推到下一个正常点。
        为什么不能无脑补：早上开机时，昨天 / 上周的所有定时点全都「到期」，
        连环跑会把机器和模型额度瞬间打满。
        """
        now_ts = time.time() if now is None else float(now)
        ran = []
        for t in self.list_all():
            if not t.get("enabled"):
                continue
            try:
                nt = float(t.get("next_ts") or 0)
            except Exception:
                nt = 0.0
            if not nt or nt > now_ts:
                continue
            tid = t["id"]
            overdue = now_ts - nt
            if overdue <= float(window):
                self._log(f"补跑错过的定时任务：{t.get('name')}"
                          f"（迟到 {int(overdue)} 秒）")
                self._invoke(t, catchup=True)
                ran.append(tid)
                continue
            # 错过太久：只重排，不补跑
            with self._lock:
                for x in self.tasks:
                    if x["id"] != tid:
                        continue
                    kind = (x.get("kind") or "").lower()
                    if kind == "interval":
                        # interval 的 compute_next 依赖 last_ts，可能算出过去的时间；
                        # 这里直接用 now + 间隔，避免开机瞬间连环触发。
                        mins = max(1, int(x.get("interval_min") or 60))
                        x["next_ts"] = now_ts + mins * 60
                    else:
                        x["next_ts"] = compute_next(x) or 0.0
                    if kind == "once":
                        x["enabled"] = False
                    break
                self.save()
            self._log(f"跳过错过多时的定时任务：{t.get('name')}"
                      f"（迟到 {int(overdue / 60)} 分钟）")
        return ran

    # ---------- 互斥 ----------
    def _acquire(self, task_id):
        """按任务 id 抢执行锁；抢不到返回 None（说明同一任务正在跑）。"""
        tid = str(task_id or "")
        with self._locks_guard:
            lk = self._task_locks.get(tid)
            if lk is None:
                lk = threading.Lock()
                self._task_locks[tid] = lk
        return lk if lk.acquire(blocking=False) else None

    def is_running(self, task_id):
        tid = str(task_id or "")
        with self._locks_guard:
            lk = self._task_locks.get(tid)
            if lk is None:
                return False
            locked = lk.locked()
            return bool(locked)

    # ---------- 执行 ----------
    def run_now(self, task_id):
        """立即执行一次（手动触发），同时按结果重排下次时间。"""
        t = self.get(task_id)
        if not t:
            return None, "找不到该任务"
        return t, self._invoke(t)

    def run_task(self, task_id, catchup=False):
        """(是否真的执行了, 结果)。被互斥跳过时第一项为 False。"""
        t = self.get(task_id)
        if not t:
            return False, "找不到该任务"
        res = self._invoke(t, catchup=catchup)
        return (not str(res).startswith("[跳过]")), res

    def _retry_count(self, task):
        try:
            return max(0, int(task.get("retry", DEFAULT_RETRY)))
        except Exception:
            return DEFAULT_RETRY

    def _retry_delays(self, task):
        raw = task.get("retry_delays")
        if isinstance(raw, (list, tuple)) and raw:
            out = []
            for v in raw:
                try:
                    out.append(max(0.0, float(v)))
                except Exception:
                    continue
            if out:
                return out
        return list(RETRY_DELAYS)

    def _execute_once(self, task):
        """跑一遍执行体，返回字符串结果。异常在这里就转成 ``[错误] …``。"""
        kind = str(task.get("kind") or "").lower()
        if kind == "seewo_share":
            if not self.share_cb:
                return ("[错误] 希沃共享任务还没接线：需要给 Scheduler 传入 "
                        "share_cb(task, payload)")
            try:
                payload = task.get("payload") or {}
                res = self.share_cb(dict(task), dict(payload))
                return res if isinstance(res, str) else str(res)
            except Exception as e:
                return f"[错误] {type(e).__name__}: {e}"
        if not self.run_cb:
            return "（未挂接执行器）"
        try:
            res = self.run_cb(dict(task))
            return res if isinstance(res, str) else str(res)
        except Exception as e:
            return f"[错误] {type(e).__name__}: {e}"

    def _invoke(self, task, catchup=False):
        """执行一个任务（含重试 / 互斥 / 历史 / 连续失败停用）。

        返回最终结果字符串。被互斥跳过时返回 ``"[跳过] …"``。
        """
        tid = str(task.get("id") or "")
        lock = self._acquire(tid)
        if lock is None:
            self._history_append(tid, {
                "ts": time.time(), "ok": False, "duration_ms": 0,
                "summary": "跳过：同一个任务上一次还在执行",
                "error": "", "skipped": "running",
                "catchup": bool(catchup), "level": "warn",
            })
            self._log(f"任务「{task.get('name')}」还在执行，本次跳过")
            return "[跳过] 同一个任务上一次还没跑完"

        started = time.time()
        try:
            attempts = self._retry_count(task) + 1
            delays = self._retry_delays(task)
            res = ""
            for i in range(attempts):
                res = self._execute_once(task)
                if not _is_failure(res):
                    break
                if i >= attempts - 1:
                    break
                delay = delays[min(i, len(delays) - 1)]
                self._log(f"任务「{task.get('name')}」第 {i + 1} 次失败，"
                          f"{int(delay)} 秒后重试")
                # 用 _stop 等：关程序时不该再傻等 120 秒
                if self._stop.wait(float(delay)):
                    break
            duration_ms = int((time.time() - started) * 1000)
            ok = not _is_failure(res)
            self._record_result(task, res, ok, duration_ms, catchup)
            return res
        finally:
            try:
                lock.release()
            except Exception:
                pass

    def _record_result(self, task, res, ok, duration_ms, catchup):
        """写任务状态 + 写一条历史（必要时追加告警条目）。"""
        now = time.time()
        streak = 0
        disabled = False
        name = task.get("name") or "未命名任务"
        with self._lock:
            for t in self.tasks:
                if t["id"] != task["id"]:
                    continue
                t["last_ts"] = now
                t["last_status"] = "成功" if ok else "失败"
                t["last_result"] = str(res)[:600]
                t["run_count"] = int(t.get("run_count") or 0) + 1
                t["fail_streak"] = 0 if ok else int(t.get("fail_streak") or 0) + 1
                streak = t["fail_streak"]
                kind = (t.get("kind") or "").lower()
                if kind == "once":
                    t["enabled"] = False
                    t["next_ts"] = 0.0
                else:
                    t["next_ts"] = compute_next(t) or 0.0
                # 连续失败 3 次 -> 自动停用（一次性任务本来就跑完即停，不额外告警）
                if not ok and streak >= MAX_CONSEC_FAIL and kind != "once":
                    t["enabled"] = False
                    t["next_ts"] = 0.0
                    t["disabled_reason"] = f"连续失败 {streak} 次，已自动停用"
                    disabled = True
                self.save()
                break

        self._history_append(str(task.get("id") or ""), {
            "ts": now,
            "ok": bool(ok),
            "duration_ms": int(duration_ms),
            "summary": str(res or "")[:300],
            "error": "" if ok else str(res or "")[:600],
            "catchup": bool(catchup),
            "level": "info" if ok else "warn",
        })
        if disabled:
            msg = f"任务「{name}」连续失败 {streak} 次，已自动停用"
            self._log("⚠ " + msg)
            self._history_append(str(task.get("id") or ""), {
                "ts": time.time(), "ok": False, "duration_ms": 0,
                "summary": msg, "error": str(res or "")[:600],
                "level": "error", "catchup": bool(catchup),
            })

    def _history_append(self, task_id, entry):
        try:
            self.history.append(task_id, entry)
        except Exception as e:
            self._log(f"执行历史写入失败：{e}")

    def history_of(self, task_id, limit=HISTORY_MAX):
        """某个任务的最近执行记录（给界面用）。"""
        try:
            return self.history.get(task_id, limit=limit)
        except Exception:
            return []

    def tick(self):
        """扫一遍到期任务并执行。返回本次执行的任务 id 列表。"""
        ran = []
        for t in self.due():
            self._log(f"触发定时任务：{t.get('name')}")
            self._invoke(t)
            ran.append(t["id"])
        return ran


def _is_failure(res):
    """判断执行结果算不算失败（沿用既有约定：``[错误]`` 开头）。"""
    s = str(res or "")
    return s.startswith("[错误]") or s.startswith("[失败]")
