# -*- coding: utf-8 -*-
"""下载加速：多通道测速（延迟 + 吞吐）→ 选最快 → 断点续传（带校验）→ 失败切换。

为什么需要这个模块（本机实测）：
- GitHub Release / raw 直连被严重限速或被系统代理拦（约 24KB/s 甚至 404），
  而 gh-proxy.com / ghfast.top 等镜像实测可用且快（秒级）。
- 没有哪个镜像在所有网络环境下都稳定：本机不同时刻 ``ghproxy.net`` 有时可用、
  有时超时。所以不能「只信一个镜像」，要把「直连 + 多个镜像」全部当候选，
  **同时测延迟和吞吐，动态选最快**，下载中断还能从 .part 断点续传。

设计要点：
1. ``mirror_candidates()`` 只对 GitHub 域名追加镜像，其他网址不动；
2. 镜像列表内置一套 + 支持环境变量 ``AIWORKBENCH_MIRRORS`` 追加（逗号分隔），
   用户换电脑后不需要改代码，改环境变量即可扩展/替换镜像通道；
3. ``pick_fastest()`` 用 ``Range: bytes=0-<PROBE_BYTES-1>`` 测一段真实字节，
   综合「吞吐优先、延迟兜底」选最快通道；文本/html 假镜像页会被过滤；
4. ``download()`` 依次尝试候选通道，失败自动切下一个；
5. 写 ``<dest>.part`` 再原子改名；``<dest>.part.json`` 记录来源 URL / 总大小 /
   ETag，用于校验续传点，防止把损坏/异源的 .part 当有效续传点；
6. 全程有大小上限与进度回调，不会无声卡死。
"""
import json
import os
import re
import time

UA = "Mozilla/5.0 (AIWorkbench)"
CHUNK = 65536

# 测速时实际读取的字节数（大文件看吞吐，这个量级足够采样出真实速度）。
# 128KB 在「采样准确度」和「慢网不被误杀」之间折中：4s 读超时下，最快也要
# ~32KB/s 才能读完；更慢的慢网会走「部分数据估速」分支，不会被判为不可用。
PROBE_BYTES = 128 * 1024

# 只有这些 GitHub 域名才做镜像改写（别的站点改写会 404）。
_GH_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
)

# 内置镜像前缀（按历史可用性排序）。实测结论（本机 2026-10-25）：
#   - gh-proxy.com  → HTTP 206 可用、较快
#   - ghfast.top    → HTTP 206 可用、稍慢
#   - ghproxy.net   → 历史上最稳，但本次探测偶发超时（保留作候选，靠测速兜底）
#   - ghproxy.cc    → 本机 SSL 报错，但其他电脑可能可用，保留
# 用户可用环境变量 AIWORKBENCH_MIRRORS 追加/替换，例如：
#   AIWORKBENCH_MIRRORS=https://ghfast.top/,https://mirror.example.com/
DEFAULT_MIRRORS = (
    "https://ghproxy.net/",
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.cc/",
)


def _host_of(url):
    try:
        from urllib.parse import urlsplit
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def mirror_prefixes():
    """返回全部镜像前缀（内置 + 环境变量 AIWORKBENCH_MIRRORS 追加，去重）。"""
    out = []
    for p in DEFAULT_MIRRORS:
        p = str(p or "").strip()
        if p and p not in out:
            out.append(p)
    env = os.environ.get("AIWORKBENCH_MIRRORS", "")
    for part in env.replace(";", ",").split(","):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out


def mirror_candidates(url):
    """返回下载候选通道；GitHub 域名自动追加每个镜像前缀各一次。"""
    url = str(url or "").strip()
    cands = [url] if url else []
    if _host_of(url) in _GH_HOSTS:
        for prefix in mirror_prefixes():
            prefix = prefix if prefix.endswith("/") else prefix + "/"
            mirrored = prefix + url
            if mirrored not in cands:
                cands.append(mirrored)
    return cands


def _is_mirror(url):
    return any(str(url).startswith(p) for p in mirror_prefixes())


def describe_channel(url):
    """给上层展示用的通道名：镜像返回其域名，直连返回「直连」。"""
    if _is_mirror(url):
        return _host_of(url) or "镜像加速"
    return "直连"


