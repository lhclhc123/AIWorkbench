# -*- coding: utf-8 -*-
"""工作区与本地持久化：设置、对话历史、文件沙箱、导出。"""
import os
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

DEFAULT_SETTINGS = {
    # —— 密钥（落盘时会被 DPAPI 加密，磁盘上不存在明文）——
    "api_keys": dict(config.DEFAULT_API_KEYS),
    "github_token": "",
    # —— 对话 ——
    "system_prompt": config.DEFAULT_SYSTEM_PROMPT,
    "prompt_version": config.PROMPT_VERSION,
    "selected_model": "auto",
    "model_policy": config.MODEL_FAILURE_POLICY,   # strict | fallback
    "enable_search": False,
    "agent_mode": True,
    "theme": "light",
    "font_scale": 100,                             # 界面缩放（100 = 标准）
    # —— 语音 ——
    "asr_prefer": "auto",
    "mic_device": -1,                              # -1 = 系统默认
    "voice_auto_send": False,                      # 说完自动发送
    "voice_silence": 3.0,                          # 静音多少秒算说完（默认 3s，别太短）
    "voice_max_seconds": 300,                      # 单次录音最长秒数（5 分钟）
    "tts_enabled": False,                          # 自动朗读 AI 回复
    "tts_engine": config.TTS_DEFAULT_ENGINE,
    "tts_voice": "",                               # edge 音色
    "tts_rate": 0,
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
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.settings_file = os.path.join(self.path, "settings.json")
        self.conv_dir = os.path.join(self.path, "conversations")
        self.files_dir = os.path.join(self.path, "files")
        self.exports_dir = os.path.join(self.path, "exports")

    def ensure(self):
        for d in (self.path, self.conv_dir, self.files_dir, self.exports_dir):
            os.makedirs(d, exist_ok=True)
        if not os.path.exists(self.settings_file):
            self.save_settings(DEFAULT_SETTINGS.copy())
        return self

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
