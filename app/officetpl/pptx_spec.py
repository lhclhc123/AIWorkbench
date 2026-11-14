# -*- coding: utf-8 -*-
"""PowerPoint 模板规格（16:9）。

只描述「长什么样」，具体怎么落地在 ``app/docwrite.py`` 的 ``write_pptx``。
和 ``docx_spec.py`` / ``xlsx_spec.py`` 一样，这里是一个**纯数据**模块：
渲染层只读常量，不往这里写状态，改版式只需要改这一个文件。

分层关系
========
``themes.py``   —— 配色 + 每套主题的字号（「配色家族」）
``pptx_spec.py``—— 画布/留白/栅格/间距/版式清单 + 「成品模板预设」（「版式规格」）
``docwrite.py`` —— 只用不定义（「渲染」）

尺寸口径（英寸）
================
画布 13.333 × 7.5（16:9）。历史常量 ``SLIDE_W/SLIDE_H/MARGIN_X/...`` 全部收敛到
``PPTX_SPEC["slide"]`` 与 ``PPTX_SPEC["margin"]``，**数值一字不改**——改这里等于改版式。
"""
from .themes import PPT_THEMES, DEFAULT_THEME_KEY

# ------------------------------------------------------------------ 画布
# 注意：这三个基础值刻意写成「原始字面量」，下面的派生量一律由它们算出来，
# 这样 ``content_w`` 与历史常量 ``SLIDE_W - 2 * MARGIN_X`` 的浮点值完全一致
# （13.333 - 1.7 == 11.633000000000001），渲染出来像素级等价。
_SLIDE_W = 13.333
_SLIDE_H = 7.5
_MARGIN_LEFT = 0.85

# 各版式用到的字号键。**必须与 themes.PPT_THEMES[*]["size"] 一一对应**，
# 缺键时 ``_size_table()`` 会补上默认主题的值（不会崩），但新增主题请同步补齐。
SIZE_KEYS = (
    "cover_title",      # 封面主标题
    "cover_sub",        # 封面副标题
    "cover_meta",       # 封面演讲人 / 日期
    "section_num",      # 章节过渡页的大号章节数字
    "section_title",    # 章节过渡页的章节名
    "slide_title",      # 内容页标题
    "body",             # 正文一级要点
    "body2",            # 正文二级要点（备用，渲染层用 body-step 推导）
    "table",            # 表格正文 / 图表坐标轴
    "table_head",       # 表格表头
    "footer",           # 页脚
    "toc",              # 目录条目
    "end_title",        # 结尾页「谢谢」
)


def _size_table():
    """从主题的 ``size`` 里推导出每个字号键的取值范围。

    ``default`` 取默认主题（academic）的值，也就是「不指定模板时」实际用的字号；
    ``min`` / ``max`` 是四套主题里的最小值 / 最大值，方便做自动缩字的下界约束
    和「这个键还能不能再压」的校验。
    """
    default_size = {}
    for t in PPT_THEMES:
        if t.get("key") == DEFAULT_THEME_KEY:
            default_size = t.get("size") or {}
            break
    if not default_size and PPT_THEMES:
        default_size = PPT_THEMES[0].get("size") or {}

    out = {}
    for key in SIZE_KEYS:
        vals = [float(t["size"][key]) for t in PPT_THEMES
                if key in (t.get("size") or {})]
        base = float(default_size.get(key, vals[0] if vals else 20.0))
        out[key] = {
            "default": base,
            "min": min(vals) if vals else base,
            "max": max(vals) if vals else base,
            "unit": "pt",
        }
    return out


