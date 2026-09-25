# -*- coding: utf-8 -*-
"""主题：浅色 / 深色。

同时做两件事：
1. 提供统一的色板 token，所有卡片/代码框都从 token 取色，不再硬编码颜色；
2. 用 Fusion 风格 + 显式 QPalette 强制覆盖系统配色，
   解决 Windows 深色模式下 QLabel / QLineEdit / 各种对话框文字看不清的问题。
"""

LIGHT = {
    "window_bg": "#f4f6f8", "chat_bg": "#f4f6f8", "panel_bg": "#ffffff",
    "text": "#1f2328", "text_muted": "#7a828b",
    "user_bg": "#2196F3", "user_text": "#ffffff", "user_label": "#e3f2fd",
    "ai_bg": "#eef1f4", "ai_text": "#1f2328", "ai_label": "#5a6270",
    "tool_bg": "#fff8e1", "tool_text": "#4a4200", "tool_label": "#8a7a2a",
    "border": "#e3e6ea",
    "code_bg": "#f6f8fa", "code_text": "#24292e",
    "code_head_bg": "#eef1f4", "code_border": "#dfe3e8",
    "accent": "#2196F3", "accent_hover": "#1976D2", "accent_press": "#0D47A1",
    "accent_text": "#0d47a1", "accent_soft": "#e3f2fd",
    "btn_bg": "#ffffff", "btn_text": "#1976D2", "btn_border": "#cfd8e3",
    "btn_hover_bg": "#e3f2fd",
    "input_bg": "#ffffff", "input_border": "#d0d7de",
    "list_bg": "#ffffff", "list_hover": "#e3f2fd",
    "list_sel_bg": "#bbdefb", "list_sel_text": "#0d47a1",
    "thinking_bg": "#eef1f4", "status_bg": "#e3f2fd",
    # —— v9 新增：侧边导航 / 卡片 / 标签 / 状态色 ——
    "nav_bg": "#ffffff", "nav_border": "#e3e6ea",
    "nav_text": "#5a6270", "nav_hover_bg": "#eef1f4",
    "nav_active_bg": "#e3f2fd", "nav_active_text": "#0d47a1",
    "header_bg": "#ffffff",
    "card_bg": "#ffffff", "card_hover": "#f1f5fb", "card_border": "#e3e6ea",
    "chip_bg": "#eef1f4", "chip_text": "#5a6270",
    "success": "#1a7f4b", "warn": "#b7791f", "danger": "#c0392b",
    "wave": "#2196F3", "wave_dim": "#cfe4fb",
}

DARK = {
    "window_bg": "#1e2126", "chat_bg": "#1e2126", "panel_bg": "#252a31",
    "text": "#e6edf3", "text_muted": "#9aa4af",
    "user_bg": "#2d6fd6", "user_text": "#ffffff", "user_label": "#cfe3fb",
    "ai_bg": "#2a2f37", "ai_text": "#e6edf3", "ai_label": "#a8b3c0",
    "tool_bg": "#3a3423", "tool_text": "#f0e6c8", "tool_label": "#c9b96f",
    "border": "#363c45",
    "code_bg": "#161a1f", "code_text": "#d5dde6",
    "code_head_bg": "#22272e", "code_border": "#363c45",
    "accent": "#3b82f6", "accent_hover": "#2f6fd0", "accent_press": "#2559ab",
    "accent_text": "#8ab4f8", "accent_soft": "#22303f",
    "btn_bg": "#2a2f37", "btn_text": "#8ab4f8", "btn_border": "#3d444d",
    "btn_hover_bg": "#333a43",
    "input_bg": "#252a31", "input_border": "#3d444d",
    "list_bg": "#252a31", "list_hover": "#2a2f37",
    "list_sel_bg": "#2d4a73", "list_sel_text": "#cfe3fb",
    "thinking_bg": "#2a2f37", "status_bg": "#22303f",
    # —— v9 新增：侧边导航 / 卡片 / 标签 / 状态色 ——
    "nav_bg": "#20242a", "nav_border": "#31363e",
    "nav_text": "#a8b3c0", "nav_hover_bg": "#2a2f37",
    "nav_active_bg": "#22303f", "nav_active_text": "#8ab4f8",
    "header_bg": "#20242a",
    "card_bg": "#252a31", "card_hover": "#2c333c", "card_border": "#31363e",
    "chip_bg": "#2a2f37", "chip_text": "#a8b3c0",
    "success": "#3fb950", "warn": "#d29922", "danger": "#f85149",
    "wave": "#3b82f6", "wave_dim": "#2a3a4f",
}

