# -*- coding: utf-8 -*-
"""增量更新包（patch）：只发布「相对上一版真正变化的文件」。

## 为什么要有这个模块

整包 ``AIWorkbench_v9.zip`` 约 236MB，其中 ``_internal/PyQt6`` 占 532MB
（未压缩）却几乎每个版本都一模一样；每版真正变的只有 ``AIWorkbench.exe``
（15MB，PyInstaller 把 PYZ 编进 exe 了）和 ``_internal/app/webui/**``（约 1MB）。
换句话说：每次让用户下载 236MB，换来的是 99% 的重复内容——单连接 3.5MB/s
也要 70 秒，并发再多也没用（瓶颈是那一个 236MB 的文件）。

## 怎么做

1. 发布机对安装目录跑 :func:`write_manifest`，得到 ``manifest.json``
   （每个文件的大小 + sha256）。
2. 下一版打包时用 :func:`make_patch`：只把「内容变了 / 新增」的文件塞进 zip，
   并附上**新版完整清单**。包内条目保持原有相对路径
   （``AIWorkbench.exe``、``_internal/app/webui/js/app.js``…），
   这样 ``app/updater.py`` 里 ``_install_impl`` 那套
   「解压 → 找到装 exe 的那一层 → 覆盖 → 重启」完全不用改。
3. 客户端装完，安装目录里就有了一份新版 ``manifest.json``，
   下一版算增量时直接拿它当基准（见 :func:`local_manifest`）。

## 边界

* 只依赖标准库（发布机与客户端都要能跑），绝不 import PyQt。
* 相对路径统一用 ``/`` 分隔（写 zip 跨平台安全），读磁盘时再 ``os.path.join``。
* 所有异常都翻成 ``RuntimeError("一句中文人话")``，不把英文 errno 漏给用户。
* **删除旧文件**：全量包覆盖安装不需要删文件，但增量包只覆盖"变化的文件"，
  新版删掉/改名的模块会永远留在用户机器上（旧 webui 资源、旧插件…迟早出诡异
  bug）。所以 ``removed`` 非空时包根会多一个 ``__delete__.txt``（一行一个
  相对路径），安装时先按它删、再覆盖；删不存在的文件静默跳过。
"""
import errno
import hashlib
import json
import os
import sys
import time
import zipfile

#: 清单文件名。放在安装根目录（与 ``AIWorkbench.exe`` 同级）。
MANIFEST_NAME = "manifest.json"

#: 删除清单文件名。只存在于**增量包 zip 根**，安装器读完就删，绝不装进安装目录。
#: 内容：UTF-8 纯文本，一行一个相对路径（``/`` 分隔），末尾换行。
DELETE_LIST_NAME = "__delete__.txt"

#: 清单里的应用标识，防止把别的程序的清单当基准。
APP_ID = "AIWorkbench"

# 15MB 的 exe 要分块算哈希：一次读进内存既慢又可能把内存打爆。
_CHUNK = 1024 * 1024

# 永远不进清单的目录（构建产物/版本库，任何情况下都不该发布）。
_ALWAYS_SKIP_DIRS = ("__pycache__", ".git", ".svn", ".idea", ".vs")

# 永远不进清单的文件（都是控制文件，不是产品内容）。
_ALWAYS_SKIP_FILES = (MANIFEST_NAME, DELETE_LIST_NAME)

