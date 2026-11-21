# -*- coding: utf-8 -*-
"""通知弹窗：置顶投屏 + 必须点「我已知悉」才能关（v9.22.12）。

设计取向来自用户场景（学校中层 / 主任）
----------------------------------------
家长请假、上级临时通知、调课/检查安排 —— **漏一条就出事**。所以：

* 窗口无标题栏（没有 X）、`FramelessWindowHint + WindowStaysOnTopHint + Tool`；
* `closeEvent` 一律 `event.ignore()`，`Esc` 也忽略 —— 只有点「我已知悉」才消失；
* 位置在屏幕可用区右下角，多条**向上堆叠**，最多同时 3 个，余下排队；
* 「稍后再看」把窗口缩成一个**置顶角标**（显示还有几条待确认，点击重新展开），
  记录仍留在未确认清单里 —— 缩起来不等于消掉；
* 置顶有节制：`show()` 但**不** `activateWindow()`，不抢焦点打断用户打字。

本文件是唯一 import PyQt6 的通知相关文件；纯逻辑（识别 / 落盘）在
`app/im/notify_center.py`，可以脱离 Qt 被后台进程 import。

接线方式（主窗口一行）
----------------------
    from app.gui import notify_popup
    notify_popup.attach(self)        # 内部完成 set_sink + 单例 + 落盘路径

之后后台线程只要 `notify_center.deliver(rec)`，信号会把记录排队送到主线程。
"""
import time
import weakref

from PyQt6.QtCore import (
    QObject, QPropertyAnimation, QRect, QThread, QTimer, Qt, pyqtSignal,
)
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..im import notify_center as ncs

# ---- 外观常量 -------------------------------------------------------------
CARD_W = 380                 # 卡片宽度（像素）
WINDOW_PAD = 10              # 给圆角/边框留的透明边距
MARGIN = 16                  # 距屏幕边缘
GAP = 10                     # 卡片间距
MAX_VISIBLE = 3              # 同时最多显示几个
BREATH_MS = 2000             # 边框呼吸动画时长（用户要的 2 秒）

KIND_COLOR = {
    "leave": "#f0a63c",
    "meeting": "#4f9cf9",
    "inspect": "#f2635c",
    "adjust": "#a17bf5",
    "task": "#3fbf84",
    "notice": "#8b98ab",
}


def _kind_label(kind):
    return ncs.KIND_NAMES.get(str(kind or ""), "通知")


def _kind_color(kind):
    return KIND_COLOR.get(str(kind or ""), KIND_COLOR["notice"])


def _fmt_ts(ts):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts or 0)))
    except Exception:
        return ""


def _who_line(rec):
    """「发送人 · 会话名」；重复或缺失时自动省略。"""
    sender = str(rec.get("sender") or "").strip()
    conv = str(rec.get("conv_title") or "").strip()
    if sender and conv and sender != conv:
        return "%s · %s" % (sender, conv)
    return sender or conv or "钉钉消息"


