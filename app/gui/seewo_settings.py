# -*- coding: utf-8 -*-
"""希沃共享屏幕 —— 设置面板（v9.23.0）。

一个**自包含**的 ``QWidget``：自己读配置、自己写配置、自己起线程干活，
外面只要 ``addWidget`` 进来就能用（不依赖主窗口的任何内部结构）。

    widget = SeewoSettingsWidget(settings_getter, settings_setter,
                                 page_getter=lambda: browser_page.page(),
                                 workspace_getter=lambda: self.workspace.path)

三条硬规矩（本项目踩过的坑）
----------------------------
1. **绝不阻塞 UI 线程**：所有页面操作（学一次 / 试定位 / 跑一遍）都在
   ``QThread`` 里跑，主线程只收 ``pyqtSignal``。
2. **跨线程只走 pyqtSignal**：worker 不碰任何控件，连字符串都只通过信号发回。
3. **不写死字号**：颜色全部取自 ``themes.tokens()``，字号沿用系统/全局 QSS。
   （项目刚因为把字号改大出过问题，这里一个 ``font-size`` 都不写。）
"""
import os
import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import seewo as seewo_mod
from .. import seewo_steps as steps_mod
from .. import themes

# (字段路径, 中文名, 类型)  类型: text=普通文本 / sel=选择器（可被「学一次」填充）
_FIELD_SPECS = (
    ("site_url", "站点地址", "text"),
    ("login.url", "登录页地址", "text"),
    ("login.user_sel", "账号输入框", "sel"),
    ("login.pass_sel", "密码输入框", "sel"),
    ("login.submit_sel", "登录按钮", "sel"),
    ("login.logged_in_sel", "「已登录」标志", "sel"),
    ("share.entry_sel", "「共享屏幕」入口", "sel"),
    ("share.confirm_sel", "共享确认按钮", "sel"),
    ("share.started_sel", "「共享中」标志", "sel"),
    ("stop_share_sel", "「结束共享」按钮", "sel"),
    ("media.list_sel", "媒体列表容器", "sel"),
    ("media.item_sel_tpl", "条目模板（{i}=1起/{n}=0起/%d）", "sel"),
    ("media.play_btn_sel", "播放按钮（可空）", "sel"),
    ("media.video_sel", "video 元素", "sel"),
    ("media.ended_sel", "播放结束标志（可空）", "sel"),
)

_SECTIONS = (
    ("基本", ("site_url",)),
    ("登录", ("login.url", "login.user_sel", "login.pass_sel",
              "login.submit_sel", "login.logged_in_sel")),
    ("共享屏幕", ("share.entry_sel", "share.confirm_sel",
                  "share.started_sel", "stop_share_sel")),
    ("媒体（视频/手势操）", ("media.list_sel", "media.item_sel_tpl",
                            "media.play_btn_sel", "media.video_sel",
                            "media.ended_sel")),
)

_ACTIONS = ("click", "type", "wait", "wait_gone", "js", "sleep")


# ---------------------------------------------------------------------------
# 小工具：按 "a.b" 路径读写 dict
# ---------------------------------------------------------------------------
def _get_path(cfg, path, default=""):
    node = cfg or {}
    for part in str(path).split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part, default)
    return node if node is not None else default


def _set_path(cfg, path, value):
    parts = str(path).split(".")
    node = cfg
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _tokens():
    try:
        return themes.tokens()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 后台线程：所有页面/磁盘操作都在这里跑
# ---------------------------------------------------------------------------
class _Worker(QThread):
    """一次后台作业。``kind`` 决定干什么。"""

    progress = pyqtSignal(str, str)          # stage, detail
    logline = pyqtSignal(str)                # 一行日志
    done = pyqtSignal(str, object)           # kind, result(dict)

    def __init__(self, kind, fn, parent=None):
        super().__init__(parent)
        self.kind = kind
        self._fn = fn

    def run(self):
        try:
            res = self._fn(self)
        except Exception as e:               # 绝不让线程把异常抛给 Qt
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.done.emit(self.kind, res if isinstance(res, dict) else
                       {"ok": True, "value": res})

    # 给内部函数用的转发方法（线程安全：signal emit 本身是安全的）
    def emit_progress(self, stage, detail):
        self.progress.emit(str(stage), str(detail))

    def emit_log(self, text):
        self.logline.emit(str(text))


