# -*- coding: utf-8 -*-
"""自动检测更新（依赖 GitHub Releases，全免费）。

优先走 api.github.com（返回信息最全）；
本机/部分网络环境下 api.github.com 会被重定向，于是兜底走
github.com 的 releases.atom 与 releases/latest 重定向——这两个都在 github.com 域下，
只要能打开网页就能拿到版本号。
"""
import json
import re
import ssl
import time
import urllib.request

from . import version as ver

UA = "AIWorkbench-Updater/9.0 (+https://github.com/lhclhc123/AIWorkbench)"
TIMEOUT = 12


class NotFound(Exception):
    """仓库 / Release 不存在（还没发布过）。"""


def _req(url, token=None, accept=None, timeout=TIMEOUT):
    """发一个 GET。优先 requests（对系统代理/重定向兼容更好），
    失败再退回 urllib。返回 (status, final_url, text)；HTTP 4xx/5xx 不抛异常。"""
    headers = {"User-Agent": UA, "Accept": accept or "*/*"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        import requests
        r = requests.get(url, headers=headers, timeout=timeout,
                         allow_redirects=True)
        return r.status_code, str(r.url or url), (r.text or "")
    except ImportError:
        pass
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return (getattr(resp, "status", 200) or 200,
                    resp.geturl() or url,
                    resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:
            body = ""
        return e.code, url, body


def _normalize(release):
    """把 GitHub release JSON 整理成统一结构。"""
    tag = release.get("tag_name") or release.get("name") or ""
    assets = []
    for a in release.get("assets") or []:
        assets.append({
            "name": a.get("name", ""),
            "size": a.get("size", 0),
            "url": a.get("browser_download_url", ""),
            "downloads": a.get("download_count", 0),
        })
    return {
        "ok": True,
        "source": "api",
        "tag": tag,
        "latest": ver.version_tuple_of(tag),
        "name": release.get("name") or tag,
        "notes": release.get("body") or "",
        "html_url": release.get("html_url") or ver.LATEST_PAGE,
        "published_at": release.get("published_at") or "",
        "prerelease": bool(release.get("prerelease")),
        "assets": assets,
    }


def _check_api(repo, token=None, timeout=TIMEOUT):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    status, _final, text = _req(url, token, "application/vnd.github+json", timeout)
    if status == 404:
        raise NotFound(f"仓库或 Release 还不存在（HTTP 404）")
    if status >= 400:
        raise RuntimeError(f"HTTP {status}")
    return _normalize(json.loads(text))


_ATOM_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
_ATOM_TAG = re.compile(r"<id>tag:github\.com,2008:Repository/\d+/([^<]+)</id>")
_ATOM_LINK = re.compile(r'<link[^>]*href="([^"]+)"')
_ATOM_TITLE = re.compile(r"<title>([^<]*)</title>")
_ATOM_UPDATED = re.compile(r"<updated>([^<]*)</updated>")
_ATOM_CONTENT = re.compile(r'<content[^>]*>(.*?)</content>', re.DOTALL)


def _check_atom(repo, timeout=TIMEOUT):
    """兜底：解析 releases.atom（github.com 域，通常可达）。"""
    url = f"https://github.com/{repo}/releases.atom"
    status, _final, text = _req(url, timeout=timeout)
    if status == 404:
        raise NotFound(f"仓库 / releases.atom 不存在（HTTP 404）")
    if status >= 400:
        raise RuntimeError(f"HTTP {status}")
    entries = _ATOM_ENTRY.findall(text)
    if not entries:
        raise RuntimeError("releases.atom 里没有 release 条目")
    first = entries[0]
    m = _ATOM_TAG.search(first)
    tag = m.group(1) if m else ""
    lm = _ATOM_LINK.search(first)
    link = lm.group(1) if lm else ver.RELEASES_PAGE
    tm = _ATOM_TITLE.search(first)
    title = _unescape(tm.group(1)) if tm else tag
    um = _ATOM_UPDATED.search(first)
    cm = _ATOM_CONTENT.search(first)
    notes = _strip_html(_unescape(cm.group(1))) if cm else ""
    return {
        "ok": True, "source": "atom", "tag": tag,
        "latest": ver.version_tuple_of(tag), "name": title,
        "notes": notes, "html_url": link,
        "published_at": um.group(1) if um else "",
        "prerelease": False, "assets": [],
    }


def _check_redirect(repo, timeout=TIMEOUT):
    """再兜底：跟随 /releases/latest 重定向，从最终 URL 里抠出 tag。"""
    url = f"https://github.com/{repo}/releases/latest"
    status, final, _text = _req(url, timeout=timeout)
    if status >= 400:
        raise NotFound(f"仓库还没有发布任何 Release（HTTP {status}）")
    m = re.search(r"/releases/tag/([^/?#]+)", final or "")
    if not m:
        raise RuntimeError("无法从重定向地址解析版本号")
    tag = m.group(1)
    return {
        "ok": True, "source": "redirect", "tag": tag,
        "latest": ver.version_tuple_of(tag), "name": tag, "notes": "",
        "html_url": final, "published_at": "", "prerelease": False, "assets": [],
    }


_ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " "}


def _unescape(s):
    for k, v in _ENT.items():
        s = s.replace(k, v)
    return s


def _strip_html(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "")
    s = re.sub(r"</p>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def check(repo=None, current=None, token=None, timeout=TIMEOUT):
    """检查更新。

    返回 dict：{ok, has_update, current, latest, tag, notes, html_url, source, ...}
    """
    repo = (repo or ver.GITHUB_REPO).strip().strip("/")
    current = current or ver.VERSION
    errors = []
    notfound = 0
    info = None
    for fn in (_check_api, _check_atom, _check_redirect):
        try:
            info = fn(repo, token, timeout) if fn is _check_api else fn(repo, timeout)
            break
        except NotFound as e:
            notfound += 1
            errors.append(f"{fn.__name__}: {e}")
        except Exception as e:
            errors.append(f"{fn.__name__}: {type(e).__name__}: {e}")
    if info is None:
        if notfound == 3:
            return {"ok": False, "has_update": False, "current": current,
                    "no_release": True,
                    "error": f"仓库 {repo} 还没有发布任何 Release（先在 GitHub 打 tag 发布）",
                    "html_url": ver.RELEASES_PAGE, "repo": repo}
        return {"ok": False, "has_update": False, "current": current,
                "error": "；".join(errors), "html_url": ver.LATEST_PAGE,
                "repo": repo}
    info["has_update"] = ver.is_newer(info.get("tag") or "", current)
    info["current"] = current
    info["repo"] = repo
    info["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return info


def pick_asset(info, prefer=(".zip", ".exe")):
    """从 releases 里挑一个最适合下载的附件。"""
    assets = info.get("assets") or []
    for ext in prefer:
        for a in assets:
            if a.get("name", "").lower().endswith(ext):
                return a
    return assets[0] if assets else None


def download(url, dest, progress=None, timeout=30, on_stage=None):
    """下载更新包（多通道测速 + 镜像加速 + 断点续传 + 阶段提示）。

    ``progress(done, total)``：下载字节进度回调（与旧版一致，向后兼容）。
    ``on_stage(stage, detail)``：可选阶段回调，stage ∈
        ``{"probing", "downloading", "downloaded"}``，用于 GUI 显示阶段提示。
    """
    def _stage(stage, detail=""):
        try:
            if on_stage:
                on_stage(stage, detail)
        except Exception:
            pass

    _stage("probing", "正在测速选择最快通道…")
    # ★ v9.19：GitHub Release 直连实测被限速到 ~24KB/s，镜像可快几十倍；
    #   先走 downloader 选最快通道，失败再落回下面的直连兜底。
    try:
        from app import downloader as _dl
    except Exception:
        _dl = None
    if _dl is not None:
        try:
            best, _stats = _dl.pick_fastest(url, timeout=min(6, timeout))
            _stage("downloading", "正在下载（通道：%s）…" % _dl.describe_channel(best))
            _dl.download(url, dest, progress=progress,
                         timeout=(10, timeout), prefer=best)
            _stage("downloaded", "下载完成")
            return dest
        except Exception:
            _stage("downloading", "加速通道失败，回退直连下载…")
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        _stage("downloading", "正在下载（直连）…")
        with requests.get(url, headers={"User-Agent": UA}, timeout=timeout,
                          stream=True, allow_redirects=True) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        _stage("downloaded", "下载完成")
        return dest
    status, _final, _text = _req(url, timeout=timeout)
    if status >= 400:
        raise RuntimeError(f"下载失败：HTTP {status}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    _stage("downloaded", "下载完成")
    return dest


def install_package(zip_path, app_dir=None, progress=None, on_stage=None,
                    restart_exe="AIWorkbench.exe", dry_run=False):
    """把下载好的更新包解压、覆盖安装目录并安排本程序退出后重启。

    这就是「安装进度条/提示」的落点：GUI 下载完成后调本函数即可拿到
    解压/覆盖/重启三个阶段的可视化回调，无需自己写 zipfile + PowerShell。

    ``progress(done, total)``：解压字节进度回调（近似，total 为包内总字节）。
    ``on_stage(stage, detail)``：阶段回调，stage ∈
        ``{"extracting", "installing", "restarting", "done"}``。
    ``dry_run=True``：只解压并定位 exe，不真正覆盖/重启（供自测）。

    返回 dict：``{"ok": True, "src": 解压根目录, "app_dir": 安装目录,
    "exe": 目标exe, "restarted": bool}``。
    """
    import base64
    import os
    import sys
    import tempfile
    import time as _time
    import zipfile as _zf

    def _stage(stage, detail=""):
        try:
            if on_stage:
                on_stage(stage, detail)
        except Exception:
            pass

    def _prog(done, total):
        try:
            if progress:
                progress(done, total)
        except Exception:
            pass

    zip_path = os.path.abspath(str(zip_path or ""))
    if not os.path.isfile(zip_path):
        raise RuntimeError("更新包不存在：" + zip_path)

    if app_dir is None:
        # frozen：本 exe 所在目录；开发态：仓库根目录
        app_dir = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                   else os.path.dirname(os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__)))))
    app_dir = os.path.abspath(app_dir)

    tmp_root = os.path.join(tempfile.gettempdir(),
                            "AIWorkbench_upd_%d" % int(_time.time()))
    _stage("extracting", "正在解压更新包…")
    with _zf.ZipFile(zip_path) as z:
        infos = z.infolist()
        total = sum(i.file_size for i in infos) or 1
        done = 0
        for info in infos:
            z.extract(info, tmp_root)
            done += info.file_size
            _prog(done, total)

    # 在解压结果里找「直接装着 restart_exe 的那一层」
    target = restart_exe.lower()
    src = None
    for root, _dirs, files in os.walk(tmp_root):
        if any(f.lower() == target for f in files):
            src = root
            break
    if not src:
        raise RuntimeError("压缩包里没找到 %s，无法自动安装" % restart_exe)

    _stage("installing", "正在覆盖文件并准备重启…")
    if dry_run:
        return {"ok": True, "src": src, "app_dir": app_dir,
                "exe": os.path.join(app_dir, restart_exe), "restarted": False}

    # 用 -EncodedCommand（UTF-16LE base64）传脚本，彻底避开中文路径在
    # bat/ps1 文件里的编码坑。本程序退出后由 PowerShell 覆盖安装并重启。
    src_q = src.replace("'", "''")
    dst_q = app_dir.replace("'", "''")
    exe_q = os.path.join(app_dir, restart_exe).replace("'", "''")
    ps = (
        "$ErrorActionPreference = 'Stop'\n"
        "Start-Sleep -Seconds 2\n"
        "$ok = $false\n"
        "for ($i = 0; $i -lt 40; $i++) {\n"
        f"  try {{ Copy-Item -Path '{src_q}\\*' -Destination '{dst_q}' -Recurse -Force\n"
        "      $ok = $true; break }\n"
        "  catch { Start-Sleep -Milliseconds 500 }\n"
        "}\n"
        "if ($ok) {\n"
        "  Start-Sleep -Milliseconds 800\n"
        f"  Start-Process -FilePath '{exe_q}'\n"
        "}\n"
        f"Remove-Item -LiteralPath '{tmp_root}' -Recurse -Force -ErrorAction SilentlyContinue\n"
        f"Remove-Item -LiteralPath '{zip_path}' -Force -ErrorAction SilentlyContinue\n"
    )
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    from . import winproc
    winproc.popen(["powershell", "-NoProfile", "-EncodedCommand", encoded],
                  creationflags=0x08000000)
    _stage("restarting", "已安排重启（本程序即将退出）")
    return {"ok": True, "src": src, "app_dir": app_dir,
            "exe": os.path.join(app_dir, restart_exe), "restarted": True}

def format_notes(notes, limit=1200):
    n = (notes or "").strip()
    if len(n) > limit:
        n = n[:limit] + "\n…（更多内容见发布页）"
    return n
