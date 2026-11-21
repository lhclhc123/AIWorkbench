# -*- coding: utf-8 -*-
"""「助理」服务：轮询钉钉消息 → 交给 AI 处理 → 把结果（含产出文件）发回钉钉。

走 dws（个人 OAuth 授权，不需要企业应用），和 WorkBuddy 的钉钉连接器同一条路。
只负责「收」：轮询、去重、把新消息抛给界面；「处理」和「回」由主窗口编排
（主窗口有 Agent 和发文件能力）。这样职责清晰，也方便手动/自动两种模式。

★ v9.22.12 修用户 2026-11-16 反馈的一批体验问题：

  ① **停止要真的能停**：所有耗时循环（遍历会话、逐个会话拉消息、逐条消息解析、
     批量打标签）内层都检查 ``_stop``；长等待一律用 ``Event.wait()`` 而不是
     ``time.sleep()``，保证点「停止助理」后线程在几毫秒内就能看到停止信号。
     界面侧不等待线程收尾 —— 点下去立刻回到空闲态（见 main_window）。
  ② **边扫边显示**：``TagScanService.partial`` 每扫完一个会话就推一条，
     界面增量 append，不整表重建。
  ③ **跳过官方号**：``is_official_account()`` + ``OFFICIAL_ACCOUNT_NAMES``，
     钉钉助手 / 工作通知 / 审批 这类系统账号不参与打标签，日志里明确记一行。
  ④ **进度胶囊**（UI 在 main_window）：这里只提供带速率和进度的信号。
  ⑤ **网络自适应**：``probe_bandwidth()`` 用 100KB / 500KB / 1MB 三档实测，
     按有效速率决定并发度与单次超时；失败指数退避重试；结果缓存 30 分钟。
  ⑥/⑦ 属于界面（main_window）。

为什么扫描编排不直接复用 ``app/im/tags.py: scan_and_tag()``：
那个函数没有停止钩子（``collect()`` 内部 4 路并发 + 子进程调用，外层拿不到
中断点），而本次验收明确要求「扫描过程中点停止，1.5 秒内回到空闲态」。
``app/im/tags.py`` 不在本次可改动的文件范围内，所以这里做了一层**可停止的编排**，
纯逻辑（提示词 / 标签守卫 / 类型判断 / 排序）仍然复用 ``app.im.tags``，不重复实现。
"""
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait

from PyQt6.QtCore import QObject, pyqtSignal

DEFAULT_CFG = {
    "enabled": False,      # 是否自动轮询
    "interval": 25,        # 轮询间隔（秒）
    "scope": "self",       # self=只听自己发给自己(自聊)；all=所有会话；at_me=群里@我
    "auto_reply": True,    # 处理完自动把结果发回钉钉
    # ★ v9.20.0：scope=all 时最多轮询多少个会话（多了会一轮几十次 dws 调用，很慢）
    "max_convs": 15,
    "self_cid": "",        # 「自己给自己」会话的 openConversationId（自动探测后缓存）
    "processed": [],       # 已处理过的 messageId（去重，最多留 800 条）
    "primed": False,       # 是否已「跳过历史消息」（首次启动只处理新消息）
}
MAX_PROCESSED = 800

# 停止时界面最多等这么久就认为「已空闲」（秒）。线程本身可能还堵在一次
# dws 子进程里收尾，但界面绝不能等它 —— 见 main_window._im_scan_stop()。
STOP_UI_DEADLINE = 1.5


# ======================================================================
# ③ 官方号 / 系统账号过滤
# ======================================================================
# 归一化后**完全相等**才跳过（不做子串匹配 —— 「审批流程讨论群」是真人建的群，
# 不能被当成「审批」官号误杀）。
OFFICIAL_ACCOUNT_NAMES = frozenset({
    "钉钉助手", "工作通知", "钉钉团队", "智能人事", "审批", "日程助手",
    "钉钉会议", "考勤打卡", "钉钉小秘书", "钉钉智能助手", "智能工作助理",
    "钉钉办公", "钉钉服务中心", "服务通知", "消息通知", "系统通知",
    "打卡", "日报", "日志", "公告", "钉钉打卡", "考勤", "审批中心",
    "智能审批", "钉钉公告", "团队通知", "组织架构", "通讯录",
    "钉钉审批", "智能考勤", "工作台", "应用通知", "钉钉安全中心",
})

# 这些开头的名字几乎一定是官方应用（钉钉助手、钉钉团队、钉钉会议…）。
# 刻意**不含**「钉钉」两个字本身 —— 用户可能真的建了「钉钉用户交流群」。
OFFICIAL_ACCOUNT_PREFIXES = (
    "钉钉助手", "钉钉团队", "钉钉会议", "钉钉小秘书", "钉钉智能",
    "钉钉办公", "钉钉服务", "钉钉安全", "钉钉公告", "钉钉审批",
    "工作通知", "智能人事", "日程助手", "考勤打卡", "服务通知",
    "系统通知", "消息通知", "应用通知", "团队通知",
)

# 会话对象里表示「机器人 / 官方 / 系统」的布尔字段
_OFFICIAL_BOOL_KEYS = ("isRobot", "is_robot", "robot", "isBot", "is_bot",
                       "isOfficial", "is_official", "official", "isSystem",
                       "is_system", "isApp", "is_app")

# 会话对象里表示「会话类型」的字符串字段
_OFFICIAL_TYPE_KEYS = ("conversationType", "chatMode", "type", "category",
                       "conversationTag", "tag", "channelType")
