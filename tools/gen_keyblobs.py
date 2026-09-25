# -*- coding: utf-8 -*-
"""生成/更新内嵌密钥密文（app/_keyblobs.py），让源码与仓库里不出现明文 Key。

三种用法：

1) 从环境变量注入（CI / GitHub Actions 用，密钥放 Secrets 里，不落源码）：
       python tools/gen_keyblobs.py --from-env
   读 AIWB_KEY_ZHIPU / AIWB_KEY_DASHSCOPE / AIWB_KEY_SCNET / AIWB_KEY_BAIDU

2) 从本地 JSON 注入（自己机器上一次性配置）：
       python tools/gen_keyblobs.py --from-json local_keys.json
   JSON 形如 {"zhipu": "xxxx", "dashscope": "sk-..."}

3) 不带参数：只打印当前 app/_keyblobs.py 的密文，便于手工比对。

生成的文件 app/_keyblobs.py 会被 .gitignore 忽略；
config.py 会在存在时自动读取它并覆盖内置密文。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import security as sec      # noqa: E402

TARGET = os.path.join(ROOT, "app", "_keyblobs.py")
ENV_MAP = {
    "zhipu": "AIWB_KEY_ZHIPU",
    "dashscope": "AIWB_KEY_DASHSCOPE",
    "scnet": "AIWB_KEY_SCNET",
    "baidu": "AIWB_KEY_BAIDU",
    "deepseek": "AIWB_KEY_DEEPSEEK",
    "siliconflow": "AIWB_KEY_SILICONFLOW",
}

HEAD = '''# -*- coding: utf-8 -*-
"""本文件由 tools/gen_keyblobs.py 生成，存放「内嵌密钥的混淆密文」。

- 里面**没有明文**，是 security.obf() 的产物；
- 已被 .gitignore 忽略，不会进仓库；
- 删除本文件程序照样能跑，只是首次启动需要自己填 Key。
"""
BLOBS = {
'''


def write(blobs):
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(HEAD)
        for k, v in blobs.items():
            f.write(f'    "{k}": "{v}",\n')
        f.write("}\n")
    return TARGET


def from_env():
    out = {}
    for name, env in ENV_MAP.items():
        v = (os.environ.get(env) or "").strip()
        if v:
            out[name] = sec.obf(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-env", action="store_true")
    ap.add_argument("--from-json")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    if a.show or (not a.from_env and not a.from_json):
        try:
            from app import config
            print(f"当前 app/config.py 内嵌密文（{len(config._KEY_BLOBS)} 条）：")
            for k, v in config._KEY_BLOBS.items():
                if v:
                    print(f'    "{k}": "{v}",')
        except Exception as e:
            print("读取失败：", e)
        if not a.show:
            print("\n用法：--from-env 或 --from-json local_keys.json")
        return 0

    blobs = {}
    if a.from_env:
        blobs = from_env()
        if not blobs:
            print("环境变量里没找到任何 AIWB_KEY_* ——"
                  "跳过（不生成文件，程序用内置密文或留空）。")
            return 0
    if a.from_json:
        with open(a.from_json, "r", encoding="utf-8") as f:
            raw = json.load(f)
        blobs = {k: sec.obf(str(v)) for k, v in raw.items() if str(v).strip()}

    p = write(blobs)
    print(f"已写入 {p}（{len(blobs)} 条：{', '.join(blobs)}）")
    print("注意：该文件已在 .gitignore 里，不要提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
