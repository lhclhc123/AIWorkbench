# -*- coding: utf-8 -*-
"""最小 MCP（Model Context Protocol）客户端：stdio 传输 + JSON-RPC。

目标：让工作台能接上任意现成的 MCP 服务器（GitHub / 文件 / 数据库 / 自建），
不必为每个系统写死对接代码。

配置文件（任一存在即读取，后读的覆盖同名的）：
  <工作区>/mcp.json
  ~/.aiworkbench/mcp.json
格式与 Claude / WorkBuddy 一致：
{
  "mcpServers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\"]},
    "myserver":   {"command": "python", "args": ["server.py"], "env": {"K": "V"}}
  }
}
"""
import os
import json
import time
import queue
import threading
import subprocess

from . import winproc

INIT_TIMEOUT = 20
CALL_TIMEOUT = 90
PROTOCOL_VERSION = "2024-11-05"


def _config_paths(workspace_path):
    paths = []
    if workspace_path:
        paths.append(os.path.join(workspace_path, "mcp.json"))
    paths.append(os.path.join(os.path.expanduser("~"), ".aiworkbench", "mcp.json"))
    return paths


def load_config(workspace_path=None):
    """读取 MCP 服务器配置，返回 {name: cfg}。配置坏了不影响程序运行。"""
    servers = {}
    for p in _config_paths(workspace_path):
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            block = data.get("mcpServers") or data.get("servers") or {}
            for name, cfg in (block or {}).items():
                if isinstance(cfg, dict) and cfg.get("command"):
                    servers[name] = cfg
        except Exception:
            continue
    return servers


class MCPServer:
    """一个 MCP 子进程 + 它的工具表。所有异常都被吞掉，只返回错误字符串。"""

    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg or {}
        self.proc = None
        self.tools = []
        self._q = queue.Queue()
        self._id = 0
        self._alive = False
        self._lock = threading.Lock()
        self.error = ""

    # ---------- 生命周期 ----------
    def start(self):
        if self._alive:
            return True
        cmd = self.cfg.get("command")
        if not cmd:
            self.error = "缺少 command"
            return False
        args = self.cfg.get("args") or []
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (self.cfg.get("env") or {}).items()})
        cwd = self.cfg.get("cwd") or None
        try:
            self.proc = winproc.popen(
                [cmd] + [str(a) for a in args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, cwd=cwd,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, shell=False,
            )
        except Exception as e:
            self.error = f"启动失败：{e}"
            return False
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._err_reader, daemon=True).start()
        try:
            self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "AIWorkbench", "version": "8.0"},
            }, timeout=INIT_TIMEOUT)
            self._notify("notifications/initialized", {})
            self._alive = True
        except Exception as e:
            self.error = f"初始化失败：{e}"
            self.close()
            return False
        self.refresh_tools()
        return True

    def _reader(self):
        try:
            for line in self.proc.stdout:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            self._q.put(None)   # 结束哨兵

    def _err_reader(self):
        try:
            for _ in self.proc.stderr:
                pass
        except Exception:
            pass

    def close(self):
        self._alive = False
        try:
            if self.proc:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except Exception:
                    self.proc.kill()
        except Exception:
            pass
        self.proc = None

    # ---------- JSON-RPC ----------
    def _write(self, obj):
        if not self.proc or self.proc.poll() is not None:
            raise RuntimeError("MCP 进程已退出")
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _request(self, method, params, timeout=CALL_TIMEOUT):
        self._id += 1
        mid = self._id
        self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise TimeoutError(f"{method} 超时")
            try:
                msg = self._q.get(timeout=remain)
            except queue.Empty:
                raise TimeoutError(f"{method} 超时")
            if msg is None:
                raise RuntimeError("MCP 进程已关闭")
            if msg.get("id") == mid:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(err.get("message") if isinstance(err, dict)
                                       else str(err))
                return msg.get("result") or {}
            # 不是我们要的响应（例如服务端主动通知）-> 丢弃继续等

    def _notify(self, method, params):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ---------- 工具 ----------
    def refresh_tools(self):
        try:
            res = self._request("tools/list", {}, timeout=INIT_TIMEOUT)
            self.tools = res.get("tools") or []
        except Exception as e:
            self.error = str(e)
            self.tools = []
        return self.tools

    def call_tool(self, tool, arguments):
        res = self._request("tools/call",
                            {"name": tool, "arguments": arguments or {}},
                            timeout=CALL_TIMEOUT)
        parts = []
        for c in (res.get("content") or []):
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text") or "")
                elif c.get("type") == "resource":
                    parts.append(str(c.get("resource") or ""))
                else:
                    parts.append(json.dumps(c, ensure_ascii=False)[:2000])
        text = "\n".join(p for p in parts if p)
        if res.get("isError"):
            return "[错误] " + (text or "MCP 工具返回错误")
        return text or "（MCP 工具无文本输出）"


class MCPManager:
    """管理所有 MCP 服务器；未配置时所有方法安全返回空。"""

    def __init__(self, workspace_path=None):
        self.workspace_path = workspace_path
        self._servers = {}
        self._lock = threading.Lock()
        self.last_error = ""

    def configured(self):
        return load_config(self.workspace_path)

    def reload(self):
        with self._lock:
            for s in self._servers.values():
                s.close()
            self._servers = {}

    def server(self, name):
        cfg = self.configured().get(name)
        if not cfg:
            return None
        with self._lock:
            if name not in self._servers:
                s = MCPServer(name, cfg)
                if not s.start():
                    self.last_error = f"{name}: {s.error}"
                self._servers[name] = s
            return self._servers[name]

    def list_all_tools(self, timeout_each=INIT_TIMEOUT):
        """返回 [{'server','name','description','schema'}]。"""
        out = []
        for name in self.configured():
            s = self.server(name)
            if not s or not s._alive:
                continue
            for t in (s.tools or []):
                out.append({
                    "server": name,
                    "name": t.get("name", ""),
                    "description": t.get("description") or "",
                    "schema": t.get("inputSchema") or {},
                })
        return out

    def call(self, server, tool, arguments):
        s = self.server(server)
        if not s:
            return f"[错误] 未找到 MCP 服务器：{server}"
        if not s._alive and not s.start():
            return f"[错误] MCP 服务器 {server} 启动失败：{s.error}"
        try:
            return s.call_tool(tool, arguments)
        except Exception as e:
            return f"[错误] MCP 调用失败（{server}/{tool}）：{e}"

    def close_all(self):
        with self._lock:
            for s in self._servers.values():
                try:
                    s.close()
                except Exception:
                    pass
            self._servers = {}
