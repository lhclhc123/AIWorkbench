# -*- coding: utf-8 -*-
"""多角色团队 —— 让一支"产品/架构/工程/测试"小队各自独立跑，互不阻塞。

用户 2026-10-02 原话：
    「再给它增加一个像 WorkBuddy 这样的团队的功能，各个工程师这种，什么产品经理啊
      这种独立跑，然后不影响主进程，你把各个东西都协调做好。」

和"多个后台任务"的区别：
  · 后台任务 = 一堆互不相干的活儿并行跑；
  · 团队     = **有协作关系**的一条流水线：产品经理定需求 → 架构师出方案 + 拆任务 →
               **多个工程师并行实现**（各自独立工作目录、独立会话、互不干扰）→
               测试工程师验证 → 组长汇总交付。

三个阶段（用户能看见每一步在干什么）：
    ┌─ 阶段一 规划 ────────────────────────────────┐
    │  产品经理(串行) → 架构师(串行，拿到 PM 的产出)  │
    └──────────────────────────────────────────────┘
    ┌─ 阶段二 建造（★ 真并行）─────────────────────┐
    │  工程师A ┐                                     │
    │  工程师B ├─ 各自独立目录/独立会话，同时跑        │
    │  工程师C ┘                                     │
    └──────────────────────────────────────────────┘
    ┌─ 阶段三 校验交付 ────────────────────────────┐
    │  测试工程师(串行) → 组长汇总(串行)              │
    └──────────────────────────────────────────────┘

产物全部落在 `workspace/team/<run_id>/`：
    <角色>_files/   该角色的工作目录（工程师写的东西在这儿，互不覆盖）
    report.md       组长汇总的最终交付说明
    run.json        全过程记录（可复现、可排查）
"""
import json
import os
import re
import threading
import time
import uuid

# ---------------------------------------------------------------------------
# 内置角色。每个角色一段独立人设 = 独立 system 提示，独立跑。
# ---------------------------------------------------------------------------
ROLES = {
    "pm": {
        "name": "产品经理", "emoji": "📋",
        "system": (
            "你是这支 AI 团队的产品经理。你的职责是**把用户那句模糊的需求变成可验收的清单**。\n"
            "请输出（用中文，Markdown）：\n"
            "1. 需求理解：一句话说清用户到底要什么；\n"
            "2. 验收标准：3~6 条**可以逐条打勾验证**的具体标准（不要写「体验良好」这种空话）；\n"
            "3. 交付物清单：最终要给用户哪些文件/结果。\n"
            "⚠️ 不要写代码，不要执行命令，不要谈技术选型 —— 那是架构师和工程师的事。"
        ),
    },
    "arch": {
        "name": "架构师", "emoji": "🏗",
        "system": (
            "你是这支 AI 团队的架构师。产品经理已经给出了需求与验收标准，你要给出**技术方案**\n"
            "并把工作拆成可以**并行**执行的任务。\n"
            "请输出（中文 Markdown）：\n"
            "1. 技术方案：用什么语言/库/结构，关键步骤；\n"
            "2. 任务清单：用 ```json 代码块给出一个数组，每项形如\n"
            '   {"title": "任务名（8 字内）", "task": "给工程师的完整指令，要写清楚产出什么文件、'
            '放在哪个文件、怎么算做完"}\n'
            "   拆成 2~4 个任务，**每个任务必须产出不重叠的文件**（否则并行时会互相覆盖）。\n"
            "3. 依赖说明：哪些任务必须先做。\n"
            "⚠️ 不写完整代码（工程师来写），但要说清接口/文件名/数据结构。"
        ),
    },
    "dev": {
        "name": "工程师", "emoji": "💻",
        "system": (
            "你是这支 AI 团队的一名工程师。你只负责分配给自己的**那一个**任务，\n"
            "不要管别的任务，也不要重写别人的文件。\n"
            "工作要求：\n"
            "1. **必须真的写出文件**（用 write_file 工具），不要只在回答里贴代码；\n"
            "2. 写完**必须真的运行验证一次**（run_python / run_command，命令里要出现你写的文件名），\n"
            "   并把真实输出贴出来；跑不通就自己修，别急着说完成；\n"
            "3. 最后用 3~5 行说明：做了什么、文件在哪、验证结果是什么（贴真实输出）。\n"
            "⚠️ 工作目录就是你的专属目录，用相对路径写文件即可。"
        ),
    },
    "qa": {
        "name": "测试工程师", "emoji": "🧪",
        "system": (
            "你是这支 AI 团队的测试工程师。工程师们已经交付了代码，你要**独立验证**，\n"
            "而不是替他们说好话。\n"
            "工作步骤：\n"
            "1. 用 list_dir / read_file 找到实际产物，逐个检查是否真的存在、内容是否完整；\n"
            "2. **真的运行一遍**（run_python / run_command），把真实输出贴出来；\n"
            "3. 逐条对照产品经理的验收标准，给每条打 ✅/❌ 并附证据；\n"
            "4. 发现的问题要写清「哪条命令、什么报错、怎么复现」。\n"
            "⚠️ 严禁编造测试结果；跑不起来就如实说跑不起来。"
        ),
    },
    "lead": {
        "name": "组长", "emoji": "🧭",
        "system": (
            "你是这支 AI 团队的组长。所有角色都干完了，你要给用户一份**最终交付说明**。\n"
            "必须包含：\n"
            "1. 一句话结论：任务达成了没有；\n"
            "2. 交付物清单：**每个文件的完整路径** + 它是什么；\n"
            "3. 验证证据：测试工程师跑出来的真实结果（贴关键输出，别只写「测试通过」）；\n"
            "4. 未完成/有风险的部分：如实列出，不要粉饰；\n"
            "5. 用户下一步该做什么（怎么用这些文件）。\n"
            "⚠️ 只依据团队真实产出说话，绝不编造没发生的事。"
        ),
    },
}

