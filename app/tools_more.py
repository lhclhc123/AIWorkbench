# -*- coding: utf-8 -*-
"""AI 工作台 v9.18.0 —— 扩容工具集（12 个）：文档 / 图片 / 编译 / 磁盘 / 代码。

本模块是**独立可插拔**的一批工具实现，风格与 ``app/webcap.py`` 一致：
每个函数 ``fn(args: dict, ctx=None) -> str`` —— **永远返回给模型看的中文文本，
永远不抛异常**（所有异常自己兜住，返回 ``[错误] …`` + 一句可操作建议）。

为什么单独一个文件（而不是塞进 ``agent.py``）：
  · 这批工具是"重活"，跟 Agent 主循环解耦，便于单测与并行执行；
  · 接入成本极低 —— 上层只要：

        from . import tools_more
        for name, spec in tools_more.TOOLS_MORE.items():
            register(name, spec["fn"], spec["desc"],
                     danger=spec["danger"], readonly=spec["readonly"])

  · ``TOOLS_MORE`` 是唯一注册表：``{工具名: {"fn", "desc", "danger", "readonly"}}``。

工具的 12 个名字（一行一个）：
  excel_op / pdf_op / image_batch / compile_run / disk_usage / find_duplicate /
  file_hash / text_diff / pip_manage / git_op / net_info / text_op

设计红线（本文件自查保证）：
  · **不引入任何新的第三方依赖**；只用标准库 + 仓库已装的
    ``openpyxl / Pillow / reportlab / pdfminer / psutil``（PDF 合并用已装的
    ``PyPDF2``，缺失时优雅降级并说明）。
  · **所有子进程一律走 ``app/winproc.py``**（``winproc.run``）——
    本文件不出现裸 ``subprocess.run/Popen``、也不出现 ``os.system``，
    避免打包成无控制台的 GUI exe 时弹黑窗口。
  · 参数**宽容归一**：中英文 + 常见别名都认（``src``/``source``/``源``、
    ``dst``/``dest``/``target``、``path``/``file`` …），缺参数时给出**具体**的中文提示。
  · 结果**一律截断**：长列表/长文本给上限，超出注明"还有 N 条未列出"并说明怎么取全。
  · 中文路径稳：``PYTHONUTF8=1`` 下 ``os.path`` + ``utf-8-sig`` 读写，中文不乱码。
  · 危险动作（写文件 / 装包 / 编译执行 / 改仓库）在注册表里标 ``danger``，
    函数内部**再做一次校验**（第二道保险）：绝不删用户文件、绝不越权写系统目录。
"""
import glob
import hashlib
import io
import os
import re
import shutil
import stat
import sys
import tempfile
import time

from . import winproc

# --------------------------------------------------------------------------
# PDF 读写库：优先 pypdf，其次本机已装的 PyPDF2（3.0.1）。
# ⚠ 刻意放在**模块顶层**并 try/except：这样 PyInstaller 静态分析能发现并把它打进
#    exe（写在函数里的延迟 import 是扫不到的）。缺库时 PDF 合并/拆分/加页码会
#    返回中文提示，图片转 PDF（img2pdf）仍可用。
# --------------------------------------------------------------------------
_PDF_LIB_NAME = None
_PDF_READER = None
_PDF_WRITER = None
try:
    from pypdf import PdfReader as _PDF_READER, PdfWriter as _PDF_WRITER   # noqa
    _PDF_LIB_NAME = "pypdf"
except Exception:
    try:
        from PyPDF2 import PdfReader as _PDF_READER, PdfWriter as _PDF_WRITER  # noqa
        _PDF_LIB_NAME = "PyPDF2"
    except Exception:
        _PDF_READER = _PDF_WRITER = None

# --------------------------------------------------------------------------
# 通用小工具（零依赖、纯标准库）
# --------------------------------------------------------------------------
def _resolve_python():
    """解析用来跑脚本的 Python 解释器（不写死任何用户目录）。

    - 开发态：当前进程解释器（sys.executable）。
    - 打包态：排除 app 自身（frozen 时 sys.executable 是 AIWorkbench.exe，
      它并不是 Python 解释器），回落到 PATH 里能找到的 python/python3。
    """
    import shutil as _sh
    cands = [sys.executable]
    for _n in ("python.exe", "python3.exe", "python", "python3"):
        _w = _sh.which(_n)
        if _w:
            cands.append(_w)
    for _c in cands:
        if not _c or not os.path.isfile(_c):
            continue
        # 打包后的 app 自身不是 Python 解释器，跳过
        if getattr(sys, "frozen", False) and os.path.basename(_c).lower().startswith("aiworkbench"):
            continue
        return _c
    return cands[0] if cands else "python"


def _resolve_git():
    """解析 git 可执行文件：优先 PATH，找不到回落字面值 'git' 交给系统找。"""
    import shutil as _sh
    _w = _sh.which("git")
    if _w and os.path.isfile(_w):
        return _w
    return "git"


# 本机跑脚本用的解释器 / git：运行时探测（兼顾"别的电脑也能用" + 隐私扫描）
PY_EXE = _resolve_python()
GIT_EXE = _resolve_git()

MAX_LIST = 60          # 普通列表最多列多少条
MAX_TEXT = 6000        # 普通文本最多多少字符


def _err(msg):
    """统一的中文错误文本（模型看到 ``[错误]`` 就知道这一步没成）。"""
    return "[错误] " + str(msg)


def _ok(msg):
    return "[成功] " + str(msg)


def _s(v):
    """把任意值安全转成字符串。"""
    if v is None:
        return ""
    if isinstance(v, bytes):
        for enc in ("utf-8", "gbk", "mbcs", "latin-1"):
            try:
                return v.decode(enc)
            except Exception:
                continue
        return v.decode("utf-8", "replace")
    return str(v)


def _arg(args, *names, default=None):
    """按多个别名取参数（中英文都认），空串/None 视为没给。"""
    a = args if isinstance(args, dict) else {}
    for n in names:
        if n in a and a[n] not in (None, ""):
            return a[n]
    return default


def _arg_list(args, *names):
    """取一个「字符串列表」参数：支持 list / JSON 字符串 / 逗号或顿号分隔。"""
    v = _arg(args, *names)
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [_s(x).strip() for x in v if _s(x).strip()]
    txt = _s(v).strip()
    if not txt:
        return []
    if txt.startswith("["):
        try:
            import json
            data = json.loads(txt)
            if isinstance(data, list):
                return [_s(x).strip() for x in data if _s(x).strip()]
        except Exception:
            pass
    return [x.strip() for x in re.split(r"[\n,，、;；]+", txt) if x.strip()]


def _arg_int(args, *names, default=None):
    """取一个整数参数（容忍 "10" / "10 秒" / "1e3" 这类写法）。"""
    v = _arg(args, *names)
    if v is None:
        return default
    try:
        return int(float(str(v).strip()))
    except Exception:
        m = re.search(r"-?\d+", _s(v))
        return int(m.group(0)) if m else default


def _arg_bool(args, *names, default=False):
    """取一个布尔参数（中英文真值都认）。"""
    v = _arg(args, *names)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    t = _s(v).strip().lower()
    if t in ("1", "true", "yes", "y", "on", "是", "真", "对", "要", "好"):
        return True
    if t in ("0", "false", "no", "n", "off", "否", "假", "不", "不要"):
        return False
    return default


def _clip(text, limit=MAX_TEXT, head="…（已截断，共 {n} 字符）"):
    """长文本截断，并在尾部注明还剩多少。"""
    text = _s(text)
    if limit and len(text) > limit:
        return text[:limit] + "\n" + head.format(n=len(text))
    return text


def _human(n):
    """字节数 -> 人类可读。"""
    try:
        n = float(n)
    except Exception:
        return _s(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def _dec(b):
    """子进程输出字节 -> 字符串（中文 Windows 可能是 GBK）。"""
    return _s(b)


def _run(cmd, cwd=None, input_text=None, timeout=30, env=None, text=False):
    """跑一个子进程并抓输出。**唯一入口 winproc.run**（不弹窗）。

    返回 ``(rc, out, err, timed_out)``；rc 为 None 表示压根没起来。
    """
    kw = {"capture_output": True, "timeout": max(1, int(timeout))}
    if cwd:
        kw["cwd"] = cwd
    if env is not None:
        kw["env"] = env
    if input_text is not None:
        kw["input"] = _s(input_text).encode("utf-8", "replace")
    try:
        p = winproc.run(cmd, **kw)
        return (p.returncode, _dec(p.stdout), _dec(p.stderr), False)
    except Exception as e:
        out = getattr(e, "stdout", None)
        err = getattr(e, "stderr", None)
        timed_out = "imeout" in type(e).__name__
        if out is not None or err is not None or timed_out:
            return (None, _dec(out), _dec(err), timed_out)
        return (None, "", f"{type(e).__name__}: {e}", False)


def _which(names, extra_paths=()):
    """在 PATH 与若干常见安装位置里找一个可执行文件；找不到返回 None。"""
    cands = list(names) + list(extra_paths)
    for c in cands:
        try:
            if os.sep in c or "/" in c:
                if os.path.isfile(c):
                    return c
            else:
                p = shutil.which(c)
                if p:
                    return p
        except Exception:
            continue
    return None


def _ctx_base(ctx):
    """从 ctx（Agent 实例）里猜一个默认工作目录，猜不到就用当前目录。"""
    for attr in ("files_dir", "work_dir", "root", "workspace"):
        try:
            v = getattr(ctx, attr, None)
            if isinstance(v, str) and os.path.isdir(v):
                return v
            if v is not None and hasattr(v, "files_dir"):
                vv = getattr(v, "files_dir", None)
                if isinstance(vv, str) and os.path.isdir(vv):
                    return vv
        except Exception:
            continue
    return os.getcwd()


def _abs_in(base, p, default=""):
    """把相对路径接到 base 上；空则返回 default 的绝对路径。"""
    p = _s(p).strip()
    if not p:
        p = default
    if not p:
        return ""
    if os.path.isabs(p) or os.path.splitdrive(p)[0]:
        return os.path.normpath(p)
    return os.path.normpath(os.path.join(base, p))


def _ensure_parent(path):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def _rmtree(path):
    """健壮地删临时目录：Windows 上 .git 对象等是只读文件，
    直接 ``shutil.rmtree`` 会失败，这里先改可写再删。"""
    try:
        shutil.rmtree(path)
        return
    except Exception:
        pass
    for dp, dns, fns in os.walk(path, topdown=False):
        for fn in fns:
            fp = os.path.join(dp, fn)
            try:
                os.chmod(fp, stat.S_IWRITE)
                os.remove(fp)
            except Exception:
                pass
        for dn in dns:
            d = os.path.join(dp, dn)
            try:
                os.chmod(d, stat.S_IWRITE)
                os.rmdir(d)
            except Exception:
                pass
    try:
        os.chmod(path, stat.S_IWRITE)
        os.rmdir(path)
    except Exception:
        pass


def _safe_out_dir(path):
    """危险动作的第二道保险：拒绝把产物写进系统关键目录。"""
    p = os.path.abspath(path)
    win = os.environ.get("SystemRoot", r"C:\Windows")
    bad = [win, os.path.join(win, "System32"), r"C:\Program Files",
           r"C:\Program Files (x86)"]
    for b in bad:
        try:
            if p == os.path.abspath(b) or p.startswith(os.path.abspath(b) + os.sep):
                return False
        except Exception:
            continue
    return True


def _json_or_text(args, *names):
    v = _arg(args, *names)
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    try:
        import json
        return json.loads(_s(v))
    except Exception:
        return None


# ==========================================================================
# ① excel_op —— Excel 读写 / 合并 / 去重 / 分组
# ==========================================================================
def excel_op(args, ctx=None):
    """Excel 表格处理：读成文本、多表合并、按列去重、分组统计、写新表。

    什么时候用：用户给了一个 .xlsx 让你"看看里面有什么 / 把这几张表合起来 /
    按某列去重 / 按某列统计各有多少 / 生成一张新表"。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        alias = {"读": "read", "读取": "read", "查看": "read", "打开": "read",
                 "合并": "merge", "合并表": "merge", "汇总": "merge",
                 "去重": "dedupe", "唯一": "dedupe", "唯一化": "dedupe",
                 "分组": "group", "统计": "group", "计数": "group", "groupby": "group",
                 "写": "write", "写入": "write", "生成": "write", "导出": "write",
                 "new": "write", "create": "write"}
        action = alias.get(action, action)
        if action not in ("read", "merge", "dedupe", "group", "write"):
            return _err("action 不认识：%r。可用：read（读取）/ merge（合并多表）/ "
                        "dedupe（去重）/ group（分组统计）/ write（写新表）。"
                        "例：{\"action\":\"read\",\"path\":\"报表.xlsx\"}" % action)
        fn_map = {"read": _xl_read, "merge": _xl_merge, "dedupe": _xl_dedupe,
                  "group": _xl_group, "write": _xl_write}
        return fn_map[action](a, ctx)
    except Exception as e:
        return _err(f"Excel 处理出错：{type(e).__name__}: {e}。"
                    f"请检查文件是否是 .xlsx/.csv 且没有被别的程序占用。")


def _xl_load_rows(path):
    """读一张表 -> (表头 list[str], 数据行 list[list[str]], 表名)。支持 xlsx/xlsm/csv。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt"):
        raw = None
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                raw = io.open(path, encoding=enc, newline="").read()
                break
            except Exception:
                continue
        if raw is None:
            raise RuntimeError("读取失败（编码都试过了）")
        import csv
        rows = [r for r in csv.reader(io.StringIO(raw))]
        rows = [[_s(c) for c in r] for r in rows if any(_s(c).strip() for c in r)]
        if not rows:
            return [], [], os.path.basename(path)
        return rows[0], rows[1:], os.path.basename(path)
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0] if wb.worksheets else None
        if ws is None:
            return [], [], os.path.basename(path)
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append([_s(c) for c in r])
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            return [], [], ws.title
        return rows[0], rows[1:], ws.title
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _xl_read(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("read 需要 path 参数（要读取的表格路径）。"
                    "例：{\"action\":\"read\",\"path\":\"销售.xlsx\"}")
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}。请确认路径（支持 .xlsx/.xlsm/.csv）。")
    header, rows, sheet = _xl_load_rows(path)
    if not header:
        return _ok(f"表 {path}（工作表「{sheet}」）是空的，没有读到任何数据。")
    limit = _arg_int(a, "limit", "max_rows", "行数", default=MAX_LIST) or MAX_LIST
    lines = [f"[成功] 工作表「{sheet}」：共 {len(rows)} 行数据、{len(header)} 列",
             "表头：" + " | ".join(header)]
    for i, r in enumerate(rows[:limit], 1):
        lines.append(f"{i}. " + " | ".join(r))
    if len(rows) > limit:
        lines.append(f"…（还有 {len(rows) - limit} 行未列出；加大 limit 可看更多，"
                     f"或 action=group 做统计）")
    return _clip("\n".join(lines))


