# -*- coding: utf-8 -*-
"""工作区与本地持久化：设置、对话历史、文件沙箱、导出。"""
import os
import re
import json
import time
import shutil
import tempfile

from . import config, security
from . import version as ver

try:
    from . import dingtalk as _dt
except Exception:      # 钉钉模块不可用时也不该影响主程序
    _dt = None

# ★ v9.18.0：机器内部数据的藏身处。用户要求「工作空间里一律只保留产物」，
#   于是把对话记录 / 轨迹 / 日志 / 后台任务 / 团队运行产物 / 导出 / 备份 /
#   自动记忆流水 / assistant.json 全收进 `<工作区>/.awb/`。
#   点开头只是"不碍眼"，不调 API 设隐藏属性位（跨平台也更稳）。
INTERNAL_DIRNAME = ".awb"


def internal_path(workspace_path, name):
    """给「只拿到工作区路径字符串」的模块（trace / worklog / taskman / memory）用。
    返回内部数据的路径：新位置 `.awb/<name>` 有 → 用新的；否则老位置
    `<工作区>/<name>` 有 → 用老的；都没有 → 用新位置。
    """
    ws = os.path.abspath(workspace_path)
    new = os.path.join(ws, INTERNAL_DIRNAME, name)
    old = os.path.join(ws, name)
    if os.path.exists(new):
        return new
    if os.path.exists(old):
        return old
    return new


def internal_dir(workspace_path, name):
    """同 `internal_path`，但保证是一个**目录**（不存在就建新的）。"""
    ws = os.path.abspath(workspace_path)
    new = os.path.join(ws, INTERNAL_DIRNAME, name)
    old = os.path.join(ws, name)
    if os.path.isdir(new):
        return new
    if os.path.isdir(old):
        return old
    try:
        os.makedirs(new, exist_ok=True)
    except Exception:
        pass
    return new


DEFAULT_SETTINGS = {
    # —— 密钥（落盘时会被 DPAPI 加密，磁盘上不存在明文）——
    "api_keys": dict(config.DEFAULT_API_KEYS),
    "github_token": "",
    # —— 并行任务 / 团队（v9.16.0）——
    # 后台链路没有界面弹"确认执行这条命令"的框，所以默认**拒绝**命令类操作；
    # 用户在「⟳ 并行任务」页勾选后才放行（危险命令仍会被工具层单独拦截）。
    "bg_allow_commands": False,
    "bg_max_parallel": 4,          # 同时最多跑几条后台链路（超出的排队）
    # —— 对话 ——
    "system_prompt": config.DEFAULT_SYSTEM_PROMPT,
    "prompt_version": config.PROMPT_VERSION,
    "selected_model": "auto",
    "model_policy": config.MODEL_FAILURE_POLICY,   # strict | fallback
    # v9.8 起：联网与 Agent 模式「默认开启」（用户要求，仿 WorkBuddy），
    # 开关入口统一收进「设置」页，聊天页顶部不再放这一排控件。
    "enable_search": True,
    "agent_mode": True,
    "theme": "light",
    "font_scale": 100,                             # 界面缩放（100 = 标准）
    # —— 语音 ——
    "asr_prefer": "auto",
    "mic_device": -1,                              # -1 = 系统默认
    "voice_auto_send": False,                      # 说完自动发送
    "voice_silence": 10.0,                         # 静音多少秒算说完（默认 10s，用户要求）
    "voice_max_seconds": 300,                      # 单次录音最长秒数（5 分钟）
    "tts_enabled": False,                          # 自动朗读 AI 回复
    "tts_engine": config.TTS_DEFAULT_ENGINE,
    "tts_voice": "",                               # edge 音色
    "tts_rate": 0,
    # —— DeepSeek 网页版（v9.14.2：免手动）——
    "deepseek_web_auto": True,      # 启动/切到该模型时，自动拉起浏览器（免手动开 .bat）
    # True = 后台无头（桌面零窗口，但实测 visibilityState 仍是 visible，发送正常）；
    # False = 显示窗口。⚠ 绝不改回"挪到屏幕外"：那种窗口 visibilityState='hidden'，
    # 浏览器会丢弃发送动作 -> 字填进输入框却发不出去。
    "deepseek_web_silent": True,
    # —— GitHub（v9.15.0）——
    # 留空即可：优先用本机已登录的 gh CLI。只有没装 gh / 要读私有仓库时才需要填 Token。
    "github_token": "",
    # —— 钉钉 ——
    "dingtalk": dict(_dt.DEFAULT_CONFIG) if _dt else {},
    # —— 更新 ——
    "update_repo": ver.GITHUB_REPO,
    "check_update_on_start": True,
    # —— 托盘与全局热键 ——
    "tray_enabled": True,          # 常驻系统托盘
    "tray_on_close": True,         # 点 X 收进托盘而不是退出
    "hotkey_show": "ctrl+alt+space",   # 呼出/收起主窗口
    "hotkey_voice": "ctrl+alt+v",      # 一键开始/结束语音输入
}


