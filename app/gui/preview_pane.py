# -*- coding: utf-8 -*-
"""内嵌浏览器面板里的「本地文件预览」控件。

背景：右侧浏览器面板原来只能显示网页。用户希望能直接在面板里看图片、
Word、PPT、Excel、PDF 和常见文本，不必另外打开外部程序。

设计取舍：
  · 用 ``QTextBrowser`` 渲染生成的 HTML，而不是再开一个 QWebEngineView。
    预览不需要 JS、不需要联网，少一个 Chromium 渲染进程，内存和启动都轻。
  · 图片用 ``QLabel + QPixmap``，外面套 ``QScrollArea``，等比缩放且大图可滚动。
  · 所有第三方解析库（python-docx / python-pptx / openpyxl / pdfminer）
    一律**函数内延迟导入**：模块被 import 时绝不因为缺库而失败；
    单种格式解析失败也只影响这一个标签，显示中文说明页，绝不把异常抛到 UI。
  · 所有颜色取自 ``app.themes.tokens()``，深浅色主题切换后 ``refresh_theme()``
    会重新生成一次内容，保证文字始终看得清。
  · 本模块不 import browser_page，避免循环依赖。

支持的格式：
  图片   png jpg jpeg gif bmp webp svg
  文档   docx
  演示   pptx
  表格   xlsx xlsm csv
  PDF    pdf
  文本   txt md json py log xml yaml yml ini cfg htm html css js ts sql bat sh toml rst
  其它   显示文件名、大小、类型说明，不报错、不白屏
"""
from __future__ import annotations

import csv
import html as _html
import io
import os
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QLabel, QScrollArea, QSizePolicy, QStackedWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from .. import themes

# ---------------------------------------------------------------------------
# 扩展名分组（全部小写，带点）
# ---------------------------------------------------------------------------
IMAGE_EXTS: Tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg")
DOC_EXTS: Tuple[str, ...] = (".docx",)
SLIDE_EXTS: Tuple[str, ...] = (".pptx",)
SHEET_EXTS: Tuple[str, ...] = (".xlsx", ".xlsm", ".csv")
PDF_EXTS: Tuple[str, ...] = (".pdf",)
TEXT_EXTS: Tuple[str, ...] = (
    ".txt", ".md", ".json", ".py", ".log", ".xml", ".yaml", ".yml",
    ".ini", ".cfg", ".htm", ".html", ".css", ".js", ".ts", ".sql",
    ".bat", ".sh", ".toml", ".rst",
)
_ALL_EXTS: frozenset = frozenset(
    IMAGE_EXTS + DOC_EXTS + SLIDE_EXTS + SHEET_EXTS + PDF_EXTS + TEXT_EXTS)

# 大文件保护：别让一张巨型表 / 一份超长日志把 QTextBrowser 卡死。
MAX_ROWS = 80
MAX_COLS = 16
MAX_CELL_CHARS = 160
MAX_TEXT_CHARS = 120000
MAX_SLIDES = 60
MAX_DOC_PARAS = 400
MAX_DOC_TABLES = 30
MAX_SHAPES_PER_SLIDE = 40
MAX_SHEETS = 8


def _esc(value) -> str:
    """HTML 转义；None / 数字 / 日期一律先转字符串。"""
    try:
        return _html.escape("" if value is None else str(value), quote=True)
    except Exception:
        return ""


def _ext_of(path: str) -> str:
    try:
        return os.path.splitext(str(path or ""))[1].lower()
    except Exception:
        return ""


def _supported_ext(path: str) -> bool:
    """这个路径的扩展名有没有内置解析器。"""
    return _ext_of(path) in _ALL_EXTS


def is_supported(path: str) -> bool:
    """模块级入口：该扩展名能否解析出内容（其它格式也有体面的信息页）。"""
    return _supported_ext(path)


def supported_extensions() -> Tuple[str, ...]:
    """返回全部内置解析器的扩展名（排序后），供 UI 拼文件对话框过滤器。"""
    return tuple(sorted(_ALL_EXTS))