PPTX_SPEC = {
    # -------------------------------------------------------------- 画布
    "slide": {
        "width_in": _SLIDE_W,
        "height_in": _SLIDE_H,
        "ratio": "16:9",
    },
    # -------------------------------------------------------------- 留白
    # left/right 就是历史常量 MARGIN_X，title_y 就是 TITLE_Y，body_top 就是 BODY_TOP…
    "margin": {
        "left": _MARGIN_LEFT,
        "right": _MARGIN_LEFT,
        "top": 0.55,            # = title_y，标题区顶
        "bottom": 0.78,         # = slide.height - body_bottom
        "content_w": _SLIDE_W - 2 * _MARGIN_LEFT,   # = MARGIN 后的正文宽度
        "title_y": 0.55,
        "title_h": 0.95,
        "body_top": 1.92,
        "body_bottom": 6.72,
        "footer_y": 6.92,
        "footer_h": 0.30,
    },
    # -------------------------------------------------------------- 栅格
    "grid": {
        # 要点页：超过 N 条自动分两栏
        "bullets": {
            "two_col_over": 8,
            "gap": 0.55,
            "width_slack": 0.6,        # 单栏时留给自动缩字的宽度余量
            "col_width_slack": 0.5,
        },
        # 两栏图文：左文右图
        "two_col": {
            "left_w": 6.15,
            "gap": 0.55,
            "body_offset_y": 0.10,
            "visual_offset_y": 0.15,
            "visual_bottom_pad": 0.10,
        },
        # 目录
        "toc": {
            "col_gap": 0.60,
            "num_w": 0.85,
            "row_h_max": 0.78,
            "two_col_over": 6,      # 条目数 > 6 就分两栏
            "top_offset": 0.15,
            "bottom_pad": 0.10,
            "row_inner_pad": 0.12,  # 行内文字框高度 = row_h - 该值
            "row_bottom_gap": 0.13, # 分隔线相对行底的偏移
        },
        # 表格
        "table": {
            "row_h_max": 0.46,
            "min_col_weight": 3,
            "cell_margin_x": 0.10,
            "cell_margin_y": 0.03,
            "body_start_offset": 0.15,  # 表格起点 = BODY_TOP + 该值
            "sub_offset_y": -0.06,
            "sub_h": 0.40,
            "sub_advance_y": 0.34,
        },
        # 代码块
        "code": {
            "inner_pad_x": 0.22,
            "inner_pad_y": 0.16,
            "box_h_inset": 0.30,        # 文字框高 = h - 该值（= 2*inner_pad_y - 0.02）
            "max_lines": 18,
            "size_max": 13.0,
            "size_min": 9.0,
        },
        # 图片页
        "image": {
            "top_offset": 0.10,
            "caption_h": 0.45,
            "caption_gap": 0.06,
            "no_caption_pad": 0.05,
        },
        # 空图时的「图示卡片」
        "visual_card": {
            "caption_ratio": 0.70,      # 文案区 y 偏移 = h * 该值
            "caption_h_ratio": 0.24,
            "caption_pad": 0.20,
            "baseline_ratio": 0.62,     # 柱状图基线位置 = h * 该值
            "bar_heights": (0.35, 0.62, 0.48, 0.80, 0.55),
            "bar_width_ratio": 0.075,
            "bar_start_ratio": 0.22,
            "bar_step_ratio": 0.13,
            "bar_height_ratio": 0.55,   # 柱高 = h * bar_heights[i] * 该值
            "rule_x_ratio": 0.14,
            "rule_w_ratio": 0.72,
            "rule_h": 0.012,
        },
    },
    # -------------------------------------------------------------- 字号
    # 由 themes.py 的 size 键集推导（见 _size_table），保证一一对应。
    "size": _size_table(),
    # -------------------------------------------------------------- 间距
    "spacing": {
        "line": 1.28,               # 正文行距
        "line_tight": 1.12,         # 封面大标题 / 代码块行距
        "space_after": 10.0,        # 要点段后（pt）
        "space_after_plain": 4.0,   # 纯文字段落段后（pt）
        "level_font_step": 3.5,     # 每深一级压多少号
        "level_font_min": 13.0,     # 二级要点最小字号
        "fit_ratio": 0.97,          # 自动缩字时允许占用的宽度比例
        "fit_min": 14.0,            # 内容页标题最小字号
        "title_fit_pad": 0.4,       # 标题可用宽度 = 标题框宽 - 该值
        "cover_fit_min": 26.0,      # 封面标题最小字号
        "cover_two_line_min": 28.0, # 封面标题折行后的下限
        "bullet": {
            "marl": 0.42,           # 项目符号左侧缩进
            "hanging": 0.28,        # 悬挂缩进
            "level_step": 0.40,     # 每级递进缩进
            "font": "Arial",        # 项目符号字形字体（圆点/三角/菱形都在 Arial 里）
        },
        # 标题装饰条（三种 title_style 的几何）
        "title_rule": {
            "dot": {"dot_x": -0.30, "dot_y": 0.36, "dot": 0.11,
                    "line_y": 0.06, "line_h": 0.014},
            "rule": {"bar_h": 0.02, "bar_y": 0.02,
                     "accent_ratio": 0.32, "accent_h": 0.008, "accent_y": 0.10},
            "bar": {"bar_x": -0.26, "bar_y": 0.22, "bar_w": 0.09, "bar_h": 0.52,
                    "line_y": 0.06, "line_h": 0.02, "accent_w": 1.1},
        },
        "footer": {"name_ratio": 0.6, "page_w": 1.6, "page_size_add": 1.0},
        "toc": {"num_size_sub": 2.0, "size_min": 11.0, "num_w": 0.85,
                "text_gap": 0.15, "rule_h": 0.008},
    },
    # -------------------------------------------------------------- 封面
    "cover": {
        "title_w": 10.4,
        "title_y": 2.35,
        "title_h": 1.90,
        "sub_offset_y": 1.55,       # 副标题相对标题 y 的偏移
        "sub_w_ratio": 0.92,
        "sub_h": 0.90,
        "kicker_text": "P R E S E N T A T I O N",
        "kicker_size": 10.0,
        "kicker_y": {"bar": 1.55, "frame": 1.50},
        "meta_h": 0.50,
        "meta_y": {"band": 6.35, "bar": 5.05, "glow": 5.55, "frame": 5.35},
        # 各 cover_style 的装饰几何
        "deco": {
            "bar": {"rail_w": 0.42, "rail_accent_h": 2.1,
                    "oval_x": 8.6, "oval_y": 4.0, "oval": 5.2, "title_x": 1.45},
            "glow": {"oval_x": 9.4, "oval_y": -1.6, "oval": 5.6,
                     "oval2_x": 10.6, "oval2_y": 3.6, "oval2": 4.6,
                     "rule_x": 0.95, "rule_y": 1.35, "rule_w": 0.9, "rule_h": 0.02,
                     "bottom_line_y": 6.2, "title_x": 1.0, "title_y": 2.45},
            "frame": {"inset1": 0.42, "inset2": 0.52,
                      "seal_x": 10.75, "seal_y": 4.55, "seal": 0.62,
                      "title_x": 1.35, "title_w": 10.2},
            "band": {"band_y": 5.72, "band_h": 1.78, "accent_h": 0.05,
                     "rule_x": 0.85, "rule_y": 1.05, "rule_w": 1.4, "rule_h": 0.05,
                     "title_x": 0.85, "title_y": 2.15},
        },
    },
    # -------------------------------------------------------------- 章节页
    "section": {
        # 非居中的三种样式（glow / panel）共用
        "num_box_w": 3.2,
        "num_box_h": 1.85,
        "num_box_h_center": 1.9,    # 居中样式（frame）的序号框高
        "rule_y_offset": 1.95,      # 数字下方装饰线（相对 ny）
        "rule_w": 1.5,
        "rule_h": 0.03,
        "title_y": 2.55,
        "title_h": 1.50,
        "sub_y": 4.05,
        "sub_h": 1.0,
        "title_min_size": 20.0,
        "sub_size": 14.0,
        # 国风（居中）变体
        "frame_title_sub": 2.0,     # 章节名字号减 2
        "frame_title_x": 0.9,
        "frame_title_inset": 1.8,
        "frame_title_y": 3.55,
        "frame_title_h": 0.9,
        "frame_rule_off": 1.2,
        "frame_rule_y": 4.75,
        "frame_rule_w": 2.4,
        "frame_rule_h": 0.02,
        "frame_sub_inset": 1.4,
        "frame_sub_y": 5.0,
        "frame_sub_h": 0.6,
        # 各样式的位置取向
        "deco": {
            "glow": {"top_h": 0.06, "oval_x": 8.9, "oval_y": 1.0, "oval": 4.4,
                     "num_x_add": 0.10, "num_y": 1.25, "title_x_add": 0.15},
            "frame": {"border_inset": 0.50, "num_x_off": 1.40, "num_y": 1.55},
            "panel": {"panel_w": 4.9, "strip_w": 0.07,
                      "num_x": 0.55, "num_y": 1.70, "title_x": 5.60},
        },
    },
    # -------------------------------------------------------------- 结尾页
    "end": {
        "title_y": 2.45,
        "title_h": 1.35,
        "rule_y": 3.95,
        "rule_w": 1.8,
        "rule_h": 0.03,
        "sub_y": 4.2,
        "sub_w_pad": 1.2,
        "sub_h": 0.7,
        "sub_size": 16.0,
        # 各 cover_style 的装饰几何
        "deco": {
            "glow": {"oval_x": 8.6, "oval_y": 0.4, "oval": 5.2, "top_h": 0.06},
            "frame": {"inset": 0.42, "line_w": 0.9},
            "band": {"band_y": 5.9, "band_h": 1.6},
            "panel": {"rail_w": 0.42},
        },
    },
    # -------------------------------------------------------------- 小字标注
    # 这些是「辅助文案」的固定字号，不随主题变（主题里没有对应键，也不想为它们
    # 给每套主题都加一项）。
    "text": {
        "note_size": 12.0,          # 表格页副标题
        "chart_note_size": 11.0,    # 图表页「数据来源」
        "caption_size": 11.0,       # 图片图注
        "card_caption_size": 12.0,  # 图示卡片文案
    },
    # -------------------------------------------------------------- 版式清单
    # 供「模板选择 / 未来加版式」用：key 必须与 _build_plan 产出的 kind 名一致。
    "layout": {
        "cover": "封面：演示标题 + 副标题 + 演讲人/日期",
        "toc": "目录：章节清单，>6 条自动分两栏",
        "section": "章节过渡：大号序号 + 章节名",
        "bullets": "要点页：项目符号列表，>8 条自动两栏，自动缩字号防溢出",
        "two_col": "两栏图文：左文右图/右图表",
        "table": "表格页：主题色表头 + 斑马纹 + 自动列宽",
        "chart": "图表页：python-pptx 原生图表（柱/折线/饼/面积）",
        "image": "图片页：等比缩放居中 + 图注",
        "code": "代码页：等宽字体 + 底纹卡片",
        "end": "结尾页：谢谢 / 欢迎交流",
    },
}


