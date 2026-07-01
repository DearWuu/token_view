"""用量查询 Provider。

统一 fetch() -> UsageData。两种实现：
  - ZhipuProvider     智谱 GLM Coding Plan。
                      * 团队版：Cookie + sub-account-rank 接口（按当前成员解析 5h/周/MCP 百分比）
                      * 个人版：API Key 或 Cookie 调 /api/monitor/usage/quota/limit（limits[] 解析）
  - OpenCodeProvider  OpenCode Go，无官方 API，用 auth cookie 抓控制台页面解析。
"""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import requests

from logger import log

try:
    from websocket import create_connection as ws_connect
    HAS_WS = True
except Exception as _e:
    HAS_WS = False
    log(f"websocket-client 导入失败: {_e}")

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 智谱个人版 limits 里 unit 字段 → 窗口语义
ZHIPU_UNIT_LABEL = {3: "5h 窗口", 6: "每周窗口"}


def fmt_tokens(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1e8:
        return f"{n / 1e8:.1f}亿"
    if n >= 1e4:
        return f"{n / 1e4:.1f}万"
    return str(int(n))


@dataclass
class UsageItem:
    label: str
    used_percent: float                  # 已用百分比 0~100
    reset_at: Optional[float] = None
    note: str = ""


@dataclass
class UsageData:
    provider_name: str
    plan_level: str = ""
    items: list = field(default_factory=list)
    status: str = "ok"
    error: str = ""
    fetched_at: float = 0.0


class BaseProvider:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def fetch(self) -> UsageData:
        raise NotImplementedError


class ZhipuProvider(BaseProvider):
    """智谱 GLM Coding Plan。按配置自动选择取数方式：

      - 团队版（Cookie + sub-account-rank）：解析团队成员的 5h/周/MCP 百分比
      - 个人版（API Key 或 Cookie + 其它 URL）：解析 limits[]
      智谱 Authorization 不加 Bearer。
    """

    TEAM_RANK_URL = "https://bigmodel.cn/api/monitor/usage/sub-account-rank"

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "智谱 GLM"
        data = UsageData(provider_name=name, fetched_at=time.time())

        cookie = (self.cfg.get("cookie") or "").strip()
        usage_url = (self.cfg.get("usage_url") or "").strip()
        key = (self.cfg.get("api_key") or "").strip()

        auth = (self.cfg.get("auth_token") or "").strip()

        # 优先 CDP：连接用户已登录的调试 Chrome，在页面上下文 fetch，避开反爬
        if self.cfg.get("cdp_enabled", True):
            return self._fetch_team_cdp(data)

        if cookie and usage_url:
            headers = {"User-Agent": BROWSER_UA, "Cookie": cookie,
                       "Accept": "application/json, text/plain, */*",
                       "Referer": "https://bigmodel.cn/"}
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
            headers = {"Authorization": key, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
                       "Content-Type": "application/json", "User-Agent": BROWSER_UA,
                       "Referer": "https://bigmodel.cn/", "Origin": "https://bigmodel.cn"}
            return self._get(base + endpoint, headers, data, None, self._parse_limits)

        data.status, data.error = "error", "未配置：请用 API Key，或在设置里登录抓取凭证"
        return data

    # ---- CDP 模式：连接已登录的调试 Chrome 抓团队用量 ----
    CDP_EVAL_TIMEOUT = 30          # Runtime.evaluate 等待 fetch 完成的秒数

    def _fetch_team_cdp(self, data: UsageData) -> UsageData:
        """通过 CDP 连接用户已登录的 Chrome，在 bigmodel.cn 页面上下文执行
        fetch('/api/monitor/usage/sub-account-rank', {credentials:'include'})，
        拿到团队用量后用现有 _parse_team 解析。"""
        if not HAS_WS:
            data.status, data.error = "error", "缺少 websocket-client 依赖（pip install websocket-client）"
            return data

        cdp_url = (self.cfg.get("cdp_url") or "").strip()
        if not cdp_url:
            port = int(self.cfg.get("cdp_port") or 9222)
            cdp_url = f"http://127.0.0.1:{port}"

        # 1) GET /json 拿 page target 列表
        try:
            r = requests.get(cdp_url.rstrip("/") + "/json", timeout=5)
            r.raise_for_status()
            targets = r.json()
        except requests.RequestException as e:
            data.status = "error"
            data.error = "请先在设置里启动 CDP Chrome 并登录智谱（连接失败）"
            log(f"CDP /json 连接失败: {e}")
            return data
        except ValueError as e:
            data.status = "error"
            data.error = "CDP 返回非 JSON，可能端口被占用"
            log(f"CDP /json 非 JSON: {e}")
            return data

        # 2) 找一个 url 含 bigmodel.cn 的 Page target
        page = next(
            (t for t in targets
             if t.get("type") == "page" and "bigmodel.cn" in (t.get("url") or "")),
            None,
        )
        if page is None:
            data.status = "error"
            data.error = "请在 CDP Chrome 里打开 bigmodel.cn 并登录"
            log(f"CDP 未发现 bigmodel.cn 标签页，现有 target: "
                f"{[(t.get('type'), (t.get('url') or '')[:60]) for t in targets]}")
            return data

        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            data.status, data.error = "error", "CDP target 缺少 webSocketDebuggerUrl"
            return data

        # 3) 生成时间范围 + JS（从 cookie 读 JWT 加 Authorization 头）
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

        # 4) 连 WebSocket 发 Runtime.evaluate
        try:
            ws = ws_connect(
                ws_url, timeout=self.CDP_EVAL_TIMEOUT,
                origin="http://127.0.0.1:" + str(self.cfg.get("cdp_port") or 9222),
            )
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": js, "awaitPromise": True,
                           "returnByValue": True},
            }))
            raw = ws.recv()
            ws.close()
        except Exception as e:
            data.status = "error"
            data.error = f"CDP 通信失败: {e}"
            log(f"CDP WebSocket 异常: {e}")
            return data

        try:
            resp = json.loads(raw)
        except ValueError as e:
            data.status, data.error = "error", f"CDP 响应非 JSON: {e}"
            return data

        # 5) 解析结果
        result = resp.get("result", {}).get("result", {})
        if result.get("type") == "undefined" or "value" not in result:
            exc = resp.get("result", {}).get("exceptionDetails")
            if exc:
                msg = (exc.get("exception", {}) or {}).get("description") or exc.get("text")
                data.status = "error"
                data.error = f"页面 fetch 失败（登录可能已过期，请在 CDP Chrome 重新登录）: {msg}"
                log(f"CDP evaluate 异常: {msg}")
                return data
            data.status, data.error = "error", "CDP 返回 undefined"
            return data

        text = result.get("value") or ""
        log(f"CDP fetch 返回({len(text)}字符): {text[:200]}")
        if not text.strip():
            data.status, data.error = "error", "CDP fetch 返回空（登录可能已失效）"
            return data
        try:
            j = json.loads(text)
        except ValueError as e:
            data.status, data.error = "error", f"接口返回非 JSON: {e}"
            return data
        if not j.get("success") or not j.get("data"):
            data.status = "error"
            data.error = j.get("msg") or "接口返回空（登录可能已过期，请在 CDP Chrome 重新登录）"
            return data
        self._parse_team(j["data"], data)
        log(f"CDP 解析: status={data.status}, level={data.plan_level}, "
            f"items={[(i.label, i.used_percent) for i in data.items]}")
        return data

    def _get(self, url, headers, data, params, parse):
        try:
            headers = dict(headers or {})
            headers.setdefault("Cache-Control", "no-cache")
            headers.setdefault("Pragma", "no-cache")
            r = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            data.status, data.error = "error", f"网络错误: {e}"
            log(f"GET {url} 网络错误: {e}")
            return data
        log(f"GET {url} -> HTTP {r.status_code}, body={r.text[:200]!r}")
        if not r.text.strip():
            data.status = "error"
            data.error = "接口返回空（Cookie 可能失效或被反爬），请重新登录抓取"
            return data
        try:
            j = r.json()
        except ValueError:
            data.status, data.error = "error", f"非 JSON 响应（HTTP {r.status_code}）"
            return data
        if not j.get("success") or not j.get("data"):
            data.status = "error"
            data.error = j.get("msg") or "响应异常"
            return data
        parse(j["data"], data)
        log(f"解析: status={data.status}, level={data.plan_level}, "
            f"items={[(i.label, i.used_percent) for i in data.items]}")
        return data

    def _parse_team(self, d, data):
        """团队版 sub-account-rank：按 customer_id 匹配当前成员，否则取排名第一。"""
        rank_list = d.get("rankList") or []
        if not rank_list:
            data.status = "empty"
            return
        cid = str(self.cfg.get("customer_id") or "").strip()
        me = next((r for r in rank_list if str(r.get("customerId")) == cid), None) if cid else None
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
    def _parse_limits(d, data):
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
                note = f"{fmt_tokens(cur)} / {fmt_tokens(total)} tokens" \
                    if isinstance(cur, (int, float)) and isinstance(total, (int, float)) and total else ""
                data.items.append(UsageItem(label, pct, reset_at, note))
            elif t == "TIME_LIMIT":
                note = f"{int(cur)} / {int(total)} 次" \
                    if isinstance(cur, (int, float)) and isinstance(total, (int, float)) and total else ""
                data.items.append(UsageItem("MCP 工具调用", pct, reset_at, note))
        if not data.items:
            data.status = "empty"


