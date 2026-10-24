# -*- coding: utf-8 -*-
"""v9.20.0 单实例保护：保证「AI 工作台」主窗口只会开一个。

为什么用 QLocalServer，而不是锁文件 / QSharedMemory：
  * 锁文件在进程被强杀后会留下「僵尸锁」，下次启动就再也起不来；
  * QSharedMemory 在 Windows 上是「最后一个进程退出才释放」，进程崩了同样会残留；
  * QLocalServer 在 Windows 上就是一个命名管道：进程一死，系统立刻回收名字，
    而且天生支持「第二个进程给第一个进程捎一句话」这种进程间通知 ——
    正好用来实现「用户又点了一次图标 -> 把已有窗口叫到前台」。

命名带上用户维度（AIWorkbench-<用户名>），多用户共用一个会话时不会互相打架。
"""
import os
import sys
import time

from PyQt6.QtCore import QCoreApplication, QEventLoop, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

BASE_NAME = "AIWorkbench"
# 协议极简：一行 ping 表示「又来了一次」。只连不发的连接是「探活」，不计数。
_PING = b"ping\n"
_MAX_NAME = 200
_BAD_CHARS = ("\\", "/", ":")


def current_user():
    """当前 Windows 用户名（拿不到就退回 USER / user）。"""
    return (os.environ.get("USERNAME") or os.environ.get("USER") or "user").strip() or "user"


def server_name(base=BASE_NAME):
    """管道名的最终形态：带用户维度。"""
    return "%s-%s" % (base, current_user())


def _warn(msg):
    """降级只留一行痕迹。打包版是 --windowed，stderr 可能是 None。"""
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
    except Exception:
        pass


