# -*- coding: utf-8 -*-
"""一键把当前版本推到 GitHub 仓库（用户要求：每次更新完都推一遍）。

做的事情（顺序固定，任一步失败就停）：

    1. git add -A
    2. 隐私/密钥扫描（tools/privacy_scan_staged.py，扫已暂存文件）—— 不过就不推
    3. git commit -m "<消息>"
    4. git push origin <分支>
    5. （给了 --tag 时）git tag + push tag；有 gh 且 API 通的话顺手发 Release

为什么要有这个脚本：本机 git.exe 不在 PATH、api.github.com 时通时不通，
每次都手敲一遍容易漏步骤（尤其是"推送前必须跑隐私扫描"这条硬要求）。

用法：
    python tools/push_release.py -m "v9.12.0: 联网规范化 + 语音 10s + 滚动修复"
    python tools/push_release.py -m "..." --tag v9.12.0 --zip dist_v912/AIWorkbench_v9.zip

只推代码不发 Release：不加 --tag 即可。
"""
import argparse
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本机 git 不在 PATH 里（实测），这里显式兜底。
# ⚠️ 不能写死用户名路径 —— 这个脚本是要进公开仓库的（写死会被隐私扫描拦住，
#    2026-09-30 就是这么发现自己把 C:\Users\<真名> 写进脚本的）。
_HOME = os.path.expanduser("~")
GIT_CANDIDATES = [
    *sorted(glob.glob(os.path.join(
        _HOME, ".workbuddy", "binaries", "PortableGit", "versions", "*",
        "cmd", "git.exe"))),
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
]


def find_git():
    for p in GIT_CANDIDATES:
        if os.path.isfile(p):
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, "git.exe")
        if os.path.isfile(p):
            return p
    return "git"


GIT = find_git()


def run(args, check=True, capture=True, cwd=ROOT):
    print("  $ " + " ".join(str(a) for a in args), flush=True)
    r = subprocess.run(args, cwd=cwd, capture_output=capture,
                       text=True, encoding="utf-8", errors="replace")
    if capture:
        out = (r.stdout or "") + (r.stderr or "")
        if out.strip():
            print("    " + out.strip().replace("\n", "\n    "), flush=True)
    if check and r.returncode != 0:
        print(f"\n[失败] 上一步返回码 {r.returncode}，已中止。", flush=True)
        sys.exit(r.returncode or 1)
    return r


def run_retry(args, tries=8, wait=8, label=""):
    """本机代理对 github.com 的 CONNECT 是间歇性 502（实测前 4 次失败、第 5 次成功），
    所以网络类命令必须重试，不能一次失败就放弃。"""
    last = None
    for i in range(1, tries + 1):
        r = subprocess.run(args, cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            print(f"    （{label} 第 {i} 次成功）", flush=True)
            return r
        last = r
        msg = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        print(f"    第 {i} 次失败：{msg[-1] if msg else '?'}", flush=True)
        if i < tries:
            time.sleep(wait)
    print(f"\n[失败] {label} 重试 {tries} 次都没成功。", flush=True)
    if last is not None:
        print(((last.stderr or "") + (last.stdout or "")).strip(), flush=True)
    sys.exit(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", required=True, help="提交信息")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--tag", default="", help="如 v9.12.0；给了就打包成 Release")
    ap.add_argument("--zip", default="", help="Release 附件（zip 路径）")
    ap.add_argument("--repo", default="lhclhc123/AIWorkbench")
    args = ap.parse_args()

    print(f"git = {GIT}\n", flush=True)

    # 1) 暂存
    run([GIT, "add", "-A"])

    # 2) 隐私扫描（硬门槛）
    print("\n=== 推送前隐私扫描 ===", flush=True)
    scan = os.path.join(ROOT, "tools", "privacy_scan_staged.py")
    if os.path.isfile(scan):
        r = subprocess.run([sys.executable, scan], cwd=ROOT)
        if r.returncode != 0:
            print("\n[中止] 隐私扫描未通过，先处理上面列出的问题再推。", flush=True)
            sys.exit(2)
    else:
        print("  （没找到隐私扫描脚本，跳过）", flush=True)

    # 3) 提交
    st = run([GIT, "status", "--porcelain"]).stdout or ""
    if st.strip():
        print("\n=== 提交 ===", flush=True)
        run([GIT, "commit", "-m", args.message])
    else:
        print("\n（没有改动需要提交）", flush=True)

    # 4) 推送（带重试，见 run_retry 注释）
    print("\n=== 推送（带重试）===", flush=True)
    run_retry([GIT, "push", "origin", args.branch], label="git push")

    # 5) 可选：打 tag + Release
    if args.tag:
        print(f"\n=== 打 tag {args.tag} ===", flush=True)
        run([GIT, "tag", "-f", args.tag], check=False)
        run_retry([GIT, "push", "-f", "origin", args.tag], label="push tag")
        print("\n=== 发 Release ===", flush=True)
        gh_args = ["gh", "release", "create", args.tag]
        if args.zip and os.path.isfile(os.path.join(ROOT, args.zip)):
            gh_args.append(args.zip)
        gh_args += ["--repo", args.repo, "--title", f"AI 工作台 {args.tag}",
                    "--notes", args.message]
        run(gh_args, check=False)
        print("\n（gh 若因 api.github.com 不可达而失败，请在网页上手动"
              f"新建 Release：https://github.com/{args.repo}/releases/new）", flush=True)

    print("\n✅ 完成。", flush=True)


if __name__ == "__main__":
    main()