def _read_text(path: str, limit: int = MAX_TEXT_CHARS) -> str:
    """按多种编码兜底读文本，绝不因为编码不对抛异常。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(max(4096, int(limit) * 4 + 4096))
    except Exception:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)[:limit]
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        return raw.decode("utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _size_text(nbytes: int) -> str:
    """人类可读的文件大小。"""
    try:
        size = float(nbytes)
    except Exception:
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _cell_text(value) -> str:
    """表格单元格转成短字符串。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    try:
        s = str(value)
    except Exception:
        s = ""
    if len(s) > MAX_CELL_CHARS:
        s = s[:MAX_CELL_CHARS] + "..."
    return s


def _palette() -> dict:
    """取当前主题色板；主题模块异常时用一套浅色兜底，保证永远有值。"""
    try:
        t = themes.tokens()
    except Exception:
        t = {}
    return {
        "fg": t.get("text", "#1f2328"),
        "muted": t.get("text_muted", "#7a828b"),
        "line": t.get("border", "#e3e6ea"),
        "card": t.get("card_bg", "#ffffff"),
        "bg": t.get("panel_bg", "#ffffff"),
        "soft": t.get("accent_soft", "#e3f2fd"),
        "accent": t.get("accent_text", "#0d47a1"),
        "code": t.get("code_bg", "#f6f8fa"),
        "danger": t.get("danger", "#c0392b"),
    }


def _note(text: str, p: dict) -> str:
    return (f'<p style="color:{p["muted"]};font-size:11px;'
            f'margin:8px 0 4px 0;">{_esc(text)}</p>')


def _pre_html(text: str, p: dict) -> str:
    return (f'<pre style="background:{p["code"]};border:1px solid {p["line"]};'
            f'border-radius:8px;padding:10px 12px;color:{p["fg"]};'
            f'font-size:12px;white-space:pre-wrap;">{_esc(text)}</pre>')


