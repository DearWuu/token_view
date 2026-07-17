"""OpenCode Go Provider。

取数方式（按优先级）：
  1. 凭证直连（推荐）：cookie 已提取时纯 HTTP GET workspace 页面，
     用量数据由 SSR 直接渲染进 HTML（含 usagePercent 字段），无需执行 JS
  2. CDP 模式：连接已登录调试 Chrome，reload 页面后读 DOM（直连失败兜底）

页面响应格式（嵌套在 __server 响应里）：
  {
    rollingUsage: {status, resetInSec, usagePercent},
    weeklyUsage:  {status, resetInSec, usagePercent},
    monthlyUsage: {status, resetInSec, usagePercent}
  }
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from logger import log

from .base import BaseProvider, UsageData, UsageItem, BROWSER_UA
from .cdp import CDPHarness, CDPError, extract_domain_cookies, cookie_header


class OpenCodeProvider(BaseProvider):
    """OpenCode Go 用量。"""

    URL_FMT = "https://opencode.ai/workspace/{wsid}/go"
    SITE_NAME = "opencode.ai"

    def fetch(self) -> UsageData:
        wsid = (self.cfg.get("workspace_id") or "").strip()
        name = self.cfg.get("name") or "OpenCode"
        data = UsageData(
            provider_name=name, plan_level="Go", fetched_at=time.time())

        # 优先凭证直连：cookie + workspace_id 齐备时纯 HTTP，不开浏览器
        if self.has_direct_credentials(self.cfg):
            result = self._fetch_http(data)
            if result.status != "error":
                return result
            log(f"OpenCode 凭证直连失败（{result.error}），尝试 CDP 兜底")
            if not self.cfg.get("cdp_enabled", True):
                return result
            return self._fallback_cdp(data, result.error, self._fetch_cdp)

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        if not wsid:
            return self._err(data, "未配置 workspace_id")
        return self._err(data, "请在设置里点「提取凭证」，或启用 CDP 模式")

    # ---- 凭证直连模式 ----
    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        return bool((cfg.get("cookie") or "").strip()
                    and (cfg.get("workspace_id") or "").strip())

    @classmethod
    def extract_credentials(cls, port: int = 9222, cdp_url: str = "") -> dict:
        page, cookies = extract_domain_cookies(
            port, cdp_url, "opencode.ai", "opencode")
        if not cookies:
            raise CDPError("未找到 opencode cookie：请先在调试 Chrome 里登录 opencode.ai")
        out = {"cookie": cookie_header(cookies)}
        m = re.search(r"(wrk_[A-Z0-9]+)", page.get("url") or "")
        if m:
            out["workspace_id"] = m.group(1)
        return out

    def _fetch_http(self, data: UsageData) -> UsageData:
        wsid = self.cfg["workspace_id"].strip()
        headers = {
            "Cookie": self.cfg["cookie"].strip(),
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/json",
        }
        try:
            r = requests.get(self.URL_FMT.format(wsid=wsid),
                             headers=headers, timeout=20)
        except requests.RequestException as e:
            return self._err(data, f"网络错误: {e}")
        if r.status_code >= 400:
            return self._err(data, f"HTTP {r.status_code}（凭证可能已过期，请重新提取）")
        if self._parse_usage_text(r.text, data):
            return data
        return self._err(data, "页面响应中未找到用量数据（登录可能已过期）")

    # ---- CDP 模式 ----

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        port = int(self.cfg.get("cdp_port") or 9222)
        harness = CDPHarness(
            port=port,
            page_keyword="opencode.ai",
            cdp_url=self.cfg.get("cdp_url") or "",
            eval_timeout=30,
        )

        try:
            page = harness.find_page()
        except CDPError as e:
            return self._translate_err(data, e)

        wsid = (self.cfg.get("workspace_id") or "").strip()
        if not wsid:
            m = re.search(r"(wrk_[A-Z0-9]+)", page.get("url") or "")
            if m:
                wsid = m.group(1)
            else:
                return self._err(data, "未配置 workspace_id，且无法从页面 URL 自动获取")

        ws_url = page.get("webSocketDebuggerUrl", "")

        # OpenCode 是 SolidJS SPA，用量数据由 JS 水合后渲染到 DOM。
        # 直接 fetch 页面 URL 返回 HTML 不含 JSON 用量字段，
        # 读 DOM 只能拿到上次页面加载时的旧数据。
        # 解决：用 CDP Page.reload 重新加载页面（忽略缓存），
        # 等加载完 + SPA 水合后读 DOM，拿到最新数据。
        try:
            harness.page_reload(ws_url, ignore_cache=True, wait_load=True, settle=2)
        except CDPError as e:
            return self._translate_err(data, e)

        # 读 DOM 中的用量百分比
        js = (
            "(async()=>{"
            "const texts=document.body.innerText||'';"
            "const results={};"
            "const rollingMatch=texts.match(/5[Hh].*?(\\d+(?:\\.\\d+)?)\\s*%/);"
            "if(rollingMatch)results.rolling=parseFloat(rollingMatch[1]);"
            "const weeklyMatch=texts.match(/[Ww]eekly.*?(\\d+(?:\\.\\d+)?)\\s*%|周.*?(\\d+(?:\\.\\d+)?)\\s*%/);"
            "if(weeklyMatch)results.weekly=parseFloat(weeklyMatch[1]||weeklyMatch[2]);"
            "const monthlyMatch=texts.match(/[Mm]onthly.*?(\\d+(?:\\.\\d+)?)\\s*%|月.*?(\\d+(?:\\.\\d+)?)\\s*%/);"
            "if(monthlyMatch)results.monthly=parseFloat(monthlyMatch[1]||monthlyMatch[2]);"
            "results.allPcts=[...texts.matchAll(/(\\d+(?:\\.\\d)?)\\s*%/g)].map(m=>parseFloat(m[1]));"
            "return JSON.stringify(results);"
            "})()"
        )

        try:
            result = harness.evaluate(ws_url, js, await_promise=True)
        except CDPError as e:
            return self._translate_err(data, e)

        text = result.get("value") or ""
        if not text.strip():
            return self._err(data, "DOM 读取为空")
        return self._parse_response(text, data)

    def _translate_err(self, data: UsageData, e: CDPError) -> UsageData:
        from .cdp import CDPPageNotFound, CDPEvalError, CDPNotConnected
        if isinstance(e, CDPPageNotFound):
            return self._err(data, "请在 CDP Chrome 里打开 opencode.ai 并登录")
        if isinstance(e, CDPEvalError):
            return self._err(data, f"CDP 执行失败: {e}")
        if isinstance(e, CDPNotConnected):
            return self._err(data, "请先在设置里启动 CDP Chrome 并登录 opencode.ai（连接失败）")
        return self._err(data, str(e))

    def _parse_response(self, text: str, data: UsageData) -> UsageData:
        try:
            j = json.loads(text)
        except ValueError as e:
            return self._err(data, f"JSON 解析失败: {e}")

        if j.get("error") == "timeout":
            return self._err(data, "获取超时，请确保 opencode.ai 页面已加载完成")

        if j.get("source") == "network":
            body = j.get("body") or ""
            if self._parse_usage_text(body, data):
                return data
            if isinstance(j.get("dom"), dict):
                j = j["dom"]
            else:
                data.status = "empty"
                data.error = "已主动请求 OpenCode，但响应里未解析到用量字段"
                return data

        if j.get("source") == "dom" and isinstance(j.get("dom"), dict):
            j = j["dom"]

        if self._append_usage_from_obj(j, data):
            return data

        # 处理 DOM 解析结果
        if "allPcts" in j:
            pcts = j.get("allPcts", [])
            rolling = j.get("rolling")
            weekly = j.get("weekly")
            monthly = j.get("monthly")

            if rolling is not None:
                data.items.append(UsageItem("5h Rolling", rolling))
            if weekly is not None:
                data.items.append(UsageItem("每周", weekly))
            if monthly is not None:
                data.items.append(UsageItem("每月", monthly))

            # 没有任何特定匹配就用通用百分比前 3 个
            if not data.items and pcts:
                for i, pct in enumerate(pcts[:3]):
                    labels = ["5 hours usage", " weekly usage", " monthly usage"]
                    data.items.append(UsageItem(labels[i], pct))
        else:
            # 处理 _server API 响应
            for key, label in [
                ("rollingUsage", "5h Rolling"),
                ("weeklyUsage", "每周"),
                ("monthlyUsage", "每月"),
            ]:
                usage = j.get(key) or {}
                pct = usage.get("usagePercent")
                reset_sec = usage.get("resetInSec")
                if pct is not None:
                    data.items.append(UsageItem(
                        label, float(pct), note=self._reset_note(reset_sec)))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据，请确保页面显示了用量信息"
        return data

    @staticmethod
    def _reset_note(reset_sec) -> str:
        if not reset_sec:
            return ""
        h = reset_sec // 3600
        m = (reset_sec % 3600) // 60
        if h > 24:
            return f"{h // 24}天{h % 24}小时后重置"
        if h > 0:
            return f"{h}小时{m}分后重置"
        return f"{m}分钟后重置"

    def _parse_usage_text(self, text: str, data: UsageData) -> bool:
        """解析主动 fetch 返回的 JSON/HTML/序列化文本里的 OpenCode 用量字段。"""
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if self._append_usage_from_obj(obj, data):
            return True

        found = False
        for key, label in [
            ("rollingUsage", "5h Rolling"),
            ("weeklyUsage", "每周"),
            ("monthlyUsage", "每月"),
        ]:
            # 三种形态：JSON（"key":{...}）、转义 JSON（\"key\":{...}）、
            # SolidJS SSR 序列化（key:$R[33]={...usagePercent:0}，key 无引号）
            patterns = [
                rf'"{key}"\s*:\s*\{{[^{{}}]*?"usagePercent"\s*:\s*([0-9.]+)',
                rf'\\"{key}\\"\s*:\s*\{{[^{{}}]*?\\"usagePercent\\"\s*:\s*([0-9.]+)',
                rf'\b{key}\s*:[^}}]*?usagePercent\s*:\s*([0-9.]+)',
            ]
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    note = ""
                    mr = re.search(
                        rf'\b{key}\s*:[^}}]*?resetInSec\s*:\s*(\d+)', text)
                    if mr:
                        note = self._reset_note(int(mr.group(1)))
                    data.items.append(UsageItem(label, float(m.group(1)), note=note))
                    found = True
                    break
        return found

    def _append_usage_from_obj(self, obj: Any, data: UsageData) -> bool:
        """递归查找 rollingUsage/weeklyUsage/monthlyUsage 结构。"""
        if obj is None:
            return False
        labels = {
            "rollingUsage": "5h Rolling",
            "weeklyUsage": "每周",
            "monthlyUsage": "每月",
        }
        found = False
        if isinstance(obj, dict):
            for key, label in labels.items():
                usage = obj.get(key)
                if isinstance(usage, dict) and usage.get("usagePercent") is not None:
                    data.items.append(UsageItem(label, float(usage.get("usagePercent"))))
                    found = True
            if found:
                return True
            for value in obj.values():
                if self._append_usage_from_obj(value, data):
                    return True
        elif isinstance(obj, list):
            for value in obj:
                if self._append_usage_from_obj(value, data):
                    return True
        elif isinstance(obj, str) and "usagePercent" in obj:
            return self._parse_usage_text(obj, data)
        return found
