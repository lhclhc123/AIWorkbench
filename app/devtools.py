# -*- coding: utf-8 -*-
"""按行读 / 按行查 / 按行改（v9.20.0，「代码的读取 / 编辑工具增强」）。

为什么单独一个模块
------------------
`app/agent.py` 已经 4900 多行，工具实现再往里塞会失控。这里放**实现**，
`agent.py` 只保留「注册 + 分发」，跟 `app/tools_more.py` 是同一个套路。

四个工具
--------
1. `read_file(path, start=1, limit=200)`
   分批读，**每行都带行号**，被截断时明确写出「一共多少行 / 还剩多少 /
   下次从第几行开始」。替代原来「整文件读进上下文 → 超 2 万字硬切一刀」
   的做法（那种切法模型根本不知道自己漏了什么）。

2. `find_lines(pattern, path=".", glob=None, regex=False, case_sensitive=True,
              context=1, max_results=60)`
   按内容找**行号**，是 `edit_lines` 的前置步骤。`context` 是命中处前后各带几行
   （v9.22.1 起上限 10，默认 1）；命中数超过 `max_results` 时结果里会写明
   「共命中 N 处、只显示了前 M 处、用 path/glob 收窄」。`read_file` 一次
   读超过 400 行时也会在末尾追加「下次用 start/limit」的提示。

3. `edit_lines(path, start, end, new_text, expect=None)`
   只替换第 start~end 行。四道硬闸：
     ① 行号必须在文件范围内（`end == start-1` 表示纯插入，不删行）；
     ② `start > end + 1` 直接拒绝；
     ③ `expect` 与那几行原文（去首尾空白）不一致 → 拒绝，并把**实际原文**
        回给模型重新核对行号 —— 这是防「行号算错把文件改坏」的唯一硬闸；
     ④ 改完变 0 字节而原来不是 → 拒绝（v9.18.0 真发生过 `write_file`
        空内容把文件截断的事故，这里不再给第二次机会）。
   写盘**原子替换**（先写 `.tmp` 再 `os.replace`），中途失败原文件不受影响；
   换行风格（CRLF / LF / CR）、UTF-8 BOM、GBK 编码、是否以换行结尾全部保留。

4. `apply_patch(path, hunks=[{start,end,new_text,expect}, ...])`
   多处一起改，按 start **倒序**应用（避免前面的改动把后面的行号顶偏）；
   任何一个 hunk 的 expect 对不上就**整批放弃**，一个字节都不写。

约定：所有函数 `fn(args, ctx=None, confirm_cb=None) -> str`，
永远返回中文、**永远不抛异常**（跟 tools_more 一致）。
"""
import fnmatch
import os
import re

# ---- 分批读的默认值与上限 -------------------------------------------------
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000
# 单行超过这么多字符就截断（压缩过的 JS / 一行 JSON 会撑爆上下文）
MAX_LINE_CHARS = 500
# 一次最多回给模型多少字符（超了就少读几行，并明确说明读到第几行）
MAX_RETURN_CHARS = 40000

# 单个文件超过这么多字节就拒绝按行读写（读进来会撑爆内存/上下文；
# 这种体量本来就该用 find_lines / shell 工具处理）
MAX_SOURCE_BYTES = 64 * 1024 * 1024

# ---- find_lines 的扫描限制 ------------------------------------------------
MAX_FILE_BYTES = 2 * 1024 * 1024          # 单文件 > 2MB 直接跳过
DEFAULT_MAX_RESULTS = 60
MAX_MAX_RESULTS = 200
# 命中处的上下文行数（前后各几行）。v9.22.1：上限 5 -> 10 ——
# 实测 5 行不够看清一个函数的开头和收尾，模型还得再补一次 read_file。
DEFAULT_CONTEXT = 1
MAX_CONTEXT = 10

