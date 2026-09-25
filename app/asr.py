# -*- coding: utf-8 -*-
"""语音输入：麦克风录音 + 多后端语音识别（全免费）。

后端（按优先级自动尝试）：
  1. zhipu       智谱 glm-asr-2512 —— 同步 multipart，用已有的智谱 Key（实测可用）
  2. siliconflow 硅基流动 SenseVoiceSmall —— 注册即送额度，中文准确率高
  3. dashscope   阿里百炼 paraformer-v2 —— 先传 OSS 再异步转写（需账号已开通 ASR）

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
                 silence_seconds=3.0, min_seconds=1.0, max_seconds=300):
        """silence_seconds：静音多久算"说完了"（默认 3 秒，别太短，否则想词时被掐）。
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
    ("zhipu", "智谱 GLM-ASR", _asr_zhipu),
    ("siliconflow", "硅基流动 SenseVoice", _asr_siliconflow),
    ("dashscope", "百炼 Paraformer", _asr_dashscope),
]

# 后端显示名（设置页用）
BACKEND_LABELS = {
    "auto": "自动（推荐）",
    "zhipu": "智谱 GLM-ASR-2512（用现有智谱 Key）",
    "siliconflow": "硅基流动 SenseVoiceSmall（免费注册）",
    "dashscope": "阿里百炼 Paraformer-v2（需开通 ASR）",
}


def available_backends(keys):
    """哪些后端当前真的能用（有 Key）。"""
    out = []
    for name, label, _ in BACKENDS:
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
        if not (keys or {}).get(name):
            continue
        try:
            text = fn(wav16, keys, timeout)
            if text and text.strip():
                return text.strip(), label
            errors.append(f"{label}：返回空结果")
        except Exception as e:
            errors.append(f"{label}：{e}")
    if not errors:
        raise ASRError("没有可用的语音识别后端——请在「设置 → 语音」里填一个 API Key"
                       "（智谱 Key 即可直接语音输入）。")
    raise ASRError("语音识别失败：\n" + "\n".join(errors))


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
