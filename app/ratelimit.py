# -*- coding: utf-8 -*-
"""全局模型调用节流器（v9.20.0）。

用户原话：
  「调用模型让尽量控制这个时间，不要太快，否则很容易崩溃或者触发风控」

为什么需要**全局**一个：
  主对话每一轮、工具调用循环每一轮（`max_iter` 最多 12）、助理预取、
  团队多角色并行、后台任务池（`MAX_RUNNING = 4`）——它们在不同的线程里
  各调各的模型，**抢的是同一份配额**。谁都不认识谁，于是就"一秒钟发六个请求"，
  结果就是 DeepSeek 网页版回「输入过快，请稍后重试」，或者程序直接崩。

三条规则（`acquire()` 一次性全部满足）：
  1. 任意两次模型请求的**真实发起时刻**至少隔 `MIN_GAP` 秒；
  2. 同一个 tag（如 "deepseek-web"）的连续请求也至少隔 `MIN_GAP` 秒；
  3. 同时"在飞"的请求不超过 `MAX_CONCURRENT` 个（信号量）。

用法（**必须配对**，否则名额会漏光）：
    ratelimit.acquire("model")
    try:
        do_request()
    finally:
        ratelimit.release()

⚠ `acquire()` 的排队等待**不能**算进请求超时：调用方要在拿到名额**之后**
  才开始算超时（`requests` 的 timeout 本来就是在 `post()` 里起算的，天然满足）。
"""
import random
import re
import threading
import time

from . import config

__all__ = [
    "MIN_GAP", "MAX_CONCURRENT", "acquire", "release", "reset", "stats",
    "note_rate_limit", "is_rate_limit", "backoff",
]

#: 任意两次请求之间的最小间隔（秒）。测试里可以 monkeypatch 成小值。
MIN_GAP = config.MODEL_MIN_GAP
#: 同时进行的模型请求上限。改了之后要 `reset()` 才会生效（信号量要重建）。
MAX_CONCURRENT = config.MODEL_MAX_CONCURRENT

_LOCK = threading.RLock()
_SEM = threading.Semaphore(MAX_CONCURRENT)

_calls = 0              # acquire 成功次数
_waited = 0.0           # 累计排队等待秒数
_last_start = 0.0       # 上一次真实发起时刻（monotonic）
_last_gap = 0.0         # 最近两次真实发起之间的间隔
_throttled = 0          # 因为排队而实际等过（>0.05s）的次数
_next_start = 0.0       # 预约档期：下一个可以发起的时刻
_last_tag_start = {}    # tag -> 上一次"预约"的时刻
_last_tag_real = {}     # tag -> 上一次"真实"发起的时刻
_backoff_until = 0.0    # 被风控后的全局静默期截止时刻
_rl_streak = 0          # 连续被风控的次数（退避指数据此递增）
_rl_last_ts = 0.0       # 上一次被风控的时刻（用于自动清零 streak）

# 中文关键词：这些是 DeepSeek / 国内平台的原话，直接子串匹配即可。
_CN_KEYS = (
    "输入过快", "稍后重试", "请稍后再试", "请求过于频繁", "请求太频繁",
    "系统繁忙", "服务器繁忙", "当前人数过多", "人数过多", "访问频繁",
    "请求频率", "限流",
)
# 英文关键词：用词边界，避免 "busybox" 这类误判成 busy。
_EN_RE = re.compile(
    r"rate[\s_-]*limit|too[\s_-]*many[\s_-]*requests?|throttl|overload|\bbusy\b",
    re.IGNORECASE)
# 出现 429 时，只有**错误/状态语境**才算限流 ——
# 绝不能把「文件第 429 行」这种正常文本当成限流（会白白退避几十秒）。
_429_CONTEXT = (
    "http", "status", "code", "error", "retry", "too many", "limit",
    "请求", "错误", "状态", "响应", "返回", "接口", "限流", "失败",
)


def _text_of(obj):
    """把异常 / 响应 / 字符串统一成一段可匹配的文本（取不到就空串）。"""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        try:
            return bytes(obj).decode("utf-8", "ignore")
        except Exception:
            return ""
    parts = []
    try:
        parts.append(str(obj))
    except Exception:
        pass
    # requests.HTTPError 之类：状态码在 .response.status_code 上
    for holder in (obj, getattr(obj, "response", None)):
        if holder is None:
            continue
        for attr in ("status_code", "code", "status"):
            try:
                val = getattr(holder, attr, None)
            except Exception:
                val = None
            if isinstance(val, int):
                parts.append(f"HTTP {val}")
                break
    return " ".join(p for p in parts if p)


def is_rate_limit(text_or_exc):
    """判断是不是「调用太快」类错误（大小写不敏感、中英都认）。

    ⚠ 防误判：`429` 必须出现在**错误/状态语境**里才算；
      「文件第 429 行」这种普通文本一律返回 False。
    """
    text = _text_of(text_or_exc)
    if not text:
        return False

    for key in _CN_KEYS:
        if key in text:
            return True
    if _EN_RE.search(text):
        return True

    if "429" in text:
        low = text.lower()
        if re.search(r"\b429\b", low) and any(m in low for m in _429_CONTEXT):
            return True
    return False


