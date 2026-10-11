# -*- coding: utf-8 -*-
"""对话内「团队」条 —— 聊天时直接选一支预置团队，并实时显示每个成员的状态。

用户 2026-10-11 原话（需求 13）：
    「你团队的功能根本就做的不对，这个功能是直接放到对话中的，对话时可以选择使用团队，
      然后设计想软件开发团队之类的，有已经写好的成员及其身份背景，然后比如项目总监
      可以给工程师派任务之类的……」

为什么要单独一个控件（而不是再开一个「团队」页）：
  · 用户要的是**在对话里**就能选团队、看到协作过程，而不是跳到另一个页面去点。
  · 所以这里做成一条紧凑的横条，放在输入卡片上方（和「模型现在在干什么」那条状态行同层）：
      [⬡ 团队：不使用 ▾]   ⌖顾承  ▥苏念  ⬢陈砚  ⌨林一鸣  ⚗何澈        · 状态提示
  · 用 `team_presets` 的数据渲染可选项与成员名片；用 `team.py` 抛出的 on_event 事件驱动
    成员名片的「亮起 / 完成 / 失败」状态，于是协作过程能直接落在对话流这一侧。

本控件**完全自包含**：不 import main_window，可以独立实例化、独立测试。
v9.18.0 已接线（`main_window._build_shell`）：
    self.team_bar = TeamBar()
    self.team_bar.team_changed.connect(self._on_team_choice)   # 记到当前对话
    self.team_bar.open_page.connect(self._open_page)           # 看历史团队运行
    right.addWidget(self.team_bar)         # 夹在 state_line 与 input_card 之间
    # 用户在对话里选了团队 -> 这一轮交给 teamman.create(..., team_id=...) 去做
    # 团队跑起来后，team.py 的 on_event(kind, payload) 经 `_team_evt` 信号
    #   转到 UI 线程，再喂给 self.team_bar.apply_event
    ⚠ `apply_event` 会动控件，**只能在 UI 线程调**；后台线程抛来的事件必须
      先转成 Qt 信号（见 main_window._send_team 里的 `_on_event`）。
"""
from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QToolButton, QMenu,
                             QSizePolicy, QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal

from .. import themes, team_presets

# 成员名片的状态 -> 取色（run=正在跑、done=完成、fail=失败、queued=已派单待跑）
_STATE_TEXT = {"idle": "待命", "queued": "已派单", "run": "进行中",
               "done": "已完成", "fail": "失败"}