_OFFICIAL_TYPE_VALUES = frozenset({
    "robot", "bot", "official", "official_account", "system", "sys",
    "service", "notice", "notification", "app", "application", "oa",
})

# 名字里带这些「括号后缀」时先剥掉再比对（「工作通知(应用)」→「工作通知」）
_BRACKET_RE = re.compile(r"[（(\[【<《][^）)\]】>》]*[）)\]】>》]")


def normalize_conv_name(name):
    """会话名归一化：去空白、去括号注释、转小写。

    只用于「和官方号名单比对」，不用于显示 —— 显示仍然用原名字。
    """
    s = str(name or "")
    # 全角空格 / 不换行空格先统一成半角，再整体去空白
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = _BRACKET_RE.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s.strip(" .·-—_、,，。:").lower()


def _truthy(value):
    """把 dws / SDK 里各种「真」写法统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value or "").strip().lower()
    return s in ("1", "true", "yes", "y", "on", "robot", "bot", "official")


def is_official_account(name, conv=None):
    """判断一个会话是不是「钉钉助手 / 工作通知 / 审批」这类官方号。

    两道判据（任一命中就跳过）：
      1. 名字归一化后等于官方号名单，或以官方号前缀开头；
      2. 会话对象自带的机器人 / 官方 / 系统类型字段（``isRobot``、
         ``conversationType=robot`` 之类）—— 名字被用户改过时靠它兜底。

    Args:
        name: 会话显示名（``conversationName`` / ``name``）。
        conv: 原始会话 dict（可选；给了就顺便看类型字段）。

    Returns:
        bool: True = 官方号，应当跳过。
    """
    norm = normalize_conv_name(name)
    if norm:
        if norm in OFFICIAL_ACCOUNT_NAMES:
            return True
        for pre in OFFICIAL_ACCOUNT_PREFIXES:
            if norm.startswith(pre):
                return True
    if isinstance(conv, dict):
        for k in _OFFICIAL_BOOL_KEYS:
            if k in conv and _truthy(conv.get(k)):
                return True
        for k in _OFFICIAL_TYPE_KEYS:
            v = conv.get(k)
            if isinstance(v, str) and v.strip().lower() in _OFFICIAL_TYPE_VALUES:
                return True
    return False


# ======================================================================
# ⑤ 带宽探测（100KB / 500KB / 1MB 三档实测 + 结果缓存）
# ======================================================================
PROBE_SIZES = (100 * 1024, 500 * 1024, 1024 * 1024)
BANDWIDTH_CACHE_TTL = 30 * 60          # 30 分钟，别每次扫描都重测
# 默认探测地址：Cloudflare 的测速端点，支持按字节下载、无鉴权、有 CORS。
# 可用环境变量覆盖（公司网络/离线环境可以指向内网文件）。
PROBE_URL = os.environ.get(
    "AIWORKBENCH_SPEED_PROBE_URL",
    "https://speed.cloudflare.com/__down?bytes=%d")

_BW_CACHE = {"ts": 0.0, "result": None, "key": ""}
_BW_LOCK = threading.Lock()


def human_speed(bps):
    """把 B/s 变成人看的一行：``1.2 MB/s`` / ``350 KB/s`` / ``—``。"""
    try:
        v = float(bps or 0.0)
    except Exception:
        v = 0.0
    if v <= 0:
        return "—"
    if v >= 1024 * 1024:
        return "%.1f MB/s" % (v / (1024.0 * 1024.0))
    return "%d KB/s" % max(1, int(round(v / 1024.0)))


def _http_probe(size, timeout):
    """默认探测实现：HTTP GET 指定字节数，返回 ``(已读字节, 耗时秒)``。

    单次读取上限就是 ``size`` —— 不会为了测 100KB 把 1MB 全下下来。
    任何网络错误直接抛出，由 ``probe_bandwidth()`` 记成该档失败。
    """
    import urllib.request

    url = PROBE_URL % int(size)
    req = urllib.request.Request(
        url, headers={"User-Agent": "AIWorkbench/speed-probe",
                      "Cache-Control": "no-cache"})
    started = time.time()
    got = 0
    with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
        while got < size:
            if time.time() - started > float(timeout):
                break
            chunk = resp.read(min(16384, size - got))
            if not chunk:
                break
            got += len(chunk)
    elapsed = max(0.001, time.time() - started)
    return got, elapsed


def _normalize_probe_result(result, size):
    """把 ``probe_fn`` 的返回值统一成 ``(bytes, seconds)``。

    允许注入的探测函数返回：
      * ``(bytes, seconds)``；
      * 纯 ``bytes``（耗时由调用方计时，兼容简单假实现）；
      * ``{"bytes": n, "seconds": t}``。
    """
    if isinstance(result, dict):
        got = float(result.get("bytes") or result.get("size") or 0)
        sec = float(result.get("seconds") or result.get("elapsed") or 0)
        return max(0.0, got), max(0.001, sec)
    if isinstance(result, (tuple, list)) and len(result) >= 2:
        return max(0.0, float(result[0])), max(0.001, float(result[1]))
    return max(0.0, float(result or size)), 0.001


def _probe_timeout(size):
    """单档探测超时：小档短、大档长，最慢也只要 5 秒。"""
    return max(2.0, min(5.0, 1.0 + float(size) / (400.0 * 1024.0)))


def probe_bandwidth(stop_event=None, probe_fn=None, sizes=PROBE_SIZES,
                    timeout=None, deadline=8.0):
    """实测当前可用带宽（100KB / 500KB / 1MB 各一次）。

    Args:
        stop_event: ``threading.Event``；置位后不再发起新的探测档，
            并且返回结果里 ``stopped=True``（已测到的档仍然保留）。
        probe_fn: 可注入的探测函数 ``fn(size, timeout) -> (bytes, seconds)``；
            默认走 ``_http_probe``（离线环境/单测可以注入假实现）。
        sizes: 探测尺寸列表，默认 ``PROBE_SIZES``。
        timeout: 固定超时（秒）；None = 按尺寸自适应（2~5 秒）。
        deadline: **整个探测的总时限**（秒）。弱网/断网时不能为了测速让用户
            干等半分钟 —— 超时就按已测到的档位收尾，没测到的档记为
            ``{"skipped": "deadline"}``。

    Returns:
        dict: ``{"ok", "speed_bps", "samples", "tier", "workers",
        "timeout", "stopped"}``。全部失败时 ``ok=False, speed_bps=0``，
        调用方按「慢网」处理（并发 2 / 超时 20 秒），绝不整轮崩掉。
    """
    fn = probe_fn or _http_probe
    samples = []
    stopped = False
    good = []
    began = time.time()
    for size in sizes:
        if stop_event is not None and stop_event.is_set():
            stopped = True
            break
        remaining = float(deadline) - (time.time() - began)
        if remaining <= 0.4:
            samples.append({"size": int(size), "bytes": 0, "seconds": 0.0,
                            "speed": 0.0, "skipped": "deadline"})
            break
        tmo = float(timeout) if timeout else _probe_timeout(size)
        tmo = max(0.4, min(tmo, remaining))
        started = time.time()
        try:
            raw = fn(int(size), tmo)
            got, sec = _normalize_probe_result(raw, int(size))
            # 注入的假实现只回 bytes 时，用真实墙钟计时
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                sec = max(0.001, time.time() - started)
            speed = got / sec if sec > 0 else 0.0
            samples.append({"size": int(size), "bytes": int(got),
                            "seconds": round(sec, 3),
                            "speed": round(speed, 1)})
            if speed > 0:
                good.append(speed)
        except Exception as exc:
            samples.append({"size": int(size), "bytes": 0, "seconds": 0.0,
                            "speed": 0.0,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            # ★ 一档都还没成功就失败 → 基本可以断定是断网 / DNS 挂了，
            #   没必要再等后面两档各自超时（用户对"卡住不动"极度敏感）。
            if not good:
                self_skip = [s for s in sizes if s not in [x["size"] for x in samples]]
                for rest in self_skip:
                    samples.append({"size": int(rest), "bytes": 0,
                                    "seconds": 0.0, "speed": 0.0,
                                    "skipped": "no-network"})
                break
    # 用户原话是「多线程选择最快的」：取三档里最快的有效速率作为有效带宽。
    speed = max(good) if good else 0.0
    workers, tmo = recommend_workers(speed)
    return {
        "ok": bool(good),
        "speed_bps": float(speed),
        "samples": samples,
        "tier": bandwidth_tier(speed),
        "workers": workers,
        "timeout": tmo,
        "stopped": stopped,
    }


def bandwidth_tier(speed_bps):
    """把有效速率分成三档（用于日志和并发决策）。"""
    try:
        v = float(speed_bps or 0.0)
    except Exception:
        v = 0.0
    if v <= 0:
        return "unknown"          # 探测失败 → 按慢网处理
    if v < 200 * 1024:
        return "slow"
    if v < 1024 * 1024:
        return "normal"
    return "fast"


def recommend_workers(speed_bps):
    """按带宽档位给出 ``(并发数, 单次超时秒)``。

    <200KB/s → 1~2 路 + 20 秒超时（弱网硬并发只会互相拖死）；
    200KB~1MB/s → 3~4 路；>1MB/s → 6~8 路。
    """
    tier = bandwidth_tier(speed_bps)
    if tier == "fast":
        return 8, 8.0
    if tier == "normal":
        return 4, 10.0
    # slow / unknown
    return 2, 20.0


def probe_bandwidth_cached(force=False, stop_event=None, probe_fn=None,
                           ttl=BANDWIDTH_CACHE_TTL, now=None):
    """带 30 分钟缓存的带宽探测（同一进程内共享，别每次扫描都重测）。"""
    now = time.time() if now is None else float(now)
    key = "inject" if probe_fn is not None else str(PROBE_URL)
    with _BW_LOCK:
        cached = _BW_CACHE.get("result")
        age = now - float(_BW_CACHE.get("ts") or 0.0)
        if (not force and cached and _BW_CACHE.get("key") == key
                and 0 <= age < float(ttl)):
            out = dict(cached)
            out["cached"] = True
            return out
    result = probe_bandwidth(stop_event=stop_event, probe_fn=probe_fn)
    result["cached"] = False
    if result.get("ok"):
        with _BW_LOCK:
            _BW_CACHE["ts"] = now
            _BW_CACHE["result"] = dict(result)
            _BW_CACHE["key"] = key
    return result


def reset_bandwidth_cache():
    """清空探测缓存（设置页「重新测速」/ 单测用）。"""
    with _BW_LOCK:
        _BW_CACHE["ts"] = 0.0
        _BW_CACHE["result"] = None
        _BW_CACHE["key"] = ""


# ======================================================================
# ⑤ 可停止的「按原顺序回填」并发执行器
# ======================================================================
def _interruptible_sleep(seconds, stop_event=None):
    """可被 stop 立刻打断的 sleep（返回 True 表示被打断）。"""
    if stop_event is not None:
        return bool(stop_event.wait(max(0.0, float(seconds))))
    time.sleep(max(0.0, float(seconds)))
    return False


def ordered_concurrent_map(fn, items, workers=4, stop_event=None,
                           retries=0, backoff=(0.5, 1.0, 2.0),
                           poll=0.2, on_item=None):
    """并发执行 ``fn(item)``，结果**严格按输入顺序**回填。

    为什么不用 ``executor.map``：它在慢任务上会让「后面的快任务」也跟着等，
    而且没法在中途检查停止信号。这里用 ``wait(timeout=poll)`` 轮询，
    既能每 200ms 看一次 stop，也能把已完成的结果先收进 ``results[idx]``，
    再**从前往后**按连续前缀回调 ``on_item(idx, value)`` —— 顺序永远不打乱。

    Args:
        fn: ``fn(item) -> value``（可以在里面抛异常，会被记成 ``None`` + 错误）。
        workers: 并发度。
        stop_event: 置位后不再等待，直接返回已完成部分（按原下标）。
        retries: 失败重试次数（指数退避 ``backoff``）。
        on_item: ``on_item(idx, value)``，只在**连续前缀**完成时按顺序回调。
        poll: 检查 stop 的间隔（秒）。

    Returns:
        ``(results, errors, stopped)``：``results`` 与 ``items`` 等长，
        未完成的槽位是 ``None``；``errors`` 是 ``[(idx, "类型: 消息"), ...]``。
    """
    items = list(items or [])
    n = len(items)
    results = [None] * n
    errors = []
    if n == 0:
        return results, errors, False
    stopped = False
    next_emit = [0]

    def _emit_prefix():
        while next_emit[0] < n and results[next_emit[0]] is not None:
            if on_item is not None:
                try:
                    on_item(next_emit[0], results[next_emit[0]])
                except Exception:
                    pass
            next_emit[0] += 1

    def _one(idx, item):
        last = None
        for attempt in range(max(0, int(retries)) + 1):
            if stop_event is not None and stop_event.is_set():
                return None
            try:
                return fn(item)
            except Exception as exc:          # noqa: BLE001（要兜住任意网络异常）
                last = "%s: %s" % (type(exc).__name__, exc)
                if attempt >= int(retries):
                    break
                wait_s = backoff[min(attempt, len(backoff) - 1)]
                if _interruptible_sleep(wait_s, stop_event):
                    return None
        raise RuntimeError(last or "未知错误")

    pool = ThreadPoolExecutor(max_workers=max(1, int(workers or 1)))
    futs = {}
    try:
        for idx, item in enumerate(items):
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            futs[pool.submit(_one, idx, item)] = idx
        pending = set(futs.keys())
        while pending:
            if stop_event is not None and stop_event.is_set():
                stopped = True
                break
            finished, pending = _futures_wait(pending, timeout=poll)
            for fu in finished:
                idx = futs[fu]
                try:
                    results[idx] = fu.result()
                except Exception as exc:      # noqa: BLE001
                    results[idx] = None
                    errors.append((idx, "%s: %s" % (type(exc).__name__, exc)))
                    # 用哨兵值占位，避免这个下标永远卡住前缀回调
                    results[idx] = {"__error__": errors[-1][1]}
            _emit_prefix()
        # ★ 停止信号可能刚好在**最后一轮 wait() 里**被置位：那时排队中的任务会
        #   立刻全部返回（_one 见到 stop 直接 return None），pending 一下清空，
        #   循环根本来不及在顶部看到信号。所以循环结束后再补一次判定。
        if stop_event is not None and stop_event.is_set():
            stopped = True
    finally:
        # 不等正在跑的任务：它们可能是 dws 子进程（几十秒），界面早已回到空闲。
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:                     # Python < 3.9
            pool.shutdown(wait=False)
    _emit_prefix()
    return results, errors, stopped


# ======================================================================
# 轮询服务（原 AssistantService，保留全部对外接口）
# ======================================================================
class AssistantService(QObject):
    """钉钉消息轮询服务。"""

    incoming = pyqtSignal(dict)   # 新消息 {msgid, cid, conv, sender, text, time}
    # ★ v9.20.0：我自己本人在钉钉里发的消息（不进处理队列，只用来补全上下文）
    outgoing = pyqtSignal(dict)
    log = pyqtSignal(str)
    state = pyqtSignal(bool)      # 开/停

    def __init__(self, ding, state_path, parent=None):
        super().__init__(parent)
        self.ding = ding
        self.state_path = state_path
        self.cfg = dict(DEFAULT_CFG)
        try:
            with open(state_path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.cfg.update(d)
        except Exception:
            pass
        self._running = False
        self._stop = threading.Event()
        self._thread = None
        self._last_scan = 0.0
        # ★ v9.22.12：标签扫描与轮询共用同一个「停止」语义 —— 点一次
        #   「停止助理」两者都停，不会出现「轮询停了、扫描还在后台跑」。
        self.scanner = TagScanService(self)

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
        old_scope = self.cfg.get("scope")
        for k in ("interval", "scope", "auto_reply", "max_convs"):
            if k in cfg:
                self.cfg[k] = cfg[k]
        # ★ v9.20.0：监听范围变了就得重新「跳过历史」。
        #   否则从「只听自聊」切到「所有会话」的那一瞬间，程序会把过去几个月的
        #   聊天记录全当成新消息，一条条排队回过去 —— 这是灾难。
        new_scope = cfg.get("scope")
        if new_scope and new_scope != old_scope:
            self.cfg["primed"] = False
            self.cfg["processed"] = []
            self.log.emit("监听范围已改为「%s」，现有历史消息会跳过，只处理之后的新消息"
                          % self._scope_name())
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
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="awb-assistant-poll")
        self._thread.start()
        self.state.emit(True)
        self.log.emit("助理已启动：每 %d 秒检查一次钉钉消息（范围：%s）"
                      % (int(self.cfg.get("interval") or 25), self._scope_name()))

    def stop(self):
        """停止轮询 + 停止正在跑的标签扫描。**幂等**：重复点不会卡在「停止中」。"""
        was_active = bool(self._running or not self._stop.is_set()
                          or self.scanner.running())
        self._running = False
        self._stop.set()
        self.cfg["enabled"] = False
        self.save()
        try:
            self.scanner.stop("用户停止")
        except Exception:
            pass
        # state 每次都要发：界面可能在别处漏接了，重发是幂等的
        self.state.emit(False)
        if was_active:
            self.log.emit("助理已停止")

    def _scope_name(self):
        return {"self": "自己发给自己（自聊）", "all": "所有会话",
                "at_me": "群里 @我 的消息"}.get(self.cfg.get("scope"), "自聊")

    def _loop(self):
        while self._running:
            if self._stop.is_set():
                return
            try:
                self.scan_once()
            except Exception as e:
                self.log.emit("[错误] 轮询失败：%s: %s" % (type(e).__name__, e))
            iv = max(10, int(self.cfg.get("interval") or 25))
            # ★ v9.22.12：用 Event.wait 分段等，点停止立刻退出（原来是 sleep(0.5) 循环）
            for _ in range(iv * 2):
                if self._stop.wait(0.5):
                    return

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
        if self._stop.is_set():
            return ""
        try:
            items = (self.ding.dws_conversation_list_full(30) or {}).get("conversations") or []
        except Exception:
            return ""
        if self._stop.is_set():
            return ""
        for c in items:
            if self._stop.is_set():
                return ""
            nm = (c.get("conversationName") or "")
            if nm.lower().endswith("self") or (me and nm == me):
                self.cfg["self_cid"] = c.get("openConversationId") or ""
                self.save()
                return self.cfg["self_cid"]
        return ""

    def scan_once(self):
        """扫一轮，把新消息 emit 出去。返回新消息条数。

        ★ v9.22.12：每个内层循环都检查 ``_stop``；被打断时**保留**已经采集到的
        消息（照常去重、emit），只是不再继续扫后面的会话。
        """
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
        stopped = self._stop.is_set()

        if scope == "at_me":
            try:
                rows = self.ding.dws_at_me(10) or []
            except Exception as e:
                self.log.emit("读 @我 消息失败：%s" % e)
                rows = []
            for m in rows:
                if self._stop.is_set():
                    stopped = True
                    break
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
                if self._stop.is_set():
                    return 0
                # ★ v9.20.0：一次别读太多会话（每个会话一次 dws 调用，很慢）
                try:
                    cap = max(1, int(self.cfg.get("max_convs") or 15))
                except Exception:
                    cap = 15
                if len(items) > cap:
                    items = items[:cap]
                targets = [(c.get("openConversationId"), c.get("conversationName") or "")
                           for c in items]
            for cid, name in targets:
                if self._stop.is_set():
                    stopped = True
                    break
                if not cid:
                    continue
                try:
                    msgs = self.ding.dws_read_messages_raw(cid, 12) or []
                except Exception as e:
                    self.log.emit("读「%s」失败：%s" % (name, e))
                    continue
                # ★ v9.22.12：dws 子进程调用没法中途打断，但拿到结果后立刻判断 ——
                #   已停止就丢弃这一批，绝不把「停止后的新消息」再塞进处理队列。
                if self._stop.is_set():
                    stopped = True
                    break
                for m in msgs:
                    if self._stop.is_set():
                        stopped = True
                        break
                    found.append(self._wrap(m, cid, name))

        new, mine_out, seen = [], [], set()
        for it in found:
            # ★ v9.22.12：逐条解析的内层检查点（消息多时这里是最大头）
            if self._stop.is_set() and not stopped:
                stopped = True
            if not it or not it.get("msgid") or it.get("ai_sent"):
                continue
            if it["msgid"] in processed or it["msgid"] in seen:
                continue
            if not (it.get("text") or "").strip():
                continue
            if scope != "self" and mine and it.get("sender") == mine:
                # ★ v9.20.0：我自己在钉钉里发的，不算给助理的任务，
                #   但要单独抛出去 —— AI 得知道"我本人说过什么"，
                #   延时回复也要靠它判断"你是不是已经自己回过了"。
                seen.add(it["msgid"])
                mine_out.append(it)
                continue
            seen.add(it["msgid"])
            new.append(it)
        new.sort(key=lambda x: x.get("time") or "")

        # 首次运行：把现有历史消息全部记为「已处理」，只从新消息开始干活，
        # 否则一启动就会把过去几个月的聊天记录全部当成任务处理。
        if not self.cfg.get("primed"):
            self.cfg["primed"] = True
            p = list(self.cfg.get("processed") or [])
            p.extend([it["msgid"] for it in new])
            p.extend([it["msgid"] for it in mine_out])
            self.cfg["processed"] = p[-MAX_PROCESSED:]
            self.save()
            self.log.emit("已跳过 %d 条历史消息，从现在开始只处理新消息" % len(new))
            return 0

        if stopped:
            # ★ 停止后**不再把新消息抛进处理队列** —— 否则「点了停止，AI 还在回消息」。
            #   消息本身不丢：没标记成 processed，下次启动助理会重新读到。
            if new:
                self.log.emit("本轮扫描已停止：%d 条新消息留到下次处理" % len(new))
            new = []
        for it in mine_out:
            try:
                self.outgoing.emit(it)
            except Exception:
                pass
        for it in new:
            try:
                self.incoming.emit(it)
            except Exception:
                pass
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


# ======================================================================
# ②③⑤ 标签扫描服务（可停止 / 增量推送 / 跳官方号 / 自适应并发）
# ======================================================================
class TagScanService(QObject):
    """扫全部会话 → 让 AI 逐个打关系标签（后台线程，可随时停止）。

    与 ``app/im/tags.py: scan_and_tag()`` 的区别（也是重写编排的原因）：

    * 每个耗时阶段都有 ``_stop`` 检查点，点停止后线程立刻退出（不等网络）；
    * 每扫完一个会话就 ``partial.emit`` 一条，界面增量 append；
    * 官方号在**采集前**就跳过（省掉一次 dws 调用 + 一次模型调用）；
    * 并发度由实测带宽决定；失败按 0.5/1/2 秒指数退避重试，连续失败的会话
      标记为「跳过（网络）」后继续，不让整轮崩掉。
    """

    # i, n, 当前会话名, 有效速率(B/s)
    progress = pyqtSignal(int, int, str, float)
    # 增量结果：{cid,name,kind,tag,note,index,total}
    partial = pyqtSignal(dict)
    # 被跳过的官方号名字
    skipped = pyqtSignal(str)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    state = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._thread = None
        self._gen = 0
        self.speed_bps = 0.0
        self.skipped_count = 0
        self._last_emit = 0.0

    # ---------- 生命周期 ----------
    def running(self):
        return bool(self._thread is not None and self._thread.is_alive())

    def stop(self, reason="用户停止"):
        """请求停止。幂等：没在跑时返回 False，不会改任何状态。"""
        was = self.running()
        self._stop.set()
        if was:
            try:
                self.log.emit(reason)
            except Exception:
                pass
        return was

    def start(self, store, ding, llm_fn, max_convs=0, per_conv=0,
              probe=True, force_probe=False):
        """启动扫描（立刻返回）。重复 start 会被忽略。"""
        if self.running():
            return False
        self._stop.clear()
        self._gen += 1
        gen = self._gen
        self.skipped_count = 0
        self._last_emit = 0.0
        self._thread = threading.Thread(
            target=self._run,
            args=(gen, store, ding, llm_fn, int(max_convs or 0),
                  int(per_conv or 0), bool(probe), bool(force_probe)),
            daemon=True, name="awb-tag-scan")
        self._thread.start()
        self.state.emit(True)
        return True

    # ---------- 主流程 ----------
    def _run(self, gen, store, ding, llm_fn, max_convs, per_conv,
             probe, force_probe):
        started = time.time()
        res = {"ok": False, "reason": "", "tagged": 0, "total": 0,
               "skipped": 0, "stopped": False, "errors": [],
               "speed_bps": 0.0, "elapsed": 0.0}
        try:
            res = self._scan(gen, store, ding, llm_fn, max_convs, per_conv,
                             probe, force_probe, res)
        except Exception as exc:                  # noqa: BLE001（后台线程绝不外抛）
            res["ok"] = False
            res["reason"] = "%s: %s" % (type(exc).__name__, exc)
            try:
                self.log.emit("扫描失败：%s" % res["reason"])
            except Exception:
                pass
        finally:
            res.pop("_emitted", None)      # 内部计数，不往外传
            res["elapsed"] = round(time.time() - started, 2)
            res["stopped"] = bool(self._stop.is_set())
            try:
                self.state.emit(False)
            except Exception:
                pass
            # 代次号保证「停止后重启」不会收到上一轮迟到的 done
            if gen == self._gen:
                try:
                    self.done.emit(res)
                except Exception:
                    pass

    def _scan(self, gen, store, ding, llm_fn, max_convs, per_conv,
              probe, force_probe, res):
        from .im import tags as _t     # 延迟导入：纯逻辑复用，避免循环依赖

        # ---- 1. 登录状态 ----
        try:
            st = ding.dws_auth_status() or {}
        except Exception as exc:
            res["reason"] = "读取 dws 登录状态失败：%s: %s" % (type(exc).__name__, exc)
            return res
        if not (isinstance(st, dict) and st.get("authenticated")):
            res["reason"] = "dws 未登录，请先到「集成 → 钉钉」完成授权"
            return res
        me = str(st.get("user_name") or "").strip()
        if self._stop.is_set():
            res.update(ok=True, reason="", stopped=True)
            return res

        # ---- 2. 会话列表 ----
        items, reason = self._list_convs(ding, max_convs or _t.MAX_CONVS)
        if not items:
            res["reason"] = reason or "没读到任何会话（dws 返回空列表）"
            return res
        if self._stop.is_set():
            res.update(ok=True, stopped=True)
            return res

        items = _t.sort_convs(items)

        # ---- 3. ③ 采集前跳过官方号 ----
        convs = []
        seen = set()
        for c in items:
            if self._stop.is_set():
                break
            cid = str(c.get("openConversationId") or "").strip()
            if not cid or cid in seen:
                continue
            seen.add(cid)
            name = _t.conv_display_name(c) or "（未命名会话）"
            if is_official_account(name, c):
                self.skipped_count += 1
                line = "已跳过官方号：%s" % name
                try:
                    self.skipped.emit(name)
                    self.log.emit(line)
                except Exception:
                    pass
                continue
            convs.append({"cid": cid, "name": name,
                          "kind": _t.conv_kind(c),
                          "last_ts": c.get("last_ts") or 0.0})
        if max_convs and len(convs) > max_convs:
            convs = convs[:max_convs]
        total = len(convs)
        res["total"] = total
        res["skipped"] = self.skipped_count
        if not total:
            res.update(ok=True, reason="")
            self.log.emit("没有需要打标签的会话（已跳过 %d 个官方号）"
                          % self.skipped_count)
            return res

        # ---- 4. ⑤ 带宽探测 → 并发度 / 超时 ----
        workers, call_timeout = recommend_workers(0.0)
        if probe:
            self._emit_progress(0, total, "正在测试网络…", 0.0, force=True)
            bw = probe_bandwidth_cached(force=force_probe, stop_event=self._stop)
            self.speed_bps = float(bw.get("speed_bps") or 0.0)
            res["speed_bps"] = self.speed_bps
            workers, call_timeout = recommend_workers(self.speed_bps)
            if bw.get("ok"):
                self.log.emit("网络探测：%s（%d 档实测最快 %s），并发 %d、单次超时 %.0f 秒"
                              % (bw.get("tier"), len(bw.get("samples") or []),
                                 human_speed(self.speed_bps), workers, call_timeout))
            else:
                self.log.emit("网络探测失败，按弱网处理：并发 %d、单次超时 %.0f 秒"
                              % (workers, call_timeout))
            if bw.get("stopped") or self._stop.is_set():
                res.update(ok=True, stopped=True)
                return res

        # ---- 5. 并发拉消息（结果按原顺序回填，可停止） ----
        per_conv = per_conv or getattr(_t, "MAX_SAMPLE_PER_CONV", 80)
        self._emit_progress(0, total, "正在采集会话样本…", self.speed_bps, force=True)

        def _fetch(conv):
            # 单次调用无法传 timeout（dws 封装在 dingtalk.py 里，不在本次改动范围），
            # 但重试与「拿到就判停」都在这里做。
            raw = ding.dws_read_messages_raw(conv["cid"], per_conv) or []
            return raw if isinstance(raw, list) else []

        def _on_fetched(idx, value):
            conv = convs[idx]
            if isinstance(value, dict) and "__error__" in value:
                value = []            # 失败的会话样本留空，下面按「网络失败」标记
            conv["raw"] = value
            self._emit_progress(idx + 1, total, "采集·" + conv["name"],
                                self.speed_bps)

        _results, fetch_errors, fetch_stopped = ordered_concurrent_map(
            _fetch, convs, workers=max(2, min(int(workers), 8)),
            stop_event=self._stop, retries=2, backoff=(0.5, 1.0, 2.0),
            on_item=_on_fetched)
        for idx, err in fetch_errors:
            convs[idx]["net_failed"] = True
            res["errors"].append("%s：网络失败，已跳过（%s）"
                                 % (convs[idx]["name"], err))
            try:
                self.log.emit("跳过（网络）：%s（%s）" % (convs[idx]["name"], err))
            except Exception:
                pass
        if self._stop.is_set() or fetch_stopped:
            res.update(ok=True, stopped=True, tagged=0)
            return res

        # ---- 6. 逐个会话让模型打标签（按原顺序写回 + 增量推送） ----
        todo = [c for c in convs if not c.get("net_failed")]
        tag_workers = max(2, min(4, int(workers)))

        def _tag(conv):
            samples = self._samples_from_raw(ding, conv.get("raw") or [],
                                             me, per_conv)
            conv["samples"] = samples
            conv["me"] = me
            prompt = _t.build_prompt({"cid": conv["cid"], "name": conv["name"],
                                      "kind": conv["kind"], "samples": samples},
                                     me)
            return llm_fn(prompt, {"cid": conv["cid"], "name": conv["name"],
                                   "kind": conv["kind"], "samples": samples})

        def _on_tagged(idx, text):
            if isinstance(text, dict) and "__error__" in text:
                return                # 模型失败的会话不写标签，错误已在下面汇总
            conv = todo[idx]
            self._write_one(store, conv, text, _t, res)

        _, tag_errors, tag_stopped = ordered_concurrent_map(
            _tag, todo, workers=tag_workers, stop_event=self._stop,
            retries=0, poll=0.2, on_item=_on_tagged)
        for idx, err in tag_errors:
            res["errors"].append("%s：调用模型失败（%s）" % (todo[idx]["name"], err))
            try:
                self.log.emit("跳过（模型）：%s（%s）" % (todo[idx]["name"], err))
            except Exception:
                pass
        if self._stop.is_set() or tag_stopped:
            res["ok"] = True
            res["stopped"] = True
            self.log.emit("扫描已停止：已保留 %d 个会话的结果" % res["tagged"])
        else:
            res["ok"] = True
        return res

    # ---------- 子步骤 ----------
    def _list_convs(self, ding, max_convs):
        """读会话列表（优先 ``+chat-list``，它带 chatMode 能区分单聊/群聊）。"""
        items, data = [], None
        try:
            if hasattr(ding, "dws_chat_list"):
                page = min(100, max(20, int(max_convs or 120)))
                data = ding.dws_chat_list(types="group,p2p", page_size=page,
                                          page_all=True)
        except Exception:
            data = None
        if isinstance(data, dict):
            items = data.get("chats") or data.get("items") or []
        elif isinstance(data, list):
            items = data
        if items:
            items = [c for c in items if isinstance(c, dict)]
        if not items:
            try:
                data = ding.dws_conversation_list_full(max_convs or 120) or {}
            except Exception as exc:
                return [], "读取会话列表失败：%s: %s" % (type(exc).__name__, exc)
            if isinstance(data, dict):
                items = data.get("conversations") or []
            elif isinstance(data, list):
                items = data
            items = [c for c in items if isinstance(c, dict)]
        items = [c for c in items
                 if str(c.get("openConversationId") or "").strip()]
        if not items:
            return [], "没读到任何会话（dws 返回空列表）"
        return items, ""

    @staticmethod
    def _pick(m, *keys):
        for k in keys:
            if m.get(k) not in (None, ""):
                return m.get(k)
        return ""

    @staticmethod
    def _sender(m):
        s = m.get("sender")
        if isinstance(s, dict):
            s = s.get("name") or s.get("nick") or s.get("nickName") or ""
        return str(s or m.get("senderName") or "").strip()

    @staticmethod
    def _ts(m):
        for k in ("createTime", "sendTime", "time", "msgTime"):
            v = m.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v) / (1000.0 if float(v) > 1e11 else 1.0)
            s = str(v or "").strip()
            if s.isdigit():
                n = float(s)
                return n / (1000.0 if n > 1e11 else 1.0)
        return 0.0

    def _samples_from_raw(self, ding, raw, me, per_conv):
        """把原始消息压成样本（保留「我」说的话，剔除 AI 套话和纯媒体占位）。"""
        out = []
        for m in raw:
            if self._stop.is_set():
                break
            if not isinstance(m, dict):
                continue
            if m.get("messageAiSendFlag"):
                continue                      # AI 自己发的套话会污染判断
            try:
                text = ding._msg_text(m)
            except Exception:
                text = (m.get("text") or m.get("content") or "")
            text = re.sub(r"\s+", " ", str(text or "")).strip()[:120]
            if not text or text in ("[图片]", "[语音]", "[视频]", "[表情]", "[链接]"):
                continue
            who = self._sender(m)
            if me and who == me:
                who = "我"
            out.append({"sender": who or "（未知）", "text": text,
                        "ts": self._ts(m)})
        out = out[:int(per_conv or 80)]
        out.sort(key=lambda s: s.get("ts") or 0.0)
        return out

    def _write_one(self, store, conv, text, _t, res):
        """写回一个会话的标签，并立刻把这一条增量推给界面。"""
        cid = conv["cid"]
        name = conv["name"]
        tag_list = _t.guard_tags(_t.parse_tags(text),
                                 self._material_text(conv, _t),
                                 name, conv.get("kind") or "")
        tag = "、".join(tag_list) if tag_list else ""
        note = "AI 扫描" if tag else "AI 扫描：证据不足，未打标签"
        try:
            store.set_tag(cid, tag, note=note, auto=True,
                          name=name, kind=conv.get("kind"))
        except Exception as exc:
            res["errors"].append("%s：写标签失败 %s: %s"
                                 % (name, type(exc).__name__, exc))
            return
        if tag:
            res["tagged"] += 1
        row = {"cid": cid, "name": name, "kind": conv.get("kind") or "group",
               "tag": tag, "note": note,
               "index": int(res.get("_emitted") or 0) + 1}
        res["_emitted"] = int(res.get("_emitted") or 0) + 1
        try:
            self.partial.emit(row)
        except Exception:
            pass
        # 进度用「已处理数」而不是「已打上标签数」——证据不足的会话也走了一步，
        # 否则 0/57 会一直不动，看着像卡死。
        self._emit_progress(row["index"], res["total"], "打标签·" + name,
                            self.speed_bps)

    @staticmethod
    def _material_text(conv, _t):
        """给 ``guard_tags`` 的「证据材料」文本（会话名 + 全部样本）。"""
        try:
            return _t._material_text(conv, conv.get("me") or "")   # noqa: SLF001
        except Exception:
            parts = [conv.get("name") or ""]
            for s in conv.get("samples") or []:
                parts.append("%s：%s" % (s.get("sender") or "", s.get("text") or ""))
            return "\n".join(parts)

    def _emit_progress(self, i, n, name, speed, force=False):
        """进度节流：每 20 条或每 200ms（取先到者）推一次。"""
        now = time.time()
        if not force and i % 20 != 0 and (now - self._last_emit) < 0.2:
            return
        self._last_emit = now
        try:
            self.progress.emit(int(i), int(n), str(name or ""), float(speed or 0.0))
        except Exception:
            pass


__all__ = [
    "AssistantService", "TagScanService", "DEFAULT_CFG", "MAX_PROCESSED",
    "OFFICIAL_ACCOUNT_NAMES", "OFFICIAL_ACCOUNT_PREFIXES",
    "normalize_conv_name", "is_official_account",
    "probe_bandwidth", "probe_bandwidth_cached", "reset_bandwidth_cache",
    "recommend_workers", "bandwidth_tier", "human_speed",
    "ordered_concurrent_map",
]
