"""凭证直连模式测试：has_direct_credentials 判定 + fetch 路由 + 解析。"""
from __future__ import annotations

import config
import providers
from providers.base import UsageData


# ---------------------------------------------------------------------------
# has_direct_credentials 判定
# ---------------------------------------------------------------------------

def test_zhipu_cred_requires_three_fields():
    cls = providers.provider_class("zhipu")
    cfg = config.new_provider("zhipu")
    assert not cls.has_direct_credentials(cfg)
    cfg.update({"auth_token": "t", "org_id": "o", "project_id": "p"})
    assert cls.has_direct_credentials(cfg)
    cfg["org_id"] = ""
    assert not cls.has_direct_credentials(cfg)


def test_opencode_cred_requires_cookie_and_wsid():
    cls = providers.provider_class("opencode")
    cfg = config.new_provider("opencode")
    assert not cls.has_direct_credentials(cfg)
    cfg["cookie"] = "auth=x"
    assert not cls.has_direct_credentials(cfg)
    cfg["workspace_id"] = "wrk_ABC"
    assert cls.has_direct_credentials(cfg)


def test_kimi_cred_requires_kimi_auth_in_cookie():
    cls = providers.provider_class("kimi")
    cfg = config.new_provider("kimi")
    assert not cls.has_direct_credentials(cfg)
    cfg["cookie"] = "theme=light"
    assert not cls.has_direct_credentials(cfg)
    cfg["cookie"] = "theme=light; kimi-auth=tok123"
    assert cls.has_direct_credentials(cfg)


def test_mimo_volcengine_cred_only_needs_cookie():
    for ptype in ("mimo", "volcengine"):
        cls = providers.provider_class(ptype)
        cfg = config.new_provider(ptype)
        assert not cls.has_direct_credentials(cfg)
        cfg["cookie"] = "x=1"
        assert cls.has_direct_credentials(cfg)


# ---------------------------------------------------------------------------
# fetch 路由：凭证齐走 HTTP，失败回退 CDP
# ---------------------------------------------------------------------------

def _ok_data(name="p"):
    d = UsageData(provider_name=name, fetched_at=1.0)
    d.items.append(providers.UsageItem("5h 窗口", 12.5))
    return d


def test_zhipu_fetch_prefers_http(monkeypatch):
    from providers.zhipu import ZhipuProvider
    cfg = config.new_provider("zhipu")
    cfg.update({"auth_token": "t", "org_id": "o", "project_id": "p",
                "cdp_enabled": True})
    calls = []
    monkeypatch.setattr(ZhipuProvider, "_fetch_team_http",
                        lambda self, d: (calls.append("http"), _ok_data())[1])
    monkeypatch.setattr(ZhipuProvider, "_fetch_team_cdp",
                        lambda self, d: (calls.append("cdp"), _ok_data())[1])
    data = ZhipuProvider(cfg).fetch()
    assert data.status == "ok"
    assert calls == ["http"]


def test_zhipu_fetch_falls_back_to_cdp_on_http_error(monkeypatch):
    from providers.zhipu import ZhipuProvider
    cfg = config.new_provider("zhipu")
    cfg.update({"auth_token": "t", "org_id": "o", "project_id": "p",
                "cdp_enabled": True})
    calls = []

    def http_fail(self, d):
        calls.append("http")
        return self._err(d, "HTTP 401")

    monkeypatch.setattr(ZhipuProvider, "_fetch_team_http", http_fail)
    monkeypatch.setattr(ZhipuProvider, "_fetch_team_cdp",
                        lambda self, d: (calls.append("cdp"), _ok_data())[1])
    data = ZhipuProvider(cfg).fetch()
    assert calls == ["http", "cdp"]
    assert data.status == "ok"
    assert data.items, "回退成功后错误状态应已重置"


def test_zhipu_fetch_no_fallback_when_cdp_disabled(monkeypatch):
    from providers.zhipu import ZhipuProvider
    cfg = config.new_provider("zhipu")
    cfg.update({"auth_token": "t", "org_id": "o", "project_id": "p",
                "cdp_enabled": False})
    monkeypatch.setattr(ZhipuProvider, "_fetch_team_http",
                        lambda self, d: self._err(d, "HTTP 401"))
    data = ZhipuProvider(cfg).fetch()
    assert data.status == "error"
    assert "401" in data.error


