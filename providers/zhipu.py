"""智谱 GLM Coding Plan Provider。

取数方式（按优先级）：
  1. 凭证直连（推荐）：auth_token + org_id + project_id 已提取时，
     纯 HTTP 请求 sub-account-rank，不开浏览器（实测无反爬，
     缺 Bigmodel-* header 才会返回空 data）
  2. CDP 模式：连接已登录调试 Chrome，在页面上下文 fetch（凭证直连失败时兜底）
  3. API Key（个人版，/api/monitor/usage/quota/limit）

智谱 Authorization 不加 Bearer 前缀。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import requests

from logger import log

from .base import BaseProvider, UsageData, UsageItem, BROWSER_UA, fmt_tokens
from .cdp import CDPHarness, CDPError

# 智谱个人版 limits 里 unit 字段 → 窗口语义
ZHIPU_UNIT_LABEL = {3: "5h 窗口", 6: "每周窗口"}


class ZhipuProvider(BaseProvider):
    """智谱 GLM Coding Plan。"""

    TEAM_RANK_URL = "https://bigmodel.cn/api/monitor/usage/sub-account-rank"
    # 团队版页面同款接口：返回各窗口 nextResetTime（毫秒时间戳）
    QUOTA_LIMIT_URL = "https://bigmodel.cn/api/monitor/usage/quota/limit"
    CDP_EVAL_TIMEOUT = 30
    SITE_NAME = "bigmodel.cn"

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "智谱 GLM"
        data = UsageData(provider_name=name, fetched_at=time.time())

        # 优先凭证直连：已提取 JWT + org/project 时纯 HTTP，不开浏览器
        if self.has_direct_credentials(self.cfg):
            result = self._fetch_team_http(data)
            if result.status != "error":
                return result
            log(f"智谱凭证直连失败（{result.error}），尝试 CDP 兜底")
            if not self.cfg.get("cdp_enabled", True):
                return result
            return self._fallback_cdp(data, result.error, self._fetch_team_cdp)

        # CDP 兜底：连接用户已登录的调试 Chrome，在页面上下文 fetch
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

        return self._err(data, "未配置：请在设置里点「提取凭证」，或填 API Key")

    # ---- 凭证直连模式 ----
    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        return all((cfg.get(k) or "").strip()
                   for k in ("auth_token", "org_id", "project_id"))

    @classmethod
    def extract_credentials(cls, port: int = 9222, cdp_url: str = "") -> dict:
        harness = CDPHarness(
            port=port, page_keyword="bigmodel.cn", cdp_url=cdp_url,
            eval_timeout=cls.CDP_EVAL_TIMEOUT)
        page = harness.find_page()
        js = (
            "(()=>{"
            "var m=/bigmodel_token_production=([^;]+)/.exec(document.cookie);"
            "return JSON.stringify({"
            "token:m?m[1]:'',"
            "org:localStorage.getItem('Bigmodel-Organization')||'',"
            "proj:localStorage.getItem('Bigmodel-Project')||''});"
            "})()"
        )
        result = harness.evaluate(page["webSocketDebuggerUrl"], js)
        cred = json.loads(result.get("value") or "{}")
        if not cred.get("token"):
            raise CDPError("未找到登录 token：请先在调试 Chrome 里登录 bigmodel.cn")
        if not cred.get("org") or not cred.get("proj"):
            raise CDPError(
                "未找到组织/项目信息：请打开 bigmodel.cn 团队用量页后再提取")
        return {
            "auth_token": cred["token"],
            "org_id": cred["org"],
            "project_id": cred["proj"],
        }

    def _fetch_team_http(self, data: UsageData) -> UsageData:
        """纯 HTTP 直连 sub-account-rank（凭证已提取，无需浏览器）。"""
        headers = {
            "Accept": "application/json",
            "Authorization": self.cfg["auth_token"].strip(),
            "Bigmodel-Organization": self.cfg["org_id"].strip(),
            "Bigmodel-Project": self.cfg["project_id"].strip(),
            "User-Agent": BROWSER_UA,
            "Referer": "https://bigmodel.cn/coding-plan/team/usage-stats",
        }
        now = datetime.now()
        params = {
            "startTime": (now - timedelta(days=6)).strftime("%Y-%m-%d") + " 00:00:00",
            "endTime": now.strftime("%Y-%m-%d") + " 23:59:59",
            "pageNum": 1, "pageSize": 20, "keyword": "",
        }
        resets = self._fetch_quota_resets_http(headers)
        parse = (lambda d, data: self._parse_team(d, data, resets)) if resets else self._parse_team
        return self._get(self.TEAM_RANK_URL, headers, data, params, parse)

    def _fetch_quota_resets_http(self, headers: dict) -> dict:
        """调 quota/limit?type=2 拿各窗口 nextResetTime → {label: reset_at}。

        失败返回空 dict（不阻塞主流程，只是没有重置倒计时）。
        """
        try:
            r = requests.get(self.QUOTA_LIMIT_URL, headers=headers,
                             params={"type": 2}, timeout=15)
            return self._parse_quota_resets(r.json())
        except Exception as e:  # noqa: BLE001
            log(f"智谱 quota/limit 获取重置时间失败: {e}")
            return {}

    @staticmethod
    def _parse_quota_resets(j: dict) -> dict:
        """quota/limit 响应 → {团队版 label: reset_at(秒)}。"""
        out = {}
        for lim in (j.get("data") or {}).get("limits", []) or []:
            reset = lim.get("nextResetTime")
            if not isinstance(reset, (int, float)):
                continue
            t = lim.get("type")
            if t == "TOKENS_LIMIT":
                label = ZHIPU_UNIT_LABEL.get(lim.get("unit"))
            elif t == "TIME_LIMIT":
                label = "MCP 月度"
            else:
                label = None
            if label:
                out[label] = reset / 1000
        return out

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
            "var quota='';"
            "try{"
            f"const r2=await fetch('/api/monitor/usage/quota/limit?type=2&_={cache_bust}',"
            "{credentials:'include',headers:h,cache:'no-store'});"
            "quota=await r2.text();}catch(e){}"
            "return JSON.stringify({rank:await r.text(),quota:quota});})()"
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
            payload = json.loads(text)
            j = json.loads(payload.get("rank") or "{}")
        except ValueError as e:
            return self._err(data, f"接口返回非 JSON: {e}")
        if not j.get("success") or not j.get("data"):
            return self._err(data, j.get("msg") or "接口返回空（登录可能已过期）")

        resets = {}
        try:
            resets = self._parse_quota_resets(json.loads(payload.get("quota") or "{}"))
        except ValueError:
            pass
        self._parse_team(j["data"], data, resets)
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
        if r.status_code >= 400:
            return self._err(data, f"HTTP {r.status_code}（凭证可能已过期，请重新提取）")
        if not r.text.strip():
            return self._err(data, "接口返回空（凭证可能已失效），请重新提取")
        try:
            j = r.json()
        except ValueError:
            return self._err(data, f"非 JSON 响应（HTTP {r.status_code}）")
        if not j.get("success") or not j.get("data"):
            return self._err(data, j.get("msg") or "响应异常（凭证可能已失效）")
        parse(j["data"], data)
        return data

    # ---- 解析 ----
    def _parse_team(self, d, data: UsageData, reset_map: dict | None = None) -> None:
        """团队版 sub-account-rank：按 customer_id 匹配当前成员，否则取排名第一。

        reset_map：quota/limit?type=2 提供的 {label: reset_at}，
        团队版接口本身只有百分比、不返回重置时间（实测确认）。
        """
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
                reset_at = (reset_map or {}).get(label)
                data.items.append(UsageItem(label, float(v), reset_at))
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