def _xl_resolve_cols(header, specs):
    """把「列名 / 1 基序号 / 列字母」统一解成 0 基下标列表。"""
    out = []
    for spec in specs:
        s = _s(spec).strip()
        if not s:
            continue
        idx = None
        if s.isdigit():
            idx = int(s) - 1
        elif re.fullmatch(r"[A-Za-z]", s):
            import openpyxl
            idx = openpyxl.utils.column_index_from_string(s.upper()) - 1
        else:
            for i, h in enumerate(header):
                if h == s:
                    idx = i
                    break
            if idx is None:
                for i, h in enumerate(header):
                    if s and (s in h or h in s):
                        idx = i
                        break
        if idx is not None and 0 <= idx < max(1, len(header)):
            out.append(idx)
    return out


def _xl_write_rows(path, header, rows, sheet="Sheet1"):
    """写 xlsx（表头加粗底色、冻结首行、列宽自适应）。"""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    _ensure_parent(path)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = _s(sheet) or "Sheet1"
    header = [_s(h) for h in header]
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4472C4")
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append(list(r))
    for i, h in enumerate(header, 1):
        w = max([len(_s(h))] + [len(_s(r[i - 1])) if i - 1 < len(r) else 0
                                for r in rows[:300]] or [8]) + 4
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = max(8, min(40, w))
    ws.freeze_panes = "A2"
    wb.save(path)
    return os.path.getsize(path)


def _xl_merge(a, ctx):
    base = _ctx_base(ctx)
    paths = [_abs_in(base, p) for p in _arg_list(a, "paths", "files", "src", "sources",
                                                 "文件", "列表")]
    if not paths:
        single = _abs_in(base, _arg(a, "path", "file", "文件"))
        paths = [single] if single else []
    if not paths:
        return _err("merge 需要 paths 参数（要合并的表格列表）。"
                    '例：{"action":"merge","paths":["1月.xlsx","2月.xlsx"],"out":"合并.xlsx"}')
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出", "输出路径"))
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些文件不存在：" + "；".join(missing))
    header = None
    all_rows = []
    for p in paths:
        h, rows, _ = _xl_load_rows(p)
        if not h:
            continue
        if header is None:
            header = h
        for r in rows:
            r = list(r) + [""] * (len(header) - len(r))
            all_rows.append(r[:len(header)])
    if not header:
        return _err("这些文件里没有读到表头，无法合并。")
    if not out:
        return _ok(f"合并预览：{len(paths)} 个文件、共 {len(all_rows)} 行、"
                   f"{len(header)} 列（未指定 out，故未落盘）。"
                   f'加 "out":"合并.xlsx" 就会写出文件。')
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。请换到桌面/工作区/D 盘。")
    size = _xl_write_rows(out, header, all_rows, _arg(a, "sheet", "表名", default="合并结果"))
    return _ok(f"已合并 {len(paths)} 个文件 -> {out}（{len(all_rows)} 行，"
               f"{_human(size)}）")


def _xl_dedupe(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("dedupe 需要 path 参数（要处理的表格）。"
                    '例：{"action":"dedupe","path":"名单.xlsx","columns":["姓名"]}')
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}")
    header, rows, sheet = _xl_load_rows(path)
    if not header:
        return _err("表是空的，没有数据可去重。")
    keys = _arg_list(a, "columns", "by", "key", "keys", "列", "按列")
    idxs = _xl_resolve_cols(header, keys) if keys else list(range(len(header)))
    if keys and not idxs:
        return _err(f"columns 里这些列名在表头里找不到：{keys}。"
                    f"表头是：{header}。请照抄表头里的列名。")
    seen, kept, dropped = set(), [], 0
    for r in rows:
        sig = tuple((r[i] if i < len(r) else "") for i in idxs)
        if sig in seen:
            dropped += 1
        else:
            seen.add(sig)
            kept.append(r)
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出", "输出路径"))
    by = "、".join(header[i] for i in idxs if i < len(header))
    head = (f"[成功] 按「{by}」去重：原 {len(rows)} 行 -> "
            f"保留 {len(kept)} 行，去掉 {dropped} 行重复。")
    if not out:
        return head + '\n（未指定 out，未落盘；加 "out":"去重后.xlsx" 即可写出结果。）'
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    size = _xl_write_rows(out, header, kept, _arg(a, "sheet", "表名", default="去重结果"))
    return head + f"\n结果已写入：{out}（{_human(size)}）"


def _xl_group(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("group 需要 path 参数。"
                    '例：{"action":"group","path":"销售.xlsx","by":"城市"}')
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}")
    header, rows, _ = _xl_load_rows(path)
    if not header:
        return _err("表是空的，没有数据可统计。")
    bys = _arg_list(a, "by", "columns", "group_by", "key", "按", "分组")
    idxs = _xl_resolve_cols(header, bys)
    if not idxs:
        return _err(f"by 参数需要指定分组列（列名/序号/列字母）。"
                    f"表头是：{header}。例：\"by\":\"{header[0] if header else '城市'}\"")
    agg = _s(_arg(a, "agg", "op", "聚合", default="count")).lower()
    num_idx = _xl_resolve_cols(header, _arg_list(a, "value", "sum", "数值列", "值列"))
    groups = {}
    order = []
    for r in rows:
        k = tuple((r[i] if i < len(r) else "") for i in idxs)
        if k not in groups:
            groups[k] = 0.0
            order.append(k)
        if agg in ("sum", "求和", "和") and num_idx:
            try:
                groups[k] += float((r[num_idx[0]] if num_idx[0] < len(r) else 0) or 0)
            except Exception:
                pass
        else:
            groups[k] += 1
    items = sorted(order, key=lambda k: groups[k], reverse=True)
    names = "、".join(header[i] for i in idxs if i < len(header))
    label = "求和" if agg in ("sum", "求和", "和") and num_idx else "计数"
    lines = [f"[成功] 按「{names}」分组{label}：共 {len(items)} 组、{len(rows)} 行"]
    if num_idx:
        lines.append(f"（数值列：{header[num_idx[0]]}）")
    for k in items[:MAX_LIST]:
        val = groups[k]
        vs = f"{val:.2f}".rstrip("0").rstrip(".") if label == "求和" else f"{int(val)}"
        lines.append(f"{' + '.join(k)}  ->  {vs}")
    if len(items) > MAX_LIST:
        lines.append(f"…（还有 {len(items) - MAX_LIST} 组未列出，加大 limit 或先脱敏）")
    return _clip("\n".join(lines))


def _xl_write(a, ctx):
    base = _ctx_base(ctx)
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "path", "输出", "路径"))
    if not out:
        return _err("write 需要 out 参数（写入哪个文件）。"
                    '例：{"action":"write","out":"结果.xlsx",'
                    '"columns":["姓名","分数"],"rows":[["张三",90]]}')
    header = _arg_list(a, "columns", "header", "headers", "列", "表头")
    data = _json_or_text(a, "rows", "data", "values", "数据", "行")
    if not header:
        return _err("write 需要 columns 参数（表头列名列表），"
                    '例："columns":["姓名","分数"]')
    if not isinstance(data, list) or not data:
        return _err("write 需要 rows 参数（二维数组）。"
                    '例："rows":[["张三",90],["李四",85]]')
    rows = []
    for r in data:
        if isinstance(r, (list, tuple)):
            rows.append([_s(x) for x in r])
        elif isinstance(r, dict):
            rows.append([_s(r.get(h, "")) for h in header])
        else:
            rows.append([_s(r)])
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    size = _xl_write_rows(out, header, rows, _arg(a, "sheet", "表名", default="Sheet1"))
    return _ok(f"已写入 {out}（{len(rows)} 行 × {len(header)} 列，{_human(size)}）")


# ==========================================================================
# ② pdf_op —— PDF 合并 / 拆分 / 抽页 / 加页码 / 图片转 PDF
# ==========================================================================
def _pdf_lib():
    """取 PDF 读写库（pypdf 优先，其次已装的 PyPDF2）；都没有返回 ``(None,None,None)``。"""
    return _PDF_LIB_NAME, _PDF_READER, _PDF_WRITER


def _pdf_pages(path):
    """用 pdfminer 数页数（只读，最稳）。失败返回 None。"""
    try:
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument
        with open(path, "rb") as f:
            parser = PDFParser(f)
            doc = PDFDocument(parser)
            n = 0
            for _ in doc.get_pages().keys() if hasattr(doc.get_pages(), "keys") \
                    else doc.get_pages():
                n += 1
            if n:
                return n
    except Exception:
        pass
    try:
        from pdfminer.high_level import extract_pages
        return sum(1 for _ in extract_pages(path))
    except Exception:
        return None


def _parse_page_spec(spec, total):
    """把 "1-3,5" / [1,3,5] / "2-" 解析成 0 基页号列表。"""
    s = _s(spec).strip()
    if not s:
        return list(range(total))
    nums = []
    for part in re.split(r"[,，、;；\s]+", s):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            step = 1 if b >= a else -1
            nums.extend(range(a, b + step, step))
        elif re.fullmatch(r"(\d+)\s*-", part):
            a = int(part.split("-")[0])
            nums.extend(range(a, total + 1))
        elif re.fullmatch(r"-\s*(\d+)", part):
            b = int(part.split("-")[1])
            nums.extend(range(1, b + 1))
        elif part.isdigit():
            nums.append(int(part))
    out = []
    for n in nums:
        i = n - 1
        if 0 <= i < total and i not in out:
            out.append(i)
    return out


