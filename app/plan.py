# -*- coding: utf-8 -*-
"""多步任务计划：AI 用 update_plan 维护一份可勾选的步骤清单，界面实时显示。

计划存在对话对象里（conv["plan"]），随对话一起保存，重新打开还在。
"""
import time

STATUS_MARKS = {"pending": "○", "doing": "◔", "done": "●", "failed": "✗"}
STATUS_NAMES = {"pending": "待办", "doing": "进行中", "done": "已完成", "failed": "失败"}

# 兼容模型可能写的中文/英文状态
_STATUS_ALIAS = {
    "pending": "pending", "todo": "pending", "wait": "pending", "未开始": "pending",
    "待办": "pending", "待处理": "pending", "计划": "pending", "○": "pending",
    "doing": "doing", "in_progress": "doing", "inprogress": "doing",
    "running": "doing", "进行中": "doing", "正在进行": "doing", "◔": "doing",
    "done": "done", "completed": "done", "complete": "done", "finished": "done",
    "ok": "done", "已完成": "done", "完成": "done", "已完成 ": "done", "●": "done",
    "failed": "failed", "error": "failed", "失败": "failed", "✗": "failed",
}


def normalize_status(s):
    return _STATUS_ALIAS.get(str(s or "").strip().lower(),
                             _STATUS_ALIAS.get(str(s or "").strip(), "pending"))


def normalize_steps(raw):
    """把模型给的各种写法统一成 [{'text':..., 'status':...}]。"""
    out = []
    if not raw:
        return out
    if isinstance(raw, str):
        raw = [x for x in (raw or "").split("\n") if x.strip()]
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            status = "pending"
        elif isinstance(item, dict):
            text = (item.get("text") or item.get("step") or item.get("title")
                    or item.get("task") or item.get("内容") or "").strip()
            status = normalize_status(item.get("status") or item.get("state")
                                      or item.get("状态"))
        else:
            continue
        # 形如 "1. [x] 读文件"
        text = _strip_prefix(text)
        if not text:
            continue
        out.append({"text": text[:120], "status": status})
    return out[:12]


def _strip_prefix(text):
    import re
    t = text.strip()
    m = re.match(r"^\[([ xX✓✔✗!\-])\]\s*(.*)$", t)
    if m:
        mark = m.group(1).lower()
        t = m.group(2)
        return t
    t = re.sub(r"^\d+[.)、]\s*", "", t)
    t = re.sub(r"^[-*•]\s*", "", t)
    return t.strip()


def build(steps):
    """生成计划 dict。"""
    norm = normalize_steps(steps)
    return {
        "steps": norm,
        "updated": time.time(),
        "revision": 0,
    }


def apply_update(plan, steps, note=None):
    """根据 AI 传来的新 steps 更新计划。

    支持「只给文字不给状态」的简写：此时自动推断——
    第一条未完成的标为 doing，之前已完成的前缀保持 done。
    """
    norm = normalize_steps(steps)
    if not norm:
        return plan or {"steps": [], "updated": time.time(), "revision": 0}
    old = (plan or {}).get("steps") or []
    old_map = {_key(s["text"]): s["status"] for s in old}
    simple = all(s["status"] == "pending" for s in norm) and _looks_plain(steps)
    if simple:
        # 让模型少写状态也能用：没做过的一律 pending，第一条 pending 变 doing
        first_pending = None
        for s in norm:
            k = _key(s["text"])
            if k in old_map:
                s["status"] = old_map[k]
            if s["status"] == "pending" and first_pending is None:
                first_pending = s
        if first_pending is not None:
            first_pending["status"] = "doing"
    plan = dict(plan or {})
    plan["steps"] = norm
    plan["updated"] = time.time()
    plan["revision"] = int((plan.get("revision") or 0)) + 1
    if note:
        plan["note"] = str(note)[:200]
    return plan


def _looks_plain(raw):
    if isinstance(raw, str):
        return True
    if isinstance(raw, list):
        for it in raw:
            if isinstance(it, dict) and (it.get("status") or it.get("state")
                                         or it.get("状态")):
                return False
        return True
    return False


def _key(text):
    import re
    return re.sub(r"\s+", "", (text or "")).lower()[:60]


def progress(plan):
    steps = (plan or {}).get("steps") or []
    if not steps:
        return 0, 0, 0
    done = sum(1 for s in steps if s["status"] == "done")
    return done, len(steps), (done * 100 // max(len(steps), 1))


def to_markdown(plan):
    """给 AI 看 / 给界面看的一份清单文本。"""
    steps = (plan or {}).get("steps") or []
    if not steps:
        return ""
    lines = []
    for i, s in enumerate(steps, 1):
        lines.append(f"{STATUS_MARKS.get(s['status'], '○')} {i}. {s['text']}"
                     f"（{STATUS_NAMES.get(s['status'], s['status'])}）")
    done, total, pct = progress(plan)
    lines.append(f"进度：{done}/{total}（{pct}%）")
    return "\n".join(lines)


def to_html(plan):
    steps = (plan or {}).get("steps") or []
    if not steps:
        return ""
    colors = {"pending": "#9aa0a6", "doing": "#f0a020", "done": "#2e9e5b",
              "failed": "#d9534f"}
    rows = []
    for i, s in enumerate(steps, 1):
        c = colors.get(s["status"], "#9aa0a6")
        deco = "text-decoration:line-through;opacity:.6;" if s["status"] == "done" else ""
        rows.append(
            f'<div style="margin:2px 0;color:{c};{deco}">'
            f'{STATUS_MARKS.get(s["status"], "○")} {i}. {_esc(s["text"])}</div>')
    return "".join(rows)


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
