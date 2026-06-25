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
    import websocket
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
        js = (
            "(async()=>{"
            "var m=/bigmodel_token_production=([^;]+)/.exec(document.cookie);"
            "var tok=m?m[1]:'';"
            "var h={'Accept':'application/json'};"
            "if(tok)h['Authorization']=tok;"
            "var org=localStorage.getItem('Bigmodel-Organization');"
            "var proj=localStorage.getItem('Bigmodel-Project');"
            "if(org)h['Bigmodel-Organization']=org;"
            "if(proj)h['Bigmodel-Project']=proj;"
            "const r=await fetch("
            f"'/api/monitor/usage/sub-account-rank?startTime={st}&endTime={et}"
            "&pageNum=1&pageSize=20&keyword='"
            ",{credentials:'include',headers:h});"
            "return await r.text();})()"
        )

        # 4) 连 WebSocket 发 Runtime.evaluate
        try:
            ws = websocket.create_connection(
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
    """OpenCode Go。无官方用量 API，用 auth cookie 抓 /workspace/{id}/go 页面解析。

    多层解析策略（按优先级）：
      1) 从页面 <script> 里提取 __NEXT_DATA__ 等 JSON 数据
      2) HTML 语义化提取（progress / SVG 环 / aria-label）
      3) 纯文本关键词 + 百分比正则（回退）
    """

    URL_FMT = "https://opencode.ai/workspace/{wsid}/go"

    # ---- 公开入口 ----
    def fetch(self) -> UsageData:
        wsid = (self.cfg.get("workspace_id") or "").strip()
        name = self.cfg.get("name") or "OpenCode Go"
        data = UsageData(provider_name=name, plan_level="Go", fetched_at=time.time())

        if not wsid:
            data.status = "error"
            data.error = "未配置 workspace_id"
            return data

        # 优先 CDP：连接用户已登录的调试 Chrome，在页面上下文 fetch HTML
        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        cookie = (self.cfg.get("cookie") or "").strip()
        if not cookie:
            data.status = "error"
            data.error = "未配置 cookie（请在设置里打开 CDP Chrome 并登录 opencode.ai）"
            return data
        if "=" not in cookie:
            cookie = f"auth={cookie}"
        url = self.URL_FMT.format(wsid=wsid)
        headers = {"Cookie": cookie, "User-Agent": BROWSER_UA,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                   "Accept-Language": "en-US,en;q=0.9", "Referer": "https://opencode.ai/"}
        try:
            r = requests.get(url, headers=headers, timeout=20)
        except requests.RequestException as e:
            data.status, data.error = "error", f"网络错误: {e}"
            return data
        if r.status_code >= 400:
            data.status = "error"
            data.error = f"HTTP {r.status_code}（cookie 可能已失效，请重新登录）"
            return data
        html = r.text
        log(f"OpenCode fetch: HTTP {r.status_code}, body={len(html)} 字符")
        items = self._parse_html(html)
        if items:
            data.items = items
        else:
            data.status = "empty"
            data.error = "无法解析页面（前端可能改版，请在设置里重新登录）"
            self._dump_debug(html)
        log(f"OpenCode 解析: status={data.status}, items={[(i.label, i.used_percent) for i in data.items]}")
        return data

    # ---- CDP 模式：连接已登录的调试 Chrome 抓页面 HTML ----
    CDP_TIMEOUT = 30

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        """通过 CDP 连接用户已登录的 Chrome，在 opencode.ai 页面上下文
        fetch('/workspace/{wsid}/go') 拿到 HTML，再用多层解析器提取用量。"""
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
            data.error = "请先在设置里启动 CDP Chrome 并登录 opencode.ai（连接失败）"
            log(f"OpenCode CDP /json 连接失败: {e}")
            return data
        except ValueError as e:
            data.status = "error"
            data.error = "CDP 返回非 JSON，可能端口被占用"
            log(f"OpenCode CDP /json 非 JSON: {e}")
            return data

        # 2) 找一个 url 含 opencode.ai 的 Page target
        page = next(
            (t for t in targets
             if t.get("type") == "page" and "opencode.ai" in (t.get("url") or "")),
            None,
        )
        if page is None:
            data.status = "error"
            data.error = "请在 CDP Chrome 里打开 opencode.ai 并登录"
            log(f"OpenCode CDP 未发现 opencode.ai 标签页，现有 target: "
                f"{[(t.get('type'), (t.get('url') or '')[:60]) for t in targets]}")
            return data

        ws_url = page.get("webSocketDebuggerUrl")
        if not ws_url:
            data.status, data.error = "error", "CDP target 缺少 webSocketDebuggerUrl"
            return data

        # 3) JS：从登录态页面 fetch workspace 的 HTML
        wsid = (self.cfg.get("workspace_id") or "").strip()
        ws_path = self.URL_FMT.format(wsid=wsid).replace("https://opencode.ai", "")
        js = (
            "(async()=>{"
            "const r=await fetch(" + json.dumps(ws_path) + ",{credentials:'include'});"
            "return await r.text();})()"
        )

        # 4) 连 WebSocket 发 Runtime.evaluate
        try:
            ws = websocket.create_connection(
                ws_url, timeout=self.CDP_TIMEOUT,
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
            log(f"OpenCode CDP WebSocket 异常: {e}")
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
                data.error = f"页面 fetch 失败（登录可能已过期）: {msg}"
                log(f"OpenCode CDP evaluate 异常: {msg}")
                return data
            data.status, data.error = "error", "CDP 返回 undefined"
            return data

        html = result.get("value") or ""
        log(f"OpenCode CDP fetch: HTML {len(html)} 字符")
        if not html.strip():
            data.status, data.error = "error", "CDP fetch 返回空（登录可能已失效）"
            return data

        items = self._parse_html(html)
        if items:
            data.items = items
        else:
            data.status = "empty"
            data.error = "无法解析页面（前端可能改版）"
            self._dump_debug(html)
        log(f"OpenCode CDP 解析: status={data.status}, items={[(i.label, i.used_percent) for i in data.items]}")
        return data
    def _parse_html(self, html: str) -> list:
        # 1) 从 __NEXT_DATA__ / 内嵌 JSON 里找用量数据
        items = self._parse_embedded_json(html)
        if items:
            log("OpenCode 策略1（内嵌JSON）命中")
            return items

        # 2) HTML 语义化提取：progress 元素、SVG 圆环、data-* 属性
        items = self._parse_semantic_html(html)
        if items:
            log("OpenCode 策略2（HTML语义化）命中")
            return items

        # 3) 纯文本关键词 + 百分比正则（回退）
        items = self._parse_text_fallback(html)
        if items:
            log("OpenCode 策略3（文本回退）命中")
        return items

    # ---- 策略 1：内嵌 JSON ----
    def _parse_embedded_json(self, html: str) -> list:
        """从 __NEXT_DATA__、<script id="__NEXT_DATA__"> 等标签里提取用量百分比。"""
        json_patterns = [
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            r'<script[^>]*id="__NEXT_DATA__"[^>]*/>',
        ]
        for pat in json_patterns:
            m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
            if not m or not m.group(1):
                continue
            try:
                blob = json.loads(m.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                continue
            items = self._scan_json(blob)
            if items:
                return items

        # window.__INITIAL_STATE__ / __DATA__ = {...}（需要括号匹配）
        for js_var in (r"window\.__INITIAL_STATE__", r"window\.__DATA__"):
            m = re.search(js_var + r"\s*=\s*", html)
            if m:
                js = self._extract_json_object(html, m.end())
                if js:
                    try:
                        blob = json.loads(js)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    items = self._scan_json(blob)
                    if items:
                        return items
        return []

    @staticmethod
    def _extract_json_object(text: str, start_pos: int) -> str:
        """从 text[start_pos:] 提取第一个完整的 JSON 对象 {...}，支持嵌套。"""
        i = start_pos
        while i < len(text) and text[i] != "{":
            i += 1
        if i >= len(text):
            return None
        brace_start = i
        depth = 0
        in_str = False
        escape = False
        while i < len(text):
            c = text[i]
            if escape:
                escape = False
            elif c == "\\" and in_str:
                escape = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return text[brace_start: i + 1]
            i += 1
        return None

    def _scan_json(self, obj, depth=0) -> list:
        """递归扫描 JSON，寻找含有 percentage/usage/token 等字段的结构。"""
        if depth > 20 or obj is None:
            return []
        if isinstance(obj, dict):
            pct = None
            # 检查是否是一个用量单元
            for k in ("percentage", "usedPercentage", "usagePercentage", "pct",
                      "used_percent", "usage_percent"):
                v = obj.get(k)
                if isinstance(v, (int, float)):
                    pct = float(v)
                    break
            if pct is not None:
                label = ""
                for k in ("label", "name", "title", "type", "window", "period"):
                    label = obj.get(k, "")
                    if isinstance(label, str) and label:
                        break
                reset = obj.get("resetAt") or obj.get("reset_at") or obj.get("resetIn") or ""
                reset_str = str(reset) if reset else ""
                return [UsageItem(label=label or "用量", used_percent=pct,
                                  note=reset_str if isinstance(reset_str, str) else "")]
            # 如果当前对象有多条用量数据（如 usage: {5h: {...}, weekly: {...}}）
            items = []
            for v in obj.values():
                items.extend(self._scan_json(v, depth + 1))
            return items
        if isinstance(obj, list):
            items = []
            for v in obj:
                items.extend(self._scan_json(v, depth + 1))
            return items
        return []

    # ---- 策略 2：HTML 语义化提取 ----
    def _parse_semantic_html(self, html: str) -> list:
        """从 progress 元素、SVG 圆环、aria-valuenow 等提取百分比。"""
        items = []

        # progress 元素：<progress value="45" max="100">
        for m in re.finditer(
            r'<progress[^>]*\bvalue\s*=\s*"(\d+(?:\.\d+)?)"[^>]*>',
            html, re.IGNORECASE,
        ):
            pct = float(m.group(1))
            ctx = self._surrounding_text(html, m.start(), 200)
            label = self._guess_label(ctx)
            items.append(UsageItem(label=label, used_percent=pct))
        if items:
            return items

        # SVG 环形进度条（stroke-dasharray/stroke-dashoffset）
        for m in re.finditer(
            r'stroke-dash(?:array|offset)\s*=\s*"([^"]+)"',
            html, re.IGNORECASE,
        ):
            ctx = self._surrounding_text(html, m.start(), 300)
            pct = self._svg_stroke_pct(m.group(1), ctx)
            if pct is not None:
                label = self._guess_label(ctx)
                items.append(UsageItem(label=label, used_percent=pct))
        if items:
            return items

        # aria-valuenow（常见于无障碍进度条）
        for m in re.finditer(
            r'aria-valuenow\s*=\s*"(\d+(?:\.\d+)?)"',
            html, re.IGNORECASE,
        ):
            pct = float(m.group(1))
            ctx = self._surrounding_text(html, m.start(), 200)
            label = self._guess_label(ctx)
            items.append(UsageItem(label=label, used_percent=pct))
        return items

    @staticmethod
    def _svg_stroke_pct(attr: str, ctx: str = "") -> float:
        """尝试从 SVG stroke-dasharray/offset 推导百分比。"""
        nums = re.findall(r"(\d+(?:\.\d+)?)", attr)
        if len(nums) >= 2:
            try:
                dash, total = float(nums[0]), float(nums[1])
                if total > 0:
                    return round((dash / total) * 100, 1)
            except (ValueError, ZeroDivisionError):
                pass
        if nums:
            # 尝试从上下文找百分比文字
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", ctx)
            if m:
                return float(m.group(1))
        return None

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


def build(cfg: dict) -> BaseProvider:
    ptype = cfg.get("type")
    if ptype == "zhipu":
        return ZhipuProvider(cfg)
    if ptype == "opencode":
        return OpenCodeProvider(cfg)
    raise ValueError(f"未知 provider 类型: {ptype}")
