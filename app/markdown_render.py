# -*- coding: utf-8 -*-
"""将 Markdown 渲染为 QTextBrowser 可显示的 HTML（代码块内联高亮）。"""
import re
import markdown
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter

_CODE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_TOOL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)

# 预解析 Pygments 默认风格的 类->样式 映射（内联用）
_FMT = HtmlFormatter(nowrap=True, style="default")
_STYLE_DEFS = _FMT.get_style_defs()
_STYLE_MAP = {}
for _m in re.finditer(r"\.([a-zA-Z0-9_]+)\s*\{([^}]*)\}", _STYLE_DEFS):
    _STYLE_MAP[_m.group(1)] = _m.group(2).strip()


def _inline_pygments(code: str, lang: str) -> str:
    try:
        lexer = get_lexer_by_name(lang, stripall=False)
    except Exception:
        lexer = TextLexer()
    raw = highlight(code, lexer, _FMT)

    def repl(mt):
        classes = mt.group(1).split()
        styles = [_STYLE_MAP.get(c, "") for c in classes]
        styles = [s for s in styles if s]
        if styles:
            return ' style="%s"' % ";".join(styles)
        return ""

    return re.sub(r'class="([^"]*)"', repl, raw)


_EMPTY_P_RE = re.compile(r"<p>\s*</p>", re.IGNORECASE)


def render_with_blocks(text: str):
    """把 Markdown 拆成「正文 HTML」+「代码块列表」。

    返回 (prose_html, blocks)，blocks 为 dict 列表：
      {"lang": 语言, "html": 高亮后的 HTML, "code": 原始代码字符串}
    代码块不塞进正文，由界面单独渲染成带复制按钮的代码卡片。
    """
    if not text:
        return "", []
    text = _TOOL_RE.sub("", text)
    blocks = []

    def grab(m):
        blocks.append((m.group(1) or "", m.group(2)))
        return "\n\n%%CODEBLOCK%d%%\n\n" % (len(blocks) - 1)

    text = _CODE_RE.sub(grab, text)
    html = markdown.markdown(text, extensions=["tables", "nl2br"])

    out = []
    for i, (lang, code) in enumerate(blocks):
        out.append({"lang": lang, "html": _inline_pygments(code, lang), "code": code})
        html = html.replace("%%CODEBLOCK%d%%" % i, "")
    # 清掉占位符移除后留下的空段落
    html = _EMPTY_P_RE.sub("", html)
    return html, out


def render_markdown(text: str) -> str:
    if not text:
        return ""
    # 去掉 Agent 工具调用标签（工具活动由专用 UI 展示）
    text = _TOOL_RE.sub("", text)
    blocks = []

    def grab(m):
        blocks.append((m.group(1), m.group(2)))
        return "%%CODEBLOCK%d%%" % (len(blocks) - 1)

    text = _CODE_RE.sub(grab, text)
    html = markdown.markdown(text, extensions=["tables", "nl2br"])
    for i, (lang, code) in enumerate(blocks):
        hi = _inline_pygments(code, lang)
        code_html = (
            '<pre style="background:#f6f8fa;color:#24292e;padding:10px 12px;'
            "border:1px solid #e1e4e8;border-radius:8px;"
            "font-family:Consolas,'Microsoft YaHei',monospace;"
            'font-size:13px;line-height:1.6;white-space:pre-wrap;'
            'word-break:break-word;overflow-x:auto;">' + hi + "</pre>"
        )
        html = html.replace("%%CODEBLOCK%d%%" % i, code_html)
    return html
