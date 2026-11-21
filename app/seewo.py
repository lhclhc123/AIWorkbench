# -*- coding: utf-8 -*-
"""希沃共享屏幕集成 —— 配置模型与接线（v9.23.0）。

分工：
  · ``app/seewo_steps.py``  纯自动化（步骤引擎 / 定位 / 播放 / 等结束 / 关共享）
  · **本模块**              配置读写、账号密码加密、工作区路径、接定时任务
  · ``app/gui/seewo_settings.py``  UI

配置落 ``<工作区>/.awb/seewo.json``，结构见 :data:`DEFAULT_CONFIG`。

账号密码的处理（**明文绝不许落盘**）
------------------------------------
  · 优先用 Windows DPAPI 加密后存 ``creds_enc``（``security.seal``，密文绑定
    当前 Windows 账户，拷到别的机器/别的账户都解不开）。
  · 如果当前环境拿不到 DPAPI（非 Windows 或 symbol 缺失），**降级为只存内存**：
    ``creds_enc`` 留空，账号密码只活在本进程里（``_MEM_CREDS``），关程序即丢。
    这种情况下 :func:`credential_mode` 会明确返回 ``"memory"``，
    :func:`warnings_of` 也会给出一条中文提示 —— 绝不偷偷用弱混淆冒充加密。
  · 任何落盘前都会剥掉 ``_user`` / ``_pass`` / 其它 ``_`` 开头的运行期字段
    （见 :func:`_persistable`）。
"""
import json
import os
import threading
import time

from . import security
from . import seewo_steps as steps_mod

CONFIG_DIRNAME = ".awb"
CONFIG_FILENAME = "seewo.json"
PROBE_FILENAME = "seewo_probe.json"

#: 默认配置（字段与用户确认过的结构完全一致；新增字段必须给默认值）
DEFAULT_CONFIG = {
    "enabled": False,
    "site_url": "",
    "login": {
        "url": "",
        "user_sel": "",
        "pass_sel": "",
        "submit_sel": "",
        "logged_in_sel": "",
    },
    "share": {
        "entry_sel": "",
        "confirm_sel": "",
        "started_sel": "",
    },
    "media": {
        "list_sel": "",
        "item_sel_tpl": "",
        "play_btn_sel": "",
        "video_sel": "video",
        "ended_sel": "",
    },
    "steps": [],
    "stop_share_sel": "",
    "creds_enc": "",
}

#: 必须由主任在**本机**确认一次的字段（没有通用值，只能对着真实站点挑）
REQUIRED_FIELDS = (
    ("login.user_sel", "账号输入框"),
    ("login.pass_sel", "密码输入框"),
    ("login.submit_sel", "登录按钮"),
    ("share.entry_sel", "「共享屏幕」入口"),
    ("media.list_sel", "媒体列表容器"),
    ("stop_share_sel", "「结束共享」按钮"),
)

#: 运行期字段（不落盘）
_RUNTIME_KEYS = ("_user", "_pass", "_creds_mode", "_suggested", "_media_hint")

# 无 DPAPI 时的内存兜底：{工作区绝对路径: {"u":..., "p":...}}
_MEM_CREDS = {}
_MEM_LOCK = threading.Lock()

# 停止信号：每个工作区一个 Event（UI 的「停止」和定时任务的取消都走它）
_STOP_EVENTS = {}
_STOP_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def config_path(workspace_path):
    """``<工作区>/.awb/seewo.json``。"""
    return os.path.join(os.path.abspath(workspace_path), CONFIG_DIRNAME,
                        CONFIG_FILENAME)


def probe_path(workspace_path):
    """``<工作区>/.awb/seewo_probe.json``（「学一次」导出）。"""
    return os.path.join(os.path.abspath(workspace_path), CONFIG_DIRNAME,
                        PROBE_FILENAME)