def _table_html(rows: Sequence[Sequence[str]], p: dict, header: bool = True) -> str:
    """把二维数据渲染成带边框的 HTML 表格（全内联样式，QTextBrowser 友好）。"""
    if not rows:
        return _note("这个文件里没有可显示的内容。", p)
    out = [
        '<table border="0" cellspacing="0" cellpadding="0" width="100%" '
        f'style="border-collapse:collapse;color:{p["fg"]};font-size:12px;">'
    ]
    for r_i, row in enumerate(rows):
        cells = list(row)[:MAX_COLS]
        # 去掉行尾全空列，表格不至于拖出一大片空白
        while cells and not str(cells[-1] or "").strip():
            cells.pop()
        if not cells:
            continue
        is_head = bool(header) and r_i == 0
        bg = p["soft"] if is_head else p["card"]
        out.append("<tr>")
        for cell in cells:
            txt = _esc(cell)
            if is_head:
                txt = f"<b>{txt}</b>"
            out.append(
                f'<td style="border:1px solid {p["line"]};background:{bg};'
                f'padding:5px 7px;text-align:left;vertical-align:top;">{txt}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _doc_html(name: str, subtitle: str, body: str) -> str:
    """统一的预览页外壳：文件名 + 类型 + 正文。"""
    p = _palette()
    return (
        f'<div style="font-family:\'Microsoft YaHei\',sans-serif;color:{p["fg"]};'
        f'font-size:13px;padding:18px 20px;">'
        f'<p style="font-size:17px;font-weight:bold;color:{p["fg"]};'
        f'margin:0 0 4px 0;">{_esc(name)}</p>'
        f'<p style="font-size:11px;color:{p["muted"]};'
        f'margin:0 0 16px 0;">{_esc(subtitle)}</p>'
        f'{body}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# 各格式解析器：一律返回「正文 HTML 片段」，异常由 FilePreview.preview 统一兜底
# ---------------------------------------------------------------------------
def _docx_body(path: str) -> str:
    """Word：段落（按样式映射标题层级）+ 表格。"""
    from docx import Document

    p = _palette()
    doc = Document(path)
    out: List[str] = []
    count = 0
    for para in doc.paragraphs:
        if count >= MAX_DOC_PARAS:
            out.append(_note(f"段落较多，只显示前 {MAX_DOC_PARAS} 段。", p))
            break
        text = (getattr(para, "text", "") or "").strip()
        if not text:
            continue
        style = ""
        try:
            style = str(getattr(getattr(para, "style", None), "name", "") or "").lower()
        except Exception:
            style = ""
        if "title" in style:
            tag, size = "h1", 20
        elif "heading 1" in style or style.rstrip().endswith(" 1"):
            tag, size = "h2", 17
        elif "heading" in style:
            tag, size = "h3", 15
        else:
            tag, size = "p", 13
        out.append(
            f'<{tag} style="font-size:{size}px;color:{p["fg"]};'
            f'margin:6px 0;line-height:1.6;">{_esc(text)}</{tag}>')
        count += 1

    tables = list(getattr(doc, "tables", []) or [])[:MAX_DOC_TABLES]
    for t_i, table in enumerate(tables):
        rows: List[List[str]] = []
        try:
            for r_i, row in enumerate(table.rows):
                if r_i >= MAX_ROWS:
                    break
                rows.append([_cell_text(getattr(c, "text", ""))
                             for c in list(row.cells)[:MAX_COLS]])
        except Exception:
            continue
        if not rows:
            continue
        out.append(f'<p style="font-size:13px;color:{p["accent"]};font-weight:bold;'
                   f'margin:16px 0 6px 0;">表格 {t_i + 1}</p>')
        out.append(_table_html(rows, p, header=False))
    if len(list(getattr(doc, "tables", []) or [])) > MAX_DOC_TABLES:
        out.append(_note(f"表格较多，只显示前 {MAX_DOC_TABLES} 张。", p))
    if not out:
        out.append(_note("这个 Word 文档里没有可提取的段落或表格。", p))
    return "".join(out)


def _slide_rows(shape) -> Optional[List[List[str]]]:
    """从 PPT 形状里取表格数据；没有表格返回 None。"""
    try:
        if not bool(getattr(shape, "has_table", False)):
            return None
        table = shape.table
        rows: List[List[str]] = []
        for r_i, row in enumerate(table.rows):
            if r_i >= MAX_ROWS:
                break
            rows.append([_cell_text(getattr(c, "text", ""))
                         for c in list(row.cells)[:MAX_COLS]])
        return rows or None
    except Exception:
        return None


def _pptx_body(path: str) -> str:
    """PPT：每页一张卡片，含标题、文本框、表格。"""
    from pptx import Presentation

    p = _palette()
    prs = Presentation(path)
    out: List[str] = []
    total = 0
    for i, slide in enumerate(prs.slides):
        if i >= MAX_SLIDES:
            out.append(_note(f"页数较多，只显示前 {MAX_SLIDES} 页。", p))
            break
        title = ""
        try:
            title_shape = slide.shapes.title
            if title_shape is not None:
                title = (getattr(title_shape, "text", "") or "").strip()
        except Exception:
            title = ""
        texts: List[str] = []
        tables: List[List[List[str]]] = []
        try:
            shapes = list(slide.shapes)
        except Exception:
            shapes = []
        for shape in shapes[:MAX_SHAPES_PER_SLIDE]:
            rows = _slide_rows(shape)
            if rows is not None:
                tables.append(rows)
                continue
            try:
                if bool(getattr(shape, "has_text_frame", False)):
                    txt = (getattr(shape, "text", "") or "").strip()
                    if txt and txt != title and len(texts) < 30:
                        texts.append(txt)
            except Exception:
                continue
        total += 1
        card = [
            '<div style="border:1px solid ' + p["line"] + ';background:'
            + p["card"] + ';border-radius:10px;padding:12px 14px;margin:0 0 12px 0;">',
            f'<p style="font-size:14px;font-weight:bold;color:{p["accent"]};'
            f'margin:0 0 8px 0;">第 {i + 1} 页　{_esc(title or "（无标题）")}</p>',
        ]
        if texts:
            card.append("".join(
                f'<p style="font-size:12px;color:{p["fg"]};margin:3px 0;">'
                f'{_esc(txt)}</p>' for txt in texts))
        for rows in tables:
            card.append(_table_html(rows, p, header=False))
        if not texts and not tables:
            card.append(_note("这一页没有可提取的文字（可能只有图片）。", p))
        card.append("</div>")
        out.append("".join(card))
    if total == 0:
        out.append(_note("这个演示文稿里没有幻灯片。", p))
    return "".join(out)


def _csv_sheets(path: str) -> List[Tuple[str, List[List[str]], bool]]:
    """CSV：猜分隔符，按行列上限读。"""
    text = _read_text(path, MAX_TEXT_CHARS)
    delim = ","
    try:
        delim = csv.Sniffer().sniff(text[:4096], delimiters=",\t;").delimiter
    except Exception:
        delim = ","
    rows: List[List[str]] = []
    truncated = False
    try:
        for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=delim)):
            if i >= MAX_ROWS:
                truncated = True
                break
            rows.append([_cell_text(v) for v in list(row)[:MAX_COLS]])
    except Exception:
        pass
    return [("CSV", rows, truncated)]