class Workspace:
    # 老 files/ 的产物统一迁进这个历史对话文件夹
    HISTORY_FOLDER = "00_历史文件"

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.settings_file = os.path.join(self.path, "settings.json")
        # ★ v9.18.0 用户要求：「工作空间中一律只保留产物」。
        #   所有**机器内部数据**都收进 <工作区>/.awb/，根目录只留
        #   chats/、MEMORY.md、settings.json、mcp.json 和隐藏的 .awb/。
        self.internal_dir = os.path.join(self.path, INTERNAL_DIRNAME)
        # ★ v9.17.0 用户要求：「工作空间内每个对话都新建一个文件夹，
        #   分别存放产物和记忆，还要有主记忆」。
        #   chats/<NN_标题>/files     → 这个对话的产物
        #   chats/<NN_标题>/MEMORY.md → 这个对话的记忆
        #   工作区根的 MEMORY.md       → 主记忆（跨对话共享，一直保留）
        self.chats_dir = os.path.join(self.path, "chats")
        self._index_file = os.path.join(self.chats_dir, "_index.json")
        self.current_cid = None
        self._cur_folder = ""
        # 迁移过程记录（每条"从哪搬到哪"），供界面 / 测试读取
        self.migration_notes = []

    # ---------- ★ v9.18.0 内部数据目录（.awb/）----------
    def internal(self, name: str) -> str:
        """返回 `<工作区>/.awb/<name>`，并确保该目录存在。"""
        d = os.path.join(self.internal_dir, str(name).strip("/\\"))
        os.makedirs(d, exist_ok=True)
        return d

    def _internal_path(self, name: str) -> str:
        """新位置(.awb) 优先，老位置(根目录) 兜底，都没有则给新位置。"""
        return internal_path(self.path, name)

    def _internal_dir(self, name: str) -> str:
        """内部目录：`.awb/` 优先后回退根目录；都没有就建 `.awb/`。"""
        return internal_dir(self.path, name)

    @property
    def conv_dir(self):
        return self._internal_dir("conversations")

    @property
    def exports_dir(self):
        return self._internal_dir("exports")

    @property
    def traces_dir(self):
        return self._internal_dir("traces")

    @property
    def worklog_dir(self):
        return self._internal_dir("worklog")

    @property
    def bgtasks_dir(self):
        return self._internal_dir("bgtasks")

    @property
    def team_dir(self):
        return self._internal_dir("team")

    @property
    def backups_dir(self):
        return self._internal_dir("backups")

    @property
    def assistant_file(self):
        """助手状态文件（原来在根目录 assistant.json）→ `.awb/assistant.json`。"""
        return self._internal_path("assistant.json")

    @property
    def memory_auto_file(self):
        """自动记忆流水（原来在根目录 memory_auto.jsonl）→ `.awb/memory_auto.jsonl`。"""
        return self._internal_path("memory_auto.jsonl")

    @property
    def files_root(self):
        """没有当前对话时的产物兜底目录 —— 指向历史对话文件夹，绝不落工作区根。"""
        return os.path.join(self.chats_dir, self.HISTORY_FOLDER, "files")

    def ensure(self):
        # ★ v9.18.0：先把老布局一次性搬进新布局，再建目录。
        self.migration_notes = []
        self._migrate_layout()
        for d in (self.path, self.internal_dir, self.internal("conversations"),
                  self.internal("exports"), self.chats_dir, self.files_root):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        if not os.path.exists(self.settings_file):
            self.save_settings(DEFAULT_SETTINGS.copy())
        return self

    # ---------- ★ v9.18.0 一次性布局迁移（幂等 / 失败不致命 / 只搬不删）----------
    def _migrate_layout(self):
        """把老工作区根目录下的机器内部数据搬进 `.awb/`。

        规则：
          · conversations/traces/worklog/bgtasks/team/exports → `.awb/同名`
            （老有、新无 → 整目录 move；两边都有 → 只补搬新位置缺的条目，不动旧的）
          · assistant.json / memory_auto.jsonl → `.awb/`
          · MEMORY.md.bak-* → `.awb/backups/`
          · 老 `<ws>/files/` → `chats/00_历史文件/files/`（见 `_migrate_legacy_files`）

        ⚠ 全程 try/except：任何一步失败都保持原样继续，绝不抛异常让程序起不来。
        ⚠ 只 move、只删空目录，绝不删文件（用户真实工作区里有重要数据）。
        """
        notes = self.migration_notes
        try:
            os.makedirs(self.internal_dir, exist_ok=True)
        except Exception as e:                    # noqa: BLE001
            notes.append(f"[跳过] 无法创建 .awb/：{e}")
            return

        # ① 机器内部目录
        for name in ("conversations", "traces", "worklog",
                     "bgtasks", "team", "exports"):
            old = os.path.join(self.path, name)
            new = os.path.join(self.internal_dir, name)
            if not os.path.isdir(old):
                continue
            if not os.path.isdir(new):
                try:
                    shutil.move(old, new)
                    notes.append(f"迁移目录 {name}/ → .awb/{name}/")
                except Exception as e:            # noqa: BLE001
                    notes.append(f"[跳过] 迁移 {name}/ 失败：{e}")
            else:
                self._merge_dir(old, new, name, notes)

        # ② 单文件
        for fn in ("assistant.json", "memory_auto.jsonl"):
            old = os.path.join(self.path, fn)
            new = os.path.join(self.internal_dir, fn)
            if os.path.exists(old) and not os.path.exists(new):
                try:
                    shutil.move(old, new)
                    notes.append(f"迁移文件 {fn} → .awb/{fn}")
                except Exception as e:            # noqa: BLE001
                    notes.append(f"[跳过] 迁移 {fn} 失败：{e}")

        # ③ 记忆备份 MEMORY.md.bak-*
        try:
            for fn in os.listdir(self.path):
                if not fn.startswith("MEMORY.md.bak"):
                    continue
                src = os.path.join(self.path, fn)
                if not os.path.isfile(src):
                    continue
                dst = os.path.join(self.backups_dir, fn)
                if os.path.exists(dst):
                    continue
                try:
                    shutil.move(src, dst)
                    notes.append(f"迁移备份 {fn} → .awb/backups/{fn}")
                except Exception as e:            # noqa: BLE001
                    notes.append(f"[跳过] 迁移 {fn} 失败：{e}")
        except Exception as e:                    # noqa: BLE001
            notes.append(f"[跳过] 扫描备份失败：{e}")

        # ④ 老 files/ → chats/00_历史文件/files/
        self._migrate_legacy_files(notes)

    def _merge_dir(self, old, new, name, notes):
        """两边都有同名目录：只把新位置缺的条目补搬过去，老位置不动、不删。"""
        try:
            entries = os.listdir(old)
        except Exception as e:                    # noqa: BLE001
            notes.append(f"[跳过] 读取 {name}/ 失败：{e}")
            return
        for e in entries:
            src = os.path.join(old, e)
            dst = os.path.join(new, e)
            if os.path.exists(dst):
                continue
            try:
                shutil.move(src, dst)
                notes.append(f"补搬 {name}/{e} → .awb/{name}/{e}")
            except Exception as ex:               # noqa: BLE001
                notes.append(f"[跳过] 补搬 {name}/{e} 失败：{ex}")
        # 老目录若已被搬空，顺手删掉这个**空**目录
        try:
            if not os.listdir(old):
                os.rmdir(old)
                notes.append(f"删除空目录 {name}/")
        except Exception:
            pass

    def _migrate_legacy_files(self, notes):
        """老 `<工作区>/files/` 里的东西 → `chats/00_历史文件/files/`。

        · 跳过 `__pycache__`（Python 缓存，不是产物；挪进 .awb/backups/ 暂存而非删除）
        · 目标同名已存在 → 不覆盖，两边都留
        · 搬完若老目录空了 → 删掉这个**空**目录
        """
        old = os.path.join(self.path, "files")
        if not os.path.isdir(old):
            return
        dest = self.files_root
        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as e:                    # noqa: BLE001
            notes.append(f"[跳过] 创建历史产物目录失败：{e}")
            return
        try:
            entries = os.listdir(old)
        except Exception as e:                    # noqa: BLE001
            notes.append(f"[跳过] 读取 files/ 失败：{e}")
            return
        for e in entries:
            if e == "__pycache__":
                continue                          # 缓存，下面单独处理
            src = os.path.join(old, e)
            dst = os.path.join(dest, e)
            if os.path.exists(dst):
                notes.append(f"[保留两边] files/{e} 已存在于历史产物目录，未覆盖")
                continue
            try:
                shutil.move(src, dst)
                notes.append(f"迁移产物 files/{e} → chats/{self.HISTORY_FOLDER}/files/{e}")
            except Exception as ex:               # noqa: BLE001
                notes.append(f"[跳过] 迁移 files/{e} 失败：{ex}")
        # __pycache__：缓存可再生，但"绝不删文件"——挪进 .awb/backups/ 暂存
        pyc = os.path.join(old, "__pycache__")
        if os.path.isdir(pyc):
            try:
                stash = os.path.join(self.backups_dir, "legacy_files__pycache")
                if os.path.exists(stash):
                    stash += "_" + time.strftime("%Y%m%d_%H%M%S")
                shutil.move(pyc, stash)
                notes.append("移走 files/__pycache__（缓存，不放产物目录）→ .awb/backups/")
            except Exception as ex:               # noqa: BLE001
                notes.append(f"[跳过] 移走 files/__pycache__ 失败：{ex}")
        # 只在老目录真的空了才删
        try:
            if not os.listdir(old):
                os.rmdir(old)
                notes.append("删除空的 files/（产物已全部迁入历史文件夹）")
        except Exception:
            pass


    # ---------- ★ v9.17.0 每个对话一个文件夹 ----------
    @property
    def files_dir(self):
        """当前对话的产物目录（**永远**落在 chats/ 里，绝不回落到工作区根）。

        有当前对话 → `<ws>/chats/<NN_标题>/files`；
        还没选定对话 → `<ws>/chats/00_历史文件/files`（历史产物兜底目录）。
        """
        if self._cur_folder:
            d = os.path.join(self._cur_folder, "files")
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                return self.files_root
            return d
        d = self.files_root
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    @staticmethod
    def _safe_name(text, limit=14):
        s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "")).strip(" ._")
        return (s[:limit] or "对话")

    def _load_index(self):
        try:
            with open(self._index_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_index(self, idx):
        try:
            os.makedirs(self.chats_dir, exist_ok=True)
            with open(self._index_file, "w", encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _title_of(self, cid):
        try:
            conv = self.load_conversation(cid)
            return (conv or {}).get("title") or ""
        except Exception:
            return ""

    def _next_seq(self):
        """给新对话分配一个没被占用的文件夹序号。

        chats/ 下可能有**不在 _index.json 里**的目录（例如 `00_历史文件`），
        所以不能再用 `len(idx)+1` —— 必须跳过所有已存在的目录名，避免撞名
        （撞名会导致两个对话共用同一个文件夹、产物互相覆盖）。
        """
        used = set()
        try:
            for fn in os.listdir(self.chats_dir):
                m = re.match(r"^(\d+)_", fn)
                if m:
                    used.add(int(m.group(1)))
        except Exception:
            pass
        try:
            for v in self._load_index().values():
                m = re.match(r"^(\d+)_", str(v or ""))
                if m:
                    used.add(int(m.group(1)))
        except Exception:
            pass
        n = 1
        while n in used:
            n += 1
        return n

    def conv_folder(self, cid, title=None, create=True):
        """这个对话的专属目录（`chats/<NN_标题>`）的绝对路径。

        `chats/_index.json` 记着 对话id -> 目录名 的映射：序号一旦分配就不再变
        （避免"改了标题就换目录、产物全失联"），标题变了只改后半段。
        """
        cid = str(cid or "").strip()
        if not cid:
            return ""
        idx = self._load_index()
        old = idx.get(cid)
        if not old and not create:
            return ""
        title = title or self._title_of(cid) or "新对话"
        if not old:
            name = f"{self._next_seq():02d}_{self._safe_name(title)}"
        else:
            m = re.match(r"^(\d+)_", old)
            n = int(m.group(1)) if m else self._next_seq()
            name = f"{n:02d}_{self._safe_name(title)}"
        dst = os.path.join(self.chats_dir, name)
        if create:
            try:
                if old and old != name:
                    src = os.path.join(self.chats_dir, old)
                    if os.path.isdir(src) and not os.path.isdir(dst):
                        # 标题变了：把整个文件夹（含里面的产物和记忆）一起改名
                        os.rename(src, dst)
                os.makedirs(os.path.join(dst, "files"), exist_ok=True)
            except Exception:
                pass
            if idx.get(cid) != name:
                idx[cid] = name
                self._save_index(idx)
        return dst

    def set_current(self, cid):
        """切换"当前对话"——决定之后产物和对话记忆落到哪个文件夹。"""
        cid = str(cid or "").strip()
        self.current_cid = cid or None
        self._cur_folder = self.conv_folder(cid, create=True) if cid else ""
        return self.current_cid

    def current_folder(self):
        return self._cur_folder or ""

    def conv_memory_file(self, cid=None):
        """这个对话的记忆文件（`chats/<NN_标题>/MEMORY.md`）。"""
        cid = str(cid or self.current_cid or "").strip()
        if not cid:
            return ""
        p = self.conv_folder(cid, create=True)
        return os.path.join(p, "MEMORY.md") if p else ""

    # ---------- 设置 ----------
    @staticmethod
    def _protect(data: dict) -> dict:
        """落盘前把敏感字段加密（api_keys / github_token）。"""
        out = {k: v for k, v in (data or {}).items()
               if k not in ("api_keys", "github_token")}
        out["api_keys_enc"] = security.seal_dict(data.get("api_keys") or {})
        tok = data.get("github_token")
        if tok:
            out["github_token_enc"] = security.seal(tok)
        out["_crypto"] = "dpapi" if security.DPAPI_AVAILABLE else "obf"
        return out

    @staticmethod
    def _unprotect(data: dict) -> dict:
        """读盘后把敏感字段解出来；同时兼容老版本的明文写法。"""
        data = dict(data or {})
        if "api_keys_enc" in data:
            data["api_keys"] = security.unseal_dict(data.pop("api_keys_enc"))
        elif "api_keys" in data:
            # 老工作区是明文存的 —— 读进来，下次保存就会自动加密
            data["api_keys"] = {k: (security.unseal(v) if security.is_sealed(v) else v)
                                for k, v in (data.get("api_keys") or {}).items()}
        else:
            data["api_keys"] = {}
        if "github_token_enc" in data:
            data["github_token"] = security.unseal(data.pop("github_token_enc"))
        # 缺失的种子密钥补齐（空值也要补，方便首次使用就可用）
        for k, v in config.DEFAULT_API_KEYS.items():
            data["api_keys"].setdefault(k, v)
        return data

    def load_settings(self):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return dict(DEFAULT_SETTINGS)
        data = self._unprotect(raw)
        for k, v in DEFAULT_SETTINGS.items():
            data.setdefault(k, v)
        if not isinstance(data.get("api_keys"), dict):
            data["api_keys"] = dict(config.DEFAULT_API_KEYS)
        # 提示词升级：版本不一致时，用最新提示词覆盖并回写，
        # 这样老工作区也能自动拿到改进后的 Agent 提示词。
        dirty = "api_keys" in raw or "github_token" in raw   # 老格式 -> 需要立刻加密回写
        if data.get("prompt_version") != config.PROMPT_VERSION:
            data["system_prompt"] = config.DEFAULT_SYSTEM_PROMPT
            data["prompt_version"] = config.PROMPT_VERSION
            dirty = True
        if dirty:
            try:
                self.save_settings(data)
            except Exception:
                pass
        return data

    def save_settings(self, settings: dict):
        payload = self._protect(settings)
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # ---------- 对话 ----------
    def _conv_path(self, cid):
        return os.path.join(self.conv_dir, cid + ".json")

    def list_conversations(self):
        out = []
        if not os.path.isdir(self.conv_dir):
            return out
        for fn in os.listdir(self.conv_dir):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(self.conv_dir, fn), "r", encoding="utf-8") as f:
                        d = json.load(f)
                    out.append({
                        "id": d.get("id", fn[:-5]),
                        "title": d.get("title", "未命名对话"),
                        "updated": d.get("updated", 0),
                    })
                except Exception:
                    continue
        out.sort(key=lambda x: x["updated"], reverse=True)
        return out

    def load_conversation(self, cid):
        p = self._conv_path(cid)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_conversation(self, conv: dict):
        conv["updated"] = time.time()
        if not conv.get("title") and conv.get("messages"):
            first = ""
            for m in conv["messages"]:
                if m["role"] == "user":
                    first = m["content"]
                    break
            conv["title"] = (first[:20] + "…") if len(first) > 20 else first or "未命名对话"
        with open(self._conv_path(conv["id"]), "w", encoding="utf-8") as f:
            json.dump(conv, f, ensure_ascii=False, indent=2)
        # ★ v9.17.0：登记 / 按标题更新这个对话的文件夹。
        #   新建对话时标题还叫「新对话」，第一轮发完消息后标题才定下来 ——
        #   这里顺手把文件夹改名（连同里面已有的产物和 MEMORY.md 一起搬）。
        try:
            self.conv_folder(conv.get("id"), conv.get("title"), create=True)
            if self.current_cid and str(self.current_cid) == str(conv.get("id")):
                self.set_current(conv.get("id"))
        except Exception:
            pass

    def delete_conversation(self, cid):
        p = self._conv_path(cid)
        if os.path.exists(p):
            os.remove(p)

    def new_conversation(self):
        cid = "c" + str(int(time.time() * 1000))
        conv = {"id": cid, "title": "新对话", "created": time.time(),
                "updated": time.time(), "messages": []}
        self.save_conversation(conv)
        return conv

    # ---------- 导出 ----------
    def export_conversation(self, conv, fmt="md"):
        os.makedirs(self.exports_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(conv.get("updated", time.time())))
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in conv.get("title", "对话"))[:30]
        fname = f"{safe_title}_{ts}.md"
        fpath = os.path.join(self.exports_dir, fname)
        lines = [f"# {conv.get('title', '对话')}", "", f"> 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
        for m in conv.get("messages", []):
            role = "用户" if m["role"] == "user" else ("AI" if m["role"] == "assistant" else "工具")
            lines.append(f"## {role}")
            lines.append("")
            lines.append(m["content"])
            lines.append("")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return fpath


# ---------- 全局启动配置（记住上次工作区）----------
def _launcher_file():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(appdata, "AIWorkbench")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "launcher.json")