class TeamBar(QWidget):
    """对话内的团队选择条。

    对外信号：
      · team_changed(str)  —— 选中的团队 id（""=不使用团队），供外层记录并传给 teamman.create。
    对外方法：
      · list_team_ids() / selected_team_id() / current_team()
      · set_team(team_id)           选中某支团队（非法 id 视为不使用）
      · apply_event(kind, payload)  接 team.py 的 on_event，实时刷新成员名片状态
      · set_running(bool) / set_phase(text) / reset_transient()
      · refresh_theme()             主题切换时重新上色
      · member_state(member_id)     读某成员当前状态（探针/调试用）
    """

    team_changed = pyqtSignal(str)
    # ★ v9.18.0：团队功能搬进对话之后，侧栏不再给「团队」留格子（需求 13 + 10）。
    #   但"这支队伍上一轮干了什么"仍然值得回头看 —— 所以下拉菜单尾部留一个入口，
    #   由主窗口接到 `_open_page("team")`。控件本身不认识任何页面，保持自包含。
    open_page = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._team_id = ""
        self._running = False
        self._member_chips = {}         # member_id -> QLabel
        self._member_state = {}         # member_id -> 状态字符串

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(6)

        # 左侧：团队选择按钮（下拉菜单里列出所有预置团队）
        self.pick_btn = QToolButton()
        self.pick_btn.setObjectName("TeamPickBtn")
        self.pick_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.pick_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.pick_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.menu = QMenu(self)
        self.pick_btn.setMenu(self.menu)
        row.addWidget(self.pick_btn)

        # 中间：成员名片区（选中团队后把成员一个个摆出来）
        self.members_box = QWidget()
        self.members_layout = QHBoxLayout(self.members_box)
        self.members_layout.setContentsMargins(0, 0, 0, 0)
        self.members_layout.setSpacing(4)
        row.addWidget(self.members_box)

        # 右侧：状态提示（正在派单 / 第 2/3 批并行 / 已完成……）
        self.phase = QLabel("")
        self.phase.setObjectName("TeamPhase")
        self.phase.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row.addWidget(self.phase, 1)

        self._rebuild_menu()
        self.refresh_theme()
        self.set_team("")               # 默认不使用团队

    # ------------------------------------------------------------------
    # 团队选择
    # ------------------------------------------------------------------
    def list_team_ids(self):
        """所有可选团队 id（不含"不使用"）。"""
        return [p["id"] for p in team_presets.list_presets()]

    def selected_team_id(self):
        """当前选中的团队 id；""=不使用团队。"""
        return self._team_id

    def current_team(self):
        """当前团队定义（dict）；未选团队时返回 None。"""
        if not self._team_id:
            return None
        return team_presets.get_preset(self._team_id)

    def _rebuild_menu(self):
        """重建下拉菜单：先「不使用团队」，再逐个预置团队。"""
        self.menu.clear()
        act_none = self.menu.addAction("⊘ 不使用团队（普通对话）")
        act_none.triggered.connect(lambda: self.set_team(""))
        self.menu.addSeparator()
        for p in team_presets.list_presets():
            members = "、".join(m["name"] for m in p["members"])
            act = self.menu.addAction(f"{p['emoji']} {p['name']}")
            act.setToolTip(f"{p['desc']}\n成员：{members}")
            act.setCheckable(True)
            act.setChecked(p["id"] == self._team_id)
            act.triggered.connect(lambda _=False, tid=p["id"]: self.set_team(tid))
        # 历史入口：侧栏不再给「团队」留格子，但老运行记录仍然能翻出来看。
        self.menu.addSeparator()
        act_hist = self.menu.addAction("▥ 查看历史团队运行")
        act_hist.triggered.connect(lambda: self.open_page.emit("team"))

    def set_team(self, team_id):
        """选中某支团队并刷新成员名片。非法/空 id 一律当作"不使用团队"。"""
        tid = str(team_id or "").strip()
        if tid not in self.list_team_ids():
            tid = ""
        changed = (tid != self._team_id)
        self._team_id = tid
        self._rebuild_members()
        self._rebuild_menu()
        self._update_btn_text()
        if changed:
            self.team_changed.emit(tid)
        return tid

    def _update_btn_text(self):
        if self._team_id:
            t = team_presets.get_preset(self._team_id)
            self.pick_btn.setText(f"⬡ {t['emoji']} {t['name']} ▾")
            self.pick_btn.setToolTip(f"当前使用「{t['name']}」；点此切换或关闭\n{t['desc']}")
        else:
            self.pick_btn.setText("⬡ 团队：不使用 ▾")
            self.pick_btn.setToolTip("点击选择一支预置团队，让多位成员在对话里协作完成这件事")

    # ------------------------------------------------------------------
    # 成员名片
    # ------------------------------------------------------------------
    def _clear_members(self):
        while self.members_layout.count():
            item = self.members_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._member_chips = {}
        self._member_state = {}

    def _rebuild_members(self):
        """按当前团队重建成员名片（每个成员一张小标签）。"""
        self._clear_members()
        team = self.current_team()
        if not team:
            return
        for m in team["members"]:
            chip = QLabel(f"{m['emoji']} {m['name']}")
            chip.setObjectName("TeamMemberChip")
            chip.setToolTip(f"{m['name']} · {m['title']}\n{m['intro']}")
            self.members_layout.addWidget(chip)
            self._member_chips[m["id"]] = chip
            self._member_state[m["id"]] = "idle"
            self._style_chip(chip, "idle")

    def member_state(self, member_id):
        """读某成员名片当前状态：idle/queued/run/done/fail。"""
        return self._member_state.get(str(member_id), "idle")

    def _style_chip(self, chip, state):
        t = themes.tokens()
        if state == "run":
            bg, fg, bd = t["accent"], "#ffffff", t["accent"]
        elif state == "done":
            bg, fg, bd = t["chip_bg"], t["success"], t["success"]
        elif state == "fail":
            bg, fg, bd = t["chip_bg"], t["danger"], t["danger"]
        elif state == "queued":
            bg, fg, bd = t["chip_bg"], t["warn"], t["border"]
        else:
            bg, fg, bd = t["chip_bg"], t["chip_text"], t["border"]
        chip.setStyleSheet(
            f"background:{bg};color:{fg};border:1px solid {bd};"
            "border-radius:9px;padding:1px 8px;font-size:11px;")

    def _set_member_state(self, member_id, state, payload=None):
        mid = str(member_id or "")
        chip = self._member_chips.get(mid)
        if chip is None:
            return
        self._member_state[mid] = state
        self._style_chip(chip, state)
        note = _STATE_TEXT.get(state, state)
        if payload and payload.get("task_title"):
            note = f"{note}：{payload['task_title']}"
        chip.setToolTip(f"{chip.toolTip().splitlines()[0]}\n状态：{note}")

    def _set_all_states(self, state):
        for mid in list(self._member_chips.keys()):
            self._set_member_state(mid, state)

    # ------------------------------------------------------------------
    # 运行状态 / 事件驱动
    # ------------------------------------------------------------------
    def set_running(self, running):
        self._running = bool(running)

    def is_running(self):
        """当前是否有团队在跑（由 apply_event 维护）。"""
        return self._running

    def set_phase(self, text):
        self.phase.setText(str(text or ""))

    def reset_transient(self):
        """一次团队跑完/取消后，把名片状态复位为待命。"""
        self._set_all_states("idle")
        self.set_phase("")

    def apply_event(self, kind, payload):
        """接 team.py 的 on_event(kind, payload)，把协作过程映射到名片状态与状态提示。

        注意：事件可能从后台线程抛出 —— 外层应转成 Qt 信号再调这里（或确保在主线程调用）。
        """
        payload = payload or {}
        if kind == "phase":
            self.set_phase(payload.get("label") or "")
        elif kind == "dispatch":
            n = len(payload.get("tasks") or [])
            self._set_all_states("queued")
            self.set_phase(f"项目总监已派单 {n} 项，准备并行执行…")
        elif kind == "member_start":
            self.set_running(True)
            self._set_member_state(payload.get("member"), "run", payload)
        elif kind == "member_delta":
            pass                    # 流式片段不改变名片状态
        elif kind == "member_done":
            st = "done" if payload.get("status") == "done" else "fail"
            self._set_member_state(payload.get("member"), st, payload)
        elif kind == "done":
            self.set_running(False)
            s = payload.get("status")
            self.set_phase({"done": "✓ 团队已完成",
                            "failed": "✕ 团队执行失败",
                            "cancelled": "⊘ 团队已取消"}.get(s, "团队已结束"))

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def refresh_theme(self):
        t = themes.tokens()
        self.pick_btn.setStyleSheet(
            f"QToolButton#TeamPickBtn{{background:{t['btn_bg']};color:{t['btn_text']};"
            f"border:1px solid {t['btn_border']};border-radius:9px;padding:3px 10px;"
            "font-size:12px;}"
            f"QToolButton#TeamPickBtn:hover{{background:{t['btn_hover_bg']};}}"
            f"QToolButton#TeamPickBtn::menu-indicator{{image:none;width:0;}}")
        self.phase.setStyleSheet(f"color:{t['text_muted']};font-size:11px;")
        for mid, chip in self._member_chips.items():
            self._style_chip(chip, self._member_state.get(mid, "idle"))
