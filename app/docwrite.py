# -*- coding: utf-8 -*-
"""文档生成：把 Markdown 写成 Word(.docx) / Excel(.xlsx) / PPT(.pptx) / PDF(.pdf)。

设计要点
========
1. 只依赖一个「极简 Markdown 解析器」，不做完整 CommonMark，够用即可；
2. 每种格式各自落地，任何一段失败都降级为纯文本，绝不让整篇生成失败；
3. 中文字体显式指定（``Microsoft YaHei`` / ``微软雅黑``），避免默认 Calibri 中文变宋体发虚；
4. **版面是设计过的**，不是「标题 + 一堆字」：
   - PPT：主题化版式（封面 / 目录 / 章节过渡 / 内容 / 两栏图文 / 表格 / 原生图表 / 结尾），
     自动分页、自动缩字号防溢出、页脚页码；
   - Excel：表头样式、冻结首行、自动筛选、真公式、条件格式（不及格标红 / 色阶 / 数据条）、
     统计行（平均 / 最高 / 最低 / 及格率）、多 sheet 时的目录页；
   - Word：封面页、TOC 域（打开后按 F9 可更新）、三级标题自动编号、正文首行缩进 2 字符、
     1.5 倍行距、表格表头底纹、图表题注、页脚页码。
5. 主题 / 配色 / 字号全部放在 ``app/officetpl/``，本文件只管渲染。

对外接口（保持向后兼容，``app/agent.py`` 只用到 ``create(path, content, title=, base_dir=)``）
=========================================================================================
- ``create(path, content, title=None, base_dir=None, theme=None, author=None,
          subtitle=None, date=None, template=None) -> (label, size)``
- ``write_docx / write_xlsx / write_pptx / write_pdf(path, blocks, title=None, base_dir=None, ...)``
  （``template`` 只对 .pptx 有意义：汇报蓝 / 科技深色 / 国风红 / 极简灰）
- ``supported(path)`` / ``format_label(path)`` / ``parse_blocks(text)``

Markdown 约定（都是可选的「加分项」，不写也能出好看的文档）
=========================================================
- ``# `` 一级标题：PPT 里当「演示标题」或「章节」；Word 里当「章节」。
- ``## `` 二级标题：PPT 里当「单页标题」；Word 里当「二级标题」。
- ``### `` 三级标题：PPT 里降级为「二级要点」；Word 里当「三级标题」。
- ``- `` / ``1. `` 列表：PPT 要点（超过 6 条自动分页），Word 项目符号列表。
- ``| a | b |`` 表格：PPT 单独成页并套主题配色；Excel 一律按列建表。
- ```` ```chart bar ```` 代码块，内容形如 ``标签,数值`` 每行一条 → PPT 原生柱状图。
- 表格里以 ``=`` 开头的单元格按**真公式**写入 Excel，支持 ``{r}`` 占位当前行号：
  ``=SUM(B{r}:D{r})``、``=RANK(E{r},$E$2:$E$41)``。

Excel 列名语义（命中即自动生成真公式，无需模型写公式）
=====================================================
``总分/合计`` → ``=SUM(B{r}:D{r})``；``平均分`` → ``=AVERAGE``；``排名`` → ``=RANK``；
``及格率`` → ``=COUNTIF(...,">=60")/COUNT(...)``；``等级`` → ``=IF(...)`` 四档评级。
"""
import os
import re

try:  # 正常情况：app.docwrite 作为 app 包的子模块被导入
    from .officetpl import (
        PPT_FONT, PPT_FONT_CJK, PPT_FONT_MONO,
        DOCX_SPEC, XLSX_SPEC, PPTX_SPEC, NUMERIC_RATIO, MIN_ROWS_FOR_STATS,
        get_theme, get_palette, get_preset, is_score_column, match_keyword,
        fit_font_size, estimate_height_in, wrap_lines, text_em, split_pages,
    )
except ImportError:  # pragma: no cover - 直接以脚本方式加载时兜底
    from officetpl import (  # type: ignore
        PPT_FONT, PPT_FONT_CJK, PPT_FONT_MONO,
        DOCX_SPEC, XLSX_SPEC, PPTX_SPEC, NUMERIC_RATIO, MIN_ROWS_FOR_STATS,
        get_theme, get_palette, get_preset, is_score_column, match_keyword,
        fit_font_size, estimate_height_in, wrap_lines, text_em, split_pages,
    )


# ================================================================ Markdown 解析

_IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]+)\s*\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITAL_RE = re.compile(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)|(?<![\w_])_([^_\n]+)_(?!_)")
_STRIKE_RE = re.compile(r"~~([^~]+)~~")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

# 看起来像「章节」的标题（用于自动插章节过渡页）
_CHAPTER_RE = re.compile(
    r"^\s*(第\s*[一二三四五六七八九十百零\d]+\s*[章节部分篇讲]"
    r"|[一二三四五六七八九十]+\s*[、.．]"
    r"|\d+\s*[、.．]\s*\S)")


def inline_plain(s):
    """把行内标记抹平，得到纯文本（Word 标题、Excel 单元格、PPT 要点都要用）。"""
    if not s:
        return ""
    s = _IMG_RE.sub(lambda m: m.group(1) or "", s)
    s = _LINK_RE.sub(lambda m: (m.group(1) or m.group(2)), s)
    s = _CODE_RE.sub(r"\1", s)
    s = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _STRIKE_RE.sub(r"\1", s)
    s = _ITAL_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    return s.strip()


def inline_runs(s):
    """把一行拆成 [(text, bold, italic, code)] 片段，供 Word 做行内加粗。"""
    if not s:
        return []
    runs = []
    pos = 0
    pattern = re.compile(
        r"(\*\*[^*]+\*\*)|(__[^_]+__)|(`[^`]+`)|(~~[^~]+~~)|(\*[^*\n]+\*)|(_[^_\n]+_)")
    for m in pattern.finditer(s):
        if m.start() > pos:
            runs.append((_strip_links(s[pos:m.start()]), False, False, False))
        tok = m.group(0)
        if tok.startswith("**") or tok.startswith("__"):
            runs.append((tok[2:-2], True, False, False))
        elif tok.startswith("~~"):
            runs.append((tok[2:-2], False, False, False))
        elif tok.startswith("`"):
            runs.append((tok[1:-1], False, False, True))
        else:
            runs.append((tok[1:-1], False, True, False))
        pos = m.end()
    if pos < len(s):
        runs.append((_strip_links(s[pos:]), False, False, False))
    out = [r for r in runs if r[0]]
    return out or [(inline_plain(s), False, False, False)]


def _strip_links(s):
    return _LINK_RE.sub(lambda m: (m.group(1) or m.group(2)), s or "")


def _split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_blocks(text):
    """把 Markdown 文本切成结构块列表。

    每个块是 dict，kind ∈ {h, p, li, table, code, chart, img, quote, hr}
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()

        # 代码围栏（chart 围栏单独识别成图表块）
        m = re.match(r"^\s*```+\s*([A-Za-z0-9_+.-]*)\s*([A-Za-z0-9_+-]*)\s*$", line)
        if m:
            lang = (m.group(1) or "").strip()
            extra = (m.group(2) or "").strip()
            i += 1
            buf = []
            while i < n and not re.match(r"^\s*```+\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            body = "\n".join(buf)
            if lang.lower().startswith("chart"):
                ctype = "bar"
                if ":" in lang:
                    ctype = lang.split(":", 1)[1].strip().lower() or "bar"
                elif extra:
                    ctype = extra.lower()
                labels, values, sname = _parse_chart_pairs(body)
                if len(labels) >= 2:
                    blocks.append({"kind": "chart", "type": ctype,
                                   "labels": labels, "values": values,
                                   "series": sname, "text": body})
                else:
                    blocks.append({"kind": "code", "lang": "text", "text": body})
            else:
                blocks.append({"kind": "code", "lang": lang, "text": body})
            continue

        if not line.strip():
            i += 1
            continue

        if _HR_RE.match(line):
            blocks.append({"kind": "hr"})
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            blocks.append({"kind": "h", "level": len(m.group(1)),
                           "text": m.group(2).strip()})
            i += 1
            continue

        # 表格：当前行是 |...|，下一行是分隔行
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]) \
                and "|" in lines[i + 1]:
            header = _split_row(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            blocks.append({"kind": "table", "header": header, "rows": rows})
            continue

        # 引用
        if line.lstrip().startswith(">"):
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            blocks.append({"kind": "quote", "text": " ".join(x for x in buf if x)})
            continue

        # 单行成段的图片
        m = _IMG_RE.match(line.strip())
        if m and _IMG_RE.sub("", line).strip() == "":
            blocks.append({"kind": "img", "src": m.group(2), "alt": m.group(1)})
            i += 1
            continue

        # 列表
        m = _UL_RE.match(line) or _OL_RE.match(line)
        if m:
            ordered = bool(_OL_RE.match(line))
            indent = len(m.group(1).replace("\t", "    "))
            text_ = m.group(3)
            blocks.append({"kind": "li", "text": text_.strip(),
                           "level": min(indent // 2, 4), "ordered": ordered})
            i += 1
            continue

        # 普通段落：连续非空行合并
        buf = [line.strip()]
        i += 1
        while i < n and lines[i].strip() and not re.match(
                r"^\s*(#{1,6}\s|```|>|\||[-*+]\s|\d+[.)]\s)", lines[i]) \
                and not _HR_RE.match(lines[i]):
            buf.append(lines[i].strip())
            i += 1
        blocks.append({"kind": "p", "text": " ".join(buf)})
    return blocks


def _parse_chart_pairs(body):
    """解析 chart 围栏：每行 ``标签,数值``（也容忍 空格 / 冒号 / 制表符分隔）。

    可选第一行 ``title=xxx`` 作为系列名。
    """
    labels, values, series = [], [], ""
    for raw in (body or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(?:title|series|name)\s*[=:：]\s*(.+)$", line, re.I)
        if m and not labels:
            series = m.group(1).strip()
            continue
        # 从右往左找最后一个数字
        m = re.match(r"^(.*?)[,，;；\t:：]?\s*(-?\d+(?:\.\d+)?)\s*%?$", line)
        if not m:
            continue
        label = m.group(1).strip(" ,，;；\t:：")
        try:
            val = float(m.group(2))
        except ValueError:
            continue
        if not label:
            continue
        labels.append(label)
        values.append(val)
    return labels, values, series


def _resolve_img(src, base_dir):
    """把 Markdown 里的图片地址解析成本地绝对路径；不是本地存在的文件则返回 None。"""
    if not src:
        return None
    if src.startswith(("http://", "https://", "data:")):
        return None
    p = src.strip().strip("<>")
    p = p.replace("/", os.sep) if os.sep == "\\" else p
    if not os.path.isabs(p):
        p = os.path.join(base_dir or os.getcwd(), p)
    p = os.path.normpath(p)
    return p if os.path.isfile(p) else None


# ================================================================ 通用小工具

def _hex_rgb(h):
    h = str(h or "000000").lstrip("#").strip()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return h.upper()[:6].ljust(6, "0")


def _to_num(v):
    """把单元格文本转成数字；转不了返回 None。容忍千分位、百分号、全角空格。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v or "").strip().replace(",", "").replace("，", "").replace("　", "")
    if not s:
        return None
    pct = s.endswith("%")
    if pct:
        s = s[:-1].strip()
    try:
        f = float(s)
    except ValueError:
        return None
    if pct:
        f = f / 100.0
    if f == int(f) and abs(f) < 1e15 and not pct:
        return int(f)
    return f


def _is_formula(v):
    return isinstance(v, str) and v.strip().startswith("=")


def _col_letter(idx):
    """0 -> A, 25 -> Z, 26 -> AA"""
    from openpyxl.utils import get_column_letter
    return get_column_letter(idx + 1)


def _cell_display_width(v):
    """中日韩按 2 个字符宽估算，用于 Excel 列宽。"""
    s = "" if v is None else str(v)
    return sum(2 if ord(ch) > 127 else 1 for ch in s)


# ================================================================ Word (.docx)

def _docx_east_asian(holder, font_name):
    """给 run 或 style 设置 w:eastAsia 字体，否则中文会退回宋体。"""
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        rpr = holder._element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.insert(0, rf)
        rf.set(qn("w:eastAsia"), font_name)
        rf.set(qn("w:ascii"), font_name)
        rf.set(qn("w:hAnsi"), font_name)
    except Exception:
        pass


def _docx_para_bottom_border(p, color="1B4B7A", size=8, space=1):
    """给段落加下边框（当分隔线用，比画表格稳）。"""
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        pPr = p._p.get_or_add_pPr()
        pbdr = pPr.find(qn("w:pBdr"))
        if pbdr is None:
            pbdr = OxmlElement("w:pBdr")
            pPr.append(pbdr)
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), str(int(size)))
        bottom.set(qn("w:space"), str(int(space)))
        bottom.set(qn("w:color"), _hex_rgb(color))
        pbdr.append(bottom)
    except Exception:
        pass


def _docx_first_line_chars(style_or_para, chars=200):
    """设置「首行缩进 N 字符」（OOXML 的 firstLineChars，单位 1/100 字符）。"""
    try:
        from docx.oxml.ns import qn
        el = style_or_para.element if hasattr(style_or_para, "element") else style_or_para._p
        pPr = el.get_or_add_pPr()
        ind = pPr.get_or_add_ind()
        ind.set(qn("w:firstLineChars"), str(int(chars)))
    except Exception:
        pass


def _docx_field(paragraph, instr, placeholder="1", font=None, size=None, color=None):
    """插入一个 Word 域（PAGE / NUMPAGES / TOC）。"""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    def mk_run():
        r = paragraph.add_run()
        if font:
            r.font.name = font
            _docx_east_asian(r, font)
        if size:
            from docx.shared import Pt
            r.font.size = Pt(size)
        if color:
            from docx.shared import RGBColor
            r.font.color.rgb = RGBColor.from_string(_hex_rgb(color))
        return r._r

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    mk_run().append(begin)

    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    mk_run().append(it)

    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    mk_run().append(sep)

    if placeholder:
        t = OxmlElement("w:t")
        t.text = placeholder
        mk_run().append(t)

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    mk_run().append(end)


def _docx_shade_cell(cell, hex_color):
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), _hex_rgb(hex_color))
        tcPr.append(shd)
    except Exception:
        pass


def _docx_repeat_header(row):
    """表格跨页时重复表头。"""
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        trPr = row._tr.get_or_add_trPr()
        el = OxmlElement("w:tblHeader")
        el.set(qn("w:val"), "true")
        trPr.append(el)
    except Exception:
        pass


def _docx_style_font(style, name, size_pt=None, bold=None, color=None):
    from docx.shared import Pt, RGBColor
    style.font.name = name
    if size_pt:
        style.font.size = Pt(size_pt)
    if bold is not None:
        style.font.bold = bold
    if color:
        style.font.color.rgb = RGBColor.from_string(_hex_rgb(color))
    _docx_east_asian(style, name)


