# -*- coding: utf-8 -*-
"""钉钉 / IM 助手的**持久化底座**（v9.20.0）。

为什么单独一个包
----------------
老实现（`app/assistant.py`）只做"扫消息 → emit → 主窗口内存队列"，一律不落盘：
程序一重启，待处理消息、处理结果、上下文全部消失 —— 用户看到的就是
「AI 正在处理中，最后消息消失不见、不回复」。

这里把 IM 助手真正需要长期存在的东西**分类落盘**，并且全部是**人类可读的
纯文本/JSON**（用户要求"能在界面中审查上下文"）：

```
<工作区>/.awb/im/
├── threads.json          每个会话的档案（模式/人设/上下文条数/最后活跃）
├── msgs/<会话>.jsonl     消息流水（进/出、谁说的、是不是 AI、处理状态）
├── tasks.json            定时任务（每日/每周/每月主动发消息）
├── logbook.jsonl         日志本（谁 哪天 做什么 —— 值日/排班）
├── memory.md             共享记忆（所有会话都能看到）
└── mem/<会话>.md         这个会话自己的记忆
```

设计原则
--------
1. **append-only 为主**：消息流水只追加，读的时候截断，避免边读边写损坏。
2. **每个写操作都可单独失败**：任何一次写盘异常都不允许把对话主流程带崩。
3. **不做隐式迁移**：老版本没有这些文件也能跑（空列表 + 默认值）。
"""
import json
import os
import re
import time

MAX_MSGS_READ = 60          # 默认取最近多少条做上下文
MAX_LINES_PER_THREAD = 4000  # 单个会话流水超过这个行数就压缩一次

MODES = {
    "secretary": "秘书模式 —— 默认主动汇报、提醒；群里不闲聊，只在有明确指令或 @ 时发言",
    "colleague": "同事模式 —— 像同事一样正常聊天，被 @ 或被叫到名字时接话",
    "group_bot": "群助手模式 —— 盯住群聊，按指令/关键词回，@ 优先",
}
DEFAULT_MODE = "group_bot"

# 界面上「自动回复设置」那一页的东西，全部落在 `<root>/im_settings.json`
IM_SETTINGS_FILE = "im_settings.json"
IM_DEFAULT_SETTINGS = {
    "scope_group": True,       # 自动回复范围：群聊
    "scope_p2p": True,         # 自动回复范围：单聊
    "scope_at_me": True,       # 群里只在 @我 时才接（False=群里所有消息都算）
    "whitelist": [],           # 白名单 [{"id": cid, "name": 名称, "kind": "group"|"p2p"}]
    "delay_seconds": 60,       # 我多少秒没自己回，才由 AI 代回（0=立刻）
    "reply_uncertain": True,   # AI 拿不准时要不要回（False=不猜，标记需人工）
    "uncertain_text": "消息已纳入收件箱，加急处理中，主人正在赶来的路上，此消息由AI代回",
    "identity": "",            # 用户自填的身份背景（口吻/关系/最近发生的事）
    "scanned": False,          # 是否做过首次全量扫描打标签
    "interval": 25,            # 轮询间隔（秒）
}

# 布尔 / 整数 / 纯文本 三类 key，set_settings 时分别处理
_SETTING_BOOLS = ("scope_group", "scope_p2p", "scope_at_me",
                  "reply_uncertain", "scanned")
_SETTING_INTS = ("delay_seconds", "interval")
_SETTING_TEXTS = ("uncertain_text", "identity")


def _now():
    return time.time()


def _safe(name):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" ._")
    return (s[:40] or "thread")


