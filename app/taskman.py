# -*- coding: utf-8 -*-
"""后台任务（并行链路）管理 —— 让"跑一条慢命令"不再卡住整个对话。

用户 2026-10-02 原话：
    「给我这个 AI 添加多任务同时执行这种能力。就是说，模型在跑我本地命令的时候
      不影响它模型继续思考，或者说继续弄别的东西，也可以多条链路同时执行。」

为什么必须做：
    老版本里 `run_command` / `run_python` / `spawn_agent` 都是**当场阻塞**的 ——
    模型发一条 3 分钟的打包命令，整条 agent 循环就干等 3 分钟，期间既不能思考
    也不能干别的。用户看到的就是"它卡在那儿不动"。

设计要点（每条都是为了"不卡住调用方"）：
  · 每个任务一个 **daemon 线程**；`submit()` 立刻返回 `Task`，绝不阻塞调用方；
  · 并发上限 `max_running`（默认 4），超出的进 queued 队列，避免把机器打满；
  · **协作式取消**：`cancel()` 置事件 + 杀子进程；任务函数要自己看 `task.cancelled`；
  · 变更通知走 `subscribe(cb)` 回调（UI 侧转成 Qt 信号，跨线程安全）；
  · 状态/日志/产物持久化到 `workspace/bgtasks/<id>.json`，重启后仍能看到历史。

⚠️ 线程安全约定：
  · `Task` 的字段只在任务线程里改；读取方（UI/工具）请用 `task.snapshot()` 拿快照。
  · `TaskManager` 内部所有可变结构都用 `_lock` 保护。
"""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

from . import winproc as _winproc

# 并发上限：超过就排队。4 条链路并行已经够用，再多只会互相抢 CPU/网络。
MAX_RUNNING = 4
# 单个任务的日志最多留多少行（防止打包那种几千行的输出把内存和 JSON 撑爆）
MAX_LOG_LINES = 400
# 落盘节流（秒）：日志是高频写入，不能每行都写一次 JSON
_PERSIST_INTERVAL = 2.0