def test_zhipu_fetch_cdp_fail_returns_http_error(monkeypatch):
    """直连失败 + CDP 也失败 → 返回直连真实错误，不暴露 CDP 连接细节。"""
    from providers.zhipu import ZhipuProvider
    cfg = config.new_provider("zhipu")
    cfg.update({"auth_token": "t", "org_id": "o", "project_id": "p",
                "cdp_enabled": True})
    monkeypatch.setattr(ZhipuProvider, "_fetch_team_http",
                        lambda self, d: self._err(d, "凭证已过期"))
    monkeypatch.setattr(ZhipuProvider, "_fetch_team_cdp",
                        lambda self, d: self._err(d, "请启动 CDP Chrome"))
    data = ZhipuProvider(cfg).fetch()
    assert data.status == "error"
    assert "凭证已过期" in data.error
    assert "CDP" not in data.error and "Chrome" not in data.error


def test_mimo_fetch_without_cred_and_cdp_gives_guidance():
    from providers.mimo import MimoProvider
    cfg = config.new_provider("mimo")
    cfg["cdp_enabled"] = False
    data = MimoProvider(cfg).fetch()
    assert data.status == "error"
    assert "提取凭证" in data.error


# ---------------------------------------------------------------------------
# opencode _parse_usage_text 三种 SSR 形态
# ---------------------------------------------------------------------------

def _parse(text):
    from providers.opencode import OpenCodeProvider
    data = UsageData(provider_name="t", fetched_at=1.0)
    ok = OpenCodeProvider({})._parse_usage_text(text, data)
    return ok, data


def test_opencode_parse_json_form():
    ok, data = _parse(
        '{"rollingUsage": {"usagePercent": 12.5, "resetInSec": 3600}}')
    assert ok
    assert data.items[0].label == "5h Rolling"
    assert data.items[0].used_percent == 12.5


def test_opencode_parse_solidjs_form():
    # SolidJS SSR：key 无引号，值是 $R[n]={...}
    text = ("rollingUsage:$R[33]={status:\"ok\",resetInSec:18000,usagePercent:0},"
            "weeklyUsage:$R[34]={status:\"ok\",resetInSec:250197,usagePercent:33},"
            "monthlyUsage:$R[35]={status:\"ok\",resetInSec:654403,usagePercent:88}")
    ok, data = _parse(text)
    assert ok
    by_label = {i.label: i for i in data.items}
    assert by_label["5h Rolling"].used_percent == 0
    assert by_label["每周"].used_percent == 33
    assert by_label["每月"].used_percent == 88
    assert "重置" in by_label["每周"].note


def test_opencode_parse_escaped_json_form():
    text = '\\"rollingUsage\\":{\\"usagePercent\\":7.5,\\"resetInSec\\":60}'
    ok, data = _parse(text)
    assert ok
    assert data.items[0].used_percent == 7.5


def test_opencode_reset_note():
    from providers.opencode import OpenCodeProvider
    f = OpenCodeProvider._reset_note
    assert f(0) == ""
    assert f(300) == "5分钟后重置"
    assert f(3660) == "1小时1分后重置"
    assert f(90000) == "1天1小时后重置"


# ---------------------------------------------------------------------------
# kimi 直连
# ---------------------------------------------------------------------------

def test_kimi_build_headers_injects_jwt_claims():
    from providers.kimi import KimiProvider
    h = KimiProvider._build_headers("tok", {
        "sub": "u1", "device_id": "d1", "ssid": "s1"})
    assert h["Authorization"] == "Bearer tok"
    assert h["x-traffic-id"] == "u1"
    assert h["x-msh-device-id"] == "d1"
    assert h["x-msh-session-id"] == "s1"
    # 缺 claim 时不塞空头
    h2 = KimiProvider._build_headers("tok", {})
    assert "x-traffic-id" not in h2


def test_kimi_fetch_http_missing_kimi_auth():
    from providers.kimi import KimiProvider
    cfg = config.new_provider("kimi")
    cfg["cookie"] = "theme=light"
    data = KimiProvider(cfg)._fetch_http(UsageData(provider_name="k"))
    assert data.status == "error"
    assert "kimi-auth" in data.error
