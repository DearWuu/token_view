"""Kimi 自愈链路测试：refresh_token 失效（HTTP 401）→ 从页面重捕凭证。

全部走 mock（requests.post / CDPHarness），不碰真网络。
"""
from __future__ import annotations

import base64 as b64
import json as _json
import time

import config
import providers.kimi as kimi_mod
from providers.base import UsageData


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _jwt(claims: dict) -> str:
    """构造一个假 JWT（payload 段 base64url，不验签）。"""
    payload = b64.urlsafe_b64encode(
        _json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.sig"


class _Resp:
    """requests.Response 的最小替身。"""

    def __init__(self, status_code: int, text: str = "", reason: str = ""):
        self.status_code = status_code
        self.text = text
        self.reason = reason


class _FakeHarness:
    """CDPHarness 替身：find_page + evaluate 返回预设的 localStorage token。"""

    def __init__(self, at: str = "", rt: str = "", *args, **kwargs):
        self.at = at
        self.rt = rt
        self.instances = []

    def find_page(self):
        return {"webSocketDebuggerUrl": "ws://fake/page"}

    def evaluate(self, ws_url, expression, await_promise=True):
        return {"value": _json.dumps({"at": self.at, "rt": self.rt})}


def _patch_harness(monkeypatch, at: str, rt: str) -> list:
    """把 providers.kimi.CDPHarness 换成假的，返回构造记录列表。"""
    made = []

    def factory(*args, **kwargs):
        h = _FakeHarness(at, rt, *args, **kwargs)
        made.append(h)
        return h

    monkeypatch.setattr(kimi_mod, "CDPHarness", factory)
    return made


def _cfg(**over) -> dict:
    cfg = config.new_provider("kimi")
    cfg.update({
        "access_token": _jwt({"exp": time.time() + 3600, "sub": "sub-a"}),
        "refresh_token": "old-rt",
        "token_sub": "sub-a",
        "token_biz": "",
        "cdp_enabled": True,
    })
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------------------
# 401 → 重捕成功
# ---------------------------------------------------------------------------

def test_refresh_401_recaptures_from_page(monkeypatch):
    """refresh_token 已被页面轮转作废（401）→ 从页面重捕 → 流程继续。"""
    from providers.kimi import KimiProvider
    cfg = _cfg()
    new_at = _jwt({"exp": time.time() + 3600, "sub": "sub-a"})

    posted = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posted.append(url)
        return _Resp(401, "unauthenticated")

    monkeypatch.setattr(kimi_mod.requests, "post", fake_post)
    _patch_harness(monkeypatch, new_at, "new-rt")
    persisted = []
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": persisted.append(old_rt))

    err = KimiProvider(cfg)._refresh_access_token()

    assert err == "", f"重捕成功后应无错误，实际：{err}"
    assert cfg["access_token"] == new_at
    assert cfg["refresh_token"] == "new-rt"
    assert len(posted) == 1, "重捕后的 at 仍有效时不应再发第二次刷新请求"
    assert persisted == ["old-rt"], "落盘时应带上旧 rt 以同步同族 provider"


def test_recapture_persists_identity(monkeypatch):
    """重捕成功后身份标记写入 cfg（供下次重捕做空间校验）。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="", token_biz="")
    new_at = _jwt({"exp": time.time() + 3600, "sub": "sub-z"})
    _patch_harness(monkeypatch, new_at, "new-rt")
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    assert KimiProvider(cfg)._recapture_tokens_cdp() == ""
    assert cfg["token_sub"] == "sub-z"
    assert cfg["token_biz"] == ""


def test_refresh_401_without_cdp_does_not_recapture(monkeypatch):
    """关了 CDP 就不重捕，直接返回原始错误。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(cdp_enabled=False)

    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(401, "unauthenticated")

    monkeypatch.setattr(kimi_mod.requests, "post", fake_post)
    made = _patch_harness(monkeypatch, "x", "y")

    err = KimiProvider(cfg)._refresh_access_token()
    assert "401" in err
    assert made == [], "cdp_enabled=False 时不应连 CDP"


# ---------------------------------------------------------------------------
# 身份校验：页面空间与 provider 错位时拒绝写入
# ---------------------------------------------------------------------------

def test_recapture_rejects_mismatched_sub(monkeypatch):
    """页面登录身份与该 provider 不一致 → 拒绝写入，不覆盖现有 token。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a")
    other_at = _jwt({"exp": time.time() + 3600, "sub": "sub-b"})
    _patch_harness(monkeypatch, other_at, "other-rt")
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    err = KimiProvider(cfg)._recapture_tokens_cdp()

    assert "不匹配" in err
    assert cfg["refresh_token"] == "old-rt", "身份不符时不应覆盖 refresh_token"
    assert cfg["access_token"] != other_at, "身份不符时不应覆盖 access_token"


def test_recapture_rejects_mismatched_biz(monkeypatch):
    """个人版 provider 遇到团队空间 token（带 business_id）→ 拒绝写入。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    team_at = _jwt({"exp": time.time() + 3600, "sub": "sub-a",
                    "business_id": "biz-team-1"})
    _patch_harness(monkeypatch, team_at, "team-rt")
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    err = KimiProvider(cfg)._recapture_tokens_cdp()

    assert "不匹配" in err
    assert cfg["refresh_token"] == "old-rt"


def test_recapture_allows_matching_team_token(monkeypatch):
    """团队版 provider 遇到同空间 token → 正常写入。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="biz-team-1")
    team_at = _jwt({"exp": time.time() + 3600, "sub": "sub-a",
                    "business_id": "biz-team-1"})
    _patch_harness(monkeypatch, team_at, "team-rt")
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    assert KimiProvider(cfg)._recapture_tokens_cdp() == ""
    assert cfg["refresh_token"] == "team-rt"
    assert cfg["token_biz"] == "biz-team-1"


def test_refresh_401_then_retry_with_recaptured_rt(monkeypatch):
    """重捕到的 access_token 也过期 → 用新 rt 再换一次（不再触发重捕）。"""
    from providers.kimi import KimiProvider
    cfg = _cfg()
    stale_at = _jwt({"exp": time.time() - 10, "sub": "sub-a"})
    fresh_at = _jwt({"exp": time.time() + 3600, "sub": "sub-a"})
    posted = []

    def fake_post(url, headers=None, json=None, timeout=None):
        posted.append(json)
        if len(posted) == 1:
            return _Resp(401, "unauthenticated")
        return _Resp(200, _json.dumps({"accessToken": fresh_at,
                                       "refreshToken": "rt-2"}), "OK")

    monkeypatch.setattr(kimi_mod.requests, "post", fake_post)
    _patch_harness(monkeypatch, stale_at, "new-rt")
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    err = KimiProvider(cfg)._refresh_access_token()

    assert err == ""
    assert len(posted) == 2
    assert posted[1]["refreshToken"] == "new-rt", "第二次应带重捕来的 rt"
    assert cfg["access_token"] == fresh_at
    assert cfg["refresh_token"] == "rt-2"


def test_recapture_without_rt_reports_guidance(monkeypatch):
    """页面没有登录（localStorage 无 refresh_token）→ 给明确指引。"""
    from providers.kimi import KimiProvider
    cfg = _cfg()
    _patch_harness(monkeypatch, "", "")
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    err = KimiProvider(cfg)._recapture_tokens_cdp()
    assert "refresh_token" in err


# ---------------------------------------------------------------------------
# 身份落盘：fetch 成功后记录 sub / business_id
# ---------------------------------------------------------------------------

def test_fetch_stats_records_identity_on_success(monkeypatch):
    """用量拉取成功后记录 token 身份，供下次重捕校验页面空间。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="", token_biz="")
    body = _json.dumps({
        "ratelimitCode5h": {"ratio": 0.25, "enabled": True},
    })

    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(200, body, "OK")

    monkeypatch.setattr(kimi_mod.requests, "post", fake_post)
    persisted = []
    monkeypatch.setattr(KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": persisted.append(1))

    result = KimiProvider(cfg)._fetch_stats_http(
        UsageData(provider_name="k"), retried=False)

    assert result.status == "ok"
    assert cfg["token_sub"] == "sub-a"
    assert cfg["token_biz"] == ""
    assert persisted, "身份首次记录后应落盘"


def test_persist_tokens_writes_identity_to_config(monkeypatch):
    """_persist_tokens 把 token 与身份标记一起写到 config。"""
    from providers.kimi import KimiProvider
    cfg = config.new_provider("kimi")
    cfg.update({"id": "pid-1", "access_token": "at", "refresh_token": "rt",
                "token_sub": "sub-a", "token_biz": "biz-1"})
    saved = {}

    def fake_load():
        return {"providers": [{"id": "pid-1", "type": "kimi",
                               "access_token": "", "refresh_token": ""}]}

    def fake_save(c):
        saved.update(c)

    monkeypatch.setattr(kimi_mod.config, "load", fake_load)
    monkeypatch.setattr(kimi_mod.config, "save", fake_save)

    KimiProvider(cfg)._persist_tokens()

    p = saved["providers"][0]
    assert p["access_token"] == "at"
    assert p["refresh_token"] == "rt"
    assert p["token_sub"] == "sub-a"
    assert p["token_biz"] == "biz-1"
