"""火山引擎 Ark Agent Plan Provider。

取数方式（按优先级）：
  1. 凭证直连：cookie 已提取时纯 HTTP POST GetAgentPlanAFPUsage
     （x-csrf-token 从 cookie 里的 csrfToken 取；csrf 失效时回退 CDP）
  2. CDP 模式：连接已登录调试 Chrome，reload 页面刷新 CSRF 后页面内 POST

API：POST /api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage
Body: {"projectName":"default"}
需要 cookie 认证 + x-csrf-token（从 csrfToken cookie 读）

返回格式：
  {
    "Result": {
      "PlanType": "large",
      "AFPFiveHour": {"Quota": 25000, "Used": 7424, "ResetTime": ...},
      "AFPWeekly":  {"Quota": 125000, "Used": 24261, "ResetTime": ...},
      "AFPMonthly": {"Quota": 250000, "Used": 55665, "ResetTime": ...},
      "AFPDaily":   {"Quota": 87500, "Used": 0, "ResetTime": ...}
    }
  }
"""
from __future__ import annotations

import json
import re
import time

import requests

from logger import log

from .base import BaseProvider, UsageData, UsageItem, BROWSER_UA, fmt_tokens
from .cdp import CDPHarness, CDPError, extract_domain_cookies, cookie_header


class VolcEngineProvider(BaseProvider):
    """火山引擎 Ark Agent Plan。"""

    API_PATH = "/api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage"
    SITE_NAME = "console.volcengine.com"
    PLAN_LABELS = {
        "large": "Large",
        "medium": "Medium",
        "small": "Small",
    }

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "火山 Ark"
        data = UsageData(
            provider_name=name, plan_level="Agent Plan", fetched_at=time.time())

        # 优先凭证直连：cookie 已提取时纯 HTTP，不开浏览器
        if self.has_direct_credentials(self.cfg):
            result = self._fetch_http(data)
            if result.status != "error":
                return result
            log(f"火山凭证直连失败（{result.error}），尝试 CDP 兜底")
            if not self.cfg.get("cdp_enabled", True):
                return result
            return self._fallback_cdp(data, result.error, self._fetch_cdp)

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        return self._err(data, "请在设置里点「提取凭证」，或启用 CDP 并登录 console.volcengine.com")

    # ---- 凭证直连模式 ----
    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        return bool((cfg.get("cookie") or "").strip())

    @classmethod
    def extract_credentials(cls, port: int = 9222, cdp_url: str = "") -> dict:
        _, cookies = extract_domain_cookies(
            port, cdp_url, "volcengine.com", "volcengine")
        if not cookies:
            raise CDPError(
                "未找到 cookie：请先在调试 Chrome 里登录 console.volcengine.com")
        return {"cookie": cookie_header(cookies)}

    def _fetch_http(self, data: UsageData) -> UsageData:
        cookie = self.cfg["cookie"].strip()
        m = re.search(r"(?:^|;\s*)csrfToken=([^;]+)", cookie)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie,
            "User-Agent": BROWSER_UA,
            "Referer": "https://console.volcengine.com/",
            "Origin": "https://console.volcengine.com",
        }
        if m:
            headers["x-csrf-token"] = m.group(1)
        try:
            r = requests.post(
                "https://console.volcengine.com" + self.API_PATH,
                headers=headers, json={"projectName": "default"}, timeout=20)
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
            page_keyword="volcengine.com",
            cdp_url=self.cfg.get("cdp_url") or "",
            eval_timeout=30,
        )

        try:
            page = harness.find_page()
        except CDPError as e:
            return self._translate_err(data, e)

        ws_url = page.get("webSocketDebuggerUrl", "")

        # 先 reload 页面刷新 session/CSRF token，再 POST API 拿最新数据
        try:
            harness.page_reload(ws_url, ignore_cache=True, wait_load=True, settle=1.5)
        except CDPError as e:
            return self._translate_err(data, e)

        # 在页面上下文 POST GetAgentPlanAFPUsage
        # 从 cookie 读 csrfToken 加 x-csrf-token 头
        # x-web-id 尝试从 localStorage / meta 读取，找不到就跳过
        js = (
            "(async()=>{"
            "var csrfM=/csrfToken=([^;]+)/.exec(document.cookie);"
            "var csrf=csrfM?csrfM[1]:'';"
            "var webId='';"
            "try{webId=localStorage.getItem('x-web-id')||'';}catch(e){}"
            "if(!webId){try{webId=localStorage.getItem('web_id')||'';}catch(e){}}"
            "if(!webId){var m=document.querySelector('meta[name=\"x-web-id\"]');if(m)webId=m.content;}"
            "var h={'Content-Type':'application/json','Accept':'application/json, text/plain, */*'};"
            "if(csrf)h['x-csrf-token']=csrf;"
            "if(webId)h['x-web-id']=webId;"
            "var r=await fetch(" + json.dumps(self.API_PATH) + ",{"
            "method:'POST',credentials:'include',headers:h,"
            "body:'{\"projectName\":\"default\"}',cache:'no-store'"
            "});"
            "return await r.text();})()"
        )

        try:
            result = harness.evaluate(ws_url, js, await_promise=True)
        except CDPError as e:
            return self._translate_err(data, e)

        text = result.get("value") or ""
        if not text.strip():
            return self._err(data, "API 返回空（登录可能已失效）")
        return self._parse_json(text, data)

    def _translate_err(self, data: UsageData, e: CDPError) -> UsageData:
        from .cdp import CDPPageNotFound, CDPEvalError, CDPNotConnected
        if isinstance(e, CDPPageNotFound):
            return self._err(data, "请在 CDP Chrome 里打开 console.volcengine.com 并登录")
        if isinstance(e, CDPEvalError):
            return self._err(data, f"API 调用失败（登录可能已过期）: {e}")
        if isinstance(e, CDPNotConnected):
            return self._err(data, "请先在设置里启动 CDP Chrome 并登录火山引擎")
        return self._err(data, str(e))

    def _parse_json(self, text: str, data: UsageData) -> UsageData:
        try:
            j = json.loads(text)
        except ValueError as e:
            return self._err(data, f"JSON 解析失败: {e}")

        result = j.get("Result") or {}
        if not result:
            err_msg = ""
            resp_meta = j.get("ResponseMetadata") or {}
            if resp_meta.get("Error"):
                err_msg = resp_meta["Error"].get("Message", "")
            return self._err(data, err_msg or "响应缺少 Result 字段")

        plan_type = result.get("PlanType") or ""
        if plan_type:
            data.plan_level = self.PLAN_LABELS.get(plan_type, plan_type)

        for key, label in [
            ("AFPFiveHour", "5h 窗口"),
            ("AFPWeekly", "每周窗口"),
            ("AFPMonthly", "每月窗口"),
        ]:
            slot = result.get(key) or {}
            quota = slot.get("Quota")
            used = slot.get("Used")
            reset_time = slot.get("ResetTime")
            if quota is not None and used is not None:
                pct = float(used) / float(quota) * 100 if quota > 0 else 0.0
                reset_at = reset_time / 1000 if isinstance(reset_time, (int, float)) and reset_time > 0 else None
                note = f"{fmt_tokens(used)} / {fmt_tokens(quota)}"
                data.items.append(UsageItem(label, pct, reset_at, note))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据"
        return data