# ---------------------------------------------------------------------------
# 「学一次」结果对话框：从清单里挑选择器
# ---------------------------------------------------------------------------
class _ProbeDialog(QDialog):
    """展示探针结果，选中一条即可填到指定字段。"""

    def __init__(self, items, parent=None, field_labels=None):
        super().__init__(parent)
        self.setWindowTitle("学一次 · 页面元素清单")
        self.resize(820, 520)
        self._items = list(items or [])
        self.chosen = None                   # (field_path, selector)
        self._field_labels = list(field_labels or [])

        lay = QVBoxLayout(self)
        tip = QLabel(
            "这是刚才那个页面上的可点元素（最多 200 条）。"
            "选中一行、选好要填的字段，点「填入」。\n"
            "提示：优先选文字最短的那一行 —— 它通常就是按钮本身。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("填到："))
        self.cmb_field = QComboBox()
        for path, label in self._field_labels:
            self.cmb_field.addItem(label, path)
        row.addWidget(self.cmb_field, 1)
        lay.addLayout(row)

        self.table = QTableWidget(len(self._items), 4, self)
        self.table.setHorizontalHeaderLabels(["标签", "文字", "选择器", "可见"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for r, it in enumerate(self._items):
            self.table.setItem(r, 0, QTableWidgetItem(str(it.get("tag") or "")))
            self.table.setItem(r, 1, QTableWidgetItem(str(it.get("text") or "")))
            self.table.setItem(r, 2, QTableWidgetItem(
                str(it.get("selector_guess") or "")))
            self.table.setItem(r, 3, QTableWidgetItem(
                "是" if it.get("visible") else "否"))
        lay.addWidget(self.table, 1)

        btns = QDialogButtonBox()
        self.btn_fill = QPushButton("填入")
        btns.addButton(self.btn_fill, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(QDialogButtonBox.StandardButton.Close)
        self.btn_fill.clicked.connect(self._on_fill)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.table.doubleClicked.connect(lambda _idx: self._on_fill())

    def _on_fill(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "先选一行。")
            return
        sel = self.table.item(row, 2)
        if sel is None or not sel.text().strip():
            return
        self.chosen = (self.cmb_field.currentData(), sel.text().strip())
        self.accept()


# ---------------------------------------------------------------------------
# 主面板
# ---------------------------------------------------------------------------
class SeewoSettingsWidget(QWidget):
    """希沃共享屏幕设置面板（自包含）。"""

    def __init__(self, settings_getter=None, settings_setter=None,
                 page_getter=None, workspace_getter=None, parent=None):
        super().__init__(parent)
        self._settings_getter = settings_getter
        self._settings_setter = settings_setter
        self._page_getter = page_getter
        self._ws_getter = workspace_getter
        self._worker = None
        self._cfg = seewo_mod.normalize_config(seewo_mod.DEFAULT_CONFIG)
        self._edits = {}
        self._busy = False
        self._build_ui()
        self.reload()

    # ---------- 工作区 ----------
    def workspace_path(self):
        """优先用显式 getter；否则从 settings 里猜；再不行用上次的工作区。"""
        if callable(self._ws_getter):
            try:
                p = self._ws_getter()
                if p:
                    return os.path.abspath(str(p))
            except Exception:
                pass
        try:
            s = self._settings_getter() if callable(self._settings_getter) else {}
        except Exception:
            s = {}
        if isinstance(s, dict):
            for key in ("workspace", "workspace_path", "ws_path",
                        "last_workspace"):
                v = s.get(key)
                if v:
                    return os.path.abspath(str(v))
        # ★ 刻意**不**回落到「上次的工作区」：那会让一个没被明确告知工作区的
        #   面板把配置写进用户真实的工作区（测试里真踩到过）。宁可报错让调用方
        #   传 workspace_getter，也不要偷偷写别人的目录。
        return ""

    def _page(self):
        """取当前内置浏览器页面（可能为空）。"""
        if not callable(self._page_getter):
            return None
        try:
            return steps_mod.as_page(self._page_getter())
        except Exception:
            return None

    # ---------- 界面 ----------
    def _build_ui(self):
        tk = _tokens()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 顶部：开关 + 站点
        top = QHBoxLayout()
        self.chk_enabled = QCheckBox("启用希沃共享屏幕")
        self.chk_enabled.setToolTip(
            "关掉后：定时任务不会真正执行（只记一条跳过），手动「现在跑一遍」仍可用。")
        top.addWidget(self.chk_enabled)
        top.addStretch(1)
        self.lbl_ws = QLabel("")
        if tk.get("text_muted"):
            self.lbl_ws.setStyleSheet("color:%s;" % tk["text_muted"])
        top.addWidget(self.lbl_ws)
        outer.addLayout(top)

        # 主体：可滚动
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        blay = QVBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(8)

        by_path = {p: (lbl, kind) for p, lbl, kind in _FIELD_SPECS}
        for title, paths in _SECTIONS:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            for path in paths:
                lbl, kind = by_path[path]
                ed = QLineEdit()
                ed.setPlaceholderText(
                    "CSS / XPath / text=文本  都能写" if kind == "sel" else "")
                if kind == "sel":
                    ed.setClearButtonEnabled(True)
                self._edits[path] = ed
                form.addRow(lbl, ed)
            blay.addWidget(box)

        # 账号密码
        cred_box = QGroupBox("账号密码（DPAPI 加密保存，明文不落盘）")
        cred_form = QFormLayout(cred_box)
        self.ed_user = QLineEdit()
        self.ed_user.setPlaceholderText("主任账号")
        self.ed_pass = QLineEdit()
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_pass.setPlaceholderText("留空 = 不改动已保存的密码")
        self.btn_clear_cred = QPushButton("清除账号密码")
        self.btn_clear_cred.clicked.connect(self._on_clear_cred)
        cred_form.addRow("账号", self.ed_user)
        row = QHBoxLayout()
        row.addWidget(self.ed_pass, 1)
        row.addWidget(self.btn_clear_cred)
        cred_form.addRow("密码", row)
        self.lbl_cred_mode = QLabel("")
        if tk.get("text_muted"):
            self.lbl_cred_mode.setStyleSheet("color:%s;" % tk["text_muted"])
        cred_form.addRow("", self.lbl_cred_mode)
        blay.addWidget(cred_box)

        # 媒体大致位置 + 试定位
        pos_box = QGroupBox("媒体大致位置（主任怎么说的就怎么填）")
        pos_lay = QVBoxLayout(pos_box)
        prow = QHBoxLayout()
        self.ed_hint = QLineEdit()
        self.ed_hint.setPlaceholderText(
            "第三个视频 / 第二段手势操 / 《七彩阳光》那首 / 最后一个")
        self.btn_try = QPushButton("试一下定位")
        self.btn_try.clicked.connect(self._on_try_locate)
        prow.addWidget(self.ed_hint, 1)
        prow.addWidget(self.btn_try)
        pos_lay.addLayout(prow)
        self.lbl_hint_result = QLabel("")
        self.lbl_hint_result.setWordWrap(True)
        pos_lay.addWidget(self.lbl_hint_result)
        blay.addWidget(pos_box)

        # 步骤列表
        step_box = QGroupBox("自定义步骤（可选，跑在「选媒体」之前）")
        slay = QVBoxLayout(step_box)
        self.tbl_steps = QTableWidget(0, 4)
        self.tbl_steps.setHorizontalHeaderLabels(
            ["动作", "选择器", "值", "超时(秒)"])
        self.tbl_steps.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.tbl_steps.verticalHeader().setVisible(False)
        slay.addWidget(self.tbl_steps, 1)
        srow = QHBoxLayout()
        self.btn_add_step = QPushButton("＋ 增加一步")
        self.btn_del_step = QPushButton("－ 删除选中")
        self.btn_add_step.clicked.connect(lambda: self._add_step_row(
            {"action": "click", "selector": "", "value": "", "timeout": 15}))
        self.btn_del_step.clicked.connect(self._del_step_row)
        srow.addWidget(self.btn_add_step)
        srow.addWidget(self.btn_del_step)
        srow.addStretch(1)
        slay.addLayout(srow)
        blay.addWidget(step_box)

        # 提示
        self.lbl_warn = QLabel("")
        self.lbl_warn.setWordWrap(True)
        if tk.get("warn"):
            self.lbl_warn.setStyleSheet("color:%s;" % tk["warn"])
        blay.addWidget(self.lbl_warn)

        blay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 3)

        # 按钮区
        brow = QHBoxLayout()
        self.btn_save = QPushButton("保存")
        self.btn_learn = QPushButton("学一次")
        self.btn_run = QPushButton("现在跑一遍")
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_learn.clicked.connect(self._on_learn)
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_learn.setToolTip(
            "先在内置浏览器里打开希沃页面并登录，再点这里，\n"
            "程序会导出页面上所有可点元素，让你直接挑选择器。")
        brow.addWidget(self.btn_save)
        brow.addWidget(self.btn_learn)
        brow.addStretch(1)
        brow.addWidget(self.btn_run)
        brow.addWidget(self.btn_stop)
        outer.addLayout(brow)

        # 日志
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(2000)
        self.txt_log.setPlaceholderText("这里显示运行日志…")
        outer.addWidget(self.txt_log, 2)

    # ---------- 日志 ----------
    def _log(self, text):
        try:
            self.txt_log.appendPlainText(
                "[%s] %s" % (time.strftime("%H:%M:%S"), text))
        except Exception:
            pass

    # ---------- 读 ----------
    def reload(self):
        """从磁盘重新读配置并刷到界面（不动密码框）。"""
        ws = self.workspace_path()
        self._cfg = seewo_mod.load_config(ws) if ws else \
            seewo_mod.normalize_config(seewo_mod.DEFAULT_CONFIG)
        c = self._cfg
        self.chk_enabled.setChecked(bool(c.get("enabled")))
        for path, ed in self._edits.items():
            ed.setText(str(_get_path(c, path) or ""))
        user = str(c.get("_user") or "")
        if not user:
            try:
                user, _p, _src = seewo_mod.get_credentials(c, ws)
            except Exception:
                user = ""
        self.ed_user.setText(user)
        self.ed_pass.clear()
        has_pw = bool(str(c.get("creds_enc") or "")) or bool(
            str(c.get("_pass") or ""))
        self.ed_pass.setPlaceholderText(
            "已加密保存（留空不改）" if has_pw else "未设置")
        mode = seewo_mod.credential_mode()
        self.lbl_cred_mode.setText(
            "加密方式：Windows DPAPI（绑定当前账户）" if mode == "dpapi"
            else "⚠ 本机拿不到 DPAPI：账号密码只存内存，关程序即丢（不会落盘）")
        self.lbl_ws.setText(f"工作区：{ws}" if ws else "⚠ 没有工作区路径")
        self._fill_steps(c.get("steps") or [])
        self._refresh_warn()

    def _fill_steps(self, steps):
        self.tbl_steps.setRowCount(0)
        for s in steps:
            self._add_step_row(s)

    def _add_step_row(self, s):
        r = self.tbl_steps.rowCount()
        self.tbl_steps.insertRow(r)
        cmb = QComboBox()
        cmb.addItems(_ACTIONS)
        act = str((s or {}).get("action") or "click")
        idx = _ACTIONS.index(act) if act in _ACTIONS else 0
        cmb.setCurrentIndex(idx)
        self.tbl_steps.setCellWidget(r, 0, cmb)
        self.tbl_steps.setItem(r, 1, QTableWidgetItem(
            str((s or {}).get("selector") or "")))
        self.tbl_steps.setItem(r, 2, QTableWidgetItem(
            str((s or {}).get("value") or "")))
        self.tbl_steps.setItem(r, 3, QTableWidgetItem(
            str((s or {}).get("timeout") or 15)))

    def _del_step_row(self):
        r = self.tbl_steps.currentRow()
        if r >= 0:
            self.tbl_steps.removeRow(r)

    def _collect_steps(self):
        out = []
        for r in range(self.tbl_steps.rowCount()):
            cmb = self.tbl_steps.cellWidget(r, 0)
            action = cmb.currentText() if cmb is not None else "click"
            sel = self.tbl_steps.item(r, 1)
            val = self.tbl_steps.item(r, 2)
            tmo = self.tbl_steps.item(r, 3)
            out.append({
                "action": action,
                "selector": sel.text().strip() if sel else "",
                "value": val.text() if val else "",
                "timeout": (tmo.text().strip() if tmo else "15") or "15",
            })
        return seewo_mod.normalize_steps(out)

    def _refresh_warn(self):
        c = self._cfg
        c["enabled"] = self.chk_enabled.isChecked()
        for path, ed in self._edits.items():
            _set_path(c, path, ed.text().strip())
        warns = seewo_mod.warnings_of(c, self.workspace_path())
        self.lbl_warn.setText("　".join("· " + w for w in warns) if warns else "")

    # ---------- 写 ----------
    def collect(self):
        """把界面上的内容收成一个配置 dict（不含落盘动作）。"""
        c = dict(self._cfg or {})
        c.setdefault("login", {})
        c.setdefault("share", {})
        c.setdefault("media", {})
        c["enabled"] = self.chk_enabled.isChecked()
        for path, ed in self._edits.items():
            _set_path(c, path, ed.text().strip())
        c["steps"] = self._collect_steps()
        return seewo_mod.normalize_config(c, self.workspace_path())

    def save(self):
        """保存到 ``.awb/seewo.json``；返回 ``(ok, error)``。"""
        ws = self.workspace_path()
        if not ws:
            return False, "没有工作区路径，无法保存"
        c = self.collect()
        user = self.ed_user.text().strip()
        pw = self.ed_pass.text()
        old_user, old_pw, _src = seewo_mod.get_credentials(self._cfg, ws)
        # 密码框留空 = 不改；账号被清空 = 一并清掉
        if pw or (user and user != old_user) or (not user and old_user):
            mode, err = seewo_mod.set_credentials(c, user, pw or old_pw, ws)
            if err:
                self._log("账号密码保存提示：" + err)
            self.lbl_cred_mode.setText(
                "加密方式：Windows DPAPI" if mode == "dpapi"
                else "⚠ 无 DPAPI：只存内存，关程序即丢")
        ok, err = seewo_mod.save_config(ws, c)
        if ok:
            self._cfg = seewo_mod.load_config(ws)
            self.ed_pass.clear()
            self.ed_pass.setPlaceholderText(
                "已加密保存（留空不改）"
                if self._cfg.get("creds_enc") or c.get("creds_enc")
                else "未设置")
            self.lbl_warn.setText("")
            self._log("已保存配置：" + seewo_mod.config_path(ws))
        else:
            self._log("保存失败：" + err)
        return ok, err

    # ---------- 按钮 ----------
    def _on_save(self):
        ok, err = self.save()
        if not ok:
            QMessageBox.warning(self, "保存失败", err)
        else:
            QMessageBox.information(self, "已保存", "配置已写入工作区 .awb/seewo.json")

    def _on_clear_cred(self):
        ws = self.workspace_path()
        seewo_mod.clear_credentials(self._cfg, ws)
        if ws:
            seewo_mod.save_config(ws, self._cfg)
        self.ed_user.clear()
        self.ed_pass.clear()
        self.ed_pass.setPlaceholderText("未设置")
        self._log("已清除账号密码")

    def _set_busy(self, busy, kind=""):
        self._busy = busy
        for b in (self.btn_save, self.btn_learn, self.btn_run, self.btn_try):
            b.setEnabled(not busy)
        self.btn_stop.setEnabled(busy and kind in ("run", "locate"))

    def _start(self, kind, fn):
        if self._busy:
            self._log("还有任务在跑，先等它结束（或点「停止」）。")
            return
        self._set_busy(True, kind)
        self._worker = _Worker(kind, fn, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.logline.connect(self._log)
        self._worker.done.connect(self._on_worker_done)
        self._worker.start()

    def _on_progress(self, stage, detail):
        label = steps_mod.STAGE_LABELS.get(stage, stage)
        self._log(f"[{label}] {detail}")

    def _on_worker_done(self, kind, res):
        self._set_busy(False)
        res = res if isinstance(res, dict) else {"ok": False, "error": str(res)}
        if kind == "learn":
            self._after_learn(res)
        elif kind == "locate":
            self._after_locate(res)
        elif kind == "run":
            if res.get("ok"):
                self._log("✔ " + str(res.get("summary") or "完成"))
                QMessageBox.information(self, "完成", res.get("summary") or "完成")
            else:
                self._log("✘ 失败（%s）：%s" % (res.get("stage"), res.get("error")))
                QMessageBox.warning(
                    self, "没跑完",
                    "卡在「%s」：\n%s" % (
                        steps_mod.STAGE_LABELS.get(res.get("stage"),
                                                   res.get("stage")),
                        res.get("error")))

    def _on_learn(self):
        page = self._page()
        ws = self.workspace_path()
        if page is None:
            QMessageBox.warning(
                self, "没有页面",
                "先在内置浏览器里打开希沃页面（并登录），再点「学一次」。")
            return
        if not ws:
            QMessageBox.warning(self, "没有工作区", "缺少工作区路径，无法保存探针文件。")
            return
        self._log("开始「学一次」：导出页面元素…")

        def job(w):
            return seewo_mod.learn_once(ws, page, suggest=True)

        self._start("learn", job)

    def _after_learn(self, res):
        if not res.get("ok"):
            QMessageBox.warning(self, "学一次失败", str(res.get("error") or ""))
            return
        items = res.get("items") or []
        self._log("学一次完成：%d 个候选元素，清单已写到\n%s"
                  % (len(items), res.get("path") or ""))
        sug = res.get("suggested") or {}
        filled_names = []
        if isinstance(sug, dict):
            for path, label, kind in _FIELD_SPECS:
                if kind != "sel":
                    continue
                val = str(_get_path(sug, path) or "").strip()
                ed = self._edits.get(path)
                if val and ed is not None and not ed.text().strip():
                    ed.setText(val)
                    filled_names.append(label)
        if filled_names:
            self._log("自动填了 %d 个空选择器（**务必核对一遍再保存**）：%s"
                      % (len(filled_names), "、".join(filled_names)))
        self._refresh_warn()

        labels = [(p, lbl) for p, lbl, k in _FIELD_SPECS if k == "sel"]
        dlg = _ProbeDialog(items, self, labels)
        dlg.setWindowTitle("学一次 · 挑选择器（%d 条）" % len(items))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen:
            path, sel = dlg.chosen
            ed = self._edits.get(path)
            if ed is not None:
                ed.setText(sel)
                self._log("已把选择器填到「%s」：%s" % (path, sel))
                self._refresh_warn()

    def _on_try_locate(self):
        page = self._page()
        if page is None:
            QMessageBox.warning(self, "没有页面", "先在内置浏览器里打开希沃的媒体列表页。")
            return
        cfg = self.collect()
        hint = self.ed_hint.text().strip()
        if not hint:
            QMessageBox.information(self, "先填位置", "比如：第三个视频 / 七彩阳光")
            return
        self.lbl_hint_result.setText("定位中…")
        self._log(f"试定位：{hint}")

        def job(w):
            return steps_mod.resolve_media(page, hint, cfg, None, play=False)

        self._start("locate", job)

    def _after_locate(self, res):
        if res.get("ok"):
            txt = ("命中第 %d 条：%s（依据：%s，共 %d 条）"
                   % (res.get("index", 0) + 1, res.get("label") or "",
                      {"text": "文本匹配", "ordinal": "位置"}.get(
                          res.get("match"), res.get("match")),
                      res.get("total") or 0))
            self.lbl_hint_result.setText("✔ " + txt)
            self._log(txt)
        else:
            self.lbl_hint_result.setText("✘ " + str(res.get("error") or "没定位到"))
            self._log("定位失败：" + str(res.get("error") or ""))

    def _on_run(self):
        page = self._page()
        ws = self.workspace_path()
        if page is None:
            QMessageBox.warning(self, "没有页面",
                                "先在内置浏览器里打开希沃页面，再点「现在跑一遍」。")
            return
        if not ws:
            QMessageBox.warning(self, "没有工作区", "缺少工作区路径。")
            return
        hint = self.ed_hint.text().strip()
        if not hint:
            QMessageBox.information(self, "先填位置",
                                    "在「媒体大致位置」里写一句人话，比如「第三个视频」。")
            return
        cfg = self.collect()
        miss = seewo_mod.missing_fields(cfg)
        if miss:
            if QMessageBox.question(
                    self, "配置不完整",
                    "这些还没填：\n" + "\n".join("%s（%s）" % (l, p)
                                             for p, l in miss) +
                    "\n\n仍要试跑吗？") != QMessageBox.StandardButton.Yes:
                return
        seewo_mod.reset_stop(ws)
        stop_event = seewo_mod.stop_event_for(ws)
        self._log("开始跑共享流程…")

        def job(w):
            return seewo_mod.run_share_flow_for_workspace(
                ws, page, hint, stop_event=stop_event, on_progress=w.emit_progress,
                cfg=cfg)

        self._start("run", job)

    def _on_stop(self):
        ws = self.workspace_path()
        if ws:
            seewo_mod.request_stop(ws)
            self._log("已发出停止信号（当前这一步结束后就停）。")
        self.btn_stop.setEnabled(False)

    # ---------- 生命周期 ----------
    def shutdown(self):
        """主窗口关闭时调一下：停线程、发停止信号。"""
        ws = self.workspace_path()
        if ws:
            seewo_mod.request_stop(ws)
        w = self._worker
        if w is not None and w.isRunning():
            w.requestInterruption()
            w.wait(1500)
