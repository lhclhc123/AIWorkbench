# -*- coding: utf-8 -*-
"""密钥保护：让 API Key 不以任何明文形式出现在 exe 或本地文件里。

两层：
1. 编译期混淆（obf / deobf）——写进源码的是密文，`strings` 扫不出明文 Key。
   ⚠ 诚实说明：客户端程序里的密钥，理论上一定能被逆向出来（这是所有
   桌面 App 的共同限制）。这层的作用是"防顺手翻看 / 防懒人抄"，不是密码学保证。
2. 运行期加密（DPAPI）——用户本地的 settings.json 里的 Key 用 Windows
   数据保护接口加密，密文绑定当前 Windows 账户，别的账户/拷到别的机器都解不开。
   这层是真保护。
"""
import base64
import ctypes
import hashlib
import os
from ctypes import wintypes

_IS_WIN = os.name == "nt"

# ---------------------------------------------------------------- 编译期混淆
_SEED = b"AIWorkbench::v9::local-agent::key-vault::2026-09-25"


def _keystream(n, seed=_SEED):
    out = bytearray()
    ctr = 0
    while len(out) < n:
        out += hashlib.sha256(seed + ctr.to_bytes(4, "big")).digest()
        ctr += 1
    return bytes(out[:n])


def obf(text):
    """明文 -> 密文（写进源码用）。"""
    if not text:
        return ""
    raw = text.encode("utf-8")
    ks = _keystream(len(raw))
    x = bytes(a ^ b for a, b in zip(raw, ks))
    return base64.urlsafe_b64encode(x).decode().rstrip("=")


def deobf(token):
    """密文 -> 明文。"""
    if not token:
        return ""
    s = str(token).strip()
    s += "=" * (-len(s) % 4)
    try:
        x = base64.urlsafe_b64decode(s)
    except Exception:
        return ""
    ks = _keystream(len(x))
    return bytes(a ^ b for a, b in zip(x, ks)).decode("utf-8", "ignore")


