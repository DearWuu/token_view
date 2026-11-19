"""Kimi 身份校验兜底测试：直连 / CDP 兜底 / 自动重捕都不得跨空间取数。

场景（真实事故）：个人版 provider 与团队版共用 GetSubscriptionStats，
token 的 sub / business_id 决定返回哪个空间的数据。页面切到别的空间时，
CDP 兜底与自动重捕都曾绕过锚定校验，导致个人版静默显示团队空间数据、
甚至把 provider 锚成错身份。

三条取数路径（直连 / CDP 兜底 / 自动重捕）都要核对身份；锚定一经建立
不可变，一次成功的直连不得把锚点刷成另一个空间的身份。

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


def _exp(offset: float = 3600) -> float:
    return time.time() + offset


class _Resp:
    """requests.Response 的最小替身。"""

    def __init__(self, status_code: int, text: str = "", reason: str = ""):
        self.status_code = status_code
        self.text = text
        self.reason = reason


class _CdpHarness:
    """CDPHarness 替身：按表达式内容分派返回值。

    - 读 access_token 的表达式 → 纯字符串 token
    - 读 access_token+refresh_token 的表达式 → {"at":..,"rt":..}
    - 其余（页面内 fetch） → {status, statusText, body} 包装
    """

    def __init__(self, at: str = "", rt: str = "", body: str = "{}",
                 *args, **kwargs):
        self.at = at
        self.rt = rt
        self.body = body
        self.expressions = []

    def find_page(self):
        return {"webSocketDebuggerUrl": "ws://fake/page"}

    def evaluate(self, ws_url, expression, await_promise=True):
        self.expressions.append(expression)
        if "refresh_token" in expression:
            return {"value": _json.dumps({"at": self.at, "rt": self.rt})}
        if "access_token" in expression:
            return {"value": self.at}
        return {"value": _json.dumps({"status": 200, "statusText": "OK",
                                      "body": self.body})}


def _patch_harness(monkeypatch, at: str = "", rt: str = "",
                   body: str = "{}") -> list:
    """把 providers.kimi.CDPHarness 换成假的，返回构造记录列表。"""
    made = []

    def factory(*args, **kwargs):
        h = _CdpHarness(at, rt, body, *args, **kwargs)
        made.append(h)
        return h

    monkeypatch.setattr(kimi_mod, "CDPHarness", factory)
    return made


def _patch_post(monkeypatch, resp: "_Resp") -> list:
    """把 providers.kimi.requests.post 换成假的，记录调用参数。"""
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return resp

    monkeypatch.setattr(kimi_mod.requests, "post", fake_post)
    return calls


def _cfg(**over) -> dict:
    cfg = config.new_provider("kimi")
    cfg.update({
        "access_token": _jwt({"exp": _exp(), "sub": "sub-a"}),
        "refresh_token": "old-rt",
        "token_sub": "sub-a",
        "token_biz": "",
        "cdp_enabled": True,
    })
    cfg.update(over)
    return cfg


def _data() -> UsageData:
    return UsageData(provider_name="kimi", plan_level="订阅额度")


# ---------------------------------------------------------------------------
# CDP 兜底：页面 token 身份与 provider 锚定不符 → 拒绝取数
# ---------------------------------------------------------------------------

def test_fetch_cdp_rejects_mismatched_biz(monkeypatch):
    """个人版 provider 撞上团队空间 token → 报错且不产出任何用量项。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    team_at = _jwt({"exp": _exp(), "sub": "sub-a", "business_id": "biz-team-1"})
    _patch_harness(monkeypatch, team_at, "team-rt",
                   body=_json.dumps({"ratelimitCode5h": {"ratio": 0.25,
                                                        "enabled": True}}))

    result = KimiProvider(cfg)._fetch_cdp(_data())

    assert result.status == "error", "身份错位时必须报错，不能静默取数"
    assert "不匹配" in result.error
    assert "CDP 兜底" in result.error
    assert result.items == [], "身份错位时不得产出任何用量项"


