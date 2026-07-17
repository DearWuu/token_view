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

    # 站点提示：凭证提取失败时给用户的报错里用（"请打开 XX 并登录"）
    SITE_NAME = ""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def fetch(self) -> UsageData:
        raise NotImplementedError

    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        """是否已有可纯 HTTP 直连的凭证。

        返回 True 时 fetch 优先走 HTTP（不开浏览器），CDP 预检也不拦截。
        子类按各自凭证字段覆盖。
        """
        return False

    @classmethod
    def extract_credentials(cls, port: int = 9222, cdp_url: str = "") -> dict:
        """从 CDP Chrome 一次性提取登录凭证，返回可并入 cfg 的字段 dict。

        抛出 CDPNotConnected / CDPPageNotFound / CDPEvalError。
        """
        from .cdp import CDPError
        raise CDPError(f"{cls.__name__} 不支持凭证提取")

    # ---- 工具方法：构造 UsageData 时的常用错误 ----
    def _err(self, data: UsageData, msg: str) -> UsageData:
        data.status = "error"
        data.error = msg
        return data

    @staticmethod
    def _reset(data: UsageData) -> UsageData:
        """HTTP 直连失败回退 CDP 前，清掉上一次写入的错误状态。"""
        data.status = "ok"
        data.error = ""
        data.items.clear()
        return data

    def _fallback_cdp(self, data: UsageData, http_error: str,
                      cdp_fetch) -> UsageData:
        """直连失败后回退 CDP；CDP 也失败时返回直连的真实错误。

        避免误导用户去开 Chrome——直连失败的真实原因（如凭证过期）
        才是需要展示给用户的。仅当 CDP 确实兜底成功时才用 CDP 数据。
        """
        data = self._reset(data)
        cdp_result = cdp_fetch(data)
        if cdp_result.status == "error" and http_error:
            return self._err(data, http_error)
        return cdp_result


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
