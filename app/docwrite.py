# -*- coding: utf-8 -*-
"""文档生成：把 Markdown 写成 Word(.docx) / Excel(.xlsx) / PPT(.pptx) / PDF(.pdf)。

设计要点：
- 只依赖一个「极简 Markdown 解析器」，不做完整 CommonMark，够用即可；
- 每种格式各自落地，任何一段失败都降级为纯文本，绝不让整篇生成失败；
- 中文字体显式指定，避免 Word/PDF 里变方块。
"""
import os
import re

# ---------------------------------------------------------------- Markdown 解析

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

    每个块是 dict，kind ∈ {h, p, li, table, code, img, quote, hr}
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()

        # 代码围栏
        m = re.match(r"^\s*```+\s*([\w+.-]*)\s*$", line)
        if m:
            lang = m.group(1)
            i += 1
            buf = []
            while i < n and not re.match(r"^\s*```+\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            blocks.append({"kind": "code", "lang": lang, "text": "\n".join(buf)})
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
            text_ = m.group(3) if ordered else m.group(3)
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


# ---------------------------------------------------------------- Word (.docx)

_CN_FONT = "微软雅黑"


def _set_east_asian(style_or_run_font_holder, font_name=_CN_FONT):
    try:
        from docx.oxml.ns import qn
        rpr = style_or_run_font_holder._element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            from docx.oxml import OxmlElement
            rf = OxmlElement("w:rFonts")
            rpr.append(rf)
        rf.set(qn("w:eastAsia"), font_name)
    except Exception:
        pass


def write_docx(path, blocks, title=None, base_dir=None):
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = _CN_FONT
    normal.font.size = Pt(10.5)
    _set_east_asian(normal)

    if title:
        h = doc.add_heading("", level=0)
        run = h.add_run(title)
        run.font.name = _CN_FONT
        _set_east_asian(run)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def add_para(text, style=None, bold=False, italic=False, code=False,
                 level=0, ordered=None, idx=None):
        p = doc.add_paragraph(style=style)
        if level:
            p.paragraph_format.left_indent = Inches(0.25 * level)
        if ordered:
            p.add_run(f"{idx}. ")
        for seg, b, it, cd in inline_runs(text):
            r = p.add_run(seg)
            r.bold = bool(b or bold)
            r.italic = bool(it or italic)
            if cd or code:
                r.font.name = "Consolas"
                r.font.color.rgb = RGBColor(0xB0, 0x30, 0x60)
        return p

    for b in blocks:
        k = b["kind"]
        try:
            if k == "h":
                lvl = min(max(b["level"], 1), 4)
                h = doc.add_heading("", level=lvl)
                r = h.add_run(inline_plain(b["text"]))
                r.font.name = _CN_FONT
                _set_east_asian(r)
            elif k == "p":
                add_para(b["text"])
            elif k == "li":
                if b.get("ordered"):
                    add_para(b["text"], style="List Number", level=b.get("level", 0))
                else:
                    add_para(b["text"], style="List Bullet", level=b.get("level", 0))
            elif k == "quote":
                p = add_para(b["text"], style="Intense Quote")
            elif k == "code":
                p = doc.add_paragraph()
                r = p.add_run(b["text"])
                r.font.name = "Consolas"
                r.font.size = Pt(9)
                p.paragraph_format.left_indent = Inches(0.3)
            elif k == "table":
                header = b.get("header") or []
                rows = b.get("rows") or []
                ncol = max([len(header)] + [len(r) for r in rows] + [1])
                t = doc.add_table(rows=1, cols=ncol)
                t.style = "Light Grid Accent 1"
                for j in range(ncol):
                    cell = t.rows[0].cells[j]
                    cell.text = ""
                    run = cell.paragraphs[0].add_run(
                        inline_plain(header[j]) if j < len(header) else "")
                    run.bold = True
                    run.font.name = _CN_FONT
                for r_ in rows:
                    cells = t.add_row().cells
                    for j in range(ncol):
                        cells[j].text = ""
                        run = cells[j].paragraphs[0].add_run(
                            inline_plain(r_[j]) if j < len(r_) else "")
                        run.font.size = Pt(9.5)
                        run.font.name = _CN_FONT
                doc.add_paragraph()
            elif k == "img":
                ip = _resolve_img(b.get("src"), base_dir)
                if ip:
                    try:
                        doc.add_picture(ip, width=Inches(5.6))
                    except Exception:
                        doc.add_paragraph("[图片] " + (b.get("alt") or b.get("src") or ""))
                else:
                    doc.add_paragraph("[图片] " + (b.get("alt") or b.get("src") or ""))
            elif k == "hr":
                doc.add_paragraph("―" * 20)
        except Exception:
            try:
                doc.add_paragraph(inline_plain(b.get("text") or b.get("alt") or ""))
            except Exception:
                pass
    doc.save(path)
    return path


# ---------------------------------------------------------------- Excel (.xlsx)

def write_xlsx(path, blocks, title=None, base_dir=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF", size=10.5, name=_CN_FONT)
    head_fill = PatternFill("solid", fgColor="4472C4")
    cell_font = Font(size=10.5, name=_CN_FONT)
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    tables = []
    pending_title = title
    for b in blocks:
        if b["kind"] == "h":
            pending_title = inline_plain(b["text"])
        elif b["kind"] == "table":
            tables.append((pending_title or f"表{len(tables)+1}",
                           b.get("header") or [], b.get("rows") or []))
            pending_title = None

    def sheet_name(s, used):
        s = re.sub(r"[\\/*?:\[\]]", "_", (s or "Sheet"))[:28] or "Sheet"
        name, k = s, 2
        while name in used:
            name = f"{s[:26]}_{k}"
            k += 1
        used.add(name)
        return name

    def style_sheet(ws, header, rows):
        if header:
            ws.append(header)
            for c in ws[1]:
                c.font = head_font
                c.fill = head_fill
                c.alignment = Alignment(horizontal="center", vertical="center")
                c.border = border
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows(min_row=2 if header else 1):
            for c in row:
                c.font = cell_font
                c.alignment = Alignment(vertical="top", wrap_text=True)
                c.border = border
        # 列宽自适应
        widths = {}
        for row in ws.iter_rows():
            for c in row:
                v = "" if c.value is None else str(c.value)
                w = sum(2 if ord(ch) > 127 else 1 for ch in v)
                widths[c.column] = max(widths.get(c.column, 8), min(w + 2, 60))
        for col, w in widths.items():
            ws.column_dimensions[
                __import__("openpyxl").utils.get_column_letter(col)].width = w
        ws.freeze_panes = ws.cell(row=2 if header else 1, column=1)

    used = set()
    if tables:
        wb.remove(wb.active)
        for name, header, rows in tables:
            ws = wb.create_sheet(sheet_name(name, used))
            style_sheet(ws, header, rows)
    else:
        # 没有表格：整篇当纯文本，一行一格
        ws = wb.active
        ws.title = sheet_name(title or "内容", used)
        if title:
            ws.append([title])
            ws["A1"].font = Font(bold=True, size=13, name=_CN_FONT)
        for b in blocks:
            if b["kind"] == "h":
                ws.append([("　" * (b["level"] - 1)) + inline_plain(b["text"])])
            elif b["kind"] in ("p", "quote"):
                ws.append([inline_plain(b["text"])])
            elif b["kind"] == "li":
                ws.append(["　" * (b.get("level", 0) * 2)
                           + ("• " if not b.get("ordered") else f"{b.get('level',0)+1}. ")
                           + inline_plain(b["text"])])
            elif b["kind"] == "code":
                for ln in b["text"].split("\n"):
                    ws.append([ln])
        for row in ws.iter_rows():
            for c in row:
                c.font = cell_font
                c.alignment = Alignment(vertical="top", wrap_text=False)
        ws.column_dimensions["A"].width = 90
        ws.freeze_panes = "A2"
    wb.save(path)
    return path


# ---------------------------------------------------------------- PPT (.pptx)

def write_pptx(path, blocks, title=None, base_dir=None):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    def fill_title(slide, text):
        slide.shapes.title.text = inline_plain(text)
        for p in slide.shapes.title.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(30)
                r.font.bold = True
                r.font.name = _CN_FONT
                r.font.color.rgb = RGBColor(0x1F, 0x36, 0x5D)

    def content_ph(slide):
        for ph in slide.placeholders:
            if ph.placeholder_format.idx != 0:
                return ph
        return None

    def add_bullets(slide, bullets):
        ph = content_ph(slide)
        tf = ph.text_frame
        tf.word_wrap = True
        first = True
        for text, lvl in bullets:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            p.text = inline_plain(text)
            p.level = min(lvl, 4)
            for r in p.runs:
                r.font.size = Pt(20 if lvl == 0 else 17)
                r.font.name = _CN_FONT
                r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    # 把 blocks 切成若干「幻灯片」：h1 开新页并作标题
    slides = []          # [ (title, [items]) ]，item = ("bullet"|"para"|"img"|"table"|"code", payload)
    cur = None
    for b in blocks:
        if b["kind"] == "h" and b["level"] == 1:
            cur = (b["text"], [])
            slides.append(cur)
        elif b["kind"] == "h" and cur is None:
            cur = (b["text"], [])
            slides.append(cur)
        elif b["kind"] == "h":
            cur[1].append(("bullet", (b["text"], 1)))
        else:
            if cur is None:
                cur = (title or "演示文稿", [])
                slides.append(cur)
            cur[1].append((b["kind"], b))

    if not slides:
        slides = [(title or "演示文稿", [])]

    for stitle, items in slides:
        layout = prs.slide_layouts[1]      # 标题 + 内容
        slide = prs.slides.add_slide(layout)
        fill_title(slide, stitle)
        bullets = []
        for kind, payload in items:
            if kind in ("li", "p", "quote"):
                lvl = payload.get("level", 0) if kind == "li" else 0
                bullets.append((payload["text"], lvl))
            elif kind == "bullet":
                bullets.append(payload)
            elif kind == "table":
                if bullets:
                    add_bullets(slide, bullets)
                    bullets = []
                lay = prs.slide_layouts[5] if len(prs.slide_layouts) > 5 else layout
                ns = prs.slides.add_slide(lay)
                try:
                    ns.shapes.title.text = inline_plain(stitle) + " 表"
                except Exception:
                    pass
                header = payload.get("header") or []
                rows = payload.get("rows") or []
                ncol = max([len(header)] + [len(r) for r in rows] + [1])
                nrow = len(rows) + 1
                gt = ns.shapes.add_table(nrow, ncol, Inches(0.6), Inches(1.6),
                                         prs.slide_width - Inches(1.2),
                                         Inches(0.4) * nrow)
                tbl = gt.table
                for j in range(ncol):
                    tbl.cell(0, j).text = inline_plain(header[j]) if j < len(header) else ""
                for ri, r_ in enumerate(rows):
                    for j in range(ncol):
                        tbl.cell(ri + 1, j).text = inline_plain(r_[j]) if j < len(r_) else ""
                for row in tbl.rows:
                    for c in row.cells:
                        for p in c.text_frame.paragraphs:
                            for r in p.runs:
                                r.font.size = Pt(12)
                                r.font.name = _CN_FONT
            elif kind == "code":
                if bullets:
                    add_bullets(slide, bullets)
                    bullets = []
                ns = prs.slides.add_slide(layout)
                fill_title(ns, stitle + "（代码）")
                tb = ns.shapes.add_textbox(Inches(0.6), Inches(1.5),
                                           prs.slide_width - Inches(1.2), Inches(5.2))
                tf = tb.text_frame
                tf.word_wrap = True
                tf.text = payload["text"][:1800]
                for p in tf.paragraphs:
                    for r in p.runs:
                        r.font.name = "Consolas"
                        r.font.size = Pt(12)
            elif kind == "img":
                ip = _resolve_img(payload.get("src"), base_dir)
                if ip:
                    if bullets:
                        add_bullets(slide, bullets)
                        bullets = []
                    ns = prs.slides.add_slide(layout)
                    fill_title(ns, stitle)
                    try:
                        ns.shapes.add_picture(ip, Inches(1.2), Inches(1.8),
                                              height=Inches(5.0))
                    except Exception:
                        pass
        if bullets:
            add_bullets(slide, bullets)
        elif not items:
            pass
    prs.save(path)
    return path


# ---------------------------------------------------------------- PDF

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


def write_pdf(path, blocks, title=None, base_dir=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image, Preformatted)
    from reportlab.lib.enums import TA_CENTER

    font = _pdf_font()
    ss = getSampleStyleSheet()
    body = ParagraphStyle("bodyCN", parent=ss["BodyText"], fontName=font,
                          fontSize=10.5, leading=16, spaceAfter=6)
    h1 = ParagraphStyle("h1CN", parent=body, fontSize=20, leading=26,
                        spaceBefore=14, spaceAfter=10, textColor=colors.HexColor("#1F365D"))
    h2 = ParagraphStyle("h2CN", parent=body, fontSize=16, leading=22,
                        spaceBefore=12, spaceAfter=8, textColor=colors.HexColor("#2E5A88"))
    h3 = ParagraphStyle("h3CN", parent=body, fontSize=13, leading=19,
                        spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#3A6EA5"))
    quote = ParagraphStyle("qCN", parent=body, leftIndent=12,
                           textColor=colors.HexColor("#555555"))
    code = ParagraphStyle("cCN", parent=ss["Code"], fontName=font, fontSize=9,
                          leading=13, backColor=colors.HexColor("#F5F5F5"))
    tstyle = ParagraphStyle("tCN", parent=body, fontSize=9.5, leading=13)
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
                                      ParagraphStyle("li", parent=body, leftIndent=indent)))
            elif k == "quote":
                flow.append(Paragraph(esc(b["text"]), quote))
            elif k == "code":
                from xml.sax.saxutils import escape as xesc
                flow.append(Preformatted(xesc(b["text"]), code))
                flow.append(Spacer(1, 4))
            elif k == "hr":
                flow.append(Spacer(1, 6))
            elif k == "img":
                ip = _resolve_img(b.get("src"), base_dir)
                if ip:
                    try:
                        img = Image(ip)
                        iw, ih = img.imageWidth, img.imageHeight
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
                         colors.HexColor("#4472C4") if header else colors.white),
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
    d = SimpleDocTemplate(path, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=18 * mm, bottomMargin=18 * mm,
                          title=title or "文档")
    d.build(flow)
    return path


# ---------------------------------------------------------------- 统一入口

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


def create(path, content, title=None, base_dir=None):
    """按扩展名把 Markdown 内容写成目标文档，返回 (描述, 字节数)。"""
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in FORMATS:
        raise ValueError(f"不支持生成 {ext or '（无扩展名）'}，"
                         f"支持：{', '.join(sorted(FORMATS))}")
    blocks = parse_blocks(content or "")
    label, writer = FORMATS[ext]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    writer(path, blocks, title=title, base_dir=base_dir)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return label, size