def internal_dir(workspace_path):
    return os.path.join(os.path.abspath(workspace_path), CONFIG_DIRNAME)


# ---------------------------------------------------------------------------
# 配置归一化 / 读写
# ---------------------------------------------------------------------------
def _deep_merge(base, incoming):
    """把 incoming 合进 base 的副本（未知字段保留，缺失字段用默认值）。"""
    out = json.loads(json.dumps(base, ensure_ascii=False))
    if not isinstance(incoming, dict):
        return out
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif k == "steps":
            out[k] = normalize_steps(v)
        else:
            out[k] = v
    return out


def normalize_steps(raw):
    """把步骤列表收拾干净：丢掉不是 dict 的、补齐字段、把 timeout 转成数字。"""
    out = []
    for s in (raw or []):
        if not isinstance(s, dict):
            continue
        action = str(s.get("action") or "click").strip().lower()
        if action not in steps_mod.ACTIONS:
            action = "click"
        try:
            timeout = float(s.get("timeout") or 15)
        except Exception:
            timeout = 15.0
        out.append({
            "action": action,
            "selector": str(s.get("selector") or ""),
            "value": "" if s.get("value") is None else str(s.get("value")),
            "timeout": max(0.2, min(3600.0, timeout)),
        })
    return out


def normalize_config(raw, workspace_path=None):
    """读进来的东西一律过这里：不合法也不会炸，缺字段补默认值。"""
    cfg = _deep_merge(DEFAULT_CONFIG, raw if isinstance(raw, dict) else {})
    cfg["enabled"] = bool(cfg.get("enabled"))
    for key in ("site_url", "stop_share_sel", "creds_enc"):
        cfg[key] = str(cfg.get(key) or "")
    for group in ("login", "share", "media"):
        # 配置文件被人手工改坏（比如 login 写成字符串）时也要能起来
        if not isinstance(cfg.get(group), dict):
            cfg[group] = {}
        for k, v in list(cfg[group].items()):
            cfg[group][k] = "" if v is None else str(v)
    cfg["media"].setdefault("video_sel", "video")
    if not cfg["media"]["video_sel"]:
        cfg["media"]["video_sel"] = "video"
    cfg["steps"] = normalize_steps(cfg.get("steps"))
    if workspace_path:
        cfg["_workspace"] = os.path.abspath(workspace_path)
    return cfg


def load_config(workspace_path):
    """读配置；文件不存在/读坏了都返回默认配置（**绝不抛异常**）。"""
    path = config_path(workspace_path) if workspace_path else ""
    raw = {}
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
    return normalize_config(raw, workspace_path)


def _persistable(cfg):
    """剥掉运行期字段，只留能落盘的部分。"""
    out = {}
    for k, v in (cfg or {}).items():
        if k in _RUNTIME_KEYS or str(k).startswith("_"):
            continue
        out[k] = v
    out = normalize_config(out)
    out.pop("_workspace", None)
    # 双保险：任何形如账号/密码的明文键都不许出现在盘上
    for bad in ("user", "password", "passwd", "pwd", "account", "username"):
        out.pop(bad, None)
    for grp in ("login", "share", "media"):
        if isinstance(out.get(grp), dict):
            for bad in ("user", "password", "passwd", "pwd", "account"):
                out[grp].pop(bad, None)
    return out


def save_config(workspace_path, cfg):
    """原子写配置（tmp + ``os.replace``）。返回 ``(ok, error)``。"""
    if not workspace_path:
        return False, "没有工作区路径"
    path = config_path(workspace_path)
    data = _persistable(cfg)
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 账号密码（DPAPI / 内存兜底）
# ---------------------------------------------------------------------------
def credential_mode():
    """``"dpapi"`` 或 ``"memory"``（拿不到 DPAPI 时只存内存，不落盘）。"""
    return "dpapi" if security.DPAPI_AVAILABLE else "memory"


