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
    fallback_member_id, dispatch_ids, invalid_dispatch_members, strip_team_noise,
    build_member_system, build_lead_dispatch_system,
    parse_dispatch, topo_batches,
)

# 对外事件种类（GUI 侧按这个 kind 分派渲染逻辑；签名稳定，勿随意改）。
EVENT_KINDS = ("phase", "dispatch", "member_start", "member_delta", "member_done", "done")

# 单个成员的最长等待（秒）。P1-11：以前 `thread.join()` 无超时，一个卡住的模型请求
# 会把整支团队永久挂住（界面一直转圈、取消也不生效）。可用环境变量覆盖。
MEMBER_TIMEOUT = float(os.environ.get("AWB_TEAM_MEMBER_TIMEOUT", "900") or 900)

# ★ v9.19 新增：整支团队的总预算（秒）。实测事故：用户选的 SCNet 端点抖动时，
#   单次成员模型调用最长可以耗 150s×3 次重试 ≈ 460s，而团队**没有任何总时限**，
#   于是界面能连续转 7 分钟以上、什么也交付不了。有了总预算，超时就带着已有产出收尾，
#   宁可交"部分完成 + 明确说明缺什么"，也不要无限转圈。
TOTAL_BUDGET = float(os.environ.get("AWB_TEAM_BUDGET", "420") or 420)


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


def track_client(cli):
    """把成员线程里新建的 LLMClient 挂到当前 TeamRun 上（供"停止"掐断）。

    没有 TeamRun 上下文时静默忽略（例如普通对话、独立后台任务）。
    """
    run = getattr(_tls, "run", None)
    if run is None:
        return
    try:
        run.track_client(cli)
    except Exception:
        pass