def _docx_setup_styles(doc):
    """把默认模板的样式改成「中文调研报告」的样子。"""
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    spec = DOCX_SPEC
    f = spec["font"]
    s = spec["size"]
    sp = spec["spacing"]
    c = spec["color"]

    # ---- 正文：宋体五号、1.5 倍行距
    normal = doc.styles["Normal"]
    _docx_style_font(normal, f["body"], s["body"], color="000000")
    normal.paragraph_format.line_spacing = sp["line"]
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)

    # ---- 正文段落（带首行缩进 2 字符、两端对齐）
    from docx.enum.style import WD_STYLE_TYPE
    try:
        body = doc.styles["BodyCN"]
    except KeyError:
        body = doc.styles.add_style("BodyCN", WD_STYLE_TYPE.PARAGRAPH)
        body.base_style = doc.styles["Normal"]
    _docx_style_font(body, f["body"], s["body"], color="000000")
    body.paragraph_format.line_spacing = sp["line"]
    body.paragraph_format.space_before = Pt(0)
    body.paragraph_format.space_after = Pt(sp["body_after"])
    body.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    body.paragraph_format.first_line_indent = Pt(s["body"] * 2)
    _docx_first_line_chars(body, sp["first_line_chars"])

    # ---- 三级标题
    for lvl, name, size, before, after, color in (
            (1, "Heading 1", s["h1"], sp["h1_before"], sp["h1_after"], c["h1"]),
            (2, "Heading 2", s["h2"], sp["h2_before"], sp["h2_after"], c["h2"]),
            (3, "Heading 3", s["h3"], sp["h3_before"], sp["h3_after"], c["h3"])):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        _docx_style_font(st, f["heading"], size, bold=True, color=color)
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.line_spacing = sp["heading_line"]
        st.paragraph_format.keep_with_next = True
        st.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        st.paragraph_format.first_line_indent = Pt(0)

    # ---- 题注（图/表说明）
    for nm in ("Caption", "题注"):
        try:
            cap = doc.styles[nm]
        except KeyError:
            continue
        _docx_style_font(cap, f["heading"], s["caption"], bold=False,
                         color=c["caption"])
        cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_before = Pt(4)
        cap.paragraph_format.space_after = Pt(8)

    # ---- 列表样式：去掉首行缩进，行距跟正文一致
    for nm in ("List Bullet", "List Bullet 2", "List Number", "List Number 2"):
        try:
            st = doc.styles[nm]
        except KeyError:
            continue
        _docx_style_font(st, f["body"], s["body"])
        st.paragraph_format.line_spacing = sp["line"]
        st.paragraph_format.space_after = Pt(3)
        st.paragraph_format.first_line_indent = Pt(0)


def _docx_page_setup(doc):
    from docx.shared import Mm
    spec = DOCX_SPEC["page"]
    for sec in doc.sections:
        sec.page_width = Mm(spec["width_mm"])
        sec.page_height = Mm(spec["height_mm"])
        sec.top_margin = Mm(spec["margin_mm"]["top"])
        sec.bottom_margin = Mm(spec["margin_mm"]["bottom"])
        sec.left_margin = Mm(spec["margin_mm"]["left"])
        sec.right_margin = Mm(spec["margin_mm"]["right"])
        sec.header_distance = Mm(spec["header_mm"])
        sec.footer_distance = Mm(spec["footer_mm"])


def _docx_add_footer(section, title_text):
    """页脚：左侧文档名 + 右侧「第 X 页 / 共 Y 页」，细横线。"""
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    spec = DOCX_SPEC
    footer = section.footer
    try:
        footer.is_linked_to_previous = False
    except Exception:
        pass
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.text = ""
    p.paragraph_format.first_line_indent = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)

    # 用右对齐制表位把页码推到右边
    try:
        from docx.shared import Mm
        usable = section.page_width - section.left_margin - section.right_margin
        p.paragraph_format.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
    except Exception:
        pass

    if title_text:
        r = p.add_run((title_text[:28] + "　") if title_text else "")
        r.font.name = spec["font"]["heading"]
        _docx_east_asian(r, spec["font"]["heading"])
        r.font.size = Pt(spec["size"]["footer"])
        from docx.shared import RGBColor
        r.font.color.rgb = RGBColor.from_string(_hex_rgb(spec["color"]["muted"]))
        p.add_run("\t")

    r2 = p.add_run("第 ")
    r2.font.size = Pt(spec["size"]["footer"])
    r2.font.name = spec["font"]["heading"]
    _docx_east_asian(r2, spec["font"]["heading"])
    _docx_field(p, "PAGE", "1", font=spec["font"]["heading"],
                size=spec["size"]["footer"], color=spec["color"]["muted"])
    r3 = p.add_run(" 页 / 共 ")
    r3.font.size = Pt(spec["size"]["footer"])
    r3.font.name = spec["font"]["heading"]
    _docx_east_asian(r3, spec["font"]["heading"])
    _docx_field(p, "NUMPAGES", "1", font=spec["font"]["heading"],
                size=spec["size"]["footer"], color=spec["color"]["muted"])
    r4 = p.add_run(" 页")
    r4.font.size = Pt(spec["size"]["footer"])
    r4.font.name = spec["font"]["heading"]
    _docx_east_asian(r4, spec["font"]["heading"])
    for r in (r2, r3, r4):
        from docx.shared import RGBColor
        r.font.color.rgb = RGBColor.from_string(_hex_rgb(spec["color"]["muted"]))
    _docx_para_bottom_border_top(p, spec["color"]["rule"])


def _docx_para_bottom_border_top(p, color, size=6):
    """页脚上方的分隔线（段落的 top border）。"""
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        pPr = p._p.get_or_add_pPr()
        pbdr = pPr.find(qn("w:pBdr"))
        if pbdr is None:
            pbdr = OxmlElement("w:pBdr")
            pPr.append(pbdr)
        top = OxmlElement("w:top")
        top.set(qn("w:val"), "single")
        top.set(qn("w:sz"), str(int(size)))
        top.set(qn("w:space"), "4")
        top.set(qn("w:color"), _hex_rgb(color))
        pbdr.append(top)
    except Exception:
        pass


def _docx_add_header(section, title_text, rule_color="D6E1EE"):
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    spec = DOCX_SPEC
    header = section.header
    try:
        header.is_linked_to_previous = False
    except Exception:
        pass
    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    p.text = ""
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.first_line_indent = Pt(0)
    r = p.add_run(title_text or "")
    r.font.name = spec["font"]["heading"]
    _docx_east_asian(r, spec["font"]["heading"])
    r.font.size = Pt(spec["size"]["footer"])
    r.font.color.rgb = RGBColor.from_string(_hex_rgb(spec["color"]["muted"]))
    _docx_para_bottom_border(p, rule_color, size=4, space=2)


def _docx_cover(doc, title, subtitle, author, date_text, theme):
    """封面页：大标题 + 副标题 + 分隔线 + 署名 / 日期。"""
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    spec = DOCX_SPEC
    cov = spec["cover"]
    th = get_theme(theme)
    accent = th["primary"]
    c = spec["color"]

    # 顶部装饰块（一个只有底边框的段落，做出「色条」的感觉）
    for _ in range(2):
        doc.add_paragraph()

    # 眉标
    kicker = doc.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.LEFT
    kr = kicker.add_run("　REPORT　/　调研报告")
    kr.font.name = spec["font"]["heading"]
    _docx_east_asian(kr, spec["font"]["heading"])
    kr.font.size = Pt(9)
    kr.font.color.rgb = RGBColor.from_string(_hex_rgb(c["muted"]))

    # 主标题
    pt = doc.add_paragraph()
    pt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pt.paragraph_format.space_before = Pt(18)
    pt.paragraph_format.space_after = Pt(cov["title_space_after"])
    pt.paragraph_format.line_spacing = 1.25
    tr = pt.add_run(title)
    tr.font.name = spec["font"]["cover"]
    _docx_east_asian(tr, spec["font"]["cover"])
    tr.font.size = Pt(spec["size"]["cover_title"])
    tr.font.bold = True
    tr.font.color.rgb = RGBColor.from_string(_hex_rgb(c["cover_title"]))

    # 主色分隔线
    rule = doc.add_paragraph()
    rule.paragraph_format.space_before = Pt(2)
    rule.paragraph_format.space_after = Pt(14)
    _docx_para_bottom_border(rule, accent, size=14, space=1)

    # 副标题
    if subtitle:
        ps = doc.add_paragraph()
        ps.alignment = WD_ALIGN_PARAGRAPH.LEFT
        ps.paragraph_format.space_after = Pt(cov["sub_space_after"])
        sr = ps.add_run(subtitle)
        sr.font.name = spec["font"]["heading"]
        _docx_east_asian(sr, spec["font"]["heading"])
        sr.font.size = Pt(spec["size"]["cover_sub"])
        sr.font.color.rgb = RGBColor.from_string(_hex_rgb(th["primary_dark"]))

    # 署名 / 日期
    meta = "　　".join([x for x in (author, date_text) if x])
    if meta:
        pm = doc.add_paragraph()
        pm.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pm.paragraph_format.space_before = Pt(cov["meta_space_before"])
        mr = pm.add_run(meta)
        mr.font.name = spec["font"]["heading"]
        _docx_east_asian(mr, spec["font"]["heading"])
        mr.font.size = Pt(spec["size"]["cover_meta"])
        mr.font.color.rgb = RGBColor.from_string(_hex_rgb(c["muted"]))

    # 页脚提示（放在封面正文区，不占页脚）
    for _ in range(6):
        doc.add_paragraph()
    note = doc.add_paragraph()
    nr = note.add_run(cov["bottom_note"])
    nr.font.name = spec["font"]["heading"]
    _docx_east_asian(nr, spec["font"]["heading"])
    nr.font.size = Pt(8)
    nr.font.color.rgb = RGBColor.from_string(_hex_rgb(c["muted"]))


def _docx_toc(doc, entries, theme):
    """目录页：TOC 域 + 手工条目兜底（Word 打开后按 F9 会替换成真目录）。"""
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    spec = DOCX_SPEC
    th = get_theme(theme)

    h = doc.add_paragraph()
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    h.paragraph_format.space_before = Pt(6)
    h.paragraph_format.space_after = Pt(16)
    hr = h.add_run(spec["toc"]["title"])
    hr.font.name = spec["font"]["heading"]
    _docx_east_asian(hr, spec["font"]["heading"])
    hr.font.size = Pt(spec["size"]["toc_title"])
    hr.font.bold = True
    hr.font.color.rgb = RGBColor.from_string(_hex_rgb(th["primary"]))
    _docx_para_bottom_border(h, th["primary"], size=8)

    # TOC 域：begin / instrText / separate / 占位 / end
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Pt(0)
    _docx_field(p, 'TOC \\o "%s" \\h \\z \\u' % spec["toc"]["levels"],
                placeholder=spec["toc"]["placeholder"],
                font=spec["font"]["body"], size=spec["size"]["toc_entry"],
                color=spec["color"]["muted"])

    # 手工降级目录：即使不更新域，读者也能看到结构
    if entries:
        gap = doc.add_paragraph()
        gap.paragraph_format.space_after = Pt(6)
        for lvl, text in entries:
            ep = doc.add_paragraph()
            ep.paragraph_format.first_line_indent = Pt(0)
            ep.paragraph_format.left_indent = Pt(12 * (lvl - 1))
            ep.paragraph_format.space_after = Pt(4)
            er = ep.add_run(text)
            er.font.name = spec["font"]["body"]
            _docx_east_asian(er, spec["font"]["body"])
            er.font.size = Pt(spec["size"]["toc_entry"])
            er.font.color.rgb = RGBColor.from_string(
                _hex_rgb(spec["color"]["h1"] if lvl == 1 else "333333"))
            er.bold = (lvl == 1)