def _is_temp_path(path):
    """系统临时目录里的路径绝不能被当成工作区：一清理就没了。"""
    if not path:
        return True
    p = os.path.abspath(path).lower().rstrip("\\/")
    tmp = os.path.abspath(tempfile.gettempdir()).lower().rstrip("\\/")
    if p == tmp or p.startswith(tmp + "\\") or p.startswith(tmp + "/"):
        return True
    return ("\\temp\\" in p) or p.endswith("\\temp") or ("/tmp/" in p)


def get_last_workspace():
    """只在记住的是一个真实、持久存在的目录时才返回它。"""
    try:
        with open(_launcher_file(), "r", encoding="utf-8") as f:
            p = json.load(f).get("last_workspace")
    except Exception:
        return None
    if not p or _is_temp_path(p) or not os.path.isdir(p):
        return None
    return p


def set_last_workspace(path):
    """绝不把临时目录记成工作区（避免测试/异常把用户引导到 TMP）。"""
    if not path or _is_temp_path(path):
        return
    try:
        with open(_launcher_file(), "w", encoding="utf-8") as f:
            json.dump({"last_workspace": os.path.abspath(path)}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def default_workspace_suggestion():
    """默认建议放在「文档\\AI工作台」，而不是任何临时目录。"""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(docs):
        docs = os.path.expanduser("~")
    return os.path.join(docs, "AI工作台")
