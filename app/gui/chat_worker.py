# -*- coding: utf-8 -*-
"""对话工作线程：流式调用 LLM；Agent 模式下真实多轮循环执行工具。"""
import os
import re
import json
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from .. import llm_client, agent as agent_mod


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
        self._confirm_event = threading.Event()
        self._confirm_res = False

    def confirm(self, ok: bool):
        self._confirm_res = ok
        self._confirm_event.set()

    # ---------- 注入给 AgentRunner 的回调（工具执行时在工作线程内被调用）----------
    def _search_cb(self, query):
        """联网搜索工具的真实实现（走支持联网的端点）。"""
        return self.client.web_search(query)

    def _plan_cb(self, steps, note=None):
        """update_plan 工具的真实实现：更新计划并通知界面刷新。"""
        from .. import plan as plan_mod
        self.plan = plan_mod.apply_update(self.plan, steps, note)
        self.plan_changed.emit(self.plan)
        return ("计划已更新（界面已同步显示，请继续执行，"
                "每完成一步记得再调一次 update_plan）：\n"
                + plan_mod.to_markdown(self.plan))

    def _on_token(self, delta: str):
        if not self._abort:
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
            self.wrapup.emit({
                "user": (user_text or "")[:1000],
                "answer": (answer or "")[:2000],
                "tools": sorted(set(tools_used or [])),
                "files": files,
                "ok": not any(str(m.get("content") or "").startswith(
                    "[错误]") for m in self.out_messages
                    if m.get("role") == "tool") or bool(files),
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
                text = self.client.chat(
                    self.api_messages, self.model_sel, self.enable_search,
                    on_token=self._on_token)
                self._after_chat()
                clean = self._clean(text)
                self.api_messages.append({"role": "assistant", "content": clean})
                self.out_messages.append({"role": "assistant", "content": clean})
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
            fence_rescues = 0    # 把"只贴代码块"直接转成落盘的次数（最多 1 次）
            # —— 「独立开发一个成品」五步流程的关卡计数（各最多催 1 次）——
            project_env_nudges = 0
            project_plan_nudges = 0
            project_verify_nudges = 0
            watchdog_nudges = 0      # 「还没做完就别收尾」的打回次数（最多 4 次）
            judge_nudges = 0         # 用模型当裁判判断完成度的次数（最多 2 次）
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

                text = self.client.chat(
                    self.api_messages, self.model_sel, self.enable_search,
                    on_token=self._on_token)
                self._after_chat()
                if not text:
                    break

                calls = agent_mod.parse_tool_calls(text)

                # A-0) 用户明确要「写程序 / 写文件」，模型却只在聊天里贴了一个代码块，
                #      一个 write_file 都没调 —— 直接拿代码块凑一个写入调用真落盘。
                #      （实测这是最高频的失败姿势：模型宁可贴代码也不落盘）
                if (not calls and not write_done and fence_rescues < 1
                        and agent_mod.needs_write_action(last_user)):
                    synth = agent_mod.synthesize_write_from_fence(text)
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
                    # A-2c) 项目流程最后一关：写完代码必须**真的跑一遍**才算交付
                    if (_project and write_done and not verified_after_write
                            and project_verify_nudges < 1):
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
                    if agent_mod.is_meta_talk(final) and any(
                            m.get("role") == "tool" for m in self.out_messages):
                        final = self._fallback_summary()
                    self.api_messages.append({"role": "assistant", "content": final})
                    self.out_messages.append({"role": "assistant", "content": final})
                    break

                # ---------- 真正执行工具（一条回复里可能有多个调用，全部执行）----------
                feedback_parts = []
                blocked_repeat = False
                for call in calls:
                    if self._abort:
                        break
                    name = call.get("name")
                    _is_write = name in ("write_file", "create_document", "archive",
                                         "download_file", "screenshot")
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
                    if name == "write_file":
                        m = re.search(r"完整路径：(.+?)（", result) or \
                            re.search(r"已写入 (.+?)（", result)
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
                    if ok and write_done and name in ("run_python", "run_command",
                                                      "read_file", "read_document"):
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
                    # 工具结果作为黄色卡片显示给用户
                    self.out_messages.append({"role": "tool", "content": result})
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
                                            self.enable_search, on_token=self._on_token)
                    self._after_chat()
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

            self.finished.emit(self.out_messages)
            self._emit_wrapup(last_user, tools_used, written_paths)
        except Exception as e:
            self.error.emit(str(e))
