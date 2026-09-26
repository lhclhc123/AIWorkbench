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

    def _app_modules():
        from app import (config, llm_client, agent, docread, docwrite,   # noqa
                         memory, plan, knowledge, trace, mcp_client,
                         workspace, themes, markdown_render,
                         version, security, asr, tts, dingtalk, updater,
                         folders, worklog)                              # noqa
        from app.gui import (main_window, chat_worker, panels,           # noqa
                             widgets, pages, voice_bar)
        from app import version as _v
        return f"应用模块（v{_v.VERSION} 共 26 个）全部导入成功"

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
        app = QApplication.instance() or QApplication([])
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

    app = QApplication(sys.argv)
    app.setApplicationName("AI 工作台")
    try:
        from app.gui.main_window import _make_icon
        app.setWindowIcon(_make_icon())
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
    win = MainWindow(ws, settings)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
