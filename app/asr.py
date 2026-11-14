# -*- coding: utf-8 -*-
"""语音输入：麦克风录音 + 多后端语音识别。

后端（按优先级自动尝试）：
  0. local       离线识别（本机 sherpa-onnx + 中文 Paraformer）—— **默认首选**
                 不联网、不要 Key、没有「余额不足」。见 `app/asr_local.py`。
  1. zhipu       智谱 glm-asr-2512 —— 同步 multipart，用已有的智谱 Key（**付费**）
  2. siliconflow 硅基流动 SenseVoiceSmall —— 注册即送额度，中文准确率高
  3. dashscope   阿里百炼 paraformer-v2 —— 先传 OSS 再异步转写（需账号已开通 ASR）

★ v9.22.10 为什么把「离线」提到第一位（2026-11-14 实测，不是猜的）：
  用户问「智谱不是免费的吗？」——同一把智谱 Key：
    · glm-4-flash 文本模型 → HTTP 200（免费，正常）
    · glm-asr-2512 语音转写 → HTTP 429 code=1113「余额不足或无可用资源包」
  也就是说「智谱免费」只覆盖文本/视觉模型，**语音转写是单独计费的付费产品**；
  硅基流动 HTTP 402 余额不足、百炼 FILE_DOWNLOAD_FAILED/SERVER_ERROR，同样不能用。
  于是默认改成在本机跑模型：永久免费、断网也能用、换台电脑也不用重新配。

录音用 pyaudio，统一转成 16kHz / 单声道 / 16bit PCM（各家的 ASR 都吃这个格式）。
"""
import io
import os
import queue
import struct
import tempfile
import threading
import time
import wave

import requests

RATE = 16000
CHANNELS = 1
CHUNK = 1600          # 100ms @16k


class ASRError(Exception):
    pass


# ------------------------------------------------------------------ 音频工具
def audio_dir():
    d = os.path.join(tempfile.gettempdir(), "AIWorkbench", "audio")
    os.makedirs(d, exist_ok=True)
    return d


def new_wav_path(prefix="voice"):
    return os.path.join(audio_dir(), f"{prefix}_{int(time.time() * 1000)}.wav")


def wav_duration(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate() or RATE)
    except Exception:
        return 0.0


def to_16k_mono(src, dst=None):
    """把任意 WAV 转成 16k / 单声道 / 16bit，返回新路径。"""
    dst = dst or (os.path.splitext(src)[0] + "_16k.wav")
    with wave.open(src, "rb") as w:
        ch, rate, sw, n = (w.getnchannels(), w.getframerate(),
                           w.getsampwidth(), w.getnframes())
        data = w.readframes(n)
    if sw != 2:
        raise ASRError("音频位深不是 16bit，无法转换")
    if ch == 2:
        # 手动降混，避免依赖已废弃的 audioop
        count = len(data) // 4
        out = bytearray(count * 2)
        for i in range(count):
            l = struct.unpack_from("<h", data, i * 4)[0]
            r = struct.unpack_from("<h", data, i * 4 + 2)[0]
            struct.pack_into("<h", out, i * 2, int((l + r) / 2))
        data = bytes(out)
    if rate != RATE:
        data = _resample_pcm16(data, rate, RATE)
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)
    return dst


def _resample_pcm16(data, src_rate, dst_rate):
    """线性插值重采样（够用且无依赖）。"""
    if src_rate == dst_rate:
        return data
    n_in = len(data) // 2
    if n_in == 0:
        return b""
    samples = struct.unpack("<%dh" % n_in, data[:n_in * 2])
    ratio = dst_rate / float(src_rate)
    n_out = int(n_in * ratio)
    out = bytearray(n_out * 2)
    for i in range(n_out):
        pos = i / ratio
        i0 = int(pos)
        i1 = min(i0 + 1, n_in - 1)
        frac = pos - i0
        v = samples[i0] * (1 - frac) + samples[i1] * frac
        struct.pack_into("<h", out, i * 2, int(max(-32768, min(32767, v))))
    return bytes(out)


