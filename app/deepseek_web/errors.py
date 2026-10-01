# -*- coding: utf-8 -*-
"""DeepSeek 网页版接入层：统一失败类型 + 中文文案。

设计约定（见 docs/deepseek-web/02-arch.md 第 9 节）：
- 全项目**唯一**的错误枚举就是这里的 `WebErrorKind`；
- 任何一层出现失败，都先归类到它，再由 `humanize()` 转成"用户能看懂的中文"；
- 禁止在别处另造字符串错误码。
"""
from enum import Enum


class WebErrorKind(Enum):
    """DeepSeek 网页通道的状态 / 失败类型。"""

    NOT_INSTALLED = "not_installed"      # 没装 Chrome
    NOT_STARTED = "not_started"          # 网页没启动（调试端口不可用）
    NOT_LOGGED_IN = "not_logged_in"      # 网页打开了但没登录
    WINDOW_HIDDEN = "window_hidden"      # 窗口被最小化/藏起来（会导致发送失效）
    READY = "ready"                      # 已连接，可以对话
    GENERATING = "generating"            # 网页正在生成
    TIMEOUT = "timeout"                  # 等待网页返回超时（不重发）
    SERVER_ERROR = "server_error"        # 网页自身报错（服务器暂时不可用/已停止）
    PAGE_CHANGED = "page_changed"        # 网页改版，选择器失效
    DUPLICATE = "duplicate"              # 检测到重复请求，已阻止重发
    BUSY = "busy"                        # 通道忙（串行队列）
    OTHER = "other"                      # 其他未分类失败


# 失败/状态 -> 面向用户的中文文案（全部可操作，无英文堆栈）。
WEB_ERROR_TEXT = {
    WebErrorKind.NOT_INSTALLED:
        "未检测到 Chrome 浏览器。请先安装 Google Chrome，然后回到「设置 → 模型」点"
        "「启动/打开网页」。",
    WebErrorKind.NOT_STARTED:
        "DeepSeek 网页还没启动。请到「设置 → 模型 → DeepSeek 网页版」点"
        "「启动/打开网页」。",
    WebErrorKind.NOT_LOGGED_IN:
        "网页已打开，但还没有登录。请在刚打开的网页里登录一次 DeepSeek"
        "（手机号验证码 / 微信扫码都行），登录完成后回来点「连通性自检」。",
    WebErrorKind.READY:
        "已连接，可以对话。",
    WebErrorKind.WINDOW_HIDDEN:
        "DeepSeek 网页窗口当前**不可见**（被最小化、或被移到了屏幕外）。"
        "这种状态下网页收不到按键和点击，提问会填进输入框却发不出去。"
        "请把浏览器窗口恢复出来（点设置页的「显示网页窗口」），再重试。",
    WebErrorKind.GENERATING:
        "网页正在生成回复，请稍候…",
    WebErrorKind.TIMEOUT:
        "等待网页返回超时。可以点「重试」重新连接网页"
        "（不会重复发送同一条提问，请放心）。",
    WebErrorKind.SERVER_ERROR:
        "DeepSeek 网页自己报了服务器错误（如「服务器暂时不可用 / 服务已停止」）。"
        "这不是你账号的问题，程序也不会重复发送。请稍后到网页里刷新一下，再点「重试」。",
    WebErrorKind.PAGE_CHANGED:
        "网页结构可能已变，该功能暂不可用，已记录问题。"
        "请稍后升级程序版本后再试。",
    WebErrorKind.DUPLICATE:
        "检测到重复请求，已阻止重发，以免你的账号里出现两条一模一样的提问。"
        "如需继续，请点「重试」。",
    WebErrorKind.BUSY:
        "网页通道正忙（同一时间只能处理一条提问），稍等片刻再发就好。",
    WebErrorKind.OTHER:
        "调用 DeepSeek 网页失败。可以点「重试」重新连接网页后再试。",
}


# 状态（非失败）文案，供设置页状态行使用。
WEB_STATE_LABEL = {
    WebErrorKind.NOT_INSTALLED: "未检测到 Chrome",
    WebErrorKind.NOT_STARTED: "网页未启动",
    WebErrorKind.NOT_LOGGED_IN: "网页已打开，未登录",
    WebErrorKind.WINDOW_HIDDEN: "网页窗口不可见（会发不出去）",
    WebErrorKind.READY: "已连接，可以对话",
    WebErrorKind.GENERATING: "网页正在生成…",
    WebErrorKind.TIMEOUT: "等待超时",
    WebErrorKind.SERVER_ERROR: "网页服务器暂不可用",
    WebErrorKind.PAGE_CHANGED: "网页可能已改版",
    WebErrorKind.DUPLICATE: "已阻止重复发送",
    WebErrorKind.BUSY: "正在忙于上一条",
    WebErrorKind.OTHER: "连接异常",
}


def _as_kind(kind):
    """把 WebErrorKind / 字符串 / None 统一成 WebErrorKind。"""
    if isinstance(kind, WebErrorKind):
        return kind
    if kind is None:
        return WebErrorKind.OTHER
    try:
        return WebErrorKind(str(kind))
    except ValueError:
        return WebErrorKind.OTHER


def humanize(kind, detail=""):
    """把失败类型翻译成中文人话；detail 作为补充说明附在后面。

    Args:
        kind: WebErrorKind 或其字符串值（如 "not_logged_in"），也接受 None。
        detail: 可选补充（例如超时秒数、底层异常摘要）。

    Returns:
        面向用户的中文说明字符串；任何输入都保证返回非空中文文案。
    """
    k = _as_kind(kind)
    text = WEB_ERROR_TEXT.get(k) or WEB_ERROR_TEXT[WebErrorKind.OTHER]
    detail = (detail or "").strip()
    if detail and detail not in text:
        return f"{text}\n（补充：{detail}）"
    return text


def label_of(kind):
    """状态行的短标签（设置页圆点旁那行字）。"""
    k = _as_kind(kind)
    return WEB_STATE_LABEL.get(k, WEB_STATE_LABEL[WebErrorKind.OTHER])
