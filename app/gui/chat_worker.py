# -*- coding: utf-8 -*-
"""对话工作线程：流式调用 LLM；Agent 模式下真实多轮循环执行工具。"""
import os
import re
import json
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from .. import llm_client, agent as agent_mod


# 最终答复里「算得上真实验证证据」的标志。
# 没有这些就说明模型只是在口头说"已完成"（实测有整轮跑了 16 次、
# 最后答复却只有"我理解了，之前的操作有重复…"这种废话）。
_PROOF_MARKERS = ("退出码", "PASS", "FAIL", "True", "False", "rc =", "rc=",
                  "标准输出", "测试通过", "断言")


def _has_real_proof(text):
    """答复里有没有贴出真实的验证证据（而不是只说"已完成"）。"""
    t = text or ""
    return any(m in t for m in _PROOF_MARKERS)


_PREVIEW_MAX_LINES = 60


def _write_preview(call):
    """把写入类工具的正文做成 Markdown 代码块，供界面展开查看。

    修的真实 bug（用户 2026-09-30 反馈"写入 python 程序时，不管写多少行，
    界面都只显示一行"）：write_file 的工具结果只有一行
    「[成功] 已写入 520 字符，共 20 行 → 完整路径：…」，
    **写入的正文根本没进展示消息** —— 所以界面上永远只有那一行。
    现在把正文一并带上，界面的代码卡片就能完整展开。
    """
    args = call.get("arguments") or {}
    if not isinstance(args, dict):
        return ""
    body = None
    for k in ("content", "text", "markdown", "source", "code"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            body = v
            break
    if body is None:
        return ""
    path = str(args.get("path") or args.get("filename")
               or args.get("file") or args.get("name") or "")
    low = path.lower()
    lang = ("python" if low.endswith(".py")
            else "markdown" if low.endswith((".md", ".markdown")) else "")
    lines = body.rstrip("\n").split("\n")
    total = len(lines)
    more = ""
    if total > _PREVIEW_MAX_LINES:
        lines = lines[:_PREVIEW_MAX_LINES]
        more = ("\n# …（全文共 %d 行，这里只预览前 %d 行；"
                "完整内容已写入文件）" % (total, _PREVIEW_MAX_LINES))
    return "```%s\n%s%s\n```" % (lang, "\n".join(lines), more)


class ChatWorker(QThread):
    token = pyqtSignal(str)            # 增量文本
    tool_start = pyqtSignal(str)       # 工具调用描述
    tool_result = pyqtSignal(str)      # 工具结果
    finished = pyqtSignal(list)        # 只含"本轮新增的、要显示给用户"的消息
    error = pyqtSignal(str)
    confirm_requested = pyqtSignal(str)  # 需要用户确认命令
    plan_changed = pyqtSignal(dict)      # 任务计划有更新（跨线程安全）
    notice = pyqtSignal(str)             # 需要提示用户的事（模型切换、上下文压缩…）
    wrapup = pyqtSignal(dict)            # 本轮结束 -> 交给主窗口写「工作总结 + 自动记忆」
    phase = pyqtSignal(str, str)         # ★ 实时状态行：("model"/"tool"/"answer"/…, 详情)

    def __init__(self, client, runner, api_messages, agent_mode,
                 model_sel="auto", enable_search=False, max_iter=16, plan=None):
        super().__init__()
        self.client = client
        self.runner = runner
        self.api_messages = [dict(m) for m in api_messages]  # 含 system 在首
        # 只放"真正要显示给用户"的消息：工具结果卡片 + 最终答复
        # （纠正提示、工具回喂等内部消息绝不进这里，否则会变成莫名其妙的对话气泡）
        self.out_messages = []
        self.agent_mode = agent_mode
        self.model_sel = model_sel
        self.enable_search = enable_search
        self.max_iter = max_iter
        self.plan = dict(plan) if plan else {"steps": []}
        self._abort = False
        self._answering = False          # 本轮是否已经开始吐字（给状态行用）
        self._confirm_event = threading.Event()
        self._confirm_res = False
        # 真思维链（reasoning_content）累积缓冲：只有支持思维链的模型才会往里写，
        # 其它模型回调 0 次，行为与以前完全一致。
        self._reason_buf = []

    def confirm(self, ok: bool):
        self._confirm_res = ok
        self._confirm_event.set()

    def abort(self):
        """用户点了右下角的方形「停止」：立刻中断本轮。

        只置标志位 —— 循环里的 `if self._abort` 会在下一个可中断点跳出，
        流式 token 也会停止转发（见 _on_token），所以界面会立即"停下"。
        已经产生的消息照常收尾（_run_impl 末尾的统一收尾逻辑会跑完）。
        """
        self._abort = True
        try:
            cb = getattr(self.client, "cancel", None)
            if callable(cb):
                cb()
        except Exception:
            pass

    # ---------- 真思维链（reasoning_content）----------
    def _collect_reason(self, delta):
        """收集思维链增量（默认 None 的模型不会调用）。"""
        if delta and not self._abort:
            self._reason_buf.append(delta)

    def _take_reason(self, limit=6000):
        """取走并清空思维链缓冲。过长时截断，避免把对话文件撑爆。"""
        txt = "".join(self._reason_buf).strip()
        self._reason_buf = []
        if len(txt) > limit:
            txt = txt[:limit] + "\n…（思维链过长，已截断）"
        return txt

    # ---------- 注入给 AgentRunner 的回调（工具执行时在工作线程内被调用）----------
    def _search_cb(self, query):
        """联网搜索工具的真实实现（走支持联网的端点）。"""
        self.phase.emit("search", str(query or "")[:40])
        try:
            return self.client.web_search(query)
        finally:
            self.phase.emit("model", "")

    def _plan_cb(self, steps, note=None):
        """update_plan 工具的真实实现：更新计划并通知界面刷新。"""
        from .. import plan as plan_mod
        self.plan = plan_mod.apply_update(self.plan, steps, note)
        self.plan_changed.emit(self.plan)
        return ("计划已更新（界面已同步显示，请继续执行，"
                "每完成一步记得再调一次 update_plan）：\n"
                + plan_mod.to_markdown(self.plan))

    def _on_token(self, delta: str):
        if self._abort:
            return
        # 第一个字到了：状态行从「正在调用模型」切到「正在生成回复」
        if not self._answering:
            self._answering = True
            self.phase.emit("answer", "")
        self.token.emit(delta)

    def _after_chat(self):
        """把 LLM 客户端攒下的提示（模型被切换等）转给界面，然后清掉。"""
        n = getattr(self.client, "notice", "")
        if n:
            self.notice.emit(n)
            try:
                self.client.notice = ""
            except Exception:
                pass

    def _maybe_compact(self, threshold=70000, keep=8):
        """历史对话太长时自动压缩：把较早的消息摘要成一段，只保留最近若干条。

        这样长对话不会因为超出上下文窗口而报错，也不会让每一轮都慢得要死。
        """
        msgs = self.api_messages
        if len(msgs) <= keep + 5:
            return
        total = sum(len(str(m.get("content") or "")) for m in msgs)
        if total < threshold:
            return
        head = msgs[0] if msgs and msgs[0].get("role") == "system" else None
        older = msgs[1:-keep] if head else msgs[:-keep]
        if len(older) < 4:
            return
        role_name = {"user": "用户", "assistant": "AI", "tool": "工具"}
        lines = []
        for m in older:
            r = role_name.get(m.get("role"), str(m.get("role")))
            lines.append(f"{r}: {str(m.get('content') or '')[:900]}")
        try:
            summary = self.client.chat(
                [{"role": "system", "content":
                    "你是对话摘要器。把下面这段对话压缩成一段不超过 500 字的中文摘要，"
                    "只保留：用户的目标与偏好、已经完成的事、关键结论与结论依据、"
                    "未完成事项、涉及的文件路径。不要评论、不要客套，直接给摘要。"},
                 {"role": "user", "content": "\n".join(lines)[-30000:]}],
                "auto", False, None, 60)
        except Exception:
            return
        summary = (summary or "").strip()
        if not summary:
            return
        new_msgs = []
        if head:
            new_msgs.append(head)
        new_msgs.append({"role": "system",
                         "content": "# 早前对话摘要（原始记录过长，已自动压缩）\n" + summary})
        new_msgs.extend(msgs[-keep:])
        self.api_messages = new_msgs
        self.notice.emit(f"历史对话较长，已自动压缩为摘要（保留最近 {keep} 条，"
                         f"前面的内容不会丢，只是变成摘要）。")

    def _clean(self, text):
        """去掉工具调用标记，只留下要给用户看的正文。"""
        s = re.sub(r"<tool_call>.*?</tool_call>", "", text or "", flags=re.DOTALL)
        s = re.sub(r"<tool_call>.*", "", s, flags=re.DOTALL)
        s = re.sub(r"<tool_?c?a?l?l?\s*$", "", s)
        return s.strip()

    def _ask_judge(self, user_text, tools_used, written_paths, final_text):
        """让模型自己当一次「完成度裁判」。

        只在规则层查不出问题时用一次，防止"回复写得像完成了但其实没做"。
        返回要补的话（str）；判定完成或拿不到结论时返回 None。
        """
        done_things = "、".join(sorted(tools_used or [])) or "（没执行过任何工具）"
        if written_paths:
            done_things += "；产出文件：" + "、".join(str(p) for p in written_paths[:5])
        try:
            raw = self.client.chat(
                agent_mod.judge_payload(user_text, done_things, final_text),
                self.model_sel, False, None, 40)
        except Exception:
            return None
        raw = (raw or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
        if obj.get("done") is True:
            return None
        missing = str(obj.get("missing") or "").strip()
        if not missing:
            return None
        return agent_mod.COMPLETION_HINT.format(gaps="- " + missing[:120])

    def _auto_verify(self, paths):
        """模型死活不肯自己跑验证时，由程序代跑一遍冒烟测试。

        2026-09-30 实测：glm-4-flash 有过整整一轮 **0 次执行** ——
        写完 prime.py 就把「自测跑通」标成已完成并交差，催办全打不动。
        用户的诉求是「你确定他做完会自己跑测试吗」，所以要给一个**硬保证**：
        模型不跑，程序替它跑，把真实输出（退出码 + stdout/stderr + PASS/FAIL）
        直接贴进答复。

        返回真实输出字符串；确实没得跑（没有 .py 产物）时返回空串。
        """
        target = ""
        for p in (paths or []):
            if p and str(p).lower().endswith(".py"):
                target = str(p)          # 取最后一个 .py 作为入口
        if not target:
            return ""
        # 相对路径（如 "calc.py"）先按工作区 files/ 解析成真实存在的文件，
        # 免得因为 cwd 不对而跑不起来、把"跑不了"误判成"跑不过"。
        try:
            if not (os.path.isabs(target) and os.path.exists(target)):
                cand = agent_mod._safe_path(self.runner.files_dir, target)
                if cand and os.path.exists(cand):
                    target = cand
        except Exception:
            pass
        self.phase.emit("verify", "运行 " + os.path.basename(target))
        safe = target.replace("\\", "\\\\").replace("'", "\\'")
        code = (
            "import importlib.util, subprocess, sys\n"
            f"p = r'{safe}'\n"
            "print('入口文件:', p)\n"
            # ① 当脚本直接跑一遍（有 __main__ 自测的会输出来）
            "r = subprocess.run([sys.executable, p], capture_output=True, text=True,\n"
            "                   encoding='utf-8', errors='replace', timeout=60)\n"
            "print('直接运行退出码:', r.returncode)\n"
            "out = (r.stdout or '').strip()\n"
            "err = (r.stderr or '').strip()\n"
            "print('标准输出:', out[-900:] if out else '(空)')\n"
            "print('标准错误:', err[-400:] if err else '(空)')\n"
            # ② 再验证能不能被 import（很多模块没有 __main__ 块，直接运行什么都不打印，
            #    但"能被正常导入"至少证明没有语法错误、没有导入期异常）
            "try:\n"
            "    spec = importlib.util.spec_from_file_location('awb_target', p)\n"
            "    mod = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(mod)\n"
            "    fns = [n for n in dir(mod)\n"
            "           if not n.startswith('_')\n"
            "           and callable(getattr(mod, n))\n"
            "           and getattr(getattr(mod, n), '__module__', None) == 'awb_target']\n"
            "    print('可导入: 是；模块里可调用的对象:', fns[:12])\n"
            "    imp_ok = True\n"
            "except Exception as e:\n"
            "    print('可导入: 否 ->', type(e).__name__, e)\n"
            "    imp_ok = False\n"
            "ok = (r.returncode == 0) and imp_ok\n"
            "print('结论: PASS —— 能被 Python 正常执行/导入，无语法或运行时错误'\n"
            "      if ok else '结论: FAIL —— 执行或导入报错，需要修')\n"
        )
        try:
            return self.runner.execute(
                {"name": "run_python", "arguments": {"code": code, "timeout": 90}})
        except Exception as e:                       # noqa: BLE001
            return f"[自动验证失败] {e}"

    def _fallback_summary(self):
        """模型只回了一堆"感谢提醒 / 我明白了"式废话时，
        用真实工具结果拼一段收尾，保证用户拿到的是实际做了什么。"""
        lines = []
        for m in self.out_messages:
            if m.get("role") != "tool":
                continue
            c = (m.get("content") or "").strip()
            if c.startswith("【系统完成自检】"):
                continue
            first = c.split("\n")[0][:140]
            if first:
                lines.append("- " + first)
        if not lines:
            return ("这一步没能自动完成。你可以换个说法或换个路径，让我再试一次。")
        return ("这是本轮实际执行的操作：\n" + "\n".join(lines[-10:])
                + "\n\n需要我继续补充或调整，直接说就行。")

    def _emit_wrapup(self, user_text, tools_used, written_paths):
        """把本轮的真实执行情况交给主窗口：写工作总结 + 自动补记忆。

        刻意放在最后、且用 signals 抛出，主窗口在后台线程里处理，
        不会拖慢界面，也不会因为归档失败影响对话。
        """
        try:
            files = list(getattr(self.runner, "written", []) or [])
            if not files:
                for p in (written_paths or []):
                    files.append({"path": p, "exists": None})
            answer = ""
            for m in reversed(self.out_messages):
                if m.get("role") == "assistant" and m.get("content"):
                    answer = m["content"]
                    break
            # ---------- 诚实判定「这一轮到底做成没有」 ----------
            # 修的真实事故（用户 2026-09-26 反馈「日志还有好大问题」）：
            #   21:29 / 21:50 两轮模型一个工具都没调、还让用户自己去 Google Drive
            #   上传文件，工作总结却写了「结果：成功」。
            # 老写法 `not any(工具报错) or bool(files)` 在"整轮零工具"时必然为 True。
            # 判据统一收在 agent.honest_ok，后台通道也用它，避免两处漂移。
            _ok, _notes = agent_mod.honest_ok(
                user_text, tools_used, files, answer,
                [m.get("content") for m in self.out_messages
                 if m.get("role") == "tool"])
            self.wrapup.emit({
                "user": (user_text or "")[:1000],
                "answer": (answer or "")[:2000],
                "tools": sorted(set(tools_used or [])),
                "files": files,
                "ok": _ok,
                "notes": _notes,
                "source": "对话",
                "model": getattr(self.client, "last_model", "") or "",
            })
        except Exception:
            pass

    def run(self):
        # 把本次运行的联网搜索 / 计划回调挂到 runner 上（同一时刻只有一个 worker，安全）
        prev_search = self.runner.search_cb
        prev_plan = self.runner.plan_cb
        self.runner.search_cb = self._search_cb
        self.runner.plan_cb = self._plan_cb
        try:
            self._run_impl()
        finally:
            self.runner.search_cb = prev_search
            self.runner.plan_cb = prev_plan

    def _run_impl(self):
        try:
            # 每轮开始先把「上一轮写过的文件」清掉，否则工作总结/完成自检会把旧文件又报一遍
            try:
                self.runner.written = []
            except Exception:
                pass
            self._maybe_compact()
            # ---------- 普通模式：单轮 ----------
            if not self.agent_mode:
                self.phase.emit("model", "")
                text = self.client.chat(
                    self.api_messages, self.model_sel, self.enable_search,
                    on_token=self._on_token, on_reasoning=self._collect_reason)
                self._after_chat()
                clean = self._clean(text)
                # 有真思维链就先留一块「深度思考」（单轮问答也能有 CoT）
                _reason = self._take_reason()
                if _reason:
                    self.out_messages.append({"role": "thinking", "content": _reason})
                self.api_messages.append({"role": "assistant", "content": clean})
                self.out_messages.append({"role": "assistant", "content": clean})
                self.phase.emit("idle", "")
                self.finished.emit(self.out_messages)
                # 普通模式也补一次收尾：写工作总结 + 自动补记忆（"每次都要写"）
                _u = ""
                for m in reversed(self.api_messages):
                    if m.get("role") == "user":
                        _u = m.get("content", "")
                        break
                self._emit_wrapup(_u, [], [])
                return

            # ---------- Agent 模式：真实多轮循环 ----------
            corrections = 0
            exec_nudges = 0
            fake_nudges = 0
            err_nudges = 0
            meta_nudges = 0
            part_nudges = 0      # "用户提了多件事，你只做了部分"的催办次数
            remember_nudges = 0  # 用户要求记住但没调 remember
            plan_nudges = 0      # 用户要求列计划但没调 update_plan
            plan_done_nudges = 0  # 计划没标完成时的收尾催办
            tool_done = 0
            tools_used = set()   # 本轮真正执行过的工具名
            write_done = False   # 本轮是否真的执行过 write_file（识别"只读不写"）
            written_paths = []   # 记录本轮真实写入过的相对路径（用于完成自检）
            write_nudges = 0     # 强制真写的纠正次数（最多 1 次）
            build_nudges = 0     # 用户要 exe 却没打 build_exe 的纠正次数（最多 1 次）
            fence_rescues = 0    # 把"只贴代码块"直接转成落盘的次数（最多 1 次）
            # —— 「独立开发一个成品」五步流程的关卡计数（各最多催 1 次）——
            project_env_nudges = 0
            project_plan_nudges = 0
            project_verify_nudges = 0
            watchdog_nudges = 0      # 「还没做完就别收尾」的打回次数（最多 4 次）
            judge_nudges = 0         # 用模型当裁判判断完成度的次数（最多 2 次）
            fix_rounds = 0           # ★ 程序代跑验证失败后「返修」的次数（最多 2 次）
            env_checked = False          # 是否真的做过环境检查
            verified_after_write = False  # 写完代码后是否真的跑过一遍
            fail_counts = {}     # 调用签名 -> 连续失败次数（防死循环）
            ok_counts = {}       # 调用签名 -> 累计成功次数（防"成功还一直重复"）
            last_tool_ok = False
            last_user = ""
            for m in reversed(self.api_messages):
                if m.get("role") == "user":
                    last_user = m.get("content", "")
                    break

            for _ in range(self.max_iter):
                if self._abort:
                    break

                self._answering = False
                self.phase.emit("model", "")
                text = self.client.chat(
                    self.api_messages, self.model_sel, self.enable_search,
                    on_token=self._on_token, on_reasoning=self._collect_reason)
                self._after_chat()
                _reason = self._take_reason()   # 本轮的思维链（无则空串）
                if not text:
                    break

                calls = agent_mod.parse_tool_calls(text)

                # A-0) 用户明确要「写程序 / 写文件」，模型却只在聊天里贴了一个代码块，
                #      一个 write_file 都没调 —— 直接拿代码块凑一个写入调用真落盘。
                #      （实测这是最高频的失败姿势：模型宁可贴代码也不落盘）
                if (not calls and not write_done and fence_rescues < 1
                        and agent_mod.needs_write_action(last_user)):
                    # 用用户点名的文件名（"写个 calc.py" -> calc.py），
                    # 别退化成笼统的 main.py
                    synth = agent_mod.synthesize_write_from_fence(
                        text,
                        default_name=(agent_mod.filename_stem_from_text(last_user)
                                      or None))
                    if synth:
                        fence_rescues += 1
                        calls = [synth]

                # 没有解析出工具调用：要么是真·最终答复，要么是格式跑偏
                if not calls:
                    # A-2) 用户要的是「独立开发出一个成品」-> 强制走工程化五步：
                    #      检查环境 -> 列任务清单 -> 生成 -> 自校验 -> 交付。
                    #      这里在模型想收尾时逐关检查，缺哪关就催哪关（各最多 1 次）。
                    _project = agent_mod.needs_project_flow(last_user)
                    if _project and not env_checked and project_env_nudges < 1:
                        project_env_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append(
                            {"role": "user",
                             "content": agent_mod.PROJECT_STEP_HINTS["env"]})
                        continue
                    if (_project and "update_plan" not in tools_used
                            and project_plan_nudges < 1):
                        project_plan_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append(
                            {"role": "user",
                             "content": agent_mod.PROJECT_STEP_HINTS["plan"]})
                        continue
                    # A-1) 用户要"真的写入/修改文件"，但模型只读不写、甚至说"模拟环境无法修改"，
                    #      或者只在嘴上说"已创建了 xxx 文件"（假成功）-> 强制它真调用写入工具。
                    #      实测：一次催办未必够（模型第一反应常是贴一段 Python 代码），故给 2 次机会。
                    _want_write = agent_mod.needs_write_action(last_user)
                    _fake_done = agent_mod.claims_wrote_file(text)
                    if (_want_write and not write_done
                            and (write_nudges < 1
                                 or (write_nudges < 2 and _fake_done))):
                        write_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        hint = agent_mod.WRITE_NUDGE_HINT
                        if _fake_done:
                            hint = (agent_mod.FAKE_WRITE_HINT + "\n\n" + hint)
                        self.api_messages.append({"role": "user", "content": hint})
                        continue
                    # A-1b) 用户明确要 exe / 打包，但模型没调 build_exe
                    #      实测：它会自己拼 pyinstaller 命令（挑到没装 PyInstaller 的 Python 而失败），
                    #      或者只贴一段打包教程、谎称"已打包完成"，最后钉钉那边啥也没收到。
                    if (agent_mod.needs_exe(last_user)
                            and "build_exe" not in tools_used
                            and build_nudges < 1):
                        build_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append(
                            {"role": "user", "content": agent_mod.BUILD_EXE_HINT})
                        continue
                    # A-2c) 项目流程最后一关：写完代码必须**真的跑一遍**才算交付
                    #       （最多催 2 次：实测 glm-4-flash 催 1 次时常常只是
                    #        把计划标成 done 就收工，第 2 次才真的去跑）
                    if (_project and write_done and not verified_after_write
                            and project_verify_nudges < 2):
                        project_verify_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append(
                            {"role": "user",
                             "content": agent_mod.PROJECT_STEP_HINTS["verify"]})
                        continue
                    # A0) 模型自己编造「工具返回」-> 打回，要求真调用（最多 1 次）
                    if fake_nudges < 1 and agent_mod.faked_tool_result(text):
                        fake_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.EXECUTE_HINT})
                        continue
                    # A) 模型想调工具但格式不对 -> 纠正后重试（这就是"多轮"）
                    if agent_mod.looks_like_tool_attempt(text) and corrections < 3:
                        corrections += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.TOOL_FORMAT_HINT})
                        continue
                    # B) 用户要的是"真的对我的电脑做点什么"，而 AI 一个工具都没执行过 ->
                    #    强制它真跑一次（最多 1 次）。覆盖两种情况：
                    #      (a) 只贴了代码没执行；
                    #      (b) 要读取/列出/查询数据，却零工具直接给结论（典型的"编造"）。
                    #    （只要已经执行过任何工具，就说明它在基于真实结果作答，不再打扰；
                    #      危险命令的优雅拒绝不含读取/写入意图，不会误触发强制执行。）
                    if (tool_done == 0 and exec_nudges < 1
                            and agent_mod.wants_real_action(last_user)
                            and (agent_mod.gave_code_only(text)
                                 or agent_mod.needs_read_action(last_user))):
                        exec_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.EXECUTE_HINT})
                        continue
                    # C) 对着系统说明"表态"而不回答用户 -> 打回（最多 2 次）
                    if meta_nudges < 2 and agent_mod.is_meta_talk(text):
                        meta_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.ANSWER_NOW_HINT})
                        continue
                    # D) 上一个工具刚报错，AI 却直接给"结果" —— 极可能是在编造，打回
                    if (err_nudges < 1 and not last_tool_ok and tool_done > 0):
                        err_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.NO_FABRICATE_HINT})
                        continue
                    # E) 用户明确要求「记住」，但 remember 一次都没调 -> 催它真记
                    if (remember_nudges < 1 and "remember" not in tools_used
                            and agent_mod.needs_remember(last_user)):
                        remember_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.REMEMBER_NUDGE_HINT})
                        continue
                    # F) 用户要求「列计划」，但 update_plan 没调 -> 催它真列
                    if (plan_nudges < 1 and "update_plan" not in tools_used
                            and agent_mod.needs_plan(last_user)):
                        plan_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.PLAN_NUDGE_HINT})
                        continue
                    # G) 用户一条消息提了多个要求，但工具明显没跑够 -> 催它做全（最多 1 次）
                    need_n = agent_mod.count_requests(last_user)
                    if (part_nudges < 1 and need_n >= 2 and tool_done < need_n):
                        part_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append({"role": "user",
                                                  "content": agent_mod.PART_NUDGE_HINT})
                        continue
                    # H) 计划还有步骤没标完成 -> 催它收尾，别让进度条停在半路（最多 1 次）
                    pending_steps = [s for s in (self.plan.get("steps") or [])
                                     if s.get("status") != "done"]
                    if (plan_done_nudges < 1 and tool_done > 0
                            and "update_plan" in tools_used and pending_steps):
                        plan_done_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append(
                            {"role": "user",
                             "content": agent_mod.PLAN_FINALIZE_HINT})
                        continue
                    # I) 【看门狗】任务没真做完就不许收尾：
                    #    以「工具真实执行过什么 / 文件真实落盘没」为准，
                    #    而不是看模型嘴上写得像不像完成了。
                    _final_preview = self._clean(text) or (text or "")
                    gaps = agent_mod.completion_gaps(
                        last_user, tools_used, write_done, verified_after_write,
                        (self.plan.get("steps") or []), _final_preview)
                    if gaps and watchdog_nudges < 4:
                        watchdog_nudges += 1
                        self.api_messages.append({"role": "assistant", "content": text})
                        self.api_messages.append(
                            {"role": "user",
                             "content": agent_mod.COMPLETION_HINT.format(
                                 gaps="\n".join("- " + g for g in gaps))})
                        continue
                    # J) 规则层看不出问题，但任务本身是"复杂任务"时，
                    #    再让模型自己当一次裁判（最多 1 次），避免"看起来做完了其实没做"。
                    if (not gaps and judge_nudges < 2
                            and (agent_mod.needs_project_flow(last_user)
                                 or agent_mod.count_requests(last_user) >= 2)):
                        judge_nudges += 1
                        verdict = self._ask_judge(last_user, tools_used,
                                                  written_paths, _final_preview)
                        if verdict:
                            self.api_messages.append(
                                {"role": "assistant", "content": text})
                            self.api_messages.append({"role": "user", "content": verdict})
                            continue
                    # 所有检查通过 -> 这才是最终答复。若它仍在"对着系统说明表态"，
                    # 换成基于真实工具结果的收尾，绝不把废话交给用户。
                    final = self._clean(text) or text
                    # ★★ 程序级「跑 -> 失败 -> 回灌真实报错 -> 让它修 -> 再跑」闭环 ★★
                    #   不依赖模型自觉：只要这轮是「开发一个成品」且真的写过代码，
                    #   系统就自己把程序跑起来。跑不过 -> 把**真实报错**塞回上下文，
                    #   逼模型自己改（最多返修 2 次），改完再跑 —— 这才是真正的多轮 agent。
                    #   （老写法只代跑一次，失败就写句"没验证过"交差。）
                    if _project and write_done:
                        auto = self._auto_verify(written_paths)
                        if auto:
                            verified_after_write = True
                            self.out_messages.append({"role": "tool", "content": auto})
                            self.tool_result.emit(auto)
                            if agent_mod.verify_failed(auto) and fix_rounds < 2:
                                fix_rounds += 1
                                self.notice.emit(
                                    "系统代跑了一遍，**没通过** —— 已把真实报错交回模型"
                                    f"继续修（第 {fix_rounds} 次返修），改完会重新运行。")
                                self.api_messages.append(
                                    {"role": "assistant", "content": text})
                                self.api_messages.append(
                                    {"role": "user",
                                     "content": agent_mod.VERIFY_FIX_HINT.format(
                                         output=auto[-2400:])})
                                continue
                            final = (final.rstrip()
                                     + "\n\n---\n**程序自动运行的验证结果**"
                                       "（系统代跑，以下是真实输出）：\n\n" + auto)
                        elif not verified_after_write:
                            final = (final.rstrip()
                                     + "\n\n> ⚠️ **说明**：本轮我只写出了代码文件，"
                                       "**没有真的运行过验证**（没跑测试）。"
                                       "上面写的「已完成」只代表文件已生成，"
                                       "**不代表验证通过**；要我再跑一遍就说一声。")
                    if agent_mod.is_meta_talk(final) and any(
                            m.get("role") == "tool" for m in self.out_messages):
                        final = self._fallback_summary()
                    # 最终答复轮的真思维链（reasoning_content）同样要留痕：
                    # 落一条默认收起的「深度思考」块，且必须排在 assistant **之前**；
                    # 一轮只会走「中间工具轮」或「最终答复轮」之一，故不会重复。
                    if _reason:
                        self.out_messages.append({"role": "thinking", "content": _reason})
                    self.api_messages.append({"role": "assistant", "content": final})
                    self.out_messages.append({"role": "assistant", "content": final})
                    break

                # ---------- 把这一轮的「中间话」留痕（可折叠的「深度思考」块）----------
                # 以前这段文字只进 api_messages，界面上"生成完就消失"。
                # 现在额外 append 成 role="thinking"，由界面渲染成默认收起的「深度思考」块。
                # 优先用模型的**真思维链**（reasoning_content）；没有思维链的模型
                # （如 glm-4-flash，回调 0 次）则退回用它的中间正文，行为与以前一致。
                # ⚠️ 绝不能影响 out_messages 里 assistant / tool 的语义：
                #    工作总结（wrapup -> worklog）、files 统计、假成功防御（honest_ok）
                #    都只认这两种角色，所以中间过程用独立的 thinking 角色承载。
                _think = _reason or self._clean(text)
                if _think:
                    self.out_messages.append({"role": "thinking", "content": _think})

                # ---------- 真正执行工具（一条回复里可能有多个调用，全部执行）----------
                feedback_parts = []
                blocked_repeat = False
                for call in calls:
                    if self._abort:
                        break
                    name = call.get("name")
                    _is_write = name in agent_mod.WRITE_TOOLS
                    sig = name + "|" + json.dumps(call.get("arguments", {}),
                                                  sort_keys=True, ensure_ascii=False)
                    if fail_counts.get(sig, 0) >= 2:
                        # 同一个调用已经失败两次，再试也没意义 -> 明确告诉模型换个办法
                        blocked_repeat = True
                        feedback_parts.append(
                            f"（工具 {name} 未执行）这个调用之前已经连续失败 2 次，"
                            f"不要再原样重试。请换一种写法或换一个路径。")
                        self.out_messages.append(
                            {"role": "tool",
                             "content": f"[跳过] {name} 这个调用已连续失败 2 次，已停止重试。"})
                        continue
                    self.phase.emit("tool", name)
                    self.tool_start.emit(
                        f"{name}  {json.dumps(call.get('arguments', {}), ensure_ascii=False)}")

                    confirm_cb = None
                    need_confirm = (name == "run_command"
                                    or (name == "file_op"
                                        and str(call.get("arguments", {})
                                                .get("op", "")).lower() == "delete"))
                    if need_confirm:
                        def confirm_cb(text):
                            self._confirm_event.clear()
                            self.confirm_requested.emit(text)
                            self._confirm_event.wait()
                            return self._confirm_res

                    result = self.runner.execute(call, confirm_cb)
                    self.tool_result.emit(result)
                    tool_done += 1
                    tools_used.add(name)
                    # 只有真写成功才算 write_done（写入报错时保持 False，好让催办继续）
                    if _is_write and str(result).startswith("[成功]"):
                        write_done = True
                        # ★ 立刻把「执行器自己记下的真实写入路径」并进 written_paths。
                        #   老代码只靠下面那个正则从返回文本里抠路径，而正则是
                        #   `完整路径：(.+?)（` —— **要求后面跟着左括号**，
                        #   但 write_file 现在返回的是
                        #   `[成功] 已写入 N 字符，共 M 行 → 完整路径：E:\x.py`
                        #   （结尾没有括号）-> 抠不出来 -> written_paths 为空 ->
                        #   循环末尾的「代跑验证」因为找不到 .py 而静默跳过
                        #   （2026-09-30 端到端实测：系统自动补跑=False）。
                        #   runner.written 是工具层亲手记录的绝对路径，最可靠。
                        try:
                            for _f in (self.runner.written or []):
                                _p = _f.get("path") if isinstance(_f, dict) else _f
                                if _p and _p not in written_paths:
                                    written_paths.append(_p)
                        except Exception:
                            pass
                    if name == "write_file":
                        # 兼容两种结尾：`完整路径：x.py（已确认落盘）` 和裸的 `完整路径：x.py`
                        m = (re.search(r"完整路径：(.+?)(?:（|$)", result, re.M)
                             or re.search(r"已写入 (.+?)(?:（|$)", result, re.M))
                        if m:
                            written_paths.append(m.group(1).strip())
                    elif name == "create_document":
                        m = re.search(r"已生成[^：]*：(.+?)（", result)
                        if m:
                            written_paths.append(m.group(1).strip())
                    elif name == "download_file":
                        m = re.search(r"已下载到 (.+?)（", result)
                        if m:
                            written_paths.append(m.group(1).strip())
                    ok = (not result.startswith("[错误]") and not result.startswith("[拒绝]")
                          and not result.startswith("[取消]"))
                    last_tool_ok = ok
                    # 记录「环境检查过了没」「写完代码后验证过没」
                    if ok and name == "system_info":
                        env_checked = True
                    if ok and name == "run_command":
                        env_checked = True
                    # ★ 「写完代码后真的验证过」必须**确实碰了刚写的那个文件**。
                    #   老写法：只要 write_done 之后再跑一个 run_python/run_command/
                    #   read_file 就算"已验证" —— 2026-09-30 实测被这个漏洞坑了：
                    #   模型写完 prime.py 后跑了一句 `print(sys.version)` 的环境检查，
                    #   就被判定"已验证过"，于是验证催办 + 完成度看门狗全都不再触发，
                    #   它直接把计划标 100% 完成，**一次冒烟测试都没跑**。
                    #   现在要求：执行的代码/命令里出现刚写的文件名（或去掉扩展名的主名），
                    #   否则不算验证。读文件也不算验证（读一眼不等于测过）。
                    if ok and write_done and name in ("run_python", "run_command"):
                        _blob = json.dumps(call.get("arguments", {}) or {},
                                           ensure_ascii=False)
                        _names = [os.path.basename(p) for p in written_paths if p]
                        _stems = [os.path.splitext(n)[0] for n in _names if n]
                        if (any(n and n in _blob for n in _names)
                                or any(s and s in _blob for s in _stems)
                                or any(s and s in str(result) for s in _stems)):
                            verified_after_write = True
                    fail_counts[sig] = 0 if ok else fail_counts.get(sig, 0) + 1
                    # 同一个调用「成功」了好几次还继续发 —— 说明模型在原地打转
                    # （实测：update_plan / start / run_python 常被重复七八次，
                    #   既烧 token 又拖慢交付）。第二次起明确叫停。
                    if ok:
                        ok_counts[sig] = ok_counts.get(sig, 0) + 1
                        if ok_counts[sig] >= 2:
                            feedback_parts.append(
                                f"（系统提醒）{name} 这个一模一样的调用你已经成功执行 "
                                f"{ok_counts[sig]} 次，结果完全相同，属于原地打转。"
                                f"立刻停止重复它：直接做下一步，"
                                f"或者给出最终答复（写清文件在哪、怎么运行）。")
                    # 工具结果作为卡片显示给用户。
                    # ★ 写入类工具必须**额外带上刚写进去的正文**：老写法只存一行
                    #   「[成功] 已写入 520 字符，共 20 行 → …」，所以无论写多少行，
                    #   界面上都只有那一行（用户 2026-09-30 反馈的 bug）。
                    #   注意：只影响给界面看的 out_messages，回灌给模型的
                    #   feedback_parts 仍是简短摘要，不浪费 token。
                    shown = result
                    if _is_write:
                        prev = _write_preview(call)
                        if prev:
                            shown = result + "\n\n" + prev
                    self.out_messages.append({"role": "tool", "content": shown})
                    feedback_parts.append(f"（工具 {name} 的真实输出）\n{result}")

                if blocked_repeat:
                    feedback_parts.append(agent_mod.NO_FABRICATE_HINT)

                # 把自己这条（含工具调用）记进上下文
                self.api_messages.append({"role": "assistant", "content": text})
                # 工具结果用 user 角色回喂：兼容性最好
                # （role=tool 需要配套 tool_call_id，很多兼容端点会直接返回 400）
                self.api_messages.append({
                    "role": "user",
                    "content": ("\n\n".join(feedback_parts) + "\n\n"
                                + agent_mod.ANSWER_NOW_HINT),
                })
                # 继续下一轮 -> 再次调用大模型

            # ---------- 系统级「完成自检」：确认写过的文件真的落盘 ----------
            # 以 runner.written（工具自己记下的绝对路径）为准，比正则抠出来的可靠
            try:
                real = [f.get("path") for f in (self.runner.written or [])
                        if f.get("path")]
                if real:
                    written_paths = real
            except Exception:
                pass
            if write_done and written_paths:
                chk = []
                for rel in written_paths:
                    # 写入目标可能是工作区外的绝对路径，也可能是相对路径
                    try:
                        ap = (rel if os.path.isabs(rel)
                              else agent_mod._safe_path(self.runner.files_dir, rel))
                    except Exception:
                        ap = None
                    if ap and os.path.exists(ap):
                        chk.append(f"✓ 已落盘：{rel}（{os.path.getsize(ap)} 字节）")
                    else:
                        chk.append(f"✗ 未找到：{rel}（写入可能失败，请检查）")
                try:
                    items = sorted(os.listdir(self.runner.files_dir))
                except Exception:
                    items = []
                chk.append("📂 工作区当前文件：" + (", ".join(items) if items else "（空）"))
                self.out_messages.append({
                    "role": "tool",
                    "content": "【系统完成自检】\n" + "\n".join(chk),
                })

            # ---------- 收尾：保证一定有一条最终答复 ----------
            if not any(m.get("role") == "assistant" for m in self.out_messages):
                try:
                    self.api_messages.append({"role": "user",
                                              "content": agent_mod.WRAPUP_HINT})
                    text = self.client.chat(self.api_messages, self.model_sel,
                                            self.enable_search, on_token=self._on_token,
                                            on_reasoning=self._collect_reason)
                    self._after_chat()
                    _reason = self._take_reason()
                    if _reason and text:
                        self.out_messages.append({"role": "thinking", "content": _reason})
                    text = (text or "").strip()
                except Exception:
                    text = ""
                if not text:
                    text = ("这一步没能自动完成。工具尝试返回如下：\n\n"
                            + "\n".join("- " + (m.get("content") or "")[:160]
                                        for m in self.out_messages
                                        if m.get("role") == "tool")[:800]
                            + "\n\n请换个说法或换个路径再让我试一次。")
                self.out_messages.append({"role": "assistant", "content": text})

            # ---------- 诚实兜底：用户要写文件，但始终没写成，别说"已完成" ----------
            if agent_mod.needs_write_action(last_user) and not write_done:
                warn = ("\n\n> ⚠️ 提醒：你这次要求「写入 / 创建文件」，"
                        "但本次**没有成功写出任何文件**（可能是我没调对工具，或路径没有权限）。"
                        "上面回复里若出现「已创建 / 已保存」字样，请以这条提醒为准。"
                        "可以再让我试一次，或把完整路径直接告诉我。")
                done = False
                for m in reversed(self.out_messages):
                    if m.get("role") == "assistant":
                        m["content"] = (m.get("content") or "") + warn
                        done = True
                        break
                if not done:
                    self.out_messages.append({"role": "assistant", "content": warn.strip()})

            # ---------- ★ 兜底代跑：循环无论怎么结束，写过代码就必须有真实输出 ----------
            #   循环可能因三种原因结束：① 正常收尾（那条路径上已经跑过闭环）
            #   ② max_iter 耗尽 ③ 用户点了停止。
            #   后两种以前直接进收尾，**代跑验证压根没机会执行**
            #   （2026-09-30 实测：模型为 ModuleNotFoundError 空转 21 轮把迭代耗尽，
            #    用户最后只拿到一句道歉，界面上一条真实输出都没有）。
            if _project and write_done and not verified_after_write:
                auto = self._auto_verify(written_paths)
                if auto:
                    verified_after_write = True
                    self.out_messages.append({"role": "tool", "content": auto})
                    self.tool_result.emit(auto)
                    _tail = ("\n\n---\n**程序自动运行的验证结果**"
                             "（系统代跑，以下是真实输出）：\n\n" + auto)
                    _added = False
                    for m in reversed(self.out_messages):
                        if m.get("role") == "assistant":
                            m["content"] = (m.get("content") or "") + _tail
                            _added = True
                            break
                    if not _added:
                        self.out_messages.append(
                            {"role": "assistant",
                             "content": "这轮没跑完就停下了，我先把程序跑了一遍，"
                                        "真实输出如下：\n\n" + auto})

            self.phase.emit("idle", "")
            self.finished.emit(self.out_messages)
            self._emit_wrapup(last_user, tools_used, written_paths)
        except Exception as e:
            self.phase.emit("idle", "")
            self.error.emit(str(e))