def list_input_devices():
    """列出可用的录音设备，供设置页选择。"""
    try:
        import pyaudio
    except Exception:
        return []
    pa = pyaudio.PyAudio()
    out = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                out.append({"index": i, "name": info.get("name", f"设备{i}"),
                            "channels": int(info.get("maxInputChannels", 1)),
                            "rate": int(info.get("defaultSampleRate", 44100))})
    finally:
        try:
            pa.terminate()
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ 录音
class Recorder:
    """麦克风录音器。

    后台线程持续读取音频块：
      - 计算实时音量 level（0~1），给界面画波形/电平；
      - 支持"静音自动停止"（说完停顿一下就自动结束）；
      - 支持随时 stop() 拿到 WAV 文件。
    """

    def __init__(self, rate=RATE, channels=CHANNELS, device=None,
                 silence_seconds=10.0, min_seconds=1.0, max_seconds=300):
        """silence_seconds：静音多久算"说完了"（默认 10 秒）。
        用户 2026-09-30 明确要求："我说话中间停顿个 10 秒左右再停"——
        旧默认 3 秒（更早 1.6 秒）在正常说话换气/想词时就会被提前掐断。
        min_seconds：最少录多久才允许自动停（防"嗯"一声就结束）。
        max_seconds：最长录音（默认 5 分钟，到点自动停；也可以手动点停）。
        """
        self.rate = rate
        self.channels = channels
        self.device = device
        self.silence_seconds = silence_seconds
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.level = 0.0
        self.peak = 0.0
        self.error = None
        self.path = None
        self.auto_stopped = False
        self._frames = []
        self._stop_flag = threading.Event()
        self._thread = None
        self._started = 0.0
        self._last_loud = 0.0
        self._lock = threading.Lock()

    # ---- 生命周期 ----
    def start(self):
        import pyaudio
        self._pa = pyaudio.PyAudio()
        kwargs = dict(format=pyaudio.paInt16, channels=self.channels, rate=self.rate,
                      input=True, frames_per_buffer=CHUNK)
        if self.device is not None:
            kwargs["input_device_index"] = int(self.device)
        self._stream = self._pa.open(**kwargs)
        self._started = time.time()
        self._last_loud = self._started
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        try:
            while not self._stop_flag.is_set():
                data = self._stream.read(CHUNK, exception_on_overflow=False)
                with self._lock:
                    self._frames.append(data)
                    self.level = _rms(data)
                    self.peak = max(self.peak * 0.92, self.level)
                now = time.time()
                if self.level > 0.02:
                    self._last_loud = now
                elapsed = now - self._started
                if elapsed >= self.max_seconds:
                    self.auto_stopped = True
                    self._stop_flag.set()
                elif (elapsed >= self.min_seconds
                      and now - self._last_loud >= self.silence_seconds):
                    self.auto_stopped = True
                    self._stop_flag.set()
        except Exception as e:
            self.error = str(e)
            self._stop_flag.set()

    @property
    def seconds(self):
        return max(0.0, time.time() - self._started)

    @property
    def recorded_seconds(self):
        """已录到的音频**实际**时长（按帧数算）。

        seconds 是墙钟时间，设备缓冲/丢帧时会和真实音频长度有偏差；
        滑动窗口要靠它算「窗口起点」和「已确认前缀」，用帧数更准。
        """
        with self._lock:
            sizes = [len(f) for f in self._frames]
        return sum(sizes) / float(max(1, self.rate * self.channels * 2))

    @property
    def recording(self):
        """是否正在录音（录音线程还活着且没有被要求停止）。"""
        t = self._thread
        return bool(t is not None and t.is_alive() and not self._stop_flag.is_set())

    def snapshot_wav(self, prefix="snap", window_seconds=0):
        """把"当前已经录到的音频"立刻写成一份完整可解码的 WAV，返回路径。

        window_seconds > 0 时**只导出最近 N 秒**：按本机采样率的 16bit PCM
        字节数计算（默认 16k 单声道 = 32000 B/s）。这是 v9.22.11 去卡顿的
        关键 —— 流式识别每轮只吃固定长度的尾巴，单轮计算量不随录音总长增长。
        window_seconds <= 0 时行为与旧版完全一致（导出整段）。

        与 stop() 的区别：不停录音、不关闭设备，只取一个快照，
        供"边说边出字"的流式识别每轮拿一份完整文件去请求 ASR。

        线程安全：录音线程在 _loop() 里 append 时持 self._lock，
        这里在锁内只做一次列表浅拷贝（很快），joining/wave 写盘全在锁外，
        不会长时间卡住录音线程（否则会丢音频）。
        """
        with self._lock:
            frames = list(self._frames)
            channels = self.channels
            rate = self.rate
        if not frames:
            raise ASRError("还没有录到音频")
        try:
            window = float(window_seconds or 0.0)
        except (TypeError, ValueError):
            window = 0.0
        frame_bytes = max(1, channels * 2)
        if window > 0:
            keep = int(rate * channels * 2 * window)
            keep = (keep // frame_bytes) * frame_bytes
            if keep > 0:
                # 只从尾部取够 N 秒的块，避免把整段音频都 join 一遍
                picked = []
                total = 0
                for chunk in reversed(frames):
                    picked.append(chunk)
                    total += len(chunk)
                    if total >= keep:
                        break
                picked.reverse()
                data = b"".join(picked)
                if len(data) > keep:
                    data = data[len(data) - keep:]
            else:
                data = b"".join(frames)
        else:
            data = b"".join(frames)
        if not data:
            raise ASRError("还没有录到音频")
        path = new_wav_path(prefix)
        with wave.open(path, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(data)
        return path

    def silence_left(self):
        """距离自动停止还有多久（秒）；界面可以倒计时提示。"""
        if not self._started:
            return self.silence_seconds
        if self.seconds < self.min_seconds:
            return self.silence_seconds
        return max(0.0, self.silence_seconds - (time.time() - self._last_loud))

    def stop(self):
        """停止录音，返回 WAV 路径（失败返回 None）。"""
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            frames = list(self._frames)
            self._frames = []
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass
        if self.error:
            raise ASRError("录音失败：" + self.error)
        if not frames:
            raise ASRError("没有录到音频（检查麦克风是否可用/被占用）")
        path = new_wav_path()
        with wave.open(path, "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(b"".join(frames))
        self.path = path
        return path

    def cancel(self):
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self._stream.stop_stream()
            self._stream.close()
            self._pa.terminate()
        except Exception:
            pass
        with self._lock:
            self._frames = []


def _rms(data):
    """16bit PCM 的均方根 -> 0~1 的电平值。"""
    if not data:
        return 0.0
    n = len(data) // 2
    if n == 0:
        return 0.0
    total = 0
    unpack = struct.unpack
    for v in unpack("<%dh" % n, data[:n * 2]):
        total += v * v
    rms = (total / n) ** 0.5
    return min(1.0, rms / 8000.0)


# ------------------------------------------------------------------ 流式识别
class StreamingSession:
    """边说边出字：滑动窗口 + 已确认前缀（v9.22.11 去卡顿版）。

    旧版每轮把「整段已录音频」全量重跑一次离线识别，音频越长单轮越慢，
    CPU 被占满 → 界面卡顿。现在改成：

      - 每轮只取**最近 window 秒**（默认 8s）的快照去识别，
        单轮计算量与录音总长无关，恒定；
      - 更早的音频按静音边界（预览文本里由停顿/语义规则生成的标点）
        累计成「已确认前缀」，on_text 发「前缀 + 本轮预览文本」；
      - 每轮前检查 _busy，绝不并发识别（上一轮没完就跳过）；
      - 录音超过 max_window（默认 45s）就停增量，交给 finish() 整段识别定稿。

    现有后端（本机 Paraformer / 智谱 / 硅基流动 / 百炼）都只有同步接口，
    没有真正的流式 WebSocket，所以用滑动窗口逼近流式体验；
    说完由 VoiceInput.finish() 再做一次整段识别作为最终结果。

    线程模型：整个循环（含网络请求、模型计算与写盘）都在守护线程里跑，
    绝不在 UI 线程做重活；回调由调用方（Qt 信号）自行跨线程投递。
    同一时刻最多只有一个识别请求在跑——上一轮没回来就等它，
    不排队、不堆积请求。
    """

    MIN_GROWTH = 0.4          # 音频至少增长这么多秒才值得再识别一次
    REQUEST_TIMEOUT = 12      # 单轮识别超时（秒）——慢就慢，不能卡死循环
    DEFAULT_MAX_WINDOW = 45.0  # 默认超过 45s 就停增量，交给整段识别定稿

    def __init__(self, recorder, keys_provider, prefer="auto",
                 on_text=None, on_error=None, interval=1.6, max_window=0,
                 window=8.0):
        """recorder：Recorder（或同接口对象，需有 snapshot_wav()/seconds）。
        keys_provider：无参函数，返回 {"zhipu": "...", ...}（每轮都重新取，改 Key 即时生效）。
        on_text：识别到文本时的回调（str）——已是「已确认前缀 + 本轮预览」。
        on_error：出错回调（str），整个会话最多回调一次，避免刷屏。
        interval：音频 <8s 时的识别间隔上限（秒，默认 1.6，实际压到 1.2）。
        max_window：>0 时录音超过这么多秒就停止增量识别；0 = 用默认 45s；
            <0 = 不限制（旧版语义，仅调试用）。
        window：滑动窗口长度（秒，默认 8.0）。★ 新增参数，只能追加在最后，
            旧调用（旧参数顺序 / 默认值 / 含义）完全不受影响。
        """
        self.recorder = recorder
        self._keys_provider = keys_provider or (lambda: {})
        self.prefer = prefer or "auto"
        self.on_text = on_text
        self.on_error = on_error
        self.base_interval = float(interval or 1.6)
        try:
            given = float(max_window or 0)
        except (TypeError, ValueError):
            given = 0.0
        self.max_window = self.DEFAULT_MAX_WINDOW if given == 0 else given
        try:
            self.window = float(window or 8.0)
        except (TypeError, ValueError):
            self.window = 8.0
        self.window = max(2.0, min(30.0, self.window))
        self._stop_flag = threading.Event()
        self._thread = None
        self._busy = False            # 是否有一轮识别请求在跑
        self._last_duration = 0.0     # 上一轮识别时录音的**总**时长
        self._last_text = ""          # 上一次回调的文本（去重）
        self._error_reported = False
        # ---- 滑动窗口 / 已确认前缀状态 ----
        self._confirmed_prefix = ""   # 已经滚出窗口的音频对应的文字
        self._committed_end = 0.0     # 已确认前缀覆盖到的音频时间（秒）
        self._prev_start = 0.0        # 上一轮窗口起点（秒，绝对时间）
        self._prev_end = 0.0          # 上一轮窗口终点（秒，绝对时间）
        self._prev_text = ""          # 上一轮窗口识别出的文本
        self._prev_commit_index = 0   # 上一轮文本里「已经进前缀」的字符数
                                      # （窗口是重叠的，绝不重复提交同一段字）

    # ---- 生命周期 ----
    def start(self):
        """启动后台循环（幂等：已在跑就直接返回）。"""
        t = self._thread
        if t is not None and t.is_alive():
            return self
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._loop, name="asr-streaming", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=3.0):
        """请求停止。

        若此刻没有识别请求在跑，会等线程退出（最多 timeout 秒）；
        若正在等网络返回，立刻返回，让线程在本次请求回来后自行结束
        （线程是 daemon，且停止位已置，不会再回调 on_text）。
        这样 UI 线程调用 stop() 永远不会被网络卡住。
        """
        self._stop_flag.set()
        t = self._thread
        if t is None or not t.is_alive():
            return self
        if self._busy:
            return self
        try:
            t.join(timeout=max(0.0, min(3.0, float(timeout or 0.0))))
        except Exception:
            pass
        return self

    @property
    def running(self):
        t = self._thread
        return bool(t is not None and t.is_alive())

    @property
    def busy(self):
        return self._busy

    @property
    def last_text(self):
        return self._last_text

    # ---- 主循环 ----
    def _loop(self):
        while not self._stop_flag.is_set():
            if not self._still_recording():
                break
            if self._busy:
                # 上一轮还没回来：不排队，等一小会儿再看
                self._sleep(0.2)
                continue
            try:
                attempted = self._recognize_once()
            except Exception as e:      # 单轮失败绝不能让线程死掉
                self._report_error(e)
                attempted = False
            if self._stop_flag.is_set():
                break
            self._sleep(self._interval_for(self._last_duration) if attempted else 0.25)

    def _recognize_once(self):
        """做一轮：尾部窗口快照 -> 增长判断 -> 累计前缀 -> 识别 -> 回调。

        返回是否真的发了请求。★ 每轮识别的音频恒为最近 window 秒（不够则
        取全部），单轮计算量与录音总长无关 —— 这是「根治卡顿」的核心。
        """
        if self._busy:                 # ★ 绝不并发识别
            return False
        path = None
        try:
            path = self._snapshot_window()
        except ASRError:
            # 刚开始录音、还没攒到音频：正常情况，等下一轮，不算错误
            return False
        try:
            win_dur = wav_duration(path)
            if win_dur <= 0:
                return False
            total = self._total_seconds(win_dur)
            if total - self._last_duration < self.MIN_GROWTH:
                return False            # 没有新内容，跳过这一轮
            if self.max_window > 0 and total > self.max_window:
                # 太长了：增量识别的性价比已经很低，收工等整段识别
                self._stop_flag.set()
                return False
            win_start = max(0.0, total - win_dur)
            self._commit_prefix(win_start)
            self._last_duration = total
            self._busy = True
            try:
                text, _backend = transcribe(
                    path, self._keys(), self.prefer, timeout=self.REQUEST_TIMEOUT)
            finally:
                self._busy = False
        finally:
            self._cleanup(path)

        preview = (text or "").strip()
        self._prev_start = win_start
        self._prev_end = total
        self._prev_text = preview
        self._prev_commit_index = 0   # 新窗口还没提交过任何字
        combined = self._confirmed_prefix + preview
        if combined and combined != self._last_text and not self._stop_flag.is_set():
            self._last_text = combined
            try:
                if self.on_text is not None:
                    self.on_text(combined)
            except Exception as e:      # 回调异常也不能打断循环
                self._report_error(e)
        return True

    # ---- 滑动窗口：只认最近 window 秒 ----
    def _snapshot_window(self):
        """取最近 window 秒的快照。

        优先用 Recorder.snapshot_wav(prefix, window_seconds) 直接导出尾巴；
        若调用方给的是旧接口 / 测试桩（snapshot_wav 只收 0~1 个参数），
        就退回整段快照，再复用 to_16k_mono() 就地裁剪成同样长度。
        """
        try:
            return self.recorder.snapshot_wav("snap", self.window)
        except TypeError:
            path = self.recorder.snapshot_wav("snap")
            self._crop_in_place(path, self.window)
            return path

    @staticmethod
    def _crop_in_place(path, window_seconds):
        """旧接口兜底：转成 16k 单声道后只留最后 N 秒，原地覆盖快照文件。"""
        wav16 = to_16k_mono(path)
        try:
            with wave.open(wav16, "rb") as w:
                data = w.readframes(w.getnframes())
            keep = int(RATE * 2 * max(0.1, float(window_seconds or 8.0)))
            keep -= keep % 2
            if 0 < keep < len(data):
                data = data[-keep:]
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(data)
        finally:
            if wav16 != path:
                try:
                    if os.path.exists(wav16):
                        os.remove(wav16)
                except OSError:
                    pass

    def _total_seconds(self, win_dur):
        """录音总时长（秒）。

        优先用 Recorder.recorded_seconds（按帧数算出的真实音频长度），
        没有该属性的旧实现 / 测试桩退回墙钟 seconds，最后再退回整段快照实测。
        """
        try:
            total = float(getattr(self.recorder, "recorded_seconds", 0.0) or 0.0)
        except (TypeError, ValueError):
            total = 0.0
        if total <= 0:
            try:
                total = float(getattr(self.recorder, "seconds", 0.0) or 0.0)
            except (TypeError, ValueError):
                total = 0.0
        if total <= 0:
            # 兼容没有 seconds 的测试桩：量一次整段快照（只写盘，不跑模型）
            p = None
            try:
                try:
                    p = self.recorder.snapshot_wav("dur")
                except TypeError:
                    p = self.recorder.snapshot_wav()
                total = wav_duration(p)
            except Exception:
                total = 0.0
            finally:
                StreamingSession._cleanup(p)
        return max(win_dur, total)

    # ---- 已确认前缀 ----
    @property
    def confirmed_prefix(self):
        """已经滚出窗口、被累计下来的文字（测试 / 调试可读）。"""
        return self._confirmed_prefix

    def _commit_prefix(self, win_start):
        """把已经滚出本轮窗口的音频累计成「已确认前缀」。

        切点按上一轮文本里的**标点位置**就近吸附 —— 那些标点正是
        asr_punct.restore() 按停顿 / 语义（静音边界）插进去的，
        因此不会把一个词劈成两半。前缀覆盖到 win_start，本轮窗口
        从 win_start 开始，两者不重不漏。

        ★ 窗口是重叠的：必须只提交「上一次已提交位置 → 本次该提交位置」
          之间的新字，否则同一句话会被反复追加进前缀（踩过这个坑）。
        """
        if win_start <= self._committed_end + 0.05:
            return
        prev = self._prev_text
        span = self._prev_end - self._prev_start
        if not prev or span <= 0:
            # 还没有可确认的文本：宁可让预览暂时重复，也不能吞掉已说过的话
            return
        target = min(win_start, self._prev_end)
        frac = max(0.0, min(1.0, (target - self._prev_start) / span))
        k_hi = int(round(len(prev) * frac))
        k_hi = max(0, min(len(prev), self._snap_to_boundary(prev, k_hi)))
        k_lo = max(0, min(len(prev), self._prev_commit_index))
        if k_hi > k_lo:
            self._confirmed_prefix += prev[k_lo:k_hi]
        self._prev_commit_index = k_hi
        self._committed_end = max(self._committed_end, target)

    @staticmethod
    def _snap_to_boundary(text, k):
        """把切点吸附到最近的标点之后（标点即静音边界）；没有就原样返回。"""
        from . import asr_punct
        if not text:
            return k
        tolerance = max(2, len(text) // 8)
        best = None
        for i, ch in enumerate(text):
            if ch not in asr_punct.PUNCT_CHARS:
                continue
            pos = i + 1                  # 标点之后才是干净的断点
            if pos >= len(text):
                continue
            dist = abs(pos - k)
            if dist <= tolerance and (best is None or dist < best[0]):
                best = (dist, pos)
        return best[1] if best else k

    # ---- 小工具 ----
    def _keys(self):
        try:
            return self._keys_provider() or {}
        except Exception:
            return {}

    def _still_recording(self):
        rec = self.recorder
        if rec is None:
            return False
        flag = getattr(rec, "recording", None)
        if flag is None:
            # 兼容没有 recording 属性的 recorder（测试桩/旧实现）
            return not bool(getattr(rec, "auto_stopped", False))
        return bool(flag)

    def _interval_for(self, duration):
        """自适应间隔（v9.22.11）：短句跟得紧，长句让开 CPU。

        <8s 用 1.2s、8~20s 用 2.5s、>20s 用 4s。旧版要等到 >20s 才
        升到 3s，前 20 秒每 1.6s 就全量重识别一次，正是卡顿的起点。
        caller 显式传了更短的 interval 时仍然尊重它。
        """
        if duration < 8.0:
            return max(0.5, min(float(self.base_interval or 1.2), 1.2))
        if duration < 20.0:
            return 2.5
        return 4.0

    def _sleep(self, seconds):
        """可被 stop() 立刻打断的睡眠（分片等待，最长 0.2s 醒一次）。"""
        end = time.time() + max(0.0, float(seconds or 0.0))
        while not self._stop_flag.is_set():
            left = end - time.time()
            if left <= 0:
                return
            self._stop_flag.wait(min(0.2, left))

    def _report_error(self, err):
        """只在第一次出错时回调，避免网络抖动把界面刷满。"""
        if self._error_reported:
            return
        self._error_reported = True
        try:
            if self.on_error is not None:
                self.on_error(str(err))
        except Exception:
            pass

    @staticmethod
    def _cleanup(path):
        """删掉本轮快照，以及 to_16k_mono() 在快照旁边生成的 *_16k.wav。"""
        if not path:
            return
        for p in (path, os.path.splitext(path)[0] + "_16k.wav"):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


# ------------------------------------------------------------------ 识别后端
def _pick(resp_json):
    """从各家不同的返回结构里取出文本。"""
    if not isinstance(resp_json, dict):
        return ""
    if isinstance(resp_json.get("text"), str):
        return resp_json["text"]
    try:
        return resp_json["choices"][0]["message"]["content"]
    except Exception:
        pass
    for key in ("result", "output", "data"):
        v = resp_json.get(key)
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and isinstance(v.get("text"), str):
            return v["text"]
    return ""


def _asr_local(path, keys, timeout):
    """离线识别：模型在本机跑，不需要任何 Key、不联网。

    模型可能还没下载完（首次使用），这里**等**它 —— 这个函数只会在后台线程里
    被调用（recognize_file_async / StreamingSession），等多久都不会卡界面。
    """
    from . import asr_local, asr_punct
    if not asr_local.installed():
        raise ASRError("本机没有离线语音引擎组件")
    if not asr_local.ready():
        if not asr_local.ensure(timeout=min(300, max(30, timeout * 3))):
            raise ASRError(asr_local.status_text())
    # Paraformer 输出不带标点：用真实停顿位置把「，」「。」补回去
    text, pauses = asr_local.recognize_with_pauses(path)
    return asr_punct.restore(text, pauses)


def _asr_zhipu(path, keys, timeout):
    key = (keys or {}).get("zhipu", "")
    if not key:
        raise ASRError("未配置智谱 Key")
    with open(path, "rb") as f:
        r = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions",
            headers={"Authorization": "Bearer " + key},
            files={"file": (os.path.basename(path), f, "audio/wav")},
            data={"model": "glm-asr-2512", "stream": "false"}, timeout=timeout)
    if r.status_code != 200:
        raise ASRError(f"HTTP {r.status_code} {r.text[:160]}")
    return _pick(r.json())


def _asr_siliconflow(path, keys, timeout):
    key = (keys or {}).get("siliconflow", "")
    if not key:
        raise ASRError("未配置硅基流动 Key")
    with open(path, "rb") as f:
        r = requests.post(
            "https://api.siliconflow.cn/v1/audio/transcriptions",
            headers={"Authorization": "Bearer " + key},
            files={"file": (os.path.basename(path), f, "audio/wav")},
            data={"model": "FunAudioLLM/SenseVoiceSmall"}, timeout=timeout)
    if r.status_code != 200:
        raise ASRError(f"HTTP {r.status_code} {r.text[:160]}")
    return _pick(r.json())


def _dashscope_upload(path, key, timeout):
    h = {"Authorization": "Bearer " + key, "X-DashScope-OssResourceResolve": "enable"}
    r = requests.get("https://dashscope.aliyuncs.com/api/v1/uploads", headers=h,
                     params={"action": "getPolicy", "model": "paraformer-v2"},
                     timeout=timeout)
    if r.status_code != 200:
        raise ASRError(f"getPolicy HTTP {r.status_code}")
    pol = r.json()["data"]
    with open(path, "rb") as f:
        up = requests.post(pol["upload_host"], data={
            "OSSAccessKeyId": pol["oss_access_key_id"], "policy": pol["policy"],
            "Signature": pol["signature"], "key": pol["upload_dir"],
            "x-oss-object-acl": pol["x_oss_object_acl"],
            "x-oss-forbid-overwrite": pol["x_oss_forbid_overwrite"],
            "success_action_status": "200"},
            files={"file": (os.path.basename(path), f, "audio/wav")}, timeout=timeout * 2)
    if up.status_code not in (200, 203, 204):
        raise ASRError(f"上传失败 HTTP {up.status_code}")
    return pol["upload_dir"]


def _asr_dashscope(path, keys, timeout):
    key = (keys or {}).get("dashscope", "")
    if not key:
        raise ASRError("未配置百炼 Key")
    oss_key = _dashscope_upload(path, key, timeout)
    h = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
         "X-DashScope-Async": "enable"}
    r = requests.post(
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription",
        headers=h, json={"model": "paraformer-v2",
                         "input": {"file_urls": ["oss://" + oss_key]},
                         "parameters": {"language_hints": ["zh"]}}, timeout=timeout)
    if r.status_code != 200:
        raise ASRError(f"提交失败 HTTP {r.status_code} {r.text[:140]}")
    tid = r.json()["output"]["task_id"]
    deadline = time.time() + max(timeout, 60)
    poll_h = {"Authorization": "Bearer " + key, "X-DashScope-OssResourceResolve": "enable"}
    while time.time() < deadline:
        j = requests.get("https://dashscope.aliyuncs.com/api/v1/tasks/" + tid,
                         headers=poll_h, timeout=timeout).json()
        st = j.get("output", {}).get("task_status")
        if st == "SUCCEEDED":
            url = j["output"]["results"][0].get("transcription_url")
            if not url:
                return ""
            t = requests.get(url, timeout=timeout).json()
            parts = []
            for tr in t.get("transcripts", []):
                if tr.get("text"):
                    parts.append(tr["text"])
            return "\n".join(parts).strip()
        if st == "FAILED":
            raise ASRError("转写失败：" + str(j.get("output", {}).get("code")))
        time.sleep(2)
    raise ASRError("转写超时")


BACKENDS = [
    # ★ 离线排第一：不依赖任何账户余额，断网 / 换机器都能用
    ("local", "离线识别（本机）", _asr_local),
    ("zhipu", "智谱 GLM-ASR", _asr_zhipu),
    ("siliconflow", "硅基流动 SenseVoice", _asr_siliconflow),
    ("dashscope", "百炼 Paraformer", _asr_dashscope),
]

# 后端显示名（设置页用）
BACKEND_LABELS = {
    "auto": "自动（推荐：先用本机离线，再试云端）",
    "local": "离线识别（本机跑模型，不联网、不要 Key、永久免费）",
    "zhipu": "智谱 GLM-ASR-2512（付费产品，需账户余额）",
    "siliconflow": "硅基流动 SenseVoiceSmall（需账户余额）",
    "dashscope": "阿里百炼 Paraformer-v2（需开通 ASR）",
}


def available_backends(keys):
    """哪些后端当前真的能用。

    local 不需要 Key —— 只要引擎和模型都在就算「能用」。
    """
    out = []
    from . import asr_local
    if asr_local.ready():
        out.append("local")
    for name, label, _ in BACKENDS:
        if name == "local":
            continue
        if (keys or {}).get(name):
            out.append(name)
    return out


def transcribe(path, keys, prefer="auto", timeout=90):
    """识别一段音频。返回 (文本, 后端名)。失败抛 ASRError。"""
    if not os.path.exists(path):
        raise ASRError("音频文件不存在")
    if wav_duration(path) < 0.2:
        raise ASRError("录音太短了")
    # 统一格式（各后端都要 16k 单声道）
    try:
        wav16 = to_16k_mono(path)
    except Exception:
        wav16 = path

    order = BACKENDS
    if prefer and prefer != "auto":
        order = sorted(BACKENDS, key=lambda b: 0 if b[0] == prefer else 1)

    errors = []
    for name, label, fn in order:
        # ★ local 不需要 Key；其余三家必须有 Key 才试
        if name != "local" and not (keys or {}).get(name):
            continue
        try:
            text = fn(wav16, keys, timeout)
            if text and text.strip():
                final = text.strip()
                # ★ v9.22.11 统一兜底：一个标点都没有时补逗号/句号。
                #   云端后端自带标点的文本一个字符都不动，别把人家的结果改坏。
                from . import asr_punct
                if not asr_punct.has_punctuation(final):
                    final = asr_punct.restore(final)
                return final, label
            errors.append(f"{label}：返回空结果")
        except Exception as e:
            errors.append(f"{label}：{e}")
    if not errors:
        raise ASRError("没有可用的语音识别后端——请在「设置 → 语音」里填一个 API Key"
                       "（智谱 Key 即可直接语音输入）。")
    joined = "\n".join(errors)
    low = joined.lower()
    # ★ v9.22.10：把「账户欠费」翻译成用户看得懂、能自己解决的话。
    #   实测本机三个后端同时挂：智谱 429「余额不足或无可用资源包」、
    #   硅基流动 402「balance is insufficient」、百炼 SERVER_ERROR。
    #   以前只把原始 HTTP 报文甩出来，用户以为是「语音模型坏了」。
    if ("余额" in joined or "insufficient" in low
            or " 429" in joined or " 402" in joined):
        raise ASRError(
            "语音识别用不了：云端的几个识别服务账户额度都用完了。\n"
            "（智谱免费的是文本模型；语音转写是另外按量计费的付费产品，"
            "余额为 0 就会返回「余额不足」。）\n"
            "本程序已经内置「离线识别」，不花钱也不用 Key —— 只是模型还没下好。\n"
            "怎么恢复：点一下麦克风，让它把离线模型下完（约 78 MB，只下一次），"
            "之后断网也能用。\n\n"
            "原始信息：\n" + joined)
    raise ASRError("语音识别失败：\n" + joined)


def recognize_file_async(path, keys, prefer, on_done, on_error):
    """在后台线程里识别，避免卡住界面。"""

    def _work():
        try:
            text, backend = transcribe(path, keys, prefer)
            on_done(text, backend)
        except Exception as e:
            on_error(str(e))

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    return t
