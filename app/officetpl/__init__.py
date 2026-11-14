# -*- coding: utf-8 -*-
"""``app.officetpl`` — 文档外观模板/主题资源包。

只放「设计常量 + 度量工具」，不放渲染逻辑（渲染在 ``app/docwrite.py``）。
这样加一套主题 / 改一次配色，不用碰几百行的生成函数。
"""
from .themes import (  # noqa: F401
    PPT_THEMES,
    PPT_FONT,
    PPT_FONT_CJK,
    PPT_FONT_MONO,
    DEFAULT_THEME_KEY,
    get_theme,
    theme_names,
    list_themes_text,
)
from .docx_spec import DOCX_SPEC  # noqa: F401
from .xlsx_spec import (  # noqa: F401
    XLSX_SPEC,
    PALETTES,
    NUMERIC_RATIO,
    MIN_ROWS_FOR_STATS,
    get_palette,
    match_keyword,
    is_score_column,
)
from .textfit import (  # noqa: F401
    is_cjk,
    char_em,
    text_em,
    wrap_lines,
    estimate_height_in,
    fit_font_size,
    split_pages,
)

__all__ = [
    "PPT_THEMES", "PPT_FONT", "PPT_FONT_CJK", "PPT_FONT_MONO",
    "DEFAULT_THEME_KEY", "get_theme", "theme_names", "list_themes_text",
    "DOCX_SPEC",
    "XLSX_SPEC", "PALETTES", "NUMERIC_RATIO", "MIN_ROWS_FOR_STATS",
    "get_palette", "match_keyword", "is_score_column",
    "is_cjk", "char_em", "text_em", "wrap_lines", "estimate_height_in",
    "fit_font_size", "split_pages",
]
