"""CDP（Chrome DevTools Protocol）通讯封装。

三个 provider（zhipu / opencode / mimo）都需要：
  1. 通过 /json 找到特定的 page target
  2. 用 WebSocket 连上去发 Runtime.evaluate

把这段共性抽出来，避免重复。

注意：调用方必须在 GUI 线程之外使用本模块的阻塞式 WebSocket 操作。
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

from .base import HAS_WS, ws_connect


class CDPError(Exception):
    """CDP 通讯过程中所有可预期错误的统一基类。"""


class CDPNotConnected(CDPError):
    """CDP Chrome 没启动或端口不通。"""


class CDPPageNotFound(CDPError):
    """找到了 CDP Chrome 但没看到目标页面。"""


class CDPEvalError(CDPError):
    """Runtime.evaluate 执行本身失败（页面 fetch 报错、登录过期等）。"""


class CDPHarness:
    """单次 CDP 调用的薄封装。

    用法：
        harness = CDPHarness(port=9222, page_keyword="bigmodel.cn")
        page = harness.find_page()
        text = harness.evaluate(harness, js_source, await_promise=True)
    """

    DEFAULT_TIMEOUT = 15

    def __init__(
        self,
        port: int = 9222,
        page_keyword: str = "",
        cdp_url: str = "",
        eval_timeout: int = DEFAULT_TIMEOUT,
    ):
        # cdp_url 优先，否则用 port 拼 127.0.0.1
        if cdp_url:
            self._cdp_url = cdp_url.rstrip("/")
        else:
            self._cdp_url = f"http://127.0.0.1:{port}"
        self._port = port
        self._keyword = page_keyword
        self._eval_timeout = eval_timeout

    @property
    def origin(self) -> str:
        """WebSocket 连接必须带的 Origin 头值。"""
        return f"http://127.0.0.1:{self._port}"

    def find_page(self) -> dict:
        """GET /json 找到 type=='page' 且 url 含 self._keyword 的 target。

        抛出 CDPNotConnected / CDPPageNotFound。
        """
        try:
            r = requests.get(self._cdp_url + "/json", timeout=5)
            r.raise_for_status()
            targets = r.json()
        except requests.RequestException as e:
            raise CDPNotConnected(f"无法连接 {self._cdp_url}/json: {e}") from e
        except ValueError as e:
            raise CDPNotConnected(f"CDP 返回非 JSON（端口可能被占用）: {e}") from e

        page = next(
            (t for t in targets
             if t.get("type") == "page"
             and self._keyword in (t.get("url") or "")),
            None,
        )
        if page is None:
            raise CDPPageNotFound(
                f"未找到含 '{self._keyword}' 的标签页"
            )
        if not page.get("webSocketDebuggerUrl"):
            raise CDPNotConnected("CDP target 缺少 webSocketDebuggerUrl")
        return page

    def evaluate(self, ws_url: str, expression: str,
                 await_promise: bool = True, timeout: Optional[int] = None
                 ) -> dict:
        """在指定 target 上执行 Runtime.evaluate，返回原始 CDP 响应。

        抛出 CDPNotConnected / CDPEvalError。
        """
        if not HAS_WS:
            raise CDPNotConnected("缺少 websocket-client 依赖（pip install websocket-client）")

        try:
            ws = ws_connect(
                ws_url,
                timeout=timeout or self._eval_timeout,
                origin=self.origin,
            )
        except Exception as e:  # noqa: BLE001
            raise CDPNotConnected(f"WebSocket 连接失败: {e}") from e

        try:
            ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "awaitPromise": await_promise,
                    "returnByValue": True,
                },
            }))
            raw = ws.recv()
        except Exception as e:  # noqa: BLE001
            raise CDPNotConnected(f"WebSocket 通讯失败: {e}") from e
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        try:
            resp = json.loads(raw)
        except ValueError as e:
            raise CDPEvalError(f"CDP 响应非 JSON: {e}") from e

        result = resp.get("result", {}).get("result", {})
        if result.get("type") == "undefined" or "value" not in result:
            exc = (resp.get("result", {}) or {}).get("exceptionDetails")
            if exc:
                msg = (exc.get("exception", {}) or {}).get("description") or exc.get("text")
                raise CDPEvalError(f"页面内执行失败: {msg}")
            raise CDPEvalError("CDP 返回 undefined")
        return result

    def page_reload(self, ws_url: str, ignore_cache: bool = True,
                    wait_load: bool = True, settle: float = 1.0,
                    timeout: Optional[int] = None) -> None:
        """用 CDP Page.reload 重新加载页面，等加载完 + SPA 水合后返回。

        用于 OpenCode 等 SPA 页面：直接读 DOM 只有旧数据，
        reload 后才能拿到最新渲染结果。

        抛出 CDPNotConnected / CDPEvalError。
        """
        if not HAS_WS:
            raise CDPNotConnected("缺少 websocket-client 依赖")

        t = timeout or self._eval_timeout
        try:
            ws = ws_connect(ws_url, timeout=t, origin=self.origin)
        except Exception as e:  # noqa: BLE001
            raise CDPNotConnected(f"WebSocket 连接失败: {e}") from e

        try:
            # 1) 启用 Page 域
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            self._recv_until_id(ws, 1, timeout=5)

            # 2) 重新加载页面（忽略缓存）
            ws.send(json.dumps({
                "id": 2,
                "method": "Page.reload",
                "params": {"ignoreCache": ignore_cache},
            }))

            # 3) 等待 Page.loadEventFired 事件
            if wait_load:
                deadline = time.time() + t
                loaded = False
                while time.time() < deadline:
                    try:
                        raw = ws.recv()
                        msg = json.loads(raw)
                        if msg.get("method") == "Page.loadEventFired":
                            loaded = True
                            break
                    except Exception:
                        break
                if not loaded:
                    raise CDPEvalError("页面重新加载超时")

            # 4) 等待 SPA 水合完成
            if settle > 0:
                time.sleep(settle)
        except CDPError:
            raise
        except Exception as e:  # noqa: BLE001
            raise CDPNotConnected(f"Page.reload 通讯失败: {e}") from e
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _recv_until_id(ws, msg_id: int, timeout: float = 5) -> str:
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
        raise CDPEvalError(f"等待 CDP id={msg_id} 响应超时")
