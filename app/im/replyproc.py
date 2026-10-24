# -*- coding: utf-8 -*-
"""IM 回复的「子进程拿模型回复」通道（v9.20.0）。

为什么要有这个文件
------------------
IM 助手是在后台线程里等模型回话的。一旦模型端点卡住，**主进程里的请求线程**
就只能陪着一起卡（甚至影响退出）；想取消也没法从外面掐断一条正在读的 SSE。

于是把「调模型」这一步甩给一个**一次性子进程**：
    主进程  ──写 req.json──►  子进程（没 PyQt6，纯 HTTP）
            ◄─读 resp.json──
拿不到就 `kill()` —— 主进程最多只等 timeout 秒，不会更久。

三条硬约束（都是踩过的坑）
--------------------------
1. **绝不走 stdout 传结果**：Windows 上 spawn 出来的子进程 stdout 编码是
   本地代码页（GBK），中文/emoji 会乱码；管道写满还会把父子双方一起卡死。
   所以请求/响应一律用临时文件，stdout/stderr 只重定向进日志文件。
2. **子进程里绝不 import PyQt6**：那会让每次调用多付 1~2 秒的 Qt 初始化，
   打包成 windowed exe 时还可能弹窗。本模块只依赖 `app.llm_client`
   （requests + config + security），已经实测不含 PyQt6（见 `--selftest` 的
   `pyqt_loaded` 字段）。
3. **不留黑窗**：`CREATE_NO_WINDOW`。否则打包后每回一次消息就闪一个黑框。

密钥怎么进子进程
----------------
**命令行参数里永远不带 key**（进程列表里人人可见）。子进程自己从
`<工作区>/settings.json` 只读地读出 `api_keys_enc`（DPAPI 解密），
只读不写 —— 子进程绝不碰工作区写操作（`Workspace.load_settings()` 会在
提示词版本不一致时回写，所以这里不用它，自己解析）。

请求 / 响应格式
---------------
请求文件：`{"messages": [...], "model": "glm-4-flash", "timeout": 180, "extra": {...}}`
响应文件：`{"ok": true, "text": "...", "error": ""}`
两者都放在 `tempfile.mkdtemp(prefix="awb_imr_")` 里，用完整个目录删掉。

用法
----
    from app.im import replyproc
    if replyproc.available():
        r = replyproc.request(messages, model="glm-4-flash", timeout=180)
    else:
        ... # 调用方退回进程内调用
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

# 子进程日志文件名（stdout/stderr 都重定向到这里，避免管道写满死锁）
_CHILD_LOG = "child.log"
_REQ_FILE = "req.json"
_RESP_FILE = "resp.json"

# available() 的探测结果缓存：None = 还没探过
_AVAIL = None
_AVAIL_LOCK = threading.Lock()

# 子进程路径其实没有"多条可退"：开发态只能是 `python -m app.im.replyproc`，
# 冻结态只能是 exe 的 `--im-reply` 分流（冻结包里没有解释器可用）。
# 所以"记住哪条路能用"就等价于下面这个 available() 的缓存。
_PROBE_TIMEOUT = 8.0    # 探测最多等这么久（正常应 < 2s）

# 可注入的假 responder（仅测试用）：
#     replyproc._responder = lambda messages, model, timeout: "测试回复"
# 设了它就完全不联网。子进程里不会被继承（那是另一个进程），
# 所以探针测"真·子进程"时用请求里的 extra["_test_fake"]（见 worker_main）。
_responder = None

_logger = None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def set_logger(fn):
    """注入日志回调（callable(str)）。只在出错时调用，不会传任何密钥。"""
    global _logger
    _logger = fn


def _log(msg):
    """记一条诊断。只对父进程有效 —— 子进程没有日志回调可用，
    它的失败原因一律走 resp.json 的 error 字段带回（不往 stderr 打中文，避免乱码）。
    """
    fn = _logger
    if not fn:
        return
    try:
        fn(str(msg))
    except Exception:
        pass


def _project_root():
    """项目根目录（开发态）；冻结态用 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _no_window_flags():
    """Windows 下别弹黑框（打包成 --windowed 后这是用户一眼能看见的问题）。"""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _spawn_argv(req_path="--selftest"):
    """当前环境该走的那条启动方式：(mode, argv, cwd)。

    - 开发态：python -m app.im.replyproc <req>
    - 冻结态：exe --im-reply <req>   （依赖 main.py 里的分流分支）
    """
    root = _project_root()
    if getattr(sys, "frozen", False):
        # 冻结态没有解释器，只能让 exe 自己分流
        return "frozen", [sys.executable, "--im-reply", req_path], root
    return "dev", [sys.executable, "-m", "app.im.replyproc", req_path], root