def set_credentials(cfg, user, password, workspace_path=None):
    """写入账号密码；返回 ``(mode, error)``。

    DPAPI 可用 -> 加密进 ``cfg["creds_enc"]``；
    不可用     -> ``creds_enc`` 置空，只放内存（``_MEM_CREDS``，按工作区）。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    user = "" if user is None else str(user)
    password = "" if password is None else str(password)
    mode = credential_mode()
    cfg["_user"] = user
    cfg["_pass"] = password
    cfg["_creds_mode"] = mode
    if mode == "dpapi":
        try:
            blob = json.dumps({"u": user, "p": password}, ensure_ascii=False)
            sealed = security.seal(blob)
            cfg["creds_enc"] = sealed or ""
            if not sealed:
                return mode, "DPAPI 加密返回空值，已改为只存内存"
        except Exception as e:
            cfg["creds_enc"] = ""
            return mode, f"DPAPI 加密失败：{type(e).__name__}: {e}"
    else:
        cfg["creds_enc"] = ""
    if workspace_path:
        with _MEM_LOCK:
            _MEM_CREDS[os.path.abspath(workspace_path)] = {
                "u": user, "p": password}
    return mode, ""


def get_credentials(cfg, workspace_path=None):
    """取 ``(user, password, source)``。``source`` 是 ``memory``/``dpapi``/``""``。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    user = str(cfg.get("_user") or "")
    password = str(cfg.get("_pass") or "")
    if user or password:
        return user, password, "memory"

    sealed = str(cfg.get("creds_enc") or "")
    if sealed:
        try:
            blob = security.unseal(sealed)
            if blob:
                data = json.loads(blob)
                if isinstance(data, dict):
                    return (str(data.get("u") or ""),
                            str(data.get("p") or ""), "dpapi")
        except Exception:
            pass

    if workspace_path:
        with _MEM_LOCK:
            hit = _MEM_CREDS.get(os.path.abspath(workspace_path))
        if hit:
            return str(hit.get("u") or ""), str(hit.get("p") or ""), "memory"
    return "", "", ""


def clear_credentials(cfg, workspace_path=None):
    """清掉账号密码（内存 + 配置字段）。"""
    if isinstance(cfg, dict):
        cfg["creds_enc"] = ""
        cfg["_user"] = ""
        cfg["_pass"] = ""
    if workspace_path:
        with _MEM_LOCK:
            _MEM_CREDS.pop(os.path.abspath(workspace_path), None)
    return True


def mask(text, keep=3):
    """打码：``zhangsan`` -> ``zha***an``（UI 回显用）。"""
    s = str(text or "")
    if not s:
        return ""
    if len(s) <= keep * 2:
        return s[:1] + "***"
    return s[:keep] + "***" + s[-2:]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _field_value(cfg, path, default=""):
    """按 ``"a.b"`` 或 ``"a"`` 取值（路径可以没有点，别拿标量当 dict 用）。"""
    node = cfg or {}
    for part in str(path).split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part, default)
    return default if node is None else node


def missing_fields(cfg):
    """返回**还没填**的关键字段 ``[(字段路径, 中文名), ...]``。"""
    out = []
    for path, label in REQUIRED_FIELDS:
        val = str(_field_value(cfg, path) or "").strip()
        if not val:
            out.append((path, label))
    return out


def warnings_of(cfg, workspace_path=None):
    """给人看的一组提示（不阻塞保存，只是提醒）。"""
    warns = []
    if credential_mode() == "memory":
        warns.append("当前环境拿不到 Windows DPAPI：账号密码**只存在内存里**，"
                     "关掉程序就要重填（不会以任何形式落盘）。")
    c = cfg or {}
    if not str(c.get("site_url") or "").strip():
        warns.append("还没填站点地址（site_url）。")
    miss = missing_fields(c)
    if miss:
        warns.append("这些选择器还没填：" +
                     "、".join(f"{label}（{path}）" for path, label in miss))
    media = c.get("media") or {}
    if not str(media.get("video_sel") or "").strip():
        warns.append("media.video_sel 为空，播放判定会退回默认的 video 标签。")
    if not str(c.get("stop_share_sel") or "").strip() and \
            not str((c.get("share") or {}).get("started_sel") or "").strip():
        warns.append("既没配结束共享按钮、也没配「共享中」标志："
                     "播完后无法可靠校验共享是否已结束。")
    return warns


