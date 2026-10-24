# -*- coding: utf-8 -*-
"""IM 助手的编排核心（v9.20.0）。

一个 AI 核心，多个"对话身份"：
- 所有会话共用**同一套模型能力**（`llm_fn` 由上层注入，可以是 API 模型，
  也可以是刚做好的 DeepSeek 接口直发）；
- 但每个会话有自己的**模式 / 人设 / 规则 / 上下文 / 记忆**（存在 store 里）；
- 共享记忆所有人都能看到，本会话记忆只在这个会话里。

本模块**不碰 Qt、不碰网络**，全部靠注入，方便直接拿假实现跑测试。
"""
import time

from . import logbook as _logbook
from . import policy, scheduler
from .store import IMStore

MAX_REPLY_CHARS = 1800      # 钉钉单条太长不好读，超了就截断（并说明）


class IMHub:
    """编排：收到消息 → 判定 → 组上下文 → 调模型 → 回消息 → 落盘。"""

    def __init__(self, store: IMStore, llm_fn, send_fn, log=None, me_name=""):
        """
        Args:
            store: `IMStore`
            llm_fn: `llm_fn(messages, thread) -> str`，返回模型正文
            send_fn: `send_fn(thread_id, text) -> bool`
            log: `log(str)` 日志回调
            me_name: 机器人在钉钉里的名字（用于识别"自己发的"和"被叫我"）
        """
        self.store = store
        self.llm_fn = llm_fn
        self.send_fn = send_fn
        self._log = log or (lambda *a, **k: None)
        self.me_name = str(me_name or "")

    def log(self, msg):
        try:
            self._log(str(msg))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def observe(self, msg):
        """只记录，不回复（用于"日志本"和上下文）。"""
        tid = str(msg.get("cid") or msg.get("thread") or "")
        if not tid:
            return None
        self.store.thread(tid, create=True, name=msg.get("conv") or "",
                          kind=msg.get("kind") or "group")
        rec = self.store.append_message(
            tid, mid=msg.get("msgid") or "", direction="in",
            sender=msg.get("sender") or "", text=msg.get("text") or "",
            ai=bool(msg.get("ai_sent")))
        try:
            _logbook.record(self.store, tid, msg.get("text") or "")
        except Exception:
            pass
        return rec

    # ------------------------------------------------------------------
    def should_reply(self, msg):
        tid = str(msg.get("cid") or msg.get("thread") or "")
        t = self.store.thread(tid, create=True, name=msg.get("conv") or "",
                              kind=msg.get("kind") or "group") or {}
        # ★ v9.20.0：把用户在「设置」里勾的范围 / 白名单一起交给策略，
        #   否则勾了「不回群聊」也照样会回。
        try:
            st = self.store.settings()
        except Exception:
            st = None
        return policy.should_reply(msg, t, self.me_name, settings=st)

    # ------------------------------------------------------------------
    def build_context(self, thread_id, limit=None, extra_system=""):
        """给这一轮准备 messages（system + 最近 N 条）。"""
        t = self.store.thread(thread_id) or {}
        msgs = self.store.messages(thread_id, limit or t.get("ctx_limit") or 20)
        try:
            ident = str((self.store.settings() or {}).get("identity") or "")
        except Exception:
            ident = ""
        return policy.build_messages(
            t, msgs, limit=limit, me_name=self.me_name,
            shared_memory=self.store.memory(),
            thread_memory=self.store.thread_memory(thread_id),
            extra_system=extra_system, identity=ident)

    # ------------------------------------------------------------------
    def handle(self, msg, timeout_note=""):
        """处理一条消息。

        Returns:
            dict{ok, replied, reason, text, error}
        """
        tid = str(msg.get("cid") or msg.get("thread") or "")
        if not tid:
            return {"ok": False, "replied": False, "reason": "没有会话 id",
                    "text": "", "error": "no-thread"}

        self.observe(msg)
        ok, reason = self.should_reply(msg)
        if not ok:
            self.log(f"不回复（{reason}）：{str(msg.get('text'))[:40]}")
            return {"ok": True, "replied": False, "reason": reason,
                    "text": "", "error": ""}

        hint = policy.reply_style_hint(msg.get("text") or "")
        msgs = self.build_context(tid, extra_system=hint)
        try:
            answer = str(self.llm_fn(msgs, self.store.thread(tid) or {}) or "").strip()
        except Exception as exc:
            answer = ""
            self.log(f"模型调用失败：{type(exc).__name__}: {exc}")
            err = f"{type(exc).__name__}: {exc}"
        else:
            err = ""

        if not answer:
            # ① 绝不静默：模型没吐字也要给一句人话，否则用户只看到"处理中"然后没了
            answer = (timeout_note or
                      "这条我这边没处理成（模型没给出内容），你再发一次试试，"
                      "或者换个说法我再来。")
            self.log("模型没有返回正文，已按兜底话术回复")
        if len(answer) > MAX_REPLY_CHARS:
            answer = answer[:MAX_REPLY_CHARS] + "\n…（太长了，剩下的我整理成文件给你）"

        self.store.append_message(tid, direction="out", sender=self.me_name,
                                  text=answer, ai=True,
                                  status="failed" if err else "sent")
        sent = False
        try:
            sent = bool(self.send_fn(tid, answer))
        except Exception as exc:
            self.log(f"发送失败：{type(exc).__name__}: {exc}")
            sent = False
        if sent:
            self.store.touch_thread(tid, answer, 0)
        return {"ok": bool(sent), "replied": True, "reason": reason if not err else err,
                "text": answer, "error": "" if sent else "send-failed"}

    # ------------------------------------------------------------------
    def tick(self, now=None):
        """检查定时任务，到点就主动发消息。返回本次发出去的任务列表。"""
        now = time.time() if now is None else float(now)
        tasks = self.store.tasks()
        due = scheduler.due_tasks(tasks, now)
        sent = []
        for t in due:
            tid = str(t.get("thread") or "")
            text = str(t.get("text") or "").strip()
            if not tid or not text:
                self.store.update_task(t.get("id"), last_run=now)
                continue
            # 标成"正在跑"，避免同一轮被重复触发
            self.store.update_task(t.get("id"), last_run=now)
            ok = False
            try:
                ok = bool(self.send_fn(tid, text))
            except Exception as exc:
                self.log(f"定时消息发送失败：{type(exc).__name__}: {exc}")
            self.store.append_message(tid, direction="out", sender=self.me_name,
                                      text=text, ai=True,
                                      status="sent" if ok else "failed")
            sent.append({"task": t, "ok": ok, "text": text})
            self.log(f"定时消息「{t.get('title')}」（{scheduler.describe(t)}）"
                     + ("已发送" if ok else "发送失败"))
        return sent

    # ------------------------------------------------------------------
    def note_memory(self, text, thread_id="", scope="shared"):
        """记一条记忆（共享或本会话）。"""
        return self.store.remember(text, thread_id, scope)

    def daily_log(self, day=None):
        day = day or time.strftime("%Y-%m-%d", time.localtime())
        return self.store.query_log(day=day)
