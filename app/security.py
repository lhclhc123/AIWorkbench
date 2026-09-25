# -*- coding: utf-8 -*-
"""密钥保护：让 API Key 不以任何明文形式出现在 exe 或本地文件里。

两层：
1. 编译期混淆（obf / deobf）——写进源码的是密文，`strings` 扫不出明文 Key。
   ⚠️ 诚实说明：客户端程序里的密钥，理论上一定能被逆向出来（这是所有
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
