# -*- coding: utf-8 -*-
"""语音输入控件：麦克风按钮 + 实时音量波形 + 识别中状态。"""
import os
import time

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen

from .. import themes, asr as asr_mod


class LevelMeter(QWidget):
    """实时音量条（录音时跳动）。"""

    def __init__(self, bars=28, parent=None):
        super().__init__(parent)
        self.bars = bars
        self.values = [0.0] * bars
        self.setFixedHeight(26)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.active = False

    def push(self, v):
        self.values.append(max(0.0, min(1.0, v)))
        while len(self.values) > self.bars:
            self.values.pop(0)
        self.update()

    def reset(self):
        self.values = [0.0] * self.bars
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

    transcribed = pyqtSignal(str)      # 识别完成（文本）
    status = pyqtSignal(str)           # 状态提示
    failed = pyqtSignal(str)           # 出错

    def __init__(self, keys_provider=None, settings_provider=None, parent=None):
        super().__init__(parent)
        self._keys_provider = keys_provider or (lambda: {})
        self._settings_provider = settings_provider or (lambda: {})
        self.recorder = None
        self.busy = False
        self._pending_path = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.mic = QPushButton("🎙")
        self.mic.setObjectName("MicBtn")
        self.mic.setCheckable(True)
        self.mic.setFixedSize(40, 40)
        self.mic.setToolTip("点一下开始说话，再点一下结束（也可以说完停顿自动结束，默认最长 5 分钟）")
        self.mic.clicked.connect(self.toggle)
        row.addWidget(self.mic)

        self.meter = LevelMeter()
        row.addWidget(self.meter, 1)

        self.hint = QLabel("语音输入")
        self.hint.setStyleSheet(f"color:{themes.tokens()['text_muted']};font-size:11px;")
        self.hint.setMinimumWidth(96)
        row.addWidget(self.hint)

        self._timer = QTimer(self)
        self._timer.setInterval(90)
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
                # 静音判定默认 3 秒（以前 1.6 秒，想词时经常被提前掐断）
                silence_seconds=float(s.get("voice_silence", 3.0) or 3.0),
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
        self.status.emit("正在录音…说完了停顿一下会自动结束，也可以再点一下麦克风手动结束")

    def finish(self):
        rec = self.recorder
        self.recorder = None
        self._timer.stop()
        self.meter.active = False
        self.mic.setChecked(False)
        self.mic.setText("🎙")
        if rec is None:
            return
        try:
            path = rec.stop()
        except Exception as e:
            self.meter.reset()
            self.failed.emit(str(e))
            self.hint.setText("语音输入")
            return
        self.hint.setText("识别中…")
        self.busy = True
        self.status.emit("正在识别语音…")
        self._pending_path = path
        s = self._settings_provider() or {}
        asr_mod.recognize_file_async(
            path, self._keys_provider(), s.get("asr_prefer", "auto"),
            self._ok, self._err)

    def cancel(self):
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
        self.mic.setText("🎙")
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