# 永远不进清单的文件后缀（字节码每台机器都不一样，纳进去会让增量包永远"有变化"）。
_SKIP_SUFFIX = (".pyc", ".pyo")


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------
def _human_reason(err):
    """把 OSError 翻成一句用户能看懂的原因。

    Args:
        err: 原始异常。

    Returns:
        str: 中文原因（不带技术术语）。
    """
    if isinstance(err, PermissionError):
        return "没有文件写入权限，请用管理员身份运行"
    code = getattr(err, "errno", None)
    text = str(err or "").lower()
    if code == errno.ENOSPC or "no space" in text or ("磁盘" in text and "满" in text):
        return "磁盘空间不足"
    if (code in (errno.EACCES, errno.EPERM)
            or "permission" in text or "access is denied" in text
            or "拒绝访问" in str(err or "")):
        return "没有文件写入权限，请用管理员身份运行"
    if code == errno.ENOENT:
        return "文件不存在，可能已经被移动或删除"
    if "being used by another process" in text or "另一个程序" in str(err or ""):
        return "文件正被占用，请先关闭 AI 工作台再试"
    return "文件读写失败，请稍后重试"


def _wrap(action, err):
    """把底层异常包成 RuntimeError（消息 = 动作 + 人话原因）。"""
    return RuntimeError("%s：%s" % (action, _human_reason(err)))


def _norm_rel(path):
    """相对路径归一成 ``a/b/c`` 形式（写 zip、比清单都用这一种写法）。"""
    return str(path or "").replace("\\", "/").strip("/")


def _sha256(path):
    """分块算 sha256（1MB 一块）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _collect(root, skip_dirs=(), skip_files=(MANIFEST_NAME,)):
    """列出要纳入清单的文件。

    Args:
        root: 目录绝对路径。
        skip_dirs: 额外要跳过的目录（按名字或相对路径匹配）。
        skip_files: 要跳过的文件（按名字或相对路径匹配）。

    Returns:
        list[tuple[str, str, int]]: ``[(相对路径, 绝对路径, 字节数)]``，按路径排序。

    Raises:
        RuntimeError: 目录不存在或读不到文件信息。
    """
    root = os.path.abspath(str(root or ""))
    if not os.path.isdir(root):
        raise RuntimeError("目录不存在：%s" % (root or "（空）"))

    skip_dir_set = set(_ALWAYS_SKIP_DIRS)
    for d in (skip_dirs or ()):
        skip_dir_set.add(_norm_rel(d).lower())
    skip_file_set = set(_norm_rel(f).lower() for f in (skip_files or ()))

    out = []
    try:
        for cur, dirs, names in os.walk(root):
            rel_cur = "" if os.path.normcase(cur) == os.path.normcase(root) \
                else _norm_rel(os.path.relpath(cur, root))
            keep = []
            for d in dirs:
                cand = ("%s/%s" % (rel_cur, d)).lstrip("/").lower()
                if d.lower() in skip_dir_set or cand in skip_dir_set:
                    continue
                keep.append(d)
            dirs[:] = keep
            for name in names:
                rel = ("%s/%s" % (rel_cur, name)).lstrip("/")
                low = rel.lower()
                if name.lower() in _ALWAYS_SKIP_FILES:
                    continue
                if low in skip_file_set or name.lower() in skip_file_set:
                    continue
                if low.endswith(_SKIP_SUFFIX):
                    continue
                full = os.path.join(cur, name)
                try:
                    size = os.path.getsize(full)
                except OSError as e:
                    raise _wrap("读取文件大小失败（%s）" % rel, e)
                out.append((rel, full, int(size)))
    except OSError as e:
        raise _wrap("扫描目录失败", e)
    out.sort(key=lambda item: item[0])
    return out


def _files_of(manifest):
    """兼容两种入参：完整清单（含 ``files``）或直接的 files 表。"""
    if not isinstance(manifest, dict):
        return {}
    inner = manifest.get("files")
    if isinstance(inner, dict):
        return inner
    return manifest


def _payload(files, version="", extra=None):
    """拼清单内容（唯一出口，保证 write_manifest 与 make_patch 写出来的一致）。"""
    payload = {
        "app": APP_ID,
        "version": str(version or ""),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": dict(files or {}),
    }
    for key, value in (extra or {}).items():
        # extra 只做「补充」：绝不允许覆盖 files/version 这些核心字段，
        # 否则一个笔误就会写出基准本身就是错的清单。
        if key not in payload:
            payload[key] = value
    return payload


def _dump(payload):
    """清单序列化：sort_keys 保证同内容同字节（便于 diff 与复现），
    indent=1 保证每个文件一行，出问题时能直接 grep。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)


