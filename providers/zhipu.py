"""智谱 GLM Coding Plan Provider。

三种取数方式（按优先级）：
  1. CDP 模式（默认，唯一能拿到团队版用量的方案）
  2. Cookie + usage_url（旧路径，已被反爬限制）
  3. API Key（个人版，/api/monitor/usage/quota/limit）

智谱 Authorization 不加 Bearer 前缀。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import requests

from .base import BaseProvider, UsageData, UsageItem, BROWSER_UA, fmt_tokens
from .cdp import CDPHarness, CDPError

# 智谱个人版 limits 里 unit 字段 → 窗口语义
ZHIPU_UNIT_LABEL = {3: "5h 窗口", 6: "每周窗口"}


class ZhipuProvider(BaseProvider):
    """智谱 GLM Coding Plan。"""

    TEAM_RANK_URL = "https://bigmodel.cn/api/monitor/usage/sub-account-rank"
    CDP_EVAL_TIMEOUT = 30

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "智谱 GLM"
        data = UsageData(provider_name=name, fetched_at=time.time())

        # 优先 CDP：连接用户已登录的调试 Chrome，在页面上下文 fetch
        if self.cfg.get("cdp_enabled", True):
            return self._fetch_team_cdp(data)

        cookie = (self.cfg.get("cookie") or "").strip()
        usage_url = (self.cfg.get("usage_url") or "").strip()
        key = (self.cfg.get("api_key") or "").strip()
        auth = (self.cfg.get("auth_token") or "").strip()

        if cookie and usage_url:
            headers = {
                "User-Agent": BROWSER_UA,
                "Cookie": cookie,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://bigmodel.cn/",
            }
            if auth:
                headers["Authorization"] = auth
            if "sub-account-rank" in usage_url:
                now = datetime.now()
                params = {
                    "startTime": (now - timedelta(days=6)).strftime("%Y-%m-%d") + " 00:00:00",
                    "endTime": now.strftime("%Y-%m-%d") + " 23:59:59",
                    "pageNum": 1, "pageSize": 20, "keyword": "",
                }
                return self._get(self.TEAM_RANK_URL, headers, data, params, self._parse_team)
            return self._get(usage_url, headers, data, None, self._parse_limits)

        if key:
            base = (self.cfg.get("base_url") or "https://open.bigmodel.cn").rstrip("/")
            endpoint = self.cfg.get("endpoint") or "/api/monitor/usage/quota/limit"
            headers = {
                "Authorization": key,
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
                "Content-Type": "application/json",
                "User-Agent": BROWSER_UA,
                "Referer": "https://bigmodel.cn/",
                "Origin": "https://bigmodel.cn",
            }
            return self._get(base + endpoint, headers, data, None, self._parse_limits)

        return self._err(data, "未配置：请用 API Key，或在设置里登录抓取凭证")

    # ---- CDP 模式 ----
    def _fetch_team_cdp(self, data: UsageData) -> UsageData:
        port = int(self.cfg.get("cdp_port") or 9222)
        harness = CDPHarness(
            port=port,
            page_keyword="bigmodel.cn",
            cdp_url=self.cfg.get("cdp_url") or "",
            eval_timeout=self.CDP_EVAL_TIMEOUT,
        )

        try:
            page = harness.find_page()
        except CDPError as e:
            return self._err(data, self._translate_cdp_error(e))

        # 生成时间范围 + 页面内 fetch 的 JS 源码
        now = datetime.now()
        st = (now - timedelta(days=6)).strftime("%Y-%m-%d") + " 00:00:00"
        et = now.strftime("%Y-%m-%d") + " 23:59:59"
        cache_bust = int(time.time() * 1000)
        js = (
            "(async()=>{"
            "var m=/bigmodel_token_production=([^;]+)/.exec(document.cookie);"
            "var tok=m?m[1]:'';"
            "var h={'Accept':'application/json','Cache-Control':'no-cache','Pragma':'no-cache'};"
            "if(tok)h['Authorization']=tok;"
            "var org=localStorage.getItem('Bigmodel-Organization');"
            "var proj=localStorage.getItem('Bigmodel-Project');"
            "if(org)h['Bigmodel-Organization']=org;"
            "if(proj)h['Bigmodel-Project']=proj;"
            "const r=await fetch("
            f"'/api/monitor/usage/sub-account-rank?startTime={st}&endTime={et}"
            f"&pageNum=1&pageSize=20&keyword=&_={cache_bust}'"
            ",{credentials:'include',headers:h,cache:'no-store'});"
            "return await r.text();})()"
        )

        try:
            result = harness.evaluate(
                page["webSocketDebuggerUrl"], js, await_promise=True)
        except CDPError as e:
            return self._err(data, self._translate_cdp_error(e))

        text = result.get("value") or ""
        if not text.strip():
            return self._err(data, "CDP fetch 返回空（登录可能已失效）")
        try:
            j = json.loads(text)
        except ValueError as e:
            return self._err(data, f"接口返回非 JSON: {e}")
        if not j.get("success") or not j.get("data"):
            return self._err(data, j.get("msg") or "接口返回空（登录可能已过期）")

        self._parse_team(j["data"], data)
        return data

    def _translate_cdp_error(self, e: CDPError) -> str:
        from .cdp import CDPNotConnected, CDPPageNotFound, CDPEvalError
        if isinstance(e, CDPPageNotFound):
            return "请在 CDP Chrome 里打开 bigmodel.cn 并登录"
        if isinstance(e, CDPEvalError):
            return f"页面 fetch 失败（登录可能已过期，请在 CDP Chrome 重新登录）: {e}"
        if isinstance(e, CDPNotConnected):
            return "请先在设置里启动 CDP Chrome 并登录智谱（连接失败）"
        return str(e)

    # ---- HTTP 回退路径 ----
    def _get(self, url, headers, data, params, parse) -> UsageData:
        try:
            headers = dict(headers or {})
            headers.setdefault("Cache-Control", "no-cache")
            headers.setdefault("Pragma", "no-cache")
            r = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            return self._err(data, f"网络错误: {e}")
        if not r.text.strip():
            return self._err(data, "接口返回空（Cookie 可能失效或被反爬），请重新登录抓取")
        try:
            j = r.json()
        except ValueError:
            return self._err(data, f"非 JSON 响应（HTTP {r.status_code}）")
        if not j.get("success") or not j.get("data"):
            return self._err(data, j.get("msg") or "响应异常")
        parse(j["data"], data)
        return data

    # ---- 解析 ----
    def _parse_team(self, d, data: UsageData) -> None:
        """团队版 sub-account-rank：按 customer_id 匹配当前成员，否则取排名第一。"""
        rank_list = d.get("rankList") or []
        if not rank_list:
            data.status = "empty"
            return
        cid = str(self.cfg.get("customer_id") or "").strip()
        me = next((r for r in rank_list
                   if str(r.get("customerId")) == cid), None) if cid else None
        if me is None:
            me = rank_list[0]
        rs = me.get("rateLimitStatus") or {}
        nm = me.get("memberName") or ""
        data.plan_level = ("团队·" + nm) if nm else "团队"
        for label, k in [("5h 窗口", "fiveHourPercentage"),
                         ("每周窗口", "weekPercentage"),
                         ("MCP 月度", "mcpPercentage")]:
            v = rs.get(k)
            if v is not None:
                data.items.append(UsageItem(label, float(v)))
        if not data.items:
            data.status = "empty"

    @staticmethod
    def _parse_limits(d, data: UsageData) -> None:
        """个人版 limits[] 解析。"""
        data.plan_level = (d.get("level") or "GLM").upper()
        for lim in d.get("limits", []) or []:
            t = lim.get("type")
            pct = float(lim.get("percentage") or 0)
            reset = lim.get("nextResetTime")
            reset_at = reset / 1000 if isinstance(reset, (int, float)) else None
            cur, total = lim.get("currentValue"), lim.get("usage")
            if t == "TOKENS_LIMIT":
                label = ZHIPU_UNIT_LABEL.get(lim.get("unit"), "Token 配额")
                note = (f"{fmt_tokens(cur)} / {fmt_tokens(total)} tokens"
                        if isinstance(cur, (int, float))
                        and isinstance(total, (int, float))
                        and total else "")
                data.items.append(UsageItem(label, pct, reset_at, note))
            elif t == "TIME_LIMIT":
                note = (f"{int(cur)} / {int(total)} 次"
                        if isinstance(cur, (int, float))
                        and isinstance(total, (int, float))
                        and total else "")
                data.items.append(UsageItem("MCP 工具调用", pct, reset_at, note))
        if not data.items:
            data.status = "empty"