def pdf_op(args, ctx=None):
    """PDF 处理：合并、拆分、抽页、加页码、图片转 PDF、看页数。

    什么时候用：用户要"把几个 PDF 合成一个 / 拆开 / 只要第几页 / 给每页打页码 /
    把图片做成 PDF / 这个 PDF 有几页"。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        alias = {"合并": "merge", "合成": "merge", "拼接": "merge",
                 "拆分": "split", "分割": "split", "拆开": "split",
                 "抽页": "pages", "提取页": "pages", "取页": "pages", "extract": "pages",
                 "页码": "pagenum", "加页码": "pagenum", "编号": "pagenum",
                 "图片转pdf": "img2pdf", "img2pdf": "img2pdf", "图片": "img2pdf",
                 "信息": "info", "页数": "info", "info": "info"}
        action = alias.get(action, action)
        if action not in ("merge", "split", "pages", "pagenum", "img2pdf", "info"):
            return _err("action 不认识：%r。可用：merge（合并）/ split（拆分）/ "
                        "pages（抽页）/ pagenum（加页码）/ img2pdf（图片转PDF）/ "
                        "info（看页数）" % action)
        return {"merge": _pdf_merge, "split": _pdf_split, "pages": _pdf_pages_op,
                "pagenum": _pdf_pagenum, "img2pdf": _pdf_img2pdf,
                "info": _pdf_info}[action](a, ctx)
    except Exception as e:
        return _err(f"PDF 处理出错：{type(e).__name__}: {e}。"
                    f"请确认文件没被占用、且是有效的 PDF。")


def _pdf_info(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("info 需要 path 参数（PDF 文件路径）。")
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}")
    n = _pdf_pages(path)
    size = _human(os.path.getsize(path))
    if n is None:
        return _ok(f"{path}（{size}）—— 没数出页数，可能不是标准 PDF 或加密了。")
    return _ok(f"{os.path.basename(path)}：{n} 页，{size}")


def _pdf_merge(a, ctx):
    base = _ctx_base(ctx)
    paths = [_abs_in(base, p) for p in _arg_list(a, "paths", "files", "src", "sources",
                                                 "文件", "列表")]
    if not paths:
        return _err("merge 需要 paths 参数（要合并的 PDF 列表）。"
                    '例：{"action":"merge","paths":["a.pdf","b.pdf"],"out":"合并.pdf"}')
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些文件不存在：" + "；".join(missing))
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出", "输出路径"),
                  default="merged.pdf")
    if not out.lower().endswith(".pdf"):
        out += ".pdf"
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    lib, Reader, Writer = _pdf_lib()
    if not lib:
        return _err("本机缺少 PDF 合并库（pypdf / PyPDF2 都没装），"
                    "无法合并/拆分；图片转 PDF（img2pdf）仍可用。"
                    "可让 pip_manage 安装：action=install, packages=[\"pypdf\"]")
    w = Writer()
    per = []
    for p in paths:
        r = Reader(p)
        cnt = len(r.pages)
        per.append(f"{os.path.basename(p)}({cnt}页)")
        for pg in r.pages:
            w.add_page(pg)
    _ensure_parent(out)
    with open(out, "wb") as f:
        w.write(f)
    total = len(w.pages)
    return _ok(f"已合并 {len(paths)} 个 PDF -> {out}（共 {total} 页，{_human(os.path.getsize(out))}）\n"
               f"来源：" + "、".join(per))


def _pdf_split(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("split 需要 path 参数（要拆分的 PDF）。"
                    '例：{"action":"split","path":"大文件.pdf","out_dir":"拆分结果"}')
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}")
    lib, Reader, Writer = _pdf_lib()
    if not lib:
        return _err("本机缺少 PDF 拆分库（pypdf / PyPDF2 都没装）。"
                    "可让 pip_manage 安装：action=install, packages=[\"pypdf\"]")
    out_dir = _abs_in(base, _arg(a, "out_dir", "outdir", "dst", "dest", "dir",
                                 "输出目录", "目录"), default="pdf_split")
    if not _safe_out_dir(out_dir):
        return _err(f"出于安全，不向系统目录写文件：{out_dir}。")
    os.makedirs(out_dir, exist_ok=True)
    r = Reader(path)
    total = len(r.pages)
    stem = os.path.splitext(os.path.basename(path))[0]
    made = []
    for i in range(total):
        w = Writer()
        w.add_page(r.pages[i])
        fp = os.path.join(out_dir, f"{stem}_第{i + 1}页.pdf")
        with open(fp, "wb") as f:
            w.write(f)
        made.append(fp)
        if len(made) >= 200:
            break
    return _ok(f"已把 {os.path.basename(path)}（{total} 页）拆成 {len(made)} 个单页 PDF，"
               f"放在：{out_dir}")


def _pdf_pages_op(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("pages 需要 path 参数（源 PDF）。"
                    '例：{"action":"pages","path":"a.pdf","pages":"1-3,5","out":"抽页.pdf"}')
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}")
    lib, Reader, Writer = _pdf_lib()
    if not lib:
        return _err("本机缺少 PDF 处理库（pypdf / PyPDF2 都没装）。")
    r = Reader(path)
    total = len(r.pages)
    spec = _arg(a, "pages", "page", "range", "页", "页码")
    idxs = _parse_page_spec(spec, total)
    if not idxs:
        return _err(f"pages 指定的页码没解析出有效页。共 {total} 页。"
                    '例："pages":"1-3,5" 或 "pages":[2,4,6]')
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出"), default="pages.pdf")
    if not out.lower().endswith(".pdf"):
        out += ".pdf"
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    w = Writer()
    for i in idxs:
        w.add_page(r.pages[i])
    _ensure_parent(out)
    with open(out, "wb") as f:
        w.write(f)
    return _ok(f"已从 {os.path.basename(path)}（{total} 页）抽出第 "
               f"{'、'.join(str(i + 1) for i in idxs)} 页 -> {out}（{len(idxs)} 页）")


def _pdf_pagenum(a, ctx):
    base = _ctx_base(ctx)
    path = _abs_in(base, _arg(a, "path", "file", "src", "source", "文件", "路径"))
    if not path:
        return _err("pagenum 需要 path 参数（要加页码的 PDF）。"
                    '例：{"action":"pagenum","path":"a.pdf","out":"带页码.pdf"}')
    if not os.path.isfile(path):
        return _err(f"文件不存在：{path}")
    lib, Reader, Writer = _pdf_lib()
    if not lib:
        return _err("本机缺少 PDF 处理库（pypdf / PyPDF2 都没装），无法加页码。")
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出"),
                  default=os.path.splitext(path)[0] + "_带页码.pdf")
    if not out.lower().endswith(".pdf"):
        out += ".pdf"
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    try:
        from reportlab.pdfgen import canvas as _canvas
        from reportlab.lib.pagesizes import A4
    except Exception:
        return _err("本机缺少 reportlab，无法生成页码。")
    tmpl = _arg(a, "template", "fmt", "格式", default="{n} / {total}")
    r = Reader(path)
    w = Writer()
    total = len(r.pages)
    for i, pg in enumerate(r.pages, 1):
        box = pg.mediabox
        pw, ph = float(box.width), float(box.height)
        buf = io.BytesIO()
        c = _canvas.Canvas(buf, pagesize=(pw, ph))
        c.setFont("Helvetica", 9)
        txt = _s(tmpl).replace("{n}", str(i)).replace("{total}", str(total))
        c.drawCentredString(pw / 2.0, 18, txt)
        c.save()
        buf.seek(0)
        overlay = Reader(buf)
        try:
            pg.merge_page(overlay.pages[0])
        except Exception:
            try:
                pg.mergePage(overlay.pages[0])
            except Exception:
                pass
        w.add_page(pg)
    _ensure_parent(out)
    with open(out, "wb") as f:
        w.write(f)
    return _ok(f"已给 {total} 页加上页码（格式 {tmpl}）-> {out}")


def _pdf_img2pdf(a, ctx):
    base = _ctx_base(ctx)
    paths = [_abs_in(base, p) for p in _arg_list(a, "paths", "files", "images", "src",
                                                 "图片", "列表")]
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出"), default="images.pdf")
    if not out.lower().endswith(".pdf"):
        out += ".pdf"
    if not paths:
        return _err("img2pdf 需要 paths 参数（图片列表）。"
                    '例：{"action":"img2pdf","paths":["1.png","2.jpg"],"out":"图片.pdf"}')
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些图片不存在：" + "；".join(missing))
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    try:
        from PIL import Image
        from reportlab.pdfgen import canvas as _canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
    except Exception as e:
        return _err(f"本机缺少图片/PDF 生成库：{e}")
    _ensure_parent(out)
    c = _canvas.Canvas(out, pagesize=A4)
    pw, ph = A4
    margin = 36
    for p in paths:
        try:
            with Image.open(p) as _im:
                iw, ih = _im.size
        except Exception as e:
            return _err(f"图片打不开：{p}（{e}）")
        scale = min((pw - 2 * margin) / max(1, iw), (ph - 2 * margin) / max(1, ih))
        dw, dh = iw * scale, ih * scale
        c.drawImage(ImageReader(p), (pw - dw) / 2.0, (ph - dh) / 2.0, dw, dh)
        c.showPage()
    c.save()
    return _ok(f"已把 {len(paths)} 张图片转成 PDF -> {out}（{_human(os.path.getsize(out))}）")


# ==========================================================================
# ③ image_batch —— 批量图片处理（压缩/缩放/转格式/水印/拼长图）
# ==========================================================================
def _iter_images(a, ctx, base):
    """凑齐要处理的图片：显式 paths 优先，否则扫描 dir 目录。"""
    paths = [_abs_in(base, p) for p in _arg_list(a, "paths", "files", "images",
                                                 "src", "图片", "列表")]
    if not paths:
        d = _abs_in(base, _arg(a, "dir", "directory", "folder", "目录", "文件夹"))
        if d and os.path.isdir(d):
            exts = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff")
            for fn in sorted(os.listdir(d)):
                if fn.lower().endswith(exts):
                    paths.append(os.path.join(d, fn))
    return paths


_IMG_FMT_EXT = {"JPEG": ".jpg", "JPG": ".jpg", "PNG": ".png", "BMP": ".bmp",
                "WEBP": ".webp", "GIF": ".gif", "TIFF": ".tif"}


def _parse_size(spec, iw, ih):
    """把 "800x600" / "800" / "50%" / "800x" 解析成 (w,h)。"""
    s = _s(spec).strip().lower()
    if not s:
        return iw, ih
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", s)
    if m:
        k = float(m.group(1)) / 100.0
        return max(1, int(iw * k)), max(1, int(ih * k))
    m = re.fullmatch(r"(\d+)\s*[x×*]\s*(\d*)", s)
    if m:
        w = int(m.group(1))
        h = int(m.group(2)) if m.group(2) else max(1, int(round(ih * w / max(1, iw))))
        return w, h
    if s.isdigit():
        w = int(s)
        return w, max(1, int(round(ih * w / max(1, iw))))
    return iw, ih


def image_batch(args, ctx=None):
    """批量图片处理：压缩、缩放、转格式、加水印文字、拼长图。

    什么时候用：用户要对一堆图片"压缩一下 / 改成 800 宽 / 转成 jpg / 加个文字水印 /
    上下拼成一张长图"。所有产物写进 out_dir，原图不动。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        alias = {"压缩": "compress", "瘦身": "compress", "compress": "compress",
                 "缩放": "resize", "调整尺寸": "resize", "改尺寸": "resize",
                 "转格式": "convert", "格式转换": "convert", "convert": "convert",
                 "水印": "watermark", "加水印": "watermark", "watermark": "watermark",
                 "拼长图": "montage", "长图": "montage", "拼接": "montage",
                 "montage": "montage"}
        action = alias.get(action, action)
        if action not in ("compress", "resize", "convert", "watermark", "montage"):
            return _err("action 不认识：%r。可用：compress（压缩）/ resize（缩放）/ "
                        "convert（转格式）/ watermark（加水印）/ montage（拼长图）" % action)
        return {"compress": _img_compress, "resize": _img_resize,
                "convert": _img_convert, "watermark": _img_watermark,
                "montage": _img_montage}[action](a, ctx)
    except Exception as e:
        return _err(f"图片处理出错：{type(e).__name__}: {e}。")


def _img_out_dir(a, ctx, base, paths):
    d = _abs_in(base, _arg(a, "out_dir", "outdir", "dst", "dest", "dir",
                           "输出目录", "目录"), default="images_out")
    if not _safe_out_dir(d):
        return None
    os.makedirs(d, exist_ok=True)
    return d


def _img_compress(a, ctx):
    base = _ctx_base(ctx)
    paths = _iter_images(a, ctx, base)
    if not paths:
        return _err("compress 需要 paths 或 dir 参数。"
                    '例：{"action":"compress","paths":["a.png","b.jpg"],'
                    '"out_dir":"压缩后","quality":60}')
    out_dir = _img_out_dir(a, ctx, base, paths)
    if not out_dir:
        return _err("出于安全，不向系统目录写文件。")
    q = _arg_int(a, "quality", "q", "质量", default=75) or 75
    q = max(1, min(95, q))
    try:
        from PIL import Image
    except Exception as e:
        return _err(f"本机缺少 Pillow：{e}")
    total_before = total_after = 0
    done, fails = [], []
    for p in paths:
        try:
            with Image.open(p) as _im:
                if _im.mode in ("RGBA", "P", "LA"):
                    img = _im.convert("RGBA")
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1])
                    img = bg
                else:
                    img = _im.convert("RGB")
                stem = os.path.splitext(os.path.basename(p))[0]
                dst = os.path.join(out_dir, stem + ".jpg")
                img.save(dst, "JPEG", quality=q, optimize=True)
            b0 = os.path.getsize(p)
            b1 = os.path.getsize(dst)
            total_before += b0
            total_after += b1
            done.append(f"{os.path.basename(p)}: {_human(b0)} -> {_human(b1)}")
        except Exception as e:
            fails.append(f"{os.path.basename(p)}（{type(e).__name__}）")
    if not done:
        return _err("没有一张图片被处理成功。失败：" + "；".join(fails[:8]))
    save = (1 - total_after / total_before) * 100 if total_before else 0
    lines = [f"[成功] 压缩 {len(done)}/{len(paths)} 张 -> {out_dir}",
             f"总体积 {_human(total_before)} -> {_human(total_after)}"
             f"（省了 {save:.0f}%）"]
    lines.extend(done[:MAX_LIST])
    if len(done) > MAX_LIST:
        lines.append(f"…（还有 {len(done) - MAX_LIST} 张未列出）")
    if fails:
        lines.append("失败：" + "；".join(fails[:8]))
    return _clip("\n".join(lines))


def _img_resize(a, ctx):
    base = _ctx_base(ctx)
    paths = _iter_images(a, ctx, base)
    if not paths:
        return _err("resize 需要 paths 或 dir 参数，以及 size。"
                    '例：{"action":"resize","dir":"图片","size":"800","out_dir":"缩放后"}')
    size = _arg(a, "size", "wh", "resize", "尺寸", "大小")
    if not size:
        return _err('resize 需要 size 参数：可用 "800x600"、"800"（只给宽，高度按比例）、'
                    '或 "50%"（百分比）。')
    out_dir = _img_out_dir(a, ctx, base, paths)
    if not out_dir:
        return _err("出于安全，不向系统目录写文件。")
    try:
        from PIL import Image
    except Exception as e:
        return _err(f"本机缺少 Pillow：{e}")
    done, fails = [], []
    for p in paths:
        try:
            with Image.open(p) as _im:
                w, h = _parse_size(size, *_im.size)
                src_size = _im.size
                new = _im.resize((w, h), Image.LANCZOS)
                ext = os.path.splitext(p)[1].lower() or ".png"
                dst = os.path.join(out_dir,
                                   os.path.splitext(os.path.basename(p))[0] + ext)
                if new.mode in ("RGBA", "P", "LA") and ext in (".jpg", ".jpeg"):
                    new = new.convert("RGB")
                new.save(dst)
            done.append(f"{os.path.basename(p)}: {src_size[0]}x{src_size[1]} -> {w}x{h}")
        except Exception as e:
            fails.append(f"{os.path.basename(p)}（{type(e).__name__}）")
    if not done:
        return _err("没有一张图片被缩放成功。失败：" + "；".join(fails[:8]))
    lines = [f"[成功] 缩放 {len(done)}/{len(paths)} 张 -> {out_dir}"]
    lines.extend(done[:MAX_LIST])
    if len(done) > MAX_LIST:
        lines.append(f"…（还有 {len(done) - MAX_LIST} 张未列出）")
    if fails:
        lines.append("失败：" + "；".join(fails[:8]))
    return _clip("\n".join(lines))


def _img_convert(a, ctx):
    base = _ctx_base(ctx)
    paths = _iter_images(a, ctx, base)
    if not paths:
        return _err("convert 需要 paths 或 dir 参数，以及 fmt。"
                    '例：{"action":"convert","dir":"图片","fmt":"png","out_dir":"转格式"}')
    fmt = _s(_arg(a, "fmt", "format", "to", "格式")).strip().upper().lstrip(".")
    if not fmt:
        return _err('convert 需要 fmt 参数（目标格式）：png / jpg / bmp / webp / gif / tiff。')
    fmt = {"JPG": "JPEG", "TIF": "TIFF"}.get(fmt, fmt)
    if fmt not in _IMG_FMT_EXT:
        return _err(f"不支持的格式：{fmt}。可用：png / jpg / bmp / webp / gif / tiff。")
    out_dir = _img_out_dir(a, ctx, base, paths)
    if not out_dir:
        return _err("出于安全，不向系统目录写文件。")
    try:
        from PIL import Image
    except Exception as e:
        return _err(f"本机缺少 Pillow：{e}")
    done, fails = [], []
    ext = _IMG_FMT_EXT[fmt]
    for p in paths:
        try:
            with Image.open(p) as _im:
                img = _im
                if fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
                    rgb = img.convert("RGBA")
                    bg = Image.new("RGB", rgb.size, (255, 255, 255))
                    bg.paste(rgb, mask=rgb.split()[-1])
                    img = bg
                elif fmt in ("BMP", "GIF") and img.mode == "RGBA":
                    img = img.convert("RGB")
                dst = os.path.join(out_dir,
                                   os.path.splitext(os.path.basename(p))[0] + ext)
                img.save(dst, fmt)
            with Image.open(dst) as chk:
                real_fmt = chk.format
            done.append(f"{os.path.basename(p)} -> {os.path.basename(dst)}"
                        f"（{real_fmt}）")
        except Exception as e:
            fails.append(f"{os.path.basename(p)}（{type(e).__name__}）")
    if not done:
        return _err("没有一张图片被转换成功。失败：" + "；".join(fails[:8]))
    lines = [f"[成功] 转成 {fmt}：{len(done)}/{len(paths)} 张 -> {out_dir}"]
    lines.extend(done[:MAX_LIST])
    if len(done) > MAX_LIST:
        lines.append(f"…（还有 {len(done) - MAX_LIST} 张未列出）")
    if fails:
        lines.append("失败：" + "；".join(fails[:8]))
    return _clip("\n".join(lines))


