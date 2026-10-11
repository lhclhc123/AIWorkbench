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

# ---------------------------------------------------------------------------
# ★ v9.18.0（用户新增要求「确保给大模型的对话要有上下文，如果过长可以适当压缩」）：
#   把「历史压缩」抽成**纯函数**，方便单元测试不依赖 Qt / 真实模型。
#   规则：
#     · 消息数太少（< min_msgs）或总字数没到 threshold 就不压，原样返回；
#     · 永远保留 system 头（用户文件夹 / 日期 / 偏好都在这里）+ 最近 keep 条，
#       **绝不**把最近上下文丢掉 —— 压缩只动"更早的"历史；
#     · 压缩前提是 summarize() 能返回非空摘要（拿不到就原样返回，绝不留白）。
def compact_history(msgs, summarize, *, threshold=70000, keep=8, min_msgs=12):
    """把过长的对话历史压成「系统摘要 + 最近若干条」。

    msgs       : 传给大模型的消息列表（含 role/content）。
    summarize  : callable(prompt_text) -> str，调用摘要模型；返回纯中文摘要。
                 由调用方注入（方法里注入 self.client.chat，测试里注入假函数），
                 这样本函数保持纯函数、可独立验证。
    返回       : 新的消息列表（压缩后）；不该压缩时**原样返回传入的那一份引用**
                 （这样上层用 ``is not`` 就能判断「到底压没压」，不会每轮都误报）。
    """
    if len(msgs) < min_msgs:
        return msgs
    total = sum(len(str(m.get("content") or "")) for m in msgs)
    if total < threshold:
        return msgs
    head = msgs[0] if msgs and msgs[0].get("role") == "system" else None
    older = msgs[1:-keep] if head else msgs[:-keep]
    if len(older) < 4:
        return msgs
    role_name = {"user": "用户", "assistant": "AI", "tool": "工具"}
    lines = []
    for m in older:
        r = role_name.get(m.get("role"), str(m.get("role")))
        lines.append(f"{r}: {str(m.get('content') or '')[:900]}")
    try:
        summary = summarize("\n".join(lines)[-30000:])
    except Exception:
        return msgs
    summary = (summary or "").strip()
    if not summary:
        return msgs
    new_msgs = []
    if head:
        new_msgs.append(head)
    new_msgs.append({"role": "system",
                     "content": "# 早前对话摘要（原始记录过长，已自动压缩）\n" + summary})
    new_msgs.extend(msgs[-keep:])
    return new_msgs


# ---------------------------------------------------------------------------
# ★ v9.16.0：一轮里多个工具调用 —— 只读、无副作用的可以**并行跑**。
#   用户原话：「模型在跑我本地命令的时候不影响它继续思考，也可以多条链路同时执行」。
#   老版本一轮拿到 5 个调用就逐个串行 execute，查 5 个网页/5 个文件要等 5 次往返。
#
#   为什么只放这些工具：并行执行最大的风险是**互相干扰** ——
#     · 写入类（write_file 等）会改同一个工作目录，必须串行；
#     · run_command / run_python 可能改环境、装包、删文件，必须串行；
#     · 需要用户确认的（命令、删除）本来就该一个一个弹窗，不能并行；
#     · 会改共享状态的（remember / update_plan / schedule_task）也不能并行。
#   只读工具即使同时跑，也只是"同时查几个东西"，结果互不影响。
_PARALLEL_SAFE_TOOLS = {
    "read_file", "list_dir", "read_document", "system_info", "now",
    "web_scrape", "http_request", "fetch_url", "web_search",
    # ★ v9.18.0（需求 2）：`web_visit` 用浏览器内核打开网页、返回正文 + 抓到的接口，
    #   全程只读（不写盘、不动环境），所以和 web_scrape 一样可以并行。
    "web_visit",
    "list_processes", "read_worklog", "list_skills", "github",
    "bg_list", "bg_status", "team_status", "check_update", "list_tasks",
    # ★ v9.18.0（需求 9「工具再多一点」）：扩容工具集里**默认只读**的工具也可并行
    #   （只读扫描/诊断，互不干扰）：disk_usage / find_duplicate / file_hash /
    #   text_diff / net_info。写入类的工具（excel_op/write、pdf_op/merge、image_batch、
    #   compile_run、pip_manage/install、git_op/push、text_op/replace）一律不进这里。
    "disk_usage", "find_duplicate", "file_hash", "text_diff", "net_info",
}
# 并行度：4 条同时查已经够快，再多只会互相抢带宽/句柄
_PARALLEL_MAX = 4