def write_docx(path, blocks, title=None, base_dir=None, theme=None,
               author=None, subtitle=None, date=None, **kw):
    """把 blocks 写成 .docx：封面 + 目录域 + 三级标题 + 表格/图片题注 + 页脚页码。"""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.table import WD_ALIGN_VERTICAL

    spec = DOCX_SPEC
    th = get_theme(theme)
    font_body = spec["font"]["body"]
    font_head = spec["font"]["heading"]

    # ---- 决定封面标题 / 是否吃掉第一个 h1
    h1_idx = [i for i, b in enumerate(blocks)
              if b["kind"] == "h" and b["level"] == 1]
    doc_title = (title or "").strip()
    skip_idx = None
    if not doc_title and h1_idx:
        doc_title = inline_plain(blocks[h1_idx[0]]["text"])
        skip_idx = h1_idx[0]
    if not subtitle:
        for b in blocks:
            if b["kind"] == "quote" and b.get("text"):
                subtitle = inline_plain(b["text"])[:60]
                break

    doc = Document()
    _docx_setup_styles(doc)
    _docx_page_setup(doc)

    # ---------------- 封面（独立成一节，保证第 1 页只有封面）
    if doc_title:
        _docx_cover(doc, doc_title, subtitle, author, date, theme)
        body_sec = doc.add_section(WD_SECTION.NEW_PAGE)
    else:
        body_sec = doc.sections[0]

    # 正文节页码从 1 开始
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        sectPr = body_sec._sectPr
        pg = sectPr.find(qn("w:pgNumType"))
        if pg is None:
            pg = OxmlElement("w:pgNumType")
            sectPr.append(pg)
        pg.set(qn("w:start"), "1")
    except Exception:
        pass
    _docx_add_header(body_sec, doc_title, th["line"])
    _docx_add_footer(body_sec, doc_title)

    # ---------------- 目录（标题数 >= 3 才做，否则没意义）
    heads = [b for b in blocks if b["kind"] == "h" and b["level"] <= 3]
    if len(heads) >= 3:
        entries = []
        for b in heads[:24]:
            entries.append((min(b["level"], 3), inline_plain(b["text"])))
        _docx_toc(doc, entries, theme)
        doc.add_page_break()

    # ---------------- 正文
    counters = [0, 0, 0]
    table_no = 0
    fig_no = 0

    def add_runs(p, text, size=None, bold=False, italic=False, color=None,
                 mono=False):
        for seg, b, it, cd in inline_runs(text):
            r = p.add_run(seg)
            r.bold = bool(b or bold)
            r.italic = bool(it or italic)
            if mono or cd:
                r.font.name = spec["font"]["code"]
                r.font.size = Pt(spec["size"]["code"])
                r.font.color.rgb = RGBColor.from_string("B03060")
            else:
                r.font.name = font_body
                _docx_east_asian(r, font_body)
                if size:
                    r.font.size = Pt(size)
                if color:
                    r.font.color.rgb = RGBColor.from_string(_hex_rgb(color))
        return p

    def heading_text(text, lvl):
        """手动编号：1 / 1.1 / 1.1.1（标题本身已带编号就不重复加）。"""
        counters[lvl - 1] += 1
        for i in range(lvl, 3):
            counters[i] = 0
        if counters[0] == 0:
            counters[0] = 1
        num = ".".join(str(counters[i]) for i in range(lvl))
        raw = inline_plain(text)
        if re.match(r"^\s*(\d+(\.\d+)*[\s、.．]|[一二三四五六七八九十]+[、.．]|第[一二三四五六七八九十]+)", raw):
            return raw
        return f"{num}　{raw}"

    for i, b in enumerate(blocks):
        if skip_idx is not None and i == skip_idx:
            continue
        k = b["kind"]
        try:
            if k == "h":
                lvl = min(max(b["level"], 1), 3)
                p = doc.add_paragraph(style=f"Heading {lvl}")
                txt = heading_text(b["text"], lvl)
                r = p.add_run(txt)
                r.font.name = font_head
                _docx_east_asian(r, font_head)
                r.font.size = Pt(spec["size"][f"h{lvl}"])
                r.font.bold = True
                r.font.color.rgb = RGBColor.from_string(
                    _hex_rgb(spec["color"][f"h{lvl}"]))
                if b["level"] > 3:           # 4~6 级降级为正文本地加粗
                    p.style = doc.styles["BodyCN"]
                    r.font.size = Pt(spec["size"]["body"])
            elif k == "p":
                p = doc.add_paragraph(style="BodyCN")
                add_runs(p, b["text"])
            elif k == "li":
                style = "List Number" if b.get("ordered") else "List Bullet"
                if b.get("level", 0) >= 1:
                    style2 = style + " 2"
                    try:
                        doc.styles[style2]
                        style = style2
                    except KeyError:
                        pass
                p = doc.add_paragraph(style=style)
                p.paragraph_format.first_line_indent = Pt(0)
                add_runs(p, b["text"])
                if b.get("level", 0) >= 2:
                    p.paragraph_format.left_indent = Inches(0.55)
            elif k == "quote":
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.32)
                p.paragraph_format.right_indent = Inches(0.2)
                p.paragraph_format.space_before = Pt(6)
                p.paragraph_format.space_after = Pt(8)
                p.paragraph_format.line_spacing = spec["spacing"]["line"]
                p.paragraph_format.first_line_indent = Pt(0)
                from docx.oxml.ns import qn as _qn
                from docx.oxml import OxmlElement as _OE
                pPr = p._p.get_or_add_pPr()
                pbdr = _OE("w:pBdr")
                left = _OE("w:left")
                left.set(_qn("w:val"), "single")
                left.set(_qn("w:sz"), "18")
                left.set(_qn("w:space"), "8")
                left.set(_qn("w:color"), _hex_rgb(th["accent"]))
                pbdr.append(left)
                pPr.append(pbdr)
                add_runs(p, b["text"], size=spec["size"]["quote"],
                         color="44546A")
            elif k == "code":
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.3)
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(8)
                p.paragraph_format.line_spacing = 1.1
                p.paragraph_format.first_line_indent = Pt(0)
                from docx.oxml.ns import qn as _qn
                from docx.oxml import OxmlElement as _OE
                tcPr = p._p.get_or_add_pPr()
                shd = _OE("w:shd")
                shd.set(_qn("w:val"), "clear")
                shd.set(_qn("w:color"), "auto")
                shd.set(_qn("w:fill"), _hex_rgb(spec["color"]["code_fill"]))
                tcPr.append(shd)
                for ln_i, ln in enumerate((b.get("text") or "").split("\n")):
                    r = p.add_run(ln)
                    r.font.name = spec["font"]["code"]
                    r.font.size = Pt(spec["size"]["code"])
                    if ln_i < len((b.get("text") or "").split("\n")) - 1:
                        r.add_break()
            elif k == "table":
                table_no += 1
                header = [inline_plain(x) for x in (b.get("header") or [])]
                rows = [[inline_plain(x) for x in r] for r in (b.get("rows") or [])]
                ncol = max([len(header)] + [len(r) for r in rows] + [1])
                cap = doc.add_paragraph(style="Caption")
                cap.paragraph_format.first_line_indent = Pt(0)
                cr = cap.add_run(f"表 {table_no}　{header[0] if header else ''}"
                                 f"{'（' + header[-1] + '）' if len(header) > 1 else ''}")
                cr.font.name = font_head
                _docx_east_asian(cr, font_head)
                cr.font.size = Pt(spec["size"]["caption"])
                cr.bold = True
                cr.font.color.rgb = RGBColor.from_string(_hex_rgb(spec["color"]["caption"]))

                t = doc.add_table(rows=1, cols=ncol)
                t.style = "Table Grid"
                t.alignment = WD_TABLE_ALIGNMENT.CENTER
                t.autofit = True
                hdr = t.rows[0]
                _docx_repeat_header(hdr)
                for j in range(ncol):
                    cell = hdr.cells[j]
                    cell.text = ""
                    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                    _docx_shade_cell(cell, spec["color"]["table_head_fill"])
                    cp = cell.paragraphs[0]
                    cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    cp.paragraph_format.first_line_indent = Pt(0)
                    cp.paragraph_format.line_spacing = 1.15
                    run = cp.add_run(header[j] if j < len(header) else "")
                    run.bold = True
                    run.font.name = font_head
                    _docx_east_asian(run, font_head)
                    run.font.size = Pt(spec["size"]["table_head"])
                    run.font.color.rgb = RGBColor.from_string(
                        _hex_rgb(spec["color"]["table_head_text"]))
                for ri, r_ in enumerate(rows):
                    cells = t.add_row().cells
                    for j in range(ncol):
                        cell = cells[j]
                        cell.text = ""
                        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                        if ri % 2 == 1:
                            _docx_shade_cell(cell, spec["color"]["table_band_fill"])
                        cp = cell.paragraphs[0]
                        cp.paragraph_format.first_line_indent = Pt(0)
                        cp.paragraph_format.line_spacing = 1.15
                        val = r_[j] if j < len(r_) else ""
                        num = _to_num(val)
                        cp.alignment = (WD_ALIGN_PARAGRAPH.RIGHT
                                        if num is not None and not _is_formula(val)
                                        else WD_ALIGN_PARAGRAPH.LEFT)
                        run = cp.add_run(str(val))
                        run.font.name = font_body
                        _docx_east_asian(run, font_body)
                        run.font.size = Pt(spec["size"]["table"])
                        run.font.color.rgb = RGBColor.from_string("222222")
                doc.add_paragraph()
            elif k == "img":
                ip = _resolve_img(b.get("src"), base_dir)
                if ip:
                    try:
                        doc.add_picture(ip, width=Inches(5.6))
                        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                        fig_no += 1
                        cap = doc.add_paragraph(style="Caption")
                        cap.paragraph_format.first_line_indent = Pt(0)
                        cr = cap.add_run(f"图 {fig_no}　"
                                         f"{inline_plain(b.get('alt') or '示意图')}")
                        cr.font.name = font_head
                        _docx_east_asian(cr, font_head)
                        cr.font.size = Pt(spec["size"]["caption"])
                        cr.font.color.rgb = RGBColor.from_string(
                            _hex_rgb(spec["color"]["caption"]))
                    except Exception:
                        p = doc.add_paragraph(style="BodyCN")
                        add_runs(p, "[图片] " + (b.get("alt") or b.get("src") or ""))
                else:
                    p = doc.add_paragraph(style="BodyCN")
                    add_runs(p, "[图片] " + (b.get("alt") or b.get("src") or ""),
                             color="6B7A8C")
            elif k == "chart":
                # Word 里退化成「图 + 数据表」，不搞嵌入图表（太脆）
                fig_no += 1
                p = doc.add_paragraph(style="BodyCN")
                add_runs(p, f"图 {fig_no}　{b.get('series') or '数据'}"
                         f"（{'柱状图' if b.get('type') == 'bar' else b.get('type')}）",
                         color=spec["color"]["caption"])
                pairs = list(zip(b.get("labels") or [], b.get("values") or []))
                t = doc.add_table(rows=1, cols=len(pairs))
                t.style = "Table Grid"
                t.alignment = WD_TABLE_ALIGNMENT.CENTER
                for j, (lb, v) in enumerate(pairs):
                    c0 = t.rows[0].cells[j]
                    c0.text = ""
                    _docx_shade_cell(c0, spec["color"]["table_head_fill"])
                    rr = c0.paragraphs[0].add_run(str(lb))
                    rr.bold = True
                    rr.font.name = font_head
                    _docx_east_asian(rr, font_head)
                    rr.font.size = Pt(spec["size"]["table_head"] - 1)
                    rr.font.color.rgb = RGBColor.from_string("FFFFFF")
                cells = t.add_row().cells
                for j, (_lb, v) in enumerate(pairs):
                    cells[j].text = ""
                    pp = cells[j].paragraphs[0]
                    pp.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    rr = pp.add_run(f"{v:g}")
                    rr.font.name = font_body
                    _docx_east_asian(rr, font_body)
                    rr.font.size = Pt(spec["size"]["table"] - 1)
                doc.add_paragraph()
            elif k == "hr":
                p = doc.add_paragraph()
                p.paragraph_format.first_line_indent = Pt(0)
                _docx_para_bottom_border(p, spec["color"]["line"], size=6)
        except Exception:
            try:
                p = doc.add_paragraph(style="BodyCN")
                add_runs(p, inline_plain(b.get("text") or b.get("alt") or ""))
            except Exception:
                pass

    # 文档属性
    try:
        doc.core_properties.title = doc_title or "文档"
        doc.core_properties.author = author or "AIWorkbench"
        doc.core_properties.comments = "由 AIWorkbench 生成（officetpl 模板）"
    except Exception:
        pass
    doc.save(path)
    return path


# ================================================================ Excel (.xlsx)

def _xl_column_profile(rows, j):
    """统计第 j 列：数值比例、是否全整数、是否百分比。

    注意：**公式单元格不算数值**。否则「总分」列（全是 ``=SUM(...)``）会被当成
    可求和的原始分列，导致「平均分」把总分也一起平均了。
    """
    vals = [r[j] for r in rows if j < len(r) and str(r[j]).strip() != ""]
    if not vals:
        return {"numeric": False, "ratio": 0.0, "all_int": False, "pct": False}
    nums = [_to_num(v) for v in vals]
    ok = [n for n in nums if n is not None]
    ratio = len(ok) / float(len(vals))
    all_int = bool(ok) and all(isinstance(n, int) or abs(n - int(n)) < 1e-9 for n in ok)
    pct = any(str(v).strip().endswith("%") for v in vals)
    return {"numeric": ratio >= NUMERIC_RATIO, "ratio": ratio,
            "all_int": all_int, "pct": pct}


def _xl_numeric_like(rows, j):
    """数值 **或** 公式占比够高 → 这一列值得统计/上条件格式。"""
    vals = [r[j] for r in rows if j < len(r) and str(r[j]).strip() != ""]
    if not vals:
        return False
    ok = sum(1 for v in vals if _is_formula(v) or _to_num(v) is not None)
    return ok / float(len(vals)) >= NUMERIC_RATIO


def _xl_is_derived(header):
    """列名是否属于「不该参与纵向统计」的派生列（排名 / 等级 / 各种率）。"""
    h = header or ""
    if match_keyword(h, "rank") or match_keyword(h, "grade") or match_keyword(h, "pass"):
        return True
    return "率" in h or "占比" in h or "百分比" in h


def _xl_raw_score_cols(header, rows):
    """「原始分」列：数值列里排除派生列（总分/平均分/排名/等级/及格率）。"""
    out = []
    for j, h in enumerate(header):
        if j == 0:
            continue
        if _xl_is_derived(h):
            continue
        if match_keyword(h, "total") or match_keyword(h, "avg"):
            continue
        if _xl_column_profile(rows, j)["numeric"]:
            out.append(j)
    return out


def _xl_derive_formulas(header, rows):
    """按列名语义自动生成**真公式**，只填空单元格，绝不覆盖模型自己写的公式。

    安全策略：
    - 单元格为空 → 填公式；
    - 单元格是数字 → 先验算，**数值与公式结果一致**才替换成公式（这样能保证不改变原意）；
    - 单元格已经是 ``=...`` → 原样保留；
    - 任何一格对不上 → 整列不动。

    返回 (rows, formats)，formats 是 {列索引: number_format}。
    """
    if not header or not rows:
        return [list(r) for r in rows], {}

    nrow = len(rows)
    ncol = max([len(header)] + [len(r) for r in rows])
    out = [list(r) + [""] * (ncol - len(r)) for r in rows]
    formats = {}
    raw_cols = _xl_raw_score_cols(header, rows)
    if not raw_cols:
        return out, formats

    total_col = None
    for j, h in enumerate(header):
        if match_keyword(h, "total"):
            total_col = j
            break

    for j, h in enumerate(header):
        kind = None
        for k in ("total", "avg", "pass", "rank", "grade"):
            if match_keyword(h, k):
                kind = k
                break
        if not kind:
            continue
        left = [c for c in raw_cols if c < j]
        if kind in ("total", "avg", "pass") and not left:
            continue

        span = f"{_col_letter(left[0])}{{r}}:{_col_letter(left[-1])}{{r}}"

        if kind == "total":
            tmpl = f"=SUM({span})"

            def calc(i):
                vs = [v for v in (_to_num(out[i][c]) for c in left) if v is not None]
                return sum(vs)
        elif kind == "avg":
            tmpl = f"=ROUND(AVERAGE({span}),1)"

            def calc(i):
                vs = [v for v in (_to_num(out[i][c]) for c in left) if v is not None]
                return round(sum(vs) / len(vs), 1) if vs else 0
        elif kind == "pass":
            tmpl = f'=COUNTIF({span},">=60")/COUNT({span})'

            def calc(i):
                vs = [v for v in (_to_num(out[i][c]) for c in left) if v is not None]
                return (len([v for v in vs if v >= 60]) / float(len(vs))) if vs else 0
        elif kind == "rank":
            ref = total_col if total_col is not None else left[-1]
            L = _col_letter(ref)
            tmpl = "=RANK(" + L + "{r},$" + L + "${first}:$" + L + "${last})"

            def calc(i):
                return None
        else:  # grade
            ref = total_col if total_col is not None else left[-1]
            L = _col_letter(ref)
            tmpl = ("=IF(" + L + "{r}>=85,\"优秀\","
                    "IF(" + L + "{r}>=70,\"良好\","
                    "IF(" + L + "{r}>=60,\"及格\",\"不及格\")))")

            def calc(i):
                return None

        # 逐格验算：所有非公式单元格都必须「与算式结果一致」，才整列换成公式；
        # 只要有一格对不上（模型填了别的口径的数据），整列原样不动 —— 宁可少写公式，
        # 也不能把用户的数据改错。
        bad = False
        for i in range(nrow):
            cur = out[i][j]
            if _is_formula(cur):
                continue
            if str(cur).strip() == "":
                continue
            if kind in ("rank", "grade"):
                bad = True      # 手填的排名/等级不动
                break
            num = _to_num(cur)
            if num is None:
                bad = True
                break
            expect = calc(i)
            if expect is None or abs(float(num) - float(expect)) > 0.06:
                bad = True
                break
        if bad:
            continue
        for i in range(nrow):
            if not _is_formula(out[i][j]):
                out[i][j] = tmpl
        if kind == "pass":
            formats[j] = "0.0%"
        elif kind == "avg":
            formats[j] = "0.0"
        elif kind == "grade":
            formats[j] = "@"
        else:
            formats[j] = "0"
    return out, formats


