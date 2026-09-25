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
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta

TICK = 20.0
KINDS = ("once", "interval", "hourly", "daily", "weekly")

KIND_LABELS = {
    "once": "仅一次",
    "interval": "按间隔",
    "hourly": "每小时",
    "daily": "每天",
    "weekly": "每周",
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
    if kind == "weekly":
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
    return kind


def next_text(task):
    n = task.get("next_ts")
    if not n:
        return "—"
    try:
        return datetime.fromtimestamp(float(n)).strftime("%m-%d %H:%M")
    except Exception:
        return "—"


class Scheduler:
    """定时任务调度器。线程安全（内部一把锁）。"""

    def __init__(self, workspace_path=None, run_cb=None, log_cb=None):
        self.ws = os.path.abspath(workspace_path) if workspace_path else None
        self.file = os.path.join(self.ws, "tasks.json") if self.ws else None
        # run_cb(task) -> str 结果；由 UI 层注入（在 Agent 里执行）
        self.run_cb = run_cb
        self.log_cb = log_cb
        self.tasks = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
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
        # 补齐字段 + 重算下次时间
        for t in self.tasks:
            self._normalize(t, recompute=True)
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
            interval_min=60, run_at="", notify_dingtalk=False):
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
        }, recompute=True)
        if kind == "once" and not t["next_ts"]:
            raise ValueError("一次性任务的执行时间必须是将来的时间（格式 2026-09-26T08:00）")
        with self._lock:
            self.tasks.append(t)
            self.save()
        return t

    def update(self, task_id, **fields):
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
                    t[k] = v
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
    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
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

    def run_now(self, task_id):
        """立即执行一次（手动触发），不改动排期。"""
        t = self.get(task_id)
        if not t:
            return None, "找不到该任务"
        return t, self._invoke(t)

    def _invoke(self, task):
        if not self.run_cb:
            return "（未挂接执行器）"
        try:
            res = self.run_cb(dict(task))
            res = res if isinstance(res, str) else str(res)
        except Exception as e:
            res = f"[错误] {type(e).__name__}: {e}"
        with self._lock:
            for t in self.tasks:
                if t["id"] == task["id"]:
                    t["last_ts"] = time.time()
                    t["last_status"] = ("失败" if res.startswith("[错误]")
                                        else "成功")
                    t["last_result"] = res[:600]
                    t["run_count"] = int(t.get("run_count") or 0) + 1
                    if (t.get("kind") or "").lower() == "once":
                        t["enabled"] = False
                        t["next_ts"] = 0.0
                    else:
                        nt = compute_next(t)
                        t["next_ts"] = nt or 0.0
                    self.save()
                    break
        return res

    def tick(self):
        """扫一遍到期任务并执行。返回本次执行的任务 id 列表。"""
        ran = []
        for t in self.due():
            self._log(f"触发定时任务：{t.get('name')}")
            self._invoke(t)
            ran.append(t["id"])
        return ran
