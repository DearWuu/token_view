# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

桌面悬浮窗，实时显示 coding plan 的 token 用量。PySide6 实现，支持**智谱 GLM Coding Plan（含团队版）**和 **OpenCode Go**。

## 运行

```
python -X utf8 main.py
```
Windows 控制台必须用 `-X utf8` 处理中文。依赖 PySide6（含 QtWebEngine）+ requests，本机已装。

## 架构（big picture）

**数据流（跨文件理解的关键）**：
`QTimer`(widget.py) → `request_refresh` signal → `RefreshWorker`(QObject，`moveToThread` 到子线程) → `providers.build(cfg).fetch()` → `results_ready` signal（回主线程）→ `FloatingWidget._on_results` → `UsageCard.set_items`。
> `RefreshWorker` 必须是 `QObject`，**不能是 `QWidget`**——Widget 不允许 moveToThread，否则崩溃。

**Provider 抽象**（providers.py）：`BaseProvider.fetch() -> UsageData`（含 `UsageItem[]`：label / used_percent / reset_at / note）。
- `ZhipuProvider`：**优先 CDP 模式**（推荐，唯一能拿到团队用量的方案）—— 连接用户已登录的调试 Chrome（`--remote-debugging-port=9222`），通过 CDP `Runtime.evaluate` 在 bigmodel.cn 页面上下文执行 `fetch('/api/monitor/usage/sub-account-rank', {credentials:'include'})`，结果走 `_parse_team`。若 `cdp_enabled=False` 回退到 ② Cookie + usage_url 模式（受反爬限制，已基本失效）③ API Key 模式（个人版，`/api/monitor/usage/quota/limit`）。智谱 Authorization **不加 `Bearer`** 前缀。
- `OpenCodeProvider`：无官方 API，cookie 抓 `/workspace/{id}/go` 页面 HTML 正则解析。

**登录抓取**（settings.py）：
- `ZhipuLoginDialog`：`QWebEngineView` 加载 bigmodel.cn，注入 `ZHIPU_HOOK_JS` 钩住页面 `fetch`/`XMLHttpRequest`，捕获含 `limits`/`percentage` 的响应（URL+body）；同时 `cookieStore.cookieAdded` 收集 cookie。抓到即调 `_save()` 自动保存。
- `LoginWebview`：OpenCode 登录，抓 auth cookie + workspace_id（从 URL 的 `wrk_` 提取）。

**配置 / 日志**：
- 配置 `%APPDATA%/token_view/config.json`（providers 列表、刷新间隔、透明度、窗口 geometry）
- 日志 `%APPDATA%/token_view/debug.log`（`logger.py`，调试 GUI 异步流程必看）
- 调试脚本 `probe.py` / `probe_team.py`（探测智谱接口）

## 关键坑（非显而易见，踩过）

- **PySide6 6.9 的 `QWebEngineCookieStore` 信号是 `cookieAdded`，不是 `cookieReceived`**（许多文档/示例写后者，直接 AttributeError）。slot 签名用 `_on_cookie(self, cookie, origin=None)` 兼容。
- **智谱团队版无公开用量 API**：团队版 API Key 调个人 monitor 接口返回 `"当前用户不存在coding plan"`。团队用量只能靠登录态 Cookie 抓取（控制台背后的 XHR）。
- **反爬根因（真相）**：不是 WAF/`acw_sc__v2`。后端 `sub-account-rank` 要求请求带 `Bigmodel-Organization` 和 `Bigmodel-Project` 两个 header（值在 localStorage 的 `Bigmodel-Organization`/`Bigmodel-Project` key 里，对应组织 id 和项目 id），缺这两个就返回空 `data:{}`。程序化请求漏了这俩，所以拿空。**CDP 方案核心**：`Runtime.evaluate` 在 bigmodel.cn 页面上下文执行 IIFE，从 `localStorage` 读这两个 header、从 `document.cookie` 读 `bigmodel_token_production` JWT 加 `Authorization` 头（裸 JWT，不加 Bearer），再 `fetch(..., {credentials:'include'})`。`settings.launch_cdp_chrome()` 用独立 `--user-data-dir=%APPDATA%/token_view/chrome_profile` + `--remote-allow-origins=*` 启动调试 Chrome，不污染用户主 Chrome。
- **CDP 优先级**：`ZhipuProvider.fetch` 中 `cdp_enabled=True`（默认）即走 CDP；目标取值要先从 `cdp_url/json` 找 `type=='page'` 且 url 含 `bigmodel.cn` 的 target 的 `webSocketDebuggerUrl`，再 `Runtime.evaluate` 执行 IIFE fetch。WebSocket 连接必须带 `origin: http://127.0.0.1:<port>` 头，否则 Chrome 返回 403 Forbidden。
- **WebEngine 必须在 GUI 线程**；cookie 抓取依赖用户登录态，无法离线/无头测试，必须真人登录。
- 悬浮窗：`FramelessWindowHint | WindowStaysOnTopHint | Tool` + `WA_TranslucentBackground`；`app.setQuitOnLastWindowClosed(False)` 配合托盘实现关窗不退出。
- `SettingsDialog` 操作 cfg 的**深拷贝副本** `self.work`，只有 `_save()` 才写回 `self.cfg` 并 `accept()`。