def test_fetch_cdp_rejects_mismatched_sub(monkeypatch):
    """页面换了个账号（sub 不同）→ 同样拒绝。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a")
    other_at = _jwt({"exp": _exp(), "sub": "sub-b"})
    _patch_harness(monkeypatch, other_at, "other-rt")

    result = KimiProvider(cfg)._fetch_cdp(_data())

    assert result.status == "error"
    assert "不匹配" in result.error
    assert result.items == []


def test_fetch_cdp_allows_matching_identity(monkeypatch):
    """锚定与页面身份一致 → 正常取数（回归）。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    same_at = _jwt({"exp": _exp(), "sub": "sub-a"})
    _patch_harness(monkeypatch, same_at, "same-rt",
                   body=_json.dumps({"ratelimitCode5h": {"ratio": 0.25,
                                                        "enabled": True}}))

    result = KimiProvider(cfg)._fetch_cdp(_data())

    assert result.status == "ok", f"身份一致时应正常取数：{result.error}"
    assert [i.label for i in result.items] == ["5h 窗口"]
    assert result.items[0].used_percent == 25.0


def test_fetch_cdp_unanchored_provider_still_works(monkeypatch):
    """未锚定（从未成功 fetch 过）的 provider 走 CDP 兜底不受影响。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="", token_biz="")
    any_at = _jwt({"exp": _exp(), "sub": "sub-x"})
    _patch_harness(monkeypatch, any_at, "rt-x",
                   body=_json.dumps({"ratelimitCode5h": {"ratio": 0.1,
                                                        "enabled": True}}))

    result = KimiProvider(cfg)._fetch_cdp(_data())

    assert result.status == "ok", f"未锚定时不应拦截 CDP 兜底：{result.error}"
    assert len(result.items) == 1


def test_team_provider_fetch_cdp_rejects_mismatched_identity(monkeypatch):
    """团队版继承同一校验：页面切到个人空间同样拒绝。"""
    from providers.kimi_team import KimiTeamProvider
    cfg = config.new_provider("kimi_team")
    cfg.update({
        "access_token": _jwt({"exp": _exp(), "sub": "sub-a"}),
        "refresh_token": "old-rt",
        "token_sub": "sub-a",
        "token_biz": "biz-team-1",
        "cdp_enabled": True,
    })
    personal_at = _jwt({"exp": _exp(), "sub": "sub-a"})
    _patch_harness(monkeypatch, personal_at, "personal-rt")

    result = KimiTeamProvider(cfg)._fetch_cdp(_data())

    assert result.status == "error"
    assert "不匹配" in result.error
    assert result.items == []


# ---------------------------------------------------------------------------
# 自动重捕：未锚定时拒绝写入
# ---------------------------------------------------------------------------

def test_recapture_refuses_when_not_anchored(monkeypatch):
    """未锚定 → 拒绝自动重捕，提示先手动提取凭证，不碰 CDP。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="", token_biz="")
    page_at = _jwt({"exp": _exp(), "sub": "sub-z"})
    made = _patch_harness(monkeypatch, page_at, "page-rt")

    err = KimiProvider(cfg)._recapture_tokens_cdp()

    assert "尚未锚定" in err, f"应提示先锚定身份，实际：{err}"
    assert "提取凭证" in err
    assert made == [], "未锚定时不该去连 CDP"
    assert cfg["refresh_token"] == "old-rt", "未锚定时不得覆盖 refresh_token"
    assert cfg["access_token"] != page_at, "未锚定时不得写入页面的 access_token"
    assert cfg["token_sub"] == "", "未锚定时不得写入锚点"


