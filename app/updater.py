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

from . import incremental as inc
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


def _is_patch_asset(asset):
    """判断某个附件是不是增量包。

    ★ 增量包只装"变化的文件"（本项目一版约 14MB，主要是变了的那 15MB exe），
    一旦被当成整包装进安装目录，用户拿到的就是一堆缺口
    （PyQt6 等没变的部分不会补），程序当场起不来。
    所以"名字里带 patch"的附件在挑整包时必须无条件排除。
    """
    name = str((asset or {}).get("name", "")).lower()
    return "patch" in name


def pick_asset(info, prefer=(".zip", ".exe")):
    """从 releases 里挑一个最适合下载的附件（永远不返回增量包）。"""
    # 先在"非增量包"里挑，顺序与旧版一致（.zip 优先于 .exe）。
    assets = [a for a in (info.get("assets") or []) if not _is_patch_asset(a)]
    for ext in prefer:
        for a in assets:
            if a.get("name", "").lower().endswith(ext):
                return a
    return assets[0] if assets else None


#: 增量包命名规范：``AIWorkbench_patch_v9.22.5_to_v9.22.6.zip``
PATCH_NAME_FMT = "AIWorkbench_patch_v{base}_to_{tag}.zip"

# 归一化用：去掉版本号前的 'v'（只在"分隔符/开头 + v + 数字"时去，
# 避免把 aiworkbench 里别的字母当成版本前缀）。
_VER_PREFIX = re.compile(r"(?<![a-z0-9])v(?=\d)")


def _norm_name(text):
    """把版本号/文件名归一成可比较的样子。

    ``AIWorkbench_patch_v9.22.5_to_v9.22.6.ZIP``
    → ``aiworkbench.patch.9.22.5.to.9.22.6.zip``
    （忽略大小写、忽略 'v' 前缀差异、'_' 与 '.' 一视同仁）
    """
    s = str(text or "").strip().lower()
    s = s.replace("_", ".").replace("-", ".")
    s = _VER_PREFIX.sub("", s)
    s = re.sub(r"\.{2,}", ".", s)
    return s.strip(".")


def pick_patch_asset(info, base_version, tag=None):
    """在 release 附件里找"从 base_version 到 tag"的增量包。

    匹配规则故意放宽：忽略大小写、允许有无 ``v`` 前缀、``.`` 与 ``_`` 都试、
    命名里带 ``patch`` 且同时出现"起始版本"和"目标版本"也算命中
    （发布脚本改个分隔符风格不至于让老用户退回 236MB 全量下载）。

    Args:
        info: :func:`check` 返回的 dict（用到 ``assets`` 与 ``tag``）。
        base_version: 本机当前版本，例如 ``"9.22.5"``。
        tag: 目标版本；``None`` 时用 ``info["tag"]``。

    Returns:
        dict | None: 命中则返回该附件，找不到返回 ``None``（调用方回退全量包）。
    """
    assets = (info or {}).get("assets") or []
    base = _norm_name(base_version)
    if not base:
        return None
    tag_text = tag if tag is not None else ((info or {}).get("tag") or "")
    tag_norm = _norm_name(tag_text)
    want = ("aiworkbench.patch.%s.to.%s.zip" % (base, tag_norm)) if tag_norm else ""

    loose = []
    for a in assets:
        name = str((a or {}).get("name") or "")
        low = name.lower()
        if "patch" not in low or not low.endswith(".zip"):
            continue
        norm = _norm_name(name)
        if want and norm == want:
            return a
        tokens = re.findall(r"\d+(?:\.\d+)+", norm)
        base_ok = any(t == base or t.startswith(base + ".") for t in tokens)
        tag_ok = (not tag_norm) or any(t == tag_norm or t.startswith(tag_norm + ".")
                                      for t in tokens)
        if base_ok and tag_ok:
            loose.append(a)
    return loose[0] if loose else None