def _img_watermark(a, ctx):
    base = _ctx_base(ctx)
    paths = _iter_images(a, ctx, base)
    if not paths:
        return _err("watermark 需要 paths 或 dir 参数，以及 text。"
                    '例：{"action":"watermark","dir":"图片","text":"仅供审核","out_dir":"水印"}')
    text = _s(_arg(a, "text", "watermark", "label", "水印", "文字"))
    if not text:
        return _err('watermark 需要 text 参数（水印文字）。例："text":"仅供审核 请勿外传"')
    out_dir = _img_out_dir(a, ctx, base, paths)
    if not out_dir:
        return _err("出于安全，不向系统目录写文件。")
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        return _err(f"本机缺少 Pillow：{e}")
    font = None
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                 r"C:\Windows\Fonts\simsun.ttc"):
        try:
            if os.path.isfile(cand):
                font = ImageFont.truetype(cand, 28)
                break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    done, fails = [], []
    for p in paths:
        try:
            with Image.open(p) as _im:
                img = _im.convert("RGBA")
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                d = ImageDraw.Draw(layer)
                try:
                    tw, th = d.textsize(text, font=font)
                except Exception:
                    tw, th = (len(text) * 16, 28)
                step_x, step_y = max(160, tw + 120), max(120, th + 100)
                for y in range(0, img.size[1] + step_y, step_y):
                    for x in range(0, img.size[0] + step_x, step_x):
                        d.text((x, y), text, font=font, fill=(255, 255, 255, 60))
                out = Image.alpha_composite(img, layer)
                ext = os.path.splitext(p)[1].lower() or ".png"
                dst = os.path.join(out_dir,
                                   os.path.splitext(os.path.basename(p))[0] + ext)
                if ext in (".jpg", ".jpeg"):
                    out = out.convert("RGB")
                out.save(dst)
            done.append(os.path.basename(dst))
        except Exception as e:
            fails.append(f"{os.path.basename(p)}（{type(e).__name__}）")
    if not done:
        return _err("没有一张图片加上水印。失败：" + "；".join(fails[:8]))
    lines = [f"[成功] 给 {len(done)}/{len(paths)} 张图片加了水印「{text}」-> {out_dir}"]
    lines.extend(done[:MAX_LIST])
    if len(done) > MAX_LIST:
        lines.append(f"…（还有 {len(done) - MAX_LIST} 张未列出）")
    if fails:
        lines.append("失败：" + "；".join(fails[:8]))
    return _clip("\n".join(lines))


def _img_montage(a, ctx):
    base = _ctx_base(ctx)
    paths = _iter_images(a, ctx, base)
    if len(paths) < 2:
        return _err("montage 至少需要 2 张图片（paths 或 dir 指定）。"
                    '例：{"action":"montage","paths":["1.jpg","2.jpg"],"out":"长图.jpg"}')
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出"), default="长图.jpg")
    if not _safe_out_dir(out):
        return _err("出于安全，不向系统目录写文件。")
    try:
        from PIL import Image
    except Exception as e:
        return _err(f"本机缺少 Pillow：{e}")
    width = _arg_int(a, "width", "w", "宽", default=None)
    imgs = []
    for p in paths:
        try:
            with Image.open(p) as _im:
                imgs.append(_im.convert("RGB"))
        except Exception:
            continue
    if len(imgs) < 2:
        return _err("能打开的图片不足 2 张，无法拼长图。")
    if not width:
        width = max(i.size[0] for i in imgs)
    width = max(1, int(width))
    gap = _arg_int(a, "gap", "间距", default=0) or 0
    scaled = []
    for im in imgs:
        h = max(1, int(round(im.size[1] * width / max(1, im.size[0]))))
        scaled.append(im.resize((width, h), Image.LANCZOS))
    total_h = sum(i.size[1] for i in scaled) + gap * (len(scaled) - 1)
    canvas = Image.new("RGB", (width, total_h), (255, 255, 255))
    y = 0
    for i in scaled:
        canvas.paste(i, (0, y))
        y += i.size[1] + gap
    _ensure_parent(out)
    canvas.save(out, quality=90)
    return _ok(f"已把 {len(scaled)} 张图拼成长图 -> {out}"
               f"（{width}x{total_h}，{_human(os.path.getsize(out))}）")


# ==========================================================================
# ④ compile_run —— 真编译 + 真运行一段代码
# ==========================================================================
_COMPILER_HINTS = {
    "c": ["gcc", "gcc.exe", "cl", "cl.exe", "clang", "clang.exe"],
    "cpp": ["g++", "g++.exe", "cl", "cl.exe", "clang++", "clang++.exe"],
    "java": ["javac", "javac.exe"],
    "go": ["go", "go.exe"],
    "rust": ["rustc", "rustc.exe"],
    "python": ["python", "python.exe", "python3"],
}


_COMPILER_CACHE = {}  # 语言 -> exe 路径（或 None）；整进程只扫一次


def _drive_roots():
    """本机存在的盘符根路径，例如 ['C:\\', 'D:\\', ...]。"""
    roots = []
    for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        r = d + ":\\"
        try:
            if os.path.exists(r):
                roots.append(r)
        except Exception:
            continue
    return roots


def _extra_paths(kind):
    """常见安装位置的候选 exe 绝对路径（Windows，跨盘扫描，可移植）。

    ★ v9.18.0 重构：不写死本机某一处，而是把「各盘符下 Dev-C++/MinGW/MSYS/
      LLVM/JDK/Go/Rust 的常见落点」都列出来；装到哪台电脑都能找到
      （含用户本机 ``D:\\软件\\C++\\Dev-Cpp\\MinGW64\\bin\\g++.exe``）。
    """
    out = []
    home = os.path.expanduser("~")
    if kind == "c":
        out += [r"C:\MinGW\bin\gcc.exe", r"C:\msys64\mingw64\bin\gcc.exe",
                r"C:\ProgramData\mingw64\mingw64\bin\gcc.exe",
                r"C:\Program Files\LLVM\bin\clang.exe"]
    elif kind == "cpp":
        out += [r"C:\MinGW\bin\g++.exe", r"C:\msys64\mingw64\bin\g++.exe",
                r"C:\ProgramData\mingw64\mingw64\bin\g++.exe",
                r"C:\Program Files\LLVM\bin\clang++.exe"]
    elif kind == "java":
        for jh in (os.environ.get("JAVA_HOME"),):
            if jh:
                out += [os.path.join(jh, "bin", "javac.exe")]
        out += glob.glob(r"C:\Program Files\Java\*\bin\javac.exe")
        out += glob.glob(r"C:\Program Files (x86)\Java\*\bin\javac.exe")
    elif kind == "go":
        out += [r"C:\Program Files\Go\bin\go.exe", r"C:\Go\bin\go.exe"]
    elif kind == "rust":
        out += [os.path.join(home, r".cargo\bin\rustc.exe")]
    elif kind == "python":
        out += [PY_EXE, sys.executable]
    # ---- 各盘符候选（Dev-C++ / MinGW / MSYS 装哪台电脑都不怕）----
    for drv in _drive_roots():
        if kind in ("c", "cpp"):
            out += [
                drv + r"Dev-Cpp\MinGW64\bin\g++.exe",
                drv + r"Dev-Cpp\MinGW32\bin\g++.exe",
                drv + r"MinGW\bin\g++.exe",
                drv + r"mingw64\bin\g++.exe",
                drv + r"mingw32\bin\g++.exe",
                drv + r"msys64\mingw64\bin\g++.exe",
                drv + r"msys2\mingw64\bin\g++.exe",
                drv + r"msys64\ucrt64\bin\g++.exe",
                drv + r"DevTools\mingw64\bin\g++.exe",
                drv + r"软件\C++\Dev-Cpp\MinGW64\bin\g++.exe",
                drv + r"软件\Dev-Cpp\MinGW64\bin\g++.exe",
            ]
        elif kind == "java":
            out += glob.glob(drv + r"Program Files\Java\*\bin\javac.exe")
            out += glob.glob(drv + r"Program Files (x86)\Java\*\bin\javac.exe")
            out += glob.glob(drv + r"Java\jdk*\bin\javac.exe")
            out += glob.glob(drv + r"软件\*\jdk*\bin\javac.exe")
        elif kind == "go":
            out += [drv + r"Go\bin\go.exe", drv + r"Program Files\Go\bin\go.exe"]
        elif kind == "rust":
            out += [drv + r".cargo\bin\rustc.exe",
                    drv + r"Users\*\.cargo\bin\rustc.exe"]
        elif kind == "python":
            out += glob.glob(drv + r"Python*\python.exe")
    return out


def _scan_software_dirs(kind):
    """候选路径没命中时的兜底：只在各盘「软件 / Program Files / Dev-Cpp / MinGW
    等少量目录」里做**有界递归**（depth<=5），找到第一个就停，绝不扫整盘。
    用来兜住那种"装在了奇怪子目录"的 Dev-C++ / MinGW。"""
    targets = set()
    for n in _COMPILER_HINTS.get(kind, []):
        b = n.lower()
        targets.add(b if b.endswith(".exe") else b + ".exe")
    found = [None]
    _KWS = ("dev", "mingw", "msys", "gcc", "g++", "gpp", "java", "jdk",
            "go", "rust", "软件", "program", "tools", "portable", "green",
            "cpp", "code", "compiler", "llvm", "clang")
    _TOP = ("软件", "Program Files", "Program Files (x86)", "Dev-Cpp",
            "MinGW", "msys64", "DevTools", "Tools", "Apps", "绿色软件")

    def _walk(root, depth):
        if found[0] or depth > 5:
            return
        try:
            for name in os.listdir(root):
                if found[0]:
                    return
                p = os.path.join(root, name)
                low = name.lower()
                if low in targets and os.path.isfile(p):
                    found[0] = p
                    return
                if depth < 5 and os.path.isdir(p) and any(k in low for k in _KWS):
                    _walk(p, depth + 1)
        except Exception:
            return

    for drv in _drive_roots():
        for sub in _TOP:
            _walk(drv + sub, 0)
            if found[0]:
                return found[0]
    return found[0]


def _compiler_version(exe):
    """取编译器首行版本信息（缓存）。拿不到返回空串。"""
    if not exe:
        return ""
    key = ("ver", exe)
    if key in _COMPILER_CACHE:
        return _COMPILER_CACHE[key]
    ver = ""
    try:
        rc, out, err, _ = _run([exe, "--version"], timeout=15)
        line = (out or err or "").strip().splitlines()
        ver = line[0] if line else ""
    except Exception:
        ver = ""
    _COMPILER_CACHE[key] = ver
    return ver


def _find_compiler(lang):
    kind = {"c++": "cpp", "cplusplus": "cpp", "java": "java"}.get(lang, lang)
    if kind in _COMPILER_CACHE:
        return _COMPILER_CACHE[kind]
    if kind == "python":
        # 固定用本机装了 PyQt6 的那个解释器，跟 pip_manage 保持一致
        exe = PY_EXE if os.path.isfile(PY_EXE) else (
            shutil.which("python") or sys.executable)
        _COMPILER_CACHE[kind] = exe
        return exe
    names = _COMPILER_HINTS.get(kind, [])
    # 1) PATH + 各盘候选（最快）；2) 都没命中再做有界扫盘兜底
    exe = _which(names, _extra_paths(kind))
    if not exe:
        exe = _scan_software_dirs(kind)
    _COMPILER_CACHE[kind] = exe
    return exe


def _lang_dir(tmp):
    return tmp


def compile_run(args, ctx=None):
    """真编译并真运行一段代码（C / C++ / Java / Go / Rust / Python）。

    什么时候用：用户说"帮我编译运行这段 C++ / 看看这段代码输出什么 / 我这代码报错
    怎么办"。返回值里会**原样带上编译器的报错**（这是学 C++ 的人最需要的），
    并附带真实运行输出；运行有超时保护（默认 10 秒）。

    参数：
      · language : c / cpp / java / go / rust / python（给 path 时可省，按扩展名推断）
      · code     : 源码文本（与 path 二选一；path 优先）
      · path     : 源文件绝对/相对路径（直接编译这个文件）
      · stdin    : 运行时输入
      · timeout  : 运行超时（秒，1~60）
      · out_dir  : 把生成的可执行文件/类文件落到的目录（可选）
    """
    try:
        a = args if isinstance(args, dict) else {}
        lang = _s(_arg(a, "language", "lang", "语言")).strip().lower()
        alias = {"c": "c", "cpp": "cpp", "c++": "cpp", "cplusplus": "cpp",
                 "java": "java", "go": "go", "golang": "go", "rust": "rust",
                 "rs": "rust", "py": "python", "python3": "python", "python": "python",
                 "c语言": "c", "c++语言": "cpp"}
        lang = alias.get(lang, lang)
        base = _ctx_base(ctx)
        # ★ v9.18.0：支持直接给源文件（path），读文件当源码；语言可从扩展名推断
        src_path = _arg(a, "path", "file", "src_path", "source_file", "代码文件",
                        default=None)
        if src_path:
            src_path = _abs_in(base, src_path)
            if not os.path.isfile(src_path):
                return _err(f"指定的源文件不存在：{src_path}")
            try:
                with io.open(src_path, "r", encoding="utf-8-sig") as f:
                    code = f.read()
            except Exception as e:
                return _err(f"读源文件失败：{src_path}（{e}）")
            if not lang:
                ext = os.path.splitext(src_path)[1].lower()
                lang = {".c": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
                        ".java": "java", ".go": "go", ".rs": "rust",
                        ".py": "python"}.get(ext, "")
        else:
            code = _arg(a, "code", "source", "text", "content", "代码", "源")
        if not lang:
            return _err("compile_run 需要 language 参数：c / cpp / java / go / rust / python；"
                        '或给 path 且文件扩展名能推断语言。例：{"language":"cpp",'
                        '"code":"#include <iostream>\\nint main(){std::cout<<1+1;}"}')
        if lang not in ("c", "cpp", "java", "go", "rust", "python"):
            return _err(f"language 不认识：{lang}。支持：c / cpp / java / go / rust / python。")
        if code is None or not _s(code).strip():
            return _err("compile_run 需要 code（源码文本）或 path（源文件路径）。"
                        '例：{"language":"python","code":"print(1+1)"}')
        code = _s(code)
        timeout = _arg_int(a, "timeout", "limit", "超时", default=10) or 10
        timeout = max(1, min(timeout, 60))
        stdin = _arg(a, "stdin", "input", "输入", default=None)
        stdin = None if stdin is None else _s(stdin)
        out_dir = _arg(a, "out_dir", "output_dir", "output", "输出目录", default=None)
        out_dir = None if out_dir is None else _abs_in(base, out_dir)

        tmp = tempfile.mkdtemp(prefix="awb_run_")
        try:
            exe = _find_compiler(lang)
            if lang == "python":
                return _run_python(code, stdin, timeout, tmp)
            if not exe:
                return _err(f"本机没有找到 {'/'.join(_COMPILER_HINTS[lang])} 编译器，"
                            f"装好对应工具链后重试（或用 language=python 先跑起来）。")
            ver = _compiler_version(exe)
            preamble = (f"（使用编译器：{ver}）\n" if ver else "")
            if lang == "c":
                return preamble + _cr_c_cpp("c", exe, code, stdin, timeout, tmp, out_dir)
            if lang == "cpp":
                return preamble + _cr_c_cpp("cpp", exe, code, stdin, timeout, tmp, out_dir)
            if lang == "java":
                return preamble + _cr_java(exe, code, stdin, timeout, tmp, out_dir)
            if lang == "go":
                return preamble + _cr_go(exe, code, stdin, timeout, tmp, out_dir)
            if lang == "rust":
                return preamble + _cr_rust(exe, code, stdin, timeout, tmp, out_dir)
            return _err(f"暂不支持的语言：{lang}")
        finally:
            _rmtree(tmp)
    except Exception as e:
        return _err(f"编译运行出错：{type(e).__name__}: {e}。")


