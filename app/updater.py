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


# --------------------------------------------------------------------------
# 面向用户的「一句人话」（唯一文案出口）
#
# ★ v9.22.2：自动更新界面上只有「一根进度条 + 一句人话」。
#   测速 / 通道 / 镜像域名 / 断点续传 / ETag 这些内部实现细节**只允许进日志**，
#   绝不允许出现在 :func:`_stage_label` 产出的任何一句文案里 —— 用户不需要
#   为下载器的工作方式买单。
# --------------------------------------------------------------------------
_STAGE_TEXT = {
    "checking": "正在检查更新…",
    "probing": "正在准备下载…",
    "downloading": "正在下载…",
    "downloaded": "下载完成，正在准备安装…",
    "extracting": "正在解压更新包…",
    "installing": "正在安装，请稍候…",
    "restarting": "正在重启到新版本…",
    "done": "已更新到最新版本",
    "failed": "更新失败",
}

# 用户文案里绝不允许出现的内部词汇（_scrub 兜底过滤 + 自检脚本对照用）。
FORBIDDEN_WORDS = ("测速", "最快", "通道", "镜像", "直连", "ghproxy",
                   "githubusercontent", "ETag", "断点", "回退")

# 已经翻译成「人话」的原因片段：逐层包装时不要二次翻译。
_HUMAN_HINTS = ("网络不通", "磁盘空间不足", "下载文件损坏", "没有文件写入权限",
                "下载地址失效", "更新服务器暂时不可用", "更新包不存在",
                "更新包里缺少主程序")


def _log(msg):
    """内部诊断信息只写日志（通道名 / 域名 / 异常详情都走这里）。"""
    try:
        print("[updater] %s" % (msg,), flush=True)
    except Exception:
        pass


