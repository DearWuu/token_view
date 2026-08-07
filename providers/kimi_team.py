"""Kimi 团队版（团队空间）订阅额度 Provider。

与个人版（kimi.py）的区别：团队空间的用量接口要求 account 网关签发的
短期 access token（iss=account，约 15 分钟有效，payload 带 business_id），
而不是 kimi-auth cookie 那个 user-center JWT。

取数方式（按优先级）：
  1. 凭证直连（推荐）：localStorage 里的 access_token / refresh_token 提取后，
     access_token 过期就用 refresh_token 调 auth.kimi.com 换新，纯 HTTP 不开浏览器
  2. CDP 兜底：连接已登录调试 Chrome，页面内读 localStorage access_token 后 fetch

接口：
  POST https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken
      {"refreshToken": "..."} -> {"accessToken": "...", "refreshToken": "..."}
  POST https://www.kimi.com/apiv2/kimi.gateway.membership.v2.MembershipService/GetSubscriptionStats
      （与个人版同一接口，Bearer 换成团队 access token 即返回团队空间数据）
"""
from __future__ import annotations

import json
import time

import requests

import config
from logger import log

from .base import UsageData, BROWSER_UA
from .cdp import CDPHarness, CDPError
from .kimi import KimiProvider


class KimiTeamProvider(KimiProvider):
    """Kimi 团队空间订阅额度与速率窗口用量。"""

    PAGE_KEYWORD = "kimi.com"
    SITE_NAME = "www.kimi.com（团队空间）"
    REFRESH_URL = ("https://auth.kimi.com/api/"
                   "account.gateway.v1.AuthService/RefreshToken")
    STATS_REFERER = "https://www.kimi.com/membership/subscription?tab=quota"

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "Kimi 团队"
        data = UsageData(
            provider_name=name, plan_level="团队版", fetched_at=time.time())

        if self.has_direct_credentials(self.cfg):
            result = self._fetch_http(data)
            if result.status != "error":
                return result
            log(f"Kimi 团队凭证直连失败（{result.error}），尝试 CDP 兜底")
            if not self.cfg.get("cdp_enabled", True):
                return result
            return self._fallback_cdp(data, result.error, self._fetch_cdp)

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        return self._err(data, "请在设置里点「提取凭证」，或启用 CDP 并登录 www.kimi.com")

    # ---- 凭证直连模式 ----
    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        return bool(cfg.get("refresh_token") or cfg.get("access_token"))

    @classmethod
    def extract_credentials(cls, port: int = 9222, cdp_url: str = "") -> dict:
        """从调试 Chrome 的 localStorage 提取 access_token / refresh_token。"""
        harness = CDPHarness(port=port, page_keyword=cls.PAGE_KEYWORD,
                             cdp_url=cdp_url)
        page = harness.find_page()
        ws_url = page.get("webSocketDebuggerUrl", "")
        result = harness.evaluate(
            ws_url,
            "JSON.stringify({"
            "at: localStorage.getItem('access_token') || '',"
            "rt: localStorage.getItem('refresh_token') || ''"
            "})")
        try:
            tokens = json.loads(result.get("value") or "{}")
        except ValueError as e:
            raise CDPError(f"读取 localStorage 失败: {e}")
        if not tokens.get("rt"):
            raise CDPError(
                "未找到 refresh_token：请先在调试 Chrome 里登录 Kimi 团队空间")
        return {
            "access_token": tokens.get("at") or "",
            "refresh_token": tokens["rt"],
        }

    def _access_token_valid(self) -> bool:
        exp = self._decode_jwt_payload(
            self.cfg.get("access_token") or "").get("exp") or 0
        return exp - time.time() > 60

    def _refresh_access_token(self) -> str:
        """用 refresh_token 换新 access_token，成功返回 ""，失败返回错误信息。"""
        rt = (self.cfg.get("refresh_token") or "").strip()
        if not rt:
            return "缺少 refresh_token（请在设置里重新「提取凭证」）"
        try:
            r = requests.post(
                self.REFRESH_URL,
                headers={"Content-Type": "application/json",
                         "connect-protocol-version": "1",
                         "User-Agent": BROWSER_UA},
                json={"refreshToken": rt}, timeout=20)
        except requests.RequestException as e:
            return f"刷新 token 网络错误: {e}"
        if r.status_code != 200:
            return f"刷新 token 失败 HTTP {r.status_code}: {r.text[:200]}"
        try:
            j = json.loads(r.text)
        except ValueError:
            return f"刷新 token 返回非 JSON: {r.text[:200]}"
        at = j.get("accessToken") or ""
        if not at:
            return f"刷新 token 返回无 accessToken: {r.text[:200]}"
        self.cfg["access_token"] = at
        if j.get("refreshToken"):
            self.cfg["refresh_token"] = j["refreshToken"]
        self._persist_tokens()
        log("Kimi 团队 access_token 已刷新")
        return ""

    def _persist_tokens(self) -> None:
        """刷新后的 token 落盘（access_token 只有 15 分钟，重启后要能接着刷）。"""
        try:
            cfg = config.load()
            for p in cfg.get("providers", []):
                if p.get("id") == self.cfg.get("id"):
                    p["access_token"] = self.cfg.get("access_token", "")
                    p["refresh_token"] = self.cfg.get("refresh_token", "")
                    break
            config.save(cfg)
        except OSError as e:
            log(f"Kimi 团队 token 落盘失败: {e}")

    def _fetch_http(self, data: UsageData) -> UsageData:
        if not self._access_token_valid():
            err = self._refresh_access_token()
            if err:
                return self._err(data, err)
        return self._fetch_stats_http(data, retried=False)

    def _fetch_stats_http(self, data: UsageData, retried: bool) -> UsageData:
        token = self.cfg["access_token"].strip()
        payload = self._decode_jwt_payload(token)
        headers = self._build_headers(token, payload)
        headers["User-Agent"] = BROWSER_UA
        headers["Origin"] = "https://www.kimi.com"
        headers["Referer"] = self.STATS_REFERER
        try:
            r = requests.post("https://www.kimi.com" + self.API_PATH,
                              headers=headers, json={}, timeout=20)
        except requests.RequestException as e:
            return self._err(data, f"网络错误: {e}")
        # 401 时刷新一次 token 重试（access_token 可能提前失效）
        if r.status_code == 401 and not retried:
            err = self._refresh_access_token()
            if err:
                return self._err(data, err)
            return self._fetch_stats_http(data, retried=True)
        wrapper = json.dumps({
            "status": r.status_code, "statusText": r.reason, "body": r.text})
        return self._parse_json(wrapper, data)

    # ---- CDP 模式 ----

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        port = int(self.cfg.get("cdp_port") or 9222)
        harness = CDPHarness(
            port=port,
            page_keyword=self.PAGE_KEYWORD,
            cdp_url=self.cfg.get("cdp_url") or "",
            eval_timeout=30,
        )

        try:
            page = harness.find_page()
        except CDPError as e:
            return self._translate_err(data, e)

        ws_url = page.get("webSocketDebuggerUrl", "")

        # 页面自己会用 refresh_token 保持 access_token 新鲜，直接读即可
        try:
            tok_result = harness.evaluate(
                ws_url, "localStorage.getItem('access_token') || ''")
        except CDPError as e:
            return self._translate_err(data, e)

        token = (tok_result.get("value") or "").strip()
        if not token:
            return self._err(
                data, "localStorage 无 access_token（请在 CDP Chrome 里登录 Kimi 团队空间）")

        payload = self._decode_jwt_payload(token)
        js = self._build_js(token, payload)

        try:
            result = harness.evaluate(ws_url, js, await_promise=True)
        except CDPError as e:
            return self._translate_err(data, e)

        text = result.get("value") or ""
        log(f"Kimi 团队 CDP 原始响应: {text[:800]}")
        if not text.strip():
            return self._err(data, "API 返回空")
        return self._parse_json(text, data)

    def _parse_json(self, text: str, data: UsageData) -> UsageData:
        result = super()._parse_json(text, data)
        # 团队空间的 subscriptionBalance 是按月循环的总额度，
        # 前端约定"月"字样才渲染 30 天倒计时圆环
        for item in result.items:
            if item.label == "订阅额度":
                item.label = "本月窗口"
        return result

    def _translate_err(self, data: UsageData, e: CDPError) -> UsageData:
        from .cdp import CDPPageNotFound, CDPEvalError, CDPNotConnected
        if isinstance(e, CDPPageNotFound):
            return self._err(data, "请在 CDP Chrome 里打开 www.kimi.com 并登录团队空间")
        if isinstance(e, CDPEvalError):
            return self._err(data, f"API 调用失败（登录可能已过期）: {e}")
        if isinstance(e, CDPNotConnected):
            return self._err(data, "请先在设置里启动 CDP Chrome 并登录 Kimi")
        return self._err(data, str(e))