_current = "light"
_FONT_SCALE = 100


def tokens():
    return DARK if _current == "dark" else LIGHT


def set_theme(name):
    global _current
    _current = "dark" if name == "dark" else "light"


def set_font_scale(percent):
    global _FONT_SCALE
    try:
        _FONT_SCALE = max(80, min(140, int(percent or 100)))
    except Exception:
        _FONT_SCALE = 100


def fs(px, unit="px"):
    """按界面缩放换算字号字符串，例如 fs(16) -> '17.6px'。"""
    try:
        return f"{float(px) * _FONT_SCALE / 100:.2f}{unit}"
    except Exception:
        return f"{px}{unit}"


def qss(t=None):
    t = t or tokens()
    return f"""
QWidget {{ font-family: "Microsoft YaHei", "SimHei", "Segoe UI", sans-serif; }}
QMainWindow, QDialog {{ background: {t['window_bg']}; }}
QLabel {{ color: {t['text']}; }}

QPushButton {{
    background: {t['accent']}; color: #ffffff; border: none;
    border-radius: 10px; padding: 8px 16px; font-size: 13px;
}}
QPushButton:hover {{ background: {t['accent_hover']}; }}
QPushButton:pressed {{ background: {t['accent_press']}; }}
QPushButton:disabled {{ background: #9aa0a6; color: #f0f0f0; }}
QPushButton#cardBtn {{
    background: {t['btn_bg']}; color: {t['btn_text']};
    border: 1px solid {t['btn_border']}; border-radius: 8px;
    padding: 2px 8px; font-size: 11px; min-width: 0;
}}
QPushButton#cardBtn:hover {{ background: {t['btn_hover_bg']}; }}

QComboBox {{ font-size: 14px; color: {t['text']}; }}
QComboBox {{
    padding: 7px 12px; border: 1px solid {t['input_border']};
    border-radius: 10px; background: {t['input_bg']}; min-width: 220px;
}}
QComboBox:hover {{ border: 1px solid {t['accent']}; }}
QComboBox QAbstractItemView {{
    border: 1px solid {t['input_border']}; border-radius: 8px;
    background: {t['input_bg']}; color: {t['text']};
    selection-background-color: {t['list_sel_bg']};
    selection-color: {t['list_sel_text']};
    padding: 4px; outline: none;
}}

QCheckBox {{ color: {t['text']}; spacing: 8px; padding: 5px 10px; font-size: 14px; }}
QCheckBox:hover {{ color: {t['accent_text']}; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border: 1px solid {t['btn_border']};
    border-radius: 5px; background: {t['input_bg']};
}}
QCheckBox::indicator:hover {{ border: 1px solid {t['accent']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border: 1px solid {t['accent']}; }}

QLineEdit, QPlainTextEdit {{
    color: {t['text']}; background: {t['input_bg']};
    border: 1px solid {t['input_border']}; border-radius: 10px;
    padding: 8px 10px; font-size: 15px; selection-background-color: {t['accent']};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {t['accent']}; }}

QListWidget {{
    border: 1px solid {t['border']}; border-radius: 10px;
    background: {t['list_bg']}; color: {t['text']}; padding: 5px; outline: none;
}}
QListWidget::item {{ padding: 9px 11px; border-radius: 8px; color: {t['text']}; }}
QListWidget::item:hover {{ background: {t['list_hover']}; }}
QListWidget::item:selected {{
    background: {t['list_sel_bg']}; color: {t['list_sel_text']}; font-weight: bold;
}}

QScrollArea {{ border: none; background: {t['chat_bg']}; }}
QScrollBar:vertical {{
    background: {t['chat_bg']}; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t['btn_border']}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

QStatusBar {{ background: {t['status_bg']}; color: {t['accent_text']}; }}
QToolTip {{ color: {t['text']}; background: {t['panel_bg']}; border: 1px solid {t['border']}; }}

/* ---------- v9：侧边导航 ---------- */
QWidget#NavRail {{ background: {t['nav_bg']}; border-right: 1px solid {t['nav_border']}; }}
QPushButton#NavBtn {{
    background: transparent; color: {t['nav_text']}; border: none;
    border-radius: 10px; padding: 9px 6px; font-size: 12px; text-align: center;
}}
QPushButton#NavBtn:hover {{ background: {t['nav_hover_bg']}; color: {t['text']}; }}
QPushButton#NavBtn:checked {{
    background: {t['nav_active_bg']}; color: {t['nav_active_text']}; font-weight: bold;
}}

/* ---------- v9：欢迎页快捷卡片 ---------- */
QPushButton#QuickCard {{
    background: {t['card_bg']}; color: {t['text']};
    border: 1px solid {t['card_border']}; border-radius: 12px;
    padding: 14px 16px; font-size: 14px; text-align: left;
}}
QPushButton#QuickCard:hover {{ background: {t['card_hover']}; border: 1px solid {t['accent']}; }}

/* ---------- v9：顶部信息条与标签 ---------- */
QWidget#TopBar {{ background: {t['header_bg']}; border-bottom: 1px solid {t['nav_border']}; }}
QLabel#Chip {{
    background: {t['chip_bg']}; color: {t['chip_text']};
    border-radius: 9px; padding: 3px 10px; font-size: 11px;
}}
QLabel#PageTitle {{ font-size: 17px; font-weight: bold; color: {t['accent_text']}; }}
QLabel#PageSub {{ font-size: 12px; color: {t['text_muted']}; }}

/* ---------- v9.1：附件小卡片 ---------- */
QWidget#AttachBar {{ background: transparent; }}
QFrame#AttachChip {{
    background: {t['chip_bg']}; border: 1px solid {t['border']};
    border-radius: 9px;
}}
QPushButton#AttachX {{
    background: transparent; color: {t['text_muted']};
    border: none; border-radius: 9px; font-size: 12px;
}}
QPushButton#AttachX:hover {{ background: {t['btn_hover_bg']}; color: {t['accent_text']}; }}
QTableWidget {{ background: {t['list_bg']}; color: {t['text']}; gridline-color: {t['border']}; }}
QHeaderView::section {{
    background: {t['header_bg']}; color: {t['text']};
    border: 1px solid {t['border']}; padding: 5px;
}}

/* ---------- v9：语音按钮 ---------- */
QPushButton#MicBtn {{
    background: {t['btn_bg']}; color: {t['btn_text']};
    border: 1px solid {t['btn_border']}; border-radius: 20px;
    font-size: 17px; padding: 0px;
}}
QPushButton#MicBtn:hover {{ background: {t['btn_hover_bg']}; border: 1px solid {t['accent']}; }}
QPushButton#MicBtn:checked {{
    background: {t['danger']}; color: #ffffff; border: 1px solid {t['danger']};
}}

/* ---------- v9：消息操作按钮 ---------- */
QPushButton#MsgAction {{
    background: transparent; color: {t['text_muted']}; border: none;
    font-size: 11px; padding: 1px 6px; border-radius: 6px;
}}
QPushButton#MsgAction:hover {{ background: {t['chip_bg']}; color: {t['accent_text']}; }}

QTextBrowser {{ background: transparent; border: none; }}
QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 10px; background: {t['panel_bg']}; }}
QTabBar::tab {{
    background: {t['chip_bg']}; color: {t['chip_text']};
    padding: 7px 16px; margin-right: 4px; border-radius: 8px; font-size: 13px;
}}
QTabBar::tab:selected {{ background: {t['accent']}; color: #ffffff; font-weight: bold; }}
QSplitter::handle {{ background: {t['border']}; }}
QGroupBox {{
    border: 1px solid {t['border']}; border-radius: 10px;
    margin-top: 14px; padding-top: 10px; font-size: 13px; color: {t['text']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {t['accent_text']}; }}
"""


def apply(app, name="light"):
    """应用主题：Fusion 风格 + 显式调色板 + QSS。"""
    from PyQt6.QtGui import QPalette, QColor
    set_theme(name)
    t = tokens()

    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(t["window_bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(t["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(t["input_bg"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(t["panel_bg"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["panel_bg"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(t["text"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(t["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(t["panel_bg"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(t["text"]))
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["text_muted"]))
    app.setPalette(pal)
    app.setStyleSheet(qss(t))
    return t