def _looks_like_html(url, headers):
    """过滤掉镜像返回的「请验证/网页版」假响应：真二进制/纯文本不会是 text/html。"""
    if str(url or "").lower().endswith((".html", ".htm")):
        return False
    ct = str(headers.get("Content-Type") or "").lower()
    return ct.startswith("text/html")


def _probe(url, timeout=4.0, probe_bytes=PROBE_BYTES):
    """测「首字节延迟（秒）+ 吞吐（字节/秒）」。失败返回 None。

    返回 dict：``{"latency": 秒, "speed": B/s, "bytes": 已读字节, "status": HTTP码}``。

    ★ 抗慢网：读到一半超时/断流时，只要已经读到部分字节，就按已读数据估速
    返回（而不是判为不可用）。否则慢速但可用的镜像会被误杀，导致全部候选失败、
    回落到更慢的直连。
    """
    try:
        import requests
    except ImportError:
        return None
    t0 = time.time()
    first_byte = None
    got = 0
    status = 0
    try:
        with requests.get(
                url,
                headers={"User-Agent": UA, "Range": "bytes=0-%d" % (probe_bytes - 1)},
                stream=True,
                timeout=(timeout, timeout),
                allow_redirects=True) as r:
            status = r.status_code
            if status not in (200, 206):
                return None
            if _looks_like_html(url, r.headers):
                return None
            try:
                for chunk in r.iter_content(1024):
                    if not chunk:
                        continue
                    if first_byte is None:
                        first_byte = time.time() - t0
                    got += len(chunk)
                    if got >= probe_bytes:
                        break
            except Exception:
                # 读超时/断流：有部分数据就继续用，没有才判失败
                if got <= 0:
                    return None
    except Exception:
        return None
    if got <= 0:
        return None
    elapsed = max(0.001, time.time() - t0)
    latency = first_byte if first_byte is not None else elapsed
    return {
        "latency": round(max(0.001, latency), 4),
        "speed": round(got / elapsed, 1),
        "bytes": got,
        "status": status,
    }


def _score(s):
    """通道综合评分：吞吐优先，延迟兜底（分数越高越优先）。"""
    if not s:
        return None
    return (s.get("speed") or 0.0, -(s.get("latency") or 0.0))


def pick_fastest(url, timeout=4.0, probe_bytes=PROBE_BYTES):
    """并发测速选出最快通道。

    返回 ``(best_url, stats)``；若全部测速失败，回落到原始 URL。
    ``stats`` 是 ``{url: probe_dict or None}``，便于上层展示「为什么选它」。

    ★ 并发探测：以前的串行实现遇到几个不通的镜像时要等 timeout×N 才有结果，
    更新前光测速就卡十几秒。这里多通道同时探测，总耗时 ≈ 最慢的那个通道。
    """
    cands = mirror_candidates(url)
    stats = {}
    if not cands:
        return url, stats

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(cands))) as ex:
            futures = {c: ex.submit(_probe, c, timeout, probe_bytes) for c in cands}
            for c, fut in futures.items():
                try:
                    stats[c] = fut.result(timeout=timeout * 2 + 3)
                except Exception:
                    stats[c] = None
    except Exception:
        # 线程池不可用时退回串行，保证功能不缺失
        for c in cands:
            stats[c] = _probe(c, timeout=timeout, probe_bytes=probe_bytes)

    best = None
    best_score = None
    for c in cands:
        score = _score(stats.get(c))
        if score is not None and (best_score is None or score > best_score):
            best_score = score
            best = c
    return (best or url), stats


# ---------------- .part 元数据（用于校验断点续传点） ----------------

def _meta_path(dest):
    return dest + ".part.json"


def _read_meta(meta_path):
    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _write_meta(meta_path, url, total, headers):
    meta = {
        "url": url,
        "total": int(total or 0),
        "etag": headers.get("ETag") or headers.get("Etag") or "",
        "last_modified": headers.get("Last-Modified") or "",
        "mtime": time.time(),
    }
    try:
        tmp = meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, meta_path)
    except Exception:
        pass


