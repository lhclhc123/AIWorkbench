# -*- coding: utf-8 -*-
"""IM 助手子系统（钉钉等即时通讯）：统一消息总线 + 多身份 + 记忆 + 定时 + 日志。

分层：
    store.py     持久化（会话档案 / 消息流水 / 任务 / 日志本 / 记忆 / 设置）
    policy.py    "回不回、怎么回"（三种模式）+ 系统提示 + 拿不准哨兵
    scheduler.py 定时主动消息（每日/每周/每月，纯函数可测）
    logbook.py   「谁·哪天·干什么」抽取与查询
    tags.py      会话全量扫描 + AI 打关系标签（纯逻辑，能力靠注入）
    hub.py       编排：收 → 判定 → 组上下文 → 调模型 → 回 → 落盘
"""
from .store import (IMStore, MODES, DEFAULT_MODE,             # noqa: F401
                    IM_SETTINGS_FILE, IM_DEFAULT_SETTINGS)
from .hub import IMHub                                   # noqa: F401
from .pipeline import ReplyPipeline                      # noqa: F401
from .policy import UNCERTAIN_MARK                       # noqa: F401
from . import policy, scheduler, logbook, tags           # noqa: F401

__all__ = ["IMStore", "IMHub", "ReplyPipeline", "MODES", "DEFAULT_MODE",
           "IM_SETTINGS_FILE", "IM_DEFAULT_SETTINGS", "UNCERTAIN_MARK",
           "policy", "scheduler", "logbook", "tags"]