def _xl_localize_formula(formula, excel_row, data_idx, first_data, last_data,
                         offset):
    """把公式里的占位符 / 行号落成真实行号。

    - ``{r}`` / ``{n}`` / ``{first}`` / ``{last}`` 直接替换；
    - 没有占位符的公式，按「模型以为表头在第 1 行」这个最常见假设，整体下移 offset 行。
      （我们为了好看会加一行大标题，数据整体下移一行，不修正就会引用错行。）
    """
    f = str(formula).strip()
    if not f.startswith("="):
        return f
    if any(t in f for t in ("{r}", "{n}", "{first}", "{last}")):
        return (f.replace("{r}", str(excel_row))
                 .replace("{n}", str(data_idx))
                 .replace("{first}", str(first_data))
                 .replace("{last}", str(last_data)))
    if offset:
        # 只动「列字母 + 行号」这种引用，不会碰 ">=60" 里的数字
        f = re.sub(r"(\$?[A-Za-z]{1,3}\$?)(\d+)",
                   lambda m: m.group(1) + str(int(m.group(2)) + offset), f)
    return f


def _xl_stat_rows(header, rows, ncol, first_data, last_data):
    """统计行：平均 / 最高 / 最低 / 及格率，全部真公式。

    「及格率」只对**单科原始分**列有意义（总分 250 分去比 60 分没意义），
    所以这一行只覆盖 0~100 的分数列。
    """
    cols = [j for j in range(1, ncol)
            if not _xl_is_derived(header[j]) and _xl_numeric_like(rows, j)]
    if len(cols) < 2:
        return []
    raw_cols = [j for j in cols
                if is_score_column(header[j])
                and not match_keyword(header[j], "total")
                and not match_keyword(header[j], "avg")]
    out = []
    for label, fn, fmt in (("平均", "AVERAGE", "0.0"),
                           ("最高", "MAX", "0"),
                           ("最低", "MIN", "0"),
                           ("及格率", "PASS", "0.0%")):
        use = raw_cols if fn == "PASS" else cols
        if not use:
            continue
        line = [""] * ncol
        line[0] = label
        for j in use:
            L = _col_letter(j)
            rng = f"{L}{first_data}:{L}{last_data}"
            if fn == "PASS":
                line[j] = f'=COUNTIF({rng},">=60")/COUNT({rng})'
            else:
                line[j] = f"={fn}({rng})"
        out.append((line, fmt))
    return out


def _xl_stat_cols(line):
    """统计行里哪些列放了公式（用于加粗）。"""
    return {j + 1 for j, v in enumerate(line) if _is_formula(v)}


