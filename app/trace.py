# -*- coding: utf-8 -*-
"""工具调用追踪（可观测性）：把每次工具调用的名称、参数、耗时、结果摘要落盘。

出问题时能一眼看出「AI 在哪一步、用什么参数、拿到了什么」，而不是靠猜。
"""
import os
import json
import time

MAX_KEEP = 2000


class TraceLog:
    def __init__(self, workspace_path: str):
        self.dir = os.path.join(workspace_path, "traces")
        self.path = os.path.join(self.dir, "tool_calls.jsonl")
        try:
            os.makedirs(self.dir, exist_ok=True)
        except Exception:
            pass
        self.session_id = time.strftime("%Y%m%d_%H%M%S")

    def add(self, name, arguments=None, result="", ok=True, ms=0, extra=None):
        rec = {
            "t": time.time(),
            "session": self.session_id,
            "name": name,
            "args": _short_args(arguments),
            "ms": int(ms),
            "ok": bool(ok),
            "result": (result or "")[:400],
            "extra": extra or {},
        }
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        self._trim()
        return rec

    def _trim(self):
        try:
            if not os.path.isfile(self.path):
                return
            if os.path.getsize(self.path) < 4 * 1024 * 1024:
                return
            with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_KEEP:])
        except Exception:
            pass

    def tail(self, n=120):
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            return []
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def stats(self):
        recs = self.tail(MAX_KEEP)
        by = {}
        for r in recs:
            d = by.setdefault(r.get("name", "?"), {"n": 0, "ok": 0, "ms": 0})
            d["n"] += 1
            d["ok"] += 1 if r.get("ok") else 0
            d["ms"] += int(r.get("ms") or 0)
        return by

    def clear(self):
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception:
            pass


def _short_args(args):
    """参数太长（例如 write_file 的整篇 content）只留摘要，避免日志爆炸。"""
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 160:
            out[k] = v[:160] + f"…（共 {len(v)} 字）"
        elif isinstance(v, (list, dict)):
            s = json.dumps(v, ensure_ascii=False)
            out[k] = (s[:160] + "…") if len(s) > 160 else v
        else:
            out[k] = v
    return out
