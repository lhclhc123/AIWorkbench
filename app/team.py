# -*- coding: utf-8 -*-
"""多角色团队引擎 —— 让一支「预置团队」在对话里协作，每个成员各跑各的会话、互不阻塞。

用户 2026-10-11 原话（需求 13）：
    「你团队的功能根本就做的不对，这个功能是直接放到对话中的，对话时可以选择使用团队，
      然后设计想软件开发团队之类的，有已经写好的成员及其身份背景，然后比如项目总监
      可以给工程师派任务之类的……」

和「多个后台任务」的区别：
  · 后台任务 = 一堆互不相干的活儿并行跑；
  · 团队     = **有协作关系**的一条流水线，而且过程要**直接显示在对话流里**。
      总监拆需求、产出「派单表」→ 按派单**并行**派出多个成员（各自独立工作目录、独立会话）
      → 独立校验岗复核 → 总监汇总交付。

四个阶段（每一步都会通过 on_event 抛事件，界面据此在对话流里逐条渲染）：
    ┌─ 阶段一 派单 ────────────────────────────────┐
    │  项目总监(串行) → 产出 JSON 派单表              │
    └──────────────────────────────────────────────┘
    ┌─ 阶段二 并行建造（★ 真并行，按依赖分批）─────┐
    │  第 1 批：无依赖的任务 同时跑                  │
    │  第 2 批：依赖已满足的任务 同时跑 …            │
    └──────────────────────────────────────────────┘
    ┌─ 阶段三 校验 ────────────────────────────────┐
    │  独立校验岗(串行)：真的跑一遍 + 逐条对验收标准  │
    └──────────────────────────────────────────────┘
    ┌─ 阶段四 交付 ────────────────────────────────┐
    │  项目总监(串行)：汇总成最终交付说明            │
    └──────────────────────────────────────────────┘

产物全部落在 `workspace/team/<run_id>/`：
    <key>_files/     每个成员的工作目录（各自写的东西在这儿，互不覆盖）
    report.md        总监汇总的最终交付说明
    run.json         全过程记录（可复现、可排查）

⚠ 本文件只动「怎么协作」；成员人设/提示词都在 `team_presets.py`（数据和引擎分开）。
⚠ 严禁在此使用 subprocess/os.system —— 命令执行一律走 agent 工具层。
"""
import json
import os
import re
import threading
import time
import uuid

# 成员人设 + 提示词拼装 + 派单解析 + 依赖分批，全部复用 team_presets。
from .team_presets import (                                    # noqa: F401  (部分是再导出)
    TEAMS, TEAMS_ORDER, DEFAULT_TEAM_ID, TEAM_PREFIX,
    get_preset, list_presets, iter_members, get_member,
    get_lead, get_validator, spawnable_ids,
    build_member_system, build_lead_dispatch_system,
    parse_dispatch, topo_batches,
)

# 对外事件种类（GUI 侧按这个 kind 分派渲染逻辑；签名稳定，勿随意改）。
EVENT_KINDS = ("phase", "dispatch", "member_start", "member_delta", "member_done", "done")


# ---------------------------------------------------------------------------
# 向后兼容：ROLES 仍从这里导出（由 software 团队成员的 id -> 名片 生成）。
# 老代码 / 老探针 import team.ROLES 还能用。
# ---------------------------------------------------------------------------
ROLES = {
    m["id"]: {"name": m["name"], "emoji": m["emoji"], "system": m["system"]}
    for m in get_preset("software")["members"]
}

# 老版本默认团队配置（保留导出，避免老代码 import 失败）。
DEFAULT_TEAM = ["pm", "arch", "dev:2", "qa", "lead"]


def _role_title(key):
    """把角色 key 翻成「emoji 名称」；兼容 key 里的 ':' / '#' 后缀。"""
    base = str(key or "").split(":")[0].split("#")[0]
    r = ROLES.get(base)
    if r:
        return f"{r['emoji']} {r['name']}"
    m = get_member(DEFAULT_TEAM_ID, base)
    return f"{m['emoji']} {m['name']}" if m else str(key)


# ---------------------------------------------------------------------------
# 流式片段回传（可选）：GUI 侧的 agent 适配器若拿到真实流式回调，
# 调用 team.delta("片段") 即可让该成员的输出逐字进入对话流。
# 为什么用线程局部：团队里多个成员在不同线程里并发跑，各自只该收到自己的片段。
# ---------------------------------------------------------------------------
_tls = threading.local()