def backoff(n, base=2.0, cap=60.0):
    """第 n 次重试该等多久：`base * 2**(n-1)`，封顶 `cap`，±20% 抖动。

    抖动是必须的：多个线程同时被风控时，如果都精确等 2.0 秒，
    醒来又会同时发请求，二次撞墙（惊群）。
    """
    try:
        n = max(1, int(n))
    except Exception:
        n = 1
    sec = float(base) * (2 ** (n - 1))
    sec = min(sec, float(cap))
    return sec * random.uniform(0.8, 1.2)


def acquire(tag="model", timeout=120.0):
    """阻塞到"现在可以发请求"为止，返回实际等了多久（秒）。

    调用方**必须**在请求结束后调 `release()` 归还并发名额。

    `timeout` 只截断"排队等档期"的第一段等待（默认 120s）。
    即使截断了也不会抢跑：后面的校正阶段仍会把 `MIN_GAP` 等够
    （MIN_GAP 只有 1.2s，正常根本碰不到这个上限）。
    """
    global _calls, _waited, _last_start, _last_gap, _throttled
    global _next_start, _backoff_until

    t0 = time.monotonic()

    # ① 预约一个档期：把"我准备在 start_at 发起"写进全局队列，
    #    这样 6 个线程同时进来也不会算出同一个时刻。
    with _LOCK:
        now = time.monotonic()
        start_at = now
        if _last_start:
            start_at = max(start_at, _last_start + MIN_GAP)
        if _next_start > start_at:
            start_at = _next_start
        if _backoff_until > start_at:
            start_at = _backoff_until
        tag_last = _last_tag_start.get(tag, 0.0)
        if tag_last:
            start_at = max(start_at, tag_last + MIN_GAP)
        _next_start = start_at + MIN_GAP
        _last_tag_start[tag] = start_at

    delay = start_at - time.monotonic()
    if delay > 0:
        time.sleep(min(delay, max(0.0, float(timeout))))

    # ② 拿并发名额（同一时刻最多 MAX_CONCURRENT 个请求在飞）
    _SEM.acquire()

    # ③ 真实发起时刻再做一次校正：上面只是"预约"，线程可能被调度器拖晚，
    #    拖晚的那个会把间隔压缩 —— 这里以**真实时刻**为准再补睡一次。
    while True:
        with _LOCK:
            now = time.monotonic()
            earliest = 0.0
            if _last_start:
                earliest = _last_start + MIN_GAP
            if _backoff_until > earliest:
                earliest = _backoff_until
            tag_real = _last_tag_real.get(tag, 0.0)
            if tag_real:
                earliest = max(earliest, tag_real + MIN_GAP)
            if now >= earliest:
                _last_gap = (now - _last_start) if _last_start else 0.0
                _last_start = now
                _last_tag_real[tag] = now
                _calls += 1
                waited = now - t0
                _waited += waited
                if waited > 0.05:
                    _throttled += 1
                return waited
        time.sleep(min(earliest - now, 0.5))


def release():
    """归还 `acquire()` 拿到的并发名额（必须与 acquire 配对调用）。"""
    try:
        _SEM.release()
    except Exception:
        pass


def note_rate_limit(tag="model"):
    """报告「刚被风控了」：进入全局退避期，返回这次应该等多少秒。

    除了返回值，还会把 `_backoff_until` 往后推 —— 这样**别的线程**的
    `acquire()` 也会被一起拖住，不会出现"我这个线程在退避，别的线程照发"。
    """
    global _backoff_until, _rl_streak, _rl_last_ts
    with _LOCK:
        now = time.monotonic()
        # 很久没再被风控 -> 连续计数清零，避免跑了一整天后永远等 60 秒
        if _rl_last_ts and (now - _rl_last_ts) > 180.0:
            _rl_streak = 0
        _rl_streak += 1
        _rl_last_ts = now
        wait = backoff(_rl_streak)
        base_at = max(now, _backoff_until, _next_start)
        _backoff_until = base_at + wait
        return wait


def reset():
    """测试用：清空统计、档期、退避期，并重建信号量。"""
    global _SEM, _calls, _waited, _last_start, _last_gap, _throttled
    global _next_start, _backoff_until, _rl_streak, _rl_last_ts
    with _LOCK:
        _SEM = threading.Semaphore(max(1, int(MAX_CONCURRENT)))
        _calls = 0
        _waited = 0.0
        _last_start = 0.0
        _last_gap = 0.0
        _throttled = 0
        _next_start = 0.0
        _backoff_until = 0.0
        _rl_streak = 0
        _rl_last_ts = 0.0
        _last_tag_start.clear()
        _last_tag_real.clear()


def stats():
    """当前节流统计（给界面/探针看）：
    `calls` 已放行请求数、`waited` 累计等待秒数、`last_gap` 最近两次间隔、`throttled` 被拖住次数。
    """
    with _LOCK:
        return {
            "calls": int(_calls),
            "waited": round(float(_waited), 3),
            "last_gap": round(float(_last_gap), 3),
            "throttled": int(_throttled),
        }
