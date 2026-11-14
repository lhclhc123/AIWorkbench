# -*- coding: utf-8 -*-
"""语音识别文本的标点恢复（纯函数，零第三方依赖）。

为什么需要（v9.22.11）
----------------------
本机 sherpa-onnx + 中文 Paraformer 输出的是**不带任何标点**的纯汉字串，
用户原话：「说一段话中间没有自动分隔的逗号，整段文字没有分隔」。
Paraformer 不带标点预测头，也不返回词级时间戳，所以这里用两条信息补标点：

  1. 停顿（首选）：asr_local.recognize_with_pauses() 按 20ms 帧算 RMS，
     找出 >300ms 的低能量段，给出「段中心 / 总时长」比例（可带时长）。
     restore() 按字符数线性映射到文本位置：普通停顿插「，」，
     >=0.7s 的长停顿插「。」。
  2. 语义兜底（没有停顿信息时）：在常见连接词前、以及每 10~16 个汉字处
     插「，」，句末按语气补「。」/「？」。

硬约束
------
不破坏已有标点（已有标点直接原样返回，云端带标点的结果不会被改坏）、
不连续插两个逗号、不在数字/英文单词中间插、不在句首插、
纯 ASCII 文本原样返回、空串原样返回、幂等 restore(restore(x)) == restore(x)。
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "restore", "has_punctuation", "is_safe_boundary",
    "detect_pause_ratios", "PUNCT_CHARS", "LONG_PAUSE_SECONDS",
]

# 所有会被当成「标点」的字符（判断能否插入 / 找断点时用）
PUNCT_CHARS = frozenset(
    "，。！？；：、,.!?;:…～~“”‘’\"'（）()【】[]《》〈〉「」『』—-")
# 用来判断「这段文本已经带标点了，别动它」
_EXISTING_PUNCT = frozenset("，。！？；：、,.!?;:…～~")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]")

LONG_PAUSE_SECONDS = 0.7       # >=0.7s 的停顿算句子结束
_MIN_INSERT_GAP = 3            # 两个插入点至少隔这么多字符（防「，，」）
_RUN_STEP = 12                 # 每 12 个汉字补一个逗号（落在 10~16 区间）
_MIN_TAIL = 4                  # 句尾至少留这么多字，别在末尾硬插逗号

# 常见连接词 / 语气衔接词（按长度降序匹配，长词优先）
CONNECTIVES = (
    "也就是说", "这样的话", "这个时候", "接下来",
    "另外", "然后", "因为", "所以", "但是", "如果", "而且", "不过",
    "接着", "首先", "其次", "最后", "其实", "比如", "于是", "因此",
    "然而", "并且", "再说", "总之", "那么", "还有",
)
_CONNECTIVES_BY_LEN = tuple(sorted(CONNECTIVES, key=len, reverse=True))

_Q_ENDINGS = ("吗", "呢", "么", "什么", "怎么", "多少", "为什么",
              "是不是", "几点", "几号")


def has_punctuation(text: str) -> bool:
    """文本里是否已经有句子标点（有就别再改，保护云端已带标点的结果）。"""
    if not isinstance(text, str) or not text:
        return False
    return any(ch in _EXISTING_PUNCT for ch in text)


def is_safe_boundary(text: str, index: int) -> bool:
    """能否在 text[index] 之前插入标点。

    句首/句尾、紧贴已有标点、空格、以及数字或英文单词内部都不允许，
    保证「不拆词、不叠标点、不在句首插」。
    """
    if not isinstance(text, str) or index <= 0 or index >= len(text):
        return False
    prev = text[index - 1]
    nxt = text[index]
    if prev in PUNCT_CHARS or nxt in PUNCT_CHARS:
        return False
    if prev.isspace() or nxt.isspace():
        return False
    if _ASCII_WORD_RE.match(prev) or _ASCII_WORD_RE.match(nxt):
        return False
    return True


def _is_ascii_only(text: str) -> bool:
    """整串都是 ASCII 字符（纯英文/数字）时不做任何处理。"""
    return all(ord(ch) < 128 for ch in text)


def _one_pause(item) -> Tuple[Optional[float], float, bool]:
    """把一条停顿信息规范成 (比例, 时长秒, 是否长停顿)。

    支持四种写法：float 比例、(比例, 秒数)、(比例, 秒数, 是否长)、
    dict(ratio/t/pos + duration/seconds/dur + long)。
    解析不了返回 (None, 0.0, False)，由调用方丢弃。
    """
    try:
        if isinstance(item, dict):
            ratio = item.get("ratio", item.get("t", item.get("pos")))
            dur = item.get("duration", item.get("seconds", item.get("dur", 0.0)))
            is_long = bool(item.get("long", False))
        elif isinstance(item, (tuple, list)):
            if not item:
                return (None, 0.0, False)
            ratio = item[0]
            dur = item[1] if len(item) > 1 else 0.0
            is_long = bool(item[2]) if len(item) > 2 else False
        else:
            ratio, dur, is_long = item, 0.0, False
        ratio = float(ratio)
        dur = float(dur or 0.0)
        if not (0.0 <= ratio <= 1.0):     # NaN / 越界一并挡掉
            return (None, 0.0, False)
        return (ratio, dur, bool(is_long or dur >= LONG_PAUSE_SECONDS))
    except (TypeError, ValueError, OverflowError):
        return (None, 0.0, False)


def _normalize_pauses(pauses, long_pauses) -> List[Tuple[float, float, bool]]:
    """规范化停顿列表，并按时间比例排序。"""
    out: List[Tuple[float, float, bool]] = []
    for item in (pauses or ()):
        ratio, dur, is_long = _one_pause(item)
        if ratio is not None:
            out.append((ratio, dur, is_long))
    for item in (long_pauses or ()):
        ratio, dur, _ = _one_pause(item)
        if ratio is not None:
            out.append((ratio, max(dur, LONG_PAUSE_SECONDS), True))
    out.sort(key=lambda x: x[0])
    return out


def _add_insert(inserts: Dict[int, str], core: str, pos: int, mark: str) -> None:
    """在 pos 附近找一个安全位置插入 mark；太挤就放弃（防连续逗号）。

    停顿按字符数线性映射出来的位置可能正好落在「公园」这种双字词中间，
    所以 ±3 范围内若有连接词起点，优先往那儿靠（断句更自然）。
    """
    n = len(core)
    candidates = [pos + d for d in (0, -1, 1, -2, 2, -3, 3)]
    semantic = [p for p in candidates
                if 0 < p < n and any(core.startswith(w, p)
                                     for w in _CONNECTIVES_BY_LEN)]
    ordered = semantic + [p for p in candidates if p not in semantic]
    for p in ordered:
        if p < 2 or p >= n - 1:
            continue
        # 句号后面只剩两三个字（例如「。测试。」这种碎句）就别插了，
        # 句末会统一补一个句号，短尾巴不值得再切一刀
        if mark == "。" and n - p < 4:
            continue
        if not is_safe_boundary(core, p):
            continue
        if any(abs(p - q) < _MIN_INSERT_GAP for q in inserts):
            continue
        inserts[p] = mark
        return


def _add_run_inserts(inserts: Dict[int, str], core: str) -> None:
    """过长的连续汉字串里，每 _RUN_STEP 个字符补一个逗号（语义兜底）。"""
    n = len(core)
    breaks = sorted(set([0] + list(inserts.keys()) + [n]))
    for a, b in zip(breaks, breaks[1:]):
        p = a + _RUN_STEP
        while p <= b - _MIN_TAIL:
            _add_insert(inserts, core, p, "，")
            p += _RUN_STEP


def _finalize_inserts(inserts: Dict[int, str], core: str) -> Dict[int, str]:
    """二次校验：仍安全、且彼此不挨着，才真正落下。"""
    out: Dict[int, str] = {}
    for pos in sorted(inserts):
        if not is_safe_boundary(core, pos):
            continue
        if any(abs(pos - q) < _MIN_INSERT_GAP for q in out):
            continue
        out[pos] = inserts[pos]
    return out


def _apply_inserts(core: str, positions: Dict[int, str]) -> str:
    """按位置把标点织进原文（原文一个字符都不删）。"""
    out: List[str] = []
    for i, ch in enumerate(core):
        if i in positions:
            out.append(positions[i])
        out.append(ch)
    return "".join(out)


def _final_mark(text: str) -> str:
    """句末按语气补一个标点；已有标点/空串就不再补。"""
    if not text:
        return ""
    if text[-1] in PUNCT_CHARS:
        return ""
    if text.endswith(_Q_ENDINGS):
        return "？"
    return "。"


def detect_pause_ratios(samples: Sequence[float], sample_rate: int = 16000,
                        frame_ms: int = 20, min_silence_ms: int = 300,
                        long_silence_ms: Optional[float] = None
                        ) -> List[Tuple[float, float, bool]]:
    """按 20ms 帧算 RMS，找出连续低能量 >300ms 的段。

    Args:
        samples: 16k 单声道、归一化到 [-1, 1] 的 float 采样点。
        sample_rate: 采样率（默认 16000）。
        frame_ms: 帧长（默认 20ms）。
        min_silence_ms: 短于这个时长的低能量段不算停顿。
        long_silence_ms: 长停顿阈值，默认 LONG_PAUSE_SECONDS * 1000。

    Returns:
        [(段中心时间 / 总时长, 停顿秒数, 是否长停顿), ...]。
        录音首尾的静音会被丢掉（映射后落在句首/句尾，插标点没意义）。
    """
    if long_silence_ms is None:
        long_silence_ms = LONG_PAUSE_SECONDS * 1000.0
    frame = max(1, int(sample_rate * frame_ms / 1000.0))
    n = len(samples)
    if n <= 0 or frame <= 0:
        return []
    energies: List[float] = []
    for start in range(0, n, frame):
        chunk = samples[start:start + frame]
        if not chunk:
            break
        total = 0.0
        for value in chunk:
            total += value * value
        energies.append(math.sqrt(total / len(chunk)))
    if not energies:
        return []
    peak = max(energies)
    # 自适应门限：安静环境用绝对底噪 0.006，有声音时按峰值的一成二
    threshold = max(0.006, peak * 0.12)
    silent = [e < threshold for e in energies]
    min_frames = max(1, int(math.ceil(min_silence_ms / float(frame_ms))))
    count = len(silent)
    # 前缀和： voiced[k] = 前 k 帧里「有声音」的帧数
    voiced = [0] * (count + 1)
    for k in range(count):
        voiced[k + 1] = voiced[k] + (0 if silent[k] else 1)
    if voiced[count] == 0:
        # 整段都是静音（合成测试 wav 常见）：本身就是一个大停顿。
        # 识别结果通常是空串，restore("") 会原样返回，不会插出怪标点。
        return [(0.5, n / float(sample_rate), True)]
    out: List[Tuple[float, float, bool]] = []
    i = 0
    while i < count:
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < count and silent[j]:
            j += 1
        if j - i >= min_frames:
            center_sample = (i + j) / 2.0 * frame
            ratio = center_sample / float(n)
            duration = (j - i) * frame_ms / 1000.0
            # 首尾静音不算停顿：开头是「还没开口」，结尾是「说完了」。
            # 尤其结尾那段 0.7~10s 的静音（Recorder 默认等 10 秒才自动停）
            # 线性映射后会落在句尾，插出「。测试。」这种碎句 —— 必须挡掉。
            voiced_before = voiced[i] * frame_ms / 1000.0
            voiced_after = (voiced[count] - voiced[j]) * frame_ms / 1000.0
            if (0.03 <= ratio <= 0.97
                    and voiced_before >= 0.2 and voiced_after >= 0.5):
                out.append((ratio, duration,
                            duration * 1000.0 >= float(long_silence_ms)))
        i = j
    return out


def restore(text: str, pauses: Optional[Sequence] = None,
            long_pauses: Optional[Sequence] = None) -> str:
    """给一段无标点的识别文本补上逗号/句号。

    Args:
        text: ASR 原始文本。
        pauses: 停顿点列表。元素可以是 0~1 的浮点比例，
            也可以是 (比例, 停顿秒数) / (比例, 秒数, 是否长停顿) / dict。
            非空时按字符数线性映射插入「，」/「。」。
        long_pauses: 额外的长停顿比例列表，强制插「。」。

    Returns:
        补好标点的文本。已有标点 / 纯 ASCII / 空串原样返回。
    """
    if not isinstance(text, str) or not text or not text.strip():
        return text
    if _is_ascii_only(text) or has_punctuation(text):
        return text
    lead = text[:len(text) - len(text.lstrip())]
    tail = text[len(text.rstrip()):]
    core = text.strip()
    n = len(core)
    inserts: Dict[int, str] = {}
    norm = _normalize_pauses(pauses, long_pauses)
    if norm:
        # 有真实停顿：优先按停顿点插，语义规则不再叠加，避免一句话被切碎
        for ratio, _dur, is_long in norm:
            pos = int(round(ratio * n))
            _add_insert(inserts, core, pos, "。" if is_long else "，")
    else:
        # 没停顿：语义规则兜底 —— 连接词前断句
        for word in _CONNECTIVES_BY_LEN:
            start = 0
            while True:
                i = core.find(word, start)
                if i < 0:
                    break
                if 0 < i < n:
                    _add_insert(inserts, core, i, "，")
                start = i + len(word)
    # 无论走哪条路，过长的连续汉字串都补逗号（每 10~16 字一处）
    _add_run_inserts(inserts, core)
    final_positions = _finalize_inserts(inserts, core)
    out = _apply_inserts(core, final_positions)
    return lead + out + _final_mark(out) + tail