def test_recapture_allows_matching_identity(monkeypatch):
    """锚定且身份一致 → 自动重捕照旧（回归）。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    page_at = _jwt({"exp": _exp(), "sub": "sub-a"})
    _patch_harness(monkeypatch, page_at, "page-rt")
    monkeypatch.setattr(kimi_mod.KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    err = KimiProvider(cfg)._recapture_tokens_cdp()

    assert err == "", f"身份一致时重捕应成功，实际：{err}"
    assert cfg["refresh_token"] == "page-rt"
    assert cfg["access_token"] == page_at


# ---------------------------------------------------------------------------
# 错误提示要能传到用户面前
# ---------------------------------------------------------------------------

def test_fallback_cdp_keeps_identity_mismatch_error(monkeypatch):
    """直连失败 + CDP 兜底身份错位 → 最终错误保留错位提示，不被直连错误覆盖。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    team_at = _jwt({"exp": _exp(), "sub": "sub-a", "business_id": "biz-team-1"})
    _patch_harness(monkeypatch, team_at, "team-rt")
    provider = KimiProvider(cfg)

    result = provider._fallback_cdp(
        _data(), "刷新 token 失败 HTTP 401", lambda d: provider._fetch_cdp(d))

    assert result.status == "error"
    assert "不匹配" in result.error, f"错位提示被直连错误覆盖了：{result.error}"
    assert result.items == []


def test_fetch_end_to_end_keeps_identity_mismatch(monkeypatch):
    """完整链路：直连 401（重捕被锚定拒绝）→ CDP 兜底也错位 → 报身份错位。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    team_at = _jwt({"exp": _exp(), "sub": "sub-a", "business_id": "biz-team-1"})
    _patch_harness(monkeypatch, team_at, "team-rt",
                   body=_json.dumps({"ratelimitCode5h": {"ratio": 0.9,
                                                        "enabled": True}}))
    monkeypatch.setattr(kimi_mod.requests, "post",
                        lambda *a, **k: _Resp(401, "unauthenticated"))
    monkeypatch.setattr(kimi_mod.KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": None)

    result = KimiProvider(cfg).fetch()

    assert result.status == "error"
    assert "不匹配" in result.error, f"最终错误应为身份错位：{result.error}"
    assert result.items == [], "身份错位时不得产出任何用量项"


# ---------------------------------------------------------------------------
# 直连路径：身份不符必须先拦，不发请求
# ---------------------------------------------------------------------------

def test_direct_rejects_mismatched_identity_without_request(monkeypatch):
    """直连 token 身份与锚定不符 → 报错、不发用量请求、不产出用量项。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="")
    cfg["access_token"] = _jwt({"exp": _exp(), "sub": "sub-b"})
    calls = _patch_post(monkeypatch, _Resp(200, "{}"))

    result = KimiProvider(cfg)._fetch_stats_http(_data(), retried=False)

    assert result.status == "error", "身份不符时必须报错，不能静默取数"
    assert KimiProvider.DIRECT_IDENTITY_MISMATCH_HINT in result.error, \
        f"错误应带直连错位提示，实际：{result.error}"
    assert calls == [], "身份不符时不该发出用量请求"
    assert result.items == [], "身份不符时不得产出任何用量项"