def _scrub(text):
    """兜底清洗：把混进文案里的内部词汇与网址抹掉。

    即使将来有人不小心把 ``"（通道：ghproxy.net）"`` 拼进文案，
    用户看到的也只会是干净的一句话。

    Args:
        text: 原始文案。

    Returns:
        str: 清洗后的文案（去掉了内部词汇、URL、空括号与多余空白）。
    """
    s = str(text or "")
    # 先摘网址/域名：否则先删「ghproxy」会留下孤零零的「.net」。
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\b[\w.-]+\.(?:net|com|top|cc|io|org)\b", "", s, flags=re.I)
    for word in FORBIDDEN_WORDS:
        s = s.replace(word, "")
    s = re.sub(r"[（(]\s*[)）]", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _percent(done, total):
    """算 0~100 的整数百分比；算不出来返回 ``None``（界面显示不确定态进度条）。

    Args:
        done: 已完成字节数。
        total: 总字节数（0 或未知时无法算百分比）。

    Returns:
        int | None: 整数百分比，或 ``None``。
    """
    try:
        d = float(done or 0)
        t = float(total or 0)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return int(max(0.0, min(100.0, d * 100.0 / t)))


def _stage_label(key, extra=None):
    """把内部阶段 key 翻成一句给用户看的人话（唯一的文案出口）。

    Args:
        key: 阶段 key，取值见 :data:`_STAGE_TEXT`
            （``checking / probing / downloading / downloaded / extracting /
            installing / restarting / done / failed``）。
        extra: 可选附加信息。传数字表示下载百分比（0~100）；
            传 dict 支持 ``{"percent": 42}`` 或
            ``{"reason": "网络不通，请检查代理后重试"}``。

    Returns:
        str: 一行可直接展示的中文文案（已过滤全部内部实现细节）。
    """
    k = str(key or "").strip().lower()
    percent = None
    reason = ""
    if isinstance(extra, dict):
        percent = extra.get("percent")
        reason = str(extra.get("reason") or "")
    elif isinstance(extra, (int, float)) and not isinstance(extra, bool):
        percent = extra

    if k == "failed":
        return _scrub("%s：%s" % (_STAGE_TEXT["failed"], reason or "请稍后重试"))

    if k == "downloading" and percent is not None:
        try:
            pct = int(round(float(percent)))
        except (TypeError, ValueError):
            return _STAGE_TEXT["downloading"]
        return "正在下载 %d%%" % max(0, min(100, pct))

    base = _STAGE_TEXT.get(k) or ""
    return _scrub(base) if base else "正在处理…"


def _emit_stage(on_stage, key, extra=None):
    """把阶段翻译成文案后回调给界面。

    回调异常一律吞掉：更新流程不能因为 UI 层的 bug 而中断。

    Args:
        on_stage: 调用方传入的阶段回调 ``on_stage(stage, text)``，可为 None。
        key: 阶段 key。
        extra: 透传给 :func:`_stage_label` 的附加信息。

    Returns:
        str: 本次实际展示的文案。
    """
    text = _stage_label(key, extra)
    _log("stage=%s text=%s" % (key, text))
    if on_stage:
        try:
            on_stage(str(key), text)
        except Exception:
            pass
    return text


def human_error(err):
    """把技术异常压成一句用户能看懂的原因（失败提示的唯一出口）。

    映射表：
      * 超时 / 连不上 / 代理 / DNS / SSL → ``网络不通，请检查代理后重试``
      * 磁盘满                           → ``磁盘空间不足``
      * 压缩包损坏 / 校验不一致           → ``下载文件损坏，请重试``
      * 没有写权限                       → ``没有文件写入权限，请用管理员身份运行``
      * 404 / 410                        → ``下载地址失效，请到发布页手动下载``
      * 其他                             → ``请稍后重试``

    Args:
        err: 异常对象或异常字符串。

    Returns:
        str: 一句不含技术术语的中文原因（不带「更新失败：」前缀）。
    """
    if err is None:
        return "请稍后重试"
    if isinstance(err, str):
        raw = err
        name = ""
    else:
        raw = "%s: %s" % (type(err).__name__, err)
        name = type(err).__name__
    low = raw.lower()

    # 0) 已经是人话了（本模块内部逐层包装，不能把好文案再翻成「请稍后重试」）
    msg = raw[len(name) + 2:] if (name and raw.startswith(name + ": ")) else raw
    for hint in _HUMAN_HINTS:
        if hint in msg:
            return msg

    # 1) 包本身坏了（必须在网络判断之前，BadZipFile 常伴生 ReadError）
    if (name in ("BadZipFile", "LargeZipFile")
            or "badzipfile" in low or "not a zip file" in low
            or "损坏" in raw or "checksum" in low
            or "hash mismatch" in low or "校验" in raw):
        return "下载文件损坏，请重试"
    # 2) 磁盘满
    if ("no space" in low or "enospc" in low or "errno 28" in low
            or "磁盘空间" in raw or "磁盘已满" in raw):
        return "磁盘空间不足"
    # 3) 网络类（GitHub 被墙 / 代理失效 / DNS 挂了都归这一类）
    if ("timed out" in low or "timeout" in low or "超时" in raw
            or "connect" in low or "connection" in low
            or "proxy" in low or "ssl" in low or "certificate" in low
            or "getaddrinfo" in low or "name or service not known" in low
            or "network" in low or "unreachable" in low
            or "remote end closed" in low or "所有下载" in raw):
        return "网络不通，请检查代理后重试"
    # 4) 权限
    if ("permission" in low or "access is denied" in low
            or "拒绝访问" in raw or "errno 13" in low):
        return "没有文件写入权限，请用管理员身份运行"
    # 5) 地址失效 / 服务端错误（含 requests 的 "404 Client Error" 写法）
    if "not found" in low or re.search(r"\b4\d\d\b", low):
        return "下载地址失效，请到发布页手动下载"
    if "server error" in low or re.search(r"\b5\d\d\b", low):
        return "更新服务器暂时不可用，请稍后再试"
    return "请稍后重试"


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
    """下载更新包（内部多线路 + 续传，对外只暴露「一句人话」的阶段提示）。

    Args:
        url: 下载地址。
        dest: 本地保存路径。
        progress: 字节进度回调 ``progress(done, total)``（与旧版一致，向后兼容）。
        timeout: 秒。
        on_stage: 阶段回调 ``on_stage(stage, text)``，stage ∈
            ``{"probing", "downloading", "downloaded", "failed"}``，
            ``text`` 已是可直接展示的中文文案（不含任何内部实现细节）。

    Returns:
        str: 下载完成后的本地文件路径（与旧版一致）。

    Raises:
        RuntimeError: 失败时抛出，异常消息是一句给用户看的白话原因。
    """
    try:
        return _download_impl(url, dest, progress=progress, timeout=timeout,
                              on_stage=on_stage)
    except Exception as e:
        reason = human_error(e)
        _log("下载失败：%s: %s" % (type(e).__name__, e))
        _emit_stage(on_stage, "failed", {"reason": reason})
        raise RuntimeError(reason) from e


def _download_impl(url, dest, progress=None, timeout=30, on_stage=None):
    """download() 的真实实现（异常交给 download() 统一翻译成一句人话）。"""

    def _progress_bridge(done, total):
        """字节进度 → 界面：整数百分比变化时才刷新文案，避免刷屏。"""
        pct = _percent(done, total)
        if pct is not None and pct != _progress_bridge.last:
            _progress_bridge.last = pct
            _emit_stage(on_stage, "downloading", {"percent": pct})
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    _progress_bridge.last = -1

    # 探测/选线路：对用户只是「正在准备下载…」，具体线路只进日志。
    _emit_stage(on_stage, "probing")
    try:
        from app import downloader as _dl
    except Exception:
        _dl = None
    if _dl is not None:
        try:
            best, _stats = _dl.pick_fastest(url, timeout=min(6, timeout))
            _log("选用下载线路：%s" % _dl.describe_channel(best))
            _emit_stage(on_stage, "downloading")
            _dl.download(url, dest, progress=_progress_bridge,
                         timeout=(10, timeout), prefer=best)
            _emit_stage(on_stage, "downloaded")
            return dest
        except Exception as e:
            # 首选线路不行就换备用线路，用户无需知道这件事，只写日志。
            _log("首选线路失败，换备用线路：%s: %s" % (type(e).__name__, e))
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        _emit_stage(on_stage, "downloading")
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
                    _progress_bridge(done, total)
        _emit_stage(on_stage, "downloaded")
        return dest
    _emit_stage(on_stage, "downloading")
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
                _progress_bridge(done, total)
    _emit_stage(on_stage, "downloaded")
    return dest


def install_package(zip_path, app_dir=None, progress=None, on_stage=None,
                    restart_exe="AIWorkbench.exe", dry_run=False):
    """把下载好的更新包解压、覆盖安装目录并安排本程序退出后重启。

    这就是「安装进度条/提示」的落点：GUI 下载完成后调本函数即可拿到
    解压/覆盖/重启三个阶段的可视化回调，无需自己写 zipfile + PowerShell。

    ``progress(done, total)``：解压字节进度回调（近似，total 为包内总字节）。
    ``on_stage(stage, text)``：阶段回调，stage ∈
        ``{"extracting", "installing", "restarting", "failed"}``；
        ``text`` 是可直接展示的一句人话。
    ``dry_run=True``：只解压并定位 exe，不真正覆盖/重启（供自测）。

    返回 dict：``{"ok": True, "src": 解压根目录, "app_dir": 安装目录,
    "exe": 目标exe, "restarted": bool}``。

    Raises:
        RuntimeError: 失败时抛出，异常消息是一句给用户看的白话原因。
    """
    try:
        return _install_impl(zip_path, app_dir=app_dir, progress=progress,
                             on_stage=on_stage, restart_exe=restart_exe,
                             dry_run=dry_run)
    except Exception as e:
        reason = human_error(e)
        _log("安装失败：%s: %s" % (type(e).__name__, e))
        _emit_stage(on_stage, "failed", {"reason": reason})
        raise RuntimeError(reason) from e


def _install_impl(zip_path, app_dir=None, progress=None, on_stage=None,
                  restart_exe="AIWorkbench.exe", dry_run=False):
    """install_package() 的真实实现（异常交给 install_package 翻译成人话）。"""
    import base64
    import os
    import sys
    import tempfile
    import time as _time
    import zipfile as _zf

    def _prog(done, total):
        try:
            if progress:
                progress(done, total)
        except Exception:
            pass

    zip_path = os.path.abspath(str(zip_path or ""))
    if not os.path.isfile(zip_path):
        _log("更新包不存在：%s" % zip_path)
        raise RuntimeError("更新包不存在，请重新下载")

    if app_dir is None:
        # frozen：本 exe 所在目录；开发态：仓库根目录
        app_dir = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                   else os.path.dirname(os.path.dirname(os.path.dirname(
                       os.path.abspath(__file__)))))
    app_dir = os.path.abspath(app_dir)

    tmp_root = os.path.join(tempfile.gettempdir(),
                            "AIWorkbench_upd_%d" % int(_time.time()))
    _emit_stage(on_stage, "extracting")
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
        _log("压缩包里没找到 %s，解压根目录：%s" % (restart_exe, tmp_root))
        raise RuntimeError("更新包里缺少主程序，无法自动安装，请到发布页重新下载")

    _emit_stage(on_stage, "installing")
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
    _emit_stage(on_stage, "restarting")
    return {"ok": True, "src": src, "app_dir": app_dir,
            "exe": os.path.join(app_dir, restart_exe), "restarted": True}

def format_notes(notes, limit=1200):
    n = (notes or "").strip()
    if len(n) > limit:
        n = n[:limit] + "\n…（更多内容见发布页）"
    return n
