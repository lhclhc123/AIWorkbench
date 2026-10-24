# -*- coding: utf-8 -*-
"""收到消息 → 立刻预取回复 → 等 N 秒 → 查你回没回 → 合并发送（v9.20.0）。

用户描述的流程（原话）：

    「AI 收到新消息 → 调用独立子进程 → 从模型拉取到回复 → 等待 60 秒
      → 判断用户是否已经有回复 → 未回复则发送消息，多条消息的合并回复」

所以这一层只管**时序与判定**，自己不碰模型、不碰网络、不碰 Qt：

    on_message()    记一条待回消息（同一会话的多条会自动合并成一个"待回批次"）
    need_fetch()    "现在该去拉回复了" —— 返回会话 id 或 None
    begin_fetch()   标记开始拉（返回 seq）
    end_fetch()     拉回来了（seq 对不上 = 期间又来新消息了，这份草稿作废重拉）
    due()           到点了：返回该发的 [{cid, msgs, text, manual, fallback}]
    finish()        发完 / 跳过之后清账

**为什么要有 seq**
等 60 秒的期间可能又来消息，那时旧草稿已经盖不住新消息了。每次 `on_message`
把 `seq` 加一，`end_fetch` 用"拿到的 seq 是否还等于当前 seq"判断这份结果是不是
最新那批消息的 —— 过时就丢掉，让 `need_fetch()` 再拉一次。这比"取消线程"干净得多：
线程照跑，结果自己作废。

**为什么"先拉后等"**
用户要的是"到点就能发"。先花时间把回复生成好放在手里，60 秒一到只看一眼有没有
人回过，没有再发 —— 用户那边是零等待的。反过来"先等再拉"会在 60 秒后还要再等
模型十几秒，体验差一个数量级。

本模块**不 import Qt、不 import requests**，全部可测。
"""
import time

MAX_MERGE = 6          # 同一个等待窗口里最多重新拉几次（防止消息狂刷时无限重拉）
GRACE_NO_DRAFT = 90    # 预取失败/超时后，再多等这么久就发兜底话术
MAX_STATES = 200       # 同时在等的会话上限（防御性，正常远小于这个）


