# -*- coding: utf-8 -*-
"""中英文混排的「文本占位」估算。

python-pptx / python-docx 都不会替我们排版，也不会告诉我们一段字放不放得下。
这里用「字宽 em + 贪心折行」做一个足够准的估算（误差约 ±5%，对「会不会溢出」
这个判断远远够用），渲染层用它自动缩字号，测试用它判断是否溢出。

约定：
- 一个中文字 / 全角符号 = 1.0 em
- ASCII 小写字母 ≈ 0.52 em，大写 ≈ 0.62 em，数字 ≈ 0.56 em，空格 0.30 em
- 折行时中文按字断，英文按词断（不把单词劈两半）
"""

# CJK 及全角区段
_CJK_RANGES = (
    (0x2E80, 0x2FFF),    # 部首、康熙部首
    (0x3000, 0x303F),    # CJK 标点
    (0x3040, 0x30FF),    # 日文假名
    (0x3400, 0x4DBF),    # 扩展 A
    (0x4E00, 0x9FFF),    # 基本汉字
    (0xF900, 0xFAFF),    # 兼容汉字
    (0xFE30, 0xFE4F),    # 兼容形式
    (0xFF00, 0xFF60),    # 全角 ASCII
    (0xFFE0, 0xFFE6),
    (0xAC00, 0xD7AF),    # 韩文
)

_PUNCT_LOW = set(".,:;'\"|!()[]{}<>/-\\")
_PUNCT_WIDE = set("，。、；：？！“”‘’（）《》【】—…·")


def is_cjk(ch):
    if not ch:
        return False
    o = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= o <= hi:
            return True
    return False


def char_em(ch, bold=False):
    """单个字符的宽度（单位 em）。"""
    if not ch:
        return 0.0
    o = ord(ch)
    if ch in ("\n", "\r", "\t"):
        return 0.0
    if is_cjk(ch) or ch in _PUNCT_WIDE:
        w = 1.0
    elif ch == " ":
        w = 0.30
    elif ch.isdigit():
        w = 0.56
    elif ch.isupper():
        w = 0.64
    elif ch.islower():
        w = 0.52
    elif ch in _PUNCT_LOW:
        w = 0.32
    else:
        w = 0.55
    return w * (1.06 if bold else 1.0)


def text_em(text, bold=False):
    """整段文字的宽度（单位 em）。"""
    return sum(char_em(c, bold) for c in (text or ""))


def _tokens(text):
    """切成不可再分的折行单元：中文单字 / 连续 ASCII 词。"""
    out = []
    buf = []
    for ch in text or "":
        if is_cjk(ch) or ch in _PUNCT_WIDE or ch == " ":
            if buf:
                out.append("".join(buf))
                buf = []
            out.append(ch)
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def wrap_lines(text, width_in, font_pt, bold=False):
    """估算在 width_in 宽的容器里、font_pt 号字下这段文字占几行。"""
    text = (text or "").replace("\r", "")
    if not text.strip():
        return 1
    usable_pt = max(width_in, 0.1) * 72.0
    per_line_em = max(usable_pt / max(font_pt, 1.0), 1.0)
    lines = 1
    used = 0.0
    for tok in _tokens(text):
        w = text_em(tok, bold)
        if tok == " " and used == 0.0:
            continue
        if used + w > per_line_em + 0.001:
            if tok == " ":
                lines += 1
                used = 0.0
                continue
            # 单个 token 比整行还宽（超长英文单词）：按字符硬切
            if w > per_line_em:
                lines += int(w // per_line_em)
                used = w % per_line_em
                continue
            lines += 1
            used = w
        else:
            used += w
    return lines


def estimate_height_in(paras, width_in, font_pt, line_spacing=1.35,
                       space_after_pt=8.0, indent_em=0.0):
    """估算一组段落的总高度（英寸）。

    paras: [str] 或 [(text, level)]，level 每加一级，可用宽度少 indent_em 个 em。
    """
    total_pt = 0.0
    for item in paras or []:
        if isinstance(item, (tuple, list)):
            text, level = item[0], item[1] if len(item) > 1 else 0
        else:
            text, level = item, 0
        avail = max(width_in - (indent_em * level * font_pt / 72.0), 0.6)
        n = wrap_lines(text, avail, font_pt)
        total_pt += n * font_pt * line_spacing + space_after_pt
    return total_pt / 72.0


def fit_font_size(paras, width_in, height_in, sizes, line_spacing=1.35,
                  space_after_pt=8.0, indent_em=0.0):
    """从大到小试字号，返回 (最终字号, 是否放得下)。

    sizes 必须按降序传入。全部放不下时返回最小字号 + False。
    """
    cand = sorted([s for s in (sizes or []) if s], reverse=True)
    if not cand:
        return 12.0, True
    for s in cand:
        h = estimate_height_in(paras, width_in, s, line_spacing,
                               space_after_pt, indent_em)
        if h <= height_in * 0.98:
            return float(s), True
    return float(cand[-1]), False


def split_pages(items, max_items=6, max_chars=240):
    """把要点切成多页：单页最多 max_items 条 / max_chars 字。

    items: [(text, level, ordered)]；返回 [[(text, level, ordered)], ...]
    """
    pages = []
    cur = []
    chars = 0
    for it in items or []:
        text = it[0] if isinstance(it, (tuple, list)) else str(it)
        weight = len(text) * (0.8 if (len(it) > 1 and it[1]) else 1.0)
        if cur and (len(cur) >= max_items or chars + weight > max_chars):
            pages.append(cur)
            cur, chars = [], 0
        cur.append(it)
        chars += weight
    if cur:
        pages.append(cur)
    return pages or [[]]
