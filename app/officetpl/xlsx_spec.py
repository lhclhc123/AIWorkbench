# -*- coding: utf-8 -*-
"""Excel 表格模板规格：配色 + 语义关键词。

配色跟 PPT 主题同一套色系（key 对齐），这样一次「主题=business」出来的一整套
文档（PPT/Excel/Word）看起来是一家人。
"""

XLSX_SPEC = {
    "font": "微软雅黑",
    "title_size": 15.0,
    "head_size": 10.5,
    "cell_size": 10.5,
    "stat_size": 10.0,
    "title_height": 34.0,
    "head_height": 26.0,
    "row_height": 20.0,
    "max_col_width": 42.0,
    "min_col_width": 8.0,
    "index_sheet_name": "目录",
}

# 数值列判定：>= 该比例的非空单元格是数字，就当成数值列
NUMERIC_RATIO = 0.75

# 统计行：少于这么多数据行就不加（避免小表被统计行污染）
MIN_ROWS_FOR_STATS = 6

# 语义关键词：列名命中这些词时，可以自动生成「真公式」
FORMULA_KEYWORDS = {
    "total": ("总分", "合计", "总计", "小计", "总计分"),
    "avg": ("平均分", "平均", "均值", "均分"),
    "rank": ("排名", "名次", "排行"),
    "pass": ("及格率", "通过率", "合格率"),
    "grade": ("等级", "评级", "档次"),
    "rate": ("率", "占比", "百分比", "比例"),
}

# 这些列名视为「分数列」，会挂上「<60 标红」的条件格式
SCORE_KEYWORDS = (
    "语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理",
    "总分", "成绩", "分数", "得分", "月考", "期中", "期末", "score", "score1",
)

PALETTES = {
    "default": {
        "primary": "1B4B7A",
        "primary_dark": "123252",
        "accent": "2A9D8F",
        "title_bg": "123252",
        "title_text": "FFFFFF",
        "head_bg": "1B4B7A",
        "head_text": "FFFFFF",
        "band": "F2F6FB",
        "border": "BFC9D4",
        "stat_bg": "E8EEF6",
        "stat_text": "1B4B7A",
        "fail_bg": "FFD9D6",
        "fail_text": "B00020",
        "muted": "6B7A8C",
        "ok_bg": "DFF3E4",
    },
    "academic": {
        "primary": "1B4B7A",
        "primary_dark": "123252",
        "accent": "2A9D8F",
        "title_bg": "123252",
        "title_text": "FFFFFF",
        "head_bg": "1B4B7A",
        "head_text": "FFFFFF",
        "band": "F2F6FB",
        "border": "BFC9D4",
        "stat_bg": "E8EEF6",
        "stat_text": "1B4B7A",
        "fail_bg": "FFD9D6",
        "fail_text": "B00020",
        "muted": "6B7A8C",
        "ok_bg": "DFF3E4",
    },
    "dark_tech": {
        "primary": "0E7490",
        "primary_dark": "0B1220",
        "accent": "22D3EE",
        "title_bg": "0B1220",
        "title_text": "E8EFFA",
        "head_bg": "0E7490",
        "head_text": "FFFFFF",
        "band": "ECF6F8",
        "border": "B9CDD4",
        "stat_bg": "DDF1F5",
        "stat_text": "0B4E60",
        "fail_bg": "FFD9D6",
        "fail_text": "B00020",
        "muted": "6B7A8C",
        "ok_bg": "DFF3E4",
    },
    "guofeng": {
        "primary": "9E3B2F",
        "primary_dark": "6E2A22",
        "accent": "C9A227",
        "title_bg": "6E2A22",
        "title_text": "FDF6EA",
        "head_bg": "9E3B2F",
        "head_text": "FDF6EA",
        "band": "FBF3E6",
        "border": "D3BFA4",
        "stat_bg": "F3E7D3",
        "stat_text": "6E2A22",
        "fail_bg": "F8D7D0",
        "fail_text": "9E2A1F",
        "muted": "6B7A8C",
        "ok_bg": "E7EFDF",
    },
    "business": {
        "primary": "1F3A5F",
        "primary_dark": "16293F",
        "accent": "B08A3E",
        "title_bg": "16293F",
        "title_text": "FFFFFF",
        "head_bg": "1F3A5F",
        "head_text": "FFFFFF",
        "band": "F1F3F6",
        "border": "C4CBD4",
        "stat_bg": "E6EAF0",
        "stat_text": "1F3A5F",
        "fail_bg": "FFD9D6",
        "fail_text": "B00020",
        "muted": "6B7A8C",
        "ok_bg": "DFF3E4",
    },
}


def get_palette(theme_key=None):
    """按主题 key 取 Excel 配色；未知名回落到默认。"""
    key = (theme_key or "").strip().lower()
    return dict(PALETTES.get(key) or PALETTES["default"])


def match_keyword(header, group):
    """列名是否命中某个语义组，命中返回该组第一个命中的词。"""
    names = FORMULA_KEYWORDS.get(group) or ()
    h = (header or "").replace(" ", "").lower()
    for n in names:
        if n.lower() in h:
            return n
    return None


def is_score_column(header):
    h = (header or "").replace(" ", "").lower()
    return any(k.lower() in h for k in SCORE_KEYWORDS)
