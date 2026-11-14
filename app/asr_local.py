# -*- coding: utf-8 -*-
"""离线语音识别：模型在本机跑，不联网、不要 Key、不会有「余额不足」。

为什么要有这个文件（2026-11-14 实测结论，不是猜的）
--------------------------------------------------------------
用户问：「智谱不是免费的吗？」

实测**同一把**智谱 Key（`app/_keyblobs.py` 里那一条）：
  · POST /api/paas/v4/chat/completions   model=glm-4-flash
        → HTTP 200，正常返回「你好！」
  · POST /api/paas/v4/audio/transcriptions model=glm-asr-2512
        → HTTP 429 {"code":"1113","message":"余额不足或无可用资源包,请充值。"}

所以「智谱免费」指的是**文本 / 视觉模型**（glm-4-flash 之类永久免费、不扣余额）；
**语音转写（ASR）是单独计费的付费产品**，账户余额为 0 就直接 429。

另外两家也不是换个 Key 就能好：
  · 硅基流动 → HTTP 402 code=30001 "account balance is insufficient"
  · 阿里百炼 → FILE_DOWNLOAD_FAILED / SERVER_ERROR（还得单独开通 ASR 服务）

结论：只要依赖别人家的账户余额，随时会再断一次。所以这里把模型搬到本机跑。

引擎与实测数据（本机 Python 3.12 / CPU，无 GPU）
--------------------------------------------------------------
sherpa-onnx + 中文 Paraformer 小模型（int8 量化）
  · 体积：model.int8.onnx 81,828,675 B + tokens.txt 75,352 B（约 78 MB）
  · 加载：3.35 s（进程内只加载一次，之后常驻内存）
  · 速度：5.09 s 音频解码 0.20 s（RTF≈0.04，比实时快约 25 倍）
  · 准确率：5.09 s 测试音频 → 「你好这是语音识别的测试一二三四五」（一字不差）
  · 安装包只多 28 MB（sherpa_onnx 的 dll + pyd），模型**不进安装包**，
    首次使用时下载到 %LOCALAPPDATA%\\AIWorkbench\\asr\\，支持断点续传。

对外接口
--------
    installed()          这台机器的程序里有没有装引擎
    ready()              引擎在 + 模型齐 → 真的能识别
    status_text()        给界面用的一句人话（含下载进度）
    ensure_async(cb)     后台准备模型（可重复调用，只会跑一次）
    recognize(path)              WAV(16k/单声道/16bit) → 文本（签名不变）
    recognize_with_pauses(path)  WAV → (文本, 停顿点列表)，供自动加标点
"""
import os
import sys
import time
import wave
import array
import shutil
import threading

__all__ = [
    "MODEL_NAME", "MODEL_BYTES", "installed", "ready", "status_text",
    "progress", "ensure_async", "ensure", "recognize",
    "recognize_with_pauses", "model_dir", "reset_cache", "warm_async",
]

MODEL_NAME = "paraformer-zh-small"

# 文件名 → 精确字节数（用来校验「下完了没有」「有没有下坏」）
MODEL_FILES = (
    ("model.int8.onnx", 81828675),
    ("tokens.txt", 75352),
)
MODEL_BYTES = sum(n for _f, n in MODEL_FILES)

# 国内优先走 hf-mirror（实测 ~1 MB/s），失败再直连 huggingface
MIRRORS = (
    "https://hf-mirror.com/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main/",
    "https://huggingface.co/csukuangfj/sherpa-onnx-paraformer-zh-small-2024-03-09/resolve/main/",
)

_lock = threading.Lock()
_dl_thread = None
_dl_state = {"done": 0, "total": MODEL_BYTES, "msg": "", "error": "",
             "running": False, "ok": False, "t0": 0.0}

_rec = None              # 常驻的 OfflineRecognizer
_rec_lock = threading.Lock()
_rec_failed = ""


# ---------------------------------------------------------------------------
# 路径与状态
# ---------------------------------------------------------------------------
def model_dir():
    """模型存放目录。可用环境变量 AWB_ASR_MODEL_DIR 覆盖（测试用）。"""
    p = os.environ.get("AWB_ASR_MODEL_DIR", "").strip()
    if p:
        return p
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AIWorkbench", "asr", MODEL_NAME)


def _file_ok(path, size):
    try:
        return os.path.getsize(path) == size
    except OSError:
        return False


def installed():
    """引擎装没装（打包时 sherpa_onnx 有没有被带进来）。"""
    global _rec_failed
    if _rec_failed:
        return False
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception as e:                      # 缺 dll / 没装
        _rec_failed = f"{type(e).__name__}: {e}"
        return False


def missing_files():
    """还差哪些文件（空列表 = 模型齐了）。"""
    d = model_dir()
    return [(f, n) for f, n in MODEL_FILES
            if not _file_ok(os.path.join(d, f), n)]


