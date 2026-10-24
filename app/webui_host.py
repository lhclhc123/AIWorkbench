# -*- coding: utf-8 -*-
"""Web UI 宿主（v9.21.0）：注册 ``awb://`` scheme + 提供静态资源 + ``create_view()`` 工厂。

职责（见 ``docs/ui/01-v921-web-ui-plan.md`` §3 / §4.2）：

1. ``register_scheme()`` —— 在 ``QApplication`` 创建**之前**把 ``awb://`` 注册成安全 scheme，
   让本地页面拥有正常 origin（ES module、``fetch``、``localStorage`` 都可用），
   而不是用 ``file://``（origin 为 null，模块会被 CORS 拦掉）。
2. ``AWBUrlSchemeHandler`` —— 把 ``awb://ui/<path>`` 映射到 ``app/webui/<path>``，
   按扩展名回 MIME；``/js/vendor/qwebchannel.js`` 特殊路由直接从 Qt 资源
   ``:/qtwebchannel/qwebchannel.js`` 读出（不引 CDN、不手工维护第三方文件）。
3. ``create_view()`` —— 返回一个配好 scheme handler、加载 ``awb://ui/index.html`` 的
   ``QWebEngineView``，主窗口拿去 ``addWidget`` 即可。

本模块只负责「资源怎么吐」，不碰任何业务逻辑，不写前端，不改 ``app/gui/``。
"""
import mimetypes
import os
import sys

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QUrl
from PyQt6.QtWebEngineCore import (
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)
# ★ QtWebEngineWidgets 必须在 QApplication 创建**之前** import，
#   否则报 "must be imported ... before a QCoreApplication instance is created"。
#   这里放模块顶部，任何 import 本模块的调用方（前提是在 QApplication 前 import）都安全。
from PyQt6.QtWebEngineWidgets import QWebEngineView

# ★ 必须先 import PyQt6.QtWebChannel，Qt 才会把 :/qtwebchannel/qwebchannel.js
#   注册进资源系统；否则 QFile 读不到它（实测 16,510 字节）。
try:
    import PyQt6.QtWebChannel  # noqa: F401
except Exception:
    pass

SCHEME_NAME = b"awb"
_UI_DIR = "webui"
_INDEX = "index.html"
# 特殊路由：qwebchannel.js 由 Qt 资源提供，其余走 app/webui/
_QWEBCHANNEL_ROUTE = "js/vendor/qwebchannel.js"

# 常用 MIME（其余交给 mimetypes 猜）
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def register_scheme():
    """注册 ``awb://`` scheme（必须在 ``QApplication`` 创建之前调用）。"""
    scheme = QWebEngineUrlScheme(SCHEME_NAME)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme |
                    QWebEngineUrlScheme.Flag.LocalAccessAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)


def _resource_dir():
    """返回 webui 静态资源根目录，兼容开发态与 PyInstaller 打包态。

    开发态：``app/webui/``（相对当前文件）。
    打包态：PyInstaller ``--add-data app/webui`` 会把资源收进 ``sys._MEIPASS``，
    此处用 ``sys._MEIPASS/app/webui`` 兜底。
    """
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, "app", _UI_DIR)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _UI_DIR)


def _mime_for(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in _MIME:
        return _MIME[ext]
    guessed = mimetypes.guess_type(path)[0]
    return guessed or "application/octet-stream"


def _resolve(path):
    """把请求里的相对路径解析成磁盘绝对路径；非法路径（目录穿越）返回 None。"""
    p = (path or "").lstrip("/")
    if not p:
        p = _INDEX
    base = os.path.abspath(_resource_dir())
    full = os.path.abspath(os.path.join(base, p))
    if full != base and not full.startswith(base + os.sep):
        return None
    return full


def _read_qwebchannel_js():
    """从 Qt 资源读出 qwebchannel.js；读不到返回空 bytes。"""
    from PyQt6.QtCore import QFile
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        try:
            return bytes(f.readAll())
        finally:
            f.close()
    return b""


def _read_static(path):
    """读静态文件字节；不存在返回 None。"""
    full = _resolve(path)
    if not full or not os.path.isfile(full):
        return None
    with open(full, "rb") as fh:
        return fh.read()


def _reply(job, content_type, data):
    """把 bytes 作为响应吐给请求 job。"""
    buf = QBuffer(job)                      # parent 到 job，避免响应未读完就被 GC
    buf.setData(QByteArray(data))
    if not buf.open(QIODevice.OpenModeFlag.ReadOnly):
        job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
        return
    job.reply(QByteArray(content_type.encode("ascii")), buf)


class AWBUrlSchemeHandler(QWebEngineUrlSchemeHandler):
    """把 ``awb://ui/<path>`` 映射到 ``app/webui/<path>``，含 404 与 qwebchannel 特殊路由。"""

    def requestStarted(self, job):          # noqa: N802 （Qt 回调命名）
        try:
            url = job.requestUrl()
            path = url.path() or "/"
            rel = path.lstrip("/") or _INDEX

            # 特殊路由：qwebchannel.js 走 Qt 资源，不读磁盘
            if rel == _QWEBCHANNEL_ROUTE:
                data = _read_qwebchannel_js()
                if data:
                    _reply(job, "application/javascript; charset=utf-8", data)
                else:
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return

            data = _read_static(rel)
            if data is None:
                self._reply_404(job, rel)
                return
            _reply(job, _mime_for(rel), data)
        except Exception:
            # 任何异常都不能把渲染进程带崩，退成 404
            try:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            except Exception:
                pass

    @staticmethod
    def _reply_404(job, rel):
        body = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>404</title></head><body style='font-family:sans-serif'>"
            "<h1>404</h1><p>资源不存在：<code>%s</code></p></body></html>"
            % rel
        ).encode("utf-8")
        _reply(job, "text/html; charset=utf-8", body)


# 全局单例 handler：必须常驻，否则被 GC 后请求会静默失败
_HANDLER = None


def _ensure_handler(profile):
    global _HANDLER
    if _HANDLER is None:
        _HANDLER = AWBUrlSchemeHandler()
    profile.installUrlSchemeHandler(QByteArray(SCHEME_NAME), _HANDLER)
    return _HANDLER


def create_view():
    """返回一个配好 scheme handler、加载 ``awb://ui/index.html`` 的 ``QWebEngineView``。"""
    view = QWebEngineView()
    _ensure_handler(view.page().profile())
    view.setUrl(QUrl("awb://ui/index.html"))
    return view
