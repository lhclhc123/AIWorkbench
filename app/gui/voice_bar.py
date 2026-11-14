# -*- coding: utf-8 -*-
"""语音输入控件：麦克风按钮 + 实时音量波形 + 识别中状态。"""
import os
import time

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen

from .. import themes, asr as asr_mod
from .. import asr_local


class LevelMeter(QWidget):
    """实时音量条（录音时跳动）。"""

    def __init__(self, bars=28, parent=None):
        super().__init__(parent)
        self.bars = bars
        self.values = [0.0] * bars
        self._last_values = list(self.values)
        self.setFixedHeight(26)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.active = False

    def push(self, v):
        """推入一个新电平；整条波形没变化就不重绘（v9.22.11 去卡顿）。"""
        try:
            value = max(0.0, min(1.0, float(v or 0.0)))
        except (TypeError, ValueError):
            value = 0.0
        self.values.append(value)
        while len(self.values) > self.bars:
            self.values.pop(0)
        # 静音时 160ms 里推上来的全是 0，波形一点没变，没必要触发 paintEvent
        if self._last_values == self.values:
            return
        self._last_values = list(self.values)
        self.update()

    def reset(self):
        self.values = [0.0] * self.bars
        self._last_values = list(self.values)
        self.update()

    def paintEvent(self, event):
        t = themes.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = self.bars
        gap = 2
        bw = max(2.0, (w - gap * (n - 1)) / n)
        for i, v in enumerate(self.values):
            x = i * (bw + gap)
            bh = max(2.0, v * (h - 4))
            color = QColor(t["wave"] if self.active else t["wave_dim"])
            if v > 0.75:
                color = QColor(t["danger"])
            elif v > 0.5:
                color = QColor(t["warn"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(int(x), int((h - bh) / 2), int(bw), int(bh), 2, 2)
        p.end()


class VoiceInput(QWidget):
    """一体化语音输入条。

    用法：把它放进输入区旁边；接 `transcribed` 信号拿到文字。
    """

    transcribed = pyqtSignal(str)      # 识别完成（最终结果，整段）
    partial = pyqtSignal(str)          # 边说边出字：中途的实时结果（整段覆盖）
    status = pyqtSignal(str)           # 状态提示
    failed = pyqtSignal(str)           # 出错
    model_progress = pyqtSignal(int, str)   # 离线模型下载进度（百分比, 一句话）
    # 内部信号：后台识别线程 → UI 线程。QTimer 不是线程安全的，必须先跨线程
    # 排队回主线程，再做 200ms 节流，否则 start() 会在工作线程里被误调用。
    _stream_text = pyqtSignal(str)

    def __init__(self, keys_provider=None, settings_provider=None, parent=None):
        super().__init__(parent)
        self._keys_provider = keys_provider or (lambda: {})
        self._settings_provider = settings_provider or (lambda: {})
        self.recorder = None
        self.session = None            # asr.StreamingSession（边说边出字）
        self.busy = False
        self._pending_path = None
        self._stream_error = None
        self._last_model_text = ""
        # 下载线程 → 界面：信号是跨线程排队的，安全
        self.model_progress.connect(self._on_model_progress)
        # 增量识别线程 → 界面：先排队回 UI 线程，再节流
        self._stream_text.connect(self._on_stream_text)
        self._pending_partial = None
        self._stream_active = False
        self._partial_timer = QTimer(self)
        self._partial_timer.setSingleShot(True)
        self._partial_timer.setInterval(200)
        self._partial_timer.timeout.connect(self._flush_partial)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.mic = QPushButton("◍")
        self.mic.setObjectName("MicBtn")
        self.mic.setCheckable(True)
        self.mic.setFixedSize(40, 40)
        self.mic.setToolTip("点一下开始说话，再点一下结束（也可以说完停顿约 10 秒自动结束，默认最长 5 分钟）")
        self.mic.clicked.connect(self.toggle)
        row.addWidget(self.mic)

        self.meter = LevelMeter()
        row.addWidget(self.meter, 1)

        self.hint = QLabel("语音输入")
        self.hint.setStyleSheet(f"color:{themes.tokens()['text_muted']};font-size:11px;")
        self.hint.setMinimumWidth(96)
        row.addWidget(self.hint)

        self._timer = QTimer(self)
        # v9.22.11：90ms → 160ms，重绘频率砍掉四成（配合 LevelMeter 跳过无变化帧）
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    def refresh_theme(self):
        self.hint.setStyleSheet(f"color:{themes.tokens()['text_muted']};font-size:11px;")
        self.meter.update()

    def toggle(self):
        if self.busy:
            return
        if self.recorder is None:
            self.start()
        else:
            self.finish()

    def start(self):
        s = self._settings_provider() or {}
        dev = s.get("mic_device", -1)
        try:
            self.recorder = asr_mod.Recorder(
                device=None if int(dev) < 0 else int(dev),
                # 静音判定默认 10 秒（用户 2026-09-30 要求：说话中间停顿
                # 10 秒左右才停；旧值 3 秒/1.6 秒会在换气、想词时被提前掐断）
                silence_seconds=float(s.get("voice_silence", 10.0) or 10.0),
                # 最长录音默认 5 分钟（以前 120 秒，长句子/口述需求根本不够）
                max_seconds=float(s.get("voice_max_seconds", 300) or 300))
            self.recorder.start()
        except Exception as e:
            self.recorder = None
            self.mic.setChecked(False)
            self.failed.emit(f"打不开麦克风：{e}")
            return
        self.meter.reset()
        self.meter.active = True
        self.mic.setChecked(True)
        self.mic.setText("■")
        self.hint.setText("正在录音 0.0s")
        self._timer.start()
        self._ensure_local_model()
        self._start_streaming(s)
        self.status.emit("正在录音…中间停顿约 10 秒才会自动结束，说完了也可以直接再点一下麦克风")

    # ------------------------------------------------------------------
    # 离线语音模型（★ v9.22.10）
    # ------------------------------------------------------------------
    def _ensure_local_model(self):
        """首次使用：后台把离线模型下完（约 78 MB，只下一次）。

        为什么要在这里做：云端三家识别（智谱/硅基流动/百炼）全部要看账户余额，
        2026-11-14 实测已全部不可用；离线模型是本机跑、永久免费的唯一可靠路子。
        下载在守护线程里跑，进度通过 model_progress 信号回到 UI，不卡界面。
        """
        try:
            if asr_local.ready():
                asr_local.warm_async()      # 模型已在盘上：后台预热，首次识别不等 3 秒
                return
            if not asr_local.installed():
                return
            asr_local.ensure_async(
                lambda pct, text: self.model_progress.emit(int(pct), str(text)))
            self.status.emit(asr_local.status_text())
        except Exception:
            pass

    def _on_model_progress(self, pct, text):
        """下载线程的进度回到 UI 线程（同一条提示不重复刷）。"""
        if not text or text == self._last_model_text:
            return
        self._last_model_text = text
        # 录音中 _tick 每 160ms 会重写 hint，别跟它抢；只走状态栏
        if self.recorder is None:
            self.hint.setText(text)
        self.status.emit(text)

    def _start_streaming(self, settings):
        """开一路后台增量识别，让文字边说边出（失败不影响正常录音/整段识别）。"""
        self._stream_error = None
        self._stream_active = False
        try:
            self.session = asr_mod.StreamingSession(
                self.recorder,
                self._keys_provider,
                (settings or {}).get("asr_prefer", "auto"),
                on_text=self._stream_text.emit,
                on_error=self._on_stream_error)
            self.session.start()
            self._stream_active = True
        except Exception as e:
            self.session = None
            self._stream_active = False
            self._on_stream_error(e)

    def _on_stream_text(self, text):
        """增量识别线程来的预览（已跨线程排队回 UI）：只缓存最后一条。

        200ms 内最多刷一次输入框 —— 之前每轮识别结果都直接 emit，
        长句时 WebUI 的 JS 会被高频 voice_partial 事件拖慢。
        finish()/cancel() 之后排队中的旧预览一律丢弃，别回头覆盖定稿。
        """
        if not self._stream_active:
            return
        self._pending_partial = str(text or "")
        if not self._partial_timer.isActive():
            self._partial_timer.start()

    def _flush_partial(self):
        """把缓存的最后一条预览真正发出去。"""
        if self._pending_partial is None:
            return
        text = self._pending_partial
        self._pending_partial = None
        self.partial.emit(text)

    def _on_stream_error(self, msg):
        """增量识别出错：不打扰用户（结束时仍会整段识别），只记一次。"""
        if self._stream_error is not None:
            return
        self._stream_error = str(msg)

    def _stop_streaming(self):
        """停掉增量识别。stop() 在等网络时会立刻返回，绝不卡住 UI 线程。"""
        s = self.session
        self.session = None
        self._stream_active = False
        # 收尾时把节流缓存里最后一条预览补发出去，别丢字；
        # 之后 finish() 的整段结果会覆盖它（前端按整段替换，不会叠加）。
        self._partial_timer.stop()
        self._flush_partial()
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass

    def finish(self):
        rec = self.recorder
        self.recorder = None
        self._stop_streaming()
        self._timer.stop()
        self.meter.active = False
        self.mic.setChecked(False)
        self.mic.setText("◍")
        if rec is None:
            return
        try:
            path = rec.stop()
        except Exception as e:
            self.meter.reset()
            self.failed.emit(str(e))
            self.hint.setText("语音输入")
            return
        self.busy = True
        self._pending_path = path
        s = self._settings_provider() or {}
        if asr_local.installed() and not asr_local.ready():
            # 模型还在下载：如实说明，别假装「识别中」转圈（后台线程会等它下完）
            self._ensure_local_model()
            self.hint.setText("准备离线语音…")
            self.status.emit(asr_local.status_text())
        else:
            self.hint.setText("识别中…")
            self.status.emit("正在识别语音…")
        asr_mod.recognize_file_async(
            path, self._keys_provider(), s.get("asr_prefer", "auto"),
            self._ok, self._err)

    def cancel(self):
        self._stop_streaming()
        if self.recorder is not None:
            try:
                self.recorder.cancel()
            except Exception:
                pass
            self.recorder = None
        self._timer.stop()
        self.meter.active = False
        self.meter.reset()
        self.mic.setChecked(False)
        self.mic.setText("◍")
        self.hint.setText("语音输入")

    def _tick(self):
        rec = self.recorder
        if rec is None:
            self._timer.stop()
            return
        self.meter.push(rec.level)
        left = rec.silence_left()
        txt = f"录音中 {rec.seconds:.1f}s"
        if rec.max_seconds and rec.max_seconds < 3600:
            left_max = max(0.0, rec.max_seconds - rec.seconds)
            txt += f" / 上限 {left_max:.0f}s"
        if rec.seconds > rec.min_seconds and left > 0:
            txt += f" · {left:.1f}s 后自动结束"
        self.hint.setText(txt)
        if rec.auto_stopped:
            self.finish()

    # ------------------------------------------------------------------
    def _ok(self, text, backend):
        self.busy = False
        self.hint.setText("语音输入")
        self.meter.reset()
        if not text:
            self.failed.emit("没听清，再说一次？")
            return
        self.status.emit(f"识别完成（{backend}）")
        self.transcribed.emit(text)

    def _err(self, msg):
        self.busy = False
        self.hint.setText("语音输入")
        self.meter.reset()
        self.failed.emit(msg)