# ---------------------------------------------------------------- 运行期加密（DPAPI）
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _to_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def _from_blob(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _crypt32():
    return ctypes.windll.crypt32


def _kernel32():
    return ctypes.windll.kernel32


DPAPI_AVAILABLE = False
if _IS_WIN:
    try:
        _crypt32().CryptProtectData  # 探测符号
        DPAPI_AVAILABLE = True
    except Exception:
        DPAPI_AVAILABLE = False


def dpapi_encrypt(data: bytes) -> bytes:
    if not DPAPI_AVAILABLE:
        return b""
    bin_ = _to_blob(data)
    out = _BLOB()
    ok = _crypt32().CryptProtectData(
        ctypes.byref(bin_), "AIWorkbench", None, None, None, 0, ctypes.byref(out))
    if not ok:
        return b""
    return _from_blob(out)


def dpapi_decrypt(blob: bytes) -> bytes:
    if not DPAPI_AVAILABLE or not blob:
        return b""
    bin_ = _to_blob(blob)
    out = _BLOB()
    ok = _crypt32().CryptUnprotectData(
        ctypes.byref(bin_), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return b""
    return _from_blob(out)


_SEALED_PREFIX = "enc1:"


def seal(value):
    """把字符串加密成可安全落盘的文本。失败则返回 None（调用方决定怎么办）。"""
    if value is None:
        return None
    if value == "":
        return ""
    if DPAPI_AVAILABLE:
        enc = dpapi_encrypt(str(value).encode("utf-8"))
        if enc:
            return _SEALED_PREFIX + base64.b64encode(enc).decode()
    # 没有 DPAPI（非 Windows）时退回编译期混淆，至少不落明文可直接读
    return _SEALED_PREFIX + "x" + base64.b64encode(
        _xor_bytes(str(value).encode("utf-8"))).decode()


def unseal(token):
    """解析 seal() 产出的文本；遇到旧的明文值原样返回。"""
    if token is None:
        return None
    t = str(token)
    if not t.startswith(_SEALED_PREFIX):
        return t  # 老格式明文，交给调用方决定是否迁移
    body = t[len(_SEALED_PREFIX):]
    if body.startswith("x"):
        try:
            raw = base64.b64decode(body[1:])
        except Exception:
            return ""
        return _xor_bytes(raw).decode("utf-8", "ignore")
    try:
        raw = base64.b64decode(body)
    except Exception:
        return ""
    plain = dpapi_decrypt(raw)
    return plain.decode("utf-8", "ignore") if plain else ""


def _xor_bytes(data: bytes) -> bytes:
    ks = _keystream(len(data))
    return bytes(a ^ b for a, b in zip(data, ks))


def seal_dict(d: dict) -> dict:
    """把 dict 的每个值加密（用于 api_keys 这类敏感映射）。"""
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        sealed = seal(v)
        out[k] = sealed if sealed is not None else ""
    return out


def unseal_dict(d: dict) -> dict:
    return {k: (unseal(v) or "") for k, v in (d or {}).items()}


def is_sealed(token) -> bool:
    return isinstance(token, str) and token.startswith(_SEALED_PREFIX)


def looks_plaintext_key(value) -> bool:
    """粗略判断一个值是不是「看起来就是明文密钥」，用于提示用户迁移。"""
    if not isinstance(value, str) or not value:
        return False
    if is_sealed(value):
        return False
    return len(value) >= 16 and any(c.isdigit() for c in value) and any(
        c.isalpha() for c in value)


def mask(value, keep=6):
    """打码显示：sk-ws-H.PLL...c9f2"""
    if not value:
        return "（未配置）"
    s = str(value)
    if len(s) <= keep * 2:
        return s[:2] + "***"
    return s[:keep] + "…" + s[-4:]


# ===========================================================================
#                       沙箱策略（v9.18.0 统一入口）
# ===========================================================================
# 目标：让 AI 干活时只能在工作区里动，不能跑到工作区外面去乱改。
#
# 这一层**只管判定**（返回布尔 / 中文说明），**不做决策**：
#   · "要不要弹确认框" 由 agent 决定（那里才拿得到 confirm_cb）；
#   · 这样 security 保持纯函数，好测、好复用。
#
# ⚠ 所有判定都遵循一条铁律：**先展开、再判**。
#   `%TEMP%`、`~`、`\\?\C:\`、符号链接 / junction、`PROGRA~1` 短名、
#   大小写变形 —— 都必须解析成"真实最终位置"之后再比，绝不能只看原始字符串。
# ===========================================================================
import re as _re  # noqa: E402  （放这里避免影响上面的密钥逻辑）

# ---- 系统关键位置：这里的"写"一律当作受保护目标 ----
def _env(*names):
    out = []
    for n in names:
        v = os.environ.get(n)
        if v:
            out.append(v)
    return out


def system_protected_dirs():
    """返回一批"受保护目录"（写操作不得落进去）。

    覆盖：Windows 本体、程序目录、ProgramData、用户 AppData（本程序与别的
    软件都在这儿）、启动文件夹。用环境变量取，兼容盘符搬移 / 中文系统。
    """
    dirs = [
        r"C:\Windows", r"C:\Windows.old",
        r"C:\Program Files", r"C:\Program Files (x86)",
        r"C:\ProgramData",
    ]
    dirs += _env("SystemRoot", "windir", "ProgramData", "ProgramFiles",
                 "ProgramFiles(x86)", "ProgramFiles(Arm)")
    dirs += _env("APPDATA", "LOCALAPPDATA")
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if profile:
        dirs.append(os.path.join(profile, "AppData"))
        dirs.append(os.path.join(profile, "NTUSER.DAT"))
        # 启动文件夹（本机/当前用户）—— 开机自启的藏身处
        dirs.append(os.path.join(profile, "AppData", "Roaming", "Microsoft",
                                 "Windows", "Start Menu", "Programs", "Startup"))
        dirs.append(os.path.join(profile, "AppData", "Roaming", "Microsoft",
                                 "Windows", "Start Menu", "Programs", "StartupApproved"))
    pd = os.environ.get("ProgramData")
    if pd:
        dirs.append(os.path.join(pd, "Microsoft", "Windows", "Start Menu",
                                 "Programs", "Startup"))
    # 本程序自己的安装目录（覆盖 exe / 配置，会让程序起不来）
    la = os.environ.get("LOCALAPPDATA")
    if la:
        dirs.append(os.path.join(la, "Programs", "WorkBuddy"))
        dirs.append(os.path.join(la, "Programs", "AIWorkbench"))
    # 冷门但危险：WindowsApps（系统托管包，改坏很难修）
    dirs.append(r"C:\Program Files\WindowsApps")
    return [d for d in dirs if d]


# ---- 工作区"内部数据"：机器自己用，AI 直接改 = 改自己的配置 / 轨迹 / 记忆 ----
WORKSPACE_INTERNAL_DIRS = (".awb", "conversations", "traces", "worklog",
                           "bgtasks", "team", "exports", "backups")
WORKSPACE_INTERNAL_FILES = ("settings.json", "mcp.json", "MEMORY.md",
                            "assistant.json", "memory_auto.jsonl")


def _strip_ns_prefix(s):
    r"""去掉 Windows 命名空间前缀：`\\?\C:\x` -> `C:\x`；`\\?\UNC\s\v` -> `\\s\v`。"""
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    if s.startswith("\\\\?\\") or s.startswith("\\\\.\\"):
        return s[4:]
    return s


def normalize_path(raw, base=None):
    r"""把路径字符串做"完整展开"，返回绝对、规范化后的路径。

    依次处理：去引号 -> 去 `\\?\` 前缀 -> 展开环境变量（%TEMP%）->
    展开 `~` -> （给了 base 且仍是相对）拼到 base 下 -> normpath。
    绝不抛异常："解析不了"本身就是一种要拦的情况，交给上层用原串比。
    """
    s = str(raw if raw is not None else "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    if not s:
        return ""
    s = _strip_ns_prefix(s)
    try:
        s = os.path.expandvars(s)
    except Exception:
        pass
    if s[:1] == "~":
        try:
            s = os.path.expanduser(s)
        except Exception:
            pass
    s = _strip_ns_prefix(s)
    try:
        if base and not (os.path.isabs(s) or os.path.splitdrive(s)[0]):
            s = os.path.join(base, s)
        return os.path.normpath(s)
    except Exception:
        return s


def real_path(path):
    r"""解析符号链接 / junction / 8.3 短名，返回"真实最终位置"。

    Windows 上 `os.path.realpath`（Py3.8+）走 GetFinalPathNameByHandle，
    能把 junction、符号链接、`PROGRA~1` 短名都还原。路径不存在时尽力解析
    已存在的父级前缀（"要新建的文件"也能算准落到哪）。
    """
    try:
        p = os.path.abspath(str(path or ""))
    except Exception:
        p = str(path or "")
    try:
        p = os.path.realpath(p)
    except Exception:
        pass
    return _strip_ns_prefix(p)


def _cmp(path):
    """比较用的规范形式：真实路径 + 去尾分隔符 + 小写（Windows 大小写不敏感）。"""
    p = real_path(path)
    try:
        p = os.path.normcase(os.path.normpath(p))
    except Exception:
        p = p.lower()
    if len(p) > 3:
        p = p.rstrip("\\/")
    return p


def is_within(base, target):
    """target 是否就是 base 或在 base 之下。

    ⚠ 用 `base + sep` 前缀判断，绝不用裸 `startswith(base)` ——
       否则工作区 `...\\ws` 会把 `...\\ws_evil` 误判成"里面"。
    """
    if not base or not target:
        return False
    b = _cmp(base)
    t = _cmp(target)
    if not b or not t:
        return False
    if t == b:
        return True
    if not b.endswith("\\"):
        b = b + "\\"
    return t.startswith(b)


def same_path(a, b):
    return bool(a) and bool(b) and _cmp(a) == _cmp(b)


_PROT_CACHE = {}


def is_system_protected(path):
    """写这个位置算不算"动系统"（Windows / 程序目录 / AppData / 启动项…）。"""
    t = _cmp(path)
    if not t:
        return False
    key = id(system_protected_dirs)          # 目录清单稳定，缓存一次够用
    dirs = _PROT_CACHE.get(key)
    if dirs is None:
        dirs = [_cmp(d) for d in system_protected_dirs() if d]
        dirs = [d for d in dirs if d]
        _PROT_CACHE[key] = dirs
    for d in dirs:
        if t == d or t.startswith(d + "\\"):
            return True
    # hosts 文件（解析恶意域名 / 阻断更新常用它）
    sr = os.environ.get("SystemRoot") or r"C:\Windows"
    hosts = _cmp(os.path.join(sr, "System32", "drivers", "etc", "hosts"))
    if hosts and t == hosts:
        return True
    return False


def is_workspace_internal(path, workspace_root):
    """是不是工作区里的"机器内部数据"（.awb/ 配置轨迹记忆、settings.json…）。

    只对**工作区根直属**的这些名字判：
      <ws>/.awb/**、<ws>/settings.json、<ws>/mcp.json、<ws>/MEMORY.md、
      以及老布局的 <ws>/traces、conversations… 等。
    产物目录（chats/<NN>/files/…）不受影响。
    """
    if not workspace_root:
        return False
    ws = _cmp(workspace_root)
    t = _cmp(path)
    if not ws or not t:
        return False
    if not ws.endswith("\\"):
        ws = ws + "\\"
    if not t.startswith(ws):
        return False
    rel = t[len(ws):]
    head = rel.split("\\", 1)[0]
    for name in WORKSPACE_INTERNAL_DIRS:
        if head == os.path.normcase(name):
            return True
    if rel in tuple(os.path.normcase(x) for x in WORKSPACE_INTERNAL_FILES):
        return True
    return False


def protected_reason(path, sandbox=""):
    """受保护目标的拒绝说明（中文，说清"为什么 + 怎么办"）。"""
    return (f"出于安全，禁止 AI 直接写入受保护的位置：{path}\n"
            "· 这里是系统关键目录 / 本程序自己的安装目录 / 工作区的内部数据"
            "（配置、密钥、轨迹、记忆）。\n"
            f"· 允许 AI 自由写入的范围是你的**产物目录**：{sandbox}\n"
            "· 如果确实要改这里，请在弹窗里点【允许】；或由你手动操作。")


def outside_reason(path, sandbox=""):
    """超出产物目录的写入拒绝说明（中文）。"""
    return (f"出于安全，写操作默认只能落在产物目录里；这次要写的是目录外的：{path}\n"
            f"· 允许的范围：{sandbox}（以及它下面的子目录）\n"
            "· 如果确实要写到这里，请在弹窗里点【允许】；\n"
            "· 否则请改成**产物目录内的相对路径**，例如 out/报告.docx。")


def outside_read_note(path, workspace_root):
    """读工作区外文件时的如实标注（读放宽，但要说清）。"""
    return f"（工作区外只读：{path}）"


# ---------------------------------------------------------------------------
#                       命令审计：结构 + 关键字
# ---------------------------------------------------------------------------
# 老实现是"整串正则黑名单"，`echo hi & del /f x` 能靠正则蹭到，但
# `cmd /c` 套娃、`powershell -enc <base64>`、`a | b` 拆段就绕过去了。
# 现在：先按 `& | ; 换行` 拆段（尊重引号）→ 逐段递归解包
# （cmd /c、powershell -Command/-EncodedCommand）→ 再逐段跑关键字黑名单。
# ---------------------------------------------------------------------------
_DANGER_RULES = [
    (r"\brm\s+-rf?\b", "递归强制删除（rm -rf）"),
    (r"\brmdir\s+/s\b", "递归删除目录（rmdir /s）"),
    (r"\brd\s+/s\b", "递归删除目录（rd /s）"),
    (r"\bdel\s+/[fsq]", "强制/静默删除（del /f、/s、/q）"),
    (r"\berase\s+/[fsq]", "强制/静默删除（erase）"),
    (r"\bformat\s+[a-z]:", "格式化磁盘（format X:）"),
    (r"\bmkfs\b", "格式化文件系统（mkfs）"),
    (r"\bdiskpart\b", "磁盘分区操作（diskpart）"),
    (r"\bshutdown\b", "关机/重启（shutdown）"),
    (r"\bhalt\b", "关机（halt）"),
    (r"\breboot\b", "重启（reboot）"),
    (r":\(\)\s*\{", "fork 炸弹（:(){ :|:& };:）"),
    (r"\bdd\s+if=", "裸写磁盘（dd if=）"),
    (r">\s*/dev/sd", "裸写磁盘设备（> /dev/sdX）"),
    (r"\bcipher\s+/w", "擦除磁盘空闲空间（cipher /w）"),
    (r"\bchkdsk\b\s*/f", "修复/占用磁盘（chkdsk /f）"),
    (r"\bvssadmin\b", "卷影副本操作（vssadmin，勒索软件常用来删快照）"),
    (r"\bwbadmin\b", "备份删除/恢复（wbadmin）"),
    (r"\bbcdedit\b", "修改启动配置（bcdedit）"),
    (r"\btakeown\b", "夺取文件所有权（takeown）"),
    (r"\bicacls\b[^\n]*/(grant|deny|setowner)\b", "修改文件权限（icacls /grant）"),
    (r"\bnet\s+user\b[^\n]*\s(/add|/delete)\b", "增删系统账户（net user /add）"),
    (r"\bnet\s+user\s+\S+\s+\S+", "修改账户口令（net user X Y）"),
    (r"\bnet\s+localgroup\b[^\n]*\s(/add|/delete)\b", "修改本地组（net localgroup /add）"),
    (r"\bschtasks\b[^\n]*/create", "创建计划任务（schtasks /create）"),
    (r"\bat\s+\d{1,2}:\d{2}\b", "创建计划任务（at 时间）"),
    (r"\breg\s+(add|delete|import|copy|save|restore|load|unload)\b",
     "写入/导入注册表（reg add/delete/import）"),
    (r"\bcertutil\b[^\n]*-urlcache\b", "通过 certutil 下载文件（-urlcache，LOLBin）"),
    (r"\bcertutil\b[^\n]*-decode", "解码文件（certutil -decode）"),
    (r"\bbitsadmin\b", "后台传输文件（bitsadmin）"),
    (r"\bmshta\b", "执行脚本宿主（mshta）"),
    (r"\brundll32\b", "调用任意 DLL 导出（rundll32）"),
    (r"\bregsvr32\b[^\n]*(http|\\\\|/i:)", "远程注册脚本组件（regsvr32）"),
    (r"\bwmic\b[^\n]*shadowcopy[^\n]*delete", "删除卷影（wmic shadowcopy delete）"),
    (r"\bwmic\b\s+process\b[^\n]*\bcall\s+create\b", "经 wmic 启动进程"),
    (r"\bpowercfg\b[^\n]*/hibernate", "改电源策略（powercfg）"),
    (r"\bsc\s+(delete|create|config|stop)\b", "增删/改系统服务（sc）"),
    (r"\b(robocopy|robocopy\.exe)\b[^\n]*(/mir|/purge)\b",
     "镜像同步会删除目标多余文件（robocopy /mir）"),
    (r"\b(?:powershell|pwsh)\b[^\n]*-(enc|encodedcommand)\b",
     "PowerShell 编码命令（-EncodedCommand，常用来绕过检测）"),
    (r"\bfrombase64string\b", "解码后执行（FromBase64String）"),
    (r"\bremove-item\b[^\n]*-recurse\b[^\n]*-force\b", "递归强制删除（Remove-Item -Recurse -Force）"),
    (r"\brm\s+-rf\s+/\s*$", "删除根目录（rm -rf /）"),
]
_DANGER_COMPILED = [(_re.compile(p, _re.I), why) for p, why in _DANGER_RULES]

_ENC_RE = _re.compile(r"-(?:enc|encodedcommand)\s+([A-Za-z0-9+/=]{8,})", _re.I)
_REDIR_RE = _re.compile(r">>?\s*\"?([A-Za-z]:\\[^\s\"&|<>]+|\\\\[^\s\"&|<>]+)")


def split_command_segments(cmd):
    r"""按 `& && | || ; 换行` 拆成若干段（**不含引号内的分隔符**）。

    为什么要拆：`echo hi & del /f x`、`a | b`、`x; y` 这类"串联"，只查第一段
    必然漏。拆完之后每一段都要独立过检查。
    """
    s = str(cmd or "")
    out, buf, quote = [], [], None
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c in "&|;\r\n" or c in ("\r", "\n"):
            out.append("".join(buf))
            buf = []
            while i < n and s[i] in "&|;\r\n\n":
                i += 1
            continue
        buf.append(c)
        i += 1
    out.append("".join(buf))
    return [x.strip() for x in out if x.strip()]


def _unwrap_segments(seg, depth=0):
    """把一段命令里的"壳"剥开：cmd /c X -> X；powershell -Command X -> X。

    `-EncodedCommand <base64>` 会先 base64 解出来（PowerShell 是 UTF-16LE）
    再继续拆 —— 不解包就等于放行了一条"看不见的命令"。
    """
    if depth > 6:
        return [seg]
    s = seg.strip()
    low = s.lower()
    m = _re.match(r'^\s*"?[^"\s]*cmd(?:\.exe)?"?\s+/[ck]\s+(.+)$', s, _re.I)
    if m:
        return _unwrap_segments(m.group(1), depth + 1)
    m = _re.match(r'^\s*(?:".*?\\)?(?:powershell|pwsh)(?:\.exe)?"?\s+(.+)$',
                  s, _re.I)
    if m:
        payload = m.group(1)
        me = _ENC_RE.search(payload)
        if me:
            try:
                import base64 as _b64
                raw = _b64.b64decode(me.group(1) + "=" * (-len(me.group(1)) % 4))
                for enc in ("utf-16-le", "utf-8"):
                    try:
                        dec = raw.decode(enc)
                        if dec.strip():
                            return [s] + _unwrap_segments(dec, depth + 1)
                    except Exception:
                        continue
            except Exception:
                pass
            return [s]                       # 解不开的编码命令，原样交给黑名单
        mc = _re.match(r'^\s*-(?:command|c)\s+(.+)$', payload, _re.I)
        if mc:
            return [s] + _unwrap_segments(mc.group(1).strip('"'), depth + 1)
    return [s]


def audit_command(cmd):
    """审计一条命令，返回 (是否危险, 原因中文)。

    危险 = 命中破坏性关键字，**或**各段串联后有一段命中，
    **或**带编码命令（-EncodedCommand / FromBase64String），
    **或**重定向写到系统关键位置（`> C:\\Windows\\x`）。
    """
    if not str(cmd or "").strip():
        return False, ""
    segments = []
    for seg in split_command_segments(cmd):
        segments.extend(_unwrap_segments(seg))

    for seg in segments:
        for rx, why in _DANGER_COMPILED:
            if rx.search(seg):
                return True, why
        m = _REDIR_RE.search(seg)
        if m:
            tgt = normalize_path(m.group(1))
            if is_system_protected(tgt) or is_workspace_internal(tgt, None):
                return True, f"重定向写到受保护位置（{tgt}）"
    # 整串再过一遍关键字（保险：某些写法拆段后语义变了）
    for rx, why in _DANGER_COMPILED:
        if rx.search(str(cmd)):
            return True, why
    return False, ""


def is_dangerous_command(cmd):
    return audit_command(cmd)[0]


# 会被"危险命令"判定拦下的额外关键字（供 agent 复用，避免两份清单漂移）
DANGER_KEYWORDS = tuple(p for p, _ in _DANGER_RULES)