STATUS_TEXT = {
    "queued": "排队中",
    "running": "运行中",
    "done": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


def _now():
    return time.strftime("%H:%M:%S")


def find_python():
    """找一个能用的 python.exe（给后台 python 任务用）。

    优先复用 agent 里那套探测逻辑（它认得托管版/系统版/PATH），
    拿不到就退回到 shutil.which。
    """
    try:
        from . import agent as _agent      # 延迟导入，避免模块级循环依赖
        p = _agent._find_python()
        if p:
            return p
    except Exception:
        pass
    return shutil.which("python") or shutil.which("python3") or "python"


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
class Task:
    """一个后台任务。字段改动都发生在任务线程里，外部读请用 snapshot()。"""

    def __init__(self, tid, kind, title, fn, detail=""):
        self.id = tid
        self.kind = kind                  # command / python / agent / team
        self.title = title
        self.detail = detail              # 一句话描述（模型给了什么）
        self.fn = fn                      # fn(task) -> str
        self.status = "queued"
        self.created = time.time()
        self.started = None
        self.ended = None
        self.log = []                     # list[str]
        self.result = ""                  # 最终结果文本（给模型/用户看的）
        self.error = ""
        self.files = []                   # [{"path":..,"size":..}]
        self.progress = ""                # 一句话进度（UI 显示）
        self.proc = None                  # 正在跑的子进程（取消时要杀）
        self.thread = None
        self._cancel = threading.Event()
        self._notify = None               # 由 TaskManager 注入
        self._last_persist = 0.0

    # ---------- 取消 ----------
    @property
    def cancelled(self):
        return self._cancel.is_set()

    def cancel(self):
        """请求取消：置事件 + 杀掉正在跑的子进程。任务函数应尽快检查 cancelled。"""
        self._cancel.set()
        p = self.proc
        if p is not None:
            try:
                _winproc.kill_tree(p)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self.log_line("收到取消请求")
        return True

    # ---------- 写 ----------
    def log_line(self, text):
        """追加一行日志（自动去重连续重复行，避免刷屏）。"""
        s = str(text).rstrip()
        if not s:
            return
        if len(self.log) >= MAX_LOG_LINES:
            # 丢了头部但要留个痕迹，不然用户以为日志断了
            if self.log and not self.log[0].startswith("…（日志过长"):
                self.log[0] = "…（日志过长，前面的已省略）"
            self.log.append(f"[{_now()}] {s}")
            del self.log[1:3]
        else:
            self.log.append(f"[{_now()}] {s}")
        self._touch(light=True)

    def set_progress(self, text):
        self.progress = str(text or "")
        self._touch(light=True)

    def add_file(self, path):
        """登记一个产物文件（相对/绝对路径都行，统一存绝对路径）。"""
        if not path:
            return
        p = os.path.abspath(str(path))
        try:
            size = os.path.getsize(p)
        except Exception:
            size = 0
        for f in self.files:
            if f.get("path") == p:
                f["size"] = size
                self._touch()
                return
        self.files.append({"path": p, "size": size})
        self._touch()

    def _touch(self, light=False):
        """通知订阅者。light=True 时对落盘做节流（日志高频）。"""
        if self._notify:
            try:
                self._notify(self, light)
            except Exception:
                pass

    # ---------- 读 ----------
    def snapshot(self, log_tail=40):
        """给 UI / 模型的只读快照（避免跨线程读到半成品）。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
            "status_text": STATUS_TEXT.get(self.status, self.status),
            "created": self.created,
            "started": self.started,
            "ended": self.ended,
            "elapsed": round((self.ended or time.time()) - (self.started or self.created), 1),
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "files": list(self.files),
            "log": list(self.log[-log_tail:]),
        }

    def to_dict(self):
        d = self.snapshot(log_tail=MAX_LOG_LINES)
        d.pop("fn", None)
        return d

    def summary(self, limit=60):
        """一行摘要（给模型看的任务列表）。"""
        el = round((self.ended or time.time()) - (self.started or self.created), 1)
        s = f"{self.id}  [{STATUS_TEXT.get(self.status, self.status)}]  {self.title}  ({el}s)"
        if self.progress and self.status == "running":
            s += f"  · {self.progress[:limit]}"
        if self.files:
            s += f"  · 产物 {len(self.files)} 个"
        return s


# ---------------------------------------------------------------------------
# TaskManager
# ---------------------------------------------------------------------------
class TaskManager:
    """全局后台任务池。一个工作区一个实例。"""

    def __init__(self, ws_path, max_running=MAX_RUNNING):
        self.ws_path = os.path.abspath(ws_path)
        self.dir = os.path.join(self.ws_path, "bgtasks")
        os.makedirs(self.dir, exist_ok=True)
        self.max_running = max(1, int(max_running))
        self._tasks = {}                 # id -> Task
        self._order = []                 # id 列表（新的在前）
        self._queue = []                 # 排队中的 id
        self._lock = threading.RLock()
        self._subs = []
        self._running = 0
        self._load_history()

    # ---------- 订阅 ----------
    def subscribe(self, cb):
        """订阅变更：cb(task, light)。UI 侧把它转成 Qt 信号再刷新界面。"""
        with self._lock:
            if cb not in self._subs:
                self._subs.append(cb)

    def _notify(self, task, light=False):
        for cb in list(self._subs):
            try:
                cb(task, light)
            except Exception:
                pass
        # 落盘：状态变化必写；日志类节流
        need = (not light) or (time.time() - task._last_persist > _PERSIST_INTERVAL)
        if need:
            task._last_persist = time.time()
            self._persist(task)

    # ---------- 提交 ----------
    def submit(self, kind, title, fn, detail="", dedup_key="", nested=False):
        """提交一个后台任务，**立刻返回 Task**（绝不阻塞调用方）。

        dedup_key 非空且已有同类任务在跑/排队 -> 直接返回那个（避免模型
        反复发同一条命令，把并发槽占满）。

        ⚠️ `nested=True` 用于**团队里由父任务派生的子任务**：
           父任务自己占着一个并发槽、又在 join 子任务。如果子任务还要排队等槽，
           就会出现「父等子、子等槽、槽被父占着」的**死锁**（max_running=2 时必现）。
           所以嵌套子任务直接起线程，不参与排队。
        """
        with self._lock:
            if dedup_key:
                for tid in self._order:
                    t = self._tasks.get(tid)
                    if (t and t.status in ("queued", "running")
                            and getattr(t, "_dedup", "") == dedup_key):
                        return t
            tid = "bg-" + uuid.uuid4().hex[:8]
            task = Task(tid, kind, title, fn, detail=detail)
            task._dedup = dedup_key
            task._notify = self._notify
            self._tasks[tid] = task
            self._order.insert(0, tid)
            if nested:
                task.log_line(f"已排队（{kind}·嵌套）：{title}")
                self._running += 1
                th = threading.Thread(target=self._run, args=(task,),
                                      name=f"awb-{tid}", daemon=True)
                task.thread = th
                th.start()
                return task
            self._queue.append(tid)
            task.log_line(f"已排队（{kind}）：{title}")
        self._pump()
        return task

    def _pump(self):
        """有空闲槽就起下一个排队的任务。"""
        with self._lock:
            while self._queue and self._running < self.max_running:
                tid = self._queue.pop(0)
                task = self._tasks.get(tid)
                if task is None or task.status != "queued":
                    continue
                if task.cancelled:
                    task.status = "cancelled"
                    task.ended = time.time()
                    self._notify(task)
                    continue
                self._running += 1
                th = threading.Thread(target=self._run, args=(task,),
                                      name=f"awb-{tid}", daemon=True)
                task.thread = th
                th.start()
            # 通知等待者：可能刚刚空出了槽
        self._notify_queue()

    def _notify_queue(self):
        with self._lock:
            for task in self._tasks.values():
                if task.status == "queued":
                    self._notify(task, light=True)

    def _run(self, task):
        task.status = "running"
        task.started = time.time()
        task.log_line("开始执行")
        self._notify(task)
        t0 = time.time()
        try:
            out = task.fn(task)
            task.result = str(out or "").strip()
            if task.cancelled:
                task.status = "cancelled"
            else:
                task.status = "done"
        except Exception as exc:                       # noqa: BLE001
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"
            task.log_line(f"❌ 出错：{task.error}")
        finally:
            task.ended = time.time()
            task.proc = None
            task.set_progress("")
            if task.status == "done":
                task.log_line(f"✅ 完成，用时 {time.time() - t0:.1f}s")
            self._notify(task)
            with self._lock:
                self._running = max(0, self._running - 1)
            self._pump()

    # ---------- 查询 ----------
    def get(self, tid):
        with self._lock:
            t = self._tasks.get(str(tid or "").strip())
            return t

    def list(self, limit=50, active_only=False):
        with self._lock:
            out = []
            for tid in self._order:
                t = self._tasks.get(tid)
                if t is None:
                    continue
                if active_only and t.status not in ("queued", "running"):
                    continue
                out.append(t)
                if len(out) >= limit:
                    break
            return out

    def running_count(self):
        with self._lock:
            return sum(1 for t in self._tasks.values()
                       if t.status in ("queued", "running"))

    def wait(self, tid, timeout=120):
        """等一个任务结束（给 bg_wait 工具用）。返回 Task 或 None（超时）。"""
        t = self.get(tid)
        if t is None:
            return None
        if t.thread is not None:
            t.thread.join(timeout=max(1.0, float(timeout)))
        return t if t.status not in ("queued", "running") else None

    def cancel(self, tid):
        t = self.get(tid)
        if t is None:
            return False
        if t.status in ("done", "failed", "cancelled"):
            return False
        t.cancel()
        return True

    def cancel_all(self):
        n = 0
        for t in self.list(limit=200):
            if t.status in ("queued", "running"):
                t.cancel()
                n += 1
        return n

    def clear_finished(self):
        """清掉已结束任务的记录（保留正在跑的）。"""
        with self._lock:
            keep = []
            for tid in self._order:
                t = self._tasks.get(tid)
                if t and t.status in ("queued", "running"):
                    keep.append(tid)
                elif t:
                    try:
                        os.remove(os.path.join(self.dir, f"{tid}.json"))
                    except Exception:
                        pass
            self._order = keep
            for tid in list(self._tasks.keys()):
                if tid not in keep:
                    self._tasks.pop(tid, None)
        return True

    # ---------- 持久化 ----------
    def _persist(self, task):
        try:
            path = os.path.join(self.dir, f"{task.id}.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception:
            pass

    def _load_history(self, keep=60):
        """重启后把上次的任务记录读回来（只看历史，不会自动重跑）。"""
        try:
            files = [f for f in os.listdir(self.dir) if f.endswith(".json")]
        except Exception:
            return
        files.sort(key=lambda f: os.path.getmtime(os.path.join(self.dir, f)),
                   reverse=True)
        for fn in files[:keep]:
            try:
                with open(os.path.join(self.dir, fn), encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            t = Task(d.get("id") or fn[:-5], d.get("kind", "?"),
                     d.get("title", ""), fn=lambda _t: "", detail=d.get("detail", ""))
            t.status = d.get("status", "done")
            if t.status in ("queued", "running"):      # 上次没跑完的进程已经没了
                t.status = "cancelled"
                t.log = list(d.get("log") or [])
                t.log.append("[上次程序退出时这个任务还没跑完，已标记为中断]")
            else:
                t.log = list(d.get("log") or [])
            t.created = d.get("created") or time.time()
            t.started = d.get("started")
            t.ended = d.get("ended")
            t.result = d.get("result", "")
            t.error = d.get("error", "")
            t.files = list(d.get("files") or [])
            t.progress = ""
            t._notify = self._notify
            self._tasks[t.id] = t
            self._order.append(t.id)


# ---------------------------------------------------------------------------
# 现成的任务工厂（命令 / Python）
# ---------------------------------------------------------------------------
def make_command_fn(cmd, cwd=None, timeout=600, shell=True):
    """造一个"跑 shell 命令"的后台任务函数（流式收集输出）。"""

    def _fn(task):
        task.proc = _winproc.popen(
            cmd, shell=shell, cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1)
        lines = []
        try:
            assert task.proc.stdout is not None
            for line in task.proc.stdout:
                if task.cancelled:
                    break
                line = line.rstrip("\n")
                lines.append(line)
                task.log_line(line)
                task.set_progress(f"输出 {len(lines)} 行")
                if len(lines) > 2000:
                    break
            task.proc.wait(timeout=max(5, int(timeout)))
        except subprocess.TimeoutExpired:
            try:
                _winproc.kill_tree(task.proc)
            except Exception:
                pass
            task.log_line(f"⏱ 超过 {int(timeout)} 秒，已强制结束")
            return (f"[超时] 命令超过 {int(timeout)} 秒仍未结束，已终止。\n"
                    f"最后输出：\n" + "\n".join(lines[-40:]))
        except Exception as exc:                       # noqa: BLE001
            return f"[错误] 执行失败：{exc}"
        out = "\n".join(lines)
        tail = "\n".join(lines[-60:])
        if len(out) > 8000:
            out = "…（前文省略）\n" + tail
        rc = task.proc.returncode if task.proc else -1
        tag = "（已被取消）" if task.cancelled else ""
        return (f"{tag}命令：`{cmd}`\n退出码：{rc}\n输出：\n```\n{out}\n```")

    return _fn


def make_python_fn(code, cwd=None, timeout=600, extra_env=None):
    """造一个"跑一段 Python 代码"的后台任务函数。"""
    py = find_python()

    def _fn(task):
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if cwd:
            # 让脚本能 import 同目录模块（和前台 run_python 的约定一致）
            env["PYTHONPATH"] = os.path.abspath(cwd) + os.pathsep + env.get("PYTHONPATH", "")
        if extra_env:
            env.update(extra_env)
        task.proc = _winproc.popen(
            [py, "-u", "-c", code], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        lines = []
        try:
            assert task.proc.stdout is not None
            for line in task.proc.stdout:
                if task.cancelled:
                    break
                line = line.rstrip("\n")
                lines.append(line)
                task.log_line(line)
                task.set_progress(f"输出 {len(lines)} 行")
            task.proc.wait(timeout=max(5, int(timeout)))
        except subprocess.TimeoutExpired:
            try:
                _winproc.kill_tree(task.proc)
            except Exception:
                pass
            task.log_line(f"⏱ 超过 {int(timeout)} 秒，已强制结束")
            return (f"[超时] 脚本超过 {int(timeout)} 秒仍未结束，已终止。\n"
                    f"最后输出：\n" + "\n".join(lines[-40:]))
        except Exception as exc:                       # noqa: BLE001
            return f"[错误] 执行失败：{exc}"
        out = "\n".join(lines)
        if len(out) > 8000:
            out = "…（前文省略）\n" + "\n".join(lines[-60:])
        rc = task.proc.returncode if task.proc else -1
        tag = "（已被取消）" if task.cancelled else ""
        return f"{tag}Python 退出码：{rc}\n输出：\n```\n{out}\n```"

    return _fn
