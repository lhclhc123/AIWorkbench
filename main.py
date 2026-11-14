# -*- coding: utf-8 -*-
"""AI 工作台 —— 本地 AI Agent 桌面应用入口。

支持命令行自检：`AIWorkbench.exe --selftest`
（打包版是 --windowed、没有控制台，所以自检结果会同时写一份到
 %TEMP%\\aiworkbench_selftest.txt，便于排查"打包后某个功能报错"。）
"""
import os
import sys
import tempfile
import traceback

# ★ v9.20.0：助理「独立子进程拿回复」必须在 **任何 Qt / 浏览器内核导入之前** 分流。
#   子进程只读一个请求 JSON、调一次模型、写一个响应 JSON 就退出 ——
#   绝不加载 Qt（否则每个子进程多吃 1~2 秒初始化，还可能因为无显示环境弹窗/崩）。
#   主进程把模型调用扔到这里，卡住了就能直接 kill，不拖累界面。
if len(sys.argv) >= 3 and sys.argv[1] == "--im-reply":
    try:
        from app.im import replyproc
        sys.exit(replyproc.worker_main(sys.argv[2]))
    except SystemExit:
        raise
    except Exception:                       # noqa: BLE001
        # 子进程里不能再依赖任何 GUI 日志，只能退回一个失败码
        sys.exit(2)

# ★ v9.18.0 内嵌浏览器宿主：必须在**任何 GUI/内核模块导入之前**把 CDP 调试口打开。
#   QtWebEngine 只在 QApplication 创建时启动 Chromium，环境变量此刻设好就来得及。
#   端口常量只在 app/webengine.py 里维护一份，这里不重复写数字。
#   （注意：这一行要留在 `app.gui.main_window` 等导入语句之前。）
from app.webengine import enable_remote_debugging as _enable_remote_debugging

_enable_remote_debugging()

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from app import workspace as ws_mod
from app.gui.main_window import MainWindow
from app.gui.startup_dialog import StartupDialog

SELFTEST_REPORT = os.path.join(tempfile.gettempdir(), "aiworkbench_selftest.txt")


