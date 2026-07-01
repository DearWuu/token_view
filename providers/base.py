"""用量数据模型 + 通用工具。

所有 provider 的 fetch() 必须返回 UsageData，items 列表里的每个 UsageItem
对应一个用量窗口（5h / 周 / 月 / MCP 等）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


def fmt_tokens(n) -> str:
    """把 token 数字格式化成易读字符串。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1e8:
        return f"{n / 1e8:.1f}亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f}万"
    return str(int(n))


@dataclass
class UsageItem:
    """单个用量窗口。"""
    label: str
    used_percent: float                # 已用百分比 0~100
    reset_at: Optional[float] = None    # 重置时间（Unix timestamp）
    note: str = ""                      # 副文本（如 "100万 / 500万 tokens"）


@dataclass
class UsageData:
    """单次 fetch 的完整结果。"""
    provider_name: str
    plan_level: str = ""                # 例如 "团队·张三" / "Max5" / "Go"
    items: list[UsageItem] = field(default_factory=list)
    status: str = "ok"                  # ok / error / empty
    error: str = ""
    fetched_at: float = 0.0             # Unix timestamp


class BaseProvider:
    """所有 provider 的基类。子类必须实现 fetch()。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def fetch(self) -> UsageData:
        raise NotImplementedError

    # ---- 工具方法：构造 UsageData 时的常用错误 ----
    def _err(self, data: UsageData, msg: str) -> UsageData:
        data.status = "error"
        data.error = msg
        return data


# 浏览器 UA：模拟真实 Chrome，避开基础反爬
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# websocket-client 可选依赖检查
try:
    from websocket import create_connection as ws_connect
    HAS_WS = True
except Exception as _e:  # noqa: BLE001
    ws_connect = None
    HAS_WS = False
