# -*- coding: utf-8 -*-
"""「助理」服务：轮询钉钉消息 → 交给 AI 处理 → 把结果（含产出文件）发回钉钉。

走 dws（个人 OAuth 授权，不需要企业应用），和 WorkBuddy 的钉钉连接器同一条路。
只负责「收」：轮询、去重、把新消息抛给界面；「处理」和「回」由主窗口编排
（主窗口有 Agent 和发文件能力）。这样职责清晰，也方便手动/自动两种模式。
"""
import json
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_CFG = {
    "enabled": False,      # 是否自动轮询
    "interval": 25,        # 轮询间隔（秒）
    "scope": "self",       # self=只听自己发给自己(自聊)；all=所有会话；at_me=群里@我
    "auto_reply": True,    # 处理完自动把结果发回钉钉
    "self_cid": "",        # 「自己给自己」会话的 openConversationId（自动探测后缓存）
    "processed": [],       # 已处理过的 messageId（去重，最多留 800 条）
    "primed": False,       # 是否已「跳过历史消息」（首次启动只处理新消息）
}
MAX_PROCESSED = 800


class AssistantService(QObject):
    """钉钉消息轮询服务。"""

    incoming = pyqtSignal(dict)   # 新消息 {msgid, cid, conv, sender, text, time}
    log = pyqtSignal(str)
    state = pyqtSignal(bool)      # 开/停

    def __init__(self, ding, state_path, parent=None):
        super().__init__(parent)
        self.ding = ding
        self.state_path = state_path
        self.cfg = dict(DEFAULT_CFG)
        try:
            d = json.load(open(state_path, encoding="utf-8"))
            if isinstance(d, dict):
                self.cfg.update(d)
        except Exception:
            pass
        self._running = False
        self._stop = threading.Event()
        self._thread = None
        self._last_scan = 0.0

    # ---------------- 状态 ----------------
    def save(self):
        try:
            d = dict(self.cfg)
            d["processed"] = list(d.get("processed") or [])[-MAX_PROCESSED:]
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def running(self):
        return self._running

    def apply_cfg(self, cfg):
        for k in ("interval", "scope", "auto_reply"):
            if k in cfg:
                self.cfg[k] = cfg[k]
        self.save()

    def mark_processed(self, msgid):
        if not msgid:
            return
        p = list(self.cfg.get("processed") or [])
        if msgid not in p:
            p.append(msgid)
        self.cfg["processed"] = p[-MAX_PROCESSED:]
        self.save()

    # ---------------- 起停 ----------------
    def start(self):
        if self._running:
            return
        self._running = True
        self._stop.clear()
        self.cfg["enabled"] = True
        self.save()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.state.emit(True)
        self.log.emit("助理已启动：每 %d 秒检查一次钉钉消息（范围：%s）"
                      % (int(self.cfg.get("interval") or 25), self._scope_name()))

    def stop(self):
        self._running = False
        self._stop.set()
        self.cfg["enabled"] = False
        self.save()
        self.state.emit(False)
        self.log.emit("助理已停止")

    def _scope_name(self):
        return {"self": "自己发给自己（自聊）", "all": "所有会话",
                "at_me": "群里 @我 的消息"}.get(self.cfg.get("scope"), "自聊")

    def _loop(self):
        while self._running:
            try:
                self.scan_once()
            except Exception as e:
                self.log.emit("[错误] 轮询失败：%s: %s" % (type(e).__name__, e))
            iv = max(10, int(self.cfg.get("interval") or 25))
            for _ in range(iv * 2):
                if self._stop.is_set():
                    return
                time.sleep(0.5)

    # ---------------- 轮询 ----------------
    def _self_cid(self):
        cid = self.cfg.get("self_cid") or ""
        if cid:
            return cid
        try:
            st = self.ding.dws_auth_status()
            me = (st.get("user_name") or "").strip()
        except Exception:
            me = ""
        try:
            items = (self.ding.dws_conversation_list_full(30) or {}).get("conversations") or []
        except Exception:
            return ""
        for c in items:
            nm = (c.get("conversationName") or "")
            if nm.lower().endswith("self") or (me and nm == me):
                self.cfg["self_cid"] = c.get("openConversationId") or ""
                self.save()
                return self.cfg["self_cid"]
        return ""

    def scan_once(self):
        """扫一轮，把新消息 emit 出去。返回新消息条数。"""
        if not self.ding.dws_available():
            self.log.emit("没找到 dws，助理无法读取钉钉消息")
            return 0
        try:
            st = self.ding.dws_auth_status()
        except Exception:
            st = {}
        if not st.get("authenticated"):
            self.log.emit("dws 未登录，助理暂停（请到「集成 → 钉钉」完成授权）")
            return 0
        mine = (st.get("user_name") or "").strip()
        scope = self.cfg.get("scope") or "self"
        processed = set(self.cfg.get("processed") or [])
        found = []

        if scope == "at_me":
            for m in self.ding.dws_at_me(10):
                cid = (m.get("openConversationId") or m.get("conversationId")
                       or m.get("conversation", {}).get("openConversationId") or "")
                found.append(self._wrap(m, cid))
        else:
            targets = []
            if scope == "self":
                c = self._self_cid()
                if not c:
                    self.log.emit("还没找到「自己」会话，可在「集成 → 钉钉」查看会话列表确认")
                    return 0
                targets = [(c, "自己")]
            else:
                try:
                    items = (self.ding.dws_conversation_list_full(30) or {}).get("conversations") or []
                except Exception as e:
                    self.log.emit("列会话失败：%s" % e)
                    return 0
                targets = [(c.get("openConversationId"), c.get("conversationName") or "")
                           for c in items]
            for cid, name in targets:
                if not cid:
                    continue
                try:
                    msgs = self.ding.dws_read_messages_raw(cid, 12)
                except Exception as e:
                    self.log.emit("读「%s」失败：%s" % (name, e))
                    continue
                for m in msgs:
                    found.append(self._wrap(m, cid, name))

        new, seen = [], set()
        for it in found:
            if not it or not it.get("msgid") or it.get("ai_sent"):
                continue
            if it["msgid"] in processed or it["msgid"] in seen:
                continue
            if not (it.get("text") or "").strip():
                continue
            if scope != "self" and mine and it.get("sender") == mine:
                continue          # 自己在别的会话里发的，不算给助理的任务
            seen.add(it["msgid"])
            new.append(it)
        new.sort(key=lambda x: x.get("time") or "")

        # 首次运行：把现有历史消息全部记为「已处理」，只从新消息开始干活，
        # 否则一启动就会把过去几个月的聊天记录全部当成任务处理。
        if not self.cfg.get("primed"):
            self.cfg["primed"] = True
            p = list(self.cfg.get("processed") or [])
            p.extend([it["msgid"] for it in new])
            self.cfg["processed"] = p[-MAX_PROCESSED:]
            self.save()
            self.log.emit("已跳过 %d 条历史消息，从现在开始只处理新消息" % len(new))
            return 0

        for it in new:
            self.incoming.emit(it)
        if new:
            self.log.emit("收到 %d 条新消息" % len(new))
        self._last_scan = time.time()
        return len(new)

    def _wrap(self, m, cid, convname=""):
        try:
            text = self.ding._msg_text(m)
        except Exception:
            text = (m.get("text") or m.get("content") or "")
        return {
            "msgid": m.get("messageId") or m.get("msgId") or "",
            "cid": cid,
            "conv": convname or m.get("conversationName") or "",
            "sender": m.get("sender") or m.get("senderName") or "",
            "text": text,
            "time": m.get("createTime") or m.get("sendTime") or "",
            "ai_sent": bool(m.get("messageAiSendFlag")),
        }
