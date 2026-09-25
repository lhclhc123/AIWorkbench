# -*- coding: utf-8 -*-
"""全局热键（Windows）。

为什么不用键盘钩子（SetWindowsHookEx）：
- 钩子会被安全软件（火绒等）当成可疑行为告警甚至拦截；
- RegisterHotKey 是微软官方的公开接口，只注册自己关心的组合键，干净得多。

实现要点：
- 热键消息只能投递到「创建它的那个线程」的消息队列，所以单开一个线程，
  在里面建一个 message-only 窗口（HWND_MESSAGE），注册热键，然后自己抽消息；
- 不占用主线程的 Qt 事件循环，也不影响界面响应；
- 触发时回调（一般会 emit 一个 Qt 信号，Qt 的信号跨线程是安全的）。
"""
import ctypes
import os
import threading
from ctypes import wintypes

_IS_WIN = os.name == "nt"

# Win32 常量
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_DESTROY = 0x0002
HWND_MESSAGE = -3

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}

_VK_SPECIAL = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B,
    "escape": 0x1B, "tab": 0x09, "backspace": 0x08,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}

if _IS_WIN:
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]


def parse_hotkey(spec):
    """'ctrl+alt+space' -> (modifiers, vk)。无法解析返回 (None, None)。"""
    if not spec:
        return None, None
    mods = 0
    vk = None
    for raw in str(spec).replace(" ", "").split("+"):
        if not raw:
            continue
        key = raw.strip().lower()
        if key in _MODS:
            mods |= _MODS[key]
            continue
        if key in _VK_SPECIAL:
            vk = _VK_SPECIAL[key]
        elif len(key) == 1:
            vk = ord(key.upper())
        else:
            return None, None
    if vk is None or mods == 0:
        return None, None
    return mods | MOD_NOREPEAT, vk


class GlobalHotkey(threading.Thread):
    """在后台线程里注册一个全局热键，按下时调用 callback()。"""

    def __init__(self, spec="ctrl+alt+space", callback=None, name="awb-hotkey"):
        super().__init__(daemon=True, name=name)
        self.spec = spec
        self.callback = callback
        self.registered = False
        self.error = None
        self._mods, self._vk = parse_hotkey(spec)
        self._tid = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._cls_name = f"AIWorkbenchHotkey_{id(self)}"

    @property
    def available(self):
        return _IS_WIN and self._mods is not None and self._vk is not None

    def run(self):
        if not self.available:
            self.error = "不支持的热键写法或非 Windows 系统"
            self._ready.set()
            return
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        # 必须显式声明签名：否则 64 位下 LPARAM 会被当成 32 位整数，
        # 回调里转手给 DefWindowProc 时直接 OverflowError。
        u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
        u32.DefWindowProcW.restype = LRESULT
        u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                       wintypes.UINT, wintypes.UINT]
        u32.RegisterHotKey.restype = wintypes.BOOL
        u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT]
        u32.GetMessageW.restype = ctypes.c_int
        u32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        u32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        u32.DispatchMessageW.restype = LRESULT
        self._tid = k32.GetCurrentThreadId()
        hinst = k32.GetModuleHandleW(None)

        self._wndproc_ref = WNDPROC(self._wndproc)      # 必须持引用，否则会崩
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = self._cls_name
        if not u32.RegisterClassExW(ctypes.byref(wc)):
            self.error = f"注册窗口类失败（错误 {k32.GetLastError()}）"
            self._ready.set()
            return

        u32.CreateWindowExW.restype = wintypes.HWND
        u32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        hwnd = u32.CreateWindowExW(0, self._cls_name, "AIWorkbenchHotkey", 0,
                                   0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE), None,
                                   hinst, None)
        if not hwnd:
            self.error = f"创建隐藏窗口失败（错误 {k32.GetLastError()}）"
            self._ready.set()
            return
        self._hwnd = hwnd

        if not u32.RegisterHotKey(hwnd, 1, self._mods, self._vk):
            self.error = (f"热键 {self.spec} 被占用或被系统拒绝"
                          f"（错误 {k32.GetLastError()}）")
            u32.DestroyWindow(hwnd)
            self._ready.set()
            return
        self.registered = True
        self._ready.set()

        msg = MSG()
        try:
            while not self._stop.is_set():
                r = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r in (0, -1):
                    break
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                u32.UnregisterHotKey(hwnd, 1)
            except Exception:
                pass
            try:
                u32.DestroyWindow(hwnd)
            except Exception:
                pass
            self.registered = False

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY:
            try:
                if self.callback:
                    self.callback()
            except Exception:
                pass
            return 0
        if msg == WM_DESTROY:
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return ctypes.windll.user32.DefWindowProcW(
            hwnd, msg, wparam, ctypes.c_ssize_t(lparam))

    def start_and_wait(self, timeout=3.0):
        """启动并等注册结果（这样界面能马上知道热键能不能用）。"""
        self.start()
        self._ready.wait(timeout)
        return self.registered

    def stop(self):
        """注意：跨线程不能直接停消息循环，用 PostThreadMessage 唤醒。"""
        self._stop.set()
        if self._tid and _IS_WIN:
            try:
                WM_QUIT = 0x0012
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