# ---------------------------------------------------------------------------
# 单个通知卡片（独立顶层窗口）
# ---------------------------------------------------------------------------
class NotifyPopup(QWidget):
    """一条待确认通知的置顶卡片。

    **关不掉**是刻意的：`closeEvent` 恒 `ignore()`，`Esc` 也忽略。唯一的出口是
    「我已知悉」（`ack()`）或「稍后再看」（`postpone()`，缩成角标、记录仍待确认）。

    Signals:
        acked(dict):     用户点了「我已知悉」，payload 是记录 dict。
        postponed(dict): 用户点了「稍后再看」，payload 是记录 dict。
    """

    acked = pyqtSignal(dict)
    postponed = pyqtSignal(dict)

    def __init__(self, rec, parent=None):
        """Args:
            rec: 通知记录（`NotifyStore` 的字段）。
            parent: 传 None —— 本类设计成**独立顶层窗口**；不要给它换 parent。
        """
        super().__init__(parent)
        self._rec = dict(rec or {})
        self._allow_close = False       # 只有 ack/postpone 才置 True
        self._done = False
        self._anim = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                 # 不占任务栏
        )
        # 显示但不抢焦点：不打断用户正在打的字
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("待确认通知")
        self.setFixedWidth(CARD_W + WINDOW_PAD * 2)

        self._build_ui()
        self._start_breathe()

    # ---------------- UI ----------------
    def _build_ui(self):
        kind = str(self._rec.get("kind") or "notice")
        color = _kind_color(kind)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(WINDOW_PAD, WINDOW_PAD, WINDOW_PAD, WINDOW_PAD)
        outer.setSpacing(0)

        card = QFrame(self)
        card.setObjectName("notifyCard")
        outer.addWidget(card)

        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(7)

        # 第一行：类别 + 时间
        head = QHBoxLayout()
        head.setSpacing(8)
        chip = QLabel(_kind_label(kind), card)
        chip.setObjectName("npChip")
        chip.setStyleSheet("color:%s;font-size:11px;font-weight:700;" % color)
        head.addWidget(chip)
        head.addStretch(1)
        when = QLabel(_fmt_ts(self._rec.get("ts")), card)
        when.setObjectName("npMeta")
        head.addWidget(when)
        box.addLayout(head)

        # 第二行：发送人 · 会话名
        title = QLabel(_who_line(self._rec), card)
        title.setObjectName("npTitle")
        title.setWordWrap(True)
        box.addWidget(title)

        # 第三行：正文摘要（最多 300 字）
        body = QLabel(ncs.summary(self._rec.get("text"), ncs.MAX_BODY), card)
        body.setObjectName("npBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setStyleSheet("color:#c8d3e2;font-size:13px;line-height:150%;")
        box.addWidget(body)

        # 第四行：按钮
        btns = QHBoxLayout()
        btns.setSpacing(8)
        later = QPushButton("稍后再看", card)
        later.setObjectName("npLater")
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        later.setToolTip("先缩成右上角标，但不会消失 —— 记录仍是未确认")
        later.setStyleSheet(
            "QPushButton{background:transparent;color:#93a2b8;"
            "border:1px solid #3a4457;border-radius:6px;padding:6px 12px;}"
            "QPushButton:hover{background:#232b3a;color:#c8d3e2;}")
        later.clicked.connect(self.postpone)
        btns.addWidget(later)

        ack = QPushButton("我已知悉", card)
        ack.setObjectName("npAck")
        ack.setCursor(Qt.CursorShape.PointingHandCursor)
        ack.setStyleSheet(
            "QPushButton{background:%s;color:#0d1117;border:none;"
            "border-radius:6px;padding:7px 16px;font-weight:700;}"
            "QPushButton:hover{background:%s;}"
            "QPushButton:pressed{background:%s;}" % (color, color, color))
        ack.clicked.connect(self.ack)
        btns.addWidget(ack, 1)
        box.addLayout(btns)

        card.setStyleSheet(
            "QFrame#notifyCard{background:#171b24;border:2px solid %s;"
            "border-radius:12px;}" % color)

    # ---------------- 对外 ----------------
    def nid(self):
        """这条通知的 id。"""
        return str(self._rec.get("id") or "")

    def record(self):
        """这条通知的记录（副本）。"""
        return dict(self._rec)

    def ack(self):
        """「我已知悉」：先隐藏再发信号，最后真正关闭。"""
        if self._done:
            return
        self._done = True
        self._allow_close = True
        rec = dict(self._rec)
        try:
            self.hide()
        except Exception:
            pass
        try:
            self.acked.emit(rec)
        finally:
            self.close()

    def postpone(self):
        """「稍后再看」：隐藏并关闭窗口，但记录仍留在未确认清单里。"""
        if self._done:
            return
        self._done = True
        self._allow_close = True
        rec = dict(self._rec)
        try:
            self.hide()
        except Exception:
            pass
        try:
            self.postponed.emit(rec)
        finally:
            self.close()

    def show_silently(self):
        """`show()` 但**不** `activateWindow()` —— 不抢焦点。"""
        self.show()

    # ---------------- 不可绕过 ----------------
    def closeEvent(self, event):
        """没点「我已知悉」/「稍后再看」之前，一律拒绝关闭。"""
        if self._allow_close:
            try:
                super().closeEvent(event)
            except Exception:
                event.accept()
            return
        event.ignore()

    def keyPressEvent(self, event):
        """`Esc` / `Alt+F4` 之类的「下意识关窗」全部无效。"""
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F4):
            event.ignore()
            return
        super().keyPressEvent(event)

    # ---------------- 动画 ----------------
    def _start_breathe(self):
        """2 秒的边框呼吸提示（只动 windowOpacity，不拿定时器狂刷）。"""
        try:
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(BREATH_MS)
            anim.setStartValue(1.0)
            anim.setKeyValueAt(0.5, 0.80)
            anim.setEndValue(1.0)
            anim.setLoopCount(2)
            anim.start()
            self._anim = anim          # 持引用，别让 GC 把动画收了
        except Exception as exc:
            ncs.log("通知呼吸动画启动失败（不影响显示）：%s" % exc)