def ready():
    return installed() and not missing_files()


def progress():
    """(已下载字节, 总字节, 百分比)。"""
    d = int(_dl_state["done"])
    t = int(_dl_state["total"]) or MODEL_BYTES
    return d, t, int(min(100, d * 100.0 / t)) if t else 0


def status_text():
    """给界面显示的一句话。"""
    if not installed():
        return "离线语音引擎不可用（程序缺组件）"
    miss = missing_files()
    if not miss:
        return "离线语音已就绪"
    d, t, pct = progress()
    if _dl_state["running"]:
        if d <= 0:
            return "首次使用：正在连接下载服务器…"
        return (f"首次使用：正在下载离线语音模型 {pct}%"
                f"（{d // 1048576}/{max(1, t // 1048576)} MB）")
    if _dl_state["error"]:
        return "离线语音模型下载失败：" + str(_dl_state["error"])[:120]
    return "离线语音模型还没下载（点麦克风会自动下载，约 78 MB，只需一次）"


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------
def _download_one(name, size, dest, on_progress):
    """下载单个文件，带断点续传。成功返回 True。"""
    part = dest + ".part"
    errs = []
    for base in MIRRORS:
        url = base + name
        for attempt in (1, 2, 3):
            have = 0
            try:
                if os.path.exists(part):
                    have = os.path.getsize(part)
                if have > size:                       # 文件坏了，重下
                    os.remove(part)
                    have = 0
                if have == size:
                    os.replace(part, dest)
                    return True
                import requests
                # 连接超时给短一点（10s）：连不上就赶紧换镜像，别让用户干等
                # 读取超时给长一点（60s）：81MB 慢慢流也得给够时间
                headers = {"Range": f"bytes={have}-"} if have else {}
                with requests.get(url, headers=headers, stream=True,
                                  timeout=(10, 60), allow_redirects=True) as r:
                    if r.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {r.status_code}")
                    mode = "ab" if (r.status_code == 206 and have) else "wb"
                    if mode == "wb":
                        have = 0
                    with open(part, mode) as f:
                        for chunk in r.iter_content(262144):
                            if not chunk:
                                continue
                            f.write(chunk)
                            have += len(chunk)
                            if on_progress:
                                on_progress()
                if have == size:
                    os.replace(part, dest)
                    return True
                raise RuntimeError(f"大小不对 {have}/{size}")
            except Exception as e:
                # 每一路失败都留档：最后报错要把所有原因给出来，
                # 否则只会显示"最后一个镜像连不上"，看不出真因（踩过）
                errs.append(f"{url.split('/')[2]}#{attempt}: {type(e).__name__}: "
                            f"{str(e)[:80]}")
                time.sleep(1.0 * attempt)
    raise RuntimeError("; ".join(errs[-6:]) or "下载失败")


def _download_all(on_progress):
    d = model_dir()
    os.makedirs(d, exist_ok=True)
    # 已经有的大文件先算进进度，界面一开始就不是 0%
    base_done = sum(n for f, n in MODEL_FILES
                    if _file_ok(os.path.join(d, f), n))
    _dl_state.update(done=base_done, total=MODEL_BYTES, msg="",
                     error="", running=True, ok=False, t0=time.time())
    for name, size in MODEL_FILES:
        dest = os.path.join(d, name)
        if _file_ok(dest, size):
            continue
        _download_one(name, size, dest, on_progress)
    _dl_state["running"] = False
    _dl_state["ok"] = not missing_files()
    if not _dl_state["ok"]:
        _dl_state["error"] = "文件校验失败"
    else:
        # 下完就在这条后台线程里把模型加载好（实测加载 3.3s），
        # 免得用户第一次识别时白等这 3 秒
        try:
            _get_recognizer()
        except Exception:
            pass


def _tick_progress():
    """统计 .part 的实时字节数（续传与多文件都能算对）。"""
    d = model_dir()
    done = 0
    for name, size in MODEL_FILES:
        p = os.path.join(d, name)
        part = p + ".part"
        if _file_ok(p, size):
            done += size
        elif os.path.exists(part):
            try:
                done += min(size, os.path.getsize(part))
            except OSError:
                pass
    _dl_state["done"] = done


