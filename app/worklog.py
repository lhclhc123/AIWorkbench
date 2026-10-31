# -*- coding: utf-8 -*-
"""工作总结（Worklog）。

用户要求：「每次干完活要有一份工作总结」。所以每轮对话 / 定时任务跑完，
由程序**自动**写一条结构化总结到

    <工作区>/worklog/YYYY-MM-DD.md

内容包含：时间、来源（对话 / 定时任务 / 子代理）、用户请求、用了哪些工具、
产出了哪些文件（绝对路径 + 是否确认落盘）、结论摘要、成功与否。

不依赖模型「愿意写」——程序在收尾时直接落盘，模型只负责给一句「摘要」。
"""
import datetime
import json
import os
import re

from .workspace import internal_dir

_DIR = "worklog"
_INDEX = "index.json"


def _dir(ws):
    # ★ v9.18.0：工作总结属于机器内部数据，藏进 <工作区>/.awb/worklog/
    #   （老工作区还没迁移时 internal_dir 会自动回退到根目录 worklog/）
    d = internal_dir(ws, _DIR)
    os.makedirs(d, exist_ok=True)
    return d


def day_path(ws, day=None):
    day = day or datetime.date.today().strftime("%Y-%m-%d")
    return os.path.join(_dir(ws), day + ".md")


def _hhmm(ts=None):
    ts = ts or datetime.datetime.now()
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S") \
        if isinstance(ts, (int, float)) else ts.strftime("%H:%M:%S")


def _one_line(s, limit=200):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def append(ws, *, source="对话", request="", tools=None, files=None,
           summary="", ok=True, model="", extra_notes=""):
    """追加一条工作总结，返回写进去的 markdown 片段（也返回记录了没）。"""
    tools = list(tools or [])
    files = list(files or [])
    now = datetime.datetime.now()
    lines = [f"### {now.strftime('%H:%M:%S')} · {source}", ""]
    if request:
        lines.append(f"- **请求**：{_one_line(request, 240)}")
    if model:
        lines.append(f"- **模型**：{model}")
    if tools:
        uniq = list(dict.fromkeys(tools))
        lines.append(f"- **用了 {len(tools)} 次工具**：{'、'.join(uniq)}")
    else:
        lines.append("- **没有调用任何工具**")
    if files:
        lines.append("- **产出文件**：")
        for f in files:
            if isinstance(f, dict):
                p = f.get("path") or ""
                good = f.get("exists")
                mark = "✓" if good else ("✕" if good is False else "•")
                size = f.get("size")
                s = f"（{size} 字节）" if size else ""
                lines.append(f"  - {mark} `{p}`{s}")
            else:
                lines.append(f"  - `{f}`")
    lines.append(f"- **结果**：{'成功' if ok else '未完成'} · {_one_line(summary, 400) or '（无摘要）'}")
    if extra_notes:
        lines.append(f"- **备注**：{_one_line(extra_notes, 300)}")
    lines.append("")
    block = "\n".join(lines)

    p = day_path(ws)
    header = ""
    if not os.path.exists(p):
        header = (f"# 工作总结 · {now.strftime('%Y-%m-%d')}\n\n"
                  f"> 由 AI 工作台自动生成。每完成一件事追加一条。\n\n")
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(header + block + "\n")
    except Exception as e:
        return "", f"[错误] 写工作总结失败：{e}"
    _index_add(ws, _one_line(request, 80), source, ok)
    return block, ""


def _index_add(ws, title, source, ok):
    """维护一个轻量索引，方便界面列表展示（没有也能跑）。

    ★ v9.22.1 修两个真实隐患（v9.22.0 之前的写法只有一个大 try）：
      ① **非原子写**：原来 `open(p,"w")` 直写，写到一半被读（界面刷新 / 另一个
         线程同时 append）会读到半个 JSON；写的时候进程被杀更是把整份索引写坏。
      ② **读失败连累写**：原来 read+append+write 在同一个 try 里，`json.load`
         一旦抛异常，**整条记录都加不进去**，界面就显示「今天 0 件事」，
         而且异常被静默吞掉，用户完全不知道索引已经坏了。
         真实症状：`tests/smoke_gui_v91.py` 偶发「总结提示含统计 → 今天 0 件事」。
    现在的做法：读失败就把坏文件改名留档（.bad）后重建；写一律 tmp + os.replace（原子）。
    """
    p = os.path.join(_dir(ws), _INDEX)
    data = []
    try:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                data = []
    except Exception:
        # 读坏了不能连累写：留档一份，然后从空表重建，至少新记录进得去。
        try:
            os.replace(p, p + ".bad")
        except Exception:
            pass
        data = []
    data.append({
        "t": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "title": title, "source": source, "ok": bool(ok),
    })
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data[-500:], f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)          # 同盘原子替换：读者要么看到旧的、要么看到新的
    except Exception:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass


def list_days(ws):
    """返回 [(日期, 字节数), ...]，新的在前。"""
    d = _dir(ws)
    out = []
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if fn.endswith(".md"):
            p = os.path.join(d, fn)
            try:
                out.append((fn[:-3], os.path.getsize(p)))
            except Exception:
                pass
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def read_day(ws, day=None):
    day = day or datetime.date.today().strftime("%Y-%m-%d")
    p = os.path.join(_dir(ws), day + ".md")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def recent(ws, n=20):
    """返回最近 n 条总结（解析 index.json）。

    ★ v9.22.1：Windows 上 `os.replace` 撞上「另一个句柄正开着同名文件」会
      抛 `PermissionError`（共享删除没开），读侧就会**偶发**拿到空列表 ——
      界面上表现为「今天 0 件事」，而异常又被吞掉，极难查。
      所以这里做**短暂重试**：写是原子的，重试 30ms 后一定能读到完整的旧版或新版。
    """
    import time as _t
    p = os.path.join(_dir(ws), _INDEX)
    last = None
    for attempt in range(4):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return list(reversed(data[-int(n):]))
        except FileNotFoundError:
            return []
        except Exception as e:      # PermissionError / 半个 JSON / 正在替换
            last = e
            if attempt < 3:
                _t.sleep(0.03 * (attempt + 1))
    if last is not None:
        return []
    return []


def today_stats(ws):
    """今天干了多少件事 / 几次成功。"""
    day = datetime.datetime.now().strftime("%Y-%m-%d")
    items = [x for x in recent(ws, 500)
             if str(x.get("t", "")).startswith(day)]
    ok = sum(1 for x in items if x.get("ok"))
    return {"total": len(items), "ok": ok, "day": day}