class IMStore:
    """IM 数据仓库。所有路径都落在工作区的 `.awb/im/` 下。"""

    def __init__(self, workspace_path):
        self.root = os.path.join(str(workspace_path), ".awb", "im")
        self.threads_file = os.path.join(self.root, "threads.json")
        self.tasks_file = os.path.join(self.root, "tasks.json")
        self.logbook_file = os.path.join(self.root, "logbook.jsonl")
        self.memory_file = os.path.join(self.root, "memory.md")
        self.msgs_dir = os.path.join(self.root, "msgs")
        self.mem_dir = os.path.join(self.root, "mem")
        for d in (self.root, self.msgs_dir, self.mem_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 低层读写
    # ------------------------------------------------------------------
    @staticmethod
    def _read_json(path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, type(default)) else default
        except Exception:
            return default

    @staticmethod
    def _write_json(path, data):
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    def _msgs_path(self, thread_id):
        return os.path.join(self.msgs_dir, _safe(thread_id) + ".jsonl")

    # ------------------------------------------------------------------
    # 会话档案
    # ------------------------------------------------------------------
    def threads(self):
        d = self._read_json(self.threads_file, {})
        return d if isinstance(d, dict) else {}

    def thread(self, thread_id, create=False, name="", kind="group"):
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        all_t = self.threads()
        t = all_t.get(tid)
        if t is None:
            if not create:
                return None
            t = {
                "id": tid,
                "name": name or tid,
                "kind": kind or "group",        # group / p2p / self
                "mode": DEFAULT_MODE,
                "persona": "",                  # 人设/语气补充（用户可编辑）
                "rules": "",                    # 额外指令/规则（用户可编辑）
                "ctx_limit": MAX_MSGS_READ,     # 带多少条上下文
                "memory_on": True,              # 是否带本会话记忆
                "enabled": True,
                "created": _now(),
                "last_ts": 0.0,
                "last_text": "",
                "msg_count": 0,
            }
            all_t[tid] = t
            self._write_json(self.threads_file, all_t)
        elif name and t.get("name") in ("", tid):
            t["name"] = name
            all_t[tid] = t
            self._write_json(self.threads_file, all_t)
        return t

    def update_thread(self, thread_id, **kw):
        """改会话档案（mode / persona / rules / ctx_limit / memory_on / enabled / name）。"""
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        all_t = self.threads()
        t = all_t.get(tid) or self.thread(tid, create=True)
        if not t:
            return None
        for k, v in kw.items():
            if k in ("mode", "persona", "rules", "ctx_limit", "memory_on",
                     "enabled", "name", "kind"):
                t[k] = v
        all_t[tid] = t
        self._write_json(self.threads_file, all_t)
        return t

    def touch_thread(self, thread_id, text="", delta=1):
        all_t = self.threads()
        t = all_t.get(str(thread_id or ""))
        if not t:
            return
        t["last_ts"] = _now()
        if text:
            t["last_text"] = str(text)[:120]
        try:
            t["msg_count"] = int(t.get("msg_count") or 0) + int(delta)
        except Exception:
            t["msg_count"] = int(delta)
        all_t[str(thread_id)] = t
        self._write_json(self.threads_file, all_t)

    # ------------------------------------------------------------------
    # 消息流水
    # ------------------------------------------------------------------
    def append_message(self, thread_id, mid="", direction="in", sender="", text="",
                       ai=False, status="", extra=None):
        """追加一条消息。`direction`：in=收到 / out=我们发出的。"""
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        rec = {
            "mid": str(mid or ""),
            "ts": _now(),
            "dir": "out" if direction == "out" else "in",
            "sender": str(sender or ""),
            "text": str(text or ""),
            "ai": bool(ai),
            "status": str(status or ""),
        }
        if extra:
            rec.update(extra)
        try:
            with open(self._msgs_path(tid), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            return rec
        self.touch_thread(tid, rec["text"], 1)
        return rec

    def messages(self, thread_id, limit=MAX_MSGS_READ):
        """读最近 N 条（按时间正序返回）。"""
        tid = str(thread_id or "").strip()
        if not tid:
            return []
        out = []
        try:
            with open(self._msgs_path(tid), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        if limit and len(out) > int(limit):
            out = out[-int(limit):]
        return out

    def set_status(self, thread_id, mid, status, answer=""):
        """把某条消息的处理状态写回（用于"AI 到底回了没有"的可审查性）。

        jsonl 是 append-only，改写成本高，这里用**追加一条状态记录**的方式，
        读的时候以最后一条同 mid 的状态为准（见 `messages()` 的调用方）。
        """
        return self.append_message(thread_id, mid=mid, direction="out",
                                   sender="", text=answer or "",
                                   ai=True, status=status,
                                   extra={"status_of": mid})

    def compact(self, thread_id):
        """行数太多时压缩（只留最近 MAX_LINES_PER_THREAD 条）。"""
        tid = str(thread_id or "").strip()
        p = self._msgs_path(tid)
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = [ln for ln in f if ln.strip()]
            if len(lines) <= MAX_LINES_PER_THREAD:
                return 0
            keep = lines[-MAX_LINES_PER_THREAD:]
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, p)
            return len(lines) - len(keep)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # 记忆
    # ------------------------------------------------------------------
    def memory(self):
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def set_memory(self, text):
        try:
            os.makedirs(self.root, exist_ok=True)
            with open(self.memory_file, "w", encoding="utf-8") as f:
                f.write(str(text or ""))
            return True
        except Exception:
            return False

    def remember(self, text, thread_id="", scope="shared"):
        """追加一条记忆。`scope=shared` 进共享记忆，否则进该会话自己的记忆。"""
        line = "- " + str(text or "").strip().replace("\n", " ")
        if not line.strip("- "):
            return False
        if scope == "shared":
            p = self.memory_file
        else:
            if not thread_id:
                p = self.memory_file
            else:
                p = os.path.join(self.mem_dir, _safe(thread_id) + ".md")
        try:
            old = ""
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    old = f.read()
            if line in old:
                return True
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        except Exception:
            return False

    def thread_memory(self, thread_id):
        p = os.path.join(self.mem_dir, _safe(thread_id) + ".md")
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 定时任务
    # ------------------------------------------------------------------
    def tasks(self):
        d = self._read_json(self.tasks_file, [])
        return d if isinstance(d, list) else []

    def save_tasks(self, tasks):
        return self._write_json(self.tasks_file, list(tasks or []))

    def add_task(self, task):
        ts = self.tasks()
        t = dict(task or {})
        t.setdefault("id", "t%d" % int(_now() * 1000))
        t.setdefault("enabled", True)
        t.setdefault("last_run", 0.0)
        ts.append(t)
        self.save_tasks(ts)
        return t

    def update_task(self, task_id, **kw):
        ts = self.tasks()
        for i, t in enumerate(ts):
            if str(t.get("id")) == str(task_id):
                t.update(kw)
                ts[i] = t
                self.save_tasks(ts)
                return t
        return None

    def remove_task(self, task_id):
        ts = self.tasks()
        out = [t for t in ts if str(t.get("id")) != str(task_id)]
        self.save_tasks(out)
        return len(ts) != len(out)

    # ------------------------------------------------------------------
    # 日志本
    # ------------------------------------------------------------------
    def logbook(self):
        out = []
        try:
            with open(self.logbook_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            return []
        return out

    def add_log(self, who, what, day="", thread_id="", raw=""):
        rec = {"ts": _now(), "who": str(who or ""), "what": str(what or ""),
               "day": str(day or ""), "thread": str(thread_id or ""),
               "raw": str(raw or "")[:200]}
        try:
            with open(self.logbook_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            return rec
        return rec

    def query_log(self, day="", who=""):
        out = []
        for r in self.logbook():
            if day and str(r.get("day") or "") != str(day):
                continue
            if who and str(who) not in str(r.get("who") or ""):
                continue
            out.append(r)
        return out

    # ------------------------------------------------------------------
    # 设置（界面「自动回复设置」页）
    # ------------------------------------------------------------------
    @property
    def settings_file(self):
        return os.path.join(self.root, IM_SETTINGS_FILE)

    @staticmethod
    def _clean_whitelist(raw):
        """白名单只保留 id / name / kind 三个字段，顺手去掉空 id 和重复 id。"""
        out, seen = [], set()
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "").strip()
            if not cid or cid in seen:
                continue
            kind = str(item.get("kind") or "group").strip().lower()
            if kind not in ("group", "p2p"):
                kind = "group"
            seen.add(cid)
            out.append({"id": cid, "name": str(item.get("name") or ""),
                        "kind": kind})
        return out

    def settings(self):
        """默认值 + 文件里的值（只认 IM_DEFAULT_SETTINGS 里有的 key）。"""
        raw = self._read_json(self.settings_file, {})
        s = {}
        for k, v in IM_DEFAULT_SETTINGS.items():
            s[k] = list(v) if isinstance(v, list) else v
        if isinstance(raw, dict):
            for k in IM_DEFAULT_SETTINGS:
                if k not in raw:
                    continue
                v = raw[k]
                if k == "whitelist":
                    if isinstance(v, list):
                        s[k] = self._clean_whitelist(v)
                elif k in _SETTING_INTS:
                    try:
                        s[k] = int(v)
                    except Exception:
                        pass
                elif k in _SETTING_BOOLS:
                    s[k] = bool(v)
                elif k in _SETTING_TEXTS:
                    s[k] = "" if v is None else str(v)
        # 越界的值读出来也纠正，避免手改文件改出个负数
        s["delay_seconds"] = max(0, min(3600, int(s.get("delay_seconds") or 0)))
        s["interval"] = max(10, min(600, int(s.get("interval") or 25)))
        return s

    def set_settings(self, **kw):
        """只改传进来的 key，写盘后返回合并后的完整设置。"""
        cur = self.settings()
        for k, v in kw.items():
            if k not in IM_DEFAULT_SETTINGS:
                continue
            if k == "whitelist":
                cur[k] = self._clean_whitelist(v)
            elif k == "delay_seconds":
                try:
                    cur[k] = max(0, min(3600, int(v)))
                except Exception:
                    continue
            elif k == "interval":
                try:
                    cur[k] = max(10, min(600, int(v)))
                except Exception:
                    continue
            elif k in _SETTING_BOOLS:
                cur[k] = bool(v)
            elif k in _SETTING_TEXTS:
                cur[k] = "" if v is None else str(v)
        self._write_json(self.settings_file, cur)
        return cur

    def whitelist_add(self, cid, name="", kind="group"):
        """加白名单（同 id 就更新，不重复）。返回新白名单 list。"""
        cid = str(cid or "").strip()
        if not cid:
            return self.settings().get("whitelist") or []
        lst = self._clean_whitelist(self.settings().get("whitelist"))
        kind = str(kind or "group").strip().lower()
        if kind not in ("group", "p2p"):
            kind = "group"
        for item in lst:
            if item["id"] == cid:
                item["name"] = str(name or item.get("name") or "")
                item["kind"] = kind
                break
        else:
            lst.append({"id": cid, "name": str(name or ""), "kind": kind})
        return self.set_settings(whitelist=lst).get("whitelist") or []

    def whitelist_remove(self, cid):
        cid = str(cid or "").strip()
        lst = [x for x in self._clean_whitelist(self.settings().get("whitelist"))
               if x["id"] != cid]
        return self.set_settings(whitelist=lst).get("whitelist") or []

    # ------------------------------------------------------------------
    # 会话标签（写进 thread 档案，不另开文件）
    # ------------------------------------------------------------------
    def set_tag(self, thread_id, tag, note="", auto=False):
        """给会话打标签。`tag=""` 表示"不知道"（等于清空）。"""
        tid = str(thread_id or "").strip()
        if not tid:
            return None
        all_t = self.threads()
        t = all_t.get(tid)
        if t is None:
            t = self.thread(tid, create=True) or {}
        t["tag"] = str(tag or "").strip()[:20]
        t["tag_note"] = str(note or "")[:120]
        t["tag_auto"] = bool(auto)
        all_t[tid] = t
        self._write_json(self.threads_file, all_t)
        return t

    def tags(self):
        """{tid: {id,name,kind,tag,tag_note,tag_auto,last_ts,msg_count}}（只读）。"""
        out = {}
        for tid, t in (self.threads() or {}).items():
            t = t or {}
            out[str(tid)] = {
                "id": str(t.get("id") or tid),
                "name": str(t.get("name") or ""),
                "kind": str(t.get("kind") or "group"),
                "tag": str(t.get("tag") or ""),
                "tag_note": str(t.get("tag_note") or ""),
                "tag_auto": bool(t.get("tag_auto")),
                "last_ts": float(t.get("last_ts") or 0.0),
                "msg_count": int(t.get("msg_count") or 0),
            }
        return out

    # ------------------------------------------------------------------
    # 界面两栏（收到的消息 + AI 的回复）
    # ------------------------------------------------------------------
    def rows(self, limit=400):
        """把消息流水折成界面要的"左边一条消息 / 右边一条回复"。"""
        out = []
        for tid, t in (self.threads() or {}).items():
            t = t or {}
            tid = str(tid)
            msgs = self.messages(tid, limit=3000)

            status_rec, dropped, free_out = {}, set(), []
            for m in msgs:
                so = str(m.get("status_of") or "")
                if so:
                    status_rec[so] = m
                if str(m.get("status") or "") == "deleted" and so:
                    dropped.add(so)
                if (m.get("dir") == "out" and not m.get("status_of")
                        and m.get("ai")):
                    try:
                        free_out.append((float(m.get("ts") or 0),
                                         str(m.get("text") or "")))
                    except Exception:
                        continue

            name = str(t.get("name") or "").strip()
            if not name or name == tid:
                name = "钉钉"
            kind = str(t.get("kind") or "").strip().lower()

            for m in msgs:
                if m.get("dir") != "in":
                    continue
                mid = str(m.get("mid") or "").strip()
                if not mid or mid in dropped:
                    continue
                try:
                    ts = float(m.get("ts") or 0)
                except Exception:
                    ts = 0.0
                status, reply, reply_ts = "待处理", "", 0.0
                rec = status_rec.get(mid)
                if rec:
                    st = str(rec.get("status") or "").strip()
                    if st in ("sent", "已回复", "ok"):
                        status, reply = "已回复", str(rec.get("text") or "")
                        reply_ts = float(rec.get("ts") or 0)
                    elif st in ("处理中", "running"):
                        status = "处理中"
                    elif st in ("failed", "失败", "error"):
                        status, reply = "失败", str(rec.get("text") or "")
                        reply_ts = float(rec.get("ts") or 0)
                    elif st in ("manual", "需人工", "uncertain"):
                        status = "需人工"
                    # ★ v9.20.0：多条消息合并成一条回复时，只有最后那条带正文，
                    #   前面的写 merged —— 界面上要说清楚"是和另一条一起回的"，
                    #   而不是显示成"暂无回复"。
                    elif st in ("merged", "合并"):
                        status, reply = "已回复", "（和另一条一起回了）"
                        reply_ts = float(rec.get("ts") or 0)
                if not reply:
                    for ots, otext in free_out:
                        if ots > ts:
                            reply, reply_ts, status = otext, ots, "已回复"
                            break
                row_kind = kind
                if row_kind not in ("group", "p2p", "self"):
                    row_kind = ("p2p" if (t.get("name")
                                          and str(m.get("sender") or "") == str(t.get("name")))
                                else "group")
                out.append({
                    "msgid": mid,
                    "cid": tid,
                    "name": name,
                    "kind": row_kind,
                    "text": str(m.get("text") or ""),
                    "time": time.strftime("%m-%d %H:%M", time.localtime(ts)),
                    "ts": ts,
                    "status": status,
                    "reply": reply,
                    "reply_ts": reply_ts,
                    "sender": str(m.get("sender") or ""),
                })
        out.sort(key=lambda r: r["ts"], reverse=True)
        try:
            n = int(limit)
        except Exception:
            n = 400
        return out[:max(0, n)] if n else out

    def hide_message(self, thread_id, mid):
        """从界面列表里去掉一条（append 一条 deleted 状态，不真删流水）。"""
        tid = str(thread_id or "").strip()
        mid = str(mid or "").strip()
        if not tid or not mid:
            return False
        rec = {"mid": mid, "ts": _now(), "dir": "out", "sender": "", "text": "",
               "ai": True, "status": "deleted", "status_of": mid}
        try:
            with open(self._msgs_path(tid), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            return False
        return True

    def last_manual_out(self, thread_id):
        """这个会话里**我本人**手动发的最后一条的时间戳；没有就 0.0。"""
        last = 0.0
        for m in self.messages(thread_id, limit=5000):
            if m.get("dir") != "out" or m.get("ai"):
                continue
            try:
                ts = float(m.get("ts") or 0)
            except Exception:
                ts = 0.0
            if ts > last:
                last = ts
        return last

    # ------------------------------------------------------------------
    # 审查视图（用户要"能查看/审查上下文"）
    # ------------------------------------------------------------------
    def review(self, thread_id, limit=40):
        """返回给界面用的上下文快照（纯文本，人眼可读）。"""
        t = self.thread(thread_id) or {}
        lines = [f"# 会话：{t.get('name') or thread_id}",
                 f"id: {thread_id}",
                 f"模式: {t.get('mode') or DEFAULT_MODE}（{MODES.get(t.get('mode'), '')}）",
                 f"上下文条数: {t.get('ctx_limit')}｜带本会话记忆: "
                 f"{'是' if t.get('memory_on', True) else '否'}",
                 f"人设: {t.get('persona') or '（未设置）'}",
                 f"规则: {t.get('rules') or '（未设置）'}",
                 ""]
        shared = self.memory().strip()
        lines.append("## 共享记忆")
        lines.append(shared or "（空）")
        lines.append("")
        tm = self.thread_memory(thread_id).strip()
        lines.append("## 本会话记忆")
        lines.append(tm or "（空）")
        lines.append("")
        lines.append(f"## 最近 {limit} 条消息")
        for m in self.messages(thread_id, limit):
            arrow = "→" if m.get("dir") == "out" else "←"
            who = m.get("sender") or ("AI" if m.get("ai") else "?")
            st = f" [{m.get('status')}]" if m.get("status") else ""
            lines.append(f"{arrow} [{time.strftime('%m-%d %H:%M', time.localtime(m.get('ts') or 0))}]"
                         f" {who}{st}: {m.get('text')}")
        return "\n".join(lines)