def _fmt_run(rc, out, err, timed_out, secs, timeout):
    lines = []
    if timed_out:
        lines.append(f"--- 运行 ---\n⚠ 超时（超过 {timeout} 秒被强杀），程序可能有死循环。")
    else:
        lines.append(f"--- 运行 ---（退出码 {rc}，用时 {secs:.2f}s）")
    if out:
        lines.append("标准输出：\n" + out.rstrip())
    if err:
        lines.append("标准错误：\n" + err.rstrip())
    if not out and not err:
        lines.append("（没有任何输出）")
    return "\n".join(lines)


def _run_python(code, stdin, timeout, tmp):
    fp = os.path.join(tmp, "main.py")
    with io.open(fp, "w", encoding="utf-8") as f:
        f.write(code)
    exe = PY_EXE if os.path.isfile(PY_EXE) else sys.executable
    t0 = time.time()
    rc, out, err, to = _run([exe, "-X", "utf8", fp], cwd=tmp,
                            input_text=stdin, timeout=timeout)
    secs = time.time() - t0
    if rc is None and not to and not out and not err:
        return _err(f"Python 解释器没起来：{exe}")
    head = f"[成功] Python（解释器：{exe}）"
    if rc not in (0, None) and not to:
        head = f"[错误] Python 运行返回非零退出码 {rc}（这就是报错原文）："
    return head + "\n" + _clip(_fmt_run(rc, out, err, to, secs, timeout), 8000)


def _compile_and_run(cmd, run_cmd, tmp, cwd, timeout, stdin, label, exe, out_dir=None):
    t0 = time.time()
    rc, out, err, to = _run(cmd, cwd=cwd, timeout=max(timeout, 30))
    if to:
        return _err(f"[{label}] 编译超时（超过 {max(timeout, 30)} 秒）。")
    if rc != 0:
        msg = (err or out or "").strip()
        return (_err(f"[{label}] 编译失败（编译器的原话如下）：\n" + _clip(msg, 6000))
                + f"\n\n—— 编译器：{exe}\n命令：{' '.join(cmd)}")
    compile_note = (out or "").strip()
    if compile_note:
        compile_note = "编译告警/输出：\n" + _clip(compile_note, 2000) + "\n"
    t1 = time.time()
    rc2, out2, err2, to2 = _run(run_cmd, cwd=cwd, input_text=stdin, timeout=timeout)
    secs = time.time() - t1
    head = f"[成功] {label} 编译通过（编译器：{exe}）\n"
    if compile_note:
        head += compile_note
    # ★ v9.18.0：可指定 out_dir，把生成的可执行文件落过去（Java 不搬 exe）
    if out_dir and label != "Java":
        try:
            os.makedirs(out_dir, exist_ok=True)
            built = run_cmd[0]
            if os.path.isfile(built):
                dst = os.path.join(out_dir, os.path.basename(built))
                shutil.copy2(built, dst)
                head += f"已生成可执行文件：{dst}\n"
        except Exception:
            pass
    return head + _clip(_fmt_run(rc2, out2, err2, to2, secs, timeout), 8000)


def _cr_c_cpp(lang, exe, code, stdin, timeout, tmp, out_dir=None):
    ext = ".c" if lang == "c" else ".cpp"
    src = os.path.join(tmp, "main" + ext)
    out_exe = os.path.join(tmp, "a.exe")
    with io.open(src, "w", encoding="utf-8") as f:
        f.write(code)
    base = os.path.basename(exe).lower()
    if base.startswith("cl"):
        # MSVC（cl.exe）不认 -std=，默认标准够用，单独走
        cmd = [exe, "/nologo", "/Fe:" + out_exe, src]
        return _compile_and_run(cmd, [out_exe], tmp, tmp, timeout, stdin,
                                "C++" if lang == "cpp" else "C", exe, out_dir)
    # ★ v9.18.0：C++ 标准按编译器版本**自适应降级**。
    #   用户本机 Dev-C++ 是 g++.exe (tdm64-1) 4.9.2，根本不认 -std=c++17，
    #   必须从 c++17 一路降到 c++11 / c++0x；现代编译器（GCC>=7 / Clang）则直接用 c++17。
    if lang == "cpp":
        std_flags = ["-std=c++17", "-std=c++14", "-std=c++11", "-std=c++0x", None]
    else:
        std_flags = ["-std=c11", "-std=c99", None]
    label = "C++" if lang == "cpp" else "C"
    last = None
    for sf in std_flags:
        cmd = [exe, src, "-o", out_exe]
        if sf:
            cmd.insert(1, sf)
        res = _compile_and_run(cmd, [out_exe], tmp, tmp, timeout, stdin,
                               label, exe, out_dir)
        if res.startswith("[成功]"):
            return res
        last = res
        # 仅当「不认识该 -std 选项」时才换更低标准重试；其它编译错误直接返回
        if sf and "unrecognized command line option" in res and "-std=" in res:
            continue
        return res
    return last


def _cr_java(exe, code, stdin, timeout, tmp, out_dir=None):
    m = re.search(r"\b(?:public\s+)?(?:final\s+)?class\s+([A-Za-z_$][\w$]*)", code)
    cls = m.group(1) if m else "Main"
    if not re.search(r"\bclass\s+" + re.escape(cls) + r"\b", code):
        cls = "Main"
    src = os.path.join(tmp, cls + ".java")
    with io.open(src, "w", encoding="utf-8") as f:
        f.write(code)
    javac = exe
    java_exe = _which(["java", "java.exe"], _extra_paths("java"))
    if not java_exe:
        return _err("找到了 javac 但没找到 java（运行器），无法运行。")
    rc, out, err, to = _run([javac, "-encoding", "UTF-8", src], cwd=tmp,
                            timeout=max(timeout, 40))
    if to:
        return _err("[Java] javac 编译超时。")
    if rc != 0:
        return (_err("[Java] 编译失败（编译器原话）：\n"
                     + _clip((err or out or "").strip(), 6000))
                + f"\n\n—— 命令：{javac} -encoding UTF-8 {cls}.java")
    t1 = time.time()
    rc2, out2, err2, to2 = _run([java_exe, "-Dfile.encoding=UTF-8", "-cp", tmp, cls],
                                cwd=tmp, input_text=stdin, timeout=timeout)
    secs = time.time() - t1
    head = f"Java 编译通过（类 {cls}）\n"
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
            for cf in glob.glob(os.path.join(tmp, "*.class")):
                shutil.copy2(cf, os.path.join(out_dir, os.path.basename(cf)))
            head += f"已编译到：{out_dir}\n"
        except Exception:
            pass
    return _ok(head) + _clip(
        _fmt_run(rc2, out2, err2, to2, secs, timeout), 8000)


def _cr_go(exe, code, stdin, timeout, tmp, out_dir=None):
    src = os.path.join(tmp, "main.go")
    out_exe = os.path.join(tmp, "a.exe")
    with io.open(src, "w", encoding="utf-8") as f:
        f.write(code)
    return _compile_and_run([exe, "build", "-o", out_exe, src], [out_exe],
                            tmp, tmp, timeout, stdin, "Go", exe, out_dir)


def _cr_rust(exe, code, stdin, timeout, tmp, out_dir=None):
    src = os.path.join(tmp, "main.rs")
    out_exe = os.path.join(tmp, "a.exe")
    with io.open(src, "w", encoding="utf-8") as f:
        f.write(code)
    return _compile_and_run([exe, src, "-o", out_exe], [out_exe], tmp, tmp,
                            timeout, stdin, "Rust", exe, out_dir)


# ==========================================================================
# ⑤ disk_usage —— 目录 / 磁盘占用分析（只读扫描，绝不删除）
# ==========================================================================
def disk_usage(args, ctx=None):
    """分析目录与磁盘占用：各子目录体积 Top N、最大文件 Top N、磁盘剩余。

    什么时候用：用户问"C 盘怎么满了 / 这个文件夹哪个子目录最大 / 帮我看看哪些文件
    最占地方"。**只读扫描，绝不删除任何文件**。
    """
    try:
        a = args if isinstance(args, dict) else {}
        base = _ctx_base(ctx)
        path = _abs_in(base, _arg(a, "path", "dir", "folder", "target", "目录", "路径"))
        if not path:
            return _err("disk_usage 需要 path 参数（要分析的目录）。"
                        '例：{"path":"C:\\\\Users\\\\我\\\\Downloads"}')
        if not os.path.isdir(path):
            return _err(f"目录不存在：{path}")
        top_n = _arg_int(a, "top", "top_n", "limit", "数量", default=15) or 15
        top_n = max(1, min(top_n, 100))

        dir_sizes = {}
        file_list = []
        total = 0
        file_count = 0
        for root, dirs, files in os.walk(path, followlinks=False):
            rel = os.path.relpath(root, path)
            top = rel.split(os.sep)[0] if rel != "." else "."
            for fn in files:
                fp = os.path.join(root, fn)
                try:
                    sz = os.path.getsize(fp)
                except Exception:
                    continue
                total += sz
                file_count += 1
                if top not in dir_sizes:
                    dir_sizes[top] = 0
                dir_sizes[top] += sz
                file_list.append((sz, fp))
        file_list.sort(key=lambda x: x[0], reverse=True)
        sub_dirs = sorted(((v, k) for k, v in dir_sizes.items() if k != "."),
                          reverse=True)

        lines = [f"[成功] 目录占用分析：{path}",
                 f"合计：{file_count} 个文件、{_human(total)}"]
        try:
            import psutil
            drive = os.path.splitdrive(os.path.abspath(path))[0] + os.sep
            du = psutil.disk_usage(drive)
            lines.append(f"所在磁盘 {drive}：已用 {_human(du.used)} / 共 {_human(du.total)}，"
                         f"剩余 {_human(du.free)}（{du.percent}% 已用）")
        except Exception:
            pass
        lines.append("")
        lines.append(f"—— 子目录 Top {top_n}（按体积）——")
        for sz, name in sub_dirs[:top_n]:
            lines.append(f"{_human(sz):>10}  {name}")
        if len(sub_dirs) > top_n:
            lines.append(f"…（还有 {len(sub_dirs) - top_n} 个子目录未列出，"
                         f"加大 top 或直接指定更深的目录）")
        lines.append("")
        lines.append(f"—— 最大文件 Top {top_n} ——")
        for sz, fp in file_list[:top_n]:
            try:
                rel = os.path.relpath(fp, path)
            except Exception:
                rel = fp
            lines.append(f"{_human(sz):>10}  {rel}")
        if len(file_list) > top_n:
            lines.append(f"…（还有 {len(file_list) - top_n} 个文件未列出）")
        lines.append("")
        lines.append("（本工具只读扫描，没有删除或修改任何文件。）")
        return _clip("\n".join(lines), 8000)
    except Exception as e:
        return _err(f"分析占用出错：{type(e).__name__}: {e}。"
                    f"可能是没有权限读某些子目录。")


# ==========================================================================
# ⑥ find_duplicate —— 按内容找重复文件（只出报告，不删）
# ==========================================================================
def _file_sig(path, algo="sha256", chunk=1 << 20):
    try:
        h = hashlib.new(algo)
    except Exception:
        h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def find_duplicate(args, ctx=None):
    """按内容哈希找出重复文件：给一个或多个目录，输出「同组文件 + 可回收空间」。

    什么时候用：用户问"哪个文件夹里有一模一样的重复文件 / 帮我找重复的图片"。
    **只出报告，删不删由用户决定**（本工具绝不删除）。
    """
    try:
        a = args if isinstance(args, dict) else {}
        base = _ctx_base(ctx)
        dirs = [_abs_in(base, p) for p in _arg_list(a, "paths", "dirs", "dir",
                                                    "folders", "目录", "路径")]
        if not dirs:
            single = _abs_in(base, _arg(a, "path", "folder", "目录"))
            dirs = [single] if single else []
        dirs = [d for d in dirs if d]
        if not dirs:
            return _err("find_duplicate 需要 paths 或 dir 参数（一个或多个目录）。"
                        '例：{"paths":["D:\\\\照片","D:\\\\备份"]}')
        bad = [d for d in dirs if not os.path.isdir(d)]
        if bad:
            return _err("这些目录不存在：" + "；".join(bad))
        min_size = _arg_int(a, "min_size", "min", "最小", default=1) or 1
        max_groups = _arg_int(a, "max_groups", "limit", "组数", default=40) or 40

        by_size = {}
        scanned = 0
        for d in dirs:
            for root, _dirs, files in os.walk(d, followlinks=False):
                for fn in files:
                    fp = os.path.join(root, fn)
                    try:
                        sz = os.path.getsize(fp)
                    except Exception:
                        continue
                    if sz < min_size:
                        continue
                    scanned += 1
                    by_size.setdefault(sz, []).append(fp)

        groups = []
        reclaim = 0
        for sz, files in by_size.items():
            if len(files) < 2:
                continue
            by_hash = {}
            for fp in files:
                try:
                    digest = _file_sig(fp, "sha256")
                except Exception:
                    continue
                by_hash.setdefault(digest, []).append(fp)
            for digest, same in by_hash.items():
                if len(same) >= 2:
                    groups.append((sz, same))
                    reclaim += sz * (len(same) - 1)
        groups.sort(key=lambda g: g[0] * (len(g[1]) - 1), reverse=True)

        if not groups:
            return _ok(f"在 {len(dirs)} 个目录里扫描了 {scanned} 个文件，"
                       f"没有发现内容完全相同的重复文件。")
        lines = [f"[成功] 发现 {len(groups)} 组重复文件"
                 f"（扫描 {scanned} 个文件），可回收空间约 {_human(reclaim)}："]
        for i, (sz, same) in enumerate(groups[:max_groups], 1):
            lines.append(f"\n第 {i} 组（每个 {_human(sz)}，共 {len(same)} 个，"
                         f"可省 {_human(sz * (len(same) - 1))}）：")
            for fp in same[:12]:
                lines.append("  · " + fp)
            if len(same) > 12:
                lines.append(f"    …（本组还有 {len(same) - 12} 个未列出）")
        if len(groups) > max_groups:
            lines.append(f"\n…（还有 {len(groups) - max_groups} 组未列出，"
                         f"加大 max_groups 可看全）")
        lines.append("\n（本工具只出报告，没有删除任何文件；要清理请先人工确认。）")
        return _clip("\n".join(lines), 9000)
    except Exception as e:
        return _err(f"查找重复文件出错：{type(e).__name__}: {e}。")