def is_ready(cfg):
    """能不能直接跑（只检查硬性必填项，不检查密码）。"""
    return not missing_fields(cfg)


# ---------------------------------------------------------------------------
# 停止信号
# ---------------------------------------------------------------------------
def stop_event_for(workspace_path):
    """取（必要时创建）该工作区的停止事件。"""
    key = os.path.abspath(workspace_path) if workspace_path else ""
    with _STOP_LOCK:
        ev = _STOP_EVENTS.get(key)
        if ev is None:
            ev = threading.Event()
            _STOP_EVENTS[key] = ev
        return ev


def request_stop(workspace_path):
    """请求停止当前正在跑的共享流程。"""
    stop_event_for(workspace_path).set()
    return True


def reset_stop(workspace_path):
    """开始新一轮前清掉停止标志。"""
    stop_event_for(workspace_path).clear()
    return True


# ---------------------------------------------------------------------------
# 执行入口
# ---------------------------------------------------------------------------
def run_share_flow_for_workspace(workspace_path, page, media_hint,
                                 stop_event=None, on_progress=None,
                                 cfg=None, max_hours=3.0):
    """读配置 -> 解密账号 -> 跑 :func:`seewo_steps.run_share_flow`。

    返回结构同 ``run_share_flow``；配置不完整时直接返回失败并说明缺什么。
    """
    c = cfg if cfg is not None else load_config(workspace_path)
    c = normalize_config(c, workspace_path)
    if steps_mod.as_page(page) is None:
        return {"ok": False, "stage": "login",
                "error": "没有可用的内置浏览器页面（请先在内置浏览器里打开希沃）",
                "log": [], "result": {}}

    user, password, source = get_credentials(c, workspace_path)
    c["_user"] = user
    c["_pass"] = password
    c["_media_hint"] = media_hint or ""

    miss = missing_fields(c)
    if miss:
        return {"ok": False, "stage": "login",
                "error": "配置不完整，缺：" +
                         "、".join(f"{label}（{path}）" for path, label in miss),
                "log": [], "result": {}}
    login = c.get("login") or {}
    if str(login.get("user_sel") or "").strip() and not (user and password):
        return {"ok": False, "stage": "login",
                "error": "还没填希沃账号或密码（设置面板里填一次即可）",
                "log": [], "result": {}}

    res = steps_mod.run_share_flow(page, c, media_hint,
                                  stop_event=stop_event,
                                  on_progress=on_progress,
                                  max_hours=max_hours)
    try:
        res["summary"] = steps_mod.summarize_result(res)
        res["credential_source"] = source
    except Exception:
        pass
    return res


def make_scheduler_runner(page_getter, workspace_path):
    """给 ``Scheduler(share_cb=...)`` 用的执行体。

    用法（由 main_window 接线）::

        sched = Scheduler(ws, run_cb=..., share_cb=seewo.make_scheduler_runner(
            lambda: self.browser_page.page(), ws))

    ``page_getter`` 可以是可调用对象，也可以直接给页面对象。
    返回 ``"[成功] …"`` / ``"[错误] …"`` 字符串（与既有 ``run_cb`` 约定一致）。
    """
    ws = os.path.abspath(workspace_path) if workspace_path else None

    def _runner(task, payload=None):
        task = task or {}
        payload = payload or {}
        try:
            cfg = load_config(ws)
        except Exception as e:
            return f"[错误] 读希沃配置失败：{type(e).__name__}: {e}"
        if not cfg.get("enabled") and not payload.get("force"):
            return "[跳过] 希沃共享未启用（设置面板里打开开关）"

        page = page_getter() if callable(page_getter) else page_getter
        if steps_mod.as_page(page) is None:
            return "[错误] 没有可用的内置浏览器页面"

        hint = (payload.get("media_hint") or task.get("media_hint") or
                task.get("prompt") or "")
        reset_stop(ws)
        stop_event = stop_event_for(ws)
        res = run_share_flow_for_workspace(
            ws, page, hint, stop_event=stop_event, cfg=cfg,
            max_hours=float(payload.get("max_hours") or 3.0))
        return steps_mod.summarize_result(res)

    return _runner


