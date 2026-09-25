# tests/ —— 本机一次性调试脚本（不进 Git 仓库）

这个目录里的东西全是开发过程中**现写现跑的一次性探针**：验证某个接口通不通、
复现某个 bug、抓一段真实返回。它们会带上本机绝对路径、真实调试输出、
甚至临时密钥，所以整个目录已经在 `.gitignore` 里被排除，不会推到公开仓库。

需要**可复用的工具**请放 `tools/`（会进仓库），例如：

- `tools/privacy_scan.py` —— 推送前体检：明文密钥 / token / 真实姓名 / 本机路径
- `tools/privacy_scan_staged.py` —— 只扫 `git diff --cached`（即将提交）的文件
- `tools/fix_privacy.py` —— 把硬编码的本机路径、用户名、真实姓名改回动态/泛化写法
- `tools/gen_keyblobs.py` —— 生成 `app/_keyblobs.py`（密文，同样不进仓库）

跑法（都在项目根目录）：

```
python tools/privacy_scan.py                 # 扫全仓
python tools/privacy_scan_staged.py          # 只扫待提交文件
set PRIVACY_REAL_NAME=你的名字                # 可选：让扫描也查你的真实姓名
```