# ==========================================================================
# ⑦ file_hash —— 算文件哈希
# ==========================================================================
def file_hash(args, ctx=None):
    """计算文件的哈希值（md5 / sha1 / sha256，默认 sha256），支持多个文件与比对期望值。

    什么时候用：用户要"算这个文件的 sha256 / 校验下载的文件对不对 / md5 是多少"。
    """
    try:
        a = args if isinstance(args, dict) else {}
        base = _ctx_base(ctx)
        paths = [_abs_in(base, p) for p in _arg_list(a, "paths", "files", "path",
                                                     "file", "文件", "路径")]
        if not paths:
            return _err("file_hash 需要 paths 参数（一个或多个文件）。"
                        '例：{"paths":["安装包.exe"],"algo":"sha256"}')
        algo = _s(_arg(a, "algo", "algorithm", "hash", "type", "算法", default="sha256")).lower()
        algo = algo.replace("-", "")
        if algo not in ("md5", "sha1", "sha256", "sha512"):
            return _err(f"不支持的算法：{algo}。可用：md5 / sha1 / sha256（默认）/ sha512。")
        expect = _s(_arg(a, "expect", "expected", "compare", "期望", "比对")).strip().lower()
        missing = [p for p in paths if not os.path.isfile(p)]
        if missing:
            return _err("这些文件不存在：" + "；".join(missing))
        lines = [f"[成功] {algo.upper()} 哈希："]
        matched = None
        for p in paths:
            try:
                if algo == "md5":
                    h = hashlib.md5()
                elif algo == "sha1":
                    h = hashlib.sha1()
                elif algo == "sha512":
                    h = hashlib.sha512()
                else:
                    h = hashlib.sha256()
                with open(p, "rb") as f:
                    while True:
                        b = f.read(1 << 20)
                        if not b:
                            break
                        h.update(b)
                digest = h.hexdigest()
                size = _human(os.path.getsize(p))
                lines.append(f"{os.path.basename(p)}（{size}）：")
                lines.append(f"    {digest}")
                if expect:
                    eq = digest.lower() == expect
                    lines.append(f"    比对期望值：{'✔ 一致' if eq else '✘ 不一致'}")
                    matched = eq if matched is None else (matched and eq)
            except Exception as e:
                lines.append(f"{os.path.basename(p)}：读取失败（{type(e).__name__}）")
        if expect is not None:
            v = "" if matched is None else ("一致" if matched else "不一致")
            if v:
                lines.append(f"\n结论：与期望值{v}。")
        return _clip("\n".join(lines), 6000)
    except Exception as e:
        return _err(f"计算哈希出错：{type(e).__name__}: {e}。")


# ==========================================================================
# ⑧ text_diff —— 两段文本 / 两个文件逐行对比
# ==========================================================================
def _read_text_any(v, base):
    """v 若像文件路径就读文件，否则当成文本本身。返回 (文本, 来源说明)。"""
    if v is None:
        return None, ""
    s = _s(v)
    cand = s if (os.path.isabs(s) or os.path.splitdrive(s)[0]) else os.path.join(base, s)
    if len(s) < 400 and os.path.isfile(cand):
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return io.open(cand, encoding=enc).read(), os.path.basename(cand)
            except Exception:
                continue
        return None, ""
    return s, "（直接文本）"


def _find_arg_path(spec):
    """把绝对路径/相对路径字符串提取出来（用于 text_diff 判断参数是不是文件名）。"""
    return _s(spec) if spec else ""


def text_diff(args, ctx=None):
    """对比两个文件或两段文本的逐行差异（统一 diff 风格，带行号与上下文）。

    什么时候用：用户说"帮我看看这两个文件差在哪 / 这两版文本哪里改了"。
    """
    try:
        a = args if isinstance(args, dict) else {}
        base = _ctx_base(ctx)
        # 支持 a/b、left/right、file1/file2、text1/text2、src/dst 等叫法
        va = _arg(a, "a", "left", "text1", "old", "src", "a_path", "file_a",
                  "file1", "原文", "源")
        vb = _arg(a, "b", "right", "text2", "new", "dst", "b_path", "file_b",
                  "file2", "新文", "目标")
        # 也支持 path_a/path_b 这种成对命名
        if va is None and vb is None:
            pa = _arg(a, "path_a", "path1", "paths")
            pb = _arg(a, "path_b", "path2")
            if isinstance(pa, (list, tuple)) and len(pa) >= 2:
                va, vb = pa[0], pa[1]
            else:
                va, vb = pa, pb
        if va is None and vb is None:
            return _err("text_diff 需要两个输入："
                        '例：{"a":"文件1.txt","b":"文件2.txt"} 或 '
                        '{"a":"第一段文本","b":"第二段文本"}')
        ta, src_a = _read_text_any(va, base)
        tb, src_b = _read_text_any(vb, base)
        if ta is None:
            return _err(f"读不到左边的内容：{va}（既不是文件也没当文本给出）")
        if tb is None:
            return _err(f"读不到右边的内容：{vb}")
        if ta == tb:
            return _ok(f"两边内容**完全相同**（{src_a} 与 {src_b}），没有差异。")
        ctx_lines = _arg_int(a, "context", "ctx", "上下文", default=3) or 3
        ctx_lines = max(0, min(ctx_lines, 20))
        la = ta.splitlines()
        lb = tb.splitlines()
        import difflib
        diff = list(difflib.unified_diff(la, lb, fromfile=src_a or "左",
                                         tofile=src_b or "右", lineterm="",
                                         n=ctx_lines))
        if not diff:
            return _ok("两边内容按行比较后没有差异（可能只差行尾）。")
        added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        lines = [f"[成功] 逐行对比：新增 {added} 行、删除 {removed} 行"
                 f"（左：{src_a}；右：{src_b}）", ""]
        # 带行号：用记录号跟踪左右行号
        ln_a = ln_b = 0
        for d in diff:
            if d.startswith("---") or d.startswith("+++"):
                lines.append(d)
                continue
            if d.startswith("@@"):
                m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", d)
                if m:
                    ln_a, ln_b = int(m.group(1)), int(m.group(2))
                lines.append(d)
                continue
            if d.startswith("+"):
                lines.append(f"右 {ln_b:>4} | + {d[1:]}")
                ln_b += 1
            elif d.startswith("-"):
                lines.append(f"左 {ln_a:>4} | - {d[1:]}")
                ln_a += 1
            else:
                lines.append(f"          | {d[1:] if d.startswith(' ') else d}")
                ln_a += 1
                ln_b += 1
        return _clip("\n".join(lines), 8000)
    except Exception as e:
        return _err(f"文本对比出错：{type(e).__name__}: {e}。")


# ==========================================================================
# ⑨ pip_manage —— Python 包管理（用绝对路径的那个解释器）
# ==========================================================================
def pip_manage(args, ctx=None):
    """Python 包管理：list（已装包）/ show / check / install / uninstall。

    什么时候用：用户说"看看装了什么包 / 这个包装没装 / 帮我装个 xxx / 卸载 xxx"。
    **用本机装了 PyQt6 的那个解释器**（绝对路径），install/uninstall 属于危险动作。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        alias = {"列表": "list", "已装": "list", "查看": "list", "list": "list",
                 "ls": "list", "freeze": "list",
                 "详情": "show", "信息": "show", "show": "show",
                 "检查": "check", "check": "check", "verify": "check",
                 "安装": "install", "装": "install", "install": "install",
                 "卸载": "uninstall", "删": "uninstall", "uninstall": "uninstall"}
        action = alias.get(action, action)
        if action not in ("list", "show", "check", "install", "uninstall"):
            return _err("action 不认识：%r。可用：list / show / check / install / "
                        "uninstall" % action)
        exe = PY_EXE if os.path.isfile(PY_EXE) else sys.executable
        if action == "list":
            rc, out, err, to = _run([exe, "-m", "pip", "list", "--format=json"],
                                    timeout=60)
            flt = _s(_arg(a, "filter", "query", "search", "筛选", "过滤")).strip().lower()
            if out.strip().startswith("["):
                try:
                    import json
                    data = json.loads(out)
                    if flt:
                        data = [x for x in data
                                if flt in _s(x.get("name", "")).lower()]
                    data.sort(key=lambda x: _s(x.get("name", "")).lower())
                    lines = [f"[成功] 已装 {len(data)} 个包"
                             + (f"（筛选「{flt}」）" if flt else "")
                             + f"（解释器：{exe}）"]
                    for it in data[:300]:
                        lines.append(f"{it.get('name')} {it.get('version')}")
                    if len(data) > 300:
                        lines.append(f"…（还有 {len(data) - 300} 个未列出，"
                                     f"可用 filter 筛选或 show 查具体包）")
                    return _clip("\n".join(lines), 8000)
                except Exception:
                    pass
            if rc is None and not out and not err:
                return _err(f"pip 没能启动（解释器：{exe}）。")
            return _ok(f"pip list（解释器：{exe}）：\n" + _clip(out or err, 6000))
        if action in ("show", "check"):
            pkgs = _arg_list(a, "packages", "package", "name", "names", "包", "包名")
            if action == "show" and not pkgs:
                return _err("show 需要 packages 参数（包名）。"
                            '例：{"action":"show","packages":["openpyxl"]}')
            cmd = [exe, "-m", "pip", "check"] if action == "check" else \
                [exe, "-m", "pip", "show"] + pkgs
            rc, out, err, to = _run(cmd, timeout=60)
            if action == "show" and (not out.strip()) and err.strip():
                return _err(f"没查到这些包：{'、'.join(pkgs)}。"
                            f"（pip 原话：{err.strip()[:300]}）。"
                            f"可能包名拼错了，可先用 action=list 看看。")
            body = (out or "").strip() or (err or "").strip()
            head = f"[成功] {'检查依赖冲突' if action == 'check' else '包信息'}：\n"
            return _clip(head + (body or "（无输出）"), 6000)
        # install / uninstall —— 危险动作
        pkgs = _arg_list(a, "packages", "package", "name", "names", "包", "包名")
        if not pkgs:
            return _err(f"{action} 需要 packages 参数（一个或多个包名）。"
                        f'例：{{"action":"{action}","packages":["requests"]}}')
        bad = [p for p in pkgs if p.startswith("-")]
        if bad:
            return _err(f"包名不能以 '-' 开头（防止传危险参数）：{bad}")
        if action == "install":
            cmd = [exe, "-m", "pip", "install"] + pkgs
        else:
            cmd = [exe, "-m", "pip", "uninstall", "-y"] + pkgs
        rc, out, err, to = _run(cmd, timeout=600)
        if to:
            return _err(f"pip {action} 超时（超过 10 分钟）。可网络不佳时重试。")
        body = ((out or "") + ("\n" + err if err else "")).strip()
        okflag = rc == 0 or "Successfully" in body or "successfully" in body
        head = _ok(f"pip {action} {'完成' if okflag else '结束'}（解释器：{exe}）\n") \
            if okflag else _err(f"pip {action} 可能失败（退出码 {rc}）：\n")
        return _clip(head + body, 6000)
    except Exception as e:
        return _err(f"pip 操作出错：{type(e).__name__}: {e}。")


# ==========================================================================
# ⑩ git_op —— 常用 git 操作
# ==========================================================================
def _git_exe():
    if os.path.isfile(GIT_EXE):
        return GIT_EXE
    return _which(["git", "git.exe"])


def _git(a, repo, git_args, timeout=60, extra_env=None):
    exe = _git_exe()
    if not exe:
        return None, "没找到 git.exe"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    if extra_env:
        env.update(extra_env)
    cmd = [exe, "-c", "core.quotepath=false", "-c", "core.pager=cat",
           "-C", repo] + list(git_args)
    rc, out, err, to = _run(cmd, timeout=timeout, env=env)
    if to:
        return None, f"git 超时（>{timeout}s）"
    return (out, err, rc), None


def git_op(args, ctx=None):
    """常用 git 操作：status / log / diff / branch / add / commit / push / pull / init。

    什么时候用：用户说"看看仓库状态 / 最近提交了什么 / 提交一下 / 拉取推送 / 建仓库"。
    **git.exe 不在 PATH**，默认用本机便携版；仓库目录用 repo 参数（缺省当前工作区）。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "cmd", "操作", "动作")).strip().lower()
        alias = {"状态": "status", "status": "status", "st": "status",
                 "日志": "log", "提交记录": "log", "log": "log",
                 "差异": "diff", "对比": "diff", "diff": "diff",
                 "分支": "branch", "branch": "branch",
                 "添加": "add", "暂存": "add", "add": "add",
                 "提交": "commit", "commit": "commit",
                 "推送": "push", "push": "push",
                 "拉取": "pull", "pull": "pull", "更新": "pull",
                 "初始化": "init", "init": "init"}
        action = alias.get(action, action)
        if action not in ("status", "log", "diff", "branch", "add", "commit",
                          "push", "pull", "init"):
            return _err("action 不认识：%r。可用：status / log / diff / branch / "
                        "add / commit / push / pull / init" % action)
        base = _ctx_base(ctx)
        repo = _abs_in(base, _arg(a, "repo", "dir", "path", "cwd", "仓库", "目录"),
                       default=base)
        if not os.path.isdir(repo):
            return _err(f"仓库目录不存在：{repo}")
        if action != "init" and not os.path.isdir(os.path.join(repo, ".git")):
            if not os.path.exists(os.path.join(repo, ".git")):
                return _err(f"{repo} 不是一个 git 仓库（没有 .git）。"
                            f'可以用 {{"action":"init","repo":"{repo}"}} 初始化。')

        if action == "init":
            res, e = _git(a, repo, ["init"])
            if e:
                return _err(f"git init 失败：{e}")
            out, err, rc = res
            return _ok(f"已在 {repo} 初始化 git 仓库。\n" + _clip((out or err).strip(), 2000))

        if action == "status":
            res, e = _git(a, repo, ["status", "--short", "--branch"])
        elif action == "log":
            n = _arg_int(a, "limit", "n", "count", "数量", default=20) or 20
            res, e = _git(a, repo, ["log", f"-n{n}", "--oneline", "--graph",
                                    "--decorate"])
        elif action == "diff":
            args_extra = []
            if _arg_bool(a, "staged", "cached", "已暂存"):
                args_extra.append("--staged")
            tgt = _arg(a, "target", "file", "path")
            if tgt:
                args_extra.append(_s(tgt))
            res, e = _git(a, repo, ["diff"] + args_extra)
        elif action == "branch":
            res, e = _git(a, repo, ["branch", "-a", "-v"])
        elif action == "add":
            files = _arg_list(a, "files", "paths", "file", "path", "文件")
            res, e = _git(a, repo, ["add"] + (files or ["-A"]))
        elif action == "commit":
            msg = _s(_arg(a, "message", "msg", "m", "信息", "说明")).strip()
            if not msg:
                return _err('commit 需要 message 参数。例：'
                            '{"action":"commit","message":"修复登录"}')
            name = _s(_arg(a, "user_name", "author_name", "name", "作者"))
            email = _s(_arg(a, "user_email", "author_email", "email", "邮箱"))
            cflags = []
            if name:
                cflags += ["-c", f"user.name={name}"]
            if email:
                cflags += ["-c", f"user.email={email}"]
            files = _arg_list(a, "files", "paths", "文件")
            if files:
                r2, e2 = _git(a, repo, ["add"] + files)
                if e2:
                    return _err(f"提交前的 git add 失败：{e2}")
            res, e = _git(a, repo, cflags + ["commit", "-m", msg])
        elif action == "push":
            res, e = _git(a, repo, ["push"], timeout=180)
        else:  # pull
            res, e = _git(a, repo, ["pull"], timeout=180)

        if e:
            return _err(f"git {action} 失败：{e}。"
                        f"（git.exe：{_git_exe() or '未找到'}）")
        out, err, rc = res
        body = ((out or "") + ("\n" + err if err else "")).strip()
        if rc != 0:
            return _err(f"git {action} 返回非零退出码 {rc}：\n"
                        + _clip(body, 6000))
        head = f"[成功] git {action}（仓库：{repo}）\n"
        return _clip(head + (body or "（无输出）"), 6000)
    except Exception as e:
        return _err(f"git 操作出错：{type(e).__name__}: {e}。")