def write_xlsx(path, blocks, title=None, base_dir=None, theme=None, **kw):
    """把 blocks 写成 .xlsx。

    版面：大标题行（合并居中）+ 主题色表头 + 斑马纹 + 冻结窗格 + 自动筛选 +
    真公式 + 条件格式（不及格标红 / 色阶 / 数据条）+ 统计行 + 多表时的目录页。
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.formatting.rule import (CellIsRule, ColorScaleRule,
                                          DataBarRule)
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.properties import PageSetupProperties

    spec = XLSX_SPEC
    pal = get_palette(theme)
    font_name = spec["font"]

    thin = Side(style="thin", color=pal["border"])
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_font = Font(bold=True, color=pal["head_text"],
                     size=spec["head_size"], name=font_name)
    head_fill = PatternFill("solid", fgColor=pal["head_bg"])
    cell_font = Font(size=spec["cell_size"], name=font_name, color="1F2937")
    stat_font = Font(bold=True, size=spec["stat_size"], name=font_name,
                     color=pal["stat_text"])
    band_fill = PatternFill("solid", fgColor=pal["band"])
    stat_fill = PatternFill("solid", fgColor=pal["stat_bg"])

    # ---- 收集表格
    tables = []
    pending_title = title
    for b in blocks:
        if b["kind"] == "h":
            pending_title = inline_plain(b["text"])
        elif b["kind"] == "table":
            tables.append((pending_title or f"表{len(tables) + 1}",
                           [inline_plain(x) for x in (b.get("header") or [])],
                           [[inline_plain(x) for x in r]
                            for r in (b.get("rows") or [])]))
            pending_title = None

    def safe_sheet_name(s, used):
        s = re.sub(r"[\\/*?:\[\]]", "_", (s or "Sheet")).strip()[:28] or "Sheet"
        name, k = s, 2
        while name in used:
            name = f"{s[:26]}_{k}"
            k += 1
        used.add(name)
        return name

    wb = Workbook()
    used_names = set()

    # 目录页要先占名字，再给每张表分配唯一名 —— 只分配一次，
    # 否则「目录里的链接」和「真正的工作表名」会不一致（名字被 _2 了）。
    index_name = None
    if len(tables) >= 2:
        index_name = safe_sheet_name(spec["index_sheet_name"], used_names)
    planned = [(safe_sheet_name(nm, used_names), nm, header, rows_in)
               for nm, header, rows_in in tables]

    # ---- 目录页（多表时）
    if index_name:
        idx = wb.active
        idx.title = index_name
        idx.sheet_view.showGridLines = False
        idx.merge_cells("A1:D1")
        c = idx["A1"]
        c.value = f"{title or '数据总表'}　·　目录"
        c.font = Font(bold=True, size=16, color=pal["title_text"], name=font_name)
        c.fill = PatternFill("solid", fgColor=pal["title_bg"])
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        idx.row_dimensions[1].height = 40
        for j, h in enumerate(("序号", "工作表", "内容", "数据行数"), start=1):
            cell = idx.cell(row=3, column=j, value=h)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border
        idx.row_dimensions[3].height = spec["head_height"]
        idx.freeze_panes = "A4"
        for i, (sheet_nm, nm, header, rows) in enumerate(planned, start=1):
            r = 3 + i
            for j, v in enumerate((i, sheet_nm, nm, len(rows)), start=1):
                cell = idx.cell(row=r, column=j, value=v)
                cell.font = cell_font
                cell.border = border
                cell.alignment = Alignment(
                    horizontal="center" if j in (1, 4) else "left",
                    vertical="center")
                if i % 2 == 0:
                    cell.fill = band_fill
            link = idx.cell(row=r, column=2)
            link.hyperlink = f"#'{sheet_nm}'!A1"
            link.font = Font(size=spec["cell_size"], name=font_name,
                             color=pal["primary"], underline="single")
        for col, w in zip("ABCD", (8, 26, 46, 12)):
            idx.column_dimensions[col].width = w
        note = idx.cell(row=3 + len(tables) + 2, column=1,
                        value="说明：每个工作表第 1 行为大标题、第 2 行为表头；"
                              "已冻结表头并开启自动筛选；统计行是真公式，随数据联动。")
        note.font = Font(size=9, color=pal["muted"], name=font_name)
    else:
        wb.remove(wb.active)

    # ---- 建表
    for sheet_nm, nm, header, rows_in in planned:
        ws = wb.create_sheet(sheet_nm)
        ws.sheet_view.showGridLines = False
        ncol = max([len(header)] + [len(r) for r in rows_in] + [1])
        header = header + [""] * (ncol - len(header))

        rows, fmts = _xl_derive_formulas(header, rows_in)

        head_row = 1
        title_row = False
        has_formula = any(_is_formula(v) for r_ in rows for v in r_)
        # 有模型自己写的公式时，为了不打乱它假设的行号，把大标题让给工作表名
        # （行号整体偏移由 _xl_localize_formula 兜住）
        if nm:
            ws.merge_cells(start_row=1, start_column=1,
                           end_row=1, end_column=ncol)
            c = ws.cell(row=1, column=1, value=nm)
            c.font = Font(bold=True, size=spec["title_size"],
                          color=pal["title_text"], name=font_name)
            c.fill = PatternFill("solid", fgColor=pal["title_bg"])
            c.alignment = Alignment(horizontal="left", vertical="center",
                                    indent=1)
            ws.row_dimensions[1].height = spec["title_height"]
            head_row = 2
            title_row = True

        offset = 1 if title_row else 0

        for j, h in enumerate(header, start=1):
            c = ws.cell(row=head_row, column=j, value=h)
            c.font = head_font
            c.fill = head_fill
            c.alignment = Alignment(horizontal="center", vertical="center",
                                    wrap_text=True)
            c.border = border
        ws.row_dimensions[head_row].height = spec["head_height"]

        data_start = head_row + 1
        last_data = data_start + len(rows) - 1 if rows else head_row
        profiles = [_xl_column_profile(rows, j) for j in range(ncol)]

        def _fmt_for(j):
            """列默认数字格式：率用百分比，整数用 0，其它两位小数。"""
            if fmts.get(j):
                return fmts[j]
            h = header[j] if j < len(header) else ""
            if "率" in h or "占比" in h or "百分比" in h or "比例" in h:
                return "0.0%"
            return "0" if profiles[j]["all_int"] else "0.00"

        for i, r_ in enumerate(rows):
            excel_row = data_start + i
            for j, v in enumerate(r_, start=1):
                if _is_formula(v):
                    val = _xl_localize_formula(v, excel_row, i + 1,
                                               data_start, last_data, offset)
                    is_num_cell = True
                else:
                    parsed = _to_num(v)
                    val = parsed if parsed is not None else (v if v != "" else None)
                    is_num_cell = parsed is not None
                c = ws.cell(row=excel_row, column=j, value=val)
                c.font = cell_font
                c.border = border
                if is_num_cell:
                    c.alignment = Alignment(horizontal="right", vertical="center")
                    c.number_format = _fmt_for(j - 1)
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center",
                                            wrap_text=False)
                if i % 2 == 1:
                    c.fill = band_fill
            ws.row_dimensions[excel_row].height = spec["row_height"]

        # ---- 统计行（只在「像数据表」且原表没有合计行时加）
        existing_total = any(
            str(r[0]).strip() in ("合计", "总计", "小计", "平均", "汇总", "统计")
            for r in rows_in if r)
        if len(rows_in) >= MIN_ROWS_FOR_STATS and not existing_total:
            for si, (line, fmt) in enumerate(
                    _xl_stat_rows(header, rows, ncol, data_start, last_data)):
                excel_row = last_data + 1 + si
                bold_cols = _xl_stat_cols(line)
                for j, v in enumerate(line, start=1):
                    val = v if (_is_formula(v) or v != "") else None
                    c = ws.cell(row=excel_row, column=j, value=val)
                    c.font = stat_font if (j == 1 or j in bold_cols) else cell_font
                    c.fill = stat_fill
                    c.border = border
                    c.alignment = Alignment(
                        horizontal="left" if j == 1 else "right",
                        vertical="center")
                    if _is_formula(v):
                        c.number_format = fmt
                ws.row_dimensions[excel_row].height = spec["row_height"]

        # ---- 冻结窗格（标题 + 表头）+ 自动筛选（不含统计行）
        ws.freeze_panes = ws.cell(row=data_start, column=1)
        ws.auto_filter.ref = f"A{head_row}:{get_column_letter(ncol)}{last_data}"

        # ---- 条件格式
        for j in range(ncol):
            if not rows or not _xl_numeric_like(rows, j):
                continue
            h = header[j]
            if _xl_is_derived(h):
                continue
            L = get_column_letter(j + 1)
            rng = f"{L}{data_start}:{L}{last_data}"
            is_total = bool(match_keyword(h, "total")) or bool(match_keyword(h, "avg"))
            if is_total:
                # 总分/平均分：数据条，直观看出高低
                ws.conditional_formatting.add(rng, DataBarRule(
                    start_type="num", start_value=0, end_type="max",
                    color=pal["accent"], showValue=True))
            elif is_score_column(h):
                # 分数列：不及格标红（优先级高，先加）+ 红黄绿色阶
                ws.conditional_formatting.add(rng, CellIsRule(
                    operator="lessThan", formula=["60"],
                    fill=PatternFill("solid", fgColor=pal["fail_bg"],
                                     bgColor=pal["fail_bg"]),
                    font=Font(color=pal["fail_text"], bold=True,
                              name=font_name)))
                ws.conditional_formatting.add(rng, ColorScaleRule(
                    start_type="num", start_value=0, start_color="F8B4B4",
                    mid_type="num", mid_value=80, mid_color="FFF7E0",
                    end_type="num", end_value=100, end_color="C6E7CE"))

        # ---- 列宽（中文按 2 个字符宽算）
        for j in range(ncol):
            widest = _cell_display_width(header[j])
            for r_ in rows[:200]:
                if j < len(r_):
                    widest = max(widest, _cell_display_width(r_[j]))
            ws.column_dimensions[get_column_letter(j + 1)].width = max(
                spec["min_col_width"],
                min(widest * 1.15 + 4, spec["max_col_width"]))

        # ---- 打印设置
        try:
            ws.page_setup.orientation = "landscape"
            ws.page_setup.paperSize = ws.PAPERSIZE_A4
            ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
            ws.page_setup.fitToWidth = 1
            ws.page_setup.fitToHeight = 0
            ws.print_title_rows = f"{head_row}:{head_row}"
        except Exception:
            pass

    # 没有表格：整篇当纯文本，一行一格（保持老行为）
    if not tables:
        ws = wb.active if wb.active is not None else wb.create_sheet()
        ws.title = safe_sheet_name(title or "内容", used_names)
        ws.sheet_view.showGridLines = False
        r = 1
        if title:
            ws.cell(row=1, column=1, value=title).font = Font(
                bold=True, size=14, name=font_name, color=pal["primary"])
            r = 2
        for b in blocks:
            if b["kind"] == "h":
                cell = ws.cell(row=r, column=1,
                               value=("　" * (b["level"] - 1)) + inline_plain(b["text"]))
                cell.font = Font(bold=True, size=11.5 - b["level"], name=font_name,
                                 color=pal["primary"])
            elif b["kind"] in ("p", "quote"):
                WS_CELL = ws.cell(row=r, column=1, value=inline_plain(b["text"]))
                WS_CELL.font = cell_font
            elif b["kind"] == "li":
                cell = ws.cell(row=r, column=1,
                               value="　" * (b.get("level", 0) * 2)
                               + ("• " if not b.get("ordered") else "· ")
                               + inline_plain(b["text"]))
                cell.font = cell_font
            elif b["kind"] == "code":
                for ln in (b.get("text") or "").split("\n"):
                    cell = ws.cell(row=r, column=1, value=ln)
                    cell.font = cell_font
                    r += 1
                continue
            r += 1
        for row in ws.iter_rows():
            for c in row:
                if not c.font or not c.font.name:
                    c.font = cell_font
                c.alignment = Alignment(vertical="top", wrap_text=False)
        ws.column_dimensions["A"].width = 90
        ws.freeze_panes = "A2"

    try:
        wb.properties.title = title or "数据表"
        wb.properties.creator = "AIWorkbench"
    except Exception:
        pass
    wb.save(path)
    return path


# ================================================================ PPT (.pptx)

# 画布与栅格（单位：英寸，16:9）
# 这里不再是「定义」，而是「读取」——所有数值来自 ``app/officetpl/pptx_spec.py``。
# 保留这些模块级别名是为了向后兼容（外部有按 docwrite.MARGIN_X 取值的代码）。
_PPT_SPEC = PPTX_SPEC
_PPT_MARGIN = _PPT_SPEC["margin"]
_PPT_GRID = _PPT_SPEC["grid"]
_PPT_SPACING = _PPT_SPEC["spacing"]
_PPT_COVER = _PPT_SPEC["cover"]
_PPT_SECTION = _PPT_SPEC["section"]
_PPT_END = _PPT_SPEC["end"]
_PPT_TEXT = _PPT_SPEC["text"]

SLIDE_W = _PPT_SPEC["slide"]["width_in"]
SLIDE_H = _PPT_SPEC["slide"]["height_in"]
MARGIN_X = _PPT_MARGIN["left"]
CONTENT_W = _PPT_MARGIN["content_w"]
TITLE_Y = _PPT_MARGIN["title_y"]
TITLE_H = _PPT_MARGIN["title_h"]
BODY_TOP = _PPT_MARGIN["body_top"]
BODY_BOTTOM = _PPT_MARGIN["body_bottom"]
FOOTER_Y = _PPT_MARGIN["footer_y"]


def _ppt_size(th, key, delta=0.0):
    """从当前主题取字号（pt）。

    主题自带 ``size`` 字典，是字号的第一权威来源；万一某套自定义主题缺键，
    回落到 ``PPTX_SPEC['size'][key]['default']``，绝不让渲染崩。
    """
    size_map = th.get("size") or {}
    val = size_map.get(key)
    if val is None:
        val = (_PPT_SPEC["size"].get(key) or {}).get("default", 20.0)
    return float(val) + float(delta)


def _ptx():
    """延迟导入 python-pptx 的一堆零碎对象（docwrite 在无 pptx 环境也要能 import）。"""
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_VERTICAL_ANCHOR
    return dict(Inches=Inches, Pt=Pt, RGBColor=RGBColor, PP_ALIGN=PP_ALIGN,
                MSO_ANCHOR=MSO_ANCHOR, MSO_AUTO_SIZE=MSO_AUTO_SIZE,
                MSO_SHAPE=MSO_SHAPE, MSO_VERTICAL_ANCHOR=MSO_VERTICAL_ANCHOR)


def _c(hexstr):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(_hex_rgb(hexstr))


def _ppt_font_run(run, name, size, color=None, bold=False, italic=False):
    """设置 run 字体，并把 a:ea（东亚字体）也钉死，否则中文会被 PowerPoint 换掉。"""
    from pptx.util import Pt
    from pptx.oxml.ns import qn
    from lxml import etree
    run.font.size = Pt(size)
    run.font.bold = bool(bold)
    run.font.italic = bool(italic)
    run.font.name = name
    if color:
        run.font.color.rgb = _c(color)
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", name)


def _ppt_textbox(slide, x, y, w, h, anchor="t", margins=(0.0, 0.0, 0.0, 0.0)):
    from pptx.util import Inches
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE,
                          "b": MSO_ANCHOR.BOTTOM}[anchor]
    tf.margin_left = Inches(margins[0])
    tf.margin_right = Inches(margins[1])
    tf.margin_top = Inches(margins[2])
    tf.margin_bottom = Inches(margins[3])
    return box


def _ppt_rect(slide, x, y, w, h, fill=None, line=None, shape="rect",
              line_w=1.0, dash=None):
    from pptx.util import Inches, Pt
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.dml import MSO_LINE_DASH_STYLE
    kinds = {"rect": MSO_SHAPE.RECTANGLE, "round": MSO_SHAPE.ROUNDED_RECTANGLE,
             "oval": MSO_SHAPE.OVAL, "tri": MSO_SHAPE.RIGHT_TRIANGLE}
    sh = slide.shapes.add_shape(kinds.get(shape, MSO_SHAPE.RECTANGLE),
                                Inches(x), Inches(y), Inches(w), Inches(h))
    sh.shadow.inherit = False
    if fill:
        sh.fill.solid()
        sh.fill.fore_color.rgb = _c(fill)
    else:
        sh.fill.background()
    if line:
        sh.line.color.rgb = _c(line)
        sh.line.width = Pt(line_w)
        if dash:
            sh.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    else:
        sh.line.fill.background()
    if shape == "round":
        try:
            sh.adjustments[0] = 0.08
        except Exception:
            pass
    sh.text_frame.word_wrap = True
    return sh


def _ppt_bullet(p, glyph="•", marl=None, hanging=None, numbered=False, level=0):
    """给段落加真项目符号（python-pptx 没有 bullet API，得自己写 pPr）。"""
    from pptx.util import Inches
    from pptx.oxml.ns import qn
    from lxml import etree
    bs = _PPT_SPACING["bullet"]
    if marl is None:
        marl = bs["marl"]
    if hanging is None:
        hanging = bs["hanging"]
    pPr = p._p.get_or_add_pPr()
    indent = marl + bs["level_step"] * level
    pPr.set("marL", str(int(Inches(indent))))
    pPr.set("indent", str(-int(Inches(hanging))))
    if numbered:
        num = etree.SubElement(pPr, qn("a:buAutoNum"))
        num.set("type", "arabicPeriod")
    else:
        bf = etree.SubElement(pPr, qn("a:buFont"))
        bf.set("typeface", bs["font"])
        bc = etree.SubElement(pPr, qn("a:buChar"))
        bc.set("char", glyph)
    return p


def _ppt_bg(slide, color, fallback_rect=True):
    """整页底色。优先用 slide.background（PowerPoint 里改主题色能一起变），失败退化成色块。"""
    try:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = _c(color)
        return
    except Exception:
        pass
    if fallback_rect:
        sh = slide.shapes.add_shape(1, 0, 0, 0, 0)  # 占位，真实坐标下面补
        sh.left, sh.top = 0, 0
        from pptx.util import Inches
        sh.width = Inches(SLIDE_W)
        sh.height = Inches(SLIDE_H)


def _ppt_new_slide(prs, th, layout_idx=5):
    slide = prs.slides.add_slide(prs.slide_layouts[layout_idx])
    _ppt_bg(slide, th["bg"])
    return slide


def _ppt_drop_shape(shape):
    try:
        shape._element.getparent().remove(shape._element)
    except Exception:
        pass


def _ppt_drop_empty_placeholders(slide, keep=(0,)):
    for ph in list(slide.placeholders):
        try:
            if ph.placeholder_format.idx in keep:
                continue
            if not (ph.text_frame.text or "").strip():
                _ppt_drop_shape(ph)
        except Exception:
            continue


def _ppt_fit_size(text, width_in, base_size, min_size=None, bold=True):
    """标题/单行文字按宽度自动缩字号。"""
    size = float(base_size)
    if min_size is None:
        min_size = _PPT_SPACING["fit_min"]
    ratio = _PPT_SPACING["fit_ratio"]
    while size > min_size and text_em(text, bold) * size / 72.0 > width_in * ratio:
        size -= 1.0
    return size


def _ppt_title(slide, th, text, width=None):
    """填标题占位符 + 画标题条装饰。"""
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    w = width or CONTENT_W
    ph = slide.shapes.title
    if ph is None:
        box = _ppt_textbox(slide, MARGIN_X, TITLE_Y, w, TITLE_H, anchor="m")
        tf = box.text_frame
    else:
        ph.left, ph.top = Inches(MARGIN_X), Inches(TITLE_Y)
        ph.width, ph.height = Inches(w), Inches(TITLE_H)
        tf = ph.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.vertical_anchor = 3  # MIDDLE
    for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, m, Inches(0))
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    run.text = inline_plain(text)
    size = _ppt_fit_size(run.text, w - _PPT_SPACING["title_fit_pad"],
                         _ppt_size(th, "slide_title"))
    _ppt_font_run(run, PPT_FONT, size, th["text"], bold=True)

    rule = _PPT_SPACING["title_rule"]
    style = th.get("title_style")
    if style == "dot":
        d = rule["dot"]
        _ppt_rect(slide, MARGIN_X + d["dot_x"], TITLE_Y + d["dot_y"],
                  d["dot"], d["dot"], fill=th["primary"], shape="oval")
        _ppt_rect(slide, MARGIN_X - 0.02, TITLE_Y + TITLE_H + d["line_y"], w,
                  d["line_h"], fill=th["line"])
    elif style == "rule":
        d = rule["rule"]
        _ppt_rect(slide, MARGIN_X, TITLE_Y + TITLE_H + d["bar_y"], w,
                  d["bar_h"], fill=th["primary"])
        _ppt_rect(slide, MARGIN_X, TITLE_Y + TITLE_H + d["accent_y"],
                  w * d["accent_ratio"], d["accent_h"], fill=th["accent"])
    else:  # bar
        d = rule["bar"]
        _ppt_rect(slide, MARGIN_X + d["bar_x"], TITLE_Y + d["bar_y"],
                  d["bar_w"], d["bar_h"], fill=th["primary"])
        _ppt_rect(slide, MARGIN_X, TITLE_Y + TITLE_H + d["line_y"], w,
                  d["line_h"], fill=th["line"])
        _ppt_rect(slide, MARGIN_X, TITLE_Y + TITLE_H + d["line_y"],
                  d["accent_w"], d["line_h"], fill=th["primary"])


def _ppt_footer(slide, th, page_no):
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    fs = _PPT_SPACING["footer"]
    box = _ppt_textbox(slide, MARGIN_X, FOOTER_Y,
                       CONTENT_W * fs["name_ratio"], _PPT_MARGIN["footer_h"])
    p = box.text_frame.paragraphs[0]
    r = p.add_run()
    r.text = th["name"]
    _ppt_font_run(r, PPT_FONT, _ppt_size(th, "footer"), th["muted"])

    box2 = _ppt_textbox(slide, SLIDE_W - MARGIN_X - fs["page_w"], FOOTER_Y,
                        fs["page_w"], _PPT_MARGIN["footer_h"])
    p2 = box2.text_frame.paragraphs[0]
    p2.alignment = PP_ALIGN.RIGHT
    r2 = p2.add_run()
    r2.text = f"{page_no:02d}"
    _ppt_font_run(r2, PPT_FONT, _ppt_size(th, "footer", fs["page_size_add"]),
                  th["muted"], bold=True)


def _ppt_bullets_body(slide, th, items, x=MARGIN_X, y=BODY_TOP,
                      w=CONTENT_W, h=BODY_BOTTOM - BODY_TOP, width2=None):
    """渲染要点列表：自动缩字号，超过 8 条自动两栏。"""
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN

    if not items:
        return True

    sp = _PPT_SPACING
    gb = _PPT_GRID["bullets"]
    body0 = _ppt_size(th, "body")

    paras = [(inline_plain(it[0]), it[1] if len(it) > 1 else 0) for it in items]
    two_col = len(items) > gb["two_col_over"] and width2 is None
    if two_col:
        half = (len(items) + 1) // 2
        groups = [items[:half], items[half:]]
        col_w = (w - gb["gap"]) / 2.0
        fits_all = True
        for gi, grp in enumerate(groups):
            gx = x + gi * (col_w + gb["gap"])
            p_paras = [(inline_plain(it[0]), it[1] if len(it) > 1 else 0)
                       for it in grp]
            size, ok = fit_font_size(
                p_paras, col_w - gb["col_width_slack"], h,
                [body0, body0 - 1, body0 - 2, 16, 15, 14],
                line_spacing=sp["line"], space_after_pt=sp["space_after"],
                indent_em=0.5)
            fits_all = fits_all and ok
            box = _ppt_textbox(slide, gx, y, col_w, h)
            _ppt_render_paras(box, th, grp, size)
        return fits_all

    size, ok = fit_font_size(
        paras, w - gb["width_slack"], h,
        [body0, body0 - 1, body0 - 2, body0 - 3, 17, 16, 15, 14],
        line_spacing=sp["line"], space_after_pt=sp["space_after"], indent_em=0.5)
    box = _ppt_textbox(slide, x, y, w, h)
    _ppt_render_paras(box, th, items, size)
    return ok


def _ppt_render_paras(box, th, items, size):
    """把 [(text, level, ordered, plain?)] 画进文本框。"""
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Pt
    sp = _PPT_SPACING
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for it in items:
        text = inline_plain(it[0])
        level = it[1] if len(it) > 1 else 0
        ordered = bool(it[2]) if len(it) > 2 else False
        plain = bool(it[3]) if len(it) > 3 else False
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = PP_ALIGN.LEFT
        p.line_spacing = sp["line"]
        p.space_after = Pt(sp["space_after_plain"] if plain else sp["space_after"])
        p.space_before = 0
        if plain:
            pPr = p._p.get_or_add_pPr()
            from lxml import etree
            from pptx.oxml.ns import qn
            etree.SubElement(pPr, qn("a:buNone"))
            pPr.set("marL", "0")
            pPr.set("indent", "0")
        else:
            _ppt_bullet(p, th["bullet_glyph"], level=level, numbered=ordered)
        run = p.add_run()
        run.text = text
        fs = size if level == 0 else max(size - sp["level_font_step"],
                                         sp["level_font_min"])
        _ppt_font_run(run, PPT_FONT, fs,
                      th["text"] if level == 0 else th["muted"],
                      bold=(level == 0 and plain))


def _ppt_cover(prs, th, deck_title, subtitle, meta_lines, align="left"):
    """封面：按主题的 cover_style 走四套完全不同的版式。

    ``align`` 来自模板预设的 ``cover_align``：``left``（默认，历史行为）或
    ``center``。只有显式选了居中的预设才会走新分支，默认渲染不变。
    """
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN

    slide = _ppt_new_slide(prs, th, 0)   # Title Slide：真标题 + 副标题占位符
    style = th.get("cover_style")
    cfg = _PPT_COVER
    sp = _PPT_SPACING
    deco = cfg["deco"].get(style) or cfg["deco"]["bar"]
    title_w = deco.get("title_w", cfg["title_w"])
    title_y = deco.get("title_y", cfg["title_y"])

    if style == "bar":                      # 清爽学术：左侧主色竖条
        _ppt_rect(slide, 0, 0, deco["rail_w"], SLIDE_H, fill=th["primary"])
        _ppt_rect(slide, 0, 0, deco["rail_w"], deco["rail_accent_h"],
                  fill=th["accent"])
        _ppt_rect(slide, deco["oval_x"], deco["oval_y"], deco["oval"],
                  deco["oval"], fill=th["bg_alt"], shape="oval")
        title_x = deco["title_x"]
    elif style == "glow":                   # 深色科技：角落光斑 + 细线
        _ppt_rect(slide, deco["oval_x"], deco["oval_y"], deco["oval"],
                  deco["oval"], fill=th["bg_alt"], shape="oval")
        _ppt_rect(slide, deco["oval2_x"], deco["oval2_y"], deco["oval2"],
                  deco["oval2"], fill=th["primary_dark"], shape="oval")
        _ppt_rect(slide, deco["rule_x"], deco["rule_y"], deco["rule_w"],
                  deco["rule_h"], fill=th["primary"])
        _ppt_rect(slide, 0, deco["bottom_line_y"], SLIDE_W, deco["rule_h"],
                  fill=th["line"])
        title_x = deco["title_x"]
    elif style == "frame":                  # 国风暖色：双细框 + 印章色方块
        _ppt_rect(slide, deco["inset1"], deco["inset1"],
                  SLIDE_W - 2 * deco["inset1"], SLIDE_H - 2 * deco["inset1"],
                  fill=None, line=th["line"], line_w=1.2)
        _ppt_rect(slide, deco["inset2"], deco["inset2"],
                  SLIDE_W - 2 * deco["inset2"], SLIDE_H - 2 * deco["inset2"],
                  fill=None, line=th["accent"], line_w=0.75)
        _ppt_rect(slide, deco["seal_x"], deco["seal_y"], deco["seal"],
                  deco["seal"], fill=th["primary"], shape="round")
        title_x = deco["title_x"]
    else:                                   # 商务简约：底部色带
        _ppt_rect(slide, 0, deco["band_y"], SLIDE_W, deco["band_h"],
                  fill=th["primary_dark"])
        _ppt_rect(slide, 0, deco["band_y"], SLIDE_W, deco["accent_h"],
                  fill=th["accent"])
        _ppt_rect(slide, deco["rule_x"], deco["rule_y"], deco["rule_w"],
                  deco["rule_h"], fill=th["primary"])
        title_x = deco["title_x"]

    center = str(align or "left").strip().lower() == "center"
    if center:
        title_x = (SLIDE_W - title_w) / 2.0
    p_align = PP_ALIGN.CENTER if center else PP_ALIGN.LEFT

    ph = slide.shapes.title
    if ph is not None:
        ph.left, ph.top = Inches(title_x), Inches(title_y)
        ph.width, ph.height = Inches(title_w), Inches(cfg["title_h"])
        tf = ph.text_frame
        tf.clear()
        tf.word_wrap = True
        tf.vertical_anchor = 3
        for m in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
            setattr(tf, m, Inches(0))
        p = tf.paragraphs[0]
        p.alignment = p_align
        p.line_spacing = sp["line_tight"]
        run = p.add_run()
        run.text = inline_plain(deck_title)
        base = _ppt_size(th, "cover_title")
        size = _ppt_fit_size(run.text, title_w - 0.2, base,
                             min_size=sp["cover_fit_min"])
        # 标题过长时允许折成两行：按长度再压一档
        if size < base - 6:
            size = max(size, sp["cover_two_line_min"])
        _ppt_font_run(run, PPT_FONT, size, th["text"], bold=True)

    sub_ph = None
    for p_ in slide.placeholders:
        if p_.placeholder_format.idx == 1:
            sub_ph = p_
            break
    if sub_ph is not None:
        if subtitle:
            sub_ph.left, sub_ph.top = (Inches(title_x),
                                       Inches(title_y + cfg["sub_offset_y"]))
            sub_ph.width = Inches(title_w * cfg["sub_w_ratio"])
            sub_ph.height = Inches(cfg["sub_h"])
            tf = sub_ph.text_frame
            tf.clear()
            tf.word_wrap = True
            for m in ("margin_left", "margin_right", "margin_top",
                      "margin_bottom"):
                setattr(tf, m, Inches(0))
            p = tf.paragraphs[0]
            p.alignment = p_align
            run = p.add_run()
            run.text = inline_plain(subtitle)
            _ppt_font_run(run, PPT_FONT, _ppt_size(th, "cover_sub"),
                          th["primary"] if not th["is_dark"] else th["accent"])
        else:
            _ppt_drop_shape(sub_ph)
    _ppt_drop_empty_placeholders(slide, keep=(0,))

    # 演讲人 / 日期
    if meta_lines:
        meta = "　　|　　".join([x for x in meta_lines if x])
        color = th["on_primary"] if style == "band" else th["muted"]
        my = cfg["meta_y"].get(style, cfg["meta_y"]["glow"])
        box = _ppt_textbox(slide, title_x, my, title_w, cfg["meta_h"])
        p = box.text_frame.paragraphs[0]
        p.alignment = p_align
        r = p.add_run()
        r.text = meta
        _ppt_font_run(r, PPT_FONT, _ppt_size(th, "cover_meta"), color)

    # 封面上的小标记
    if style in ("bar", "frame"):
        ky = cfg["kicker_y"].get(style, cfg["kicker_y"]["bar"])
        kick = _ppt_textbox(slide, title_x, ky, title_w, 0.4)
        p = kick.text_frame.paragraphs[0]
        p.alignment = p_align
        r = p.add_run()
        r.text = cfg["kicker_text"]
        _ppt_font_run(r, PPT_FONT, cfg["kicker_size"], th["accent"])
    return slide


def _ppt_toc(prs, th, entries, page_no, style=None):
    """目录页。``style``：``numbered``（默认，带两位序号）/ ``plain``（极简，无序号）。"""
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    tc = _PPT_GRID["toc"]
    tsp = _PPT_SPACING["toc"]
    plain = str(style or "numbered").strip().lower() == "plain"

    slide = _ppt_new_slide(prs, th, 5)
    _ppt_title(slide, th, "目 录")
    n = len(entries)
    two_col = n > tc["two_col_over"]
    per = (n + 1) // 2 if two_col else n
    col_w = (CONTENT_W - tc["col_gap"]) / 2.0 if two_col else CONTENT_W
    y0 = BODY_TOP + tc["top_offset"]
    row_h = min(tc["row_h_max"],
                (BODY_BOTTOM - y0 - tc["bottom_pad"]) / max(per, 1))
    text_w = col_w if plain else col_w - (tc["num_w"] + tsp["text_gap"])
    for i, (num, text) in enumerate(entries):
        ci = 0 if i < per else 1
        ri = i if ci == 0 else i - per
        x = MARGIN_X + ci * (col_w + tc["col_gap"])
        y = y0 + ri * row_h
        if not plain:
            # 序号
            nb = _ppt_textbox(slide, x, y, tc["num_w"],
                              row_h - tc["row_inner_pad"], anchor="m")
            p = nb.text_frame.paragraphs[0]
            r = p.add_run()
            r.text = num
            _ppt_font_run(r, PPT_FONT, _ppt_size(th, "toc", -tsp["num_size_sub"]),
                          th["accent"], bold=True)
        # 标题
        tx = x if plain else x + tc["num_w"]
        tb = _ppt_textbox(slide, tx, y, text_w,
                          row_h - tc["row_inner_pad"], anchor="m")
        p2 = tb.text_frame.paragraphs[0]
        r2 = p2.add_run()
        r2.text = inline_plain(text)
        size = _ppt_fit_size(r2.text, text_w, _ppt_size(th, "toc"),
                             tsp["size_min"])
        _ppt_font_run(r2, PPT_FONT, size, th["text"], bold=False)
        _ppt_rect(slide, x, y + row_h - tc["row_bottom_gap"],
                  col_w - tsp["text_gap"], tsp["rule_h"], fill=th["line"])
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_section(prs, th, num, title, page_no, subtitle=""):
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    sec = _PPT_SECTION
    deco = sec["deco"]
    slide = _ppt_new_slide(prs, th, 5)
    # 先删掉标题占位符（章节页要自己排版）
    ph = slide.shapes.title
    if ph is not None:
        _ppt_drop_shape(ph)

    style = th.get("cover_style")
    d = deco["panel"]
    nx, ny, tx = d["num_x"], d["num_y"], d["title_x"]
    if style == "glow":
        g = deco["glow"]
        _ppt_rect(slide, 0, 0, SLIDE_W, g["top_h"], fill=th["primary"])
        _ppt_rect(slide, g["oval_x"], g["oval_y"], g["oval"], g["oval"],
                  fill=th["bg_alt"], shape="oval")
        nx, ny = MARGIN_X + g["num_x_add"], g["num_y"]
        tx = MARGIN_X + g["title_x_add"]
    elif style == "frame":
        f = deco["frame"]
        _ppt_rect(slide, 0, 0, SLIDE_W, SLIDE_H, fill=th["bg_alt"])
        _ppt_rect(slide, f["border_inset"], f["border_inset"],
                  SLIDE_W - 2 * f["border_inset"],
                  SLIDE_H - 2 * f["border_inset"],
                  fill=None, line=th["accent"], line_w=0.9)
        nx, ny = SLIDE_W / 2 - f["num_x_off"], f["num_y"]
        tx = 0
    else:
        _ppt_rect(slide, 0, 0, d["panel_w"], SLIDE_H, fill=th["primary"])
        _ppt_rect(slide, d["panel_w"], 0, d["strip_w"], SLIDE_H,
                  fill=th["accent"])

    if style == "frame":
        box = _ppt_textbox(slide, nx, ny, 2.8, sec["num_box_h_center"], anchor="m")
        p = box.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = num
        _ppt_font_run(r, PPT_FONT, _ppt_size(th, "section_num"), th["accent"],
                      bold=True)
        tb = _ppt_textbox(slide, sec["frame_title_x"], sec["frame_title_y"],
                          SLIDE_W - sec["frame_title_inset"],
                          sec["frame_title_h"], anchor="m")
        p2 = tb.text_frame.paragraphs[0]
        p2.alignment = PP_ALIGN.CENTER
        r2 = p2.add_run()
        r2.text = inline_plain(title)
        _ppt_font_run(r2, PPT_FONT,
                      _ppt_size(th, "section_title", -sec["frame_title_sub"]),
                      th["primary"], bold=True)
        _ppt_rect(slide, SLIDE_W / 2 - sec["frame_rule_off"], sec["frame_rule_y"],
                  sec["frame_rule_w"], sec["frame_rule_h"], fill=th["accent"])
        if subtitle:
            sb = _ppt_textbox(slide, sec["frame_sub_inset"], sec["frame_sub_y"],
                              SLIDE_W - 2 * sec["frame_sub_inset"],
                              sec["frame_sub_h"], anchor="m")
            sp = sb.text_frame.paragraphs[0]
            sp.alignment = PP_ALIGN.CENTER
            sr = sp.add_run()
            sr.text = inline_plain(subtitle)
            _ppt_font_run(sr, PPT_FONT, sec["sub_size"], th["muted"])
    else:
        box = _ppt_textbox(slide, nx, ny, sec["num_box_w"], sec["num_box_h"],
                           anchor="m")
        p = box.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = num
        _ppt_font_run(r, PPT_FONT, _ppt_size(th, "section_num"),
                      th["on_primary"] if style != "glow" else th["primary"],
                      bold=True)
        _ppt_rect(slide, nx, ny + sec["rule_y_offset"], sec["rule_w"],
                  sec["rule_h"], fill=th["accent"])
        tw = SLIDE_W - tx - MARGIN_X
        tb = _ppt_textbox(slide, tx, sec["title_y"], tw, sec["title_h"],
                          anchor="m")
        p2 = tb.text_frame.paragraphs[0]
        r2 = p2.add_run()
        r2.text = inline_plain(title)
        size = _ppt_fit_size(r2.text, tw, _ppt_size(th, "section_title"),
                             sec["title_min_size"])
        _ppt_font_run(r2, PPT_FONT, size, th["text"], bold=True)
        if subtitle:
            sb = _ppt_textbox(slide, tx, sec["sub_y"], tw, sec["sub_h"])
            sp = sb.text_frame.paragraphs[0]
            sr = sp.add_run()
            sr.text = inline_plain(subtitle)
            _ppt_font_run(sr, PPT_FONT, sec["sub_size"], th["muted"])
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_table(slide, th, header, rows, x=MARGIN_X, y=BODY_TOP,
               w=CONTENT_W, h=None, max_size=None):
    """主题化表格：表头主色填充、斑马纹、自动列宽、行高自适应。"""
    from pptx.util import Inches, Pt
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    nrow = len(rows) + 1
    ncol = max([len(header)] + [len(r) for r in rows] + [1])
    tg = _PPT_GRID["table"]
    h = h or (BODY_BOTTOM - y)
    row_h = min(tg["row_h_max"], h / max(nrow, 1))
    base = max_size or _ppt_size(th, "table")
    # 行太多就压字号
    if row_h < 0.3:
        base = max(8, base - 2)
    shape = slide.shapes.add_table(nrow, ncol, Inches(x), Inches(y),
                                   Inches(w), Inches(row_h * nrow))
    tbl = shape.table
    try:
        tbl.first_row = True
        tbl.horz_banding = False
    except Exception:
        pass

    # 列宽：按内容宽度比例分配
    weights = []
    for j in range(ncol):
        widest = len(str(header[j] if j < len(header) else ""))
        for r in rows:
            if j < len(r):
                widest = max(widest, len(str(r[j])))
        weights.append(max(widest, tg["min_col_weight"]) ** 0.75)
    total = sum(weights) or 1.0
    widths = [max(w * 0.09, w * ww / total) for ww in weights]
    scale = w / sum(widths)
    for j in range(ncol):
        tbl.columns[j].width = Inches(widths[j] * scale)
    for i in range(nrow):
        tbl.rows[i].height = Inches(row_h)

    def style_cell(cell, text, is_head, ri):
        cell.margin_left = Inches(tg["cell_margin_x"])
        cell.margin_right = Inches(tg["cell_margin_x"])
        cell.margin_top = Inches(tg["cell_margin_y"])
        cell.margin_bottom = Inches(tg["cell_margin_y"])
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        if is_head:
            cell.fill.fore_color.rgb = _c(th["primary"])
        elif ri % 2 == 1:
            cell.fill.fore_color.rgb = _c(th["bg_alt"])
        else:
            cell.fill.fore_color.rgb = _c(th["bg"] if not th["is_dark"] else th["bg_alt"])
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER if is_head else PP_ALIGN.LEFT
        r = p.add_run()
        r.text = str(text)
        _ppt_font_run(r, PPT_FONT, base,
                      th["on_primary"] if is_head else th["text"],
                      bold=is_head)

    for j in range(ncol):
        style_cell(tbl.cell(0, j), header[j] if j < len(header) else "", True, 0)
    for ri, r_ in enumerate(rows):
        for j in range(ncol):
            style_cell(tbl.cell(ri + 1, j), r_[j] if j < len(r_) else "", False, ri)
    return shape


def _ppt_chart(slide, th, labels, values, series, x, y, w, h, ctype="bar"):
    """python-pptx 原生图表（不是截图），配色跟主题一致。"""
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
    from pptx.util import Inches, Pt

    cd = CategoryChartData()
    cd.categories = [str(x_) for x_ in labels]
    cd.add_series(series or "数值", [float(v) for v in values])
    kind = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
        "area": XL_CHART_TYPE.AREA,
    }.get((ctype or "bar").lower(), XL_CHART_TYPE.COLUMN_CLUSTERED)
    gf = slide.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd)
    chart = gf.chart
    try:
        chart.format.fill.background()
    except Exception:
        pass
    chart.has_legend = False
    chart.has_title = False
    try:
        chart.font.name = PPT_FONT
        chart.font.size = Pt(_ppt_size(th, "table"))
        chart.font.color.rgb = _c(th["text"])
    except Exception:
        pass
    plot = chart.plots[0]
    try:
        plot.gap_width = 55
    except Exception:
        pass
    try:
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.font.size = Pt(_ppt_size(th, "table", -1))
        dl.font.name = PPT_FONT
        dl.font.bold = True
        dl.font.color.rgb = _c(th["text"])
        dl.number_format = "0.#"
        dl.number_format_is_linked = False
        if kind != XL_CHART_TYPE.PIE:
            dl.position = XL_LABEL_POSITION.OUTSIDE_END
    except Exception:
        pass
    colors = th.get("chart_colors") or [th["primary"]]
    try:
        for si, ser in enumerate(plot.series):
            ser.format.fill.solid()
            ser.format.fill.fore_color.rgb = _c(colors[si % len(colors)])
            if kind == XL_CHART_TYPE.LINE_MARKERS:
                ser.format.line.color.rgb = _c(colors[si % len(colors)])
    except Exception:
        pass
    # 坐标轴
    try:
        ca = chart.category_axis
        ca.tick_labels.font.size = Pt(_ppt_size(th, "table", -1))
        ca.tick_labels.font.name = PPT_FONT
        ca.tick_labels.font.color.rgb = _c(th["muted"])
        ca.format.line.color.rgb = _c(th["line"])
        va = chart.value_axis
        va.has_major_gridlines = True
        va.major_gridlines.format.line.color.rgb = _c(th["line"])
        va.major_gridlines.format.line.width = Pt(0.5)
        va.tick_labels.font.size = Pt(_ppt_size(th, "table", -2))
        va.tick_labels.font.name = PPT_FONT
        va.tick_labels.font.color.rgb = _c(th["muted"])
        va.format.line.fill.background()
    except Exception:
        pass
    return gf


def _ppt_visual_card(slide, th, x, y, w, h, caption="图示区"):
    """没有真图时的「图示占位卡片」——看起来是设计过的版式，不是忘了放东西。"""
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    vc = _PPT_GRID["visual_card"]
    _ppt_rect(slide, x, y, w, h, fill=th["bg_alt"], line=th["line"],
              shape="round", line_w=1.0, dash=True)
    # 中间画一个简单的柱状示意
    for i, hh in enumerate(vc["bar_heights"]):
        bh = h * hh * vc["bar_height_ratio"]
        bw = w * vc["bar_width_ratio"]
        bx = x + w * (vc["bar_start_ratio"] + i * vc["bar_step_ratio"])
        _ppt_rect(slide, bx, y + h * vc["baseline_ratio"] - bh, bw, bh,
                  fill=th["primary"] if i % 2 == 0 else th["accent"])
    _ppt_rect(slide, x + w * vc["rule_x_ratio"], y + h * vc["baseline_ratio"],
              w * vc["rule_w_ratio"], vc["rule_h"], fill=th["line"])
    box = _ppt_textbox(slide, x + vc["caption_pad"],
                       y + h * vc["caption_ratio"], w - 2 * vc["caption_pad"],
                       h * vc["caption_h_ratio"])
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = caption
    _ppt_font_run(r, PPT_FONT, _PTT_TEXT["card_caption_size"], th["muted"])


def _ppt_two_col(prs, th, title, items, visual, page_no):
    """左文右图版式：左边 ``grid.two_col.left_w`` 英寸文字，右边余下宽度给图/图表/占位。"""
    from pptx.util import Inches
    g = _PPT_GRID["two_col"]
    slide = _ppt_new_slide(prs, th, 5)
    _ppt_title(slide, th, title)
    left_w = g["left_w"]
    _ppt_bullets_body(slide, th, items, x=MARGIN_X,
                      y=BODY_TOP + g["body_offset_y"], w=left_w,
                      h=BODY_BOTTOM - BODY_TOP - g["body_offset_y"])
    vx = MARGIN_X + left_w + g["gap"]
    vw = SLIDE_W - vx - MARGIN_X
    vy = BODY_TOP + g["visual_offset_y"]
    vh = BODY_BOTTOM - vy - g["visual_bottom_pad"]
    kind = visual.get("kind")
    if kind == "chart":
        _ppt_chart(slide, th, visual.get("labels"), visual.get("values"),
                   visual.get("series"), vx, vy, vw, vh, visual.get("type", "bar"))
    elif kind == "img":
        try:
            pic = slide.shapes.add_picture(visual["path"], Inches(vx), Inches(vy),
                                           width=Inches(vw))
            if pic.height > Inches(vh):
                ratio = vh / (pic.height / 914400.0)
                pic.height = Inches(vh)
                pic.width = int(pic.width * ratio)
                pic.left = Inches(vx + (vw - pic.width / 914400.0) / 2)
            else:
                pic.top = Inches(vy + (vh - pic.height / 914400.0) / 2)
        except Exception:
            _ppt_visual_card(slide, th, vx, vy, vw, vh, visual.get("caption") or "图示区")
    else:
        _ppt_visual_card(slide, th, vx, vy, vw, vh,
                         visual.get("caption") or "图示区")
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_table_slide(prs, th, title, header, rows, page_no, sub=""):
    tg = _PPT_GRID["table"]
    slide = _ppt_new_slide(prs, th, 5)
    _ppt_title(slide, th, title)
    y = BODY_TOP + tg["body_start_offset"]
    if sub:
        box = _ppt_textbox(slide, MARGIN_X, y + tg["sub_offset_y"], CONTENT_W,
                           tg["sub_h"])
        p = box.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = inline_plain(sub)
        _ppt_font_run(r, PPT_FONT, _PTT_TEXT["note_size"], th["muted"])
        y += tg["sub_advance_y"]
    _ppt_table(slide, th, header, rows, x=MARGIN_X, y=y, h=BODY_BOTTOM - y)
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_chart_slide(prs, th, title, labels, values, series, ctype, page_no,
                     note=""):
    slide = _ppt_new_slide(prs, th, 5)
    _ppt_title(slide, th, title)
    _ppt_chart(slide, th, labels, values, series, MARGIN_X, BODY_TOP + 0.1,
               CONTENT_W, BODY_BOTTOM - BODY_TOP - (0.55 if note else 0.1), ctype)
    if note:
        box = _ppt_textbox(slide, MARGIN_X, BODY_BOTTOM - 0.42, CONTENT_W, 0.35)
        p = box.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = inline_plain(note)
        _ppt_font_run(r, PPT_FONT, _PTT_TEXT["chart_note_size"], th["muted"])
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_code_slide(prs, th, title, code, page_no):
    from pptx.util import Inches
    cg = _PPT_GRID["code"]
    slide = _ppt_new_slide(prs, th, 5)
    _ppt_title(slide, th, title)
    y = BODY_TOP + 0.12
    h = BODY_BOTTOM - y
    _ppt_rect(slide, MARGIN_X, y, CONTENT_W, h,
              fill=th["bg_alt"], shape="round")
    box = _ppt_textbox(slide, MARGIN_X + cg["inner_pad_x"], y + cg["inner_pad_y"],
                       CONTENT_W - 2 * cg["inner_pad_x"],
                       h - 2 * cg["inner_pad_y"] + cg["box_h_extra"],
                       margins=(0.05, 0.05, 0.05, 0.05))
    tf = box.text_frame
    lines = (code or "").split("\n")[:cg["max_lines"]]
    # 按最长行估字号
    longest = max([text_em(ln) for ln in lines] + [1])
    size = min(cg["size_max"],
               max(cg["size_min"], (CONTENT_W - 0.5) * 72.0 / max(longest, 1)))
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = _PPT_SPACING["line_tight"]
        r = p.add_run()
        r.text = ln
        _ppt_font_run(r, PPT_FONT_MONO, size, th["text"])
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_image_slide(prs, th, title, path, caption, page_no):
    from pptx.util import Inches
    ig = _PPT_GRID["image"]
    slide = _ppt_new_slide(prs, th, 5)
    _ppt_title(slide, th, title)
    y = BODY_TOP + ig["top_offset"]
    h = BODY_BOTTOM - y - (ig["caption_h"] if caption else ig["no_caption_pad"])
    try:
        pic = slide.shapes.add_picture(path, Inches(MARGIN_X), Inches(y),
                                       height=Inches(h))
        if pic.width > Inches(CONTENT_W):
            ratio = CONTENT_W / (pic.width / 914400.0)
            pic.width = Inches(CONTENT_W)
            pic.height = int(pic.height * ratio)
        pic.left = Inches(MARGIN_X + (CONTENT_W - pic.width / 914400.0) / 2)
        pic.top = Inches(y + (h - pic.height / 914400.0) / 2)
    except Exception:
        _ppt_visual_card(slide, th, MARGIN_X, y, CONTENT_W, h, "图片加载失败")
    if caption:
        box = _ppt_textbox(slide, MARGIN_X, y + h + ig["caption_gap"], CONTENT_W,
                           0.35)
        p = box.text_frame.paragraphs[0]
        p.alignment = 2  # CENTER
        r = p.add_run()
        r.text = inline_plain(caption)
        _ppt_font_run(r, PPT_FONT, _PTT_TEXT["caption_size"], th["muted"])
    _ppt_footer(slide, th, page_no)
    return slide


def _ppt_end(prs, th, title, subtitle, page_no):
    from pptx.util import Inches
    from pptx.enum.text import PP_ALIGN
    ec = _PPT_END
    d = ec["deco"]
    slide = _ppt_new_slide(prs, th, 5)
    ph = slide.shapes.title
    if ph is not None:
        _ppt_drop_shape(ph)
    style = th.get("cover_style")
    if style == "glow":
        _ppt_rect(slide, d["glow"]["oval_x"], d["glow"]["oval_y"],
                  d["glow"]["oval"], d["glow"]["oval"], fill=th["bg_alt"],
                  shape="oval")
        _ppt_rect(slide, 0, 0, SLIDE_W, d["glow"]["top_h"], fill=th["primary"])
    elif style == "frame":
        _ppt_rect(slide, d["frame"]["inset"], d["frame"]["inset"],
                  SLIDE_W - 2 * d["frame"]["inset"],
                  SLIDE_H - 2 * d["frame"]["inset"],
                  fill=None, line=th["accent"], line_w=d["frame"]["line_w"])
    elif style == "band":
        _ppt_rect(slide, 0, d["band"]["band_y"], SLIDE_W, d["band"]["band_h"],
                  fill=th["primary_dark"])
    else:
        _ppt_rect(slide, 0, 0, d["panel"]["rail_w"], SLIDE_H, fill=th["primary"])

    box = _ppt_textbox(slide, MARGIN_X, ec["title_y"], CONTENT_W, ec["title_h"],
                       anchor="m")
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = inline_plain(title or "谢 谢")
    _ppt_font_run(r, PPT_FONT, _ppt_size(th, "end_title"), th["text"], bold=True)
    _ppt_rect(slide, SLIDE_W / 2 - ec["rule_w"] / 2, ec["rule_y"], ec["rule_w"],
              ec["rule_h"], fill=th["accent"])
    if subtitle:
        box2 = _ppt_textbox(slide, MARGIN_X + ec["sub_w_pad"], ec["sub_y"],
                            CONTENT_W - 2 * ec["sub_w_pad"], ec["sub_h"],
                            anchor="m")
        p2 = box2.text_frame.paragraphs[0]
        p2.alignment = PP_ALIGN.CENTER
        r2 = p2.add_run()
        r2.text = inline_plain(subtitle)
        _ppt_font_run(r2, PPT_FONT, ec["sub_size"], th["muted"])
    _ppt_footer(slide, th, page_no)
    return slide


# ---------------------------------------------------------------- 拆页

def _group_by_heading(blocks, level):
    """按「标题级别 <= level」切组，返回 [(title, [blocks])]。"""
    groups = []
    cur_title = None
    cur = []
    for b in blocks:
        if b["kind"] == "h" and b["level"] <= level:
            if cur_title is not None or cur:
                groups.append((cur_title, cur))
            cur_title = b["text"]
            cur = []
        else:
            cur.append(b)
    if cur_title is not None or cur:
        groups.append((cur_title, cur))
    return groups


def _items_from_blocks(items_blocks):
    """把一组 block 转成 (bullets, tables, charts, images, codes)。"""
    bullets, tables, charts, images, codes = [], [], [], [], []
    for b in items_blocks:
        k = b["kind"]
        if k == "h":
            bullets.append((b["text"], min(max(b["level"] - 2, 1), 2), False, False))
        elif k == "li":
            bullets.append((b["text"], min(b.get("level", 0), 2),
                            bool(b.get("ordered")), False))
        elif k == "p":
            bullets.append((b["text"], 0, False, True))
        elif k == "quote":
            bullets.append((b["text"], 0, False, True))
        elif k == "table":
            tables.append(b)
        elif k == "chart":
            charts.append(b)
        elif k == "img":
            images.append(b)
        elif k == "code":
            codes.append(b)
    return bullets, tables, charts, images, codes


def _build_plan(blocks, title=None, base_dir=None):
    """把 blocks 规划成一份「幻灯片清单」（纯数据，不含 pptx 对象）。"""
    h1 = [i for i, b in enumerate(blocks) if b["kind"] == "h" and b["level"] == 1]
    h2 = [i for i, b in enumerate(blocks) if b["kind"] == "h" and b["level"] == 2]

    deck_title = (title or "").strip()
    chapters = []          # [(title, [blocks])]
    force_sections = False

    if len(h1) == 1 and len(h2) >= 2:
        # 一个 H1 当演示标题，H2 当单页
        if not deck_title:
            deck_title = blocks[h1[0]]["text"]
        body = blocks[h1[0] + 1:]
        chapters = _group_by_heading(body, 2)
    elif len(h1) >= 2:
        if deck_title:
            chapters = _group_by_heading(blocks, 1)
        else:
            deck_title = blocks[h1[0]]["text"]
            chapters = _group_by_heading(blocks[h1[0] + 1:], 1)
        force_sections = True
    elif len(h2) >= 1:
        if not deck_title:
            deck_title = "演示文稿"
        chapters = _group_by_heading(blocks, 2)
    else:
        if not deck_title:
            deck_title = "演示文稿"
        chapters = [(None, blocks)]

    # 丢掉空组
    chapters = [(t, bs) for (t, bs) in chapters
                if (t or "").strip() or any(b.get("text") for b in bs)]
    # 开头的单独引用块是「副标题」，不单独占一页
    if chapters and not (chapters[0][0] or "").strip():
        bs0 = chapters[0][1]
        if len(bs0) == 1 and bs0[0]["kind"] == "quote":
            chapters = chapters[1:]
    if not chapters:
        chapters = [(None, [])]

    # 是否插章节过渡页
    chapter_like = sum(1 for t, _ in chapters if t and _CHAPTER_RE.match(t))
    use_sections = force_sections or chapter_like >= max(2, len(chapters) // 2)

    # 目录条目
    toc = []
    for i, (t, _bs) in enumerate(chapters, start=1):
        if t:
            toc.append((f"{i:02d}", inline_plain(t)))

    # 拆成幻灯片清单
    plan = [{"kind": "cover", "title": deck_title}]
    if len(toc) >= 2:
        plan.append({"kind": "toc", "entries": toc})

    sec_no = 0
    for t, bs in chapters:
        bullets, tables, charts, images, codes = _items_from_blocks(bs)
        ch_title = inline_plain(t) if t else (deck_title or "概览")
        need_section = bool(t) and (use_sections or _CHAPTER_RE.match(t or ""))
        if need_section:
            sec_no += 1
            plan.append({"kind": "section", "num": f"{sec_no:02d}",
                         "title": ch_title})
        if not (bullets or tables or charts or images or codes):
            if not need_section:
                plan.append({"kind": "bullets", "title": ch_title,
                             "items": [(f"（{ch_title} 暂无正文）", 0, False, True)]})
            continue

        # 单图 + 少量文字 → 两栏图文
        if len(images) == 1 and len(bullets) <= 4 and not tables:
            plan.append({"kind": "two_col", "title": ch_title,
                         "items": bullets, "visual": {"kind": "img",
                                                      "block": images[0]}})
            images = []
            bullets = []
        elif len(charts) == 1 and len(bullets) <= 4 and not tables:
            plan.append({"kind": "two_col", "title": ch_title,
                         "items": bullets,
                         "visual": {"kind": "chart", "block": charts[0]}})
            charts = []
            bullets = []
        elif len(tables) == 1 and len(bullets) <= 4 and not images \
                and not charts and _is_chartable_table(tables[0]):
            plan.append({"kind": "two_col", "title": ch_title,
                         "items": bullets,
                         "visual": {"kind": "chart", "block": tables[0],
                                    "from_table": True}})
            tables = []
            bullets = []

        # 文字页：自动分页
        if bullets:
            pages = split_pages(bullets, max_items=6, max_chars=250)
            for pi, page in enumerate(pages):
                plan.append({"kind": "bullets",
                             "title": ch_title + ("（续）" if pi else ""),
                             "items": page})

        for ti, tb in enumerate(tables):
            plan.append({"kind": "table", "title": ch_title,
                         "header": tb.get("header") or [],
                         "rows": tb.get("rows") or []})

        for ci, cb in enumerate(charts):
            plan.append({"kind": "chart", "title": ch_title,
                         "labels": cb.get("labels") or [],
                         "values": cb.get("values") or [],
                         "series": cb.get("series") or ch_title,
                         "type": cb.get("type") or "bar"})

        for im in images:
            ip = _resolve_img(im.get("src"), base_dir)
            plan.append({"kind": "image", "title": ch_title,
                         "path": ip, "caption": im.get("alt") or ""})

        for cb in codes:
            plan.append({"kind": "code", "title": ch_title,
                         "code": cb.get("text") or ""})

    plan.append({"kind": "end", "title": "谢 谢",
                 "subtitle": "欢迎交流与提问"})
    return {"title": deck_title, "plan": plan}


def _is_chartable_table(tb):
    """2 列 + 第二列全是数字 + 至少 3 行 → 值得画柱状图。"""
    header = tb.get("header") or []
    rows = tb.get("rows") or []
    if len(header) != 2 or len(rows) < 3:
        return False
    for r in rows:
        if len(r) < 2 or _to_num(r[1]) is None:
            return False
    return True


def write_pptx(path, blocks, title=None, base_dir=None, theme=None,
               author=None, subtitle=None, date=None, template=None, **kw):
    """把 blocks 写成主题化的 .pptx 演示文稿。

    版式：封面 / 目录 / 章节过渡 / 要点 / 两栏图文 / 表格 / 原生图表 / 代码 / 结尾。
    几何与字号来自 ``app/officetpl/pptx_spec.py``，配色来自 ``themes.py``。

    参数 ``template`` 是「成品模板」（汇报蓝 / 科技深色 / 国风红 / 极简灰），
    用来一次性选定主题 + 版式取向；显式传 ``theme`` 时以 ``theme`` 为准。
    """
    from pptx import Presentation
    from pptx.util import Inches

    # 模板预设：先定主题，再定版式取向（没传 template 时全部走历史默认行为）
    preset = get_preset(template) if template else None
    theme_arg = theme if theme else (preset["theme"] if preset else None)
    th = get_theme(theme_arg)
    cover_align = (preset or {}).get("cover_align") or "left"
    toc_style = (preset or {}).get("toc_style") or "numbered"

    prs = Presentation()
    prs.slide_width = Inches(_PPT_SPEC["slide"]["width_in"])
    prs.slide_height = Inches(_PPT_SPEC["slide"]["height_in"])

    deck = _build_plan(blocks, title=title, base_dir=base_dir)
    deck_title = deck["title"]
    plan = deck["plan"]

    # 封面副标题：显式参数 > 正文第一个引用 > 空
    sub = subtitle
    if not sub:
        for b in blocks:
            if b["kind"] == "quote" and b.get("text"):
                sub = inline_plain(b["text"])[:48]
                break
    meta = []
    if author:
        meta.append(f"主讲：{author}")
    if date:
        meta.append(str(date))

    page_no = 0
    for item in plan:
        page_no += 1
        kind = item["kind"]
        try:
            if kind == "cover":
                _ppt_cover(prs, th, item["title"], sub, meta, align=cover_align)
            elif kind == "toc":
                _ppt_toc(prs, th, item["entries"], page_no, style=toc_style)
            elif kind == "section":
                _ppt_section(prs, th, item["num"], item["title"], page_no)
            elif kind == "bullets":
                slide = _ppt_new_slide(prs, th, 5)
                _ppt_title(slide, th, item["title"])
                _ppt_bullets_body(slide, th, item["items"])
                _ppt_footer(slide, th, page_no)
            elif kind == "two_col":
                vis = dict(item["visual"])
                if vis.get("from_table"):
                    tb = vis["block"]
                    vis = {"kind": "chart",
                           "labels": [r[0] for r in (tb.get("rows") or [])],
                           "values": [_to_num(r[1]) or 0 for r in (tb.get("rows") or [])],
                           "series": (tb.get("header") or ["", ""])[1],
                           "type": "bar"}
                elif vis.get("kind") == "img":
                    vis["path"] = _resolve_img(vis["block"].get("src"), base_dir)
                    vis["caption"] = vis["block"].get("alt") or "图示区"
                else:
                    cb = vis["block"]
                    vis = {"kind": "chart",
                           "labels": cb.get("labels") or [],
                           "values": cb.get("values") or [],
                           "series": cb.get("series") or item["title"],
                           "type": cb.get("type") or "bar"}
                _ppt_two_col(prs, th, item["title"], item["items"], vis, page_no)
            elif kind == "table":
                _ppt_table_slide(prs, th, item["title"], item["header"],
                                 item["rows"], page_no)
            elif kind == "chart":
                _ppt_chart_slide(prs, th, item["title"], item["labels"],
                                 item["values"], item["series"],
                                 item["type"], page_no,
                                 note=f"数据来源：{item['series']}")
            elif kind == "image":
                _ppt_image_slide(prs, th, item["title"], item["path"],
                                 item["caption"], page_no)
            elif kind == "code":
                _ppt_code_slide(prs, th, item["title"], item["code"], page_no)
            elif kind == "end":
                _ppt_end(prs, th, item["title"], item.get("subtitle"), page_no)
        except Exception:
            # 单页失败不影响整份：补一张纯文本页
            try:
                slide = _ppt_new_slide(prs, th, 5)
                _ppt_title(slide, th, item.get("title") or deck_title)
                _ppt_bullets_body(slide, th, [("（本页内容渲染失败，已降级）", 0, False, True)])
                _ppt_footer(slide, th, page_no)
            except Exception:
                pass

    # 去掉可能残留的空占位符（安全网）
    for slide in prs.slides:
        for ph in list(slide.placeholders):
            try:
                if not (ph.text_frame.text or "").strip():
                    _ppt_drop_shape(ph)
            except Exception:
                continue

    try:
        prs.core_properties.title = deck_title
        prs.core_properties.author = author or "AIWorkbench"
        subject = f"主题：{th['name']}"
        if preset:
            subject += f"；模板：{preset['name']}（{preset['key']}）"
        prs.core_properties.subject = subject
        prs.core_properties.comments = (
            "由 AIWorkbench 生成（app/officetpl/pptx_spec.py 版式规格 + "
            "themes.py 主题配色）")
    except Exception:
        pass
    prs.save(path)
    return path


# ================================================================ PDF

def _pdf_font():
    """注册中文字体：优先系统 msyh.ttc，失败则用 reportlab 内置 STSong-Light。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                 r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"):
        try:
            if os.path.isfile(cand):
                pdfmetrics.registerFont(TTFont("CNFont", cand))
                return "CNFont"
        except Exception:
            continue
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    except Exception:
        return "Helvetica"