def _xlsx_sheets(path: str) -> List[Tuple[str, List[List[str]], bool]]:
    """Excel：只读模式逐行取，按行列上限截断。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    sheets: List[Tuple[str, List[List[str]], bool]] = []
    try:
        worksheets = list(getattr(wb, "worksheets", []) or [])[:MAX_SHEETS]
        for ws in worksheets:
            rows: List[List[str]] = []
            truncated = False
            try:
                for r_i, row in enumerate(ws.iter_rows(values_only=True)):
                    if r_i >= MAX_ROWS:
                        truncated = True
                        break
                    values = list(row or [])[:MAX_COLS]
                    if len(list(row or [])) > MAX_COLS:
                        truncated = True
                    rows.append([_cell_text(v) for v in values])
            except Exception:
                pass
            try:
                name = str(getattr(ws, "title", "") or "工作表")
            except Exception:
                name = "工作表"
            sheets.append((name, rows, truncated))
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return sheets


def _sheet_body(path: str, ext: str) -> str:
    p = _palette()
    if ext == ".csv":
        sheets = _csv_sheets(path)
    else:
        sheets = _xlsx_sheets(path)
    if not sheets:
        return _note("这个表格文件里没有工作表。", p)
    out: List[str] = []
    for name, rows, truncated in sheets:
        out.append(f'<p style="font-size:13px;font-weight:bold;color:{p["accent"]};'
                   f'margin:14px 0 6px 0;">工作表：{_esc(name)}（{len(rows)} 行）</p>')
        out.append(_table_html(rows, p, header=True))
        if truncated:
            out.append(_note(
                f"表格较大，只显示前 {MAX_ROWS} 行、每行前 {MAX_COLS} 列。", p))
    return "".join(out)


def _pdf_body(path: str) -> str:
    """PDF：pdfminer 提取纯文本（扫描件会给出说明）。"""
    from pdfminer.high_level import extract_text

    try:
        text = extract_text(path, maxpages=60) or ""
    except TypeError:
        text = extract_text(path) or ""
    if not str(text).strip():
        text = "（这个 PDF 没有可提取的文字，可能是扫描件或纯图片文档。）"
    return _pre_html(str(text)[:MAX_TEXT_CHARS], _palette())


def _text_body(path: str) -> str:
    return _pre_html(_read_text(path, MAX_TEXT_CHARS), _palette())


def _generic_body(path: str) -> str:
    """没有内置解析器：给一张清楚的文件信息表，而不是空白或异常。"""
    p = _palette()
    try:
        st = os.stat(path)
        size = _size_text(st.st_size)
        mtime = ""
        try:
            import datetime
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S")
        except Exception:
            mtime = ""
    except Exception:
        size, mtime = "未知", ""
    ext = _ext_of(path) or "（无扩展名）"
    rows = [
        ["项目", "内容"],
        ["文件名", os.path.basename(path)],
        ["完整路径", path],
        ["文件大小", size],
        ["修改时间", mtime],
        ["文件类型", ext],
        ["说明", "这种格式还没有内置解析器，这里先列出文件信息；"
                 "图片、Word、PPT、Excel、PDF 和常见文本可以直接看内容。"],
    ]
    return _table_html(rows, p, header=True) + _note(
        "提示：直接把文件拖进浏览器面板也能打开预览。", p)


# ---------------------------------------------------------------------------
# 预览控件
# ---------------------------------------------------------------------------
class FilePreview(QWidget):
    """一个标签页里的本地文件预览区：文本页与图片页用 QStackedWidget 切。

    对外 API：
      · ``preview(path) -> bool``   解析并显示；任何失败都显示中文说明页并返回 False
      · ``current_path() -> str``  当前文件路径
      · ``title() -> str``         标签标题（文件名）
      · ``is_supported(path)``     该扩展名有没有内置解析器
      · ``refresh_theme()``        换主题后重新生成内容
      · ``show_error(reason)``     外部兜底：直接显示错误说明页
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FilePreview")

        self._path: str = ""
        self._title: str = "文件预览"
        self._kind: str = "text"       # "text" / "image"
        self._last_html: str = ""
        self._pixmap: Optional[QPixmap] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget()
        self._stack.setObjectName("FilePreviewStack")
        self._stack.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Expanding)

        # 文本 / 表格页：轻量 HTML 渲染，不联网、不跑 JS
        self._browser = QTextBrowser()
        self._browser.setObjectName("FilePreviewText")
        self._browser.setReadOnly(True)
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self._stack.addWidget(self._browser)

        # 图片页：QLabel 放在 QScrollArea 里，大图可以滚动
        self._image_label = QLabel()
        self._image_label.setObjectName("FilePreviewImage")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Ignored)
        self._image_scroll = QScrollArea()
        self._image_scroll.setObjectName("FilePreviewImageScroll")
        self._image_scroll.setWidgetResizable(False)
        self._image_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_scroll.setWidget(self._image_label)
        self._stack.addWidget(self._image_scroll)

        root.addWidget(self._stack, 1)
        self._stack.setCurrentWidget(self._browser)
        self.refresh_theme()

    # -- 对外查询 -------------------------------------------------------
    def current_path(self) -> str:
        return self._path

    def title(self) -> str:
        return self._title

    def is_supported(self, path: str) -> bool:
        return _supported_ext(path)

    # -- 主入口 ---------------------------------------------------------
    def preview(self, path: str) -> bool:
        """按扩展名分派；**任何异常都在这里被吃掉**，只显示友好说明页。"""
        self._path = str(path or "")
        self._title = os.path.basename(self._path) or "文件预览"
        if not self._path or not os.path.isfile(self._path):
            self.show_error("文件不存在，或这不是一个普通文件。")
            return False
        ext = _ext_of(self._path)
        try:
            if ext in IMAGE_EXTS:
                return self._preview_image(self._path, ext)
            if ext in DOC_EXTS:
                return self._show_html(
                    _doc_html(self._title, "Word 文档 · 段落与表格",
                              _docx_body(self._path)))
            if ext in SLIDE_EXTS:
                return self._show_html(
                    _doc_html(self._title, "演示文稿 · 逐页内容",
                              _pptx_body(self._path)))
            if ext in SHEET_EXTS:
                return self._show_html(
                    _doc_html(self._title, "电子表格 · 行列预览",
                              _sheet_body(self._path, ext)))
            if ext in PDF_EXTS:
                return self._show_html(
                    _doc_html(self._title, "PDF · 文本提取",
                              _pdf_body(self._path)))
            if ext in TEXT_EXTS:
                return self._show_html(
                    _doc_html(self._title, "纯文本 · 原文",
                              _text_body(self._path)))
            return self._show_html(
                _doc_html(self._title, "文件信息 · 暂无内置解析器",
                          _generic_body(self._path)))
        except Exception as exc:                         # noqa: BLE001
            self.show_error(f"{type(exc).__name__}: {exc}")
            return False

    def show_error(self, reason: str) -> None:
        """显示一张中文错误说明页（永不抛异常）。"""
        p = _palette()
        name = self._title or os.path.basename(self._path) or "（未命名）"
        body = (
            f'<p style="font-size:13px;color:{p["danger"]};margin:8px 0;">'
            f'这个文件暂时没法预览。</p>'
            f'<p style="font-size:12px;color:{p["fg"]};margin:6px 0;">'
            f'原因：{_esc(reason)}</p>'
            f'<p style="font-size:12px;color:{p["muted"]};margin:6px 0;line-height:1.8;">'
            f'可以换一个文件，或把文件拖进浏览器面板再试一次。'
            f'图片、Word、PPT、Excel、PDF 和常见文本格式都能直接看内容。</p>'
        )
        try:
            self._show_html(_doc_html(name, "预览失败", body))
        except Exception:
            try:
                self._browser.setPlainText(f"无法预览：{name}\n原因：{reason}")
                self._stack.setCurrentWidget(self._browser)
            except Exception:
                pass

    # -- 内部渲染 -------------------------------------------------------
    def _show_html(self, markup: str) -> bool:
        self._last_html = str(markup or "")
        self._browser.setHtml(self._last_html)
        self._stack.setCurrentWidget(self._browser)
        self._kind = "text"
        return True

    def _render_svg(self, path: str) -> QPixmap:
        """QPixmap 解不了 SVG 时，用 QtSvg 渲染；QtSvg 缺失就返回空图。"""
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return QPixmap()
            size = renderer.defaultSize()
            w = max(64, min(1600, int(size.width()) or 800))
            h = max(64, min(1200, int(size.height()) or 600))
            image = QImage(w, h, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            try:
                renderer.render(painter)
            finally:
                painter.end()
            return QPixmap.fromImage(image)
        except Exception:
            return QPixmap()

    def _preview_image(self, path: str, ext: str) -> bool:
        pm = QPixmap()
        try:
            pm.load(path)
        except Exception:
            pm = QPixmap()
        if pm.isNull() and ext == ".svg":
            pm = self._render_svg(path)
        if pm.isNull():
            # 图片解码不了时，SVG / 文本型图片至少把源码显示出来
            src = _read_text(path, 60000)
            if src.strip():
                p = _palette()
                self._show_html(_doc_html(
                    self._title, "图片 · 解码失败，显示文件内容",
                    _pre_html(src, p)))
                return True
            self.show_error("图片格式无法被 Qt 解码，文件也不是可读文本。")
            return False
        self._pixmap = QPixmap(pm)
        self._rescale_image()
        self._stack.setCurrentWidget(self._image_scroll)
        self._kind = "image"
        return True

    def _rescale_image(self) -> None:
        """等比按宽度缩放到约 1.5 倍视口；高度超出的部分由 QScrollArea 滚动。"""
        if self._pixmap is None or self._pixmap.isNull():
            return
        try:
            vw = max(240, int(self._image_scroll.viewport().width()) - 12)
        except Exception:
            vw = 480
        ow = self._pixmap.width()
        oh = self._pixmap.height()
        if ow <= 0 or oh <= 0:
            return
        target_w = min(ow, max(1, int(vw * 1.5)))
        try:
            scaled = (self._pixmap.scaledToWidth(
                target_w, Qt.TransformationMode.SmoothTransformation)
                if ow > target_w else QPixmap(self._pixmap))
        except Exception:
            scaled = QPixmap(self._pixmap)
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())

    # -- 主题 / 尺寸 ----------------------------------------------------
    def refresh_theme(self) -> None:
        """换主题后重新取色：有文件就重新解析一次，没有就刷一张欢迎页。"""
        p = _palette()
        try:
            self._browser.setStyleSheet(
                f"QTextBrowser{{background:{p['bg']};color:{p['fg']};border:none;}}")
            self._image_scroll.setStyleSheet(
                f"QScrollArea{{background:{p['bg']};border:none;}}")
            self._image_label.setStyleSheet(
                f"QLabel{{background:{p['bg']};}}")
        except Exception:
            pass
        if self._path:
            try:
                self.preview(self._path)
                return
            except Exception:
                pass
        if self._kind == "image":
            self._rescale_image()
            return
        if not self._last_html:
            welcome = (
                f'<div style="font-family:\'Microsoft YaHei\';color:{p["fg"]};'
                f'padding:24px;">'
                f'<p style="font-size:17px;font-weight:bold;">本地文件预览</p>'
                f'<p style="font-size:12px;color:{p["muted"]};line-height:1.9;">'
                f'点工具行的「▤」按钮选文件，或直接把文件拖进浏览器面板。<br>'
                f'支持图片、Word、PPT、Excel、PDF 和常见文本格式，'
                f'全部在本地解析，不联网、不上传。</p></div>')
            self._last_html = welcome
            try:
                self._browser.setHtml(welcome)
            except Exception:
                pass

    def resizeEvent(self, event):                     # noqa: N802（Qt 回调）
        super().resizeEvent(event)
        if self._kind == "image":
            self._rescale_image()
