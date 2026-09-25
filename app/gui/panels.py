# -*- coding: utf-8 -*-
"""主窗口附属面板：任务计划条、长期记忆对话框、工具调用记录、MCP 管理。"""
import os
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QDialog, QPlainTextEdit, QTextBrowser, QMessageBox, QListWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor

from .. import themes, plan as plan_mod


class PlanPanel(QFrame):
    """输入框上方的任务计划条：显示 AI 当前的多步计划与进度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        t = themes.tokens()
        self.setStyleSheet(
            f"PlanPanel{{background:{t['tool_bg']};border:1px solid {t['border']};"
            f"border-radius:10px;}}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("任务计划")
        self.title.setStyleSheet(
            f"color:{t['accent_text']};font-size:12px;font-weight:bold;background:transparent;")
        head.addWidget(self.title)
        head.addStretch(1)
        self.progress = QLabel("")
        self.progress.setStyleSheet(
            f"color:{t['text_muted']};font-size:11px;background:transparent;")
        head.addWidget(self.progress)
        self.hide_btn = QPushButton("隐藏")
        self.hide_btn.setObjectName("cardBtn")
        self.hide_btn.setFixedSize(48, 22)
        self.hide_btn.setToolTip(
            "收起计划内容（只留这一条标题栏）。\n"
            "收起后 AI 再更新计划也不会自动展开——你点「显示」才会展开。")
        self.hide_btn.clicked.connect(self._toggle_body)
        head.addWidget(self.hide_btn)
        outer.addLayout(head)
        self.body = QTextBrowser()
        self.body.setReadOnly(True)
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setStyleSheet("background:transparent;border:none;font-size:14px;")
        self.body.setMinimumHeight(30)
        outer.addWidget(self.body)
        self.setVisible(False)
        # 用户手动收起过就不要再自动弹开（用户反馈：点一次隐藏后它老自己冒出来）
        self._collapsed = False
        self._last_plan = None

    def _toggle_body(self):
        self._collapsed = not self._collapsed
        self._apply_collapsed()

    def _apply_collapsed(self):
        self.hide_btn.setText("显示" if self._collapsed else "隐藏")
        self.body.setVisible(not self._collapsed)
        if self._collapsed:
            self.setFixedHeight(40)
        elif self._last_plan is not None:
            # 展开时按上次内容恢复高度
            self.set_plan(self._last_plan, force=True)

    def refresh_theme(self):
        t = themes.tokens()
        self.setStyleSheet(
            f"PlanPanel{{background:{t['tool_bg']};border:1px solid {t['border']};"
            f"border-radius:10px;}}")
        self.title.setStyleSheet(
            f"color:{t['accent_text']};font-size:12px;font-weight:bold;background:transparent;")
        self.progress.setStyleSheet(
            f"color:{t['text_muted']};font-size:11px;background:transparent;")

    def set_plan(self, plan, force=False):
        steps = (plan or {}).get("steps") or []
        if not steps:
            self._last_plan = None
            self.setVisible(False)
            return
        self._last_plan = plan
        # 用户收起过就别自动展开（force=True 表示用户主动点「显示」）
        if self._collapsed and not force:
            self.setVisible(True)
            self.setFixedHeight(40)
            return
        t = themes.tokens()
        done, total, pct = plan_mod.progress(plan)
        self.progress.setText(f"{done}/{total} · {pct}%")
        html = ['<div style="line-height:1.85;font-size:14px;">']
        colors = {"pending": t["text_muted"], "doing": "#e08a1e",
                  "done": "#28a05a", "failed": "#d9534f"}
        for i, s in enumerate(steps, 1):
            c = colors.get(s["status"], t["text_muted"])
            deco = "text-decoration:line-through;opacity:.65;" if s["status"] == "done" else ""
            html.append(
                f'<div style="{deco}">'
                f'<span style="color:{c};font-weight:bold;">'
                f'{plan_mod.STATUS_MARKS.get(s["status"], "○")}</span>'
                f'<span style="color:{t["tool_text"]};"> {i}. {_esc(s["text"])}</span></div>')
        note = (plan or {}).get("note")
        if note:
            html.append(f'<div style="color:{t["text_muted"]};font-size:12px;'
                        f'margin-top:4px;">备注：{_esc(str(note))}</div>')
        html.append("</div>")
        self.body.setHtml("".join(html))
        self.setVisible(True)
        doc = self.body.document()
        doc.setTextWidth(max(self.body.viewport().width() - 4, 120))
        h = int(doc.size().height())
        # 面板高度严格跟着内容走：不设死的话 QVBoxLayout 会把多余空间摊给它
        bh = min(max(h + 8, 30), 220)
        self.body.setFixedHeight(bh)
        self.setFixedHeight(bh + 58)


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class MemoryDialog(QDialog):
    """查看 / 编辑 / 清空长期记忆。"""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("长期记忆")
        self.resize(760, 560)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        tip = QLabel(
            "这里是 AI 的跨会话长期记忆（工作区根目录的 MEMORY.md）。\n"
            "AI 会在每轮对话里自动参考它，也可以用 remember 工具往里记；你可以直接编辑。")
        tip.setWordWrap(True)
        v.addWidget(tip)

        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#888;font-size:12px;")
        v.addWidget(self.hint)

        self.edit = QPlainTextEdit()
        self.edit.setPlainText(store.load_text() or
                               "（还没有记忆。AI 会在对话中自动记录，你也可以在这里手写。）")
        v.addWidget(self.edit, 1)

        row = QHBoxLayout()
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self._save)
        self.reload_btn = QPushButton("重新载入")
        self.reload_btn.clicked.connect(self._reload)
        self.clear_btn = QPushButton("清空全部")
        self.clear_btn.clicked.connect(self._clear)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(self.save_btn)
        row.addWidget(self.reload_btn)
        row.addStretch(1)
        row.addWidget(self.clear_btn)
        row.addWidget(close_btn)
        v.addLayout(row)
        self._update_hint()

    def _update_hint(self):
        data = self.store.parse()
        n = sum(len(x) for x in data.values())
        cats = "、".join(f"{k}({len(v)})" for k, v in data.items() if v) or "无"
        self.hint.setText(f"共 {n} 条记忆　|　分类：{cats}")

    def _save(self):
        self.store.save_text(self.edit.toPlainText())
        self._update_hint()
        QMessageBox.information(self, "已保存", "长期记忆已保存。")

    def _reload(self):
        self.edit.setPlainText(self.store.load_text() or "")
        self._update_hint()

    def _clear(self):
        if QMessageBox.question(self, "确认", "确定要清空全部长期记忆吗？") \
                == QMessageBox.StandardButton.Yes:
            self.store.clear()
            self.edit.setPlainText("")
            self._update_hint()


class TraceDialog(QDialog):
    """工具调用记录：每一步调了什么、耗时多久、成功还是失败。"""

    def __init__(self, trace, parent=None):
        super().__init__(parent)
        self.trace = trace
        self.setWindowTitle("工具调用记录")
        self.resize(820, 580)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color:#666;font-size:12px;")
        v.addWidget(self.summary)
        self.body = QTextBrowser()
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(self.body, 1)
        row = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        clear = QPushButton("清空记录")
        clear.clicked.connect(self._clear)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(refresh)
        row.addWidget(clear)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        self.reload()

    def reload(self):
        recs = self.trace.tail(150)
        recs = list(reversed(recs))
        lines = []
        for r in recs:
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("t", 0)))
            ok = r.get("ok")
            mark = "✅" if ok else "❌"
            name = r.get("name", "?")
            ms = r.get("ms", 0)
            args = r.get("args") or {}
            arg_s = ", ".join(f"{k}={_short(v)}" for k, v in args.items())
            res = (r.get("result") or "").replace("\n", " ")[:150]
            lines.append(
                f'<div style="margin:6px 0;line-height:1.6;">'
                f'<span style="color:#888;font-size:11px;">{ts}</span> '
                f'<b>{mark} {_esc(name)}</b> '
                f'<span style="color:#888;font-size:11px;">{ms} ms</span><br>'
                f'<span style="color:#555;font-size:12px;">参数：{_esc(arg_s)}</span><br>'
                f'<span style="color:#333;font-size:12px;">{_esc(res)}</span></div>')
        if not lines:
            lines = ['<div style="color:#888;">还没有工具调用记录。</div>']
        self.body.setHtml("".join(lines))
        by = self.trace.stats()
        if by:
            tot = sum(d["n"] for d in by.values())
            oks = sum(d["ok"] for d in by.values())
            top = sorted(by.items(), key=lambda x: -x[1]["n"])[:5]
            self.summary.setText(
                f"累计调用 {tot} 次，成功 {oks} 次　|　"
                + "　".join(f"{k}×{v['n']}" for k, v in top))

    def _clear(self):
        if QMessageBox.question(self, "确认", "确定清空工具调用记录吗？") \
                == QMessageBox.StandardButton.Yes:
            self.trace.clear()
            self.reload()


def _short(v):
    s = str(v)
    return s if len(s) <= 40 else s[:40] + "…"


class MCPDialog(QDialog):
    """查看 MCP 服务器配置与已发现的工具。"""

    def __init__(self, manager, workspace_path, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.ws = workspace_path
        self.setWindowTitle("MCP 扩展（外部系统接入）")
        self.resize(760, 520)
        v = QVBoxLayout(self)
        v.setSpacing(8)
        cfg_path = os.path.join(workspace_path, "mcp.json")
        tip = QLabel(
            "MCP 让你接入现成的外部能力（GitHub、数据库、自建服务等）。\n"
            f"把配置写到：{cfg_path}\n"
            '格式：{"mcpServers": {"名字": {"command": "可执行文件", "args": ["..."], '
            '"env": {"K": "V"}}}}')
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;font-size:12px;")
        v.addWidget(tip)
        self.list = QTextBrowser()
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        scan = QPushButton("扫描并列出工具")
        scan.clicked.connect(self._scan)
        reload_ = QPushButton("重新载入配置")
        reload_.clicked.connect(self._reload_cfg)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(scan)
        row.addWidget(reload_)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        self._show_config()

    def _show_config(self):
        cfg = self.manager.configured()
        if not cfg:
            self.list.setHtml('<div style="color:#888;">（还没有配置任何 MCP 服务器）</div>')
            return
        html = []
        for name, c in cfg.items():
            html.append(f'<div style="margin:6px 0;"><b>{_esc(name)}</b><br>'
                        f'<span style="color:#555;font-size:12px;">'
                        f'{_esc(c.get("command"))} '
                        f'{_esc(" ".join(map(str, c.get("args") or [])))}</span></div>')
        self.list.setHtml("".join(html))

    def _reload_cfg(self):
        self.manager.reload()
        self._show_config()

    def _scan(self):
        self.list.setHtml('<div style="color:#888;">正在启动 MCP 服务器并拉取工具列表…</div>')
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        tools = self.manager.list_all_tools()
        if not tools:
            extra = f"<br><span style='color:#c33;'>{_esc(self.manager.last_error)}</span>" \
                if self.manager.last_error else ""
            self.list.setHtml('<div style="color:#888;">没有取到工具。'
                              '请检查 mcp.json 里的命令是否可执行。</div>' + extra)
            return
        html = [f'<div style="margin-bottom:6px;">共 {len(tools)} 个工具：</div>']
        cur = None
        for t in tools:
            if t["server"] != cur:
                cur = t["server"]
                html.append(f'<div style="margin-top:8px;"><b>【{_esc(cur)}】</b></div>')
            html.append(f'<div style="margin-left:14px;">'
                        f'<b>{_esc(t["name"])}</b>'
                        f'<span style="color:#666;font-size:12px;"> — '
                        f'{_esc((t["description"] or "")[:160])}</span></div>')
        self.list.setHtml("".join(html))