def test_fetch_rejects_mismatched_identity_before_any_request(monkeypatch):
    """完整 fetch 链路：直连身份不符 → 直接把错位提示抛给用户（不开 CDP）。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="sub-a", token_biz="", cdp_enabled=False)
    cfg["access_token"] = _jwt({"exp": _exp(), "sub": "sub-b"})
    calls = _patch_post(monkeypatch, _Resp(200, "{}"))
    monkeypatch.setattr(kimi_mod.KimiProvider, "_reload_tokens",
                        lambda self: None)

    result = KimiProvider(cfg).fetch()

    assert result.status == "error"
    assert KimiProvider.DIRECT_IDENTITY_MISMATCH_HINT in result.error, \
        f"用户看到的错误应为身份错位，实际：{result.error}"
    assert calls == [], "身份不符时不该发出用量请求"
    assert result.items == []


# ---------------------------------------------------------------------------
# 锚定不可变：成功取数也不许改写锚点（防漂移）
# ---------------------------------------------------------------------------

def test_direct_success_does_not_overwrite_anchor(monkeypatch):
    """直连成功后锚定不变：token 身份不得把锚点刷成另一个空间。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="jfh2n0", token_biz="biz-team-1")
    cfg["access_token"] = _jwt({"exp": _exp(), "sub": "jfh2n0",
                                "business_id": "biz-team-1"})
    body = _json.dumps({"ratelimitCode5h": {"ratio": 0.25, "enabled": True}})
    calls = _patch_post(monkeypatch, _Resp(200, body))
    persisted = []
    monkeypatch.setattr(kimi_mod.KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": persisted.append(old_rt))
    provider = KimiProvider(cfg)

    result = provider._fetch_stats_http(_data(), retried=False)

    assert result.status == "ok", f"身份一致时应正常取数：{result.error}"
    assert len(calls) == 1
    assert [i.label for i in result.items] == ["5h 窗口"]
    assert cfg["token_sub"] == "jfh2n0", "锚定不得被 token 身份覆盖"
    assert cfg["token_biz"] == "biz-team-1", "锚定的空间标识也不得被改写"
    assert persisted == [], "已锚定时不该再落盘身份标记"

    # 锚定不可变：拿别的空间身份来写也应被拒绝
    assert provider._record_identity({"sub": "jfh2n0",
                                      "business_id": "biz-other"}) is False
    assert cfg["token_sub"] == "jfh2n0"
    assert cfg["token_biz"] == "biz-team-1"


def test_first_direct_success_records_identity(monkeypatch):
    """锚定为空时首次直连成功 → 写入 token 身份（首捕仍然可用）。"""
    from providers.kimi import KimiProvider
    cfg = _cfg(token_sub="", token_biz="")
    cfg["access_token"] = _jwt({"exp": _exp(), "sub": "jfh2n0",
                                "business_id": "biz-team-1"})
    body = _json.dumps({"ratelimitCode5h": {"ratio": 0.4, "enabled": True}})
    calls = _patch_post(monkeypatch, _Resp(200, body))
    persisted = []
    monkeypatch.setattr(kimi_mod.KimiProvider, "_persist_tokens",
                        lambda self, old_rt="": persisted.append(old_rt))

    result = KimiProvider(cfg)._fetch_stats_http(_data(), retried=False)

    assert result.status == "ok", f"未锚定时不应拦截直连：{result.error}"
    assert len(calls) == 1
    assert cfg["token_sub"] == "jfh2n0", "首次成功应写入 token 身份"
    assert cfg["token_biz"] == "biz-team-1", "首次成功应写入空间标识"
    assert persisted == [""], "写入身份后应落盘一次"


# ---------------------------------------------------------------------------
# 团队版继承路径：同一套校验
# ---------------------------------------------------------------------------

def test_team_provider_direct_rejects_personal_token(monkeypatch):
    """团队版 provider 撞上个人 token → 同样拒绝，且不发请求。"""
    from providers.kimi import KimiProvider
    from providers.kimi_team import KimiTeamProvider
    cfg = config.new_provider("kimi_team")
    cfg.update({
        "access_token": _jwt({"exp": _exp(), "sub": "sub-a"}),
        "refresh_token": "old-rt",
        "token_sub": "sub-a",
        "token_biz": "biz-team-1",
        "cdp_enabled": False,
    })
    calls = _patch_post(monkeypatch, _Resp(200, "{}"))

    result = KimiTeamProvider(cfg)._fetch_stats_http(_data(), retried=False)

    assert result.status == "error", "团队锚定 + 个人 token 必须拒绝"
    assert KimiProvider.DIRECT_IDENTITY_MISMATCH_HINT in result.error, \
        f"错误应带直连错位提示，实际：{result.error}"
    assert calls == [], "身份不符时不该发出用量请求"
    assert result.items == [], "身份不符时不得产出任何用量项"