# ==================================================================== 成品模板
# 「主题（配色+字号）」是底层；「模板预设」是给用户挑的成品组合：
# 一套模板 = 一个主题 + 若干版式取向。以后加模板只要往这里追加一项。
PPT_TEMPLATE_PRESETS = [
    {
        "key": "report_blue",
        "name": "汇报蓝",
        "aliases": ["汇报蓝", "汇报", "蓝", "工作汇报", "述职", "report",
                    "report_blue", "blue", "深蓝"],
        "theme": "academic",
        "cover_align": "left",
        "toc_style": "numbered",
        "chart_style": "clean",
        "accent_block": True,
        "description": "白底深蓝主色 + 青色点缀，最稳的工作汇报/述职/方案评审",
    },
    {
        "key": "tech_dark",
        "name": "科技深色",
        "aliases": ["科技深色", "科技", "深色", "暗色", "科技感", "tech",
                    "tech_dark", "dark", "发布"],
        "theme": "dark_tech",
        "cover_align": "left",
        "toc_style": "numbered",
        "chart_style": "glow",
        "accent_block": True,
        "description": "深色底 + 青色高亮 + 光斑封面，技术方案/产品发布/AI 主题",
    },
    {
        "key": "guofeng_red",
        "name": "国风红",
        "aliases": ["国风红", "国风", "中国风", "传统", "古典", "红",
                    "guofeng", "guofeng_red", "chinese"],
        "theme": "guofeng",
        "cover_align": "center",
        "toc_style": "numbered",
        "chart_style": "warm",
        "accent_block": False,
        "description": "宣纸底 + 朱红主色 + 描金点缀，文化/政务/品牌类题材",
    },
    {
        "key": "minimal_gray",
        "name": "极简灰",
        "aliases": ["极简灰", "极简", "灰", "简约", "商务", "minimal",
                    "minimal_gray", "gray", "grey", "business"],
        "theme": "business",
        "cover_align": "left",
        "toc_style": "plain",
        "chart_style": "clean",
        "accent_block": False,
        "description": "蓝灰低饱和 + 无序号目录，信息密度高的评审/数据汇报",
    },
]

