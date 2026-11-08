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
6. 全程有大小上限与进度回调，不会无声卡死；
7. ``download_parallel()`` 分段并发（默认 8 段）—— 实测单连接拉 236MB 的更新包
   只有 1~3MB/s，瓶颈是单条 TCP 而不是镜像带宽；8 段能到 8~15MB/s。服务器
   不支持 Range / 文件 < 8MB 时**原样退回**单连接，不引入新的失败路径；
8. 单连接「慢速换线」：连续 5 秒平均 < 200KB/s 就换下一个镜像；但**所有**镜像
   都慢时仍然会不限速下完（慢总比更新失败强）。
"""
import json
import math
import os
import re
import threading
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

# ---------------- 分段并发下载参数（更新包提速主力） ----------------

# 只有 >= 8MB 的文件才值得分段：小文件的分段握手开销比省下来的时间还多。
PARALLEL_MIN_TOTAL = 8 * 1024 * 1024
# 单段最小 2MB：段数 = min(connections, ceil(total / min_part))。
PARALLEL_MIN_PART = 2 * 1024 * 1024
# 默认并发段数。实测 8 段即可吃满家用带宽，再多只是增加镜像侧压力。
PARALLEL_CONNECTIONS = 8
# 进度回调最小间隔：多线程每秒可能完成上千个 chunk，必须自己限流。
PROGRESS_INTERVAL = 0.2
# 单段失败重试次数与退避（秒）：0.5 / 1 / 2。
SEG_RETRIES = 3
SEG_BACKOFF = (0.5, 1.0, 2.0)
# 单连接「慢速换线」阈值：连续 5 秒平均 < 200KB/s 就换下一个镜像
# （236MB 按 200KB/s 要 20 分钟，等下去没有意义）。
SLOW_WINDOW = 5.0
SLOW_BYTES_PER_SEC = 200 * 1024


class _TooSlow(Exception):
    """当前通道慢到不值得等（内部换线信号，绝不作为用户文案）。"""


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
                  max_bytes=512 * 1024 * 1024, _allow_restart=True,
                  slow_limit=None, cancel=None):
    """从单个通道下载到 ``dest``，返回 ``(got, total)``。失败抛异常。

    Args:
        slow_limit: ``(秒, 字节/秒)``。给了就在读循环里做「慢速换线」：
            连续 ``秒`` 秒平均速度低于 ``字节/秒`` 时抛 :class:`_TooSlow`，
            让上层换下一个镜像。``None`` 表示不限速（最后一轮兜底用）。
        cancel: 可选 ``threading.Event``，置位后尽快退出并抛「已取消」。

    Raises:
        _TooSlow: 当前通道太慢（.part 保留，下一个通道可续传）。
        RuntimeError: HTTP 错误 / 超限 / 已取消。
    """
    import requests

    if cancel is not None and cancel.is_set():
        raise RuntimeError("已取消")

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
                                 _allow_restart=False,
                                 slow_limit=slow_limit, cancel=cancel)
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
                                 _allow_restart=False,
                                 slow_limit=slow_limit, cancel=cancel)

        _write_meta(meta_path, url, total, r.headers)

        mode = "ab" if resume_from else "wb"
        got = resume_from
        # 慢速换线的统计窗口：从「收到第一个字节」开始算，避免把建连/TLS
        # 握手的时间算进速度里（否则慢网络会被误杀）。
        win_start = None
        win_base = got
        with open(part, mode) as f:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("已取消")
                f.write(chunk)
                got += len(chunk)
                if max_bytes and got > max_bytes:
                    raise RuntimeError(
                        "文件超过 %d MB，已中止" % (max_bytes // 1048576))
                if slow_limit:
                    now = time.time()
                    if win_start is None:
                        win_start = now
                    span = now - win_start
                    if span >= slow_limit[0]:
                        speed = (got - win_base) / span
                        if speed < slow_limit[1]:
                            raise _TooSlow(
                                "连续 %.0f 秒平均 %.0f KB/s（阈值 %.0f KB/s）"
                                % (span, speed / 1024.0, slow_limit[1] / 1024.0))
                        win_start = now
                        win_base = got
                if progress:
                    progress(got, total)

    os.replace(part, dest)
    _clear_meta(meta_path)
    return got, total


def _range_probe(url, timeout=(10, 60)):
    """用 ``Range: bytes=0-0`` 探测是否支持分段。

    Args:
        url: 下载地址。
        timeout: ``(连接超时, 读超时)`` 秒。

    Returns:
        dict: ``{"ok": bool, "status": int, "total": int,
        "ranges": bool, "etag": str}``；``total <= 0`` 表示总长未知。
    """
    try:
        import requests
    except ImportError:
        return {"ok": False, "status": 0, "total": 0, "ranges": False, "etag": ""}
    headers = {"User-Agent": UA, "Accept": "*/*", "Range": "bytes=0-0"}
    try:
        with requests.get(url, headers=headers, stream=True, timeout=timeout,
                          allow_redirects=True) as r:
            status = r.status_code
            accept = str(r.headers.get("Accept-Ranges") or "").lower()
            total = 0
            if status == 206:
                m = re.search(r"/\s*(\d+)\s*$", r.headers.get("Content-Range") or "")
                if m:
                    total = int(m.group(1))
            if total <= 0:
                # 200 时 Content-Length 就是总长；206 时只是本段长度，不能用。
                total = (int(r.headers.get("Content-Length") or 0)
                         if status == 200 else 0)
            etag = r.headers.get("ETag") or r.headers.get("Etag") or ""
            try:
                r.close()
            except Exception:
                pass
    except Exception:
        return {"ok": False, "status": 0, "total": 0, "ranges": False, "etag": ""}
    # 真回了 206 才算支持分段；200 + Accept-Ranges: bytes 也认（少数 CDN）。
    ranges = bool(status == 206 or (status == 200 and "bytes" in accept))
    return {"ok": status in (200, 206), "status": status,
            "total": total, "ranges": ranges, "etag": etag}


def download_parallel(url, dest, progress=None, timeout=(10, 60),
                      connections=PARALLEL_CONNECTIONS,
                      min_part=PARALLEL_MIN_PART,
                      max_bytes=512 * 1024 * 1024, cancel=None):
    """分段并发下载大文件，返回 ``(dest, got, total, used_url)``。

    为什么需要：单连接拉 236MB 的更新包实测长期停在 1~3MB/s（瓶颈是单条 TCP
    的窗口/丢包恢复，不是镜像带宽），8 段并发实测能到 8~15MB/s。

    所有段都写进**同一个** ``<dest>.part``（先 truncate 到总长，各段 ``seek``
    写自己的区间），比「每段一个临时文件再合并」少一次 236MB 的全盘 IO。

    不支持 Range、总长未知或文件 < ``PARALLEL_MIN_TOTAL`` 时，原样退回
    :func:`_download_one`（单连接），不会给本来能下的场景引入新失败。

    分段模式**不做跨通道/跨进程续传**：中途失败会整包重下。原因是一份
    「长度对但可能带空洞」的 .part 无法校验，宁可重下 236MB（8 段约 30 秒），
    也不能把坏包交给安装器。

    Args:
        url: 下载地址（可带镜像前缀）。
        dest: 目标文件路径。
        progress: ``progress(done, total)``，内部已按 200ms 限流。
        timeout: ``(连接超时, 读超时)`` 秒。
        connections: 最大并发段数。
        min_part: 单段最小字节数（决定段数上限）。
        max_bytes: 大小上限，超过直接抛错（与单连接行为一致）。
        cancel: 可选 ``threading.Event``，置位后各段尽快退出并抛「已取消」。

    Returns:
        tuple: ``(dest, got, total, used_url)``，与 :func:`download` 一致。

    Raises:
        RuntimeError: 任一段重试 3 次仍失败、被取消、或最终长度校验不通过。
    """
    # 探测要快：多花 30 秒探测还不如直接退回单连接（单连接还有慢速换线兜底）。
    try:
        probe_timeout = (min(int(timeout[0]), 8), min(int(timeout[1]), 15))
    except (TypeError, ValueError, IndexError):
        probe_timeout = (8, 15)
    info = _range_probe(url, probe_timeout)
    total = int(info.get("total") or 0)

    if not info.get("ok") or not info.get("ranges") or total < PARALLEL_MIN_TOTAL:
        got, tot = _download_one(url, dest, progress, timeout, max_bytes,
                                 cancel=cancel)
        return dest, got, tot, url

    if max_bytes and total > max_bytes:
        raise RuntimeError("文件超过 %d MB，已中止" % (max_bytes // 1048576))

    import requests

    part = dest + ".part"
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)
    # 分段模式不做跨通道续传：先清干净再按 total 预分配，避免多线程写同一
    # 文件时反复扩张，也避免把旧的半截文件当成本次数据。
    _remove_quiet(part)
    _clear_meta(_meta_path(dest))
    with open(part, "wb") as f:
        f.truncate(total)
    _write_meta(_meta_path(dest), url, total, {"ETag": info.get("etag") or ""})

    seg_count = max(1, min(int(connections or 1),
                           int(math.ceil(total / float(max(1, min_part))))))
    seg_size = int(math.ceil(total / float(seg_count)))

    lock = threading.Lock()
    state = {"done": 0, "last": 0.0}
    errors = []

    def _emit(force=False):
        """聚合进度回调（200ms 限流）；回调异常不能影响下载。"""
        now = time.time()
        with lock:
            if not force and (now - state["last"]) < PROGRESS_INTERVAL:
                return
            state["last"] = now
            done = state["done"]
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    def _fetch_segment(idx, start, end):
        """下 ``[start, end]``（闭区间）；失败重试并从段内断点续传。"""
        session = requests.Session()
        pos = start
        last_err = None
        for attempt in range(SEG_RETRIES):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")
            try:
                headers = {"User-Agent": UA, "Accept": "*/*",
                           "Range": "bytes=%d-%d" % (pos, end)}
                with session.get(url, headers=headers, stream=True,
                                 timeout=timeout, allow_redirects=True) as r:
                    if r.status_code != 206:
                        raise RuntimeError("HTTP %s" % r.status_code)
                    with open(part, "r+b") as f:
                        f.seek(pos)
                        for chunk in r.iter_content(CHUNK):
                            if not chunk:
                                continue
                            if cancel is not None and cancel.is_set():
                                raise RuntimeError("已取消")
                            if pos + len(chunk) > end + 1:
                                # 服务器多给了尾巴：截掉，绝不越过本段边界。
                                chunk = chunk[:end + 1 - pos]
                            f.write(chunk)
                            pos += len(chunk)
                            with lock:
                                state["done"] += len(chunk)
                            _emit()
                            if pos > end:
                                break
                if pos <= end:
                    raise RuntimeError("分段未下完 %d/%d" % (pos, end + 1))
                return
            except Exception as e:
                last_err = e
                if cancel is not None and cancel.is_set():
                    raise
                if attempt < SEG_RETRIES - 1:
                    time.sleep(SEG_BACKOFF[attempt])
        raise RuntimeError("第 %d 段失败：%s: %s"
                           % (idx, type(last_err).__name__, last_err))

    def _run(idx, start, end):
        """线程入口：异常汇总到主线程再抛，避免静默丢失。"""
        try:
            _fetch_segment(idx, start, end)
        except Exception as e:
            with lock:
                errors.append(e)

    threads = []
    for i in range(seg_count):
        start = i * seg_size
        end = min(total - 1, start + seg_size - 1)
        if start > end:
            break
        t = threading.Thread(target=_run, args=(i, start, end),
                             name="aiwb-dl-%d" % i, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    if cancel is not None and cancel.is_set():
        _remove_quiet(part)
        _clear_meta(_meta_path(dest))
        raise RuntimeError("已取消")
    if errors:
        # 绝不留「长度对但中间是空洞」的假文件：宁可让上层换通道从头下。
        _remove_quiet(part)
        _clear_meta(_meta_path(dest))
        raise RuntimeError("分段下载失败：%s: %s"
                           % (type(errors[0]).__name__, errors[0]))

    size = os.path.getsize(part)
    if size != total:
        _remove_quiet(part)
        _clear_meta(_meta_path(dest))
        raise RuntimeError("下载不完整：%d/%d" % (size, total))

    os.replace(part, dest)
    _clear_meta(_meta_path(dest))
    _emit(force=True)
    return dest, total, total, url


def download(url, dest, progress=None, timeout=(10, 60),
             max_bytes=512 * 1024 * 1024, prefer=None,
             cancel=None, slow_limit=(SLOW_WINDOW, SLOW_BYTES_PER_SEC)):
    """单连接下载到 ``dest``，多通道自动切换 + 慢速换线。

    返回 ``(dest, got, total, used_url)``。全部通道失败抛 ``RuntimeError``。
    ``prefer`` 若给（通常是 :func:`pick_fastest` 的结果），优先尝试它。

    ``slow_limit=(秒, 字节/秒)``：单连接连续这么长时间的平均速度低于阈值，
    就主动中断当前通道换下一个镜像（以前只有连接异常才换，慢会一直耗着）。
    若**所有**通道都慢，则挑第一个通道不限速下完 —— 慢总比更新失败强。
    """
    cands = mirror_candidates(url)
    if prefer and prefer in cands:
        cands = [prefer] + [c for c in cands if c != prefer]
    elif prefer:
        cands = [prefer] + cands

    errors = []
    too_slow = []
    for cand in cands:
        try:
            got, total = _download_one(cand, dest, progress, timeout, max_bytes,
                                       slow_limit=slow_limit, cancel=cancel)
            return dest, got, total, cand
        except _TooSlow as e:
            too_slow.append(cand)
            errors.append("%s -> 太慢（%s）" % (cand, e))
        except Exception as e:
            # 保留 .part 与元数据：下一个通道（或下次运行）可以继续断点续传。
            # 元数据里的 total/etag 会在下一个通道响应时校验，异源会被丢弃重下，
            # 所以保留半截文件是安全的，还能避免"换通道就从头下"的浪费。
            errors.append("%s -> %s: %s" % (cand, type(e).__name__, e))

    if too_slow:
        # 所有通道都没达到速度阈值：回到第一个通道老老实实下完。
        got, total = _download_one(too_slow[0], dest, progress, timeout,
                                   max_bytes, slow_limit=None, cancel=cancel)
        return dest, got, total, too_slow[0]

    raise RuntimeError("所有下载通道都失败：" + "；".join(errors))