def _popen(argv, cwd, log_path):
    """起子进程：stdin 断开，stdout/stderr 进文件（不进管道 → 不会写满卡死）。"""
    logf = open(log_path, "ab")
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=_no_window_flags(),
            close_fds=True,
        )
    finally:
        # Popen 已经把句柄复制给子进程，父进程这份可以关
        try:
            logf.close()
        except Exception:
            pass


def _read_log(path, limit=600):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return ""
    txt = data.decode("utf-8", "replace")
    txt = txt.strip()
    if len(txt) > limit:
        txt = "…" + txt[-limit:]
    return txt


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, obj):
    """先写 .tmp 再 replace：父进程绝不会读到写了一半的 JSON。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _resp_path(req_path):
    """响应文件固定和请求文件同目录（一个请求一个临时目录，不会撞）。"""
    return os.path.join(os.path.dirname(os.path.abspath(req_path)), _RESP_FILE)


# ---------------------------------------------------------------------------
# 子进程这边：读请求 → 调模型 → 写响应
# ---------------------------------------------------------------------------
def _read_keys():
    """只读地取用户配置的 key（子进程里绝不能写工作区）。

    为什么不用 `Workspace.load_settings()`：它会因为 `prompt_version` 不一致
    而**回写** settings.json（见 workspace.py）。子进程只该读。
    """
    keys = {}
    try:
        from app import config, security
        from app import workspace as ws_mod
        from app.workspace import Workspace
        path = ws_mod.get_last_workspace()
        if not path:
            return dict(config.DEFAULT_API_KEYS)
        settings_file = Workspace(path).settings_file
        if not os.path.isfile(settings_file):
            return dict(config.DEFAULT_API_KEYS)
        with open(settings_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if "api_keys_enc" in raw:
            keys = security.unseal_dict(raw.get("api_keys_enc") or {}) or {}
        elif isinstance(raw.get("api_keys"), dict):
            # 老工作区：明文 / 单值密文
            keys = {k: (security.unseal(v) if security.is_sealed(v) else v)
                    for k, v in (raw.get("api_keys") or {}).items()}
        for k, v in config.DEFAULT_API_KEYS.items():
            keys.setdefault(k, v)
    except Exception as e:
        _log("读取 API Key 失败：%s: %s" % (type(e).__name__, e))
        try:
            from app import config
            keys = dict(config.DEFAULT_API_KEYS)
        except Exception:
            keys = {}
    return keys


def _real_call(messages, model, timeout):
    """真正调模型。复用 LLMClient（它不含 PyQt6，见模块头注释）。

    行为对齐 main_window._im_llm：指定模型失败后退到 auto，尽量给用户一句话，
    而不是让消息石沉大海。整体预算压在 timeout 的 90% 以内，
    好让子进程自己先失败、把错误写回去，而不是被父进程硬杀（硬杀没有错误原因）。
    """
    from app import llm_client

    cli = llm_client.LLMClient()
    keys = _read_keys()
    if keys:
        cli.set_keys(keys)

    deadline = time.perf_counter() + max(5.0, float(timeout) * 0.9)

    def budget(cap):
        left = deadline - time.perf_counter()
        if left <= 2:
            raise TimeoutError("子进程内模型调用已用完时间预算")
        return max(2.0, min(float(cap), left))

    model = (model or "glm-4-flash").strip()
    try:
        return cli.chat(messages, model, False, None, budget(min(120, timeout))) or ""
    except Exception as first:
        if model in ("auto", ""):
            raise
        try:
            return cli.chat(messages, "auto", False, None, budget(min(120, timeout))) or ""
        except Exception:
            raise first


def _fake_call(text):
    """探针用：不联网。`sleep:N` 睡 N 秒；`echo:xxx` 原样回。"""
    if text.startswith("sleep:"):
        time.sleep(float(text.split(":", 1)[1] or 30))
        return "睡醒了"
    if text.startswith("echo:"):
        return text[5:]
    return text or "ok"


def worker_main(req_path) -> int:
    """子进程入口：读请求 JSON、调模型、把结果 JSON 写到 out 文件。

    返回 0 表示"结果文件已写好"（不代表模型成功，模型失败也是 ok=false 的结果）；
    非 0 表示连结果都没法写（请求文件不存在 / JSON 坏了 / 写盘失败）。
    """
    # --selftest：只证明"这个子进程能跑起来、而且没把 Qt 拖进来"
    if not req_path or str(req_path) == "--selftest":
        out = os.environ.get("AWB_IM_REPLY_SELFTEST_OUT")
        if out:
            try:
                _write_json(out, {"ok": True, "selftest": True,
                                  "pyqt_loaded": _pyqt_loaded(),
                                  "pid": os.getpid()})
            except Exception:
                return 3
        return 0

    try:
        req = _read_json(req_path)
        if not isinstance(req, dict):
            raise ValueError("请求文件不是 JSON 对象")
    except Exception as e:
        _log("读取请求失败：%s: %s" % (type(e).__name__, e))
        return 2

    messages = req.get("messages")
    model = req.get("model") or "glm-4-flash"
    try:
        timeout = float(req.get("timeout") or 180)
    except Exception:
        timeout = 180.0
    extra = req.get("extra") if isinstance(req.get("extra"), dict) else {}

    out_path = _resp_path(req_path)
    if not isinstance(messages, list) or not messages:
        try:
            _write_json(out_path, {"ok": False, "text": "", "error": "messages 为空或类型不对"})
        except Exception:
            return 3
        return 0

    t0 = time.perf_counter()
    try:
        # 探针注入的假 responder 优先（同一进程内直接调 worker_main 时生效）
        if _responder is not None:
            text = _responder(messages, model, timeout)
        elif extra.get("_test_fake"):
            text = _fake_call(str(extra["_test_fake"]))
        else:
            text = _real_call(messages, model, timeout)
        res = {"ok": True, "text": text if isinstance(text, str) else str(text),
               "error": "", "ms": round((time.perf_counter() - t0) * 1000.0, 1),
               "pyqt_loaded": _pyqt_loaded()}
    except Exception as e:
        res = {"ok": False, "text": "",
               "error": ("%s: %s" % (type(e).__name__, e))[:800],
               "ms": round((time.perf_counter() - t0) * 1000.0, 1),
               "pyqt_loaded": _pyqt_loaded()}

    try:
        _write_json(out_path, res)
    except Exception as e:
        _log("写响应失败：%s: %s" % (type(e).__name__, e))
        return 3
    return 0


def _pyqt_loaded():
    """子进程里 Qt 有没有被拖进来（探针会断言这里是 False）。"""
    return any(k == "PyQt6" or k.startswith("PyQt6.") for k in list(sys.modules))


# ---------------------------------------------------------------------------
# 主进程这边：探测 + 调用 + 硬杀
# ---------------------------------------------------------------------------
def _probe_once():
    """轻量探测：spawn 一个只写 selftest 就退出的子进程。返回 (ok, why)。"""
    tmp = tempfile.mkdtemp(prefix="awb_imr_probe_")
    out = os.path.join(tmp, "selftest.json")
    log_path = os.path.join(tmp, _CHILD_LOG)
    try:
        _how, argv, cwd = _spawn_argv("--selftest")
        env = dict(os.environ)
        env["AWB_IM_REPLY_SELFTEST_OUT"] = out
        logf = open(log_path, "ab")
        try:
            p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=logf, stderr=subprocess.STDOUT,
                                 creationflags=_no_window_flags(), env=env,
                                 close_fds=True)
        finally:
            try:
                logf.close()
            except Exception:
                pass
        try:
            rc = p.wait(timeout=_PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            try:
                p.wait(timeout=5)
            except Exception:
                pass
            return False, "探测超时（%.0fs），日志：%s" % (_PROBE_TIMEOUT, _read_log(log_path))
        if rc != 0:
            return False, "探测子进程退出码 %s，日志：%s" % (rc, _read_log(log_path))
        if not os.path.isfile(out):
            return False, "探测子进程没写结果文件（exit=0），日志：%s" % _read_log(log_path)
        info = _read_json(out)
        if not info.get("ok"):
            return False, "探测结果 ok=false：%s" % str(info)[:200]
        if info.get("pyqt_loaded"):
            # 能用，但方案的价值（不加载 Qt）没了 —— 还是算失败，别把性能坑带上线
            return False, "子进程里加载了 PyQt6（方案失效）"
        return True, "ok"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def available() -> bool:
    """能不能用子进程模式。不能就返回 False（调用方自动退回进程内调用）。

    结果缓存在模块级变量里 —— 探测要 spawn 一个 python 进程，
    每次回消息都探一遍等于白扔几百毫秒。
    """
    global _AVAIL
    if _AVAIL is not None:
        return _AVAIL
    with _AVAIL_LOCK:
        if _AVAIL is not None:
            return _AVAIL
        ok, why = _probe_once()
        if not ok:
            _log("子进程模式不可用：" + why)
        _AVAIL = bool(ok)
        return _AVAIL


def _reset_probe_cache():
    """仅供探针/测试：忘掉缓存，让 available() 重新探一次。"""
    global _AVAIL
    with _AVAIL_LOCK:
        _AVAIL = None


def _kill(p):
    try:
        p.kill()
    except Exception:
        pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass


def request(messages, model="glm-4-flash", timeout=180, extra=None) -> dict:
    """同步调用：起子进程 → 等它拿回复 → 返回。**绝不抛异常、绝不卡超过 timeout。**

    Returns:
        {"ok": bool, "text": str, "error": str, "ms": float, "mode": "proc"|"inproc"}
    """
    t0 = time.perf_counter()

    def done(ok, text="", error="", mode="proc"):
        return {"ok": bool(ok), "text": text or "", "error": error or "",
                "ms": round((time.perf_counter() - t0) * 1000.0, 1), "mode": mode}

    # 参数先校验：明显不对的请求不用起进程
    if not isinstance(messages, list) or not messages:
        return done(False, error="messages 为空或不是列表（需要至少一条消息）")

    try:
        timeout = float(timeout)
    except Exception:
        timeout = 180.0
    if timeout <= 0:
        timeout = 180.0

    if not available():
        return done(False, error="子进程不可用", mode="inproc")

    tmp = tempfile.mkdtemp(prefix="awb_imr_")
    req_path = os.path.join(tmp, _REQ_FILE)
    log_path = os.path.join(tmp, _CHILD_LOG)
    try:
        payload = {"messages": messages, "model": model or "glm-4-flash",
                   "timeout": timeout, "extra": dict(extra or {})}
        _write_json(req_path, payload)          # 注意：里面没有任何 api_key

        mode, argv, cwd = _spawn_argv(req_path)
        try:
            p = _popen(argv, cwd, log_path)
        except Exception as e:
            return done(False, error="启动子进程失败：%s: %s" % (type(e).__name__, e))

        # 硬超时：宁可杀，也不让调用方线程多等一秒。
        # 给解释器启动留一点余量（否则 timeout=2 时子进程还没 import 完就被杀）。
        grace = min(5.0, max(1.0, timeout * 0.25))
        hard = timeout + grace
        try:
            p.wait(timeout=hard)
        except subprocess.TimeoutExpired:
            _kill(p)
            msg = "子进程超时（%ss）" % _fmt_sec(timeout)
            tail = _read_log(log_path, 300)
            _log(msg + ("，子进程日志：" + tail if tail else ""))
            return done(False, error=msg, mode="proc")
        except Exception as e:
            _kill(p)
            return done(False, error="等待子进程异常：%s: %s" % (type(e).__name__, e))

        resp_path = _resp_path(req_path)
        if not os.path.isfile(resp_path):
            tail = _read_log(log_path)
            return done(False, error="子进程没写结果（exit=%s）%s"
                        % (p.returncode, ("；" + tail) if tail else ""))

        try:
            res = _read_json(resp_path)
        except Exception as e:
            return done(False, error="结果文件损坏：%s: %s" % (type(e).__name__, e))

        if not isinstance(res, dict):
            return done(False, error="结果文件格式不对")
        if res.get("ok"):
            return done(True, text=res.get("text") or "")
        return done(False, error=res.get("error") or "子进程返回失败（无原因）")
    except Exception as e:      # 兜底：这条路径上任何意外都不该炸到调用方
        _log("replyproc.request 异常：%s: %s" % (type(e).__name__, e))
        return done(False, error="%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fmt_sec(v):
    """180.0 -> '180'，2.5 -> '2.5'（错误文案别出现 180.0s 这种别扭写法）。"""
    try:
        f = float(v)
        return str(int(f)) if abs(f - int(f)) < 1e-6 else ("%g" % f)
    except Exception:
        return str(v)


if __name__ == "__main__":
    # `python -m app.im.replyproc <req.json>` / `... --selftest`
    sys.exit(worker_main(sys.argv[1] if len(sys.argv) > 1 else ""))