# 一次 read_file 返回超过这么多行，就在结果末尾追加一句「下次别这么读」。
# 400 行大约是 2 万字符，已经占掉相当一部分上下文，再读下去只会看漏中间。
BIG_READ_LINES = 400

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "venv", ".venv",
    "env", ".env", ".awb", "build", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", ".tox", "dist-packages", ".next", ".nuxt", "site-packages",
}

# 这些编码按顺序试：utf-8 最严，gb18030 几乎能吞下任何中文字节
_TRY_ENCODINGS = ("utf-8", "gb18030")


# =====================================================================
# 小工具
# =====================================================================
def _args(a):
    return a if isinstance(a, dict) else {}


def _s(a, *keys):
    """从 args 里按候选键取第一个非空字符串。"""
    for k in keys:
        v = a.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return ""


def _as_int(v, default=None):
    try:
        if isinstance(v, bool):
            return default
        return int(float(str(v).strip()))
    except Exception:
        return default


def _as_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on", "是"):
        return True
    if s in ("0", "false", "no", "n", "off", "否"):
        return False
    return default


def _rel(path, base):
    """相对 base 的展示用路径（拿不到就返回绝对路径）。"""
    try:
        r = os.path.relpath(path, base)
        if r.startswith(".."):
            return path
        return r.replace("\\", "/")
    except Exception:
        return path


def _is_binary(data):
    """二进制判定：有 NUL，或前 8KB 里控制字符超过 30%。"""
    if not data:
        return False
    head = data[:8192]
    if b"\x00" in head:
        return True
    ctrl = sum(1 for b in head if b < 9 or 13 < b < 32)
    return (ctrl / float(len(head))) > 0.30


def _decode(data):
    """bytes -> (text, encoding)；解不开抛 ValueError。"""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in _TRY_ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("既不是 UTF-8，也不是 GBK/GB18030")


def _split(text):
    """拆行（内部统一成 \\n）。

    返回 (lines, newline, ends_with_newline)：
      · newline 是**原文件**的换行风格（"\\r\\n" / "\\r" / "\\n"）；
      · ends_with_newline 记原文件末尾有没有换行，写回时要保持原样。
    """
    if not text:
        return [], "\n", False
    if "\r\n" in text:
        nl = "\r\n"
    elif "\r" in text:
        nl = "\r"
    else:
        nl = "\n"
    work = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = work.split("\n")
    ends = False
    if lines and lines[-1] == "":
        lines.pop()
        ends = True
    return lines, nl, ends


def _encode_with(text, encoding):
    """按原编码写回；原编码编不出来时返回 (bytes, 实际编码, 是否换过编码)。"""
    try:
        return text.encode(encoding), encoding, False
    except UnicodeEncodeError:
        # 原文件是 GBK，但新写进来的内容有 GBK 编不出来的字（emoji/生僻字）
        # -> 宁可整体升级成 UTF-8，也绝不写成乱码或半个字符（但要在结果里说明）。
        return text.encode("utf-8"), "utf-8", True


