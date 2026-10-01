# -*- coding: utf-8 -*-
"""DeepSeek 网页版接入子包（全部自研）。

固定入口：
    from app.deepseek_web import service
    service.instance()          # 进程内单例协调器

分工（三层，见 docs/deepseek-web/02-arch.md）：
    cdp_client   —— 第一层：Chrome 调试协议（只懂协议）
    web_driver   —— 第二层：DeepSeek 网页驱动 + 状态机（只懂网页）
    local_server —— 第三层：本机 OpenAI 兼容服务（讲 OpenAI 协议、串行队列、幂等）
    service      —— 单例协调器：起停服务 / 拉与收拾 Chrome / 状态 / 自检 / 通知
    prompt       —— messages <-> 单段提示词的纯函数
    errors       —— 统一失败类型与中文文案
"""
__version__ = "1.0.0"

from . import errors
from .errors import WebErrorKind, humanize, label_of
from . import prompt
from . import cdp_client
from . import web_driver
from . import local_server
from . import service

__all__ = [
    "errors", "prompt", "cdp_client", "web_driver", "local_server", "service",
    "WebErrorKind", "humanize", "label_of",
    "__version__",
]
