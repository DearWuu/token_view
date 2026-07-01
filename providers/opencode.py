"""OpenCode Go Provider。

无官方 API，通过 CDP 在已登录页面上下文抓取 workspace 用量。

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

from .base import BaseProvider, UsageData, UsageItem
from .cdp import CDPHarness, CDPError


class OpenCodeProvider(BaseProvider):
    """OpenCode Go 用量。"""

    URL_FMT = "https://opencode.ai/workspace/{wsid}/go"

    def fetch(self) -> UsageData:
        wsid = (self.cfg.get("workspace_id") or "").strip()
        name = self.cfg.get("name") or "OpenCode"
        data = UsageData(
            provider_name=name, plan_level="Go", fetched_at=time.time())

        if self.cfg.get("cdp_enabled", True):
            return self._fetch_cdp(data)

        if not wsid:
            return self._err(data, "未配置 workspace_id")
        return self._err(data, "请启用 CDP 模式")

    def _fetch_cdp(self, data: UsageData) -> UsageData:
        port = int(self.cfg.get("cdp_port") or 9222)
        harness = CDPHarness(
            port=port,
            page_keyword="opencode.ai",
            cdp_url=self.cfg.get("cdp_url") or "",
            eval_timeout=15,
        )

        try:
            page = harness.find_page()
        except CDPError as e:
            return self._translate_err(data, e)

        wsid = (self.cfg.get("workspace_id") or "").strip()
        if not wsid:
            # 从页面 URL 自动提取
            m = re.search(r"(wrk_[A-Z0-9]+)", page.get("url") or "")
            if m:
                wsid = m.group(1)
            else:
                return self._err(data, "未配置 workspace_id，且无法从页面 URL 自动获取")

        # 主动 fetch 同源 URL；找不到再回退读 DOM
        js = (
            "(async()=>{"
            "const out={source:'none',url:'',status:0,body:'',dom:null,errors:[]};"
            "const hasUsage=t=>/rollingUsage|weeklyUsage|monthlyUsage|usagePercent/i.test(t||'');"
            "const sameOrigin=u=>{try{return new URL(u,location.href).origin===location.origin}catch(e){return false}};"
            "const addTs=u=>{const x=new URL(u,location.href);x.searchParams.set('_tv',Date.now());return x.href;};"
            "const readDom=()=>{"
            "const texts=document.body.innerText||'';"
            "const results={};"
            "const rollingMatch=texts.match(/5[Hh].*?(\\d+(?:\\.\\d+)?)\\s*%/);"
            "if(rollingMatch)results.rolling=parseFloat(rollingMatch[1]);"
            "const weeklyMatch=texts.match(/[Ww]eekly.*?(\\d+(?:\\.\\d+)?)\\s*%|周.*?(\\d+(?:\\.\\d+)?)\\s*%/);"
            "if(weeklyMatch)results.weekly=parseFloat(weeklyMatch[1]||weeklyMatch[2]);"
            "const monthlyMatch=texts.match(/[Mm]onthly.*?(\\d+(?:\\.\\d+)?)\\s*%|月.*?(\\d+(?:\\.\\d+)?)\\s*%/);"
            "if(monthlyMatch)results.monthly=parseFloat(monthlyMatch[1]||monthlyMatch[2]);"
            "results.allPcts=[...texts.matchAll(/(\\d+(?:\\.\\d)?)\\s*%/g)].map(m=>parseFloat(m[1]));"
            "return results;"
            "};"
            "let urls=[];"
            "try{urls=performance.getEntriesByType('resource').map(e=>e.name)"
            ".filter(sameOrigin).filter(u=>/(usage|__server|workspace|api|go|trpc|rpc)/i.test(u));}catch(e){}"
            f"urls.unshift(new URL('/workspace/'+{json.dumps(wsid)}+'/go',location.origin).href);"
            "urls.unshift(location.href);"
            "urls=[...new Set(urls)];"
            "for(const u of urls){"
            "try{"
            "const r=await fetch(addTs(u),{credentials:'include',cache:'no-store',"
            "headers:{'Accept':'application/json, text/plain, */*','Cache-Control':'no-cache','Pragma':'no-cache'}});"
            "const t=await r.text();"
            "if(hasUsage(t)){out.source='network';out.url=u;out.status=r.status;out.body=t.slice(0,200000);out.dom=readDom();return JSON.stringify(out);}"
            "out.errors.push({url:u,status:r.status,len:t.length});"
            "}catch(e){out.errors.push({url:u,error:String(e).slice(0,120)});}"
            "}"
            "out.source='dom';out.dom=readDom();return JSON.stringify(out);"
            "})()"
        )

        try:
            result = harness.evaluate(
                page["webSocketDebuggerUrl"], js, await_promise=True)
        except CDPError as e:
            return self._translate_err(data, e)

        text = result.get("value") or ""
        if not text.strip():
            return self._err(data, "API 返回空")
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