def _clear_meta(meta_path):
    for p in (meta_path, meta_path + ".tmp"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _remove_quiet(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _download_one(url, dest, progress=None, timeout=(10, 60),
                  max_bytes=512 * 1024 * 1024, _allow_restart=True):
    """从单个通道下载到 ``dest``，返回 ``(got, total)``。失败抛异常。"""
    import requests

    part = dest + ".part"
    meta_path = _meta_path(dest)
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)

    # ---- 确定续传点 ----
    # 允许跨通道续传：所有镜像代理的是同一个 GitHub 文件。只要 .part 非空、
    # 有元数据、且未超过上次记录的总大小，就先乐观续传；真正发请求后再用
    # 服务端返回的总大小 / ETag 校验，不一致就丢弃重下（见下方 mismatch）。
    resume_from = 0
    meta = _read_meta(meta_path)
    if os.path.exists(part):
        size = os.path.getsize(part)
        if size > 0 and meta:
            total_known = int(meta.get("total") or 0)
            if total_known > 0 and size >= total_known:
                # 已完整或损坏（无服务端哈希可校验）→ 重头再下
                _remove_quiet(part)
                _clear_meta(meta_path)
            else:
                resume_from = size
        else:
            # 空文件 / 无元数据 → 无法校验来源，丢弃
            _remove_quiet(part)
            _clear_meta(meta_path)

    headers = {"User-Agent": UA, "Accept": "*/*"}
    if resume_from:
        headers["Range"] = "bytes=%d-" % resume_from

    with requests.get(url, headers=headers, stream=True, timeout=timeout,
                      allow_redirects=True) as r:
        # 416：.part 已完整或已损坏 —— 删掉重来一次。
        if r.status_code == 416 and _allow_restart:
            r.close()
            _remove_quiet(part)
            _clear_meta(meta_path)
            return _download_one(url, dest, progress, timeout, max_bytes,
                                 _allow_restart=False)
        if r.status_code not in (200, 206):
            raise RuntimeError("HTTP %s" % r.status_code)

        etag = r.headers.get("ETag") or r.headers.get("Etag") or ""

        # 服务器忽略了 Range（返回 200）→ 必须从头写，不能追加。
        if resume_from and r.status_code == 200:
            resume_from = 0

        total = int(r.headers.get("Content-Length") or 0)
        if r.status_code == 206:
            cr = r.headers.get("Content-Range") or ""
            m = re.search(r"/\s*(\d+)\s*$", cr)
            if m:
                total = int(m.group(1))
        elif resume_from:
            # 200 + 已有 part：总长要把已下部分算进去
            total = resume_from + total

        # ---- 校验续传点是否真的属于同一个文件 ----
        mismatch = False
        if resume_from:
            old_total = int((meta or {}).get("total") or 0)
            old_etag = str((meta or {}).get("etag") or "")
            if old_total and total and old_total != total:
                mismatch = True
            if old_etag and etag and old_etag != etag:
                mismatch = True
            if total > 0 and resume_from >= total:
                mismatch = True
        if mismatch and _allow_restart:
            r.close()
            _remove_quiet(part)
            _clear_meta(meta_path)
            return _download_one(url, dest, progress, timeout, max_bytes,
                                 _allow_restart=False)

        _write_meta(meta_path, url, total, r.headers)

        mode = "ab" if resume_from else "wb"
        got = resume_from
        with open(part, mode) as f:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if max_bytes and got > max_bytes:
                    raise RuntimeError(
                        "文件超过 %d MB，已中止" % (max_bytes // 1048576))
                if progress:
                    progress(got, total)

    os.replace(part, dest)
    _clear_meta(meta_path)
    return got, total


def download(url, dest, progress=None, timeout=(10, 60),
             max_bytes=512 * 1024 * 1024, prefer=None):
    """下载到 ``dest``，多通道自动切换。

    返回 ``(dest, got, total, used_url)``。全部通道失败抛 ``RuntimeError``。
    ``prefer`` 若给（通常是 :func:`pick_fastest` 的结果），优先尝试它。
    """
    cands = mirror_candidates(url)
    if prefer and prefer in cands:
        cands = [prefer] + [c for c in cands if c != prefer]
    elif prefer:
        cands = [prefer] + cands

    errors = []
    for cand in cands:
        try:
            got, total = _download_one(cand, dest, progress, timeout, max_bytes)
            return dest, got, total, cand
        except Exception as e:
            # 保留 .part 与元数据：下一个通道（或下次运行）可以继续断点续传。
            # 元数据里的 total/etag 会在下一个通道响应时校验，异源会被丢弃重下，
            # 所以保留半截文件是安全的，还能避免"换通道就从头下"的浪费。
            errors.append("%s -> %s: %s" % (cand, type(e).__name__, e))

    raise RuntimeError("所有下载通道都失败：" + "；".join(errors))