class InstanceGuard(QObject):
    """单实例守卫。

    用法：
        g = InstanceGuard()          # 名字默认 AIWorkbench-<用户名>
        if g.try_acquire():          # True = 我是老大
            ...
        else:                        # 已经有实例在跑
            g.notify_existing()      # 告诉它「用户又点了一次」
    """

    #: 收到「又一次启动」的通知（主实例侧连接它去激活窗口）
    pinged = pyqtSignal()

    def __init__(self, name=None, parent=None):
        super().__init__(parent)
        raw = BASE_NAME if name is None else name
        try:
            raw = str(raw).strip()
        except Exception:
            raw = ""
        self._name = server_name(raw) if raw else ""
        self._server = None
        self._owns = False          # 本对象是否持有管道
        self._degraded = False      # 保护不可用（允许启动，但可能开多个）
        self._pending = 0           # 还没被 take_pending() 取走的「又点了一次」次数

    # ------------------------------------------------------------ 只读状态
    @property
    def name(self):
        return self._name

    @property
    def is_primary(self):
        return self._owns

    @property
    def degraded(self):
        """True = 单实例保护没起来（调用方应当照常启动，别拦着用户）。"""
        return self._degraded

    @property
    def pending_count(self):
        return self._pending

    # ------------------------------------------------------------ 抢实例
    def _name_error(self):
        n = self._name
        if not n:
            return "名字为空"
        if len(n) > _MAX_NAME:
            return "名字过长(%d)" % len(n)
        for ch in _BAD_CHARS:
            if ch in n:
                return "名字含非法字符 %r" % ch
        return ""

    def _alive(self):
        """现有实例还在吗？能连上就算在（只连不发，不计入「又点了一次」）。"""
        sock = QLocalSocket(self)
        try:
            sock.connectToServer(self._name)
            if sock.waitForConnected(300):
                try:
                    sock.disconnectFromServer()
                except Exception:
                    pass
                return True
        except Exception:
            pass
        finally:
            try:
                sock.abort()
            except Exception:
                pass
            try:
                sock.deleteLater()
            except Exception:
                pass
        return False

    def try_acquire(self):
        """抢占主实例身份。True=可以继续建窗口；False=已有实例在跑。"""
        if self._owns:
            return True
        err = self._name_error()
        if err:
            self._degraded = True
            _warn("[单实例] 管道名不可用（%s），已跳过单实例保护。" % err)
            return False
        if self._alive():
            return False

        srv = QLocalServer(self)
        try:
            # 只允许当前用户连（多用户机器上别人连不上，也就不会误激活）
            srv.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        except Exception:
            pass

        ok = False
        try:
            ok = bool(srv.listen(self._name))
        except Exception:
            ok = False
        if not ok:
            # 上次进程被强杀，管道名可能还挂着 —— 清一次再试。
            try:
                QLocalServer.removeServer(self._name)
            except Exception:
                pass
            try:
                ok = bool(srv.listen(self._name))
            except Exception:
                ok = False
        if not ok:
            # 起不来就降级：宁可允许开两个窗口，也不能让程序起不来。
            self._degraded = True
            try:
                srv.deleteLater()
            except Exception:
                pass
            _warn("[单实例] 命名管道 %s 监听失败，已降级（允许重复启动）。" % self._name)
            return True

        self._server = srv
        self._owns = True
        try:
            srv.newConnection.connect(self._on_new_connection)
        except Exception:
            pass
        return True

    # ------------------------------------------------------------ 第二个进程用
    def notify_existing(self):
        """通知已有实例「用户又点了一次」。True=送达到了。"""
        if self._owns or self._degraded or not self._name:
            return False
        sock = QLocalSocket(self)
        try:
            sock.connectToServer(self._name)
            if not sock.waitForConnected(500):
                return False
            sock.write(_PING)
            sock.flush()
            # Windows 命名管道上 waitForBytesWritten() 靠不住，而「写完就 abort」
            # 会把这条消息直接丢掉 —— 这里抽一小段事件循环，等写缓冲真的吐完。
            deadline = time.monotonic() + 1.0
            while sock.bytesToWrite() > 0 and time.monotonic() < deadline:
                QCoreApplication.processEvents(
                    QEventLoop.ProcessEventsFlag.AllEvents, 20)
                time.sleep(0.005)
            sock.flush()
            try:
                sock.disconnectFromServer()
                sock.waitForDisconnected(200)
            except Exception:
                pass
            return True
        except Exception:
            return False
        finally:
            try:
                sock.deleteLater()
            except Exception:
                pass

    # ------------------------------------------------------------ 主实例用
    def take_pending(self):
        """取走「又点了一次」的记录（取完清零）。没有就返回空串。

        窗口还没建好时收到通知会先攒在这里，等窗口 ready 后再补一次 —— 不会丢。
        """
        n = self._pending
        self._pending = 0
        if n <= 0:
            return ""
        if n == 1:
            return "又启动了一次"
        return "又启动了一次（共 %d 次）" % n

    def release(self):
        """放开管道名（正常退出/自检结束时用；进程被杀时系统会自动回收）。"""
        self._owns = False
        self._pending = 0
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass
            try:
                srv.deleteLater()
            except Exception:
                pass
        try:
            QLocalServer.removeServer(self._name)
        except Exception:
            pass

    close = release

    # ------------------------------------------------------------ 内部：收通知
    def _on_new_connection(self):
        srv = self._server
        if srv is None:
            return
        while True:
            try:
                if not srv.hasPendingConnections():
                    break
                sock = srv.nextPendingConnection()
            except Exception:
                break
            if sock is None:
                break
            try:
                sock.setProperty("awb_seen", False)
                sock.readyRead.connect(lambda s=sock: self._read_socket(s))
                sock.disconnected.connect(lambda s=sock: self._read_socket(s, True))
                sock.errorOccurred.connect(lambda _e, s=sock: self._read_socket(s, True))
            except Exception:
                pass

    def _read_socket(self, sock, final=False):
        if sock is None:
            return
        try:
            if bool(sock.property("awb_seen")):
                return
        except Exception:
            pass
        try:
            data = bytes(sock.readAll())
        except Exception:
            data = b""
        if data:
            try:
                sock.setProperty("awb_seen", True)
            except Exception:
                pass
            self._consume(data)
            return
        if final:
            try:
                sock.deleteLater()
            except Exception:
                pass

    def _consume(self, data):
        try:
            text = data.decode("utf-8", "ignore")
        except Exception:
            return
        for line in text.splitlines():
            w = line.strip().lower()
            if not w or w.startswith("probe"):
                continue            # 探活连接不算「又点了一次」
            self._pending += 1
            try:
                self.pinged.emit()
            except Exception:
                pass


def guard(name=None):
    """一行式入口：`ok, g = guard()`。

    ok=True  → 本进程是主实例（若 g.degraded 为真，表示保护不可用，但仍应继续启动）；
    ok=False → 已有实例在跑，调用方应 `g.notify_existing()` 后安静退出。
    """
    g = InstanceGuard(name)
    ok = g.try_acquire()
    return ok, g
