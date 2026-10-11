# -*- coding: utf-8 -*-
r"""将 Markdown 渲染为 QTextBrowser 可显示的 HTML（代码块内联高亮）。

★ v9.18.0（需求 6「模型响应较慢」）：本模块是全流程里最烫的一处 ——
  流式输出时，界面上每 80ms 就要把**到目前为止的全部正文**重新渲染一遍，
  其中 Pygments 语法高亮是纯 CPU 硬活。所以这里加了两级缓存：
    ① `_inline_pygments` 按 (语言, 代码内容) 缓存 —— 同一段代码被反复渲染时直接命中；
    ② `render_with_blocks` / `render_markdown` 按**整段原文**缓存最后几次结果 ——
      界面在同一帧里被不同信号（工具结果 / 流式节拍）连续调用多次时直接命中。
  两级都是简单 LRU，容量很小（几十条），不会无限吃内存。

★ v9.18.0 修「生成的 Python 代码没有按代码格式显示」：
  真凶是原来那一行的围栏正则 `_CODE_RE = r"```(\w*)\n(.*?)```"`，它太脆：
    ① **必须闭合** —— 流式输出时围栏只吐了开头，整段（连 ```python 三个反引号）
       都被当普通段落渲染，用户边生成边看就是"一堆没有格式的纯文本"；
    ② 硬吃 `\n` —— 模型/文件里是 `\r\n`（Windows 上极常见）时 ` ```python\r\n `
       根本匹配不上，**最终态也认不出代码块**；
    ③ `\w*` 不认 `c++` / `c#` / `objective-c`，"```python "（语言名后带空格）
       也认不出。
  → 换成按**行扫描**的围栏解析（见 `_split_code_blocks`）：兼容 CRLF、容忍
    语言名后的空格与 `+#.-`、4 个以上反引号的围栏、行首缩进；
    **围栏没闭合时把剩下的内容整段当代码块**（流式必需）。
"""
import re
from collections import OrderedDict

import markdown
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter

# ★ v9.18.0：围栏改成**逐行扫描**（见模块头注释），不再用一条正则硬套。
#   开围栏：行首最多 3 个空格 + ≥3 个反引号 + 可选语言名（c++ / c# / objective-c 都能认）
_FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,})[ \t]*([^\s`]*)[ \t]*$")
# 闭围栏：行首最多 3 个空格 + ≥3 个反引号，且**不能比开围栏短**、后面不能带内容
_FENCE_CLOSE_RE = re.compile(r"^[ \t]{0,3}(`{3,})[ \t]*$")
# 语言名允许的字符集（碰到别的字符就截断，避免 "python:foo.py" 整串当语言名）
_LANG_CHARS_RE = re.compile(r"[A-Za-z0-9+#._-]*")

# 把工具调用块从正文里剔掉再渲染。
# ⚠ v9.16.0 起正则**只有一份**，在 app/agent.py（TOOL_BLOCK_RE / TOOL_BODY_RE）——
#    以前这儿和 chat_worker / main_window 各抄一套，早晚漂移。
#    这里保留同名别名，纯粹是为了不破坏既有引用。
from .agent import TOOL_BLOCK_RE as _TOOL_RE              # noqa: E402
from .agent import TOOL_BODY_RE as _TOOL_XMLARG_RE        # noqa: E402

# 缓存容量：代码高亮条数 / 整段渲染条数
_HL_CACHE_MAX = 48
_RENDER_CACHE_MAX = 8
_hl_cache = OrderedDict()
_render_cache = OrderedDict()
_text_block_cache = OrderedDict()


def _cache_get(cache, key):
    val = cache.get(key)
    if val is not None:
        cache.move_to_end(key)      # LRU：命中的挪到队尾
    return val


def _cache_put(cache, key, val, cap):
    cache[key] = val
    cache.move_to_end(key)
    while len(cache) > cap:
        cache.popitem(last=False)


def clear_caches():
    """清空渲染缓存（测试与主题/字体变更后调用）。"""
    _hl_cache.clear()
    _render_cache.clear()
    _text_block_cache.clear()


def _strip_tool_call(text: str) -> str:
    """把工具调用块从正文里剔掉（工具活动由专用 UI 展示）。

    两个正则都要跑：
      · 完整闭合的 <tool_call>…</tool_call>；
      · 漏写 </tool_call> 的 XML 参数风调用（模型经常漏，只吃调用本体，
        不吞后面的正文 —— 否则气泡会变空）。
    """
    if not text:
        return text
    return _TOOL_XMLARG_RE.sub("", _TOOL_RE.sub("", text))

# 预解析 Pygments 默认风格的 类->样式 映射（内联用）
_FMT = HtmlFormatter(nowrap=True, style="default")
_STYLE_DEFS = _FMT.get_style_defs()
_STYLE_MAP = {}
for _m in re.finditer(r"\.([a-zA-Z0-9_]+)\s*\{([^}]*)\}", _STYLE_DEFS):
    _STYLE_MAP[_m.group(1)] = _m.group(2).strip()


def _inline_pygments(code: str, lang: str) -> str:
    """语法高亮成内联样式的 HTML 片段（带 LRU 缓存，见模块头注释）。

    为什么键要包含 lang：同一段代码用不同语言高亮结果不同；
    为什么键要用 code 全文：高亮结果只由 (lang, code) 决定，没有别的输入。
    """
    key = (lang or "", code or "")
    hit = _cache_get(_hl_cache, key)
    if hit is not None:
        return hit

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

    out = re.sub(r'class="([^"]*)"', repl, raw)
    _cache_put(_hl_cache, key, out, _HL_CACHE_MAX)
    return out


_EMPTY_P_RE = re.compile(r"<p>\s*</p>", re.IGNORECASE)


def _norm_lang(info):
    """围栏信息串 -> 语言名（小写、截断到合法字符集）。

    "```python title=x" -> "python"；"```Python" -> "python"；"```" -> ""。
    """
    s = (info or "").strip()
    if not s:
        return ""
    return (_LANG_CHARS_RE.match(s).group(0) or "").lower()


def _placeholder(idx):
    return "\n\n%%CODEBLOCK%d%%\n\n" % idx


def _split_code_blocks(text):
    r"""把 ``` 代码块摘出来，正文里换成占位符。

    ★ ★ v9.18.0 重写成**逐行扫描**（原来是 `re.sub(r"```(\w*)\n(.*?)```")`）：

      ① **流式未闭合围栏**：模型边生成边显示时，``` 常常只吐了开头。
         老正则要求闭合 → 整段（含 ```python 三个反引号）被当普通段落，
         用户看到的就是"没有代码格式的一大坨字"。现在**没闭合就把剩下的
         内容整段当代码块**，用户从第一行代码开始就能看到代码卡片。
      ② **CRLF**：Windows 上模型输出 / 回读的 .py 文件常带 `\r\n`，
         老正则在 ``` 后硬吃 `\n` → 连**最终态**都认不出代码块。这里先统一换行。
      ③ 兼容 4 个以上反引号的围栏、行首缩进、语言名后的空格与 `c++`/`c#`。

    ★ 也做了缓存：流式渲染时同一段正文会被反复切分（正文在长、但**已完成的
    代码块**是不变的），命中率很高。
    """
    hit = _cache_get(_text_block_cache, text)
    if hit is not None:
        return hit

    # 统一换行符：\r\n / \r -> \n（否则围栏行末尾的 \r 会让整行匹配失败）
    src = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = src.split("\n")

    blocks = []
    out = []
    fence = None          # 已开围栏：(反引号个数, 语言名)
    buf = []

    def flush():
        """收下一个代码块（闭合时 / 文末仍未闭合时都走这里）。"""
        code = "\n".join(buf)
        if code:
            code += "\n"
        blocks.append((fence[1], code))
        out.append(_placeholder(len(blocks) - 1))

    for line in lines:
        if fence is None:
            m = _FENCE_OPEN_RE.match(line)
            if m and len(m.group(1)) >= 3:
                fence = (len(m.group(1)), _norm_lang(m.group(2)))
                buf = []
                continue
            out.append(line)
        else:
            mc = _FENCE_CLOSE_RE.match(line)
            if mc and len(mc.group(1)) >= fence[0]:
                flush()
                fence = None
                buf = []
                continue
            buf.append(line)
    # ★ 流式中间态：围栏开了但还没闭合 -> 剩下的**全部**算代码，照样按代码显示
    if fence is not None:
        flush()

    res = ("\n".join(out), blocks)
    _cache_put(_text_block_cache, text, res, _RENDER_CACHE_MAX)
    return res


def render_with_blocks(text: str):
    """把 Markdown 拆成「正文 HTML」+「代码块列表」。

    返回 (prose_html, blocks)，blocks 为 dict 列表：
      {"lang": 语言, "html": 高亮后的 HTML, "code": 原始代码字符串}
    代码块不塞进正文，由界面单独渲染成带复制按钮的代码卡片。

    ★ 整段结果带缓存（同一段原文被连续渲染多次时直接返回）。
      返回的 blocks 每次都**复制一份新列表**，避免调用方改动污染缓存。
    """
    if not text:
        return "", []
    text = _strip_tool_call(text)
    hit = _cache_get(_render_cache, ("wb", text))
    if hit is not None:
        return hit[0], [dict(b) for b in hit[1]]

    stripped, blocks = _split_code_blocks(text)
    html = markdown.markdown(stripped, extensions=["tables", "nl2br"])

    out = []
    for i, (lang, code) in enumerate(blocks):
        out.append({"lang": lang, "html": _inline_pygments(code, lang), "code": code})
        html = html.replace("%%CODEBLOCK%d%%" % i, "")
    # 清掉占位符移除后留下的空段落
    html = _EMPTY_P_RE.sub("", html)
    _cache_put(_render_cache, ("wb", text), (html, out), _RENDER_CACHE_MAX)
    return html, [dict(b) for b in out]


def render_markdown(text: str) -> str:
    if not text:
        return ""
    # 去掉 Agent 工具调用标签（工具活动由专用 UI 展示）
    text = _strip_tool_call(text)
    hit = _cache_get(_render_cache, ("md", text))
    if hit is not None:
        return hit

    stripped, blocks = _split_code_blocks(text)
    html = markdown.markdown(stripped, extensions=["tables", "nl2br"])
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
    html = _EMPTY_P_RE.sub("", html)
    _cache_put(_render_cache, ("md", text), html, _RENDER_CACHE_MAX)
    return html