class ReplyPipeline:
    """按时序编排"预取 → 等待 → 判定 → 发送"。"""

    def __init__(self, store=None, delay=60, log=None, me_name="",
                 max_merge=MAX_MERGE, grace=GRACE_NO_DRAFT,
                 manual_fn=None, clock=None):
        """
        Args:
            store: `IMStore`，用来查"我本人最后一条手动回复的时间"。
            delay: 等多少秒（用户要的 60）。
            log: `log(str)` 回调。
            me_name: 我在钉钉里的名字（只用于日志/记录）。
            max_merge: 同一窗口最多重拉几次。
            grace: 预取还没回来时额外宽限多少秒，超过就发兜底话术。
            manual_fn: `manual_fn(cid) -> float`，返回"我本人最后一条手动回复的时间戳"。
                       默认用 `store.last_manual_out`。注入是为了测试不依赖 store。
            clock: `clock() -> float`，默认 `time.time`（测试可注入假时钟）。
        """
        self.store = store
        self.delay = max(0, int(delay or 0))
        self.max_merge = max(1, int(max_merge or MAX_MERGE))
        self.grace = max(0, int(grace if grace is not None else GRACE_NO_DRAFT))
        self.me_name = str(me_name or "")
        self._log = log or (lambda *a, **k: None)
        self._manual_fn = manual_fn
        self._clock = clock or time.time
        self._st = {}          # cid -> state

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def now(self):
        try:
            return float(self._clock())
        except Exception:
            return time.time()

    def log(self, text):
        try:
            self._log(str(text))
        except Exception:
            pass

    def set_delay(self, seconds):
        try:
            self.delay = max(0, min(3600, int(seconds)))
        except Exception:
            pass
        return self.delay

    def manual_ts(self, cid):
        """我本人最后一次在这个会话里手动发言的时间戳（0 = 没发过）。"""
        if self._manual_fn is not None:
            try:
                return float(self._manual_fn(cid) or 0)
            except Exception:
                return 0.0
        if self.store is None:
            return 0.0
        try:
            return float(self.store.last_manual_out(cid) or 0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # 收消息
    # ------------------------------------------------------------------
    def on_message(self, msg):
        """记一条待回消息。同一个 cid 的多条会合并在一个等待窗口里。

        Returns:
            dict|None —— 该会话当前状态（给调用方打日志/刷界面用）。
        """
        msg = msg or {}
        cid = str(msg.get("cid") or msg.get("thread") or "").strip()
        text = str(msg.get("text") or "").strip()
        mid = str(msg.get("msgid") or "").strip()
        if not cid or not text:
            return None

        now = self.now()
        try:
            ts = float(msg.get("recv_ts") or now)
        except Exception:
            ts = now
        if ts <= 0:
            ts = now

        st = self._st.get(cid)
        if st is None:
            if len(self._st) >= MAX_STATES:
                # 极端情况：丢掉最早开始等的那个（不会丢消息，盘上还有）
                oldest = min(self._st.items(), key=lambda kv: kv[1].get("first_ts") or 0)
                self._st.pop(oldest[0], None)
            st = {
                "msgids": [], "msgs": [],
                "first_ts": now, "last_ts": now,
                "draft": "", "draft_ts": 0.0, "files": [],
                "fetching": False, "dirty": False, "sending": False,
                "seq": 0, "tries": 0, "last_error": "",
            }
            self._st[cid] = st

        if mid and mid in st["msgids"]:
            return st                     # 同一条重复送到，不重复计

        st["msgids"].append(mid)
        st["msgs"].append({
            "msgid": mid,
            "text": text,
            "sender": str(msg.get("sender") or ""),
            "ts": ts,
        })
        st["last_ts"] = ts
        st["seq"] += 1
        # 新消息进来，旧草稿盖不住它了 —— 作废重拉。
        # 但重拉次数用完的话，宁可留着旧草稿（能答一半）也别发套话。
        if st["tries"] < self.max_merge:
            st["draft"] = ""
        st["dirty"] = True
        return st

    # ------------------------------------------------------------------
    # 预取
    # ------------------------------------------------------------------
    def need_fetch(self):
        """该去拉回复的会话 id；没有就 None。

        调用方拿到 cid 后应该：`seq = begin_fetch(cid)` → 后台拉 → `end_fetch(cid, seq, ...)`。
        """
        for cid, st in self._st.items():
            if st["fetching"] or st["sending"]:
                continue
            if not st["dirty"]:
                continue
            if st["tries"] >= self.max_merge:
                continue
            return cid
        return None

    def batch(self, cid):
        """取某个会话当前的待回批次（预取前用它拼 prompt）。"""
        st = self._st.get(str(cid or ""))
        if st is None:
            return None
        return {"cid": cid, "msgs": list(st["msgs"]), "seq": int(st["seq"]),
                "tries": int(st["tries"])}

    def begin_fetch(self, cid):
        """标记"开始拉了"，返回这次的 seq（拉完要原样带回 `end_fetch`）。"""
        st = self._st.get(str(cid or ""))
        if st is None:
            return -1
        st["fetching"] = True
        st["dirty"] = False
        st["tries"] += 1
        return int(st["seq"])

    def end_fetch(self, cid, seq, text="", files=None, error=""):
        """拉回来了。

        `seq` 和当前 seq 不一致 → 说明等的时候又来了新消息，这份作废，重拉。
        """
        st = self._st.get(str(cid or ""))
        if st is None:
            return
        st["fetching"] = False
        err = str(error or "").strip()
        if err:
            st["last_error"] = err[:200]
            self.log("预取失败（%s）：%s" % (cid, err[:120]))
        if int(seq) != int(st["seq"]):
            # 过时了 —— 期间又来新消息了，标记重拉
            st["dirty"] = True
            self.log("预取结果已过时（%s 又来新消息），重新拉一次" % cid)
            return
        body = str(text or "").strip()
        if body:
            st["draft"] = body
            st["draft_ts"] = self.now()
            st["files"] = list(files or [])
            st["last_error"] = ""

    # ------------------------------------------------------------------
    # 到点判定
    # ------------------------------------------------------------------
    def due(self, now=None):
        """到点该处理的批次。调用方处理完**必须**调 `finish()`。

        Returns:
            list[dict]，每项：
              cid     会话 id
              msgs    这一批的消息 [{msgid,text,sender,ts}]
              text    要发的内容（`fallback=True` 或 `manual=True` 时可能是空）
              files   产出文件（预取时带回来的）
              manual  True = 这期间我本人已经回过了，**不要代回**
              fallback True = 预取没成功，text 为空，调用方该发兜底话术
              waited  等了多久
              seq     对应哪次预取
        """
        now = self.now() if now is None else float(now)
        out = []
        for cid, st in list(self._st.items()):
            if st["fetching"] or st["sending"]:
                continue
            waited = now - float(st["last_ts"] or 0)
            if waited < self.delay:
                continue

            # ① 用户自己回过了 —— 绝不代回
            mts = self.manual_ts(cid)
            if mts and mts > float(st["last_ts"] or 0):
                st["sending"] = True
                out.append({"cid": cid, "msgs": list(st["msgs"]), "text": "",
                            "files": [], "manual": True, "fallback": False,
                            "waited": waited, "seq": int(st["seq"])})
                continue

            # ② 草稿还没有 —— 再给点宽限；实在拿不到就走兜底
            if not st["draft"]:
                if waited < self.delay + self.grace:
                    continue
                st["sending"] = True
                out.append({"cid": cid, "msgs": list(st["msgs"]), "text": "",
                            "files": [], "manual": False, "fallback": True,
                            "waited": waited, "seq": int(st["seq"]),
                            "error": st.get("last_error") or "预取超时"})
                continue

            # ③ 正常发送
            st["sending"] = True
            out.append({"cid": cid, "msgs": list(st["msgs"]),
                        "text": st["draft"], "files": list(st["files"]),
                        "manual": False, "fallback": False,
                        "waited": waited, "seq": int(st["seq"])})
        return out

    def finish(self, cid, sent=False, why=""):
        """一批处理完了：清账（不管发没发出去都要调，否则会卡住这一批）。"""
        cid = str(cid or "")
        st = self._st.pop(cid, None)
        if st is None:
            return
        n = len(st.get("msgids") or [])
        if sent:
            self.log("已代回（%s，%d 条合并，等了 %.0f 秒）"
                     % (cid, n, self.now() - float(st.get("first_ts") or 0)))
        elif why:
            self.log("没有代回（%s，%d 条）：%s" % (cid, n, why))

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------
    def forget(self, msgids):
        """用户把消息删了 —— 从等待窗口里摘掉（摘空了就整个丢掉）。"""
        drop = set(str(x) for x in (msgids or []))
        if not drop:
            return
        for cid, st in list(self._st.items()):
            st["msgs"] = [m for m in st["msgs"] if str(m.get("msgid")) not in drop]
            st["msgids"] = [i for i in st["msgids"] if str(i) not in drop]
            if not st["msgids"] and not st["fetching"]:
                self._st.pop(cid, None)

    def drop(self, cid):
        """放弃某个会话的等待窗口（比如用户关了自动回复）。"""
        self._st.pop(str(cid or ""), None)

    def clear(self):
        self._st.clear()

    def pending(self):
        """调试/界面用：当前还在等什么。"""
        out = {}
        for cid, st in self._st.items():
            out[cid] = {
                "count": len(st["msgids"]),
                "msgids": list(st["msgids"]),
                "draft": bool(st["draft"]),
                "fetching": bool(st["fetching"]),
                "tries": int(st["tries"]),
                "waited": self.now() - float(st["last_ts"] or 0),
                "left": max(0.0, self.delay - (self.now() - float(st["last_ts"] or 0))),
            }
        return out

    def describe(self):
        """一行中文摘要（写给运行日志）。"""
        if not self._st:
            return "没有在等的消息"
        parts = []
        for cid, st in self._st.items():
            if st["fetching"]:
                s = "取回复中"
            elif st["draft"]:
                s = "草稿就绪"
            else:
                s = "待取"
            parts.append("%s×%d/%s" % (cid[:8], len(st["msgids"]), s))
        return "；".join(parts[:8])

    # ------------------------------------------------------------------
    def build_prompt_text(self, batch, me_name=""):
        """把这一批消息拼成给模型看的"这次要回的内容"（多条合并）。

        多条消息用编号列出来，让模型一次把几件事都答掉，而不是只答最后一条。
        """
        msgs = list((batch or {}).get("msgs") or [])
        if not msgs:
            return ""
        if len(msgs) == 1:
            m = msgs[0]
            who = m.get("sender") or ""
            head = ("%s：%s" % (who, m.get("text"))) if who else str(m.get("text") or "")
            return head
        lines = []
        for i, m in enumerate(msgs, 1):
            who = m.get("sender") or "对方"
            lines.append("%d. %s：%s" % (i, who, m.get("text")))
        return ("（对方连着发了几条，请**合并成一条回复**一次答完，"
                "别一条条回、也别复述他们的话）\n" + "\n".join(lines))
