# -*- coding: utf-8 -*-
"""PPT 主题定义。

一套主题 = 一组颜色 + 一组字号 + 一组版式取向（封面怎么排、标题条怎么画）。
渲染代码在 ``app/docwrite.py``，这里只放「设计决策」，方便以后加主题时不用动渲染逻辑。

加新主题的做法：往 ``PPT_THEMES`` 里追加一个 dict，填齐 ``_REQUIRED`` 里的键即可，
渲染层不会因为缺字段崩（``get_theme`` 会用默认主题补齐）。
"""

# 所有主题必须提供的键（get_theme 会用 DEFAULT 主题补齐缺失项）
_REQUIRED = (
    "key", "name", "aliases", "is_dark",
    "bg", "bg_alt", "primary", "primary_dark", "accent", "text", "muted",
    "line", "on_primary",
    "cover_style", "title_style", "bullet_glyph",
    "size", "serif_hint",
)

# 统一使用微软雅黑：Windows 必有，中英文都不发虚。
# 不用 "微软雅黑" 作 latin 名字是因为部分老版本 PowerPoint 解析中文 typeface 名会退到 Calibri。
PPT_FONT = "Microsoft YaHei"
PPT_FONT_CJK = "微软雅黑"
PPT_FONT_MONO = "Consolas"


PPT_THEMES = [
    {
        # ---------------------------------------------------------- 清爽学术
        "key": "academic",
        "name": "清爽学术",
        "aliases": ["学术", "清爽", "academic", "light", "浅色", "默认", "default", "白底"],
        "is_dark": False,
        "bg": "FFFFFF",
        "bg_alt": "F2F6FB",
        "primary": "1B4B7A",
        "primary_dark": "123252",
        "accent": "2A9D8F",
        "text": "1F2A37",
        "muted": "6B7A8C",
        "line": "D6E1EE",
        "on_primary": "FFFFFF",
        # 封面：左侧色条 + 左对齐文字；标题条：短横线在标题下方
        "cover_style": "bar",
        "title_style": "bar",
        "bullet_glyph": "•",
        "serif_hint": False,
        "size": {
            "cover_title": 44, "cover_sub": 20, "cover_meta": 13,
            "section_num": 88, "section_title": 36,
            "slide_title": 28, "body": 20, "body2": 16,
            "table": 12, "table_head": 12.5, "footer": 9,
            "toc": 20, "end_title": 52,
        },
        "chart_colors": ["1B4B7A", "2A9D8F", "C9A227", "7C8CA6"],
    },
    {
        # ---------------------------------------------------------- 深色科技
        "key": "dark_tech",
        "name": "深色科技",
        "aliases": ["科技", "深色", "暗色", "科技感", "dark", "tech", "深蓝"],
        "is_dark": True,
        "bg": "0B1220",
        "bg_alt": "131E33",
        "primary": "22D3EE",
        "primary_dark": "0E7490",
        "accent": "A78BFA",
        "text": "E8EFFA",
        "muted": "8595AE",
        "line": "22304C",
        "on_primary": "06121F",
        "cover_style": "glow",
        "title_style": "dot",
        "bullet_glyph": "▸",
        "serif_hint": False,
        "size": {
            "cover_title": 46, "cover_sub": 20, "cover_meta": 13,
            "section_num": 92, "section_title": 36,
            "slide_title": 28, "body": 19, "body2": 15,
            "table": 12, "table_head": 12.5, "footer": 9,
            "toc": 20, "end_title": 52,
        },
        "chart_colors": ["22D3EE", "A78BFA", "F5B942", "4ADE80"],
    },
    {
        # ---------------------------------------------------------- 国风暖色
        "key": "guofeng",
        "name": "国风暖色",
        "aliases": ["国风", "传统", "暖色", "古典", "中国风", "guofeng", "chinese", "宣纸"],
        "is_dark": False,
        "bg": "FBF5EA",
        "bg_alt": "F3E7D3",
        "primary": "9E3B2F",
        "primary_dark": "6E2A22",
        "accent": "C9A227",
        "text": "3B2C21",
        "muted": "8A7561",
        "line": "DCC9AE",
        "on_primary": "FDF6EA",
        "cover_style": "frame",
        "title_style": "rule",
        "bullet_glyph": "◆",
        "serif_hint": True,
        "size": {
            "cover_title": 46, "cover_sub": 20, "cover_meta": 13,
            "section_num": 84, "section_title": 36,
            "slide_title": 28, "body": 20, "body2": 16,
            "table": 12, "table_head": 12.5, "footer": 9,
            "toc": 20, "end_title": 52,
        },
        "chart_colors": ["9E3B2F", "C9A227", "6E7F5C", "8A7561"],
    },
    {
        # ---------------------------------------------------------- 商务简约
        "key": "business",
        "name": "商务简约",
        "aliases": ["商务", "简约", "汇报", "business", "corporate", "蓝灰"],
        "is_dark": False,
        "bg": "FFFFFF",
        "bg_alt": "EEF1F6",
        "primary": "1F3A5F",
        "primary_dark": "16293F",
        "accent": "B08A3E",
        "text": "232B36",
        "muted": "6E7A8A",
        "line": "DDE3EB",
        "on_primary": "FFFFFF",
        "cover_style": "band",
        "title_style": "bar",
        "bullet_glyph": "▪",
        "serif_hint": False,
        "size": {
            "cover_title": 42, "cover_sub": 19, "cover_meta": 13,
            "section_num": 86, "section_title": 34,
            "slide_title": 26, "body": 19, "body2": 15,
            "table": 12, "table_head": 12.5, "footer": 9,
            "toc": 19, "end_title": 50,
        },
        "chart_colors": ["1F3A5F", "B08A3E", "5B7C99", "8C9AA8"],
    },
]

