"""Kimi 团队版（团队空间）订阅额度 Provider。

与个人版（kimi.py）的区别只剩展示层：
  - STATS_REFERER 指向团队订阅页
  - plan_level 显示「团队版」
  - 订阅额度 label 改为「本月窗口」（团队空间按月循环）

取数机制 2026-08 起与个人版完全一致（账号网关 access_token/refresh_token，
localStorage 提取 + auth.kimi.com 刷新），实现全部继承自 KimiProvider。
"""
from __future__ import annotations

from .base import UsageData
from .kimi import KimiProvider


class KimiTeamProvider(KimiProvider):
    """Kimi 团队空间订阅额度与速率窗口用量。"""

    PAGE_KEYWORD = "kimi.com"
    SITE_NAME = "www.kimi.com（团队空间）"
    STATS_REFERER = "https://www.kimi.com/membership/subscription?tab=quota"
    PLAN_LEVEL = "团队版"

    def _parse_json(self, text: str, data: UsageData) -> UsageData:
        result = super()._parse_json(text, data)
        # 团队空间的 subscriptionBalance 是按月循环的总额度，
        # 前端约定"月"字样才渲染 30 天倒计时圆环
        for item in result.items:
            if item.label == "订阅额度":
                item.label = "本月窗口"
        return result