# --------------------------------------------------------------------------
# 公开接口
# --------------------------------------------------------------------------
def scan_tree(root, skip_dirs=(), skip_files=(MANIFEST_NAME,), progress=None):
    """递归扫描目录，算出每个文件的大小与 sha256。

    Args:
        root: 要扫描的目录。
        skip_dirs: 额外跳过的目录名/相对路径（``__pycache__``、``.git`` 永远跳过）。
        skip_files: 额外跳过的文件名/相对路径（默认跳过清单自身）。
        progress: 可选进度回调 ``progress(done_bytes, total_bytes)``。

    Returns:
        dict[str, dict]: ``{"_internal/app/x.js": {"size": 123, "sha": "…64位…"}}``。

    Raises:
        RuntimeError: 目录不存在、文件读不了、或没有权限。
    """
    entries = _collect(root, skip_dirs, skip_files)
    total = sum(size for _rel, _full, size in entries)
    done = 0
    table = {}
    for rel, full, size in entries:
        try:
            sha = _sha256(full)
        except OSError as e:
            raise _wrap("读取文件内容失败（%s）" % rel, e)
        table[rel] = {"size": int(size), "sha": sha}
        done += int(size)
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass
    return table


def write_manifest(root, version, extra=None):
    """把 :func:`scan_tree` 的结果写成 ``root/manifest.json``。

    先写临时文件再 ``os.replace``，避免中途崩溃留下半份清单
    （半份清单会让下一版把它当成"旧版基准"，算出错误的增量）。

    Args:
        root: 安装目录（清单写在这一层）。
        version: 版本号字符串，例如 ``"9.22.6"``。
        extra: 可选补充字段（如 ``{"base": "9.22.5"}``），不会覆盖核心字段。

    Returns:
        str: 写好的 ``manifest.json`` 绝对路径。

    Raises:
        RuntimeError: 写不进去（无权限/磁盘满/文件被占用）。
    """
    root = os.path.abspath(str(root or ""))
    if not os.path.isdir(root):
        raise RuntimeError("目录不存在：%s" % (root or "（空）"))
    files = scan_tree(root)
    path = os.path.join(root, MANIFEST_NAME)
    tmp = path + ".tmp"
    text = _dump(_payload(files, version, extra))
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise _wrap("写入版本清单失败", e)
    return path


def read_manifest(path):
    """读取清单。

    Args:
        path: ``manifest.json`` 的路径，或安装目录。

    Returns:
        dict | None: 读不到 / 内容非法一律返回 ``None``（调用方据此走全量更新，
        绝不因为清单坏了就把更新流程整个打断）。
    """
    target = str(path or "")
    if not target:
        return None
    if os.path.isdir(target):
        target = os.path.join(target, MANIFEST_NAME)
    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    clean = {}
    for rel, meta in files.items():
        if not isinstance(meta, dict):
            return None
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            return None
        sha = str(meta.get("sha") or "")
        if not sha:
            return None
        clean[_norm_rel(rel)] = {"size": size, "sha": sha}
    out = dict(data)
    out["files"] = clean
    out.setdefault("app", APP_ID)
    out.setdefault("version", "")
    return out


def parse_delete_list(text):
    """解析删除清单（``__delete__.txt``）的文本内容。

    规则：忽略空行与首尾空白；统一成 ``/`` 分隔；同一路径只保留一次。
    安全：出现绝对路径、盘符或 ``..`` 段一律视为非法 —— 这种清单绝不能用，
    否则一个坏包就能删掉安装目录之外的东西。

    Args:
        text: 删除清单的原始文本（UTF-8 解码后）。

    Returns:
        list[str] | None: 合法的相对路径列表；清单非法时返回 ``None``。
    """
    out = []
    for raw in str(text or "").splitlines():
        line = str(raw).strip()
        if not line:
            continue
        if line.startswith(("/", "\\")):
            return None
        rel = _norm_rel(line)
        if not rel:
            continue
        parts = rel.split("/")
        if ".." in parts or ":" in parts[0]:
            return None
        if rel not in out:
            out.append(rel)
    return out


