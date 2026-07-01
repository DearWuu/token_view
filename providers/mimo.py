"""小米 MiMo Token Plan Provider。

通过 CDP 调 /api/v1/tokenPlan/usage 拿月度用量。

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

from .base import BaseProvider, UsageData, UsageItem, BROWSER_UA, fmt_tokens
from .cdp import CDPHarness, CDPError


class MimoProvider(BaseProvider):
    """小米 MiMo Token Plan。"""

    API_PATH = "/api/v1/tokenPlan/usage"

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "小米 MiMo"
        data = UsageData(
            provider_name=name, plan_level="Token Plan", fetched_at=time.time())

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        cookie = (self.cfg.get("cookie") or "").strip()
        if not cookie:
            return self._err(data,
                "未配置 cookie（请在设置里打开 CDP Chrome 并登录 platform.xiaomimimo.com）")
        headers = {
            "Cookie": cookie,
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
            data.items.append(UsageItem("Monthly usage", pct, note=note))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据"
        return data
