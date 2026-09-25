# -*- coding: utf-8 -*-
"""版本信息。自动更新检查、关于页、自检报告都用这里。"""

APP_NAME = "AI 工作台"
APP_ID = "AIWorkbench"
VERSION = "9.3.0"
VERSION_TUPLE = (9, 3, 0)
BUILD_DATE = "2026-09-25"

# 自动更新检查依赖的 GitHub 仓库（可在设置里改）
GITHUB_REPO = "lhclhc123/AIWorkbench"
GITHUB_USER = "lhclhc123"

RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
LATEST_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"

# 官网 / 反馈入口（关于页用）
HOMEPAGE = f"https://github.com/{GITHUB_REPO}"


def version_tuple_of(text):
    """把 'v9.1.2' / '9.1.2' / '9.1.2-beta' 解析成 (9,1,2)。"""
    if not text:
        return (0,)
    s = str(text).strip().lstrip("vV")
    out = []
    for part in s.split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out) if out else (0,)


def is_newer(remote, current=None):
    """远端版本是否比当前新。"""
    cur = version_tuple_of(current or VERSION)
    rem = version_tuple_of(remote)
    n = max(len(cur), len(rem))
    cur = cur + (0,) * (n - len(cur))
    rem = rem + (0,) * (n - len(rem))
    return rem > cur
