# -*- coding: utf-8 -*-
"""代发总闸（v9.22.7，2026-11-14 事故后加的硬闸）。

**事故复盘**
v9.22.6 的「自动代回」是 fail-open 的：
  · `scope_group` / `scope_p2p` 默认都是 `True`；
  · `whitelist` 为空 = 不设限制（等于"全部会话"）；
  · `_im_tick` 每 10 秒跑一次 `_im_pipeline_due/pump`；
  · `IMHub.handle()` 调 `send_fn` 直接发，中间没有任何确认。
于是只要程序开着、钉钉登录着，它就会**拿用户真实账号**往真实群回话。
2026-11-14 一次前端实测点了「全部处理」，AI 编的内容被直接发进了班级群。

**本模块的原则：fail-closed。**
任何一条不满足就不发；每条拒绝都要给出中文原因，前端要原样显示给人看。

三道硬闸（缺一不可）：
1. 演练闸 —— `AWB_IM_DRYRUN=1` / `--selftest` / 安全模式，一律只演练不真发
   （**这是防"我自己测试又发出去"的那道闸**，任何自动化测试都必须带着它跑）；
2. 总开关 —— `auto_send` 默认 `False`，用户显式打开才可能发；
3. 白名单 —— **可选**：填了就只回名单里的会话，不填代表不限（用户 2026-11-14 要求）。

不再做单日条数熔断与同会话冷却（用户明确说不需要）。

所有真发都写进 `sends.jsonl`，前端「发送记录」可查、可一键撤回。
"""
import json
import os
import sys
import threading
import time

SEND_LOG = "sends.jsonl"

# 同会话冷却（秒）：防"AI 连着回三条"
COOLDOWN_S = 60.0
# 单日自动发送上限（用户可在设置里调 auto_send_max）
MAX_AUTO_PER_DAY = 20

_LOCK = threading.RLock()

# 当前进程被闸门拦下的最后一条原因（给 UI 显示）
_last_block = {"reason": "", "ts": 0.0}


def dry_run_active():
    """是不是"只演练、绝不真发"的模式。

    任何一个成立即返回 True —— 宁可多发不出去，也不能误发。
    """
    try:
        for k in ("AWB_IM_DRYRUN", "AWB_DRY_RUN", "AWB_SAFE"):
            if str(os.environ.get(k, "")).strip() == "1":
                return True
        argv = [str(a).lower() for a in (sys.argv or [])]
        for flag in ("--selftest", "--self-test", "--dry-run", "--dryrun", "--safe"):
            if flag in argv:
                return True
    except Exception:
        pass
    return False


def last_block():
    return dict(_last_block)


def _set_block(reason):
    try:
        _last_block["reason"] = str(reason or "")
        _last_block["ts"] = time.time()
    except Exception:
        pass