def app_root():
    """本程序的安装目录：冻结包是 exe 所在目录，开发态回退仓库根。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # app/incremental.py → app/ → 仓库根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def local_manifest(app_dir=None):
    """取本机安装目录的清单（算增量的"旧版基准"）。

    Args:
        app_dir: 安装目录；默认见 :func:`app_root`。

    Returns:
        dict | None: 没有清单（老版本装上来还没写过）时返回 ``None``。
    """
    return read_manifest(app_dir or app_root())


def diff(old, new):
    """比对两份清单。

    Args:
        old: 旧版清单（完整清单或 files 表；``None`` 视为"全新"）。
        new: 新版清单（同上，必须有内容）。

    Returns:
        tuple[list[str], list[str]]: ``(changed, removed)``，元素是相对路径。
        ``changed`` 含"新增"与"内容变了"；``removed`` 是旧有新无。

    Raises:
        RuntimeError: 新版清单为空（说明扫描/打包出了问题，宁可报错也不算出一份空增量）。
    """
    old_files = _files_of(old)
    new_files = _files_of(new)
    if not new_files:
        raise RuntimeError("新版文件清单为空，无法比对，请检查新版本是否打包成功")

    changed = []
    for rel, meta in new_files.items():
        prev = old_files.get(rel)
        if (not isinstance(prev, dict)
                or int(prev.get("size") or 0) != int(meta.get("size") or 0)
                or str(prev.get("sha") or "") != str(meta.get("sha") or "")):
            changed.append(rel)
    removed = [rel for rel in old_files if rel not in new_files]
    return sorted(changed), sorted(removed)


def make_patch(dist_root, out_zip, old_manifest=None, version="", progress=None):
    """生成增量包：只装"变化了的文件" + 一份新版完整清单（+ 删除清单）。

    Args:
        dist_root: 新版安装目录（即 dist 里 ``AIWorkbench/`` 那一层）。
        out_zip: 输出 zip 路径。
        old_manifest: 旧版清单；``None`` 表示"全量"（所有文件都装进去）。
        version: 新版版本号，写进包内 ``manifest.json``。
        progress: 可选进度回调 ``progress(done_bytes, total_bytes)``。

    Returns:
        dict: ``{"zip": 路径, "changed": 数量, "removed": 数量, "bytes": 压缩后大小}``。
        ``removed`` 非空时包根还会多一个 ``__delete__.txt``（见 :data:`DELETE_LIST_NAME`）。

    Raises:
        RuntimeError: 目录不存在 / 打包过程写失败。
    """
    root = os.path.abspath(str(dist_root or ""))
    if not os.path.isdir(root):
        raise RuntimeError("待打包的目录不存在：%s" % (root or "（空）"))

    new_files = scan_tree(root)
    if old_manifest:
        changed, removed = diff(old_manifest, new_files)
    else:
        changed, removed = sorted(new_files), []

    out_zip = os.path.abspath(str(out_zip or "patch.zip"))
    # 输出 zip 万一落在待打包目录里，别把自己装进自己（不然每次算出来的 sha 都不一样）。
    try:
        rel_out = _norm_rel(os.path.relpath(out_zip, root))
    except ValueError:
        rel_out = ""
    if rel_out in changed:
        changed.remove(rel_out)
    total = sum(int(new_files[rel]["size"]) for rel in changed if rel in new_files)

    parent = os.path.dirname(out_zip)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError as e:
        raise _wrap("创建输出目录失败", e)

    done = 0
    payload = _payload(new_files, version)
    try:
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as z:
            for rel in changed:
                meta = new_files.get(rel)
                if not isinstance(meta, dict):
                    continue
                full = os.path.join(root, *rel.split("/"))
                if not os.path.isfile(full):
                    continue
                # arcname 用 '/' 分隔的相对路径：解压后目录结构与安装目录一致，
                # _install_impl 才能靠"哪个目录里有 AIWorkbench.exe"定位安装根。
                z.write(full, arcname=rel)
                done += int(meta.get("size") or 0)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
            # 新版删掉/改名的文件必须显式列出，否则它会永远留在用户机器上。
            # 包根位置与 manifest.json 同级，安装器读完就删、不会装进安装目录。
            if removed:
                z.writestr(DELETE_LIST_NAME,
                           "".join(rel + "\n" for rel in removed))
            # 清单最后写：中途失败时不会留下"看着完整、其实缺文件"的清单。
            z.writestr(MANIFEST_NAME, _dump(payload))
    except RuntimeError:
        raise
    except Exception as e:                   # 含 OSError / BadZipFile / 磁盘满
        raise _wrap("生成增量包失败", e)

    try:
        size = os.path.getsize(out_zip)
    except OSError as e:
        raise _wrap("读取增量包大小失败", e)
    return {"zip": out_zip, "changed": len(changed), "removed": len(removed),
            "bytes": int(size)}


def verify_tree(root, manifest, only=None, progress=None, strict=False):
    """逐个校验目录里的文件与清单是否一致（大小 + sha256）。

    这是"打完补丁真的等于新版"的唯一证明：覆盖安装后对安装目录跑一次，
    返回空列表就说明逐字节一致。

    Args:
        root: 要校验的目录。
        manifest: 清单（dict，或清单文件路径）。
        only: 只校验这些相对路径；``None`` 表示清单里的全部文件。
        progress: 可选进度回调 ``progress(done_bytes, total_bytes)``。
        strict: 为 True 时，把"清单里没有、磁盘上还有"的残留文件也算不一致
            （``only`` 给定时不生效）。增量包测试里用它证明"旧文件确实被删掉"，
            默认关闭：老安装目录本来就可能有历史残留，不该让常规校验因此失败。

    Returns:
        list[str]: 不一致（缺失/大小不符/sha 不同/严格模式下多出的残留）的相对路径，
        空 = 全部一致。

    Raises:
        RuntimeError: 清单为空或读不出来（这种情况"全对"是假结论，不能返回空列表）。
    """
    data = read_manifest(manifest) if isinstance(manifest, str) else manifest
    files = _files_of(data)
    if not files:
        raise RuntimeError("文件清单为空，无法校验")

    root = os.path.abspath(str(root or ""))
    if only:
        targets = [_norm_rel(p) for p in only]
    else:
        targets = sorted(files)
    total = sum(int(files[p]["size"]) for p in targets
                if isinstance(files.get(p), dict))

    done = 0
    bad = []
    for rel in targets:
        meta = files.get(rel)
        if not isinstance(meta, dict):
            bad.append(rel)
            continue
        full = os.path.join(root, *rel.split("/"))
        try:
            if not os.path.isfile(full):
                bad.append(rel)
                continue
            size = os.path.getsize(full)
            if int(size) != int(meta.get("size") or 0):
                bad.append(rel)
                continue
            if _sha256(full) != str(meta.get("sha") or ""):
                bad.append(rel)
                continue
        except OSError:
            bad.append(rel)
            continue
        done += int(size)
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    # strict：连"多出来的文件"也算不一致（清单里没有、磁盘上有）。
    # 只有它能证明"打完增量包后安装目录干净、没有旧版残留" —— 默认关闭是因为
    # 旧版安装目录可能本来就有历史残留，不该让常规校验因此失败。
    if strict and not only:
        for rel, _full, _size in _collect(root):
            if rel not in files:
                bad.append(rel)
    return bad