# 默认团队配置（用户可在界面上改）
DEFAULT_TEAM = ["pm", "arch", "dev:2", "qa", "lead"]


def _role_title(key):
    r = ROLES.get(key)
    return f"{r['emoji']} {r['name']}" if r else key


def parse_tasks(text):
    """从架构师的输出里抠出任务清单。

    健壮性优先：模型给的 JSON 经常带 ```json 围栏、末尾多余逗号、中文引号。
    抠不出来就退回"按方案正文拆两段"，**绝不允许因为解析失败就让整个团队停摆**。
    """
    if not text:
        return []
    blocks = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    cands = blocks + [text]
    for blob in cands:
        s = blob.find("[")
        e = blob.rfind("]")
        if s < 0 or e <= s:
            continue
        raw = blob[s:e + 1]
        raw = raw.replace("，", ",").replace("：", ":")
        raw = re.sub(r",\s*([\]}])", r"\1", raw)          # 去掉尾逗号
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
    # ⚠️ 阈值别定太高：早先写 `len(t) > 6`，像「先做A」这种短任务名会被整条过滤掉，
    #    明明能救回来却返回 0 个（探针实测打出来过）。
    lines = [ln.strip() for ln in text.splitlines()
             if re.match(r"^\s*(?:\d+[.、)]|[-*])\s*\S", ln)]
    out = []
    for ln in lines[:4]:
        t = re.sub(r"^\s*(?:\d+[.、)]|[-*])\s*", "", ln).strip()
        if len(t) >= 3:
            out.append({"title": t[:12], "task": t})
    return out


