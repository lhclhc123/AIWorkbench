# -*- coding: utf-8 -*-
"""对「即将进仓库的文件」做一次隐私/密钥扫描（含 tests 目录，privacy_scan 默认跳过它）。

用法：
    python tests/privacy_scan_staged.py            # 扫 git 已暂存的文件
    python tests/privacy_scan_staged.py --all      # 扫整个工作区文本文件
"""
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 个人信息一律从环境变量取，脚本本身不含任何真实姓名/用户名
# （因为这两个脚本是要进公开仓库的）
_REAL_NAME = (os.environ.get("PRIVACY_REAL_NAME") or "").strip()
_WIN_USER = (os.environ.get("PRIVACY_WIN_USER")
             or os.environ.get("USERNAME") or "").strip()

PATTERNS = [
    ("钉钉凭据", re.compile(r"AppSecret|app_secret|access_token\s*[=:]|accessToken")),
    ("OpenAI 风格密钥", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("通用 API Key", re.compile(r"(?i)\b(api[_-]?key|apikey)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}")),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[\w.\-]+@[\w\-]+\.[a-z]{2,}")),
    ("密钥密文 blob", re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")),
    ("本机工作区路径", re.compile(r"[A-Za-z]:\\软件\\WorkBuddy工作空间")),
]
if _REAL_NAME:
    PATTERNS.insert(0, ("真实姓名", re.compile(re.escape(_REAL_NAME))))
if _WIN_USER:
    win = re.escape(_WIN_USER)
    PATTERNS.append(("用户目录", re.compile(
        r"C:\\Users\\" + win + r"|/c/Users/" + win)))

SKIP_DIRS = {".git", "__pycache__", "build", "dist_v9", "dist_v93", "node_modules"}
TEXT_EXT = {".py", ".txt", ".md", ".json", ".yml", ".yaml", ".iss", ".ps1",
            ".spec", ".cfg", ".toml", ".ini", ".bat", ".log"}


def staged_files():
    try:
        out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                             cwd=ROOT, capture_output=True, text=True)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def all_files():
    res = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if os.path.splitext(f)[1].lower() in TEXT_EXT:
                res.append(os.path.relpath(os.path.join(dp, f), ROOT))
    return res


def main():
    files = all_files() if "--all" in sys.argv else staged_files()
    hits = []
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        try:
            s = io.open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for i, line in enumerate(s.splitlines(), 1):
            for label, rx in PATTERNS:
                if rx.search(line):
                    hits.append((label, rel, i, line.strip()[:90]))
                    break
    if not hits:
        print(f"✅ 干净：{len(files)} 个文件没有发现隐私/密钥问题")
        return 0
    print(f"⚠️ 扫描 {len(files)} 个文件，发现 {len(hits)} 处：\n")
    by = {}
    for label, rel, i, line in hits:
        by.setdefault(label, []).append((rel, i, line))
    for label in sorted(by):
        print(f"【{label}】{len(by[label])} 处")
        for rel, i, line in by[label][:12]:
            print(f"   {rel}:{i}  {line}")
        if len(by[label]) > 12:
            print(f"   …还有 {len(by[label]) - 12} 处")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