# ★ v9.17.0：AI 提问的最长等待时间。用户一直不答就按"没答"处理
#   （模型会按最合理的假设继续并在结论里注明），**不能无限等** ——
#   否则整轮对话就永远卡在等答案那一步了。
_ASK_TIMEOUT = 300.0


def _plan_parallel(calls, runner):
    """挑出这一轮里可以并行执行的调用下标。

    两道额外护栏：
      ① 同一个**显式 session 名**的网页请求不能并行 —— `requests.Session`
         不是线程安全的，两条链共用同一个会话会串 Cookie；
      ② 需要用户确认的工具（run_command / file_op 删除）绝不并行。
    """
    picked = set()
    seen_session = set()
    out = []
    for i, call in enumerate(calls):
        name = runner.canonical_name(call.get("name"))
        if name not in _PARALLEL_SAFE_TOOLS:
            continue
        args = call.get("arguments") or {}
        sess = ""
        if isinstance(args, dict):
            sess = str(args.get("session") or "").strip()
        if sess:
            if sess in seen_session:
                continue          # 同一个会话只能串行
            seen_session.add(sess)
        picked.add(i)
        out.append(i)
    # 只有一个就没必要开线程池（线程开销反而更大）
    return out if len(out) >= 2 else []


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
    reason = pyqtSignal(str)           # ★ v9.18.0（需求 17）思维链增量，边来边显示
    tool_start = pyqtSignal(str)       # 工具调用描述
    tool_result = pyqtSignal(str)      # 工具结果
    finished = pyqtSignal(list)        # 只含"本轮新增的、要显示给用户"的消息
    error = pyqtSignal(str)
    confirm_requested = pyqtSignal(str)  # 需要用户确认命令
    # ★ v9.17.0 AI 主动提问：(问题, 选项列表, 小标题, 是否多选) -> 主窗口弹提问框
    ask_requested = pyqtSignal(str, object, str, bool)
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
        # ★ v9.17.0 向用户提问：工具跑在 worker 线程里，要靠"信号 + 事件"把问题
        #   送回 UI 线程。ask_enabled 由界面接线时置 True；后台无人值守时保持
        #   False —— 那样 ask_user 会立刻退化成"按合理假设继续"，**绝不干等**。
        self._ask_event = threading.Event()
        self._ask_res = ""
        self.ask_enabled = False
        # 真思维链（reasoning_content）累积缓冲：只有支持思维链的模型才会往里写，
        # 其它模型回调 0 次，行为与以前完全一致。
        self._reason_buf = []
        # ★ v9.18.0（需求 17）：思维链要不要**实时**推给界面。
        #   正常对话=True —— 让用户看得见模型在想什么（否则"思考慢"其实只是
        #   中间过程被吞了）；程序内部自己发起的调用（上下文压缩摘要、收尾补答）
        #   会临时置 False —— 那是机器自己的活，不该混进用户的思考区。
        self._emit_reason = True

    def confirm(self, ok: bool):
        self._confirm_res = ok
        self._confirm_event.set()

    # ---------- ★ v9.17.0 AI 主动提问 ----------
    def ask_answer(self, text):
        """界面上用户答完了（或点了跳过 / 直接关掉）→ 唤醒正在等答案的工具线程。"""
        self._ask_res = text or ""
        self._ask_event.set()

    def _ask_cb_impl(self, question, options=None, header="", multi=False):
        """工具线程里被 `ask_user` 调用：把问题送回 UI 线程，等用户回答。

        没有界面在听时（后台定时任务、无人值守）直接返回空串 —— **绝不干等**，
        工具那边会退化成"按最合理的假设继续，并在结论里注明"。
        """
        if not getattr(self, "ask_enabled", False):
            return ""
        self._ask_event.clear()
        self._ask_res = ""
        try:
            self.ask_requested.emit(str(question or ""), list(options or []),
                                    str(header or ""), bool(multi))
        except Exception:
            return ""
        if not self._ask_event.wait(timeout=_ASK_TIMEOUT):
            return ""
        return self._ask_res or ""

    def abort(self):
        """用户点了右下角的方形「停止」：立刻中断本轮。

        ★ v9.18.0（需求 15「点完停止后任务要等好久才能停止」）：
          老实现只置一个标志位，靠循环"跑到下一个可中断点"才发现已经停了。
          可是循环大部分时间都**阻塞**在两处：① 读模型的流式响应；
          ② 等用户确认命令。这两处不醒，标志位就形同虚设。
          所以现在要三件事一起做，才能真正"点了就停"：
            1) 置标志位（循环里的检查点会立刻跳出）；
            2) 让 LLM 客户端把正在收流的响应关掉 —— 流一断，读循环马上返回；
            3) 把"等用户确认 / 等用户回答"的事件放行，别让后台还傻等着超时。
        """
        self._abort = True
        try:
            cb = getattr(self.client, "cancel", None)
            if callable(cb):
                cb()
        except Exception:
            pass
        # 把可能正卡着的两个等待放行（点停止 = 用户不想再管这一轮了）
        for ev, val in ((getattr(self, "_confirm_event", None), False),
                        (getattr(self, "_ask_event", None), "")):
            if ev is None:
                continue
            try:
                if ev is self._confirm_event:
                    self._confirm_res = val
                else:
                    self._ask_res = val
                ev.set()
            except Exception:
                pass

    # ---------- 并行执行一批只读工具（v9.16.0）----------
    def _run_parallel_calls(self, pairs):
        """并行跑一批只读工具，返回 {下标: 结果文本}。

        为什么不用 QThread：这些活儿是**纯 IO/等待**（读文件、发 HTTP），
        标准线程池最省事，也不会和 Qt 事件循环纠缠。真正的工具信号
        （tool_start / tool_result）仍然由本线程按原顺序发，界面行为不变。

        ⚠ 任何一个线程抛异常都不能拖垮整轮 —— 把它转成 [错误] 文本，
           让模型看到失败原因并自己决定下一步（和串行时的行为一致）。
        """
        from concurrent.futures import ThreadPoolExecutor
        out = {}
        with ThreadPoolExecutor(max_workers=min(_PARALLEL_MAX, len(pairs)),
                                thread_name_prefix="awb-par") as pool:
            futs = {pool.submit(self.runner.execute, call, None): idx
                    for idx, call in pairs}
            for fut in futs:
                idx = futs[fut]
                try:
                    out[idx] = fut.result()
                except Exception as exc:               # noqa: BLE001
                    out[idx] = f"[错误] {type(exc).__name__}: {exc}"
        return out

    # ---------- 真思维链（reasoning_content）----------
    def _clean_for_show(self, s):
        """把工具调用标记从一段文本里剥掉（捞结论时用，免得答复里全是 <tool_call>）。"""
        try:
            return agent_mod.TOOL_BODY_RE.sub(
                "", agent_mod.TOOL_BLOCK_RE.sub("", str(s or ""))).strip()
        except Exception:
            return str(s or "").strip()

    def _salvage_conclusion(self, min_len=24, stopped=True):
        """把"最近一段够大的结论"捞出来当最终答复。

        ★ v9.18.0（需求 15）用户原话：
          「把最近的大一点的结论当为最终结论」

        为什么不能简单取最后一条助手文本：Agent 循环里模型每轮吐的多半是
        「我先看看目录」这类**过程话**，几个字、没有结论价值。
        所以从后往前找**长度够大**的那一条；④ 实在什么都没有，就诚实说明
        "还没产出结论"，并把已经跑过的工具结果摘要附上 —— 绝不编。

        `stopped`：**两种完全不同的触发场景，措辞必须分开**（否则会撒谎）——
          · True  —— 用户点了停止（需求 15）。答复开头写「已按你的操作停止」。
          · False —— 循环自己跑完了却**一句话都没能说出口**（max_iter 耗尽、
                     模型连续吐空、被异常打断……）。这时写「已按你的操作停止」
                     就是假话，改成如实说"这一轮没能给出正式答复"。
                     见 `_run_impl` 收尾处那道硬保险 ——
                     复盘包实测有 **16 轮**用户完全看不到任何回应，
                     用户唯一的反应就是把同一句话再发一遍（数据里 25 句是重复）。
        """
        def _cl(s):
            return self._clean_for_show(s)

        tag = "（已按你的操作停止）" if stopped else "（这一轮我没能给出正式答复）"
        tag3 = ("（已按你的操作停止，本轮还没给出正式结论；"
                "下面是停下之前的推理进展）") if stopped else \
               ("（本轮还没给出正式结论；下面是已经跑出来的推理进展）")
        tag4 = ("（已按你的操作停止）本轮还没产出结论。" if stopped
                else "（这一轮我没能给出正式答复）本轮还没产出结论。")

        # ① 已经落库的正式答复
        for m in reversed(self.out_messages):
            if m.get("role") == "assistant":
                c = _cl(m.get("content"))
                if len(c) >= min_len:
                    return tag + "\n\n" + c
        # ② 中途那些"剥掉工具调用之后还够长"的模型输出，取最长的那条
        cands = []
        for m in self.api_messages[-8:]:
            if m.get("role") == "assistant":
                c = _cl(m.get("content"))
                if len(c) >= min_len:
                    cands.append(c)
        if cands:
            return (tag + "下面是我停下之前已经得出的结论：\n\n"
                    + max(cands, key=len))
        # ③ 思维链里最后一段
        for m in reversed(self.out_messages):
            if m.get("role") == "thinking":
                c = _cl(m.get("content"))
                if len(c) >= min_len:
                    return tag3 + "\n\n" + c
        # ④ 兜底：什么都不编，如实说清 + 附上工具结果
        tools = [_cl(m.get("content")) for m in self.out_messages
                 if m.get("role") == "tool" and _cl(m.get("content"))]
        tail = ""
        if tools:
            tail = ("\n\n**停下之前已经执行的工具**（最近几条）：\n\n"
                    + "\n".join("- " + t[:160] for t in tools[-3:]))
        return tag4 + tail

    def _collect_reason(self, delta):
        """收集思维链增量（默认 None 的模型不会调用）。

        ★ v9.18.0（需求 17）：除了往缓冲里攒（整轮结束后折成「深度思考」块落库），
          还**实时**把增量推给界面 —— 用户能看着模型一句句想，而不是对着转圈干等。
          这是"响应慢"体感最直接的缓解手段：等待时间没变，但等待**看得见**了。
        """
        if delta and not self._abort:
            self._reason_buf.append(delta)
            if self._emit_reason:
                self.reason.emit(delta)

    # ★★ v9.18.0（用户反馈）：思维链过长**不许再砍尾巴**。
    #   老实现是 `txt[:16000] + "…（已截断）"` —— 只留开头、砍掉结尾。可思维链的价值
    #   恰恰在**收尾那几段**（模型把前面绕的圈收敛成一个结论），砍尾巴等于把结论扔了，
    #   用户看到的就是"思维链被截断"。
    #   现在的策略：留一个**很高的安全上限**（防病态膨胀把对话文件撑爆），
    #   一旦真的超了，**掐中间、两头都留**（头 170k + 尾 30k）并写明丢了多少字 ——
    #   既不静默，也不丢结论。
    REASON_MAX = 200_000
    REASON_TAIL = 30_000

    def _take_reason(self, limit=None):
        """取走并清空思维链缓冲。

        `limit=None`（默认）走上面的「掐中间、保两头」策略；
        显式传数字则退回老行为（只留前 limit 字），探针会用这个口子做对照。
        """
        txt = "".join(self._reason_buf).strip()
        self._reason_buf = []
        if limit is not None:
            # 显式 limit：老语义（给探针/老调用点留的兼容口）
            if len(txt) > limit:
                txt = txt[:limit] + "\n…（思维链过长，已截断）"
            return txt
        cap = self.REASON_MAX
        if len(txt) > cap:
            head = cap - self.REASON_TAIL
            dropped = len(txt) - cap
            txt = (txt[:head]
                   + f"\n\n…（中间 {dropped} 字太长没显示；开头与**结尾结论**都已保留）…\n\n"
                   + txt[-self.REASON_TAIL:])
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
        ★ v9.18.0：抽出 `compact_history` 纯函数后，这里只是注入「真正的摘要模型」
          并回填结果；规则与边界判定都集中在纯函数里（更稳、更好测）。

        用户要求「确保给大模型的对话要有上下文，如果过长可以适当压缩」：
          · 压缩**只动更早的历史**，system 头（用户文件夹/日期/偏好）和
            最近 keep 条**永远原样保留**，上下文不丢；
          · 摘要会把「当前工作目录 / 日期 / 最近操作的文件路径 / 用户偏好」
            一并写进去，压缩后模型依然有够用的 grounding。
        """
        def _summarize(prompt_text):
            return self.client.chat(
                [{"role": "system", "content":
                    "你是对话摘要器。把下面这段早期对话压缩成一段不超过 500 字的中文摘要，"
                    "只保留这些「模型继续干活必须知道」的东西，按条目给：\n"
                    "1) 用户的目标与偏好（语言/工具/风格）；\n"
                    "2) 已经完成的事、关键结论与结论依据；\n"
                    "3) 未完成事项、下一步要做什么；\n"
                    "4) **当前工作目录（绝对路径）**与**日期**；\n"
                    "5) 涉及过的文件名与路径（相对工作目录）。\n"
                    "不要评论、不要客套、不要编造没说过的事，直接给摘要。"},
                 {"role": "user", "content": prompt_text}],
                "auto", False, None, 60)
        new_msgs = compact_history(self.api_messages, _summarize,
                                   threshold=threshold, keep=keep)
        if new_msgs is not self.api_messages:
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
            # ★ v9.17.0：把「向用户提问」的通道挂到这个 runner 上。
            #   放在这里而不是构造期，是因为 runner 是外面建好传进来的，
            #   而等待逻辑（事件 + 信号）在 worker 身上。
            try:
                self.runner.ask_cb = self._ask_cb_impl
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
                # ★ 修复 UnboundLocalError：_project 在「有工具调用」分支外（第 942 / 1245 行）
                #   也会被引用，必须每轮无条件先算好，不能只在「无工具调用」分支里赋值。
                _project = agent_mod.needs_project_flow(last_user)
                if not calls:
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
                                     + "\n\n> ⚠ **说明**：本轮我只写出了代码文件，"
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
                # ⚠ 绝不能影响 out_messages 里 assistant / tool 的语义：
                #    工作总结（wrapup -> worklog）、files 统计、假成功防御（honest_ok）
                #    都只认这两种角色，所以中间过程用独立的 thinking 角色承载。
                _think = _reason or self._clean(text)
                if _think:
                    self.out_messages.append({"role": "thinking", "content": _think})

                # ---------- 真正执行工具（一条回复里可能有多个调用，全部执行）----------
                feedback_parts = []
                blocked_repeat = False
                # ★ v9.16.0：先把"只读无副作用"的那几个**并行**跑掉。
                #   它们不改工作目录、不需要确认，同时执行只是"同时查几个东西"。
                #   结果按下标存好，下面仍按原顺序逐个做后处理 —— 这样
                #   fail_counts / write_done / verified_after_write 的语义**完全不变**。
                pre = {}
                _par = _plan_parallel(calls, self.runner)
                if _par:
                    self.phase.emit("tool", f"并行 {len(_par)} 个只读工具")
                    pre = self._run_parallel_calls([(i, calls[i]) for i in _par])
                for _ci, call in enumerate(calls):
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
                    # ★ v9.18.0：把"可能写文件"的工具也接上确认框 —— 沙箱闸门
                    #   只在**工作区外 / 受保护目标**时才真的回调它，所以工作区内的
                    #   正常写入不会多弹框；越界写入则能由用户点头放行（而不是硬拒）。
                    #   run_python 也接上：检出删除/系统级代码时要问一声。
                    need_confirm = (name == "run_command"
                                    or name == "run_python"
                                    or name in agent_mod.SANDBOX_WRITE_TOOLS
                                    or (name == "file_op"
                                        and str(call.get("arguments", {})
                                                .get("op", "")).lower() == "delete"))
                    if need_confirm:
                        def confirm_cb(text):
                            self._confirm_event.clear()
                            self.confirm_requested.emit(text)
                            self._confirm_event.wait()
                            return self._confirm_res

                    if _ci in pre:
                        # 已在并行批次里跑完（只读工具，不需要确认）
                        result = pre[_ci]
                    else:
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
                chk.append("▰ 工作区当前文件：" + (", ".join(items) if items else "（空）"))
                self.out_messages.append({
                    "role": "tool",
                    "content": "【系统完成自检】\n" + "\n".join(chk),
                })

            # ---------- 收尾：保证一定有一条最终答复 ----------
            # ★ v9.18.0（需求 15）：用户点了停止 —— **绝不再调模型**。
            #   老代码在这里会再发一次 LLM 请求去"补一条最终答复"，而模型往往
            #   又要滔滔不绝几十秒 —— 用户体感就是"点了停止它还停不下来"。
            #   改成直接从已有内容里捞最近一段够大的结论当最终答复。
            if self._abort:
                _has_good = any(
                    m.get("role") == "assistant"
                    and len(str(m.get("content") or "").strip()) >= 24
                    for m in self.out_messages)
                if not _has_good:
                    self.out_messages.append(
                        {"role": "assistant",
                         "content": self._salvage_conclusion()})
            elif not any(m.get("role") == "assistant" for m in self.out_messages):
                try:
                    self.api_messages.append({"role": "user",
                                              "content": agent_mod.WRAPUP_HINT})
                    # ★ v9.18.0（需求 17）：这是程序自己发起的「补一条最终答复」，
                    #   它的思维链不该混进用户的思考区 —— 临时关掉实时推送。
                    self._emit_reason = False
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
                finally:
                    # 无论成功与否都要还回来，否则后面几轮模型的思考全被吞掉
                    self._emit_reason = True
                if not text:
                    text = ("这一步没能自动完成。工具尝试返回如下：\n\n"
                            + "\n".join("- " + (m.get("content") or "")[:160]
                                        for m in self.out_messages
                                        if m.get("role") == "tool")[:800]
                            + "\n\n请换个说法或换个路径再让我试一次。")
                self.out_messages.append({"role": "assistant", "content": text})

            # ---------- 诚实兜底：用户要写文件，但始终没写成，别说"已完成" ----------
            if agent_mod.needs_write_action(last_user) and not write_done:
                warn = ("\n\n> ⚠ 提醒：你这次要求「写入 / 创建文件」，"
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
            if (_project and write_done and not verified_after_write
                    and not self._abort):
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

            # ---------- ★★ v9.18.0：「一句话都没说出口」的硬保险 ----------
            #   复盘包实测（120KB 真实工具调用轨迹 + 6 段真实对话）：
            #   有 **16 轮**用户完全看不到任何回应 —— 没有正文、没有思考、
            #   没有工具卡片，界面上什么都没有。用户的唯一反应就是**把同一句话
            #   再发一遍**（数据里 25 句用户消息是上一句的重复，占 12%）。
            #   这条链上的老代码是"信任上层一定能给出答复"，但只要有一环漏了
            #   （循环耗尽 / 模型连续吐空 / 清理后正文变成空串），用户就对着空气说话。
            #
            #   所以这里加一道**机械保险**，不再靠"应该没问题"：
            #   整轮跑完如果**一条非空的助手正文都没有**，就把最近一段够大的结论
            #   捞出来顶上（`_salvage_conclusion(stopped=False)` 还能退到工具结果
            #   摘要；最差也会如实说"本轮没产出结论"，绝不静默收场）。
            if not any(m.get("role") == "assistant"
                       and str(m.get("content") or "").strip()
                       for m in self.out_messages):
                self.out_messages.append(
                    {"role": "assistant",
                     "content": self._salvage_conclusion(stopped=False)})

            self.phase.emit("idle", "")
            self.finished.emit(self.out_messages)
            self._emit_wrapup(last_user, tools_used, written_paths)
        except Exception as e:
            self.phase.emit("idle", "")
            self.error.emit(str(e))
