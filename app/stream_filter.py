# -*- coding: utf-8 -*-
"""流式增量里的 DSML 标签过滤（跨 token 状态机，v9.22.x）。

问题
----
模型会把 DeepSeek 的 DSML 工具调用**原样流式吐在正文里**：

    <｜｜DSML｜｜ calls>
    <｜｜DSML｜｜ invoke name="write_file">
    <｜｜DSML｜｜ parameter name="path" string="true">a.txt</｜｜DSML｜｜ parameter>
    </｜｜DSML｜｜ invoke>
    </｜｜DSML｜｜ calls>

界面是「逐 token 追加」的：`agent.strip_dsml()` 只有拿到**完整片段**才有效，
半截标签（`<｜｜DSM`、`<｜｜DSML｜｜ para`）会先上屏 —— 用户看到的就是
"生成过程中大批量滚出这种代码"。

做法
----
一个**增量状态机**，只关心缓冲区尾部：

  · 正常正文立刻放行 —— 中文、代码、HTML 里的 `<div>`、比较符 `a < b`
    全都不受影响（只要 `<` 后面不是 `｜`/`|`，当场放行）；
  · 当尾部出现「可能是 DSML 标签开头」的 `<` 时暂扣，等更多字符；
  · 确认**不是**标签 → 立刻把扣住的原文补发出去，**一个字都不丢**；
  · 确认是 `calls` / `invoke` / `parameter` → 进入"块内"状态，整块丢弃，
    直到对应闭合标签（闭合缺失就一直丢到流结束，和 `strip_dsml()` 语义一致）；
  · 暂扣窗口上限 `HOLD_MAX`（512 字符）：扣了这么多还凑不出标签头，
    就认定它不是标签，原样放行 —— 绝不会把界面卡住。

每处理一个增量只扫缓冲区（≤ HOLD_MAX + 本次增量），不会整段重扫。

本模块是**纯 Python**（不 import Qt），desktop / Web 共用同一份实现，
可以离线单测：`tests/probe_dsml_stream_v922.py`。
"""
import re

from .agent import strip_dsml

HOLD_MAX = 512      # 暂扣窗口上限（字符）
BLOCK_TAIL = 64     # 块内只保留尾部这么多字符，用于识别闭合标签

_BAR = r"[｜|]{1,3}"
_BLOCK_TAGS = ("calls", "invoke", "parameter")

# 三种块的闭合标签；块内出现闭合就退出
_CLOSE_RES = {
    name: re.compile(r"<\s*/\s*" + _BAR + r"\s*DSML\s*" + _BAR +
                     r"\s*" + name + r"\s*>", re.IGNORECASE)
    for name in _BLOCK_TAGS
}
# 任意 DSML **开**标签（不含 </…>），parameter 块遇到下一个开标签就收尾
_OPEN_HEAD_RE = re.compile(
    r"<\s*" + _BAR + r"\s*DSML\s*" + _BAR + r"\s*[A-Za-z_]*\s*/?>",
    re.IGNORECASE)
# 流结束时残留的"半截标签"：`<｜`、`</｜｜` …（后面还没等到 DSML 字样）
_DANGLING_BAR_RE = re.compile(r"^<\s*/?\s*" + _BAR)


def _scan_tag_head(s):
    """扫描字符串开头的 DSML 标签头（手工状态机，支持"还看不出来"）。

    只认这种结构（大小写不敏感、空格可有可无）：
        < [/] [1~3 根竖线] DSML [1~3 根竖线] [名字] [/] >

    Args:
        s: 以 `<` 开头的待判定文本。

    Returns:
        (status, end, name, is_close, self_closing)
          status: "no"      确定不是 DSML 标签头
                  "partial" 目前还看不出来（需要更多字符）
                  "ok"      完整标签头，消费到 end
    """
    n = len(s)
    if n == 0 or s[0] != "<":
        return ("no", 0, "", False, False)
    i = 1
    is_close = False
    if i < n and s[i] == "/":
        is_close = True
        i += 1
    while i < n and s[i].isspace():
        i += 1
    bars = 0
    while i < n and s[i] in "｜|" and bars < 3:
        i += 1
        bars += 1
    if bars == 0:
        if i >= n:
            return ("partial", 0, "", is_close, False)
        return ("no", 0, "", is_close, False)
    if i < n and s[i] in "｜|":
        return ("no", 0, "", is_close, False)      # 4 根以上，不是 DSML
    while i < n and s[i].isspace():
        i += 1
    for ch in "DSML":
        if i >= n:
            return ("partial", 0, "", is_close, False)
        if s[i].upper() != ch:
            return ("no", 0, "", is_close, False)
        i += 1
    while i < n and s[i].isspace():
        i += 1
    bars = 0
    while i < n and s[i] in "｜|" and bars < 3:
        i += 1
        bars += 1
    if bars == 0:
        if i >= n:
            return ("partial", 0, "", is_close, False)
        return ("no", 0, "", is_close, False)
    if i < n and s[i] in "｜|":
        return ("no", 0, "", is_close, False)
    while i < n and s[i].isspace():
        i += 1
    name_start = i
    while i < n and (s[i].isalpha() or s[i] == "_"):
        i += 1
        if i - name_start > 40:                   # 名字长得离谱 -> 不是标签
            return ("no", 0, "", is_close, False)
    name = s[name_start:i].lower()
    while i < n and s[i].isspace():
        i += 1
    if i >= n:
        return ("partial", 0, "", is_close, False)
    if s[i] == ">":
        return ("ok", i + 1, name, is_close, False)
    if s[i] == "/":
        if i + 1 >= n:
            return ("partial", 0, "", is_close, False)
        if s[i + 1] == ">":
            return ("ok", i + 2, name, is_close, True)
    return ("no", 0, "", is_close, False)


