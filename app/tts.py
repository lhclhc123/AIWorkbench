# -*- coding: utf-8 -*-
"""语音输出（朗读）。

两个引擎：
  sapi    Windows 内置语音合成（win32com），离线、零配置、即装即用；
  edge    Microsoft Edge 在线语音（edge-tts），音色自然得多，但需要联网。

mp3 播放不引入额外依赖：直接用 Windows 的 MCI（winmm.dll）播放。
"""
import os
import re
import tempfile
import threading
import time

from .asr import audio_dir

_IS_WIN = os.name == "nt"

# 朗读时的默认语速/音量（SAPI）
DEFAULT_RATE = 0        # -10 ~ 10
DEFAULT_VOLUME = 100    # 0 ~ 100

# edge-tts 中文音色（免费）
EDGE_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声（推荐）"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声"),
    ("zh-CN-YunxiNeural", "云希 · 男声"),
    ("zh-CN-YunyangNeural", "云扬 · 男声·播报"),
    ("zh-CN-YunjianNeural", "云健 · 男声·沉稳"),
    ("zh-CN-XiaomoNeural", "晓墨 · 女声·情感"),
]
DEFAULT_EDGE_VOICE = EDGE_VOICES[0][0]


def _split_text(text, limit=180):
    """按句子切分，便于边合成边播放、也避免单次文本过长。"""
    text = re.sub(r"```.*?```", "（代码略）", text or "", flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*_>|]+", "", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if not text:
        return []
    if not re.search(r"[。！？!?；;\n]", text):
        text = text + "。"
    chunks = re.split(r"(?<=[。！？!?；;])", text)
    out, cur = [], ""
    for c in chunks:
        c = c.strip()
        if not c:
            continue
        if len(cur) + len(c) <= limit:
            cur += c
        else:
            if cur:
                out.append(cur)
            cur = c
    if cur:
        out.append(cur)
    return out[:40]   # 最多读 40 句，防止把整篇论文读出来


class Speaker:
    """统一的朗读控制器：同一时刻只会有一路声音。"""

    def __init__(self):
        self.engine = "sapi"
        self.edge_voice = DEFAULT_EDGE_VOICE
        self.rate = DEFAULT_RATE
        self.volume = DEFAULT_VOLUME
        self.available_engines = ["sapi"]
        if _has_edge():
            self.available_engines.append("edge")
        self._sapi = None
        self._thread = None
        self._stop = threading.Event()
        self._mci_alias = None

    # ---------------- SAPI（离线）----------------
    def _get_sapi(self):
        if self._sapi is None:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            self._sapi = win32com.client.Dispatch("SAPI.SpVoice")
        return self._sapi

    def _speak_sapi(self, text):
        import pythoncom
        pythoncom.CoInitialize()
        sp = self._get_sapi()
        try:
            sp.Rate = int(self.rate)
            sp.Volume = int(self.volume)
        except Exception:
            pass
        for chunk in _split_text(text):
            if self._stop.is_set():
                break
            sp.Speak(chunk, 0)      # 0 = 同步，读完这句再读下一句（方便中断）
        try:
            sp.Speak("", 3)         # 3 = 清空队列
        except Exception:
            pass

    # ---------------- edge-tts（在线，音色好）----------------
    def _speak_edge(self, text):
        import asyncio
        import edge_tts
        for chunk in _split_text(text):
            if self._stop.is_set():
                break
            path = os.path.join(audio_dir(), f"tts_{int(time.time()*1000)}.mp3")
            try:
                asyncio.run(self._edge_render(chunk, path))
                self._mci_play(path)
            finally:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass

    @staticmethod
    async def _edge_render(text, path):
        import edge_tts
        voice = await _resolve_voice()
        comm = edge_tts.Communicate(text, voice)
        await comm.save(path)

    # ---------------- MCI 播放 mp3 ----------------
    def _mci_play(self, path):
        if not _IS_WIN:
            return
        self._mci_close()
        alias = "awbtts"
        _mci(f'open "{path}" type mpegvideo alias {alias}')
        _mci(f"play {alias} wait")
        self._mci_close()

    def _mci_close(self):
        try:
            _mci("close awbtts")
        except Exception:
            pass

    # ---------------- 对外 ----------------
    def speak(self, text, blocking=False, engine=None):
        """朗读一段文本。blocking=True 时在当前线程读完。"""
        text = (text or "").strip()
        if not text:
            return False
        eng = engine or self.engine
        if eng == "edge" and "edge" not in self.available_engines:
            eng = "sapi"
        self.stop()
        self._stop.clear()

        def _run():
            try:
                if eng == "edge":
                    self._speak_edge(text)
                else:
                    self._speak_sapi(text)
            except Exception:
                # edge 失败就退回 SAPI，保证一定有声音
                if eng == "edge":
                    try:
                        self._speak_sapi(text)
                    except Exception:
                        pass

        if blocking:
            _run()
            return True
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        self._mci_close()
        try:
            if self._sapi is not None:
                self._sapi.Speak("", 3)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    @property
    def speaking(self):
        return bool(self._thread and self._thread.is_alive())


# ------------------------------------------------------------------ 底层小工具
def _mci(command, buf=255):
    """调用 Windows MCI 接口（不需要额外库就能放 mp3）。"""
    import ctypes
    mciSendString = ctypes.windll.winmm.mciSendStringW
    err = ctypes.create_unicode_buffer(buf)
    r = mciSendString(command, err, buf, 0)
    if r:
        raise OSError(f"MCI {r}: {err.value}")
    return err.value


def _has_edge():
    try:
        import edge_tts  # noqa: F401
        return True
    except Exception:
        return False


async def _resolve_voice():
    return DEFAULT_EDGE_VOICE


def list_sapi_voices():
    """列出本机 SAPI 音色（离线可用）。"""
    if not _IS_WIN:
        return []
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        sp = win32com.client.Dispatch("SAPI.SpVoice")
        out = []
        for i in range(sp.GetVoices().Count):
            out.append(sp.GetVoices().Item(i).GetDescription())
        return out
    except Exception:
        return []


def save_wav(text, path=None):
    """用 SAPI 把文本合成为 WAV 文件（离线，供"导出语音"用）。"""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    path = path or os.path.join(audio_dir(), f"say_{int(time.time())}.wav")
    sp = win32com.client.Dispatch("SAPI.SpVoice")
    fs = win32com.client.Dispatch("SAPI.SpFileStream")
    fs.Open(path, 3, False)
    old = sp.AudioOutputStream
    sp.AudioOutputStream = fs
    try:
        sp.Speak(text, 0)
    finally:
        fs.Close()
        sp.AudioOutputStream = old
    return path