class SendGuard:
    """一次会话生命周期内唯一的发送裁决者。

    `root` 是 im 数据目录（`<工作区>/.awb/im`），发送流水写在里面的 `sends.jsonl`。
    """

    def __init__(self, root, log=None):
        self.root = str(root or "")
        self._log = log or (lambda *a, **k: None)

    # ------------------------------------------------------------------
    @property
    def log_path(self):
        return os.path.join(self.root, SEND_LOG) if self.root else ""

    def _rows(self, limit=400):
        """读最近 N 条发送流水（文件不存在就当空）。"""
        p = self.log_path
        if not p or not os.path.exists(p):
            return []
        out = []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-limit:]
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            return []
        return out

    def _day_start(self):
        return time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))

    def count_today(self, source="auto"):
        d0 = self._day_start()
        n = 0
        for r in self._rows(800):
            try:
                if float(r.get("ts") or 0) < d0:
                    continue
                if str(r.get("source") or "") != str(source):
                    continue
                if r.get("ok") is False:
                    continue
                n += 1
            except Exception:
                continue
        return n

    def last_send_ts(self, cid, source="auto"):
        for r in reversed(self._rows(200)):
            if str(r.get("cid") or "") != str(cid or ""):
                continue
            if str(r.get("source") or "") != str(source):
                continue
            if r.get("ok") is False:
                continue
            try:
                return float(r.get("ts") or 0)
            except Exception:
                return 0.0
        return 0.0

    # ------------------------------------------------------------------
    def allow(self, cid, text, settings=None, source="auto"):
        """裁决：返回 `(是否允许, 中文原因)`。

        `source`：
          - `auto`      AI 自动代回（最严，四道闸全过）
          - `manual`    用户在界面上手点发送
          - `scheduled` 用户事先配好的定时消息
          - `test`      设置页「发条测试消息」
        """
        cid = str(cid or "").strip()
        text = str(text or "").strip()

        # ① 基本合法性
        if not cid:
            return self._no("没有会话 id，不发")
        if not text:
            return self._no("内容为空，不发")

        # ② 演练闸
        if dry_run_active():
            return self._no("当前是演练模式（AWB_IM_DRYRUN / --selftest / 安全模式），"
                            "只演练不真发")

        # ③ 测试消息永不真发
        if source == "test":
            return self._no("「发条测试消息」已改为只演练，不会真的发到钉钉")

        # ④ 用户手动 / 定时消息：用户意图明确，放行（流水照记）
        if source in ("manual", "scheduled"):
            return True, "ok"

        # ⑤ 自动代回：只看总开关（用户 2026-11-14 明确要求：
        #    白名单不强制、不要熔断，"总开关默认关着"就够）。
        st = dict(settings or {})
        if not st.get("auto_send"):
            return self._no("「自动代回」总开关是关闭的（默认关闭），这条只生成草稿")

        # 白名单是**可选的收窄**：填了就只回名单里的；没填就代表不限会话。
        wl = [str(x.get("id") or "") for x in (st.get("whitelist") or [])
              if isinstance(x, dict)]
        wl = [x for x in wl if x]
        if wl and cid not in wl:
            return self._no("这个会话不在「允许代回」名单里")

        return True, "ok"

    def _no(self, reason):
        _set_block(reason)
        try:
            # 单色符号（icons 表）而不是彩色 emoji —— 后者在日志里是个大色块
            from app.icons import ic
            self._log(ic("blocked") + " 拦住一条发送：%s" % reason)
        except Exception:
            pass
        return False, reason

    # ------------------------------------------------------------------
    def record(self, cid, text, ok, source="auto", reason="", mid=""):
        """记一条发送流水。**失败也要记**，否则用户看不出被拦了。"""
        if not self.root:
            return
        row = {
            "ts": time.time(),
            "cid": str(cid or ""),
            "text": str(text or "")[:2000],
            "ok": bool(ok),
            "source": str(source or "auto"),
            "reason": str(reason or ""),
            "mid": str(mid or ""),
        }
        p = self.log_path
        with _LOCK:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception:
                pass
        return row

    def recent(self, limit=50):
        """最近的发送流水（新的在前），给前端「发送记录」用。"""
        rows = list(reversed(self._rows(max(50, int(limit) * 3))))
        return rows[:max(1, int(limit))]


# ---------------------------------------------------------------------------
# 待发草稿箱（v9.22.7）
# ---------------------------------------------------------------------------
DRAFT_LOG = "drafts.jsonl"


class DraftBox:
    """「AI 想回、但没被授权真发」的内容放这里，等人点头。

    事故的根子就是"想回就直接发"。现在改成：**默认只生成草稿**，
    界面上逐条「发送 / 丢弃」，再也不会出现静默群发。
    """

    def __init__(self, root):
        self.root = str(root or "")

    @property
    def path(self):
        return os.path.join(self.root, DRAFT_LOG) if self.root else ""

    def _load(self):
        p = self.path
        if not p or not os.path.exists(p):
            return []
        out = []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return out

    def _save(self, rows):
        p = self.path
        with _LOCK:
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.replace(tmp, p)
            except Exception:
                pass

    def add(self, cid, name, text, reason=""):
        """存一条草稿；同一会话只保留最新一条，避免刷屏。"""
        rows = [r for r in self._load() if str(r.get("cid") or "") != str(cid)]
        row = {
            "id": "%d" % int(time.time() * 1000),
            "ts": time.time(),
            "cid": str(cid or ""),
            "name": str(name or ""),
            "text": str(text or ""),
            "reason": str(reason or ""),
        }
        rows.append(row)
        rows = rows[-200:]
        self._save(rows)
        return row

    def list(self, limit=50):
        rows = sorted(self._load(), key=lambda r: float(r.get("ts") or 0),
                      reverse=True)
        return rows[:max(1, int(limit))]

    def get(self, did):
        for r in self._load():
            if str(r.get("id")) == str(did):
                return r
        return None

    def drop(self, did):
        rows = self._load()
        keep = [r for r in rows if str(r.get("id")) != str(did)]
        if len(keep) == len(rows):
            return False
        self._save(keep)
        return True

    def clear(self):
        self._save([])