# ---------------------------------------------------------------------------
# 角标（「稍后再看」之后缩成的置顶小条）
# ---------------------------------------------------------------------------
class NotifyBadge(QWidget):
    """右下角置顶角标：显示还有几条待确认，点一下重新展开。

    Signals:
        clicked(): 用户点了角标。
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count = 0
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("待确认通知")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        box = QHBoxLayout(self)
        box.setContentsMargins(8, 8, 8, 8)
        box.setSpacing(0)

        self._label = QLabel("0 条待确认", self)
        self._label.setStyleSheet(
            "background:#b8443c;color:#ffffff;border-radius:13px;"
            "padding:7px 14px;font-size:12px;font-weight:700;")
        box.addWidget(self._label)

    def set_count(self, count):
        """更新角标上的数字。"""
        try:
            count = max(0, int(count))
        except Exception:
            count = 0
        self._count = count
        self._label.setText("%d 条待确认" % count)
        self.adjustSize()

    def count(self):
        """当前角标数字。"""
        return self._count

    def show_silently(self):
        """`show()` 但不抢焦点。"""
        self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# 投屏中心
# ---------------------------------------------------------------------------
class Notifier(QObject):
    """主线程投送通道：`notify_arrived` 可以在任意线程 emit。

    Qt 的排队连接会把跨线程的 emit 安全地送到主线程（本对象在主线程创建），
    所以后台（钉钉 Stream）线程只需要 `emit`，不用自己碰控件。
    """

    notify_arrived = pyqtSignal(dict)


class NotifyCenter(Notifier):
    """通知弹窗池 + 队列 + 角标。

    只允许在主线程创建/操作。`push()` 在后台线程被调时会自动改走信号排队。
    """

    _push_signal = pyqtSignal(dict)

    def __init__(self, parent_widget=None, store=None, sound=True,
                 max_visible=MAX_VISIBLE, keywords=None):
        """Args:
            parent_widget: 主窗口（可为 None）。只用来定位屏幕和挂生命周期。
            store: `notify_center.NotifyStore`；None 则自动建在默认路径。
            sound: 是否 `QApplication.beep()` 提示音（不引入音频文件依赖）。
            max_visible: 同时显示的卡片数（默认 3）。
            keywords: 关键词表覆盖（写法见 `notify_center.normalize_keywords`）。
        """
        super().__init__(parent_widget if isinstance(parent_widget, QObject) else None)
        self._parent_widget = parent_widget
        self.sound = bool(sound)
        self.max_visible = max(1, int(max_visible or MAX_VISIBLE))
        self._popups = []        # 已显示：[0] 在最下面，越往后越上面
        self._queue = []         # 排队等位置 / 稍后再看之后挂起的记录
        self._badge = None
        self._showing_badge = False
        self._shut = False          # shutdown 之后 attach() 会重建

        if store is None:
            root = ""
            try:
                ws = getattr(parent_widget, "workspace", None)
                root = str(getattr(ws, "path", "") or "")
            except Exception:
                root = ""
            store = ncs.NotifyStore(ncs.default_store_path(root or None))
        self.store = store

        self.keywords = keywords
        if keywords is not None:
            try:
                ncs.set_keywords(keywords)
            except Exception:
                pass

        # 跨线程 emit → 主线程 _push_main（AutoConnection 自动排队）
        self._push_signal.connect(self._push_main)
        self.notify_arrived.connect(self._push_main)

    # ---------------- 线程安全 ----------------
    @staticmethod
    def _in_main_thread():
        """当前是否在 Qt 主线程（没有 QApplication 时视为是）。"""
        app = QApplication.instance()
        if app is None:
            return True
        try:
            return QThread.currentThread() is app.thread()
        except Exception:
            return True

    # ---------------- 投送 ----------------
    def push(self, rec):
        """投送一条通知（**线程安全**）。

        Args:
            rec: `NotifyStore` 字段的 dict（至少要 `text`）。

        Note:
            主线程调用会**立即**显示（便于测试与「点完马上补一个」的体验）；
            后台线程调用走信号排队，不会碰控件、不会阻塞调用方。
        """
        if not rec:
            return
        if self._shut:
            return
        payload = dict(rec)
        if self._in_main_thread():
            self._push_main(payload)
            return
        try:
            self._push_signal.emit(payload)
        except Exception as exc:
            ncs.log("通知跨线程投送失败：%s" % exc)
            try:
                QTimer.singleShot(0, lambda r=payload: self.push(r))
            except Exception:
                pass

    def _push_main(self, rec):
        """（主线程）落库校验 → 去重 → 展示或排队。"""
        try:
            rec = dict(rec or {})
            if not str(rec.get("text") or "").strip():
                return
            nid = str(rec.get("id") or "")
            if not nid:
                saved = self.store.add(rec) if self.store is not None else None
                if not saved:
                    return
                rec, nid = saved, str(saved.get("id"))

            if any(p.nid() == nid for p in self._popups):
                return
            if any(str(q.get("id") or "") == nid for q in self._queue):
                return

            # 以盘上状态为准：已经被确认过的不再弹
            try:
                live = self.store.get(nid) if self.store is not None else None
                if live is not None:
                    if live.get("acked"):
                        return
                    rec = live
            except Exception:
                pass

            if len(self._popups) >= self.max_visible:
                self._queue.append(rec)
                self._update_badge()
                return

            self._create_popup(rec)
            self._layout()
            self._update_badge()
            if self.sound:
                self._beep()
        except Exception as exc:
            ncs.log("通知显示失败（不影响主流程）：%s" % exc)

    def _create_popup(self, rec):
        """建卡片、连信号、显示（不抢焦点）。"""
        pop = NotifyPopup(rec, parent=None)
        pop.acked.connect(self._on_popup_ack)
        pop.postponed.connect(self._on_popup_later)
        self._popups.append(pop)
        try:
            pop.adjustSize()
            pop.show_silently()
        except Exception as exc:
            ncs.log("通知卡片显示失败：%s" % exc)
        return pop

    @staticmethod
    def _beep():
        """系统提示音（不引入任何音频文件依赖）。"""
        try:
            QApplication.beep()
        except Exception:
            pass

    # ---------------- 用户操作 ----------------
    def _take_popup(self, nid):
        """把某条卡片从显示列表里摘掉并销毁。"""
        for i, pop in enumerate(self._popups):
            if pop.nid() == nid:
                self._popups.pop(i)
                try:
                    pop.deleteLater()
                except Exception:
                    pass
                return True
        return False

    def _on_popup_ack(self, rec):
        """「我已知悉」：落库标记已确认，补上一个排队中的。"""
        nid = str((rec or {}).get("id") or "")
        self._take_popup(nid)
        if self.store is not None and nid:
            try:
                self.store.ack(nid)
            except Exception as exc:
                ncs.log("通知确认落库失败：%s" % exc)
        self._fill_from_queue()
        self._layout()
        self._update_badge()

    def _on_popup_later(self, rec):
        """「稍后再看」：收进角标，记录仍是未确认。"""
        nid = str((rec or {}).get("id") or "")
        self._take_popup(nid)
        self._fill_from_queue()          # 先补队列里真正在等的
        if rec:
            self._queue.append(dict(rec))  # 再把自己挂回队尾
        self._layout()
        self._update_badge()

    def _fill_from_queue(self):
        """有空位就补卡片（不重复布局，调用方负责 `_layout()`）。"""
        while self._queue and len(self._popups) < self.max_visible:
            rec = self._queue.pop(0)
            try:
                live = (self.store.get(str(rec.get("id") or ""))
                        if self.store is not None else None)
                if live is not None:
                    if live.get("acked"):
                        continue
                    rec = live
            except Exception:
                pass
            self._create_popup(rec)

    # ---------------- 布局 ----------------
    def _screen_rect(self):
        """优先用主窗口所在的屏幕，退到主屏，再退到 1280×800。"""
        scr = None
        try:
            p = self._parent_widget
            handle = p.windowHandle() if p is not None else None
            if handle is not None:
                scr = handle.screen()
            if scr is None and p is not None and hasattr(p, "screen"):
                scr = p.screen()
        except Exception:
            scr = None
        if scr is None:
            try:
                scr = QGuiApplication.primaryScreen()
            except Exception:
                scr = None
        if scr is None:
            return QRect(0, 0, 1280, 800)
        try:
            return scr.availableGeometry()
        except Exception:
            return QRect(0, 0, 1280, 800)

    def _layout(self):
        """右下角起、向上堆叠（[0] 在最下面，位置稳定不跳）。"""
        try:
            rect = self._screen_rect()
            right = rect.x() + rect.width()
            bottom = rect.y() + rect.height()
            y = bottom - MARGIN
            if self._showing_badge and self._badge is not None:
                y -= (self._badge.height() + GAP)
            for pop in self._popups:
                pop.adjustSize()
                w, h = pop.width(), pop.height()
                pop.move(right - MARGIN - w, max(rect.y() + MARGIN, y - h))
                y = y - h - GAP
            if self._showing_badge and self._badge is not None:
                self._badge.adjustSize()
                self._badge.move(
                    right - MARGIN - self._badge.width(),
                    bottom - MARGIN - self._badge.height())
        except Exception as exc:
            ncs.log("通知布局失败：%s" % exc)

    # ---------------- 角标 ----------------
    def _update_badge(self):
        """按「待确认总数 − 当前显示数」决定角标显不显示。"""
        try:
            pending = self.pending_count()
            need = pending - len(self._popups)
            if need <= 0:
                self._hide_badge()
                return
            if self._badge is None:
                self._badge = NotifyBadge(parent=None)
                self._badge.clicked.connect(self.show_pending)
            self._badge.set_count(need)
            self._showing_badge = True
            self._badge.show_silently()
            self._layout()
        except Exception as exc:
            ncs.log("通知角标更新失败：%s" % exc)

    def _hide_badge(self):
        self._showing_badge = False
        try:
            if self._badge is not None:
                self._badge.hide()
        except Exception:
            pass

    def badge(self):
        """角标控件（没建就是 None），供 UI 测试取用。"""
        return self._badge

    # ---------------- 对外查询/操作 ----------------
    def pending_count(self):
        """未确认总数（角标 / 页面徽标用）。"""
        try:
            if self.store is not None:
                return len(self.store.pending())
        except Exception:
            pass
        ids = set(p.nid() for p in self._popups)
        ids.update(str(q.get("id") or "") for q in self._queue)
        return len([x for x in ids if x])

    def acked_count(self):
        """已确认总数（统计用）。"""
        try:
            if self.store is not None:
                return int(self.store.stats().get("acked") or 0)
        except Exception:
            pass
        return 0

    def stats(self):
        """清单统计（`notify_center.NotifyStore.stats()` 的转发）。"""
        try:
            if self.store is not None:
                return self.store.stats()
        except Exception:
            pass
        return {"total": 0, "pending": 0, "acked": 0, "by_kind": {}}

    def visible_popups(self):
        """当前显示中的卡片列表（副本）。"""
        return list(self._popups)

    def popup_count(self):
        """当前显示中的卡片数。"""
        return len(self._popups)

    def show_pending(self):
        """把所有未确认记录重新摊到屏幕上（角标点击走这里）。"""
        self._hide_badge()
        try:
            pend = self.store.pending() if self.store is not None else []
        except Exception:
            pend = []
        shown = set(p.nid() for p in self._popups)
        queued = set(str(q.get("id") or "") for q in self._queue)
        for rec in pend:
            nid = str(rec.get("id") or "")
            if nid and nid not in shown and nid not in queued:
                self._queue.append(rec)
        self._fill_from_queue()
        self._layout()
        self._update_badge()
        return self.popup_count()

    def ack_all(self):
        """全部确认（清屏）。

        Returns:
            int —— 这次确认了多少条。
        """
        n = 0
        try:
            n = self.store.ack_all() if self.store is not None else 0
        except Exception as exc:
            ncs.log("通知全部确认失败：%s" % exc)
        for pop in self.popups_snapshot():
            self._take_popup(pop.nid())
        self._queue.clear()
        self._hide_badge()
        return n

    def popups_snapshot(self):
        """显示中卡片的快照（遍历时不怕列表被改）。"""
        return list(self._popups)

    def hide_all(self):
        """只收起窗口、不动待确认清单（角标照旧盯着）。"""
        for pop in list(self._popups):
            self._take_popup(pop.nid())
        self._layout()
        self._update_badge()

    def set_sound(self, on):
        """开关提示音。"""
        self.sound = bool(on)
        return self.sound

    def set_max_visible(self, n):
        """调整同屏卡片数（最小 1）。"""
        self.max_visible = max(1, int(n or MAX_VISIBLE))
        self._fill_from_queue()
        self._layout()
        self._update_badge()
        return self.max_visible

    def shutdown(self):
        """退出时收尾：关窗、断信号（清单里未确认的仍然留着，下次启动继续盯）。"""
        self._shut = True
        try:
            self.notify_arrived.disconnect()
        except Exception:
            pass
        for pop in list(self._popups):
            try:
                pop._allow_close = True
                pop.close()
                pop.deleteLater()
            except Exception:
                pass
        self._popups = []
        try:
            if self._badge is not None:
                self._badge.hide()
                self._badge.deleteLater()
                self._badge = None
        except Exception:
            pass
        self._showing_badge = False
        self._queue = []


# ---------------------------------------------------------------------------
# 单例工厂：主窗口只要一行
# ---------------------------------------------------------------------------
_SINGLETON = None      # weakref.ref(NotifyCenter)


def attach(main_window, store=None, sound=True, keywords=None):
    """在主窗口里挂上通知中心（幂等，重复调用返回同一个实例）。

    用法（`app/gui/main_window.py` 里加**一行**）::

        from . import notify_popup
        notify_popup.attach(self)     # 放在 __init__ 末尾、im_hub 起来之前都可以

    内部做的事：
        1. `notify_center.set_sink(center.notify_arrived.emit)` —— 后台线程
           `deliver()` 时只是 emit 一个信号，跨线程由 Qt 排队到主线程；
        2. 清单落在 `<工作区>/.awb/notify_pending.json`（传 store 可覆盖）；
        3. 把实例挂在 `main_window._notify_center` 上，避免被 GC 掉。

    Args:
        main_window: 主窗口（用来定位屏幕、取工作区路径、挂生命周期）。
        store: 可选的 `NotifyStore`。
        sound: 是否响提示音。
        keywords: 可选关键词表覆盖。

    Returns:
        NotifyCenter
    """
    global _SINGLETON
    try:
        cur = _SINGLETON() if _SINGLETON is not None else None
        if cur is not None and not getattr(cur, "_shut", False):
            return cur
    except Exception:
        cur = None

    if store is None:
        root = ""
        try:
            ws = getattr(main_window, "workspace", None)
            root = str(getattr(ws, "path", "") or "")
        except Exception:
            root = ""
        store = ncs.NotifyStore(ncs.default_store_path(root or None))

    center = NotifyCenter(main_window, store, sound=sound, keywords=keywords)
    ncs.set_sink(center.notify_arrived.emit)

    try:
        setattr(main_window, "_notify_center", center)
    except Exception:
        pass
    try:
        _SINGLETON = weakref.ref(center)
    except Exception:
        _SINGLETON = None
    ncs.log("通知中心已挂载：%s" % getattr(store, "path", "?"))
    return center


def current():
    """当前挂上的通知中心（没挂就是 None）。"""
    try:
        return _SINGLETON() if _SINGLETON is not None else None
    except Exception:
        return None