def write_pdf(path, blocks, title=None, base_dir=None, theme=None, **kw):
    """A4 PDF：标题层级 + 表格 + 图片 + 页脚页码。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image, Preformatted, PageBreak)
    from reportlab.lib.enums import TA_CENTER

    th = get_theme(theme)
    font = _pdf_font()
    ss = getSampleStyleSheet()
    accent = colors.HexColor("#" + _hex_rgb(th["primary"]))
    body = ParagraphStyle("bodyCN", parent=ss["BodyText"], fontName=font,
                          fontSize=10.5, leading=17, spaceAfter=6,
                          firstLineIndent=21)
    h1 = ParagraphStyle("h1CN", parent=body, fontSize=20, leading=26,
                        spaceBefore=14, spaceAfter=10, textColor=accent,
                        firstLineIndent=0)
    h2 = ParagraphStyle("h2CN", parent=body, fontSize=16, leading=22,
                        spaceBefore=12, spaceAfter=8, textColor=accent,
                        firstLineIndent=0)
    h3 = ParagraphStyle("h3CN", parent=body, fontSize=13, leading=19,
                        spaceBefore=10, spaceAfter=6,
                        textColor=colors.HexColor("#3A4A5C"),
                        firstLineIndent=0)
    quote = ParagraphStyle("qCN", parent=body, leftIndent=12,
                           textColor=colors.HexColor("#555555"),
                           firstLineIndent=0)
    code = ParagraphStyle("cCN", parent=ss["Code"], fontName=font, fontSize=9,
                          leading=13, backColor=colors.HexColor("#F5F5F5"))
    tstyle = ParagraphStyle("tCN", parent=body, fontSize=9.5, leading=13,
                            firstLineIndent=0)
    tstyle_h = ParagraphStyle("thCN", parent=tstyle, fontName=font,
                              textColor=colors.white)
    doc_title = ParagraphStyle("dtCN", parent=h1, fontSize=24, leading=32,
                               alignment=TA_CENTER, spaceAfter=20)

    def esc(s):
        return (inline_plain(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    flow = []
    if title:
        flow.append(Paragraph(esc(title), doc_title))
    for b in blocks:
        k = b["kind"]
        try:
            if k == "h":
                st = {1: h1, 2: h2}.get(b["level"], h3)
                flow.append(Paragraph(esc(b["text"]), st))
            elif k == "p":
                flow.append(Paragraph(esc(b["text"]), body))
            elif k == "li":
                indent = 12 + b.get("level", 0) * 14
                mark = "•" if not b.get("ordered") else "-"
                flow.append(Paragraph(f"{mark} {esc(b['text'])}",
                                      ParagraphStyle("li", parent=body,
                                                     leftIndent=indent,
                                                     firstLineIndent=0)))
            elif k == "quote":
                flow.append(Paragraph(esc(b["text"]), quote))
            elif k == "code":
                from xml.sax.saxutils import escape as xesc
                flow.append(Preformatted(xesc(b["text"]), code))
                flow.append(Spacer(1, 4))
            elif k == "hr":
                flow.append(Spacer(1, 6))
            elif k == "chart":
                pairs = list(zip(b.get("labels") or [], b.get("values") or []))
                data = [[Paragraph(esc(lb), tstyle_h) for lb, _ in pairs],
                        [Paragraph(f"{v:g}", tstyle) for _, v in pairs]]
                t = Table(data)
                t.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFC9D4")),
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ]))
                flow.append(t)
                flow.append(Spacer(1, 8))
            elif k == "img":
                ip = _resolve_img(b.get("src"), base_dir)
                if ip:
                    try:
                        img = Image(ip)
                        iw = img.imageWidth
                        maxw = 160 * mm
                        if iw > maxw:
                            img.drawHeight *= maxw / iw
                            img.drawWidth = maxw
                        flow.append(img)
                        flow.append(Spacer(1, 6))
                    except Exception:
                        flow.append(Paragraph("[图片]" + esc(b.get("alt") or ""), body))
            elif k == "table":
                header = [Paragraph(esc(c), tstyle_h) for c in (b.get("header") or [])]
                rows = [[Paragraph(esc(c), tstyle) for c in r] for r in (b.get("rows") or [])]
                data = ([header] if header else []) + rows
                if data:
                    ncol = max(len(r) for r in data)
                    for r in data:
                        while len(r) < ncol:
                            r.append(Paragraph("", tstyle))
                    t = Table(data, repeatRows=1 if header else 0)
                    t.setStyle(TableStyle([
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
                        ("BACKGROUND", (0, 0), (-1, 0),
                         accent if header else colors.white),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]))
                    flow.append(t)
                    flow.append(Spacer(1, 8))
        except Exception:
            try:
                flow.append(Paragraph(esc(b.get("text") or ""), body))
            except Exception:
                pass
    if not flow:
        flow = [Paragraph("（空文档）", body)]

    def on_page(canv, doc_):
        canv.saveState()
        canv.setFont(font, 8.5)
        canv.setFillColor(colors.HexColor("#6B7A8C"))
        canv.drawRightString(A4[0] - 18 * mm, 12 * mm, f"{doc_.page}")
        canv.setStrokeColor(colors.HexColor("#D6E1EE"))
        canv.line(18 * mm, 16 * mm, A4[0] - 18 * mm, 16 * mm)
        canv.restoreState()

    d = SimpleDocTemplate(path, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=18 * mm, bottomMargin=20 * mm,
                          title=title or "文档")
    d.build(flow, onFirstPage=on_page, onLaterPages=on_page)
    return path


# ================================================================ 统一入口

FORMATS = {
    ".docx": ("Word 文档", write_docx),
    ".doc": ("Word 文档", write_docx),
    ".xlsx": ("Excel 表格", write_xlsx),
    ".xls": ("Excel 表格", write_xlsx),
    ".pptx": ("PowerPoint 演示文稿", write_pptx),
    ".ppt": ("PowerPoint 演示文稿", write_pptx),
    ".pdf": ("PDF 文档", write_pdf),
}


def supported(path):
    return os.path.splitext(path or "")[1].lower() in FORMATS


def format_label(path):
    return FORMATS.get(os.path.splitext(path or "")[1].lower(), ("未知", None))[0]


def create(path, content, title=None, base_dir=None, theme=None, author=None,
           subtitle=None, date=None, template=None):
    """按扩展名把 Markdown 内容写成目标文档，返回 (描述, 字节数)。

    新增参数全部可选，老调用 ``create(path, content, title=, base_dir=)`` 行为不变。
    ``template``（PPT 成品模板：汇报蓝 / 科技深色 / 国风红 / 极简灰）只对 .pptx 生效，
    其它格式会忽略（它们的 writer 收 **kw）。
    """
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in FORMATS:
        raise ValueError(f"不支持生成 {ext or '（无扩展名）'}，"
                         f"支持：{', '.join(sorted(FORMATS))}")
    blocks = parse_blocks(content or "")
    label, writer = FORMATS[ext]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    writer(path, blocks, title=title, base_dir=base_dir, theme=theme,
           author=author, subtitle=subtitle, date=date, template=template)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return label, size