DEFAULT_PRESET_KEY = PPT_TEMPLATE_PRESETS[0]["key"]

_PRESET_BY_KEY = {p["key"]: p for p in PPT_TEMPLATE_PRESETS}


def _alias_map():
    """模板 key / 中文名 / 别名 → 模板 key。"""
    m = {}
    for p in PPT_TEMPLATE_PRESETS:
        m[p["key"].lower()] = p["key"]
        m[p["name"].lower()] = p["key"]
        for a in p.get("aliases") or ():
            m[str(a).lower()] = p["key"]
    return m


_PRESET_ALIASES = _alias_map()


def _fill_preset(preset):
    """用第一个预设补齐缺失字段，保证调用方拿到的 dict 一定完整。"""
    base = PPT_TEMPLATE_PRESETS[0]
    out = dict(base)
    out.update({k: v for k, v in (preset or {}).items() if v is not None})
    return out


def get_preset(key=None):
    """按 key / 中文名 / 别名 / 子串解析成品模板；无法识别时回落到第一个。

    和 ``get_theme`` 一样是宽松匹配：模板名写错绝不让渲染失败。
    """
    if not key:
        return _fill_preset(PPT_TEMPLATE_PRESETS[0])
    if isinstance(key, dict):
        return _fill_preset(key)
    raw = str(key).strip().lower()
    if not raw:
        return _fill_preset(PPT_TEMPLATE_PRESETS[0])
    found = _PRESET_ALIASES.get(raw)
    if found:
        return _fill_preset(_PRESET_BY_KEY[found])
    for alias, k in _PRESET_ALIASES.items():
        if alias and alias in raw:
            return _fill_preset(_PRESET_BY_KEY[k])
    for k, p in _PRESET_BY_KEY.items():
        if k in raw or p["name"] in raw:
            return _fill_preset(p)
    return _fill_preset(PPT_TEMPLATE_PRESETS[0])


def preset_names():
    """返回 [(key, 中文名)]，供设置界面使用。"""
    return [(p["key"], p["name"]) for p in PPT_TEMPLATE_PRESETS]


def list_presets_text():
    """一行一个模板的中文描述，给用户 / 模型看的。"""
    lines = []
    for p in PPT_TEMPLATE_PRESETS:
        lines.append(f"- {p['name']}（{p['key']}）：{p['description']}")
    return "\n".join(lines)


def template_keys_text():
    """紧凑的一行模板清单，适合塞进工具描述。"""
    return " / ".join(f"{p['name']}（{p['key']}）" for p in PPT_TEMPLATE_PRESETS)
