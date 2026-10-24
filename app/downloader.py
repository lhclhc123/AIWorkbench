# -*- coding: utf-8 -*-
"""下载加速：多通道测速 + 选最快 + 断点续传 + 失败切换。

为什么需要这个模块（本机实测）：
- GitHub Release / raw 直连被严重限速（约 24KB/s），而 ``ghproxy.net`` 镜像
  实测可用且快（秒级）；``ghproxy.com`` / ``mirror.ghproxy.com`` /
  ``github.moeyy.xyz`` 在本机均超时，所以镜像只内置 ghproxy.net。
- 本机还有透明代理（https_proxy），但它对 github 会**间歇 502**，
  所以不能"一律走代理"，只能把直连/镜像都当候选，**测速取最快**。

设计要点：
1. ``mirror_candidates()`` 只对 GitHub 域名追加镜像，其他网址不动；
2. ``pick_fastest()`` 用 ``Range: bytes=0-1023`` 测首字节延迟，选最快的；
3. ``download()`` 依次尝试候选通道，失败自动切下一个；
4. 写 ``<dest>.part`` 再原子改名，支持 Range 断点续传；
5. 全程有大小上限与进度回调，不会无声卡死。
"""
import os
import re
import time

UA = "Mozilla/5.0 (AIWorkbench)"
CHUNK = 65536

# 只有这些 GitHub 域名才做镜像改写（别的站点改写会 404）。
_GH_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
)

# 本机实测唯一可用镜像。
MIRROR_PREFIX = "https://ghproxy.net/"


def _host_of(url):
    try:
        from urllib.parse import urlsplit
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def mirror_candidates(url):
    """返回下载候选通道；GitHub 域名自动追加 ghproxy.net 镜像。"""
    url = str(url or "").strip()
    cands = [url]
    if _host_of(url) in _GH_HOSTS:
        mirrored = MIRROR_PREFIX + url
        if mirrored not in cands:
            cands.append(mirrored)
    return cands


def _is_mirror(url):
    return str(url).startswith(MIRROR_PREFIX)


def describe_channel(url):
    return "镜像加速" if _is_mirror(url) else "直连"


def _probe(url, timeout=4.0):
    """测「首字节延迟」（秒）。失败返回 None。"""
    try:
        import requests
    except ImportError:
        return None
    try:
        t0 = time.time()
        with requests.get(
                url,
                headers={"User-Agent": UA, "Range": "bytes=0-1023"},
                stream=True,
                timeout=(timeout, timeout),
                allow_redirects=True) as r:
            if r.status_code not in (200, 206):
                return None
            for _ in r.iter_content(1024):
                break
        return max(0.001, time.time() - t0)
    except Exception:
        return None


def pick_fastest(url, timeout=4.0):
    """测速选出最快通道。

    返回 ``(best_url, latencies)``；若全部测速失败，回落到原始 URL。
    ``latencies`` 是 ``{url: 秒 or None}``，便于上层展示"为什么选它"。
    """
    cands = mirror_candidates(url)
    latencies = {}
    best = None
    for cand in cands:
        d = _probe(cand, timeout=timeout)
        latencies[cand] = d
        if d is not None and (best is None or d < latencies[best]):
            best = cand
    return (best or url), latencies


def _download_one(url, dest, progress=None, timeout=(10, 60),
                  max_bytes=512 * 1024 * 1024, _allow_restart=True):
    """从单个通道下载到 ``dest``，返回 ``(got, total)``。失败抛异常。"""
    import requests

    part = dest + ".part"
    parent = os.path.dirname(dest) or "."
    os.makedirs(parent, exist_ok=True)

    resume_from = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if resume_from:
        headers["Range"] = "bytes=%d-" % resume_from

    with requests.get(url, headers=headers, stream=True, timeout=timeout,
                      allow_redirects=True) as r:
        # 416：.part 已完整或已损坏 —— 删掉重来一次。
        if r.status_code == 416 and _allow_restart:
            try:
                os.remove(part)
            except OSError:
                pass
            return _download_one(url, dest, progress, timeout, max_bytes,
                                 _allow_restart=False)
        if r.status_code not in (200, 206):
            raise RuntimeError("HTTP %s" % r.status_code)

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
            # 换通道前清掉半截文件，避免 Range 续到错误内容上。
            try:
                if os.path.exists(dest + ".part"):
                    os.remove(dest + ".part")
            except OSError:
                pass
            errors.append("%s -> %s: %s" % (cand, type(e).__name__, e))

    raise RuntimeError("所有下载通道都失败：" + "；".join(errors))