def delta(chunk):
    """由成员的执行线程调用，把一段流式输出回传给当前运行的团队（可选能力）。"""
    cb = getattr(_tls, "emit", None)
    if cb and chunk:
        try:
            cb(chunk)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 旧接口：从架构师的输出里抠任务清单。保留给老代码/老探针，内部已改用派单表。
# ---------------------------------------------------------------------------
def parse_tasks(text):
    """（兼容用）从一段文本里抠出 2~4 个任务。解析失败返回 []，绝不抛异常。"""
    if not text:
        return []
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    for blob in blocks + [text]:
        s = blob.find("[")
        e = blob.rfind("]")
        if s < 0 or e <= s:
            continue
        raw = blob[s:e + 1].replace("，", ",").replace("：", ":")
        raw = re.sub(r",\s*([\]}])", r"\1", raw)
        try:
            arr = json.loads(raw)
        except Exception:
            continue
        out = []
        for it in arr:
            if isinstance(it, dict):
                t = str(it.get("title") or it.get("name") or "").strip()
                d = str(it.get("task") or it.get("desc") or it.get("detail") or "").strip()
                if d:
                    out.append({"title": t or f"任务{len(out)+1}", "task": d})
            elif isinstance(it, str) and it.strip():
                out.append({"title": f"任务{len(out)+1}", "task": it.strip()})
        if out:
            return out[:4]
    # 兜底：按「1. 2. 3.」之类的编号行拆
    lines = [ln.strip() for ln in text.splitlines()
             if re.match(r"^\s*(?:\d+[.、)]|[-*])\s*\S", ln)]
    out = []
    for ln in lines[:4]:
        t = re.sub(r"^\s*(?:\d+[.、)]|[-*])\s*", "", ln).strip()
        if len(t) >= 3:
            out.append({"title": t[:12], "task": t})
    return out