def verify_patch_zip(zip_path, require_full=False):
    """校验一个 zip 是不是"自洽"的更新包（增量包或整包都可）。

    自洽有两层含义，本函数**都**检查：

    1. 必须带 ``manifest.json``（增量包在根目录；整包在 ``AIWorkbench/`` 下，
       两种布局都认），且里面 ``files`` 是一张非空的表；
    2. 包里每一个文件都必须在清单里登记，且解压后大小一致
       —— 否则装上去就是一堆"没人认账"的文件，等于把安装目录搞脏。

    ★ 注意别把第 2 条反过来要求：增量包里的清单描述的是**新版完整文件树**
    （只有这样才能当下一版的基准），而包里只装变化的文件，
    所以"清单里的文件必须都在包里"只有整包才成立 —— 需要那种严格检查时
    传 ``require_full=True``（或在解压后用 :func:`incremental.verify_tree`）。

    Args:
        zip_path: zip 路径。
        require_full: 为 True 时额外要求"清单里列的每个文件都在包里"（整包校验）。

    Returns:
        bool: 校验通过返回 ``True``；任何异常（含文件不存在、坏 zip）返回 ``False``，
        **绝不抛异常** —— 更新流程要能在坏包上优雅回退到全量下载。
    """
    try:
        import zipfile as _zf
        with _zf.ZipFile(zip_path) as z:
            names = []
            for info in z.infolist():
                raw = str(info.filename or "").replace("\\", "/")
                if raw.endswith("/"):           # 目录条目
                    continue
                # 路径穿越（'../x' 或 '/x'）各平台解压行为不一致，一律判坏。
                if raw.startswith("/") or ".." in raw.split("/"):
                    _log("增量包里有非法路径：%s" % raw)
                    return False
                names.append(raw)
            if inc.MANIFEST_NAME not in names:
                # 整包（zip 根下套一层 AIWorkbench/）里清单会深一层，也要认。
                prefix = ""
                for name in names:
                    if (name.lower().endswith("/" + inc.MANIFEST_NAME)
                            and name.count("/") == 1):
                        prefix = name[:-(len(inc.MANIFEST_NAME) + 1)]
                        break
                if not prefix:
                    _log("更新包缺少 %s" % inc.MANIFEST_NAME)
                    return False
            else:
                prefix = ""
            manifest_entry = (prefix + "/" + inc.MANIFEST_NAME).lstrip("/")
            data = json.loads(z.read(manifest_entry).decode("utf-8", "ignore"))
            files = data.get("files") if isinstance(data, dict) else None
            if not isinstance(files, dict) or not files:
                _log("更新包清单为空")
                return False
            norm_files = {str(k).replace("\\", "/"): v for k, v in files.items()}
            # 去掉整包多出来的那一层前缀，统一成"相对安装根"的写法再比对。
            rel_names = [(n[len(prefix) + 1:] if prefix and n.startswith(prefix + "/")
                          else n) for n in names]

            for name in rel_names:
                if name in (inc.MANIFEST_NAME, inc.DELETE_LIST_NAME):
                    continue
                meta = norm_files.get(name)
                if not isinstance(meta, dict):
                    _log("更新包里有清单未登记的文件：%s" % name)
                    return False
                if int(meta.get("size") or 0) != int(
                        z.getinfo((prefix + "/" + name) if prefix else name).file_size):
                    _log("更新包文件大小与清单不符：%s" % name)
                    return False

            # 删除清单：必须在包里、必须能解析（不许绝对路径/穿越），
            # 而且不许删掉新版清单里还有的文件 —— 那等于装完自废功能。
            if inc.DELETE_LIST_NAME in rel_names:
                entry = ((prefix + "/") if prefix else "") + inc.DELETE_LIST_NAME
                dels = inc.parse_delete_list(
                    z.read(entry).decode("utf-8", "ignore"))
                if dels is None:
                    _log("删除清单内容非法（绝对路径或路径穿越）")
                    return False
                for rel in dels:
                    if rel in norm_files:
                        _log("删除清单要删的文件在新版清单里仍存在：%s" % rel)
                        return False

            if require_full:
                for rel in norm_files:
                    if rel != inc.MANIFEST_NAME and rel not in rel_names:
                        _log("整包缺少清单登记的文件：%s" % rel)
                        return False
            return True
    except Exception as e:
        _log("增量包校验异常：%s: %s" % (type(e).__name__, e))
        return False


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
            try:
                # ★ 236MB 的更新包单连接只有 1~3MB/s：优先分段并发（默认 8 段）。
                #   服务器不支持 Range 时 download_parallel 会自己退回单连接。
                _dl.download_parallel(best, dest, progress=_progress_bridge,
                                      timeout=(10, timeout))
                _log("分段并发下载完成")
            except Exception as e:
                # 分段失败（个别镜像对 Range 支持不稳）→ 退回单连接多通道下载。
                _log("分段下载失败，改用单连接：%s: %s" % (type(e).__name__, e))
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

    ★ 增量包兼容：包根若有 ``__delete__.txt``，会**先按它删掉新版已经没有的
    旧文件、再覆盖**（全量包没有这个文件，行为与旧版完全一致）；包根的
    ``__delete__.txt`` 本身不会被拷进安装目录。

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
        # ★ 增量包兜底：某一版 exe 本身没变时，包里就只有变化的文件 + manifest.json，
        #   压根找不到 AIWorkbench.exe。增量包的根目录**就是**安装根目录（路径原样保留），
        #   所以拿 tmp_root 当 src 直接覆盖安装即可；找不到清单才判定为坏包。
        if os.path.isfile(os.path.join(tmp_root, inc.MANIFEST_NAME)):
            _log("增量包里没有 %s（本版未变），按清单覆盖安装" % restart_exe)
            src = tmp_root
        else:
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

    # 删除清单：增量包才有。正常布局里它在解压根目录（与 manifest.json 同级）；
    # 万一打包时多套了一层目录，就退到 src 同级去找。整包两种情况都不存在，
    # 下面那段 PowerShell 会自动变成空操作，行为与旧版完全一致。
    del_txt = os.path.join(tmp_root, inc.DELETE_LIST_NAME)
    if not os.path.isfile(del_txt):
        _cand = os.path.join(os.path.dirname(src), inc.DELETE_LIST_NAME)
        del_txt = _cand if os.path.isfile(_cand) else del_txt
    del_q = del_txt.replace("'", "''")
    if os.path.isfile(del_txt):
        _log("安装前先按删除清单清理旧文件：%s" % del_txt)

    ps = (
        "$ErrorActionPreference = 'Stop'\n"
        "Start-Sleep -Seconds 2\n"
        "$ok = $false\n"
        "for ($i = 0; $i -lt 40; $i++) {\n"
        "  try {\n"
        # ★ 必须先删再覆盖：增量包只覆盖"变化的文件"，新版删掉/改名的文件
        #   不显式删掉就会永远留在用户机器上（旧 webui 资源、旧插件…迟早出错）。
        #   用 -ErrorAction SilentlyContinue 让"本来就不存在"静默跳过，
        #   被占用删不掉也不在这里抛错（随后的 Copy-Item 会失败并重试）。
        f"    $delList = '{del_q}'\n"
        "    if (Test-Path -LiteralPath $delList) {\n"
        "      Get-Content -LiteralPath $delList -Encoding UTF8 | ForEach-Object {\n"
        "        $p = $_.Trim()\n"
        "        if ($p) {\n"
        f"          $full = Join-Path '{dst_q}' ($p -replace '/','\\')\n"
        "          if (Test-Path -LiteralPath $full) {\n"
        "            Remove-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue\n"
        "          }\n"
        "        }\n"
        "      }\n"
        "    }\n"
        # -Exclude：__delete__.txt 是安装器的控制文件，绝不能拷进安装目录。
        f"    Copy-Item -Path '{src_q}\\*' -Destination '{dst_q}' -Recurse -Force"
        f" -Exclude '{inc.DELETE_LIST_NAME}'\n"
        "    $ok = $true; break\n"
        "  }\n"
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