def add_scheduler_task(scheduler, name, media_hint, expr="daily@08:30",
                       prompt="", **kwargs):
    """便捷方法：往 Scheduler 里加一条 ``seewo_share`` 任务。

    ``expr`` 用统一表达式语法（``workday@07:50`` 之类），见
    :func:`app.scheduler.parse_schedule`。
    """
    return scheduler.add(
        name=name or "希沃共享屏幕",
        prompt=(prompt or (f"播放{media_hint}" if media_hint else "")),
        kind=kwargs.pop("kind", "daily"),
        expr=expr,
        media_hint=media_hint,
        payload={"media_hint": media_hint},
        **kwargs)


# ---------------------------------------------------------------------------
# 「学一次」
# ---------------------------------------------------------------------------
def learn_once(workspace_path, page, suggest=True):
    """导出当前页面元素候选清单，并（可选）自动填一批选择器。

    返回::

        {"ok", "items", "path", "suggested", "error"}

    ``suggested`` 是 :func:`seewo_steps.suggest_config` 的结果（未落盘，
    交给 UI 展示/确认后再保存 —— 自动猜的选择器不该悄悄覆盖主任调好的）。
    """
    path = probe_path(workspace_path) if workspace_path else None
    probe = steps_mod.probe_page(page, out_path=path)
    out = {"ok": bool(probe.get("ok")), "items": probe.get("items") or [],
           "path": probe.get("path") or path or "",
           "error": probe.get("error") or "", "suggested": None}
    if suggest and out["ok"]:
        cfg = load_config(workspace_path)
        out["suggested"] = steps_mod.suggest_config(probe, cfg)
    return out


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def self_check(workspace_path=None):
    """不依赖页面的配置层自检（加解密往返、原子写、归一化）。"""
    out = []

    def chk(name, cond, detail=""):
        out.append({"name": name, "ok": bool(cond), "detail": str(detail)})

    mode = credential_mode()
    cfg = normalize_config(DEFAULT_CONFIG)
    err = set_credentials(cfg, "主任", "pw-中文-123", workspace_path)
    u, p, src = get_credentials(cfg, workspace_path)
    chk("账号密码加解密往返", (u, p) == ("主任", "pw-中文-123") and not err[1],
        f"mode={mode} src={src} user={mask(u)}")
    chk("落盘前不含明文", "pw-中文-123" not in json.dumps(
        _persistable(cfg), ensure_ascii=False), str(mode))

    if workspace_path:
        ok, e = save_config(workspace_path, cfg)
        chk("配置原子写", ok, e)
        back = load_config(workspace_path)
        u2, p2, src2 = get_credentials(back, workspace_path)
        if mode == "dpapi":
            chk("重读后仍能解出账号密码", (u2, p2) == ("主任", "pw-中文-123"),
                f"src={src2}")
        else:
            chk("无 DPAPI 时重读不带密码（只存内存）", not p2, f"src={src2}")
    return out


def probe_hints(cfg):
    """列出还缺哪些字段，供 UI 直接贴着输入框显示。"""
    miss = missing_fields(cfg)
    return [{"path": p, "label": lbl} for p, lbl in miss]