DEFAULT_THEME_KEY = "academic"

_BY_KEY = {t["key"]: t for t in PPT_THEMES}


def _alias_map():
    m = {}
    for t in PPT_THEMES:
        m[t["key"].lower()] = t["key"]
        m[t["name"].lower()] = t["key"]
        for a in t["aliases"]:
            m[a.lower()] = t["key"]
    return m


_ALIASES = _alias_map()


def theme_names():
    """返回 [(key, 中文名)]，供提示词 / 帮助文本使用。"""
    return [(t["key"], t["name"]) for t in PPT_THEMES]


def _fill_defaults(theme):
    """用默认主题补齐缺失字段，保证渲染层拿到的 dict 一定完整。"""
    base = _BY_KEY[DEFAULT_THEME_KEY]
    out = dict(base)
    out.update({k: v for k, v in (theme or {}).items() if v is not None})
    size = dict(base["size"])
    size.update(theme.get("size") or {})
    out["size"] = size
    cc = theme.get("chart_colors") or base["chart_colors"]
    out["chart_colors"] = list(cc)
    return out


def get_theme(name=None):
    """按 key / 中文名 / 别名 / 子串解析主题；无法识别时返回默认主题。

    渲染永远不应该因为「主题名写错」而失败，所以这里是宽松匹配。
    """
    if not name:
        return _fill_defaults(_BY_KEY[DEFAULT_THEME_KEY])
    if isinstance(name, dict):
        return _fill_defaults(name)
    raw = str(name).strip().lower()
    if not raw:
        return _fill_defaults(_BY_KEY[DEFAULT_THEME_KEY])
    key = _ALIASES.get(raw)
    if key:
        return _fill_defaults(_BY_KEY[key])
    # 子串兜底："深色科技风" / "academic-blue" 之类
    for alias, k in _ALIASES.items():
        if alias and alias in raw:
            return _fill_defaults(_BY_KEY[k])
    for k, t in _BY_KEY.items():
        if k in raw or t["name"] in raw:
            return _fill_defaults(t)
    return _fill_defaults(_BY_KEY[DEFAULT_THEME_KEY])


def list_themes_text():
    """一行一个主题的中文描述，给用户/模型看的。"""
    lines = []
    for t in PPT_THEMES:
        lines.append(f"- {t['name']}（{t['key']}，主色 #{t['primary']}）")
    return "\n".join(lines)
