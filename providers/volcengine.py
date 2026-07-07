"""火山引擎 Ark Agent Plan Provider。

通过 CDP 在已登录页面上下文 POST GetAgentPlanAFPUsage 拿用量。

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
import time

from .base import BaseProvider, UsageData, UsageItem, fmt_tokens
from .cdp import CDPHarness, CDPError


class VolcEngineProvider(BaseProvider):
    """火山引擎 Ark Agent Plan。"""

    API_PATH = "/api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage"
    PLAN_LABELS = {
        "large": "Large",
        "medium": "Medium",
        "small": "Small",
    }

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "火山 Ark"
        data = UsageData(
            provider_name=name, plan_level="Agent Plan", fetched_at=time.time())

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        return self._err(data, "请启用 CDP 模式并在 CDP Chrome 里登录 console.volcengine.com")

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