# ==========================================================================
# ⑪ net_info —— 本机网络信息与连通性诊断（只读）
# ==========================================================================
def _ipconfig_text():
    rc, out, err, to = _run(["ipconfig", "/all"], timeout=20)
    return out or ""


def net_info(args, ctx=None):
    """本机网络诊断：网卡 IP/掩码/MAC、默认网关、DNS、监听端口、主机连通性。

    什么时候用：用户问"我的 IP 是多少 / 网关 DNS 怎么配的 / 谁占着 8080 端口 /
    能连上某个主机吗"。**只读诊断**，不改动任何网络设置。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        alias = {"网卡": "adapters", "ip": "adapters", "地址": "adapters",
                 "adapters": "adapters", "iface": "adapters", "接口": "adapters",
                 "端口": "ports", "监听": "ports", "ports": "ports",
                 "ping": "ping", "连通": "ping", "连通性": "ping", "测试": "ping",
                 "全部": "all", "all": "all", "概况": "all"}
        action = alias.get(action, action)
        if action not in ("adapters", "ports", "ping", "all"):
            return _err("action 不认识：%r。可用：adapters（网卡/IP/网关/DNS）/ "
                        "ports（监听端口）/ ping（连通性测试）/ all（全给）" % action)

        if action == "ping":
            host = _s(_arg(a, "host", "target", "url", "主机", "地址")).strip()
            if not host:
                return _err("ping 需要 host 参数。"
                            '例：{"action":"ping","host":"www.baidu.com","port":443}')
            return _net_ping(a, host)

        blocks = []
        if action in ("adapters", "all"):
            blocks.append(_net_adapters())
        if action in ("ports", "all"):
            blocks.append(_net_ports(a))
        body = "\n\n".join(b for b in blocks if b)
        return _clip("[成功] 本机网络信息（只读诊断）\n" + body, 9000)
    except Exception as e:
        return _err(f"获取网络信息出错：{type(e).__name__}: {e}。")


def _net_adapters():
    try:
        import psutil
    except Exception as e:
        return _err(f"本机缺少 psutil，读不到网卡信息：{e}")
    lines = ["—— 网卡与地址 ——"]
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception as e:
        return _err(f"读取网卡失败：{type(e).__name__}: {e}")
    import socket
    for name, alist in addrs.items():
        st = stats.get(name)
        flag = ""
        if st is not None:
            flag = "已启用" if st.isup else "未启用"
        lines.append(f"\n[{name}] {flag}")
        for ad in alist:
            fam = ad.family
            if fam == socket.AF_INET:
                lines.append(f"  IPv4: {ad.address}  掩码: {ad.netmask}")
            elif fam == socket.AF_INET6 and not ad.address.startswith("fe80"):
                lines.append(f"  IPv6: {ad.address}")
            elif str(fam).endswith("AF_LINK") or fam == getattr(psutil, "AF_LINK", -1) \
                    or (ad.family == getattr(socket, "AF_PACKET", -999)):
                mac = _s(ad.address).replace("-", ":").upper()
                lines.append(f"  MAC : {mac}")
    # 默认网关 + DNS：从 ipconfig /all 里抠（psutil 不给这个）
    txt = _ipconfig_text()
    gws, dns = [], []
    if txt:
        for m in re.finditer(r"默认网关[^\n:：]*[:：]\s*([0-9.]+)", txt):
            if m.group(1) not in gws:
                gws.append(m.group(1))
        for m in re.finditer(r"DNS 服务器[^\n:：]*[:：]\s*([0-9.]+)", txt):
            if m.group(1) not in dns:
                dns.append(m.group(1))
        for m in re.finditer(r"Default Gateway[^\n:]*:\s*([0-9.]+)", txt):
            if m.group(1) not in gws:
                gws.append(m.group(1))
        for m in re.finditer(r"DNS Servers[^\n:]*:\s*([0-9.]+)", txt):
            if m.group(1) not in dns:
                dns.append(m.group(1))
    if gws:
        lines.append("\n默认网关：" + "、".join(gws))
    if dns:
        lines.append("DNS 服务器：" + "、".join(dns))
    return "\n".join(lines)


def _net_ports(a):
    try:
        import psutil
    except Exception as e:
        return _err(f"本机缺少 psutil，读不到端口信息：{e}")
    want_listen = _arg_bool(a, "listen", "listening", "监听", default=True)
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as e:
        return (f"—— 监听端口 ——\n拿不到端口列表（可能需要管理员权限）："
                f"{type(e).__name__}: {e}\n（可跳过这一项，或让用户用管理员身份再试。）")
    rows = []
    for c in conns:
        if want_listen and c.status != psutil.CONN_LISTEN:
            continue
        try:
            pid = c.pid
        except Exception:
            pid = None
        pname = ""
        if pid:
            try:
                pname = psutil.Process(pid).name()
            except Exception:
                pname = ""
        laddr = c.laddr
        addr = f"{laddr.ip}:{laddr.port}" if hasattr(laddr, "ip") else _s(laddr)
        rows.append((getattr(laddr, "port", 0), addr, pid, pname))
    rows.sort()
    lines = [f"—— 监听端口（{len(rows)} 个）——"]
    for port, addr, pid, pname in rows[:80]:
        lines.append(f"{addr:<26} PID={pid or '?':<8} {pname}")
    if len(rows) > 80:
        lines.append(f"…（还有 {len(rows) - 80} 个未列出）")
    return "\n".join(lines)


def _net_ping(a, host):
    port = _arg_int(a, "port", "p", "端口", default=None)
    timeout = _arg_int(a, "timeout", "超时", default=5) or 5
    import socket
    lines = [f"[成功] 连通性测试：{host}"]
    # ① DNS 解析
    try:
        ip = socket.gethostbyname(host)
        lines.append(f"DNS 解析：{host} -> {ip}")
    except Exception as e:
        ip = None
        lines.append(f"DNS 解析失败：{type(e).__name__}（域名可能拼错或没联网）")
    # ② TCP 连接（默认试 80/443，或用户指定端口）
    ports = [port] if port else [443, 80]
    for p in ports:
        if not p:
            continue
        try:
            s = socket.create_connection((host, int(p)), timeout=timeout)
            s.close()
            lines.append(f"TCP {p} 端口：✔ 可连接")
        except Exception as e:
            lines.append(f"TCP {p} 端口：✘ 连不上（{type(e).__name__}）")
    # ③ 系统 ping（可选，失败不算错）
    rc, out, err, to = _run(["ping", "-n", "1", "-w", str(timeout * 1000), host],
                            timeout=timeout + 5)
    body = (out or err or "")
    m = re.search(r"[<=](\d+)\s*ms", body)
    if rc == 0 and m:
        lines.append(f"系统 ping：✔ 延迟 {m.group(1)} ms")
    elif rc == 0:
        lines.append("系统 ping：✔ 有回应")
    else:
        lines.append("系统 ping：✘ 无回应（可能是对方禁 ping，不代表一定不通）")
    return _clip("\n".join(lines), 4000)


# ==========================================================================
# ⑫ text_op —— 文本文件处理（统计 / 查找 / 替换 / 切片 / 合并 / 转码）
# ==========================================================================
_TXT_ENCS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "big5", "latin-1")


def _read_text(path, encoding=None):
    """读文本：指定编码优先，否则依次试常见编码。返回 (文本, 实际编码)。

    第二个返回值特殊取值 ``"binary"`` 表示"这看起来是二进制文件，不是文本"。
    """
    try:
        with open(path, "rb") as fb:
            head = fb.read(8192)
    except Exception:
        return None, None
    if b"\x00" in head:
        return None, "binary"
    if encoding:
        try:
            return io.open(path, encoding=encoding).read(), encoding
        except Exception:
            pass
    for enc in _TXT_ENCS:
        try:
            return io.open(path, encoding=enc).read(), enc
        except Exception:
            continue
    return None, None


def _text_read_fail(name, why):
    """统一的"文本读不到"提示（区分二进制 / 编码问题）。"""
    if why == "binary":
        return (f"{name}：看起来是**二进制文件**（不是纯文本），text_op 处理不了。"
                f"如果是 Excel/Word/PDF，请用 excel_op / read_document / pdf_op。")
    return (f"{name}：读取失败（常见编码都试过了）。"
            f"可用 convert 指定 from=\"gbk\" 转一下编码。")


def text_op(args, ctx=None):
    """文本文件处理：统计(stats) / 查找(grep) / 替换(replace) / 按行切片(split) /
    合并多文件(join) / 编码转换(convert)。

    什么时候用：用户说"这个日志有多少行 / 找出含 error 的行 / 把 xx 换成 yy /
    把这几个 txt 合成一个 / txt 是乱码帮我转成 UTF-8"。
    """
    try:
        a = args if isinstance(args, dict) else {}
        action = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        alias = {"统计": "stats", "信息": "stats", "stats": "stats", "count": "stats",
                 "查找": "grep", "搜索": "grep", "grep": "grep", "find": "grep",
                 "替换": "replace", "replace": "replace", "sub": "replace",
                 "切片": "split", "切分": "split", "split": "split", "分割": "split",
                 "合并": "join", "拼接": "join", "join": "join", "merge": "join",
                 "转码": "convert", "编码转换": "convert", "convert": "convert",
                 "编码": "convert"}
        action = alias.get(action, action)
        if action not in ("stats", "grep", "replace", "split", "join", "convert"):
            return _err("action 不认识：%r。可用：stats（统计）/ grep（查找）/ "
                        "replace（替换）/ split（按行切片）/ join（合并）/ "
                        "convert（编码转换）" % action)
        return {"stats": _txt_stats, "grep": _txt_grep, "replace": _txt_replace,
                "split": _txt_split, "join": _txt_join,
                "convert": _txt_convert}[action](a, ctx)
    except Exception as e:
        return _err(f"文本处理出错：{type(e).__name__}: {e}。")


def _txt_paths(a, ctx, base):
    return [_abs_in(base, p) for p in _arg_list(a, "paths", "files", "path", "file",
                                                "文件", "路径")]


def _txt_stats(a, ctx):
    base = _ctx_base(ctx)
    paths = _txt_paths(a, ctx, base)
    if not paths:
        return _err("stats 需要 path 或 paths 参数（文本文件）。"
                    '例：{"action":"stats","path":"log.txt"}')
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些文件不存在：" + "；".join(missing))
    lines = ["[成功] 文本统计："]
    for p in paths:
        enc = _s(_arg(a, "encoding", "enc", "编码")).strip() or None
        txt, used = _read_text(p, enc)
        if txt is None:
            lines.append(_text_read_fail(os.path.basename(p), used))
            continue
        n_lines = txt.count("\n") + (0 if txt.endswith("\n") or not txt else 1)
        chars = len(txt)
        words = len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", txt))
        size = _human(os.path.getsize(p))
        lines.append(f"{os.path.basename(p)}（{size}，编码 {used}）："
                     f"{n_lines} 行、{chars} 字符、约 {words} 词")
    return _clip("\n".join(lines), 4000)


def _txt_grep(a, ctx):
    base = _ctx_base(ctx)
    paths = _txt_paths(a, ctx, base)
    if not paths:
        return _err("grep 需要 path 或 paths 参数，以及 pattern。"
                    '例：{"action":"grep","path":"log.txt","pattern":"error"}')
    pat = _s(_arg(a, "pattern", "keyword", "text", "q", "关键字", "关键词", "模式"))
    if not pat:
        return _err('grep 需要 pattern 参数（要查找的内容）。例："pattern":"error"')
    use_re = _arg_bool(a, "regex", "re", "正则", default=False)
    ignore_case = _arg_bool(a, "ignore_case", "icase", "忽略大小写", default=True)
    limit = _arg_int(a, "limit", "max", "条数", default=40) or 40
    flags = re.I if ignore_case else 0
    try:
        rx = re.compile(pat, flags) if use_re else None
    except Exception as e:
        return _err(f"正则写错了：{e}。可去掉 regex 用普通文本查找。")
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些文件不存在：" + "；".join(missing))
    out_lines = [f"[成功] 查找「{pat}」"
                 + ("（正则）" if use_re else "") + "："]
    hits = 0
    truncated = False
    for p in paths:
        txt, _used = _read_text(p, _s(_arg(a, "encoding", "enc", "编码")).strip() or None)
        if txt is None:
            continue
        found = []
        for i, ln in enumerate(txt.splitlines(), 1):
            m = rx.search(ln) if rx else (pat.lower() in ln.lower()
                                          if ignore_case else pat in ln)
            if m:
                found.append((i, ln))
        if not found:
            continue
        out_lines.append(f"\n{os.path.basename(p)}：命中 {len(found)} 行")
        for i, ln in found:
            if hits >= limit:
                truncated = True
                break
            out_lines.append(f"  第 {i} 行: {ln[:200]}")
            hits += 1
    if hits == 0:
        return _ok(f"在 {len(paths)} 个文件里没找到「{pat}」。"
                   f"（可试试 regex=True 用正则，或 ignore_case=True 忽略大小写）")
    if truncated:
        out_lines.append(f"\n…（已达上限 {limit} 条，加大 limit 看更多）")
    return _clip("\n".join(out_lines), 8000)


def _txt_replace(a, ctx):
    base = _ctx_base(ctx)
    paths = _txt_paths(a, ctx, base)
    if not paths:
        return _err("replace 需要 path 或 paths 参数，以及 pattern 与 replacement。"
                    '例：{"action":"replace","path":"a.txt","pattern":"旧","replacement":"新",'
                    '"out":"a_new.txt"}')
    pat = _arg(a, "pattern", "old", "from", "search", "原", "查找")
    rep = _arg(a, "replacement", "new", "to", "replace_with", "替换为", "新")
    if pat is None:
        return _err('replace 需要 pattern 参数（要被替换的内容）。')
    if rep is None:
        return _err('replace 需要 replacement 参数（替换成什么；留空则删除匹配内容，'
                    '请显式写 "replacement":""）。')
    pat, rep = _s(pat), _s(rep)
    use_re = _arg_bool(a, "regex", "re", "正则", default=False)
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些文件不存在：" + "；".join(missing))
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出"))
    out_dir = _abs_in(base, _arg(a, "out_dir", "outdir", "输出目录"))
    if not out and not out_dir:
        return _err('replace 会改内容，必须指定 out（单个文件）或 out_dir（批量）。'
                    '例："out":"结果.txt"；为安全**不会**原地覆盖源文件。')
    summary = []
    for p in paths:
        txt, used = _read_text(p, _s(_arg(a, "encoding", "enc", "编码")).strip() or None)
        if txt is None:
            summary.append(_text_read_fail(os.path.basename(p), used))
            continue
        if use_re:
            try:
                new, n = re.subn(pat, rep, txt)
            except Exception as e:
                return _err(f"正则写错了：{e}。")
        else:
            n = txt.count(pat)
            new = txt.replace(pat, rep)
        if out and len(paths) == 1:
            dst = out
        else:
            d = out_dir or os.path.dirname(os.path.abspath(out)) or base
            if not _safe_out_dir(d):
                return _err(f"出于安全，不向系统目录写文件：{d}。")
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, os.path.basename(p))
        if not _safe_out_dir(dst):
            return _err(f"出于安全，不向系统目录写文件：{dst}。")
        _ensure_parent(dst)
        with io.open(dst, "w", encoding="utf-8") as f:
            f.write(new)
        summary.append(f"{os.path.basename(p)}：替换 {n} 处 -> {dst}")
    return _clip(_ok(f"替换完成（源文件未动，结果另存）：\n") + "\n".join(summary), 6000)


def _txt_split(a, ctx):
    base = _ctx_base(ctx)
    paths = _txt_paths(a, ctx, base)
    if not paths:
        return _err("split 需要 path 或 paths 参数，以及 lines（每个文件多少行）。"
                    '例：{"action":"split","path":"大文件.txt","lines":1000,'
                    '"out_dir":"切片"}')
    n = _arg_int(a, "lines", "every", "per", "行数", "每份行数")
    if not n or n < 1:
        return _err('split 需要 lines 参数（每个切片多少行）。例："lines":1000')
    out_dir = _abs_in(base, _arg(a, "out_dir", "outdir", "dir", "输出目录", "目录"),
                      default="text_split")
    if not _safe_out_dir(out_dir):
        return _err(f"出于安全，不向系统目录写文件：{out_dir}。")
    os.makedirs(out_dir, exist_ok=True)
    report = []
    for p in paths:
        txt, _used = _read_text(p, _s(_arg(a, "encoding", "enc", "编码")).strip() or None)
        if txt is None:
            report.append(_text_read_fail(os.path.basename(p), _used))
            continue
        all_lines = txt.splitlines(keepends=True)
        stem = os.path.splitext(os.path.basename(p))[0]
        made = 0
        for i in range(0, len(all_lines), n):
            fp = os.path.join(out_dir, f"{stem}_part{made + 1:03d}.txt")
            with io.open(fp, "w", encoding="utf-8") as f:
                f.writelines(all_lines[i:i + n])
            made += 1
            if made >= 500:
                break
        report.append(f"{os.path.basename(p)}（{len(all_lines)} 行）-> {made} 个切片")
    return _clip(_ok(f"按每 {n} 行切片 -> {out_dir}\n") + "\n".join(report), 5000)


def _txt_join(a, ctx):
    base = _ctx_base(ctx)
    paths = _txt_paths(a, ctx, base)
    if len(paths) < 2:
        return _err("join 至少需要 2 个文件（paths）。"
                    '例：{"action":"join","paths":["1.txt","2.txt"],"out":"合并.txt"}')
    out = _abs_in(base, _arg(a, "out", "dst", "dest", "target", "输出"),
                  default="joined.txt")
    if not _safe_out_dir(out):
        return _err(f"出于安全，不向系统目录写文件：{out}。")
    sep = _s(_arg(a, "sep", "separator", "分隔", default="\n"))
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        return _err("这些文件不存在：" + "；".join(missing))
    _ensure_parent(out)
    total = 0
    with io.open(out, "w", encoding="utf-8") as wf:
        for i, p in enumerate(paths):
            txt, _used = _read_text(p, _s(_arg(a, "encoding", "enc", "编码")).strip() or None)
            if txt is None:
                continue
            if i:
                wf.write(sep)
            wf.write(txt)
            total += 1
    return _ok(f"已按顺序合并 {total} 个文本 -> {out}（{_human(os.path.getsize(out))}）")


def _txt_convert(a, ctx):
    base = _ctx_base(ctx)
    paths = _txt_paths(a, ctx, base)
    if not paths:
        return _err("convert 需要 path 或 paths 参数，以及 to 编码。"
                    '例：{"action":"convert","path":"乱码.txt","to":"utf-8",'
                    '"out_dir":"转码"}')
    src_enc = _s(_arg(a, "from", "src_encoding", "encoding", "源编码")).strip() or None
    dst_enc = _s(_arg(a, "to", "dst_encoding", "target", "目标编码")).strip() \
        or "utf-8"
    out_dir = _abs_in(base, _arg(a, "out_dir", "outdir", "dir", "输出目录", "目录"),
                      default="text_converted")
    if not _safe_out_dir(out_dir):
        return _err(f"出于安全，不向系统目录写文件：{out_dir}。")
    os.makedirs(out_dir, exist_ok=True)
    report = []
    for p in paths:
        txt, used = _read_text(p, src_enc)
        if txt is None:
            report.append(_text_read_fail(os.path.basename(p), used))
            continue
        dst = os.path.join(out_dir, os.path.basename(p))
        try:
            with io.open(dst, "w", encoding=dst_enc) as f:
                f.write(txt)
        except Exception as e:
            report.append(f"{os.path.basename(p)}：写出失败（{e}）；"
                          f"目标编码可能是 {dst_enc} 不支持中文")
            continue
        report.append(f"{os.path.basename(p)}：{used} -> {dst_enc}")
    return _clip(_ok(f"编码转换 -> {out_dir}\n") + "\n".join(report), 5000)


# ==========================================================================
# 注册表
# ==========================================================================
TOOLS_MORE = {
    "excel_op": {
        "fn": excel_op,
        "desc": "Excel 表格处理：读取成文本(read) / 多表合并(merge) / 按列去重(dedupe) / "
                "分组统计(group) / 写新表(write)。参数 action + path/paths/out/columns/by。",
        "danger": True, "readonly": False,
    },
    "pdf_op": {
        "fn": pdf_op,
        "desc": "PDF 处理：合并(merge) / 拆分(split) / 抽页(pages) / 加页码(pagenum) / "
                "图片转PDF(img2pdf) / 看页数(info)。参数 action + path/paths/out/pages。",
        "danger": True, "readonly": False,
    },
    "image_batch": {
        "fn": image_batch,
        "desc": "批量图片处理：压缩(compress) / 缩放(resize) / 转格式(convert) / "
                "加水印(watermark) / 拼长图(montage)。参数 action + paths/dir/out_dir/"
                "quality/size/fmt/text。",
        "danger": True, "readonly": False,
    },
    "compile_run": {
        "fn": compile_run,
        "desc": "真编译并真运行一段代码：C / C++ / Java / Go / Rust / Python。"
                "自动探测本机编译器（Dev-C++/MinGW/MSVC/JDK/Go/Rust，装哪台电脑都能找到），"
                "返回编译器报错原文与真实运行输出（运行有超时保护）。"
                "参数 language/code/stdin/timeout；也支持 path(直接编译某文件)/out_dir(产物落盘)。",
        "danger": True, "readonly": False,
    },
    "disk_usage": {
        "fn": disk_usage,
        "desc": "目录/磁盘占用分析：各子目录体积 Top N、最大文件 Top N、磁盘剩余。"
                "只读扫描，绝不删除任何文件。参数 path/top。",
        "danger": False, "readonly": True,
    },
    "find_duplicate": {
        "fn": find_duplicate,
        "desc": "按内容哈希查找重复文件：给一个或多个目录，输出同组文件与可回收空间。"
                "只出报告，绝不删除。参数 paths/dir/min_size/max_groups。",
        "danger": False, "readonly": True,
    },
    "file_hash": {
        "fn": file_hash,
        "desc": "计算文件哈希（md5/sha1/sha256/sha512，默认 sha256），支持多个文件、"
                "可选与期望值比对。参数 paths/algo/expect。",
        "danger": False, "readonly": True,
    },
    "text_diff": {
        "fn": text_diff,
        "desc": "两个文件或两段文本的逐行对比（统一 diff 风格，带行号与上下文）。"
                "参数 a/b（也认 left/right、file1/file2、src/dst）。",
        "danger": False, "readonly": True,
    },
    "pip_manage": {
        "fn": pip_manage,
        "desc": "Python 包管理：list / show / check / install / uninstall。"
                "用本机装了 PyQt6 的那个解释器；install/uninstall 为危险动作。"
                "参数 action/packages。",
        "danger": True, "readonly": False,
    },
    "git_op": {
        "fn": git_op,
        "desc": "常用 git 操作：status / log / diff / branch / add / commit / push / "
                "pull / init。用本机便携版 git.exe；add/commit/push/pull/init 为危险动作。"
                "参数 action/repo。",
        "danger": True, "readonly": False,
    },
    "net_info": {
        "fn": net_info,
        "desc": "本机网络诊断：adapters（网卡 IP/掩码/MAC/网关/DNS）/ ports（监听端口）/ "
                "ping（主机连通性）/ all。只读诊断。参数 action/host/port。",
        "danger": False, "readonly": True,
    },
    "text_op": {
        "fn": text_op,
        "desc": "文本文件处理：stats（行数/字数/编码）/ grep（查找含关键字的行）/ "
                "replace（替换并另存）/ split（按行切片）/ join（合并多文件）/ "
                "convert（编码转换 gbk<->utf-8）。参数 action/path/pattern/lines/out。",
        "danger": True, "readonly": False,
    },
}

# 按 action 动态判定的规则：{工具名: (读出无副作用的 action 集, 有副作用的 action 集)}
_ACTION_FLAGS = {
    "excel_op": ({"read", "group"}, {"merge", "dedupe", "write"}),
    "pdf_op": ({"info"}, {"merge", "split", "pages", "pagenum", "img2pdf"}),
    "pip_manage": ({"list", "show", "check"}, {"install", "uninstall"}),
    "git_op": ({"status", "log", "diff", "branch"}, {"add", "commit", "push",
                                                     "pull", "init"}),
    "text_op": ({"stats", "grep"}, {"replace", "split", "join", "convert"}),
}
# image_batch（所有 action 都写文件）/ compile_run（执行代码）/ net_info（只读）
_ALWAYS_FLAGS = {
    "image_batch": (False, True),
    "compile_run": (False, True),
    "net_info": (True, False),
    "disk_usage": (True, False),
    "find_duplicate": (True, False),
    "file_hash": (True, False),
    "text_diff": (True, False),
}

_ACTION_ALIAS = {
    "excel_op": {"读": "read", "读取": "read", "查看": "read", "打开": "read",
                 "合并": "merge", "汇总": "merge", "去重": "dedupe",
                 "分组": "group", "统计": "group", "写": "write", "写入": "write",
                 "导出": "write", "new": "write", "create": "write"},
    "pdf_op": {"合并": "merge", "合成": "merge", "拆分": "split", "分割": "split",
               "抽页": "pages", "提取页": "pages", "页码": "pagenum",
               "加页码": "pagenum", "图片转pdf": "img2pdf", "信息": "info",
               "页数": "info", "extract": "pages"},
    "pip_manage": {"列表": "list", "已装": "list", "查看": "list", "ls": "list",
                   "详情": "show", "信息": "show", "检查": "check",
                   "安装": "install", "卸载": "uninstall", "freeze": "list",
                   "verify": "check"},
    "git_op": {"状态": "status", "st": "status", "日志": "log", "提交记录": "log",
               "差异": "diff", "对比": "diff", "分支": "branch", "添加": "add",
               "暂存": "add", "提交": "commit", "推送": "push", "拉取": "pull",
               "更新": "pull", "初始化": "init"},
    "text_op": {"统计": "stats", "信息": "stats", "count": "stats", "查找": "grep",
                "搜索": "grep", "find": "grep", "替换": "replace", "sub": "replace",
                "切片": "split", "切分": "split", "分割": "split", "合并": "join",
                "拼接": "join", "merge": "join", "转码": "convert",
                "编码转换": "convert", "编码": "convert"},
}


def resolve_flags(name, args):
    """按 ``action`` 动态给出 ``(readonly, danger)``。

    注册表里的 ``readonly``/``danger`` 是**默认值**；这里再根据 action 细化：
    比如 ``excel_op`` 的 read 是只读、write 是危险。上层做"并行执行 / 需要用户确认"
    判断时优先用这个函数，拿不到再回落注册表默认值。
    """
    spec = TOOLS_MORE.get(name)
    if not spec:
        return False, True
    a = args if isinstance(args, dict) else {}
    if name in _ACTION_FLAGS:
        act = _s(_arg(a, "action", "op", "操作", "动作")).strip().lower()
        act = _ACTION_ALIAS.get(name, {}).get(act, act)
        ro, dg = _ACTION_FLAGS[name]
        if act in ro:
            return True, False
        if act in dg:
            return False, True
        return spec["readonly"], spec["danger"]
    if name in _ALWAYS_FLAGS:
        return _ALWAYS_FLAGS[name]
    return spec["readonly"], spec["danger"]


def readonly_tools():
    """注册表里默认只读（可丢进 _PARALLEL_SAFE_TOOLS）的工具名列表。"""
    return [n for n, s in TOOLS_MORE.items() if s.get("readonly")]


def describe_all():
    """把 12 个工具的「名字：说明」拼成一段中文，方便塞进系统提示词。"""
    lines = []
    for name, spec in TOOLS_MORE.items():
        flag = "只读" if spec.get("readonly") else ("需确认" if spec.get("danger") else "普通")
        lines.append(f"- {name}（{flag}）：{spec.get('desc')}")
    return "\n".join(lines)