def untrack_client(cli):
    run = getattr(_tls, "run", None)
    if run is None:
        return
    try:
        run.untrack_client(cli)
    except Exception:
        pass


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
        self.member_timeout = MEMBER_TIMEOUT   # P1-11：单成员等待上限（秒）
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
        self.warnings = []            # 本轮的可疑情况（成员 id 非法、重派、超时…）
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        # ★ v9.19：团队成员各自的 LLMClient（每个成员一个新实例，见
        #   main_window._bg_agent_run）。点「停止」时要把它们一起掐掉，
        #   否则用户得等当前那次 HTTP 读超时（最长 150s）才能停下来。
        self._clients = set()
        self.budget = TOTAL_BUDGET
        self._deadline = 0.0
        self.dir = os.path.join(self.ws_path, "team", self.id)
        os.makedirs(self.dir, exist_ok=True)

    # ---------- 基础 ----------
    @property
    def cancelled(self):
        return self._cancel.is_set()

    def track_client(self, cli):
        """登记一个正在为成员干活的 LLMClient，供 cancel() 一并中断。"""
        with self._lock:
            self._clients.add(cli)

    def untrack_client(self, cli):
        with self._lock:
            self._clients.discard(cli)

    def budget_left(self):
        """距离团队总预算截止还剩几秒（没启动过则返回总预算）。"""
        if not self._deadline:
            return self.budget
        return self._deadline - time.time()

    def cancel(self):
        """请求取消：置事件 + 掐断在跑的模型请求 + 连带取消子任务。"""
        self._cancel.set()
        # 先把模型请求掐了：`LLMClient.cancel()` 会 close 正在收流的响应，
        # 并让重试循环立刻退出，这样"停止"是秒级生效而不是等一个读超时。
        with self._lock:
            clients = list(self._clients)
        for cli in clients:
            try:
                cli.cancel()
            except Exception:
                pass
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
                "started": None, "ended": None, "task_id": "", "err": "", "duty": "",
            })
            c.update(kw)
        self._touch()
        return c

    def _warn(self, msg):
        """记一条「本轮不太对」的警告（会进 snapshot，界面/报告都能回看）。"""
        text = str(msg or "").strip()
        if not text:
            return
        with self._lock:
            if text not in self.warnings:
                self.warnings.append(text)
        self._touch()

    def snapshot(self):
        with self._lock:
            return {
                "id": self.id, "goal": self.goal, "status": self.status,
                "team_id": self.team_id, "team_name": self.team["name"],
                "created": self.created, "ended": self.ended,
                "elapsed": round((self.ended or time.time()) - self.created, 1),
                "report": self.report, "error": self.error,
                "warnings": list(self.warnings),
                "dir": self.dir, "dispatch": [dict(d) for d in self.dispatch],
                "cards": [dict(c) for c in self.cards.values()],
            }

    # ---------- 成员执行 ----------
    def _work_dir(self, key):
        d = os.path.join(self.dir, str(key).replace(":", "_").replace("#", "_") + "_files")
        os.makedirs(d, exist_ok=True)
        return d

    def _run_member(self, key, member, prompt, note="", steps=None):
        """跑一个成员。返回它的产出文本（失败/超时/无有效产出返回空串）。

        key     卡片键（如 'dev#1'、'qa'、'lead#summary'）
        member  成员定义 dict（来自 team_presets）

        ★ v9.18.0 修复（P0-3/P0-4/P1-7/P1-11）：
          · 成员产出统一过 strip_team_noise()，DSML / tool_call 标签不再进交付；
          · 执行器返回 ok=False（模型 429/403/异常）→ 卡片标 failed，产出按空处理；
          · steps 用尽但模型仍在调工具 → 卡片标 partial；
          · 等线程 join 有超时，不再可能永久挂住。

        steps: 这一轮给该成员的步数（默认 self.max_steps）。校验岗要多给几步
               （它要读文件、跑验证、写报告），汇总岗只给 2 步（它只该出文字）。
        """
        if self.cancelled:
            self._card(key, role=member["id"], title=f"{member['emoji']} {member['name']}",
                       status="cancelled", note="已取消")
            return ""
        name = f"{member['emoji']} {member['name']}"
        c = self._card(key, role=member["id"], title=name, status="running",
                       duty=member.get("title", ""),
                       started=time.time(), note=note or "正在工作…", err="")
        wd = self._work_dir(key)
        holder = {}
        self._emit("member_start", {"key": key, "member": member["id"], "name": member["name"],
                                    "title": member["title"], "emoji": member["emoji"],
                                    "note": note, "task_title": note})

        # 给成员线程挂上「流式回传」的钩子：agent_fn 若要流式，可调用 team.delta()。
        def _emit_delta(chunk):
            # 流式预览也别把 DSML / tool_call 标记亮给用户（收尾时还会再整体剥一次）。
            raw = str(chunk or "")
            if not raw or "DSML" in raw or "<tool_call" in raw.lower() or "｜｜" in raw:
                return
            self._emit("member_delta", {"key": key, "member": member["id"],
                                        "name": member["name"], "chunk": raw})

        def _do(_task=None):
            _tls.emit = _emit_delta
            _tls.run = self          # 供成员线程里的 LLMClient 登记（见 track_client）
            if _task is not None:
                _task.log_line(f"{name} 开始")
            try:
                res = self.agent_fn(member["id"], prompt, wd,
                                                self.max_steps if steps is None else steps)
            finally:
                _tls.emit = None
                _tls.run = None
            holder["res"] = res
            return (res or {}).get("text", "")

        timed_out = False
        st = "done"
        err = ""
        text = ""
        wait_cap = float(self.member_timeout)
        try:
            if self.taskman is not None:
                # ★ nested=True：团队子任务不参与排队。否则「父任务占着并发槽等子任务、
                #   子任务在队列里等槽」会直接死锁（max_running=2 时必现）。
                t = self.taskman.submit(
                    "agent", f"{name} · {note or self.id}", _do,
                    detail=prompt[:200], nested=True, dedup_key=f"team:{self.id}:{key}")
                self._card(key, task_id=t.id)
                if t.thread is not None:
                    # ★ v9.19：等待上限同时受"单成员超时"和"团队总预算"约束，
                    #   否则一个卡死的成员会把整轮拖到总预算之外。
                    wait_cap = min(float(self.member_timeout),
                                   max(15.0, float(self.budget_left())))
                    t.thread.join(timeout=wait_cap)
                    timed_out = t.thread.is_alive()
                text = holder.get("res", {}).get("text", "")
                st = "failed" if timed_out else t.status
                if timed_out:
                    err = (f"成员执行超时（等待上限 {wait_cap:.0f}s，"
                           f"团队剩余预算 {max(0.0, self.budget_left()):.0f}s）")
                    # 超时后一定要发取消：否则那个线程会一直占着并发槽，
                    # 后面几个成员全部挤不进去，整支团队看起来像"卡死"。
                    try:
                        t.cancel()
                    except Exception:
                        pass
            else:
                text = _do(None)
                st = "done"
        except Exception as exc:                       # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            text = ""
            st = "failed"

        res = holder.get("res") or {}
        files = res.get("files") or []
        # P0-4：执行器明确回报「模型调用失败」时，绝不能当成正常产出。
        if res.get("ok") is False:
            st = "failed"
            err = str(res.get("error") or "").strip() or err or "模型调用失败"
        steps_exhausted = bool(res.get("steps_exhausted"))

        text = strip_team_noise(text)
        soft_note = ""
        if self.cancelled or st == "cancelled":
            status = "cancelled"
        elif timed_out or st == "failed" or res.get("ok") is False:
            status = "failed"
        elif steps_exhausted or not text:
            # steps 用尽 = 只拿到半截结论；text 为空 = 模型没给出可用产出。
            # ★ v9.19：如果步数用尽但**产物文件确实落盘了**，那就是"活干完了、只是话没说完"，
            #   不能算未交付（实测里工程师/架构师都因为这条被误报成 partial）。
            if steps_exhausted and text and files:
                status = "done"
                soft_note = "步数用尽，但产物已落盘"
            else:
                status = "partial"
        else:
            status = "done"

        if err:
            self._warn(f"{member['name']}（{member['id']}）失败：{err}")
        elif status == "partial" and not text:
            self._warn(f"{member['name']}（{member['id']}）没有给出有效产出")

        if status == "done":
            final_note = soft_note
        elif status == "cancelled":
            final_note = "已取消"
        else:
            final_note = (err or ("步数用尽，仅给出部分结论" if steps_exhausted
                                  else (note or "未给出有效产出")))

        shown = text
        if status == "failed":
            shown = f"[模型调用失败，未交付内容] {err}" if err else "[无有效产出]"
            text = ""                                  # 失败内容绝不进入交付链路
            files = []                                 # 失败时登记的产物不可信
        self._card(key, status=status, ended=time.time(), text=shown, files=files,
                   note=final_note, err=err)
        self._emit("member_done", {"key": key, "member": member["id"], "name": member["name"],
                                   "title": member["title"], "emoji": member["emoji"],
                                   "status": status, "text": shown,
                                   "error": err,
                                   "files": [f.get("path") for f in files if f.get("path")]})
        self._save()
        return text

    # ---------- 派单规整 ----------
    def _normalize_tasks(self, tasks):
        """把派单表里不合法的 member 兜底成能干活的一线成员；依赖下标越界的去掉。

        ★ v9.18.0 修复（P0-2）：兜底**绝不硬编码 "dev"**（调研团队里根本没有这个人，
        会让 `_exec_batch` 回落到总监、全员塌缩成一个人）。统一走 fallback_member_id()。
        """
        allow = set(dispatch_ids(self.team_id))
        all_ids = {m["id"] for m in iter_members(self.team_id)}
        fallback = fallback_member_id(self.team_id)
        out = []
        for i, tk in enumerate(tasks):
            mid = str(tk.get("member") or "").strip()
            if mid not in allow:
                if mid and mid not in all_ids:
                    self._warn(f"派单第 {i + 1} 项写了不存在的成员 id「{mid}」，已改派给 {fallback}")
                elif mid:
                    self._warn(f"派单第 {i + 1} 项把活派给了不可派单的「{mid}」，已改派给 {fallback}")
                else:
                    self._warn(f"派单第 {i + 1} 项没写成员 id，已改派给 {fallback}")
                mid = fallback
            deps = [d for d in (tk.get("depends_on") or [])
                    if isinstance(d, int) and 0 <= d < len(tasks) and d != len(out)]
            out.append({"member": mid, "title": tk.get("title", ""),
                        "task": tk.get("task", ""), "output": tk.get("output", ""),
                        "depends_on": deps})
        return out

    # ---------- 派单 + 有界重派 ----------
    def _dispatch_retry_block(self, prev_text, invalid_ids):
        """给总监的「重派」提示：把上一轮的问题说清楚，并再次强调合法 id。"""
        if invalid_ids:
            reason = ("你上一轮派单表里的 member 写了团队里**不存在/不可派单**的 id："
                      + "、".join(sorted(set(invalid_ids)))
                      + "。这些 id 是无效的，任务会被退回。")
        else:
            reason = ("你上一轮**没有输出可解析的 JSON 派单表**"
                      "（可能把工具调用文本当成了回答）。")
        return (
            "\n\n# 【重派要求】（第 1 次也是唯一 1 次重派机会）\n"
            + reason + "\n"
            "请严格按上面的成员名册重新派单：只使用「可派单成员 id」里的 id，"
            "并且**只输出一个 ```json 数组**，不要输出任何工具调用、解释或其它内容。\n"
            "你上一轮的原话如下（只作参考，不要照抄其中的错误 id）：\n"
            "```\n" + str(prev_text or "")[:1200] + "\n```"
        )

    def _dispatch_with_retry(self, lead, lead_text):
        """解析派单表；成员 id 不合法或没解析出 JSON 时，让总监**重派一次**（有界）。

        返回 (tasks, lead_text)。tasks 可能为空（两次都失败），由 run() 明确判失败，
        **绝不把一段随便的文字硬造成任务**。
        """
        tasks = parse_dispatch(lead_text)
        bad = invalid_dispatch_members(self.team_id, tasks) if tasks else []
        if tasks and not bad:
            return tasks, lead_text

        bad_ids = [str(tasks[i].get("member") or "") for i in bad] if tasks else []
        self._warn("总监首轮派单不可用：" +
                   (f"成员 id 非法 {bad_ids}" if bad_ids else "没有可解析的 JSON 派单表") +
                   "；已要求重派一次")
        retry_prompt = (build_lead_dispatch_system(self.team_id, self.goal)
                        + self._dispatch_retry_block(lead_text, bad_ids))
        note = "重派 · 修正成员 id" if bad_ids else "重派 · 未输出 JSON"
        txt = self._run_member("lead#redispatch", lead, retry_prompt, note=note)
        if txt:
            t2 = parse_dispatch(txt)
            if t2:
                bad2 = invalid_dispatch_members(self.team_id, t2)
                if bad2:
                    self._warn("重派后仍有非法成员 id：" +
                               "、".join(str(t2[i].get("member") or "") for i in bad2) +
                               "（将由引擎兜底改派）")
                # 首轮那张卡不再参与「失败/部分完成」统计 —— 它已经被重派修正了，
                # 否则会把一次成功的重派误判成 partial。
                self._card("lead#dispatch", superseded=True,
                           note="首轮派单不合格，已重派修正")
                return t2, txt
            self._warn("重派后仍然没有可解析的派单表")
        return tasks, lead_text

    # ---------- 并行执行一批 ----------
    def _exec_batch(self, batch, tasks, results):
        """并行跑一批任务；依赖任务的产出会作为「前置产出」注入下批成员的提示。"""
        threads = []
        for idx in batch:
            tk = tasks[idx]
            member = get_member(self.team_id, tk["member"]) or get_lead(self.team_id)
            key = f"{member['id']}#{idx + 1}"
            prompt = build_member_system(self.team_id, member["id"], self.goal,
                                        dispatch=tk, work_dir=self._work_dir(key))
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
            # P1-11：批内也必须有超时 —— 子线程自己已带超时，这里留 60s 余量收尾。
            # ★ v9.19：同时不能超过团队总预算，否则"等成员"会突破整轮上限。
            th.join(timeout=min(self.member_timeout + 60,
                                max(20.0, self.budget_left() + 5)))
        for th in threads:                              # 取消/超时也要收尾，别留悬挂线程
            th.join(timeout=1)
        alive = [th for th in threads if th.is_alive()]
        if alive:
            self._warn(f"第 {len(alive)} 个成员线程在超时后仍未退出（已放弃等待，不阻塞交付）")

    # ---------- 失败呈现 ----------
    def _skip_tasks(self, batch, tasks, reason):
        """把一批任务如实标成"未执行"（不是失败、也不是完成）。

        卡片状态沿用 UI 认识的 "failed"（界面不必新增状态），但 note 明确写"未开始"，
        并且不产出正文 —— 这样 `_final_status()` 会判 partial，报告顶部横幅也会列出来。
        """
        for idx in batch:
            tk = tasks[idx]
            member = get_member(self.team_id, tk["member"]) or get_lead(self.team_id)
            key = f"{member['id']}#{idx + 1}"
            self._card(key, role=member["id"],
                       title=f"{member['emoji']} {member['name']}",
                       status="failed", duty=member.get("title", ""),
                       started=time.time(), ended=time.time(),
                       text="", files=[], err=reason,
                       note=f"{reason}（任务：{tk.get('title', '')}）")
            self._warn(f"「{member['name']}」的任务「{tk.get('title', '')}」未执行：{reason}")

    def _plain_report(self, tasks, results, file_list):
        """不调模型也能交出的交付说明（预算用尽 / 总监汇总失败时的兜底）。

        ★ v9.19 修正：以前只看"有没有文字产出"就写"已交付"，结果实测里工程师
          一个文件都没写、报告却说已完成。现在**以卡片状态 + 文件是否真的存在于磁盘**
          为准：没落盘就明写"没有产出文件"。
        """
        lines = ["## 本轮交付说明（系统按已有产出整理，未经总监润色）", ""]
        done, undone, nofile = [], [], []
        for i, tk in enumerate(tasks):
            member = get_member(self.team_id, tk["member"]) or get_lead(self.team_id)
            key = f"{member['id']}#{i + 1}"
            card = self.cards.get(key) or {}
            st = card.get("status")
            txt = (results.get(i) or "").strip()
            files = [f.get("path") for f in (card.get("files") or []) if f.get("path")]
            real = [p for p in files if os.path.exists(p)]
            label = f"- 任务{i + 1}「{tk.get('title', '')}」（{member['name']}）"
            if st != "done":
                why = str(card.get("err") or card.get("note") or "未完成").strip()
                undone.append(f"{label}：**未完成** —— {why[:160]}")
            elif real:
                done.append(f"{label}：已完成，产物 {len(real)} 个（{', '.join(os.path.basename(p) for p in real[:4])}）")
            elif files:
                # 卡片登记了文件，但磁盘上没有 → 视为不可信
                nofile.append(f"{label}：报告登记了产物，但磁盘上找不到（不可信）")
            elif txt:
                nofile.append(f"{label}：只给了文字说明，**没有产出文件**")
            else:
                undone.append(f"{label}：**没有产出**")
        if done:
            lines += ["### 已完成（产物已落盘）", *done, ""]
        if nofile:
            lines += ["### 有问题（声称做了但没落盘）", *nofile, ""]
        if undone:
            lines += ["### 未完成", *undone, ""]
        lines += ["### 登记到的产物文件", file_list or "（无）", ""]
        lines += ["> 说明：本轮因时间预算用尽或总监汇总失败而提前收尾，"
                  "未经独立校验，以上结论请以下方各成员卡片与产物文件为准。"]
        return "\n".join(lines)

    def _failure_banner(self):
        """汇总失败/部分完成的成员，生成报告顶部的醒目横幅（P0-4）。

        为什么要有它：以前模型 429/403 被当普通文本写进 last，成员卡片全是 done，
        最终 report 就是一段报错原文 —— 用户看起来"跑完了"，实际什么都没交付。
        """
        bad = [c for c in self.cards.values()
               if c.get("status") in ("failed", "partial") and not c.get("superseded")]
        if not bad:
            return ""
        lines = ["", "> ⚠ **本轮有成员未正常交付（结果不代表已完成）**"]
        for c in bad:
            why = str(c.get("err") or c.get("note") or "").strip() or "未给出有效产出"
            who = str(c.get("title") or "")
            duty = str(c.get("duty") or "").strip()
            if duty:
                who = f"{who}（{duty}）"
            lines.append(f"> - **{who}**：{c.get('status')} —— {why[:200]}")
        lines.append("")
        return "\n".join(lines)

    def _final_status(self):
        """根据卡片真实状态汇总整体状态（done / partial / failed / cancelled）。

        规则（P0-4）：
          · 取消优先；
          · 一个成员都没交付（全部 failed/partial 且无正文）→ failed；
          · 有失败/部分完成，但也有人交付了 → partial；
          · 全部 done → done。
        """
        if self.cancelled:
            return "cancelled"
        cards = [c for c in self.cards.values() if not c.get("superseded")]
        if not cards:
            return "failed"
        # 「交付」只算**一线成员**真的产出了正文；总监的汇总不能替失败的成员充数。
        lead_id = str(self.team.get("lead") or "")
        val = get_validator(self.team_id)
        val_id = val.get("id") if val else ""
        delivered = [c for c in cards
                     if c.get("role") not in (lead_id, val_id)
                     and c.get("status") in ("done", "partial")
                     and str(c.get("text") or "").strip()]
        bad = [c for c in cards if c.get("status") in ("failed", "partial")]
        if bad and not delivered:
            return "failed"
        if bad:
            return "partial"
        if not delivered:
            return "failed"
        return "done"

    # ---------- 主流程 ----------
    def run(self):
        """跑完整支团队（同步）。调用方负责把它放到后台线程里。"""
        self.status = "running"
        # ★ v9.19：整轮总预算（默认 420s，AWB_TEAM_BUDGET 可覆盖）。
        #   没有它，端点抖动时一轮能跑 7 分钟还不结束（实测事故）。
        self._deadline = time.time() + max(60.0, float(self.budget))
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
            # ★ P0-1/P0-5：成员 id 不存在或压根没输出 JSON → 让总监重派一次（有界）；
            #   两次都不行就**明确失败**，绝不把一段乱文本硬造成"实现"任务。
            tasks, lead_text = self._dispatch_with_retry(lead, lead_text)
            if not tasks:
                self.status = "failed"
                self.error = ("总监连续两次没能产出可解析的派单表"
                              "（模型没有按约定输出 JSON 派单表，或成员 id 全部非法）。")
                self._warn(self.error + " 为避免伪造任务，本轮已终止。")
                self.report = (self._failure_banner()
                               + "\n\n## 本轮未执行\n"
                               + "总监没有给出合法的派单表，因此没有向任何成员派活。\n\n"
                               + "**怎么排查**：确认所选模型能按 JSON 格式输出；"
                               + "或换一个模型/团队后重试。\n")
                return self.report
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
                if self.budget_left() <= 45:
                    # 预算不够再开一批：如实标记"未开始"，不要假装跑过。
                    self._skip_tasks(batch, tasks,
                                     f"团队总预算 {self.budget:.0f}s 已用尽，本批未开始执行")
                    continue
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
            if validator and self.budget_left() > 40:
                self._emit("phase", {"phase": "verify", "index": 3, "total": 4,
                                     "label": "阶段三 · 独立校验（真的跑一遍 + 逐条对验收标准）"})
                vprompt = (
                    build_member_system(self.team_id, validator["id"], self.goal,
                                        work_dir=self._work_dir(validator["id"]))
                    + "\n\n# 【团队各成员的本轮产出】\n" + ("\n\n".join(produced)[:6000] or "（无产出）")
                    + "\n\n# 【登记到的产物文件】\n" + file_list
                    + "\n\n请独立验证：找到产物、真的跑一遍、逐条对照验收标准打勾，发现问题附上复现步骤。"
                )
                verify_text = self._run_member(validator["id"], validator, vprompt,
                                               note="独立验证",
                                               steps=self.max_steps + 4)
            elif validator:
                self._warn("团队总预算不足，跳过独立校验（结论未经二次验证）")

            # ===== 阶段四：总监汇总交付 =====
            self._emit("phase", {"phase": "deliver", "index": 4, "total": 4,
                                 "label": "阶段四 · 项目总监汇总最终交付说明"})
            dprompt = (
                build_member_system(self.team_id, lead["id"], self.goal,
                                    work_dir=self._work_dir("lead#summary"))
                + "\n\n# 【派单执行情况】\n" + ("\n\n".join(produced)[:5000] or "（无产出）")
                + ("\n\n# 【独立校验岗的结论】\n" + verify_text[:2500] if verify_text else "")
                + "\n\n# 【登记到的产物文件】\n" + file_list
                + "\n\n请给出最终交付说明（结论 / 交付物清单含完整路径 / 验证证据 / 未完成与风险 / 用户下一步）。"
                + "\n\n⚠ 硬约束：写「已完成」之前，必须确认该任务**真的在磁盘上有产物文件**"
                  "（上面【登记到的产物文件】里没有、或成员只是说了做了却没落盘，就如实写「未产出文件」）。"
                  "**禁止**把只有文字说明的任务写成已完成。"
                + "\n\n这一轮**不要再调用任何工具**，直接用文字给出最终交付说明。"
            )
            if self.budget_left() > 25:
                summary = self._run_member("lead#summary", lead, dprompt, note="汇总交付", steps=2)
            else:
                # 预算用尽：绝不再发一次可能超时的模型请求，直接用已有产出拼交付说明。
                summary = ""
                self._warn("团队总预算用尽，跳过总监汇总，改由系统按已有产出直接生成交付说明")

            body = (summary or verify_text
                    or self._plain_report(tasks, results, file_list)
                    or "（团队未产出汇总，请查看各成员卡片）")
            # ★ P0-4：报告顶部先标失败原因，再放汇总正文；状态按真实卡片汇总，
            #   不允许「有成员失败但整体报 done」。
            banner = self._failure_banner()
            self.report = (banner + "\n" + body) if banner else body
            self.status = self._final_status()
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
            banner = self._failure_banner()
            if banner:
                lines += [banner, ""]
            if self.warnings:
                lines += ["## 过程警告", ""] + [f"- {w}" for w in self.warnings] + [""]
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
