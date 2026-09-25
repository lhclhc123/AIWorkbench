# -*- coding: utf-8 -*-
"""公开仓库前的隐私清理：把硬编码的本机路径 / 用户名 / 真实姓名替换成动态或泛化写法。

用法：python tests/fix_privacy.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 真实姓名从环境变量取 —— 脚本要进公开仓库，里面不能出现任何真名。
# 用法： set PRIVACY_REAL_NAME=张三 && python tools/fix_privacy.py
N = (os.environ.get("PRIVACY_REAL_NAME") or "").strip()

# (文件, 旧片段, 新片段)
REPLACES = [
    # 1) 构建脚本里的绝对路径 -> 取脚本自身所在目录
    ("build_v5.py",
     'PROJ = r"D:\\软件\\WorkBuddy工作空间\\2026-09-19-22-59-39\\AIWorkbench"',
     "PROJ = os.path.dirname(os.path.abspath(__file__))"),
    ("build_v6.py",
     'PROJ = r"D:\\软件\\WorkBuddy工作空间\\2026-09-19-22-59-39\\AIWorkbench"',
     "PROJ = os.path.dirname(os.path.abspath(__file__))"),
    ("build_v7.py",
     'PROJ = r"D:\\软件\\WorkBuddy工作空间\\2026-09-19-22-59-39\\AIWorkbench"',
     "PROJ = os.path.dirname(os.path.abspath(__file__))"),
    ("build_v8.py",
     'PROJ = r"D:\\软件\\WorkBuddy工作空间\\2026-09-19-22-59-39\\AIWorkbench"',
     "PROJ = os.path.dirname(os.path.abspath(__file__))"),
    ("build_v9.py",
     'PROJ = r"D:\\软件\\WorkBuddy工作空间\\2026-09-19-22-59-39\\AIWorkbench"',
     "PROJ = os.path.dirname(os.path.abspath(__file__))"),

    # 2) 安装脚本里的发布者真实姓名
    ("installer.iss", f"AppPublisher={NAME}", "AppPublisher=AIWorkbench"),

    # 3) 测试用例里的真实姓名
    ("main.py",
     f'mm.auto_candidates("我叫{NAME}，以后都用中文回答我")',
     'mm.auto_candidates("我叫小明，以后都用中文回答我")'),

    # 4) 记忆模块注释里的真实姓名
    ("app/memory.py",
     f"「我叫{NAME}，以后都用中文回答我」。只归一个分类会丢信息，所以",
     "「我叫小明，以后都用中文回答我」。只归一个分类会丢信息，所以"),

    # 5) 钉钉模块注释 / 界面提示里的真实姓名（换成泛化示例）
    ("app/dingtalk.py",
     f"踩过的坑：用户把「{NAME}self」这种昵称填进 userId，发消息直接 400",
     "踩过的坑：把「昵称」当成 userId 填进去（如「张三」），发消息直接 400"),
    ("app/dingtalk.py",
     f'用途：用户经常把「昵称」当成 userId 填进来（如"{NAME}self"），',
     '用途：用户经常把「昵称」当成 userId 填进来（如"张三"），'),
    ("app/gui/pages.py",
     f"⚠️ 接收人必须填 userId，填昵称（如「{NAME}」）会报 staffId 不存在。",
     "⚠️ 接收人必须填 userId，填昵称（如「张三」）会报 staffId 不存在。"),

    # 6) 系统路径护栏里写死的用户目录 -> 运行时按环境变量拼
    ("app/agent.py",
     '_PROG_DIR = r"C:\\Users\\47488\\AppData\\Local\\Programs\\WorkBuddy"',
     '_PROG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""),\n'
     '                         "Programs", "WorkBuddy")'),
]


def _degenerate(old):
    """没给姓名时，替换片段会退化成空壳（如 AppPublisher= 或「」），这种要跳过。"""
    return (not N) and (old.endswith("AppPublisher=") or "「」" in old
                        or "我叫，" in old or '如""' in old)


def main():
    changed, missing = [], []
    for rel, old, new in REPLACES:
        if _degenerate(old):
            continue
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            missing.append(rel)
            continue
        s = io.open(p, encoding="utf-8").read()
        if old in s:
            s = s.replace(old, new)
            io.open(p, "w", encoding="utf-8", newline="").write(s)
            changed.append(rel)
        else:
            missing.append(rel + "  (片段未命中，可能已改过)")
    print("已修改：")
    for c in changed:
        print("  ✔", c)
    if missing:
        print("未处理：")
        for m in missing:
            print("  ·", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