def _atomic_write(path, data):
    """先写 `.tmp` 再 os.replace —— 中途失败绝不动原文件。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        return None
    except OSError as e:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return e


def _resolve_read(ctx, raw):
    """只读路径解析（工作区外允许，相对路径相对当前对话的 files/）。"""
    try:
        from . import agent as _ag
        base = getattr(ctx, "files_dir", None) or os.getcwd()
        p = _ag._safe_path(base, raw, allow_outside=True)
        return p, None
    except ValueError as e:
        return None, "[错误] " + str(e)
    except Exception as e:                       # noqa: BLE001
        return None, f"[错误] 路径解析失败：{e}"


def _resolve_write(ctx, raw, confirm_cb, what):
    """写入路径解析 + 沙箱闸门（跟 write_file 走同一套）。"""
    fn = getattr(ctx, "_resolve_write", None)
    if callable(fn):
        try:
            return fn(raw, confirm_cb, what)
        except Exception as e:                   # noqa: BLE001
            return None, f"[错误] 路径检查失败：{e}"
    # 没有 ctx（单测直接调）时退化成"相对 cwd"
    p = os.path.abspath(raw)
    return p, None


def _read_source(path, raw):
    """读文件并解码。返回 (lines, nl, ends, encoding, err)。"""
    if os.path.isdir(path):
        return None, None, None, None, (
            f"[错误] {raw} 是一个目录，不能按文本文件读；要列目录请用 list_dir。")
    if not os.path.isfile(path):
        return None, None, None, None, (
            f"[错误] 文件不存在：{raw}（解析为 {path}）。"
            "路径写错了？先用 list_dir / find_lines 确认一下。")
    try:
        if os.path.getsize(path) > MAX_SOURCE_BYTES:
            return None, None, None, None, (
                f"[错误] {raw} 太大了（{_mb(path)}），不按文本整份读。\n"
                "请改用 find_lines 定位到具体行号，再用 shell 工具（run_command）看那一段。")
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:                       # noqa: BLE001
        return None, None, None, None, f"[错误] 读不了 {path}：{type(e).__name__}: {e}"
    if _is_binary(data):
        return None, None, None, None, (
            f"[错误] {raw} 看起来是**二进制文件**（zip/图片/exe/编译产物之类），"
            "我不按文本读。要看图片用 read_image，Word/PDF/Excel 用 read_document。")
    try:
        text, enc = _decode(data)
    except ValueError as e:
        return None, None, None, None, (
            f"[错误] 读不了 {raw}：{e}（可能是一种少见的编码或文件已损坏）。")
    lines, nl, ends = _split(text)
    return lines, nl, ends, enc, None


# =====================================================================
# 工具 1：read_file（分批读 + 行号）
# =====================================================================
def read_file(args, ctx=None, confirm_cb=None):
    a = _args(args)
    raw = _s(a, "path", "file", "filename", "路径", "文件")
    if not raw:
        return ("[错误] read_file 需要 path 参数（要读的文件名/路径，例如 "
                "app/agent.py）。想看目录有什么先用 list_dir。")
    path, err = _resolve_read(ctx, raw)
    if err:
        return err
    lines, _nl, _ends, _enc, err = _read_source(path, raw)
    if err:
        return err

    total = len(lines)
    base = getattr(ctx, "files_dir", None) or os.getcwd()
    disp = _rel(path, base)
    outside = ""
    try:
        b = os.path.abspath(base)
        p = os.path.abspath(path)
        if not (p == b or p.startswith(b + os.sep)):
            outside = "（工作区外只读）"
    except Exception:
        pass

    if total == 0:
        return f"# {disp}{outside}  共 0 行（空文件）\n（这个文件是空的，没有任何内容可读。）"

    # start：兼容老写法的 offset（0 基）
    start = _as_int(a.get("start"), None)
    if start is None:
        off = _as_int(a.get("offset"), None)
        start = (off + 1) if off is not None else 1
    limit = _as_int(a.get("limit"), DEFAULT_LIMIT)
    if limit is None or limit <= 0:
        limit = DEFAULT_LIMIT
    limit = min(limit, MAX_LIMIT)
    note = ""
    if start < 1:
        note = "（start 小于 1，已按 1 处理）"
        start = 1

    if start > total:
        tail_start = max(1, total - limit + 1)
        return (f"[提示] {disp} 一共只有 {total} 行，start={start} 已经超出文件末尾。\n"
                f"要读最后一段就用 start={tail_start}, limit={limit}；"
                f"要从头读就用 start=1。")

    end = min(total, start + limit - 1)

    width = max(4, len(str(end)))
    body = []
    chars = 0
    last = start - 1
    for i in range(start - 1, end):
        s = lines[i]
        if len(s) > MAX_LINE_CHARS:
            extra = len(s) - MAX_LINE_CHARS
            s = s[:MAX_LINE_CHARS] + f"…（本行还有 {extra} 字符）"
        row = f"{i + 1:>{width}}│ {s}"
        if body and chars + len(row) > MAX_RETURN_CHARS:
            break
        body.append(row)
        chars += len(row) + 1
        last = i + 1

    # 表头必须用**实际返回到的最后一行**（不是简单按 limit 算的 end）：
    # 单行特别长时会被总字数预算提前截住，写 1~200 却只给了 80 行就是撒谎。
    head = f"# {disp}{outside}  共 {total} 行，本次返回第 {start}~{last} 行{note}"

    out = [head] + body
    rest = total - last
    if rest > 0:
        out.append(f"# 还有 {rest} 行没显示；继续读请用 start={last + 1}, limit={limit}")
    else:
        out.append(f"# 已读到文件末尾（共 {total} 行）")
    # v9.22.1 护栏：一次读太多等于没读（中间会漏、上下文也会被塞爆）
    nread = last - start + 1
    if nread > BIG_READ_LINES:
        out.append(f"[提示] 本次读了 {nread} 行，共 {total} 行。"
                   f"下次用 start/limit 只读你要的那段，或先 find_lines 定位。")
    return "\n".join(out)


# =====================================================================
# 工具 2：find_lines（按内容查行号）
# =====================================================================
def _skip_dir(name):
    low = name.lower()
    if low in _SKIP_DIRS:
        return True
    # build / dist / dist_v920 这类产物目录
    return low.startswith("dist") or low.startswith("build")


def _match_glob(rel, name, pat):
    if not pat:
        return True
    pat = pat.replace("\\", "/")
    rel = rel.replace("\\", "/")
    if "/" in pat:
        return fnmatch.fnmatch(rel.lower(), pat.lower())
    return fnmatch.fnmatch(name.lower(), pat.lower())


def _iter_files(root, pat):
    """列出要扫的文件（跳过 .git / __pycache__ / node_modules / dist* …）。"""
    if os.path.isfile(root):
        return [root]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
        for fn in sorted(filenames):
            if not _match_glob(os.path.join(dirpath, fn), fn, pat):
                continue
            out.append(os.path.join(dirpath, fn))
    return out


def find_lines(args, ctx=None, confirm_cb=None):
    a = _args(args)
    needle = _s(a, "pattern", "text", "query", "keyword", "search", "内容", "关键词")
    if not needle:
        return ("[错误] find_lines 需要 pattern 参数（要搜的文本或正则，例如 "
                "'def main' 或 'def \\w+_tmp'）。")
    raw = _s(a, "path", "dir", "file", "root", "目录") or "."
    pat = _s(a, "glob", "include", "文件名")
    is_re = _as_bool(a.get("regex"), False)
    cs = _as_bool(a.get("case_sensitive"), True)
    ctxn = _as_int(a.get("context"), DEFAULT_CONTEXT)
    if ctxn is None or ctxn < 0:
        ctxn = 0
    ctxn = min(ctxn, MAX_CONTEXT)
    maxr = _as_int(a.get("max_results"), DEFAULT_MAX_RESULTS)
    if maxr is None or maxr <= 0:
        maxr = DEFAULT_MAX_RESULTS
    maxr = min(maxr, MAX_MAX_RESULTS)

    rx = None
    if is_re:
        flags = 0 if cs else re.IGNORECASE
        try:
            rx = re.compile(needle, flags)
        except re.error as e:
            return (f"[错误] 正则不合法：{e}\n"
                    f"（pattern={needle!r}）改一下写法，或者把 regex 设为 false "
                    "按纯文本包含来搜。")
    low_needle = needle if cs else needle.lower()

    path, err = _resolve_read(ctx, raw)
    if err:
        return err
    if not os.path.exists(path):
        return f"[错误] 找不到要搜的路径：{raw}（解析为 {path}）"

    files = _iter_files(path, pat)
    show_base = path if os.path.isdir(path) else (os.path.dirname(path) or ".")

    hits = 0
    shown = 0
    hit_files = set()
    skipped_big = 0
    skipped_bin = 0
    blocks = []

    for fp in files:
        try:
            size = os.path.getsize(fp)
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            skipped_big += 1
            continue
        if size == 0:
            continue
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if _is_binary(data):
            skipped_bin += 1
            continue
        try:
            text, _enc = _decode(data)
        except ValueError:
            # 搜不动编码就按"替换非法字节"硬读 —— 搜索场景不该因为一个怪字节整份放弃
            text = data.decode("utf-8", errors="replace")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        rel = _rel(fp, show_base) if os.path.isdir(path) else os.path.basename(fp)

        local = []
        for i, line in enumerate(lines):
            if rx is not None:
                ok = bool(rx.search(line))
            elif cs:
                ok = needle in line
            else:
                ok = low_needle in line.lower()
            if not ok:
                continue
            hits += 1
            hit_files.add(fp)
            if shown >= maxr:
                continue
            shown += 1
            local.append((i + 1, i, line))
        if not local:
            continue

        # 行号对齐宽度（按本文件最大行号算）
        width = max(4, len(str(min(len(lines), max(x[0] for x in local) + ctxn))))
        for lineno, idx, line in local:
            if ctxn <= 0:
                blocks.append(f"{rel}:{lineno}: {line.rstrip()}")
                continue
            blocks.append(f"{rel}:{lineno}:")
            lo = max(0, idx - ctxn)
            hi = min(len(lines) - 1, idx + ctxn)
            for j in range(lo, hi + 1):
                mark = "▶" if j == idx else " "
                blocks.append(f"{mark} {j + 1:>{width}}│ {lines[j].rstrip()}")

    nfiles = len(hit_files)
    if hits == 0:
        scope = f"{raw}" + (f"（glob={pat}）" if pat else "")
        extra = []
        if skipped_big:
            extra.append(f"{skipped_big} 个大文件（单个 > 2MB）")
        if skipped_bin:
            extra.append(f"{skipped_bin} 个二进制文件")
        tail = ("\n（另外跳过了 " + "、".join(extra) + "）") if extra else ""
        return (f"没找到：在 {scope} 里没有任何一行包含 {needle!r}"
                + ("（按正则匹配）" if is_re else "") + f"。{tail}\n"
                "换个关键词，或把 case_sensitive 设为 false、regex 设为 true 再试。")

    # 统计句必须把「共命中多少 / 只给了多少 / 怎么收窄」说全：
    # 否则模型看到满屏结果会以为已经搜全了，后面就按错误的结论干活。
    head = f"# 共命中 {hits} 处（分布在 {nfiles} 个文件）"
    if hits > shown:
        head += (f"；已显示前 {shown} 处，还有 {hits - shown} 处没显示"
                 f" —— 用 path 指定文件/目录，或加 glob（如 glob=\"*.py\"）收窄范围再搜，"
                 f"别以为这就是全部")
    else:
        head += f"；已全部显示（{shown} 处）"
    out = [head] + blocks
    if skipped_big:
        out.append(f"# 跳过了 {skipped_big} 个大文件（单个超过 2MB，按文本搜不动）")
    if skipped_bin:
        out.append(f"# 跳过了 {skipped_bin} 个二进制文件")
    return "\n".join(out)


# =====================================================================
# 工具 3：edit_lines（按行号小范围改）
# =====================================================================
def _line_range_error(total, start, end):
    """行号合法性检查，返回中文错误或 None。"""
    if start < 1:
        return (f"[拒绝] 行号不合法：start={start}。行号从 **1** 开始数（不是 0 基），"
                "第 1 行就是 start=1。")
    if end < 0:
        return (f"[拒绝] 行号不合法：end={end}。end 最小只能是 0"
                "（start=1, end=0 表示在文件最前面插入）。")
    if start > end + 1:
        return (f"[拒绝] 行号不合法：start={start} 大于 end+1={end + 1}。"
                "（end 至少要等于 start-1；end=start-1 表示「在 start 前面插入、不删行」。）")
    # 纯插入允许插到末尾（start == total+1 且 end == total）
    if start > total + 1:
        return (f"[拒绝] 行号越界：这个文件只有 {total} 行，你给的 start={start}。"
                f"（要读一下确认行号：read_file(path=..., start=..., limit=...)）")
    if end > total:
        return (f"[拒绝] 行号越界：这个文件只有 {total} 行，你给的 end={end}。"
                f"（要读一下确认行号：read_file(path=..., start=..., limit=...)）")
    return None


def _build_result(lines, start, end, new_text, nl, ends):
    """把 [start,end] 换成 new_text，返回 (new_lines, replaced_count, inserted_count)。"""
    new_lines = []
    if new_text != "":
        new_lines = new_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if new_lines and new_lines[-1] == "" and new_text.endswith(("\n", "\r")):
            new_lines.pop()
    head = lines[:start - 1]
    tail = lines[end:] if end >= start else lines[start - 1:]
    return head + new_lines + tail, max(0, end - start + 1), len(new_lines)


def _do_edit(raw, start, end, new_text, expect, ctx, confirm_cb, tag="edit_lines"):
    path, err = _resolve_write(ctx, raw, confirm_cb, "修改文件")
    if err:
        return err
    if not os.path.isfile(path):
        if os.path.isdir(path):
            return f"[错误] {path} 是一个目录，不能按行编辑。"
        return (f"[错误] 文件不存在：{raw}（解析为 {path}）。"
                "edit_lines 只改**已有文件**；要新建请用 write_file。")
    lines, nl, ends, enc, err = _read_source(path, raw)
    if err:
        return err
    total = len(lines)
    if total == 0:
        return (f"[拒绝] {raw} 是一个空文件（0 行），没有行可以改。"
                "要写内容请用 write_file。")

    e = _line_range_error(total, start, end)
    if e:
        return e

    # ③ expect：确认「要改的确实是这几行」
    if expect and end >= start:
        actual = "\n".join(lines[start - 1:end])
        if actual.strip() != str(expect).strip():
            shown = "\n".join(lines[start - 1:end])
            if len(shown) > 2000:
                shown = shown[:2000] + "\n…（后面还有，已省略）"
            return (f"[拒绝] 第 {start}~{end} 行的实际内容和你给的 expect 对不上，"
                    f"我**一个字都没改**（{raw} 共 {total} 行）。\n"
                    f"实际那几行是：\n{shown}\n"
                    "（比对时会忽略首尾空白，但中间的缩进 / 标点 / 文字必须完全一致。）\n"
                    f"请先用 read_file(path=\"{raw}\", start={max(1, start - 2)}, "
                    f"limit={(end - start) + 5}) 核对行号，再重新调用。")

    new_lines, replaced, inserted = _build_result(lines, start, end, new_text, nl, ends)
    text = "\n".join(new_lines)
    if ends and new_lines:
        text += "\n"
    if nl != "\n":
        text = text.replace("\n", nl)
    data, used_enc, enc_changed = _encode_with(text, enc)

    # ④ 防截断硬闸
    if not data.strip() and replaced:
        return (f"[拒绝] 这次改动会把整个 {raw} 变成空文件（原来是 {total} 行），"
                "几乎肯定是行号 / new_text 传错了。我一个字都没写。\n"
                "真要清空文件请用 write_file（它也会先问一遍）。")

    werr = _atomic_write(path, data)
    if werr is not None:
        return (f"[错误] 写入失败：{type(werr).__name__}: {werr}（路径 {path}）\n"
                "原文件没有被改动。该文件可能正被编辑器/程序占用，关掉再试。")

    try:
        ctx._note_written(path, os.path.getsize(path))
    except Exception:
        pass

    base = getattr(ctx, "files_dir", None) or os.getcwd()
    disp = _rel(path, base)
    new_total = len(new_lines)
    if end < start:
        what = (f"在第 {start} 行前插入 {inserted} 行（未删除原有内容）")
    elif new_text == "":
        what = f"删除第 {start}~{end} 行（{replaced} 行）"
    elif replaced == inserted:
        what = f"把第 {start}~{end} 行（{replaced} 行）改写为 {inserted} 行"
    else:
        what = f"把第 {start}~{end} 行（{replaced} 行）替换成 {inserted} 行"
    tail = ""
    if nl != "\n":
        tail += "（CRLF 换行已保持原样）" if nl == "\r\n" else "（CR 换行已保持原样）"
    if enc_changed:
        tail += f"（原编码 {enc} 编不出新内容里的字符，已改用 UTF-8 保存）"
    return (f"[成功] ✓ {disp}：{what}，文件从 {total} 行变成 {new_total} 行{tail}")


def edit_lines(args, ctx=None, confirm_cb=None):
    a = _args(args)
    raw = _s(a, "path", "file", "filename", "路径", "文件")
    if not raw:
        return "[错误] edit_lines 需要 path 参数（要改的文件）。"
    start = _as_int(a.get("start"), None)
    if start is None:
        start = _as_int(a.get("line"), None)
    if start is None:
        return ("[错误] edit_lines 需要 start 参数（1 基行号，含）。"
                "先用 find_lines 查行号，或用 read_file(start=..., limit=...) 看行号。")
    end = _as_int(a.get("end"), None)
    if end is None:
        end = start
    if "new_text" in a:
        new_text = a.get("new_text")
    elif "content" in a:
        new_text = a.get("content")
    elif "text" in a:
        new_text = a.get("text")
    else:
        return ("[错误] edit_lines 需要 new_text 参数（这几行改成什么）。"
                "**要删除这几行就显式传空串 \"\"**；参数缺失时我不敢猜（怕误删）。")
    if new_text is None:
        new_text = ""
    if not isinstance(new_text, str):
        new_text = str(new_text)
    expect = a.get("expect") or a.get("old_text") or ""
    if not isinstance(expect, str):
        expect = str(expect)
    return _do_edit(raw, start, end, new_text, expect, ctx, confirm_cb)


# =====================================================================
# 工具 4：apply_patch（多处一起改）
# =====================================================================
def apply_patch(args, ctx=None, confirm_cb=None):
    a = _args(args)
    raw = _s(a, "path", "file", "filename", "路径", "文件")
    if not raw:
        return "[错误] apply_patch 需要 path 参数（要改的文件）。"
    hunks = a.get("hunks") or a.get("edits") or a.get("patches")
    if isinstance(hunks, dict):
        hunks = [hunks]
    if not isinstance(hunks, list) or not hunks:
        return ("[错误] apply_patch 需要 hunks 参数：一个数组，每项 "
                '{"start":10,"end":12,"new_text":"...","expect":"原文"}。')

    parsed = []
    for i, h in enumerate(hunks):
        if not isinstance(h, dict):
            return f"[错误] 第 {i + 1} 个 hunk 不是对象（应为 {{start,end,new_text,expect}}）。"
        st = _as_int(h.get("start"), None)
        if st is None:
            return f"[错误] 第 {i + 1} 个 hunk 缺少 start（1 基行号）。"
        en = _as_int(h.get("end"), None)
        if en is None:
            en = st
        nt = h.get("new_text", h.get("content", ""))
        if nt is None:
            nt = ""
        if not isinstance(nt, str):
            nt = str(nt)
        ex = h.get("expect") or h.get("old_text") or ""
        if not isinstance(ex, str):
            ex = str(ex)
        parsed.append((i + 1, st, en, nt, ex))

    path, err = _resolve_write(ctx, raw, confirm_cb, "修改文件")
    if err:
        return err
    if not os.path.isfile(path):
        return (f"[错误] 文件不存在：{raw}（解析为 {path}）。"
                "apply_patch 只改**已有文件**；要新建请用 write_file。")
    lines, nl, ends, enc, err = _read_source(path, raw)
    if err:
        return err
    total = len(lines)
    if total == 0:
        return f"[拒绝] {raw} 是空文件，没有行可以改。"

    # 先按 start 倒序（后面先改，前面行号不会被顶偏）
    order = sorted(parsed, key=lambda x: (-x[1], -x[2]))
    working = list(lines)
    for no, st, en, nt, ex in order:
        e = _line_range_error(len(working), st, en)
        if e:
            return f"[拒绝] 第 {no} 个 hunk：{e[3:] if e.startswith('[拒绝] ') else e}\n整批放弃，文件一个字都没改。"
        if ex and en >= st:
            actual = "\n".join(working[st - 1:en])
            if actual.strip() != ex.strip():
                shown = actual
                if len(shown) > 1500:
                    shown = shown[:1500] + "\n…（已省略）"
                return (f"[拒绝] 第 {no} 个 hunk 的 expect 对不上（第 {st}~{en} 行），"
                        f"**整批放弃，文件一个字都没改**（{raw} 共 {total} 行）。\n"
                        f"那里实际是：\n{shown}\n"
                        "请先用 read_file 核对行号，再把所有 hunk 一起重发。")
        working, _r, _i = _build_result(working, st, en, nt, nl, ends)

    text = "\n".join(working)
    if ends and working:
        text += "\n"
    if nl != "\n":
        text = text.replace("\n", nl)
    data, _used_enc, enc_changed = _encode_with(text, enc)
    if not data.strip():
        return f"[拒绝] 这批 hunk 会把整个 {raw} 变成空文件，几乎肯定是参数错了。整批放弃。"

    werr = _atomic_write(path, data)
    if werr is not None:
        return (f"[错误] 写入失败：{type(werr).__name__}: {werr}（路径 {path}）\n"
                "原文件没有被改动。")

    try:
        ctx._note_written(path, os.path.getsize(path))
    except Exception:
        pass

    base = getattr(ctx, "files_dir", None) or os.getcwd()
    disp = _rel(path, base)
    rows = "、".join(f"第 {st}~{en} 行" if en >= st else f"第 {st} 行前"
                    for _no, st, en, _nt, _ex in parsed)
    tail = "（CRLF 换行已保持原样）" if nl == "\r\n" else ""
    if enc_changed:
        tail += f"（原编码 {enc} 编不出新内容里的字符，已改用 UTF-8 保存）"
    return (f"[成功] ✓ {disp}：一次改完 {len(parsed)} 处（{rows}），"
            f"文件从 {total} 行变成 {len(working)} 行{tail}")


# =====================================================================
# 注册表（agent.py 只认这张表分发）
# =====================================================================
TOOLS_DEV = {
    "read_file": {
        "fn": read_file, "readonly": True, "danger": False,
        "desc": ("读文件片段（必须给 start/limit 才高效；整读只适合不到 80 行的文件），"
                 "每行带行号。"),
    },
    "find_lines": {
        "fn": find_lines, "readonly": True, "danger": False,
        "desc": ("搜函数名/变量名/报错原文/中文关键词；返回 `文件:行号: 内容`"
                 "（context 可带前后各 10 行），是 read_file / edit_lines 的前置步骤。"),
    },
    "edit_lines": {
        "fn": edit_lines, "readonly": False, "danger": False,
        "desc": "按行号小范围替换/插入/删除（expect 兜底）。",
    },
    "apply_patch": {
        "fn": apply_patch, "readonly": False, "danger": False,
        "desc": "一次改多处，任何一处 expect 不符就整批放弃。",
    },
}


def readonly_tools():
    return [n for n, s in TOOLS_DEV.items() if s.get("readonly")]


def describe_all():
    return "\n".join(f"- {n}：{s['desc']}" for n, s in TOOLS_DEV.items())