def selftest():
    """验证冻结包里各项能力是否真的能导入并跑通。返回退出码。"""
    buf = []

    def log(msg=""):
        buf.append(str(msg))
        try:
            if sys.stdout is not None:      # windowed 模式下 stdout 是 None
                print(msg)
        except Exception:
            pass

    log("=" * 56)
    log("AI 工作台 自检")
    log("=" * 56)
    log("frozen: " + str(getattr(sys, "frozen", False)))
    log("executable: " + str(sys.executable))
    log("python: " + sys.version.split()[0])
    log("")

    state = {"ok": 0, "fail": 0}

    def chk(label, fn):
        try:
            extra = fn()
            state["ok"] += 1
            log(f"  [OK]   {label}" + (f"  {extra}" if extra else ""))
        except Exception as e:
            state["fail"] += 1
            log(f"  [FAIL] {label}  -> {type(e).__name__}: {e}")
            log("         " + traceback.format_exc().replace("\n", "\n         "))

    tmp = tempfile.mkdtemp(prefix="aiwb_selftest_")
    files = os.path.join(tmp, "files")
    os.makedirs(files, exist_ok=True)

    def _versions():
        import importlib
        mods = ["PyQt6", "requests", "bs4", "lxml", "docx", "openpyxl",
                "pptx", "reportlab", "pdfminer", "PIL", "psutil",
                "pyperclip", "mss", "markdown", "pygments",
                # v9 新增能力
                "pyaudio", "edge_tts", "dingtalk_stream", "websocket",
                "win32com.client"]
        missing = []
        for m in mods:
            try:
                importlib.import_module(m)
            except Exception:
                missing.append(m)
        if missing:
            raise RuntimeError("缺少依赖：" + ", ".join(missing))
        return f"{len(mods)} 个依赖全部可用"

    # ★ v9.16.0 修正：老版本这里是「手写清单 + 手写数字 26」，
    #   实际列了 27 个、数字也早就对不上，而且漏了 skills/assistant/winproc/
    #   webcap/hotkey/scheduler 以及新增的 taskman/team —— 等于自检从没验证过
    #   这些模块能不能导入。现在改成：清单写全 + 数字动态算 + 再扫一遍做交叉核对，
    #   以后加模块忘了改清单，自检会自己把漏掉的点出来。
    #   注：_keyblobs 是本机密钥密文模块（gitignore，仓库版没有），不列入。
    _APP_MODS = [
        "agent", "asr", "assistant", "config", "dingtalk", "docread",
        "docwrite", "folders", "hotkey", "knowledge", "llm_client",
        "markdown_render", "mcp_client", "memory", "plan", "scheduler",
        "security", "single_instance", "skills", "taskman", "team", "team_presets",
        "themes", "trace",
        "tts", "updater", "version", "webcap", "winproc", "worklog",
        "workspace",
        # ★ v9.22.4：补齐此前“未列入清单”的 8 个模块，否则自检根本没断言它们。
        "devtools", "downloader", "icons", "ratelimit",
        "tools_more", "webengine", "webui_bridge", "webui_host",
        # ★ v9.22.6：增量更新（manifest/diff/patch）。updater 顶层 import，
        #   漏了它冻结版会直接 ModuleNotFoundError。
        "incremental",
        # ★ v9.22.7：崩溃/卡死取证（main.py 最早期 import）
        "crashlog",
        # ★ v9.22.10：离线语音识别（本机跑 sherpa-onnx，不依赖账户余额）。
        #   asr.py 里是函数内 import，但打包时必须真的被带上，所以列进自检。
        "asr_local",
    ]
    _GUI_MODS = [
        "chat_worker", "main_window", "pages", "panels",
        "startup_dialog", "voice_bar", "widgets",
        # ★ v9.22.4：补全（此前只在“未列入清单”里出现，没被自检断言）
        "browser_page", "team_bar",
    ]
    # 历史残留、产品代码零引用、因此不进包（列进来会让冻结版自检误报）。
    # 设置界面早就改成 pages.SettingsPage 了；app/gui/settings_dialog.py 只是早期对话框的遗留文件。
    _IGNORE_MODS = {"app.gui.settings_dialog"}

    def _scan_extra_modules():
        """扫一遍包里还有哪些模块没进上面的清单（扫不动就算了，不误报）。"""
        try:
            import pkgutil
            import app as _pkg
            from app import gui as _gpkg
            known = ({"app." + n for n in _APP_MODS}
                     | {"app.gui." + n for n in _GUI_MODS}
                     | _IGNORE_MODS)
            found = set()
            for _p, prefix in ((_pkg, "app."), (_gpkg, "app.gui.")):
                for mod in pkgutil.iter_modules(_p.__path__):
                    if mod.name.startswith("_") or mod.ispkg:
                        continue
                    found.add(prefix + mod.name)
            return sorted(found - known)
        except Exception:
            return []

    def _app_modules():
        import importlib
        from app import version as _v
        bad = []
        for n in _APP_MODS:
            try:
                importlib.import_module("app." + n)
            except Exception as e:                      # noqa: BLE001
                bad.append(f"{n}: {type(e).__name__}: {e}")
        for n in _GUI_MODS:
            try:
                importlib.import_module("app.gui." + n)
            except Exception as e:                      # noqa: BLE001
                bad.append(f"gui.{n}: {type(e).__name__}: {e}")
        if bad:
            raise RuntimeError("导入失败：" + "; ".join(bad))
        total = len(_APP_MODS) + len(_GUI_MODS)
        extra = _scan_extra_modules()
        tail = f"；另有未列入清单的模块：{', '.join(extra)}" if extra else ""
        return (f"应用模块（v{_v.VERSION} 共 {total} 个）"
                f"全部导入成功{tail}")

    def _seed_keys():
        """内置种子密钥有没有随包带上（决定首次启动能不能直接用免费模型）。"""
        from app import config
        got = {k: v for k, v in (config.DEFAULT_API_KEYS or {}).items() if v}
        if not got:
            # 不算失败（用户可以自己填 Key），但必须说清楚
            return ("未内置（仓库版不含密钥密文）"
                    "——首次启动请在设置里填自己的 Key，"
                    "或用 tools/gen_keyblobs.py 生成本地密文")
        return "内置 " + ", ".join(sorted(got))

    def _security():
        from app import security as sec, config
        for v in ("", "sk-test-中文-1234567890", "gho_abcdef123456"):
            if sec.deobf(sec.obf(v)) != v:
                raise RuntimeError(f"混淆往返失败：{v!r}")
        for k, v in config.DEFAULT_API_KEYS.items():
            if v and not (v.startswith("sk-") or v.startswith("452d")
                          or v.startswith("bce-")):
                raise RuntimeError(f"{k} 解密后不像有效密钥")
        sealed = sec.seal("gho_test_token_value")
        if not sealed.startswith("enc") or sec.unseal(sealed) != "gho_test_token_value":
            raise RuntimeError("密钥封装失败")
        if sec.unseal("plain-text") != "plain-text":
            raise RuntimeError("明文回退失败")
        return ("DPAPI=" + ("可用" if sec.DPAPI_AVAILABLE else "不可用(已降级)")
                + "；密钥混淆+封装+明文回退 全部通过")

    def _asr_tts():
        from app import asr, tts, config
        be = asr.available_backends(config.DEFAULT_API_KEYS)
        if not be:
            raise RuntimeError("没有任何可用的语音识别后端")
        # 用 TTS 生成一段语音，再走一遍 16k 归一化（不联网，验证音频链路）
        wav = tts.save_wav("自检语音，一二三。")
        if not os.path.isfile(wav) or os.path.getsize(wav) < 2000:
            raise RuntimeError("语音合成落盘异常")
        norm = asr.to_16k_mono(wav)
        dur = asr.wav_duration(norm)
        if dur <= 0:
            raise RuntimeError("音频归一化后时长为 0")
        voices = tts.list_sapi_voices()
        spk = tts.Speaker()
        return (f"ASR 后端 {len(be)} 个({','.join(be)})；"
                f"TTS 音色 {len(voices)} 个；引擎 {','.join(spk.available_engines)}；"
                f"音频链路 {dur:.2f}s")

    def _dingtalk_updater():
        from app import dingtalk, updater, version as ver
        cli = dingtalk.DingTalkClient({})
        st = cli.status_text()
        if not st:
            raise RuntimeError("钉钉状态文案为空")
        if not ver.is_newer("v9.0.1", "9.0.0"):
            raise RuntimeError("版本比较逻辑异常")
        if ver.is_newer("9.0.0", "9.0.0"):
            raise RuntimeError("同版本被判为新")
        # 网络更新检查（失败不算致命，离线也要能用）
        try:
            info = updater.check(timeout=8)
            if info.get("ok"):
                net = "已连 GitHub，最新 " + str(info.get("tag"))
            elif info.get("no_release"):
                net = "可达，但仓库还没发 Release"
            else:
                net = "离线/被墙:" + str(info.get("error"))[:40]
        except Exception as e:
            net = "离线:" + type(e).__name__
        return f"钉钉[{cli.mode}] {st[:26]}…；更新检查 {net}"

    def _v9_tools():
        from app import agent as ag
        runner = ag.AgentRunner(files, workspace_path=tmp)
        runner.speak_cb = lambda t, e: f"[成功] 朗读 {len(t)} 字"
        runner.dingtalk_cb = lambda t, ti, aa: ("未配置"[::-1] if ti == "__status__"
                                                else "[成功] 已推送")
        out = []
        for name, a in (("create_chart", {"path": "c_bar.png", "chart": "bar",
                                          "labels": ["A", "B"], "values": [1, 2]}),
                        ("create_chart", {"path": "c_pie.png", "chart": "pie",
                                          "labels": ["甲", "乙"], "values": [3, 7]}),
                        ("speak_text", {"text": "自检"}),
                        ("dingtalk_status", {}),
                        ("http_request", {"url": "https://example.com",
                                          "method": "GET"})):
            r = str(runner.execute({"name": name, "arguments": a}))
            if r.startswith("[错误]") and name not in ("dingtalk_status",):
                raise RuntimeError(f"{name} -> {r[:120]}")
            out.append(f"{name}={'OK' if not r.startswith('[错误]') else 'WARN'}")
        return f"KNOWN_TOOLS={len(ag.KNOWN_TOOLS)}；" + " ".join(out)

    def _docwrite():
        from app import docwrite as dw
        made = []
        for ext in (".docx", ".xlsx", ".pptx", ".pdf"):
            p = os.path.join(files, "selftest" + ext)
            dw.create(p, "# 自检\n\n## 表格\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n",
                      title="自检")
            if not os.path.isfile(p) or os.path.getsize(p) < 500:
                raise RuntimeError(f"{ext} 生成异常")
            made.append(f"{ext}:{os.path.getsize(p)}B")
        return " ".join(made)

    def _docread():
        from app import docread as dr
        p = os.path.join(files, "read.txt")
        with open(p, "w", encoding="utf-8") as f:
            # 内容要够长：知识库索引会跳过不足 20 字的碎片
            f.write("知识库自检内容。\n"
                    "信息学奥赛常用算法包括树的遍历、最近公共祖先 LCA、动态规划等。\n"
                    "这一段文字用来验证文档解析与本地知识库检索是否正常工作。\n"
                    * 3)
        txt = dr.read_document(p)
        if "LCA" not in txt:
            raise RuntimeError("读取结果不含预期内容")
        return f"{len(txt)} 字"

    def _tools():
        from app import agent as ag
        runner = ag.AgentRunner(files, workspace_path=tmp)
        r1 = runner.execute({"name": "now", "arguments": {}})
        if r1.startswith("[错误]"):
            raise RuntimeError(r1)
        r2 = runner.execute({"name": "create_document",
                             "arguments": {"path": "tool.docx",
                                           "content": "# 工具自检\n\n- 正常"}})
        if r2.startswith("[错误]"):
            raise RuntimeError(r2)
        r3 = runner.execute({"name": "remember",
                             "arguments": {"text": "自检写入的记忆"}})
        if r3.startswith("[错误]"):
            raise RuntimeError(r3)
        r4 = runner.execute({"name": "clipboard", "arguments": {"action": "read"}})
        r5 = runner.execute({"name": "screenshot",
                             "arguments": {"path": "shot.png"}})
        r6 = runner.execute({"name": "list_processes", "arguments": {"top": 3}})
        return (f"KNOWN_TOOLS={len(ag.KNOWN_TOOLS)}；"
                f"now/create_document/remember 通过；"
                f"clipboard={'OK' if not r4.startswith('[错误]') else 'FAIL'}；"
                f"screenshot={'OK' if not r5.startswith('[错误]') else 'FAIL'}；"
                f"list_processes={'OK' if not r6.startswith('[错误]') else 'FAIL'}")

    def _memory_plan():
        from app import memory as mm, plan as pm
        store = mm.MemoryStore(tmp)
        store.remember("自检记忆条目", "重要结论")
        if "自检记忆条目" not in store.load_text():
            raise RuntimeError("记忆未写入")
        p = pm.apply_update(None, ["步骤一", "步骤二"])
        if len(p["steps"]) != 2:
            raise RuntimeError("计划未生效")
        return store.summary_text()

    def _knowledge():
        from app import knowledge as kb
        base = kb.KnowledgeBase(tmp)
        st = base.build(files)
        hits = base.search("最近公共祖先 LCA")
        if not hits:
            raise RuntimeError("检索无结果")
        return f"索引 {st['chunks']} 片，检索命中 {len(hits)} 段"

    def _gui():
        # 不显示窗口，仅验证 GUI 类能实例化（离屏）
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        from app import workspace as ws_mod, themes
        from app.gui.main_window import MainWindow
        # ★ v9.22.4：awb:// scheme 必须在 QApplication 创建之前注册，
        #   否则自检走的是“未注册”的降级路径，还会刷
        #   "Please register the custom scheme 'awb' ..." 警告。
        try:
            from app.webui_host import register_scheme as _register_awb
            _register_awb()
        except Exception:
            pass
        # 不能传空列表：argv[0] 缺失时，后面一建 QWebEngineProfile 就静默 exit 127。
        app = QApplication.instance() or QApplication(sys.argv[:1] or ["AIWorkbench"])
        themes.apply(app, "light")
        w = ws_mod.Workspace(tmp).ensure()
        win = MainWindow(w, w.load_settings())
        win.resize(1320, 860)
        pages = win.stack.count()
        for key in ("chat", "memory", "tools", "integrations", "settings", "about"):
            win._open_page(key)
        has_voice = hasattr(win, "voice")
        cards = len(win._cards)
        win.close()
        return (f"页面 {pages} 个；语音条={'有' if has_voice else '无'}；"
                f"欢迎卡片 {cards} 个；侧栏导航全部可切换")

    def _worklog_folders():
        """v9.2 新增：工作总结 / 文件夹别名 / 自动记忆（用户最在意的三件事）。"""
        from app import worklog as wl, folders as fd, memory as mm, agent as ag
        # 1) 工作总结：写一条、读回来、统计
        _, err = wl.append(tmp, source="自检", request="写一份自检文档",
                           tools=["create_document"],
                           files=[{"path": os.path.join(files, "tool.docx"),
                                   "size": 100, "exists": True}],
                           summary="自检通过", ok=True)
        if err:
            raise RuntimeError(err)
        day = wl.read_day(tmp)
        if "写一份自检文档" not in day or "自检通过" not in day:
            raise RuntimeError("工作总结内容不完整")
        stat = wl.today_stats(tmp)
        if stat["total"] < 1:
            raise RuntimeError("today_stats 统计异常")
        # 2) 文件夹别名：桌面必须解析成绝对路径
        desk = fd.resolve("桌面\\自检.txt")
        if not os.path.isabs(desk):
            raise RuntimeError(f"桌面别名没解析成绝对路径：{desk}")
        if fd.resolve("out\\a.txt") != "out\\a.txt":
            raise RuntimeError("普通相对路径被误改")
        # 3) 自动记忆：规则层能认出身份/偏好，闲聊不乱记
        c = mm.auto_candidates("我叫小明，以后都用中文回答我")
        if not any(x[0] == "用户与身份" for x in c):
            raise RuntimeError(f"自动记忆没认出身份：{c}")
        if mm.auto_candidates("请帮我查一下天气怎么样？"):
            raise RuntimeError("闲聊被误记进长期记忆")
        # 4) 假成功检测
        if not ag.claims_wrote_file("我已经在桌面创建了 a.txt 文件"):
            raise RuntimeError("假成功检测失效")
        # 5) 已知文件夹解析（本机应有桌面/文档/下载）
        kf = fd.known_folders()
        if len(kf) < 3:
            raise RuntimeError(f"已知文件夹解析过少：{list(kf)}")
        return (f"工作总结 {stat['total']} 条；桌面={desk}；"
                f"已知文件夹 {len(kf)} 个；自动记忆/假成功检测 通过")

    chk("第三方依赖", _versions)
    chk("应用模块导入", _app_modules)
    chk("内置种子密钥", _seed_keys)
    chk("密钥保护（混淆/封装/DPAPI）", _security)
    chk("语音识别 + 语音合成", _asr_tts)
    chk("钉钉 + 自动更新", _dingtalk_updater)
    chk("文档生成 docx/xlsx/pptx/pdf", _docwrite)
    chk("文档解析", _docread)
    chk("工具执行", _tools)
    chk("v9 新工具（图表/朗读/钉钉/HTTP）", _v9_tools)
    chk("记忆与计划", _memory_plan)
    chk("知识库检索", _knowledge)
    chk("GUI 构建", _gui)
    chk("工作总结 + 文件夹别名 + 自动记忆", _worklog_folders)

    log("-" * 56)
    log(f"结果：通过 {state['ok']} 项，失败 {state['fail']} 项")
    log("=" * 56)
    try:
        with open(SELFTEST_REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(buf))
    except Exception:
        pass
    return 0 if state["fail"] == 0 else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())

    # ★ v9.22.7：崩溃取证要在**任何 Qt 对象创建之前**装好（faulthandler 要求）。
    #   用户反馈「别的电脑上一点就卡退」，没有日志根本查不了；这里把
    #   Python 未捕获异常 / 子线程异常 / Qt 警告 / 原生段错误 全部落盘到
    #   %LOCALAPPDATA%\AIWorkbench\logs\run-YYYYMMDD.log，并在下次启动时
    #   检测「上次是不是异常退出」，是就提示用安全模式。
    try:
        from app import crashlog as _crash
        from app import version as _ver
        _crash.install(_ver.VERSION)
    except Exception:
        _crash = None

    # 安全模式：--safe 或环境变量 AWB_SAFE=1 → 关掉 Web 渲染 + 跳过联网检查。
    # 用户机器上一旦出现「网页渲染卡死」，这是唯一能自己救回来的开关。
    _safe = ("--safe" in sys.argv) or (os.environ.get("AWB_SAFE", "").strip() == "1")
    if _safe:
        os.environ["AWB_WEBUI"] = "0"

    # 最先把「隐藏控制台」挂上：之后所有子进程（python / cmd / node / dws）都继承它，
    # 执行任务时就不会闪黑窗口。放在启动最开始——真有闪窗也只发生在启动瞬间，
    # 不会在干活过程中反复冒出来。
    try:
        from app import winproc as _wp
        _wp.ensure_hidden_console()
    except Exception:
        pass
    # 高 DPI 适配（避免高分屏上文字/控件过小）
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    # 全局中文字体，确保 QTextBrowser / QPlainTextEdit 能显示中文
    QApplication.setFont(QFont("Microsoft YaHei", 11))

    # ★ v9.21.0 Web UI 一期：awb:// scheme 必须在 QApplication 创建之前注册，
    #   否则 QWebEngineView 加载 awb://ui/ 会静默崩（exit 127）。
    #   无条件注册（不启用 web 引擎也注册，无副作用）。
    try:
        from app.webui_host import register_scheme as _register_awb_scheme
        _register_awb_scheme()
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("AI 工作台")

    # ★ v9.20.0 单实例保护：主窗口只允许开一个。
    #   必须在建主窗口（以及启动对话框）之前抢；抢不到就把「用户又点了一次」
    #   捎给已有实例，然后安静退出（退出码 0）。
    from app.single_instance import InstanceGuard
    inst = InstanceGuard()
    if not inst.try_acquire():
        if inst.degraded:
            inst = None            # 保护机制本身起不来 -> 放行，不能因为保护把程序也弄挂
        else:
            inst.notify_existing()
            sys.exit(0)

    try:
        from app.gui.main_window import _make_icon
        app.setWindowIcon(_make_icon())
    except Exception:
        pass

    # ★ v9.22.7：上次没走正常退出（崩了/被强杀/卡死）→ 给一次「安全模式」机会。
    #   安全模式 = 关 Web 渲染（回归 Qt 原生界面）+ 跳过启动联网检查，
    #   这是用户在坏掉的电脑上唯一能自救的入口。
    if _crash is not None and not _safe:
        try:
            if _crash.last_unclean_exit():
                from PyQt6.QtWidgets import QMessageBox
                r = QMessageBox.question(
                    None, "上次没有正常退出",
                    "检测到 AI 工作台上次是异常退出（崩溃或卡死）。\n\n"
                    "要不要用「安全模式」启动？\n"
                    "· 关闭网页界面渲染（回到原生界面，最不容易卡）\n"
                    "· 跳过启动时的联网检查\n\n"
                    "诊断日志：\n%s" % (_crash.log_path() or "（无）"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes)      # 上次都崩了，默认就建议安全模式
                if r == QMessageBox.StandardButton.Yes:
                    os.environ["AWB_WEBUI"] = "0"
                    os.environ["AWB_NO_UPDATE_CHECK"] = "1"
                    _safe = True
                    try:
                        _crash.log("用户选择安全模式启动")
                    except Exception:
                        pass
        except Exception:
            pass

    # 先应用主题（Fusion + 显式调色板），这样连启动对话框在深色模式下文字也清晰
    from app import themes
    try:
        _p = ws_mod.get_last_workspace()
        _th = ((ws_mod.Workspace(_p).load_settings() if _p else None) or {}).get("theme", "light")
    except Exception:
        _th = "light"
    themes.apply(app, _th)

    # 没有记住的有效工作区 -> 必须先让用户自己选一个（默认建议「文档\AI工作台」）
    path = ws_mod.get_last_workspace()
    if not path:
        dlg = StartupDialog(None, suggest=ws_mod.default_workspace_suggestion())
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.selected:
            path = dlg.selected
        else:
            return

    try:
        themes.apply(app, (ws_mod.Workspace(path).load_settings() or {}).get("theme", "light"))
    except Exception:
        themes.apply(app, "light")

    ws = ws_mod.Workspace(path).ensure()
    settings = ws.load_settings()
    # 打完「正在运行」标记：正常退出时才删；下次启动发现它还在 = 上次崩了。
    if _crash is not None:
        try:
            _crash.mark_running()
            app.aboutToQuit.connect(_crash.clear_running)
            _crash.log("窗口创建中；web_engine=%s safe=%s" % (
                os.environ.get("AWB_WEBUI", "(默认)"), _safe))
        except Exception:
            pass
    win = MainWindow(ws, settings)
    win.show()

    # 界面卡死取证：主线程心跳停了 15 秒就把所有线程的栈写进日志。
    if _crash is not None:
        try:
            _wd = _crash.start_watchdog()
            app._awb_watchdog = _wd          # 持引用，否则 QTimer 被 GC 掉就停了
        except Exception:
            pass

    # 已有实例被再次点击 -> 只把窗口叫回前台，不新开窗口、不新建对话。
    if inst is not None:
        def _bring_to_front():
            try:
                win.showNormal()        # 已最小化 / 收进托盘 都能还原
                win.raise_()
                win.activateWindow()
            except Exception:
                pass

        inst.pinged.connect(_bring_to_front)
        # 启动期间（窗口还没建好）收到的点击攒在队列里，这里补一次 —— 不会丢。
        if inst.take_pending():
            _bring_to_front()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