def ensure_async(on_progress=None, force=False):
    """后台准备模型。重复调用只会跑一次；已就绪直接返回 None。

    on_progress(pct:int, text:str) 会被反复调用（下载线程里），
    界面要自己投递回主线程（Qt 用信号）。
    """
    global _dl_thread
    if not installed():
        return None
    if ready():
        _dl_state.update(done=MODEL_BYTES, running=False, ok=True, error="")
        if on_progress:
            try:
                on_progress(100, "离线语音已就绪")
            except Exception:
                pass
        return None
    with _lock:
        t = _dl_thread
        if t is not None and t.is_alive() and not force:
            if on_progress:
                d, tot, pct = progress()
                try:
                    on_progress(pct, status_text())
                except Exception:
                    pass
            return t

        def _cb():
            _tick_progress()
            if on_progress:
                d, tot, pct = progress()
                try:
                    on_progress(pct, status_text())
                except Exception:
                    pass

        def _work():
            try:
                _download_all(_cb)
            except Exception as e:
                _dl_state["running"] = False
                _dl_state["error"] = f"{type(e).__name__}: {e}"
            finally:
                _tick_progress()
                if on_progress:
                    d, tot, pct = progress()
                    try:
                        on_progress(pct, status_text())
                    except Exception:
                        pass

        _dl_thread = threading.Thread(target=_work, name="asr-model-dl",
                                      daemon=True)
        _dl_thread.start()
        return _dl_thread


def ensure(timeout=240, on_progress=None):
    """同步等模型准备好。已在下载就等它；没在下载就开一个。

    返回 True/False，不抛异常（识别那边会把 False 变成人话报错）。
    """
    if ready():
        return True
    if not installed():
        return False
    t = ensure_async(on_progress)
    if t is None:
        return ready()
    try:
        t.join(max(1.0, float(timeout or 1.0)))
    except Exception:
        pass
    return ready()


def warm_async():
    """后台把模型加载进内存（3.3s），让第一次识别不用等。

    模型已经加载过就直接返回 None。
    """
    if _rec is not None or not ready():
        return None

    def _work():
        try:
            _get_recognizer()
        except Exception:
            pass

    t = threading.Thread(target=_work, name="asr-model-warm", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------
def _make_recognizer():
    import sherpa_onnx
    d = model_dir()
    n = os.cpu_count() or 2
    # ★ v9.22.11：离线识别每 1.2~4s 就要跑一轮，线程开多了会把 UI 线程的
    #   CPU 抢光（用户反馈「连续说话时略微卡顿」）。实测 2 线程足够跑满
    #   RTF≈0.04 的小模型，剩下的核全部留给 Qt 主线程。
    num_threads = 1 if n <= 2 else 2
    return sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=os.path.join(d, "model.int8.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=num_threads,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
    )


def _get_recognizer():
    global _rec
    if _rec is not None:
        return _rec
    with _rec_lock:
        if _rec is None:
            _rec = _make_recognizer()
        return _rec


def reset_cache():
    """丢掉已加载的模型（测试用；下次识别会重新加载）。"""
    global _rec
    with _rec_lock:
        _rec = None


def _read_wav_16k(path):
    """读成 float32 单声道 16k。不符合就先转（复用 asr.to_16k_mono）。"""
    with wave.open(path, "rb") as w:
        ch, rate, sw, n = (w.getnchannels(), w.getframerate(),
                           w.getsampwidth(), w.getnframes())
        raw = w.readframes(n)
    if ch != 1 or rate != 16000 or sw != 2:
        from . import asr as _asr
        path = _asr.to_16k_mono(path)
        with wave.open(path, "rb") as w:
            raw = w.readframes(w.getnframes())
    if sys.byteorder == "big":
        a = array.array("h", raw)
        a.byteswap()
        raw = a.tobytes()
    a = array.array("h")
    a.frombytes(raw)
    return [s / 32768.0 for s in a]


def _detect_pauses(samples):
    """按 20ms 帧算 RMS，找出 >300ms 的静音段。

    复用 asr_punct.detect_pause_ratios() 这个纯函数，避免两处各写一份
    能量检测。返回 [(段中心 / 总时长, 停顿秒数), ...]，可直接喂给
    asr_punct.restore(text, pauses)：普通停顿插「，」，长停顿插「。」。
    """
    from . import asr_punct
    out = []
    for ratio, seconds, _is_long in asr_punct.detect_pause_ratios(samples):
        out.append((ratio, seconds))
    return out


def recognize_with_pauses(path):
    """识别一段 WAV，返回 (文本, 停顿点列表)。

    ★ 模型仍只通过 _get_recognizer() 取那一个常驻实例，绝不重复加载；
      停顿检测是纯 Python 的 RMS 计算，开销远小于模型推理。
    模型没准备好时抛 RuntimeError（与 recognize() 一致）。
    """
    if not ready():
        raise RuntimeError(status_text())
    samples = _read_wav_16k(path)
    if not samples:
        return "", []
    pauses = _detect_pauses(samples)
    rec = _get_recognizer()
    stream = rec.create_stream()
    stream.accept_waveform(16000, samples)
    rec.decode_stream(stream)
    try:
        text = (stream.result.text or "").strip()
    except Exception:
        text = ""
    return text, pauses


def recognize(path):
    """识别一段 WAV，返回文本（可能为空串）。

    签名与返回类型保持向后兼容：内部转调 recognize_with_pauses()，
    丢掉停顿信息。模型没准备好时抛 RuntimeError。
    """
    text, _pauses = recognize_with_pauses(path)
    return text
