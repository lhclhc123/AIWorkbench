# -*- coding: utf-8 -*-
r"""已知文件夹（桌面 / 文档 / 下载 / 图片…）别名解析。

为什么需要它：模型和用户说「桌面\周报.docx」「Desktop\\a.txt」时，
那是个**相对路径**，直接 join 工作区就会落到 `files\桌面\...` —— 用户
在真实桌面上当然找不到。这里把常见别名翻译成 Windows 的真实路径。

优先走 Windows 的 Known Folder API（能正确处理 OneDrive 重定向、
盘符搬移等情况）；拿不到就退回 USERPROFILE 下的默认位置。
"""
import ctypes
import os
import sys

# SHGetKnownFolderPath 的 KNOWNFOLDERID
_FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "home": "{5E6C858F-0E22-4760-9AFE-EA3317B67173}",
}

# 中文 / 英文 / 常见简写 -> 上面那个 key
_ALIASES = {
    # 桌面
    "桌面": "desktop", "desktop": "desktop", "桌面上": "desktop",
    "desktop folder": "desktop",
    # 文档
    "文档": "documents", "我的文档": "documents", "documents": "documents",
    "document": "documents", "docs": "documents", "文稿": "documents",
    # 下载
    "下载": "downloads", "下载夹": "downloads", "downloads": "downloads",
    "download": "downloads", "下载目录": "downloads",
    # 图片
    "图片": "pictures", "我的图片": "pictures", "pictures": "pictures",
    "picture": "pictures", "照片": "pictures", "images": "pictures",
    # 音乐
    "音乐": "music", "我的音乐": "music", "music": "music",
    # 视频
    "视频": "videos", "我的视频": "videos", "videos": "videos",
    "video": "videos", "影片": "videos",
    # 用户主目录
    "用户目录": "home", "主目录": "home", "userprofile": "home",
    "用户主目录": "home", "家目录": "home", "home": "home", "~": "home",
}

_CACHE = {}


def _known_folder(name):
    """用 SHGetKnownFolderPath 拿真实路径；失败返回 None。"""
    if sys.platform != "win32":
        return None
    if name in _CACHE:
        return _CACHE[name]
    path = None
    guid = _FOLDER_IDS.get(name)
    if guid:
        try:
            from ctypes import wintypes
            ole32 = ctypes.windll.ole32
            shell32 = ctypes.windll.shell32
            # 需要的导入：CoTaskMemFree 前先声明
            class GUID(ctypes.Structure):
                _fields_ = [("Data1", ctypes.c_ulong),
                            ("Data2", ctypes.c_ushort),
                            ("Data3", ctypes.c_ushort),
                            ("Data4", ctypes.c_ubyte * 8)]

                def __init__(self, text):
                    super().__init__()
                    ole32.CLSIDFromString(
                        ctypes.c_wchar_p(text), ctypes.byref(self))

            fid = GUID(guid)
            buf = ctypes.c_wchar_p()
            SHGetKnownFolderPath = shell32.SHGetKnownFolderPath
            SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID),
                                             wintypes.DWORD,
                                             wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
            SHGetKnownFolderPath.restype = ctypes.c_long
            hr = SHGetKnownFolderPath(ctypes.byref(fid), 0, None,
                                      ctypes.byref(buf))
            if hr == 0 and buf.value:
                path = buf.value
            try:
                ole32.CoTaskMemFree(buf)
            except Exception:
                pass
        except Exception:
            path = None
    if not path:
        home = os.path.expanduser("~")
        fallback = {
            "desktop": "Desktop", "documents": "Documents",
            "downloads": "Downloads", "pictures": "Pictures",
            "music": "Music", "videos": "Videos",
        }
        if name == "home":
            path = home
        elif name in fallback:
            path = os.path.join(home, fallback[name])
        # 中文系统上可能是中文目录名；两个都试
        if name in fallback and not os.path.isdir(path or ""):
            zh = {"desktop": "桌面", "documents": "文档", "downloads": "下载",
                  "pictures": "图片", "music": "音乐", "videos": "视频"}
            alt = os.path.join(home, zh.get(name, ""))
            if os.path.isdir(alt):
                path = alt
    _CACHE[name] = path
    return path


def known_folders():
    """返回 {别名key: 真实路径}（只包含真存在的）。"""
    out = {}
    for key in _FOLDER_IDS:
        p = _known_folder(key)
        if p and os.path.isdir(p):
            out[key] = p
    return out


def split_alias(raw):
    """把 '桌面\\a\\b.txt' 拆成 ('desktop', 'a\\b.txt')；不是别名返回 (None, raw)。

    只认**第一段**是别名的情况，避免把 'my桌面\\x' 这种误判。
    """
    s = (raw or "").strip()
    if not s:
        return None, raw
    # 统一分隔符来切第一段，但保留原始剩余部分
    norm = s.replace("/", "\\")
    # 去掉盘符前缀的可能性（C:\桌面 这种不走别名）
    if len(norm) >= 2 and norm[1] == ":":
        return None, raw
    if norm.startswith("\\"):
        return None, raw
    first, sep, rest = norm.partition("\\")
    key = _ALIASES.get(first.strip().lower())
    if not key:
        return None, raw
    base = _known_folder(key)
    if not base:
        return None, raw
    return base, rest


def resolve(raw):
    """把带别名的路径换成绝对路径；不是别名则原样返回。

    例：
      桌面\\周报.docx          -> C:\\Users\\xxx\\Desktop\\周报.docx
      desktop/a.txt            -> C:\\Users\\xxx\\Desktop\\a.txt
      下载\\x.zip\\            -> C:\\Users\\xxx\\Downloads\\x.zip
      out\\a.txt               -> out\\a.txt（原样，交给工作区处理）
    """
    base, rest = split_alias(raw)
    if not base:
        return raw
    rest = (rest or "").strip()
    if not rest:
        return base
    return os.path.normpath(os.path.join(base, rest))


def is_alias(raw):
    base, _ = split_alias(raw)
    return bool(base)


def hint_text():
    """给系统提示词用：告诉模型这些别名会落到哪儿。"""
    parts = []
    for key, p in known_folders().items():
        zh = {"desktop": "桌面", "documents": "文档", "downloads": "下载",
              "pictures": "图片", "music": "音乐", "videos": "视频",
              "home": "用户目录"}.get(key, key)
        parts.append(f"{zh}={p}")
    return "；".join(parts)