class OpenCodeProvider(BaseProvider):
    """OpenCode Go。通过 CDP 在页面上下文获取用量数据。

    API 返回格式（从 __server 响应解析）：
    {
        rollingUsage: {status, resetInSec, usagePercent},
        weeklyUsage: {status, resetInSec, usagePercent},
        monthlyUsage: {status, resetInSec, usagePercent}
    }
    """

    URL_FMT = "https://opencode.ai/workspace/{wsid}/go"

    def fetch(self) -> UsageData:
        wsid = (self.cfg.get("workspace_id") or "").strip()
        name = self.cfg.get("name") or "OpenCode"
        data = UsageData(provider_name=name, plan_level="Go", fetched_at=time.time())

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        if not wsid:
            data.status = "error"
            data.error = "未配置 workspace_id"
            return data
        data.status = "error"
        data.error = "请启用 CDP 模式"
        return data

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        if not HAS_WS:
            data.status, data.error = "error", "缺少 websocket-client 依赖"
            return data

        cdp_url = (self.cfg.get("cdp_url") or "").strip()
        if not cdp_url:
            port = int(self.cfg.get("cdp_port") or 9222)
            cdp_url = f"http://127.0.0.1:{port}"

        try:
            r = requests.get(cdp_url.rstrip("/") + "/json", timeout=5)
            r.raise_for_status()
            targets = r.json()
        except requests.RequestException as e:
            data.status = "error"
            data.error = "请先在设置里启动 CDP Chrome 并登录 opencode.ai（连接失败）"
            log(f"OpenCode CDP /json 连接失败: {e}")
            return data
        except ValueError as e:
            data.status = "error"
            data.error = "CDP 返回非 JSON，可能端口被占用"
            log(f"OpenCode CDP /json 非 JSON: {e}")
            return data

        page = next(
            (t for t in targets
             if t.get("type") == "page" and "opencode.ai" in (t.get("url") or "")),
            None,
        )
        if page is None:
            data.status = "error"
            data.error = "请在 CDP Chrome 里打开 opencode.ai 并登录"
            log(f"OpenCode CDP 未发现 opencode.ai 标签页")
            return data

        # 自动从 URL 提取 workspace_id
        wsid = (self.cfg.get("workspace_id") or "").strip()
        if not wsid:
            page_url = page.get("url") or ""
            m = re.search(r"(wrk_[A-Z0-9]+)", page_url)
            if m:
                wsid = m.group(1)
                log(f"OpenCode 自动获取 workspace_id: {wsid}")
            else:
                data.status = "error"
                data.error = "未配置 workspace_id，且无法从页面 URL 自动获取"
                return data

        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            data.status, data.error = "error", "CDP target 缺少 webSocketDebuggerUrl"
            return data

        # OpenCode 是 SolidJS SPA，用量数据由 JS 水合后渲染到 DOM。
        # 直接 fetch 页面 URL 返回的是 HTML 不含 JSON 用量字段，
        # 读 DOM 又只能拿到上次页面加载时的旧数据。
        # 解决：用 CDP Page.reload 重新加载页面（忽略缓存），
        # 等加载完 + SPA 水合后读 DOM，拿到最新数据。
        try:
            ws = ws_connect(
                ws_url, timeout=30,
                origin="http://127.0.0.1:" + str(self.cfg.get("cdp_port") or 9222),
            )
        except Exception as e:
            data.status = "error"
            data.error = f"CDP 通信失败: {e}"
            log(f"OpenCode CDP WebSocket 连接失败: {e}")
            return data

        try:
            # 1) 启用 Page 域
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            self._cdp_recv_until_id(ws, 1, timeout=5)

            # 2) 重新加载页面（忽略缓存）
            ws.send(json.dumps({
                "id": 2, "method": "Page.reload",
                "params": {"ignoreCache": True},
            }))

            # 3) 等待 Page.loadEventFired 事件
            loaded = self._cdp_wait_event(ws, "Page.loadEventFired", timeout=30)
            if not loaded:
                ws.close()
                data.status = "error"
                data.error = "页面重新加载超时"
                return data

            # 4) 等待 SPA 水合完成
            time.sleep(2)

            # 5) 读 DOM 中的用量百分比
            js = self._opencode_read_dom_js()
            ws.send(json.dumps({
                "id": 3, "method": "Runtime.evaluate",
                "params": {"expression": js, "awaitPromise": True,
                           "returnByValue": True},
            }))

            raw = self._cdp_recv_until_id(ws, 3, timeout=10)
            ws.close()

            if not raw:
                data.status, data.error = "error", "读取 DOM 超时"
                return data

        except Exception as e:
            try:
                ws.close()
            except Exception:
                pass
            data.status = "error"
            data.error = f"CDP 通信失败: {e}"
            log(f"OpenCode CDP WebSocket 异常: {e}")
            return data

        # 6) 解析结果
        try:
            resp = json.loads(raw)
        except ValueError as e:
            data.status, data.error = "error", f"CDP 响应非 JSON: {e}"
            return data

        result = resp.get("result", {}).get("result", {})
        if result.get("type") == "undefined" or "value" not in result:
            exc = resp.get("result", {}).get("exceptionDetails")
            if exc:
                msg = (exc.get("exception", {}) or {}).get("description") or exc.get("text")
                data.status = "error"
                data.error = f"DOM 读取失败: {msg}"
                return data
            data.status, data.error = "error", "CDP 返回 undefined"
            return data

        text = result.get("value") or ""
        log(f"OpenCode CDP DOM 读取: {text[:500]}")
        if not text.strip():
            data.status, data.error = "error", "DOM 读取为空"
            return data

        return self._parse_response(text, data)

    @staticmethod
    def _cdp_recv_until_id(ws, msg_id, timeout=10):
        """读取 CDP WebSocket 消息直到收到指定 id 的响应。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = ws.recv()
                msg = json.loads(raw)
                if msg.get("id") == msg_id:
                    return raw
            except Exception:
                break
        return None

    @staticmethod
    def _cdp_wait_event(ws, method, timeout=30):
        """等待指定的 CDP 事件。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = ws.recv()
                msg = json.loads(raw)
                if msg.get("method") == method:
                    return msg
            except Exception:
                break
        return None

    @staticmethod
    def _opencode_read_dom_js():
        """生成读取 OpenCode 页面 DOM 用量的 JS。"""
        return (
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

    def _parse_response(self, text: str, data: UsageData) -> UsageData:
        try:
            j = json.loads(text)
        except ValueError as e:
            data.status, data.error = "error", f"JSON 解析失败: {e}"
            return data

        if j.get("error") == "timeout":
            data.status = "error"
            data.error = "获取超时，请确保 opencode.ai 页面已加载完成"
            return data

        if j.get("source") == "network":
            body = j.get("body") or ""
            if self._parse_usage_text(body, data):
                log(f"OpenCode 网络刷新: url={j.get('url')}, items={[(i.label, i.used_percent) for i in data.items]}")
                return data
            log(f"OpenCode 网络响应未解析: url={j.get('url')}, body={body[:500]}")
            if isinstance(j.get("dom"), dict):
                j = j["dom"]
            else:
                data.status = "empty"
                data.error = "已主动请求 OpenCode，但响应里未解析到用量字段"
                return data

        if j.get("source") == "dom" and isinstance(j.get("dom"), dict):
            j = j["dom"]

        if self._append_usage_from_obj(j, data):
            log(f"OpenCode JSON 解析: items={[(i.label, i.used_percent) for i in data.items]}")
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
            
            # 如果没有匹配到特定类型，使用通用百分比
            if not data.items and pcts:
                for i, pct in enumerate(pcts[:3]):
                    labels = ["5 hours usage", " weekly usage", " monthly usage"]
                    data.items.append(UsageItem(labels[i], pct))
        
        # 处理 _server API 响应
        else:
            for key, label in [
                ("rollingUsage", "5h Rolling"),
                ("weeklyUsage", "每周"),
                ("monthlyUsage", "每月"),
            ]:
                usage = j.get(key) or {}
                pct = usage.get("usagePercent")
                reset_sec = usage.get("resetInSec")
                if pct is not None:
                    note = ""
                    if reset_sec:
                        h = reset_sec // 3600
                        m = (reset_sec % 3600) // 60
                        if h > 24:
                            note = f"{h // 24}天{h % 24}小时后重置"
                        elif h > 0:
                            note = f"{h}小时{m}分后重置"
                        else:
                            note = f"{m}分钟后重置"
                    data.items.append(UsageItem(label, float(pct), note=note))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据，请确保页面显示了用量信息"

        log(f"OpenCode 解析: status={data.status}, items={[(i.label, i.used_percent) for i in data.items]}")
        return data

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
            patterns = [
                rf'"{key}"\s*:\s*\{{[^{{}}]*?"usagePercent"\s*:\s*([0-9.]+)',
                rf'\\"{key}\\"\s*:\s*\{{[^{{}}]*?\\"usagePercent\\"\s*:\s*([0-9.]+)',
            ]
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    data.items.append(UsageItem(label, float(m.group(1))))
                    found = True
                    break
        return found

    def _append_usage_from_obj(self, obj, data: UsageData) -> bool:
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

    @staticmethod
    def _surrounding_text(html: str, idx: int, window: int) -> str:
        """取 idx 前后 window 字符的可见文本。"""
        start = max(0, idx - window)
        end = min(len(html), idx + window)
        chunk = html[start:end]
        chunk = re.sub(r"<[^>]+>", " ", chunk)
        chunk = re.sub(r"\s+", " ", chunk)
        return chunk.strip()

    @staticmethod
    def _guess_label(ctx: str) -> str:
        """从上下文中猜窗口标签。"""
        ctx_lower = ctx.lower()
        for kw, lbl in [("5h", "5h Rolling"), ("5 hour", "5h Rolling"),
                         ("rolling", "5h Rolling"), ("hourly", "5h Rolling"),
                         ("weekly", "每周"), ("week", "每周"),
                         ("monthly", "每月"), ("month", "每月"),
                         ("daily", "每日"), ("day", "每日")]:
            if kw in ctx_lower:
                return lbl
        return "用量"

    # ---- 策略 3：纯文本回退（原方案增强） ----
    def _parse_text_fallback(self, html: str) -> list:
        """从 HTML 去标签后的纯文本里按关键词找百分比。"""
        text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        items = []
        for keywords, label in [
            (["5h", "5 hour", "rolling", "5h rolling"], "5h Rolling"),
            (["weekly", "week", "周"], "每周"),
            (["monthly", "month", "月"], "每月"),
            (["daily", "day", "日"], "每日"),
        ]:
            pct = self._find_best_pct(text, keywords)
            reset = self._find_reset_near(text, keywords[0])
            if pct is not None and 0 <= pct <= 100:
                items.append(UsageItem(label=label, used_percent=pct,
                                       note=reset or ""))
        return items

    def _find_best_pct(self, text: str, keywords: list) -> float:
        """在文本中找到离关键词最近的百分比值。"""
        for kw in keywords:
            idx = text.lower().find(kw.lower())
            if idx < 0:
                continue
            # 在关键词前后 250 字符内收集所有百分比
            start = max(0, idx - 50)
            end = min(len(text), idx + 250)
            window = text[start:end]
            matches = []
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", window):
                val = float(m.group(1))
                if 0 <= val <= 100:
                    dist = abs(m.start() - (idx - start + len(kw)))
                    matches.append((dist, val))
            if matches:
                matches.sort()
                return matches[0][1]
        return None

    @staticmethod
    def _find_reset_near(html, keyword):
        """提取关键词附近的剩余/重置时间文字。"""
        idx = html.find(keyword)
        if idx < 0:
            return None
        patterns = [
            r"(?:reset|resets)\s*(?:in|after)?\s*([^<\"]{1,40})",
            r"(\d+)\s*(?:days?|d)\s*(?:\d+\s*)?(?:hours?|hrs?|h)",
            r"(\d+)\s*(?:hours?|hrs?|h)\s*(?:\d+\s*)?(?:minutes?|mins?|m)",
            r"(\d+)\s*(?:minutes?|mins?|m)\s*(?:remaining|left|to go)",
            r"(?:remaining|left|to go)\s*(\d+\s*(?:d|h|m|days?|hours?|minutes?))",
            r"(\d+\s*[dhm]\s*\d+\s*[hm])",
        ]
        ctx = html[idx: idx + 400]
        for pat in patterns:
            m = re.search(pat, ctx, re.IGNORECASE)
            if m:
                return m.group(0).strip()
        return None

    @staticmethod
    def _dump_debug(html):
        try:
            import os
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            with open(os.path.join(base, "token_view", "opencode_debug.html"), "w", encoding="utf-8") as f:
                f.write(html)
        except OSError:
            pass


class MimoProvider(BaseProvider):
    """小米 MiMo Token Plan。通过 CDP 调用 /api/v1/tokenPlan/usage 获取用量。

    返回格式：
    {
      "code": 0,
      "data": {
        "monthUsage": {"percent": 0.239, "items": [...]},
        "usage": {"percent": 0.24, "items": [...]}
      }
    }
    """

    API_PATH = "/api/v1/tokenPlan/usage"

    def fetch(self) -> UsageData:
        name = self.cfg.get("name") or "小米 MiMo"
        data = UsageData(provider_name=name, plan_level="Token Plan", fetched_at=time.time())

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        cookie = (self.cfg.get("cookie") or "").strip()
        if not cookie:
            data.status = "error"
            data.error = "未配置 cookie（请在设置里打开 CDP Chrome 并登录 platform.xiaomimimo.com）"
            return data
        headers = {"Cookie": cookie, "User-Agent": BROWSER_UA,
                   "Accept": "application/json", "Referer": "https://platform.xiaomimimo.com/"}
        try:
            r = requests.get("https://platform.xiaomimimo.com" + self.API_PATH, headers=headers, timeout=20)
        except requests.RequestException as e:
            data.status, data.error = "error", f"网络错误: {e}"
            return data
        if r.status_code >= 400:
            data.status = "error"
            data.error = f"HTTP {r.status_code}（cookie 可能已失效，请重新登录）"
            return data
        log(f"MiMo fetch: HTTP {r.status_code}, body={r.text[:200]}")
        return self._parse_json(r.text, data)

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        if not HAS_WS:
            data.status, data.error = "error", "缺少 websocket-client 依赖（pip install websocket-client）"
            return data

        cdp_url = (self.cfg.get("cdp_url") or "").strip()
        if not cdp_url:
            port = int(self.cfg.get("cdp_port") or 9222)
            cdp_url = f"http://127.0.0.1:{port}"

        try:
            r = requests.get(cdp_url.rstrip("/") + "/json", timeout=5)
            r.raise_for_status()
            targets = r.json()
        except requests.RequestException as e:
            data.status = "error"
            data.error = "请先在设置里启动 CDP Chrome 并登录 platform.xiaomimimo.com（连接失败）"
            log(f"MiMo CDP /json 连接失败: {e}")
            return data
        except ValueError as e:
            data.status = "error"
            data.error = "CDP 返回非 JSON，可能端口被占用"
            log(f"MiMo CDP /json 非 JSON: {e}")
            return data

        page = next(
            (t for t in targets
             if t.get("type") == "page" and "xiaomimimo.com" in (t.get("url") or "")),
            None,
        )
        if page is None:
            data.status = "error"
            data.error = "请在 CDP Chrome 里打开 platform.xiaomimimo.com 并登录"
            log(f"MiMo CDP 未发现 xiaomimimo.com 标签页，现有 target: "
                f"{[(t.get('type'), (t.get('url') or '')[:60]) for t in targets]}")
            return data

        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            data.status, data.error = "error", "CDP target 缺少 webSocketDebuggerUrl"
            return data

        js = (
            "(async()=>{"
            "const u=new URL(" + json.dumps(self.API_PATH) + ",location.origin);"
            "u.searchParams.set('_tv',Date.now());"
            "const r=await fetch(u.href,{"
            "credentials:'include',"
            "headers:{'Cache-Control':'no-cache','Pragma':'no-cache'},"
            "cache:'no-store'"
            "});"
            "return await r.text();})()"
        )

        try:
            ws = ws_connect(
                ws_url, timeout=30,
                origin="http://127.0.0.1:" + str(self.cfg.get("cdp_port") or 9222),
            )
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": js, "awaitPromise": True,
                           "returnByValue": True},
            }))
            raw = ws.recv()
            ws.close()
        except Exception as e:
            data.status = "error"
            data.error = f"CDP 通信失败: {e}"
            log(f"MiMo CDP WebSocket 异常: {e}")
            return data

        try:
            resp = json.loads(raw)
        except ValueError as e:
            data.status, data.error = "error", f"CDP 响应非 JSON: {e}"
            return data

        result = resp.get("result", {}).get("result", {})
        if result.get("type") == "undefined" or "value" not in result:
            exc = resp.get("result", {}).get("exceptionDetails")
            if exc:
                msg = (exc.get("exception", {}) or {}).get("description") or exc.get("text")
                data.status = "error"
                data.error = f"API 调用失败（登录可能已过期）: {msg}"
                log(f"MiMo CDP evaluate 异常: {msg}")
                return data
            data.status, data.error = "error", "CDP 返回 undefined"
            return data

        text = result.get("value") or ""
        log(f"MiMo CDP API 返回: {text[:300]}")
        if not text.strip():
            data.status, data.error = "error", "API 返回空（登录可能已失效）"
            return data

        return self._parse_json(text, data)

    def _parse_json(self, text: str, data: UsageData) -> UsageData:
        try:
            j = json.loads(text)
        except ValueError as e:
            data.status, data.error = "error", f"JSON 解析失败: {e}"
            return data

        if j.get("code") != 0:
            data.status = "error"
            data.error = j.get("message") or "API 返回错误"
            return data

        d = j.get("data") or {}

        # 只取月度额度（Plan 总额）
        month = d.get("monthUsage") or {}
        month_pct = month.get("percent")
        if month_pct is not None:
            pct = float(month_pct) * 100  # 0.239 -> 23.9
            items = month.get("items") or []
            note = ""
            if items:
                it = items[0]
                used = it.get("used", 0)
                limit = it.get("limit", 0)
                if limit > 0:
                    note = f"{fmt_tokens(used)} / {fmt_tokens(limit)} tokens"
            data.items.append(UsageItem("Monthly usage", pct, note=note))

        if not data.items:
            data.status = "empty"
            data.error = "未找到用量数据"

        log(f"MiMo 解析: status={data.status}, items={[(i.label, i.used_percent) for i in data.items]}")
        return data


def build(cfg: dict) -> BaseProvider:
    ptype = cfg.get("type")
    if ptype == "zhipu":
        return ZhipuProvider(cfg)
    if ptype == "opencode":
        return OpenCodeProvider(cfg)
    if ptype == "mimo":
        return MimoProvider(cfg)
    raise ValueError(f"未知 provider 类型: {ptype}")
