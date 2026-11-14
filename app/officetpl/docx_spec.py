# -*- coding: utf-8 -*-
"""Word 文档模板规格。

这里只描述「长什么样」，具体怎么落地在 ``app/docwrite.py`` 的 ``write_docx``。
数值都按中文公文 / 调研报告的常见排版惯例取：
- 正文宋体 10.5pt（五号）、1.5 倍行距、首行缩进 2 字符、两端对齐；
- 标题微软雅黑加粗，一级 18pt / 二级 15pt / 三级 13pt；
- A4，上下 2.5cm 左右 2.6cm。
"""

DOCX_SPEC = {
    "page": {
        "width_mm": 210.0,
        "height_mm": 297.0,
        "margin_mm": {"top": 25.0, "bottom": 23.0, "left": 26.0, "right": 24.0},
        "header_mm": 15.0,
        "footer_mm": 15.0,
    },
    "font": {
        "body": "宋体",
        "heading": "微软雅黑",
        "cover": "微软雅黑",
        "latin": "Times New Roman",
        "code": "Consolas",
    },
    "size": {
        "body": 10.5,
        "h1": 18.0,
        "h2": 15.0,
        "h3": 13.0,
        "cover_title": 30.0,
        "cover_sub": 14.0,
        "cover_meta": 11.0,
        "caption": 9.0,
        "table": 9.5,
        "table_head": 9.5,
        "toc_title": 16.0,
        "toc_entry": 10.5,
        "footer": 9.0,
        "quote": 10.0,
        "code": 9.0,
    },
    "spacing": {
        "line": 1.5,                 # 正文 1.5 倍行距
        "heading_line": 1.25,
        "first_line_chars": 200,     # 200 = 2.00 字符（OOXML 单位是 1/100 字符）
        "h1_before": 18.0,
        "h1_after": 12.0,
        "h2_before": 14.0,
        "h2_after": 10.0,
        "h3_before": 10.0,
        "h3_after": 8.0,
        "body_after": 6.0,
    },
    "color": {
        "h1": "1B4B7A",
        "h2": "1F4E79",
        "h3": "24303F",
        "cover_title": "123252",
        "accent": "2A9D8F",
        "muted": "6B7A8C",
        "rule": "1B4B7A",
        "table_head_fill": "1B4B7A",
        "table_head_text": "FFFFFF",
        "table_band_fill": "F2F6FB",
        "table_border": "B7C6D6",
        "caption": "44474D",
        "code_fill": "F5F6F8",
    },
    # 封面：封面正文之间的元素顺序与留白（英寸）
    "cover": {
        "top_space": 1.55,
        "title_space_after": 10.0,
        "sub_space_after": 18.0,
        "rule_width_pt": 1.6,
        "meta_space_before": 26.0,
        "bottom_note": "AIWorkbench 自动生成",
    },
    "toc": {"title": "目　录", "levels": "1-3", "placeholder": "【目录占位】按 Ctrl+A 全选后按 F9 更新域，或右键→更新域"},
}
