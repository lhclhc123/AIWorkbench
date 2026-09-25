# -*- coding: utf-8 -*-
r"""推送到公开仓库前的隐私/密钥体检。

和 tests/check_nosecrets.py（查打包产物）不同，这个查的是**要提交到 Git 的源文件**：
  1. 明文 / 可还原的 API 密钥（含内置密文块本身）；
  2. GitHub / 各类 token（gho_ / ghp_ / sk- / AKIA…）；
  3. 个人隐私：真实姓名、手机号、邮箱、本机绝对路径（D:\工作、C:\Users\xxx…）；
  4. 工作区运行数据（settings.json / traces / conversations / MEMORY.md）。

用法：python tests/privacy_scan.py            # 扫描仓库
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 这些目录/文件绝不进 Git（与 .gitignore 对齐），扫描时跳过
SKIP_DIRS = {"__pycache__", ".git", "build", "dist", "tests", "tools",
             ".workbuddy", "dist_v5", "dist_v6", "dist_v7", "dist_v8",
             "dist_v9", "dist_v8_prev", "dist_old_v2", "dist_old_v3"}
SKIP_DIR_PREFIX = ("dist_v", "dist_old", "build")
SKIP_FILE_SUFFIX = (".pyc", ".log", ".png", ".jpg", ".zip", ".exe", ".spec.bak")
SKIP_FILES = {"AIWorkbench.spec"}

# 只扫这些后缀的文本文件
TEXT_EXT = (".py", ".md", ".txt", ".json", ".ini", ".iss", ".ps1", ".bat",
            ".yml", ".yaml", ".toml", ".cfg", ".html", ".css", ".js")

TOKEN_RE = re.compile(
    r"(gh[opsu]_[A-Za-z0-9]{20,}"          # GitHub token
    r"|sk-[A-Za-z0-9_\-]{16,}"            # OpenAI 风格
    r"|AKIA[0-9A-Z]{16}"                  # AWS
    r"|AIza[0-9A-Za-z_\-]{30,}"           # Google
    r"|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # 各家 appId/secret
    r")")

# 疑似「打包进代码的密钥密文」：32 位以上的 base64url 长串
BLOB_RE = re.compile(r"[\"']([A-Za-z0-9_\-]{40,})[\"']")

# 真实姓名不写死在脚本里（脚本本身也要能进公开仓库）：
# 想查自己的名字就设环境变量，例如  set PRIVACY_REAL_NAME=张三
_REAL_NAME = os.environ.get("PRIVACY_REAL_NAME", "").strip()

PII_RES = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[\w.\-]+@[\w\-]+\.[A-Za-z]{2,}")),
    ("本机路径-工作区", re.compile(r"[A-Za-z]:\\软件\\WorkBuddy工作空间")),
    ("本机路径-工作", re.compile(r"[A-Za-z]:\\工作\\")),
    ("用户目录", re.compile(r"C:\\Users\\(?!47488\\.workbuddy)[A-Za-z0-9_\u4e00-\u9fa5]+")),
]
if _REAL_NAME:
    PII_RES.insert(0, ("真实姓名", re.compile(re.escape(_REAL_NAME))))

# 允许出现的（不算隐私）：示例/占位/公开域名
PII_ALLOW = ("example.com", "your_", "xxx", "\\Users\\xxx", "user@",
             "127.0.0.1", "github.com", "dingtalk.com", "aliyuncs.com",
             "bigmodel.cn", "siliconflow.cn", "baidu.com", "deepseek.com",
             "qq.com", "weixin.qq.com", "workbuddy.cn")


def iter_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS
                   and not d.startswith(SKIP_DIR_PREFIX)]
        for fn in files:
            if fn in SKIP_FILES or fn.endswith(SKIP_FILE_SUFFIX):
                continue
            if not fn.lower().endswith(TEXT_EXT):
                continue
            yield os.path.join(dirpath, fn)


def real_secret_values():
    """把内存里还原出来的真实 Key 也拿来搜（防明文混进代码）。"""
    out = {}
    try:
        from app import config
        for name, key in (config.DEFAULT_API_KEYS or {}).items():
            key = (key or "").strip()
            if len(key) >= 16:
                out[f"明文Key:{name}"] = key
                out[f"明文Key前缀:{name}"] = key[:24]
    except Exception as e:
        print(f"（读不到运行时密钥，跳过该检查：{e}）")
    return out


def main():
    needles = real_secret_values()
    findings = []
    nfiles = 0
    for fp in iter_files(ROOT):
        rel = os.path.relpath(fp, ROOT)
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception:
            continue
        nfiles += 1
        lines = text.splitlines()

        # 1) 真实密钥是否出现
        for label, val in needles.items():
            if val and val in text:
                for i, ln in enumerate(lines, 1):
                    if val in ln:
                        findings.append((rel, i, label, ln.strip()[:100]))
        # 2) token 形态
        for m in TOKEN_RE.finditer(text):
            ln = text[:m.start()].count("\n") + 1
            findings.append((rel, ln, "疑似Token", m.group(0)[:40]))
        # 3) 长密钥密文块（config.py 的内置 blobs 会命中）
        for m in BLOB_RE.finditer(text):
            v = m.group(1)
            if len(v) >= 40 and not v.startswith(("http", "0" * 10)):
                ln = text[:m.start()].count("\n") + 1
                findings.append((rel, ln, "疑似密钥密文", v[:40] + "…"))
        # 4) 个人隐私
        for label, rx in PII_RES:
            for m in rx.finditer(text):
                s = m.group(0)
                if any(a in s for a in PII_ALLOW):
                    continue
                # 代码里写死的示例路径白名单
                if s in ("C:\\Users\\xxx",):
                    continue
                ln = text[:m.start()].count("\n") + 1
                findings.append((rel, ln, label, s[:80]))

    print(f"扫描了 {nfiles} 个文本文件（跳过 build/dist/tests 等）")
    # 去重
    uniq = []
    seen = set()
    for f in findings:
        k = (f[0], f[1], f[2], f[3])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(f)

    if not uniq:
        print("✅ 没发现密钥 / 隐私泄漏")
        return 0
    by_kind = {}
    for rel, ln, kind, val in uniq:
        by_kind.setdefault(kind, []).append((rel, ln, val))
    print(f"\n⚠️ 发现 {len(uniq)} 处需要处理：")
    for kind, items in sorted(by_kind.items()):
        print(f"\n【{kind}】{len(items)} 处")
        for rel, ln, val in items[:25]:
            print(f"  {rel}:{ln}  {val}")
        if len(items) > 25:
            print(f"  …还有 {len(items) - 25} 处")
    return 1


if __name__ == "__main__":
    sys.exit(main())