class StreamDsmlFilter:
    """把流式增量清洗成「可以直接上屏」的文本。

    用法::

        f = StreamDsmlFilter()
        safe = f.feed(delta)      # 可以立刻 emit
        tail = f.flush()          # 流结束时调用，吐出最后一点安全正文

    Attributes:
        buf: 暂扣缓冲（只保留"还看不出来"的尾巴）。
        in_block: 当前吃在哪种 DSML 块里（None / calls / invoke / parameter）。
    """

    def __init__(self, hold_max=HOLD_MAX, block_tail=BLOCK_TAIL):
        self.hold_max = max(64, int(hold_max or HOLD_MAX))
        self.block_tail = max(32, int(block_tail or BLOCK_TAIL))
        self.buf = ""
        self.in_block = None

    # ------------------------------------------------------------------
    def reset(self):
        """丢弃全部状态（换端点重试 / 新一轮开始时用）。"""
        self.buf = ""
        self.in_block = None

    # ------------------------------------------------------------------
    def _consume_block(self):
        """块内消费一个缓冲：闭合了就退出块并返回 True；否则裁剪缓冲返回 False。"""
        close = _CLOSE_RES[self.in_block].search(self.buf)
        if close:
            # parameter 块：如果先遇到下一个 DSML 开标签，就在这里收尾，
            # 让后续流程重新识别（和 strip_dsml 的分段语义保持一致）。
            if self.in_block == "parameter":
                head = _OPEN_HEAD_RE.search(self.buf)
                if head and head.start() < close.start():
                    self.buf = self.buf[head.start():]
                    self.in_block = None
                    return True
            self.buf = self.buf[close.end():]
            self.in_block = None
            return True
        if self.in_block == "parameter":
            head = _OPEN_HEAD_RE.search(self.buf)
            if head:
                self.buf = self.buf[head.start():]
                self.in_block = None
                return True
        # 还没闭合：块内容不需要上屏，只留尾巴用于识别闭合标签
        if len(self.buf) > self.block_tail:
            self.buf = self.buf[-self.block_tail:]
        return False

    # ------------------------------------------------------------------
    def feed(self, delta):
        """喂一段增量，返回**现在就可以上屏**的文本（可能是空串）。"""
        if not delta:
            return ""
        self.buf += str(delta)
        out = []
        while True:
            if self.in_block:
                if not self._consume_block():
                    break
                continue

            i = self.buf.find("<")
            if i < 0:
                out.append(self.buf)
                self.buf = ""
                break
            if i > 0:
                out.append(self.buf[:i])
                self.buf = self.buf[i:]

            status, end, name, is_close, self_closing = _scan_tag_head(self.buf)
            if status == "ok":
                # 完整标签头：整个丢掉（标签本身绝不上屏）
                self.buf = self.buf[end:]
                if (not is_close and not self_closing
                        and name in _BLOCK_TAGS):
                    self.in_block = name
                continue
            if status == "partial":
                if len(self.buf) > self.hold_max:
                    # 扣了 512 字符还凑不出标签头 -> 肯定不是标签，原样放行
                    out.append(self.buf[0])
                    self.buf = self.buf[1:]
                    continue
                break                      # 继续等下一个增量
            # "no"：这个 '<' 就是普通正文，放行一个字符后继续找下一个
            out.append(self.buf[0])
            self.buf = self.buf[1:]
            continue
        return "".join(out)

    # ------------------------------------------------------------------
    def flush(self):
        """流结束：吐出暂扣的最后一点正文。

        半截 DSML 前缀（`<｜｜DSM`）不能当正文补发；整块没闭合的按
        `strip_dsml()` 语义丢弃到文末。普通 `<` 原样保留。
        """
        if self.in_block:
            self.buf = ""
            self.in_block = None
            return ""
        rest = self.buf
        self.buf = ""
        if not rest:
            return ""
        if _DANGLING_BAR_RE.match(rest):
            return ""
        return strip_dsml(rest)