class TeamRun:
    """一次团队执行。**同步 run()**，由调用方丢进后台线程跑（不阻塞界面）。"""

    def __init__(self, rid, goal, ws_path, agent_fn, taskman=None,
                 roles=None, max_steps=6, on_change=None):
        self.id = rid
        self.goal = goal
        self.ws_path = os.path.abspath(ws_path)
        self.agent_fn = agent_fn      # agent_fn(role_key, prompt, files_dir, max_steps) -> {"text","files"}
        self.taskman = taskman        # 有的话，每个角色会成为一个可见的后台任务
        self.roles = list(roles or DEFAULT_TEAM)
        self.max_steps = int(max_steps)
        self.on_change = on_change
        self.created = time.time()
        self.ended = None
        self.status = "queued"        # queued/running/done/failed/cancelled
        self.report = ""              # 组长最终汇总
        self.error = ""
        self.cards = {}               # key -> {role,title,status,text,files,started,ended,note}
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self.dir = os.path.join(self.ws_path, "team", self.id)
        os.makedirs(self.dir, exist_ok=True)

    # ---------- 基础 ----------
    @property
    def cancelled(self):
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()
        # 连带取消所有还在跑的角色子任务
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

    def _card(self, key, **kw):
        with self._lock:
            c = self.cards.setdefault(key, {
                "role": key, "title": _role_title(key.split(":")[0]),
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
                "created": self.created, "ended": self.ended,
                "elapsed": round((self.ended or time.time()) - self.created, 1),
                "report": self.report, "error": self.error,
                "dir": self.dir,
                "cards": [dict(c) for c in self.cards.values()],
            }

    # ---------- 角色执行 ----------
    def _work_dir(self, key):
        d = os.path.join(self.dir, key.replace(":", "_") + "_files")
        os.makedirs(d, exist_ok=True)
        return d

    def _run_role(self, key, prompt, note=""):
        """跑一个角色。返回它的产出文本（失败返回错误说明）。"""
        if self.cancelled:
            self._card(key, status="cancelled", note="已取消")
            return ""
        c = self._card(key, status="running", started=time.time(),
                       note=note or "正在工作…")
        wd = self._work_dir(key)
        holder = {}

        def _do(_task=None):
            if _task is not None:
                _task.log_line(f"角色 {key} 开始")
            res = self.agent_fn(key.split(":")[0], prompt, wd, self.max_steps)
            holder["res"] = res
            return (res or {}).get("text", "")

        try:
            if self.taskman is not None:
                # ★ nested=True：团队子任务不参与排队。否则「父任务占着并发槽等子任务、
                #   子任务在队列里等槽」会直接死锁（max_running=2 时必现）。
                t = self.taskman.submit(
                    "agent", f"{_role_title(key.split(':')[0])} · {note or self.id}",
                    _do, detail=prompt[:200], nested=True,
                    dedup_key=f"team:{self.id}:{key}")
                self._card(key, task_id=t.id)
                if t.thread is not None:
                    t.thread.join()
                text = holder.get("res", {}).get("text", "")
                st = t.status
            else:
                text = _do(None)
                st = "done"
        except Exception as exc:                       # noqa: BLE001
            self._card(key, status="failed", ended=time.time(),
                       text=f"[错误] {type(exc).__name__}: {exc}")
            return f"[错误] {type(exc).__name__}: {exc}"

        res = holder.get("res") or {}
        files = res.get("files") or []
        if res.get("files"):
            for f in files:
                try:
                    if os.path.exists(f["path"]):
                        pass
                except Exception:
                    pass
        if st == "cancelled" or self.cancelled:
            self._card(key, status="cancelled", ended=time.time(), text=text)
        elif st == "failed":
            self._card(key, status="failed", ended=time.time(), text=text)
        else:
            self._card(key, status="done", ended=time.time(), text=text,
                       files=files, note="")
        self._save()
        return text

    # ---------- 主流程 ----------
    def run(self):
        """跑完整支团队（同步）。调用方负责把它放到后台线程里。"""
        self.status = "running"
        self._touch()
        try:
            if self.cancelled:
                self.status = "cancelled"
                return self.report

            # ===== 阶段一：规划（产品经理 → 架构师，串行）=====
            pm = self._run_role("pm", self.goal, note="理解需求、定验收标准")
            arch_prompt = (
                f"【用户原始需求】\n{self.goal}\n\n"
                f"【产品经理的需求梳理与验收标准】\n{pm or '（产品经理未产出，按原始需求判断）'}\n\n"
                "请给出技术方案 + 可并行的任务清单（按你 system 里要求的 JSON 格式）。"
            )
            arch = self._run_role("arch", arch_prompt, note="出技术方案、拆任务")
            tasks = parse_tasks(arch)
            if not tasks:
                # 解析失败也要能往下走 —— 用一个通用兜底任务，别让整支队伍停摆
                tasks = [{"title": "实现", "task":
                          f"按下面的方案把东西做出来，产出可运行的文件：\n{arch[:1500]}"}]

            # ===== 阶段二：建造（多个工程师 **真并行**）=====
            n_dev = 1
            for r in self.roles:
                if str(r).startswith("dev"):
                    n_dev = int(str(r).split(":")[1]) if ":" in str(r) else 1
                    break
            n_dev = max(1, min(n_dev, len(tasks)))
            pool = {}
            threads = []
            for i in range(n_dev):
                key = f"dev:{i + 1}"
                tk = tasks[i]
                prompt = (
                    f"【总目标】{self.goal}\n\n"
                    f"【产品经理的验收标准】\n{pm[:1500]}\n\n"
                    f"【架构师的技术方案】\n{arch[:2000]}\n\n"
                    f"【你负责的任务】{tk['title']}\n{tk['task']}\n\n"
                    "请动手实现：写文件 + 真的运行验证 + 汇报结果。"
                )
                holder = {}

                def _mk(key=key, prompt=prompt, holder=holder):
                    def _fn(task=None):
                        if task is not None:
                            task.log_line(f"工程师 {key} 开工")
                        res = self.agent_fn("dev", prompt, self._work_dir(key),
                                            self.max_steps)
                        holder["res"] = res
                        return (res or {}).get("text", "")
                    return _fn

                fn = _mk()
                if self.taskman is not None:
                    t = self.taskman.submit(
                        "agent", f"{_role_title('dev')} {i + 1} · {tasks[i]['title']}",
                        fn, detail=prompt[:200], nested=True,
                        dedup_key=f"team:{self.id}:{key}")
                    self._card(key, status="running", started=time.time(),
                               note=tasks[i]["title"], task_id=t.id)
                    threads.append((key, t))
                else:
                    th = threading.Thread(
                        target=lambda k=key, f=fn: pool.__setitem__(k, f(None)),
                        daemon=True)
                    self._card(key, status="running", started=time.time(),
                               note=tasks[i]["title"])
                    th.start()
                    threads.append((key, th))

            for key, obj in threads:
                if self.cancelled:
                    break
                joiner = getattr(obj, "thread", obj)
                if joiner is not None:
                    joiner.join()
            # 收尾：把每个工程师的结果写进卡片
            dev_texts = []
            for key, obj in threads:
                res = {}
                if self.taskman is not None:
                    t = obj
                    res = {"text": t.result, "files": t.files}
                    self._card(key, status=("cancelled" if t.status == "cancelled"
                                            else "failed" if t.status == "failed"
                                            else "done"),
                               ended=t.ended or time.time(),
                               text=t.result, files=t.files, note="")
                else:
                    th = obj
                    th.join(timeout=1)
                    res = {"text": pool.get(key, ""), "files": []}
                    self._card(key, status="done", ended=time.time(),
                               text=pool.get(key, ""))
                if res.get("text"):
                    dev_texts.append(f"### {key}\n{res['text'][:2500]}")
            # 任务比工程师多 -> 剩下的也要有人做（否则交付不完整）
            for tk in tasks[n_dev:]:
                extra = self._run_role("dev:extra", (
                    f"【总目标】{self.goal}\n【技术方案】{arch[:1500]}\n"
                    f"【你负责的任务】{tk['title']}\n{tk['task']}\n"
                    "请动手实现：写文件 + 真的运行验证 + 汇报结果。"),
                    note=tk["title"])
                if extra:
                    dev_texts.append(f"### {tk['title']}\n{extra[:2500]}")

            # ===== 阶段三：验证（测试 → 组长，串行）=====
            all_files = []
            for c in self.cards.values():
                all_files += [f.get("path") for f in (c.get("files") or []) if f.get("path")]
            file_list = "\n".join(f"- {p}" for p in all_files[:40]) or "（没有登记到文件）"
            qa = self._run_role("qa", (
                f"【用户原始需求】\n{self.goal}\n\n"
                f"【产品经理的验收标准】\n{pm[:1200]}\n\n"
                f"【工程师的交付说明】\n" + "\n\n".join(dev_texts)[:4000] + "\n\n"
                f"【登记到的产物文件】\n{file_list}\n\n"
                "请独立验证：找到文件、真的跑一遍、逐条对照验收标准打勾。"),
                note="独立验证产物")

            lead = self._run_role("lead", (
                f"【用户原始需求】\n{self.goal}\n\n"
                f"【产品经理】\n{pm[:1200]}\n\n"
                f"【架构师】\n{arch[:1200]}\n\n"
                f"【工程师交付】\n" + "\n\n".join(dev_texts)[:3000] + "\n\n"
                f"【测试工程师的验证结论】\n{qa[:2000]}\n\n"
                f"【产物文件】\n{file_list}\n\n"
                "请给出最终交付说明。"), note="汇总交付")

            self.report = lead or qa or "（团队未产出汇总，请查看各角色卡片）"
            self.status = "cancelled" if self.cancelled else "done"
        except Exception as exc:                       # noqa: BLE001
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.ended = time.time()
            self._save()
            self._write_report()
            self._touch()
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
                     f"- 需求：{self.goal}",
                     f"- 状态：{self.status}",
                     f"- 用时：{round((self.ended or time.time()) - self.created, 1)} 秒", ""]
            for c in self.cards.values():
                lines += [f"## {c['title']}（{c['status']}）",
                          f"工作目录：`{os.path.join(self.dir, c['role'].replace(':', '_') + '_files')}`", ""]
                if c.get("files"):
                    lines += ["产物："] + [f"- `{f['path']}`" for f in c["files"]] + [""]
                lines += [str(c.get("text") or "")[:6000], ""]
            with open(os.path.join(self.dir, "report.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass


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

    def create(self, goal, agent_fn, taskman=None, roles=None, max_steps=6):
        rid = "team-" + time.strftime("%m%d-%H%M") + "-" + uuid.uuid4().hex[:4]
        run = TeamRun(rid, goal, self.ws_path, agent_fn, taskman=taskman,
                      roles=roles, max_steps=max_steps, on_change=self._notify)
        self._runs[rid] = run
        self._order.insert(0, rid)
        return run

    def get(self, rid):
        return self._runs.get(str(rid or "").strip())

    def list(self, limit=30):
        return [self._runs[r] for r in self._order[:limit] if r in self._runs]