# ---------------------------------------------------------------------------
# TeamRun
# ---------------------------------------------------------------------------
class TeamRun:
    """一次团队执行。**同步 run()**，由调用方丢进后台线程跑（不阻塞界面）。

    对外关键点（GUI 依赖）：
      · 构造时传 on_event(kind, payload) 收过程事件（见 EVENT_KINDS）；
      · 传 team_id 选预置团队（默认 software）；
      · agent_fn(member_id, prompt, files_dir, max_steps) -> {"text","files"} 是成员执行器；
      · run() 结束后看 self.report / self.status / self.cards。
    """

    def __init__(self, rid, goal, ws_path, agent_fn, taskman=None,
                 roles=None, max_steps=6, on_change=None,
                 team_id=None, on_event=None):
        self.id = rid
        self.goal = goal
        self.ws_path = os.path.abspath(ws_path)
        self.agent_fn = agent_fn      # agent_fn(member_id, prompt, files_dir, max_steps) -> {"text","files"}
        self.taskman = taskman        # 有的话，每个成员会成为一个可见的后台任务（nested=True）
        self.roles = list(roles or [])  # 兼容旧的 roles 参数（仅记录，流程以 team_id 为准）
        self.max_steps = int(max_steps)
        self.on_change = on_change
        self.on_event = on_event
        self.team = get_preset(team_id)
        self.team_id = self.team["id"]
        self.created = time.time()
        self.ended = None
        self.status = "queued"        # queued/running/done/failed/cancelled
        self.report = ""              # 总监最终汇总
        self.error = ""
        self.dispatch = []            # 本轮的派单表（规整后）
        self.cards = {}               # key -> {role,title,status,text,files,started,ended,note,task_id}
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self.dir = os.path.join(self.ws_path, "team", self.id)
        os.makedirs(self.dir, exist_ok=True)

    # ---------- 基础 ----------
    @property
    def cancelled(self):
        return self._cancel.is_set()

    def cancel(self):
        """请求取消：置事件 + 连带取消所有还在跑的子任务。"""
        self._cancel.set()
        if self.taskman is not None:
            for c in self.cards.values():
                tid = c.get("task_id")
                if tid:
                    self.taskman.cancel(tid)
        self._touch()

    def _touch(self):
        if self.on_change:
            try:
                self.on_change(self)
            except Exception:
                pass

    def _emit(self, kind, payload=None):
        """向界面抛一个过程事件。**异常一律吞掉**，绝不让回调影响团队执行。"""
        if not self.on_event:
            return
        data = {"kind": kind, "run_id": self.id, "team_id": self.team_id}
        if payload:
            data.update(payload)
        try:
            self.on_event(kind, data)
        except Exception:
            pass

    def _card(self, key, **kw):
        with self._lock:
            c = self.cards.setdefault(key, {
                "role": key, "title": _role_title(key),
                "status": "queued", "text": "", "files": [], "note": "",
                "started": None, "ended": None, "task_id": "",
            })
            c.update(kw)
        self._touch()
        return c

    def snapshot(self):
        with self._lock:
            return {
                "id": self.id, "goal": self.goal, "status": self.status,
                "team_id": self.team_id, "team_name": self.team["name"],
                "created": self.created, "ended": self.ended,
                "elapsed": round((self.ended or time.time()) - self.created, 1),
                "report": self.report, "error": self.error,
                "dir": self.dir, "dispatch": [dict(d) for d in self.dispatch],
                "cards": [dict(c) for c in self.cards.values()],
            }

    # ---------- 成员执行 ----------
    def _work_dir(self, key):
        d = os.path.join(self.dir, str(key).replace(":", "_").replace("#", "_") + "_files")
        os.makedirs(d, exist_ok=True)
        return d

    def _run_member(self, key, member, prompt, note=""):
        """跑一个成员。返回它的产出文本（失败/取消返回空串）。

        key     卡片键（如 'dev#1'、'qa'、'lead#summary'）
        member  成员定义 dict（来自 team_presets）
        """
        if self.cancelled:
            self._card(key, role=member["id"], title=f"{member['emoji']} {member['name']}",
                       status="cancelled", note="已取消")
            return ""
        name = f"{member['emoji']} {member['name']}"
        c = self._card(key, role=member["id"], title=name, status="running",
                       started=time.time(), note=note or "正在工作…")
        wd = self._work_dir(key)
        holder = {}
        self._emit("member_start", {"key": key, "member": member["id"], "name": member["name"],
                                    "title": member["title"], "emoji": member["emoji"],
                                    "note": note, "task_title": note})

        # 给成员线程挂上「流式回传」的钩子：agent_fn 若要流式，可调用 team.delta()。
        def _emit_delta(chunk):
            self._emit("member_delta", {"key": key, "member": member["id"],
                                        "name": member["name"], "chunk": chunk})

        def _do(_task=None):
            _tls.emit = _emit_delta
            if _task is not None:
                _task.log_line(f"{name} 开始")
            try:
                res = self.agent_fn(member["id"], prompt, wd, self.max_steps)
            finally:
                _tls.emit = None
            holder["res"] = res
            return (res or {}).get("text", "")

        try:
            if self.taskman is not None:
                # ★ nested=True：团队子任务不参与排队。否则「父任务占着并发槽等子任务、
                #   子任务在队列里等槽」会直接死锁（max_running=2 时必现）。
                t = self.taskman.submit(
                    "agent", f"{name} · {note or self.id}", _do,
                    detail=prompt[:200], nested=True, dedup_key=f"team:{self.id}:{key}")
                self._card(key, task_id=t.id)
                if t.thread is not None:
                    t.thread.join()
                text = holder.get("res", {}).get("text", "")
                st = t.status
            else:
                text = _do(None)
                st = "done"
        except Exception as exc:                       # noqa: BLE001
            text = f"[错误] {type(exc).__name__}: {exc}"
            self._card(key, status="failed", ended=time.time(), text=text)
            self._emit("member_done", {"key": key, "member": member["id"],
                                       "name": member["name"], "status": "failed",
                                       "text": text, "files": []})
            return text

        res = holder.get("res") or {}
        files = res.get("files") or []
        if st == "cancelled" or self.cancelled:
            status = "cancelled"
        elif st == "failed":
            status = "failed"
        else:
            status = "done"
        self._card(key, status=status, ended=time.time(), text=text, files=files,
                   note=("" if status == "done" else note))
        self._emit("member_done", {"key": key, "member": member["id"], "name": member["name"],
                                   "title": member["title"], "emoji": member["emoji"],
                                   "status": status, "text": text,
                                   "files": [f.get("path") for f in files if f.get("path")]})
        self._save()
        return text or ""

    # ---------- 派单规整 ----------
    def _normalize_tasks(self, tasks):
        """把派单表里不合法的 member 兜底成能干活的一线成员；依赖下标越界的去掉。"""
        spawn = spawnable_ids(self.team_id)
        fallback = spawn[0] if spawn else "dev"
        out = []
        for tk in tasks:
            mid = str(tk.get("member") or "").strip()
            if mid not in [m["id"] for m in iter_members(self.team_id)] or \
                    mid in [self.team["lead"], (get_validator(self.team_id) or {}).get("id")]:
                mid = fallback
            deps = [d for d in (tk.get("depends_on") or [])
                    if isinstance(d, int) and 0 <= d < len(tasks) and d != len(out)]
            out.append({"member": mid, "title": tk.get("title", ""),
                        "task": tk.get("task", ""), "output": tk.get("output", ""),
                        "depends_on": deps})
        return out

    # ---------- 并行执行一批 ----------
    def _exec_batch(self, batch, tasks, results):
        """并行跑一批任务；依赖任务的产出会作为「前置产出」注入下批成员的提示。"""
        threads = []
        for idx in batch:
            tk = tasks[idx]
            member = get_member(self.team_id, tk["member"]) or get_lead(self.team_id)
            key = f"{member['id']}#{idx + 1}"
            prompt = build_member_system(self.team_id, member["id"], self.goal, dispatch=tk)
            # 拼接前置任务产出（依赖的任务必须先做完，这里是它们的结论）。
            pre = []
            for d in (tk.get("depends_on") or []):
                dt = results.get(d)
                if dt:
                    dtk = tasks[d]
                    pre.append(f"### 前置任务「{dtk.get('title', '')}」的产出\n{dt[:3000]}")
            if pre:
                prompt += "\n\n# 【前置任务产出】（请在此基础上继续）\n" + "\n\n".join(pre)
            th = threading.Thread(
                target=lambda k=key, m=member, p=prompt, i=idx: results.__setitem__(
                    i, self._run_member(k, m, p, note=tk.get("title", ""))),
                daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            if self.cancelled:
                break
            th.join()
        for th in threads:                              # 取消时也要收尾，别留悬挂线程
            th.join(timeout=1)

    # ---------- 主流程 ----------
    def run(self):
        """跑完整支团队（同步）。调用方负责把它放到后台线程里。"""
        self.status = "running"
        self._touch()
        lead = get_lead(self.team_id)
        validator = get_validator(self.team_id)
        try:
            if self.cancelled:
                self.status = "cancelled"
                return self.report

            # ===== 阶段一：总监派单 =====
            self._emit("phase", {"phase": "planning", "index": 1, "total": 4,
                                 "label": "阶段一 · 项目总监拆解需求、产出派单表"})
            lead_text = self._run_member(
                "lead#dispatch", lead,
                build_lead_dispatch_system(self.team_id, self.goal),
                note="拆解需求、派单")
            tasks = parse_dispatch(lead_text)
            if not tasks:
                # 解析失败也要能往下走 —— 用一个通用兜底任务，别让整支队伍停摆。
                spawn = spawnable_ids(self.team_id)
                tasks = [{"member": (spawn[0] if spawn else "dev"), "title": "实现",
                          "task": f"按下面的安排把东西做出来，产出可运行/可交付的结果：\n{lead_text[:1500]}",
                          "output": "（见任务说明）", "depends_on": []}]
            tasks = self._normalize_tasks(tasks)
            self.dispatch = tasks
            batches = topo_batches(tasks)
            self._emit("dispatch", {
                "lead_text": lead_text,
                "tasks": [dict(t, index=i) for i, t in enumerate(tasks)],
                "batches": batches,
            })
            self._save()

            # ===== 阶段二：按派单并行建造 =====
            self._emit("phase", {"phase": "building", "index": 2, "total": 4,
                                 "label": f"阶段二 · 按派单并行执行（共 {len(batches)} 批）"})
            results = {}
            for bi, batch in enumerate(batches):
                if self.cancelled:
                    break
                self._emit("phase", {"phase": "building", "index": 2, "total": 4,
                                     "batch": bi + 1, "batches": len(batches),
                                     "label": f"第 {bi + 1}/{len(batches)} 批并行（{len(batch)} 个成员）"})
                self._exec_batch(batch, tasks, results)

            # 汇总所有成员产出
            produced = []
            for i, tk in enumerate(tasks):
                txt = results.get(i) or (self.cards.get(f"{(get_member(self.team_id, tk['member']) or lead)['id']}#{i+1}", {}).get("text") or "")
                if txt:
                    produced.append(f"### 任务{i + 1}「{tk.get('title', '')}」（{tk.get('member', '')}）\n{txt[:3000]}")

            all_files = []
            for c in self.cards.values():
                all_files += [f.get("path") for f in (c.get("files") or []) if f.get("path")]
            file_list = "\n".join(f"- {p}" for p in all_files[:40]) or "（没有登记到产物文件）"

            # ===== 阶段三：独立校验 =====
            verify_text = ""
            if validator:
                self._emit("phase", {"phase": "verify", "index": 3, "total": 4,
                                     "label": "阶段三 · 独立校验（真的跑一遍 + 逐条对验收标准）"})
                vprompt = (
                    build_member_system(self.team_id, validator["id"], self.goal)
                    + "\n\n# 【团队各成员的本轮产出】\n" + ("\n\n".join(produced)[:6000] or "（无产出）")
                    + "\n\n# 【登记到的产物文件】\n" + file_list
                    + "\n\n请独立验证：找到产物、真的跑一遍、逐条对照验收标准打勾，发现问题附上复现步骤。"
                )
                verify_text = self._run_member(validator["id"], validator, vprompt, note="独立验证")

            # ===== 阶段四：总监汇总交付 =====
            self._emit("phase", {"phase": "deliver", "index": 4, "total": 4,
                                 "label": "阶段四 · 项目总监汇总最终交付说明"})
            dprompt = (
                build_member_system(self.team_id, lead["id"], self.goal)
                + "\n\n# 【派单执行情况】\n" + ("\n\n".join(produced)[:5000] or "（无产出）")
                + ("\n\n# 【独立校验岗的结论】\n" + verify_text[:2500] if verify_text else "")
                + "\n\n# 【登记到的产物文件】\n" + file_list
                + "\n\n请给出最终交付说明（结论 / 交付物清单含完整路径 / 验证证据 / 未完成与风险 / 用户下一步）。"
            )
            summary = self._run_member("lead#summary", lead, dprompt, note="汇总交付")

            self.report = summary or verify_text or "（团队未产出汇总，请查看各成员卡片）"
            self.status = "cancelled" if self.cancelled else "done"
        except Exception as exc:                       # noqa: BLE001
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.ended = time.time()
            self._save()
            self._write_report()
            self._touch()
            self._emit("done", {"status": self.status, "report": self.report,
                                "error": self.error, "dir": self.dir})
        return self.report

    # ---------- 落盘 ----------
    def _save(self):
        try:
            path = os.path.join(self.dir, "run.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception:
            pass

    def _write_report(self):
        try:
            lines = [f"# AI 团队执行报告：{self.id}", "",
                     f"- 团队：{self.team['name']}（{self.team_id}）",
                     f"- 需求：{self.goal}",
                     f"- 状态：{self.status}",
                     f"- 用时：{round((self.ended or time.time()) - self.created, 1)} 秒", ""]
            for c in self.cards.values():
                lines += [f"## {c['title']}（{c['status']}）",
                          f"工作目录：`{os.path.join(self.dir, str(c['role']).replace(':', '_') + '_files')}`", ""]
                if c.get("files"):
                    lines += ["产物："] + [f"- `{f['path']}`" for f in c["files"]] + [""]
                lines += [str(c.get("text") or "")[:6000], ""]
            with open(os.path.join(self.dir, "report.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# TeamManager
# ---------------------------------------------------------------------------
class TeamManager:
    """管理所有团队执行（内存 + 落盘）。"""

    def __init__(self, ws_path):
        self.ws_path = os.path.abspath(ws_path)
        self.root = os.path.join(self.ws_path, "team")
        os.makedirs(self.root, exist_ok=True)
        self._runs = {}
        self._order = []
        self._subs = []

    def subscribe(self, cb):
        if cb not in self._subs:
            self._subs.append(cb)

    def _notify(self, run):
        for cb in list(self._subs):
            try:
                cb(run)
            except Exception:
                pass

    def create(self, goal, agent_fn, taskman=None, roles=None, max_steps=6,
               team_id=None, on_event=None):
        """新建一次团队执行，**立刻返回 TeamRun**（真正执行在 run() 里，由调用方丢后台）。"""
        rid = "team-" + time.strftime("%m%d-%H%M") + "-" + uuid.uuid4().hex[:4]
        run = TeamRun(rid, goal, self.ws_path, agent_fn, taskman=taskman,
                      roles=roles, max_steps=max_steps, on_change=self._notify,
                      team_id=team_id, on_event=on_event)
        self._runs[rid] = run
        self._order.insert(0, rid)
        return run

    def get(self, rid):
        return self._runs.get(str(rid or "").strip())

    def list(self, limit=30):
        return [self._runs[r] for r in self._order[:limit] if r in self._runs]
