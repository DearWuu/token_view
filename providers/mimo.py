"""小米 MiMo Token Plan Provider。

取数方式（按优先级）：
  1. 凭证直连（推荐）：cookie 已提取时纯 HTTP 调 /api/v1/tokenPlan/usage
  2. CDP 模式：连接已登录调试 Chrome，在页面上下文 fetch（直连失败兜底）

返回格式：
  {
    "code": 0,
    "data": {
      "monthUsage": {"percent": 0.239, "items": [...]},
      "usage":      {"percent": 0.24,  "items": [...]}
    }
  }
"""
from __future__ import annotations

import json
import time

import requests

from logger import log

from .base import (BaseProvider, UsageData, UsageItem, BROWSER_UA, fmt_tokens,
                   next_month_start)
from .cdp import CDPHarness, CDPError, extract_domain_cookies, cookie_header


class MimoProvider(BaseProvider):
    """小米 MiMo Token Plan。"""

    API_PATH = "/api/v1/tokenPlan/usage"
    SITE_NAME = "platform.xiaomimimo.com"

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "小米 MiMo"
        data = UsageData(
            provider_name=name, plan_level="Token Plan", fetched_at=time.time())

        # 优先凭证直连：cookie 已提取时纯 HTTP，不开浏览器
        if self.has_direct_credentials(self.cfg):
            result = self._fetch_http(data)
            if result.status != "error":
                return result
            log(f"MiMo 凭证直连失败（{result.error}），尝试 CDP 兜底")
            if not self.cfg.get("cdp_enabled", True):
                return result
            return self._fallback_cdp(data, result.error, self._fetch_cdp)

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        return self._err(data,
            "未配置 cookie（请在设置里打开 CDP Chrome 登录后点「提取凭证」）")

    # ---- 凭证直连模式 ----
    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        return bool((cfg.get("cookie") or "").strip())

    @classmethod
    def extract_credentials(cls, port: int = 9222, cdp_url: str = "") -> dict:
        _, cookies = extract_domain_cookies(
            port, cdp_url, "xiaomimimo.com", "xiaomimimo")
        if not cookies:
            raise CDPError(
                "未找到 cookie：请先在调试 Chrome 里登录 platform.xiaomimimo.com")
        return {"cookie": cookie_header(cookies)}

    def _fetch_http(self, data: UsageData) -> UsageData:
        headers = {
            "Cookie": self.cfg["cookie"].strip(),
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Referer": "https://platform.xiaomimimo.com/",
        }
        try:
            r = requests.get(
                "https://platform.xiaomimimo.com" + self.API_PATH,
                headers=headers, timeout=20)
        except requests.RequestException as e:
            return self._err(data, f"网络错误: {e}")
        if r.status_code >= 400:
            return self._err(data, f"HTTP {r.status_code}（cookie 可能已失效）")
        return self._parse_json(r.text, data)

    # ---- CDP 模式 ----

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        port = int(self.cfg.get("cdp_port") or 9222)
        harness = CDPHarness(
            port=port,
            page_keyword="xiaomimimo.com",
            cdp_url=self.cfg.get("cdp_url") or "",
            eval_timeout=30,
        )

        try:
            page = harness.find_page()
        except CDPError as e:
            return self._translate_err(data, e)

        js = (
            "(async()=>{"
            "const u=new URL(" + json.dumps(self.API_PATH) + ",location.origin);"
            "u.searchParams.set('_tv',Date.now());"
            "const r=await fetch(u.href,{"
            "credentials:'include',"
            "headers:{'Cache-Control':'no-cache','Pragma':'no-cache'},"
            "cache:'no-store'"
            "});"
            "return await r.text();})()"
        )

        try:
            result = harness.evaluate(
                page["webSocketDebuggerUrl"], js, await_promise=True)
        except CDPError as e:
            return self._translate_err(data, e)

        text = result.get("value") or ""
        if not text.strip():
            return self._err(data, "API 返回空（登录可能已失效）")
        return self._parse_json(text, data)

    def _translate_err(self, data: UsageData, e: CDPError) -> UsageData:
        from .cdp import CDPPageNotFound, CDPEvalError, CDPNotConnected
        if isinstance(e, CDPPageNotFound):
            return self._err(data, "请在 CDP Chrome 里打开 platform.xiaomimimo.com 并登录")
        if isinstance(e, CDPEvalError):
            return self._err(data, f"API 调用失败（登录可能已过期）: {e}")
        if isinstance(e, CDPNotConnected):
            return self._err(data, "请先在设置里启动 CDP Chrome 并登录")
        return self._err(data, str(e))

    def _parse_json(self, text: str, data: UsageData) -> UsageData:
        try:
            j = json.loads(text)
        except ValueError as e:
            return self._err(data, f"JSON 解析失败: {e}")

        if j.get("code") != 0:
            return self._err(data, j.get("message") or "API 返回错误")

        d = j.get("data") or {}
        # 只取月度额度（Plan 总额）
        month = d.get("monthUsage") or {}
        month_pct = month.get("percent")
        if month_pct is not None:
            pct = float(month_pct) * 100  # 0.239 -> 23.9
            items = month.get("items") or []
            note = ""
            if items:
                it = items[0]
                used = it.get("used", 0)
                limit = it.get("limit", 0)
                if limit > 0:
                    note = f"{fmt_tokens(used)} / {fmt_tokens(limit)} tokens"
            # 接口不返回重置时间，月度额度按自然月（次月 1 号）估算
            data.items.append(
                UsageItem("Monthly usage", pct, next_month_start(), note=note))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据"
        return data
