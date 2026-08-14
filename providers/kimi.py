"""Kimi（月之暗面）会员/订阅额度 Provider。

取数方式（按优先级）：
  1. 凭证直连（推荐）：localStorage 里的 access_token / refresh_token 提取后，
     access_token 过期（约 15 分钟）就用 refresh_token 调 auth.kimi.com 换新，
     纯 HTTP 不开浏览器
  2. CDP 模式：连接已登录调试 Chrome，页面内读 localStorage access_token 后 fetch（兜底）

2026-08 站点改版：个人版登录态已从 kimi-auth cookie（HttpOnly，约 28 天）
迁移到 localStorage 的 access_token/refresh_token（与团队版同一套 account 网关
机制），旧 cookie 模式仅作遗留兜底。

接口：
  POST https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken
      {"refreshToken": "..."} -> {"accessToken": "...", "refreshToken": "..."}
  POST https://www.kimi.com/apiv2/kimi.gateway.membership.v2.MembershipService/GetSubscriptionStats

返回包含：
  - ratelimitCode5h / ratelimitCode7d：5h / 7d 速率窗口已用比例
  - subscriptionBalance：订阅额度已用比例（amountUsedRatio）和到期时间
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone

import requests

import config
from logger import log

from .base import BaseProvider, UsageData, UsageItem, BROWSER_UA
from .cdp import CDPHarness, CDPError


class KimiProvider(BaseProvider):
    """Kimi 订阅额度与速率窗口用量。"""

    API_PATH = "/apiv2/kimi.gateway.membership.v2.MembershipService/GetSubscriptionStats"
    # 站点已把 /code/console 重定向到 /code，keyword 放宽到 kimi.com/code
    PAGE_KEYWORD = "kimi.com/code"
    SITE_NAME = "www.kimi.com/code"
    STATS_REFERER = "https://www.kimi.com/code"
    PLAN_LEVEL = "订阅额度"
    REFRESH_URL = ("https://auth.kimi.com/api/"
                   "account.gateway.v1.AuthService/RefreshToken")

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "Kimi"
        data = UsageData(
            provider_name=name, plan_level=self.PLAN_LEVEL, fetched_at=time.time())

        # 优先凭证直连：token 已提取时纯 HTTP，不开浏览器
        if self.has_direct_credentials(self.cfg):
            result = self._fetch_http(data)
            if result.status != "error":
                return result
            log(f"Kimi 凭证直连失败（{result.error}），尝试 CDP 兜底")
            if not self.cfg.get("cdp_enabled", True):
                return result
            return self._fallback_cdp(data, result.error, self._fetch_cdp)

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        return self._err(data, "请在设置里点「提取凭证」，或启用 CDP 并登录 www.kimi.com")

    # ---- 凭证直连模式（token） ----
    @staticmethod
    def has_direct_credentials(cfg: dict) -> bool:
        if cfg.get("refresh_token") or cfg.get("access_token"):
            return True
        # 旧 cookie 模式（站点已弃用，遗留兜底）
        return "kimi-auth=" in (cfg.get("cookie") or "")

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
                "未找到 refresh_token：请先在调试 Chrome 里登录 Kimi")
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
        log("Kimi access_token 已刷新")
        return ""

    def _persist_tokens(self) -> None:
        """刷新后的 token 落盘（access_token 只有约 15 分钟，重启后要能接着刷）。"""
        try:
            cfg = config.load()
            for p in cfg.get("providers", []):
                if p.get("id") == self.cfg.get("id"):
                    p["access_token"] = self.cfg.get("access_token", "")
                    p["refresh_token"] = self.cfg.get("refresh_token", "")
                    break
            config.save(cfg)
        except OSError as e:
            log(f"Kimi token 落盘失败: {e}")

    def _fetch_http(self, data: UsageData) -> UsageData:
        if self.cfg.get("access_token") or self.cfg.get("refresh_token"):
            if not self._access_token_valid():
                err = self._refresh_access_token()
                if err:
                    return self._err(data, err)
            return self._fetch_stats_http(data, retried=False)
        # 旧 cookie 模式（站点已弃用）
        return self._fetch_http_cookie(data)

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

    def _fetch_http_cookie(self, data: UsageData) -> UsageData:
        """旧 kimi-auth cookie 直连（2026-08 站点改版后仅作遗留兜底）。"""
        cookie = self.cfg["cookie"].strip()
        m = re.search(r"(?:^|;\s*)kimi-auth=([^;]+)", cookie)
        if not m:
            return self._err(data, "cookie 中缺少 kimi-auth（请重新「提取凭证」）")
        token = m.group(1)
        payload = self._decode_jwt_payload(token)
        headers = self._build_headers(token, payload)
        headers["User-Agent"] = BROWSER_UA
        headers["Origin"] = "https://www.kimi.com"
        headers["Referer"] = self.STATS_REFERER
        headers["Cookie"] = cookie
        try:
            r = requests.post("https://www.kimi.com" + self.API_PATH,
                              headers=headers, json={}, timeout=20)
        except requests.RequestException as e:
            return self._err(data, f"网络错误: {e}")
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
                data, "localStorage 无 access_token（请在 CDP Chrome 里登录 Kimi）")

        payload = self._decode_jwt_payload(token)
        js = self._build_js(token, payload)

        try:
            result = harness.evaluate(ws_url, js, await_promise=True)
        except CDPError as e:
            return self._translate_err(data, e)

        text = result.get("value") or ""
        log(f"Kimi CDP 原始响应: {text[:800]}")
        if not text.strip():
            return self._err(data, "API 返回空")
        return self._parse_json(text, data)

    @staticmethod
    def _build_headers(token: str, payload: dict) -> dict:
        """构造会员网关请求头（HTTP 直连与 CDP JS 共用）。"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Authorization": f"Bearer {token}",
            "connect-protocol-version": "1",
            "r-timezone": "Asia/Shanghai",
            "x-language": "zh-CN",
            "x-msh-platform": "web",
            "x-msh-version": "1.0.0",
        }
        sub = payload.get("sub") or ""
        device_id = payload.get("device_id") or ""
        sssid = payload.get("ssid") or ""
        if sub:
            headers["x-traffic-id"] = sub
        if device_id:
            headers["x-msh-device-id"] = device_id
        if sssid:
            headers["x-msh-session-id"] = sssid
        return headers

    def _build_js(self, token: str, payload: dict) -> str:
        """构造在页面上下文执行的 fetch JS，返回 {status, statusText, body}。"""
        return (
            "(async()=>{"
            "const h=" + json.dumps(self._build_headers(token, payload)) + ";"
            "const u=new URL(" + json.dumps(self.API_PATH) + ",location.origin);"
            "u.searchParams.set('_tv',Date.now());"
            "const r=await fetch(u.href,{method:'POST',credentials:'include',headers:h,body:'{}',cache:'no-store'});"
            "const t=await r.text();"
            "return JSON.stringify({status:r.status,statusText:r.statusText,body:t});"
            "})()"
        )

    @staticmethod
    def _decode_jwt_payload(token: str) -> dict:
        """base64url 解码 JWT payload，失败返回空 dict。"""
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            raw = parts[1].replace("-", "+").replace("_", "/")
            pad = len(raw) % 4
            if pad:
                raw += "=" * (4 - pad)
            return json.loads(base64.b64decode(raw).decode("utf-8"))
        except (ValueError, TypeError):
            return {}

    def _translate_err(self, data: UsageData, e: CDPError) -> UsageData:
        from .cdp import CDPPageNotFound, CDPEvalError, CDPNotConnected
        if isinstance(e, CDPPageNotFound):
            return self._err(data, "请在 CDP Chrome 里打开 www.kimi.com 并登录")
        if isinstance(e, CDPEvalError):
            return self._err(data, f"API 调用失败（登录可能已过期）: {e}")
        if isinstance(e, CDPNotConnected):
            return self._err(data, "请先在设置里启动 CDP Chrome 并登录 Kimi")
        return self._err(data, str(e))

    def _parse_json(self, text: str, data: UsageData) -> UsageData:
        try:
            wrapper = json.loads(text)
        except ValueError as e:
            return self._err(data, f"JSON 解析失败: {e}")

        status = wrapper.get("status") or 0
        body = wrapper.get("body") or ""
        if status >= 400:
            return self._err(data, f"HTTP {status} {wrapper.get('statusText') or ''}: {body[:200]}")

        try:
            j = json.loads(body)
        except ValueError as e:
            return self._err(data, f"接口 body 非 JSON: {e}")

        # 业务错误码
        if j.get("code") not in (None, 0, "0"):
            return self._err(data, j.get("message") or j.get("msg") or f"业务错误: {j.get('code')}")

        # 5h 速率窗口（ratio 缺失时按 0% 处理，窗口刚重置时 API 不返回 ratio）
        five_h = j.get("ratelimitCode5h") or {}
        if five_h.get("enabled"):
            ratio = five_h.get("ratio") or 0
            reset_at = self._parse_iso_ts(five_h.get("resetTime"))
            data.items.append(UsageItem(
                "5h 窗口", float(ratio) * 100, reset_at, ""))

        # 7d 速率窗口
        seven_d = j.get("ratelimitCode7d") or {}
        if seven_d.get("enabled"):
            ratio = seven_d.get("ratio") or 0
            reset_at = self._parse_iso_ts(seven_d.get("resetTime"))
            data.items.append(UsageItem(
                "7d 窗口", float(ratio) * 100, reset_at, ""))

        # 订阅额度
        balance = j.get("subscriptionBalance") or {}
        if balance:
            ratio = balance.get("amountUsedRatio")
            if ratio is None:
                ratio = balance.get("kimiCodeUsedRatio")
            if ratio is not None:
                reset_at = self._parse_iso_ts(balance.get("expireTime"))
                feature = balance.get("feature") or ""
                unit = balance.get("unit") or ""
                note = " / ".join(p for p in [feature, unit] if p)
                data.items.append(UsageItem(
                    "订阅额度", float(ratio) * 100, reset_at, note))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据"
        return data

    @staticmethod
    def _parse_iso_ts(value) -> float | None:
        """把 ISO 8601 字符串（含纳秒）转成 Unix 时间戳。"""
        if not value:
            return None
        s = str(value)
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            if "." in s:
                base, rest = s.split(".", 1)
                digits = ""
                for ch in rest:
                    if ch.isdigit():
                        digits += ch
                    else:
                        break
                digits = digits[:6]
                suffix = rest[len(digits):]
                s = f"{base}.{digits}{suffix}"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            return None
