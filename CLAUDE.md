# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

桌面悬浮窗，实时显示 coding plan 的 token 用量。**pywebview** 实现（HTML/CSS/JS +
Python 桥），支持**智谱 GLM Coding Plan（含团队版）**、**OpenCode Go**、**小米 MiMo**。
行业类似项目（Headroom / claude-statusbar / claude-monitor）均不用 Qt，证明这条
路线是对的。

## 运行

```
python -X utf8 main.py
```
Windows 控制台必须用 `-X utf8` 处理中文。依赖 pywebview + requests + websocket-client，
本机已装。

## 架构（big picture）

**渲染层**：pywebview 把 `web/index.html` 跑在 WebView2（Windows）/WKWebView（macOS）/
WebKitGTK（Linux）里。CSS 处理圆角/阴影/暗色/DPI，JS 调 `window.pywebview.api.xxx`
拿数据。

**桥层**：`api/core.py:Api` 注入到 `webview.create_window(js_api=...)`。Api 是无下划线
公开方法的薄门面，内部把职责分到 `api/{chrome, screen, window, providers, state, settings}`。

**数据层**：`providers/{base,zhipu,opencode,mimo,cdp}` 各自 fetch → `UsageData`。
`BaseProvider.fetch() -> UsageData`（含 `UsageItem[]`：label / used_percent / reset_at / note）。

- `ZhipuProvider`：**优先 CDP 模式**（推荐，唯一能拿到团队用量的方案）—— 连接用户
  已登录的调试 Chrome（`--remote-debugging-port=9222`），通过 `CDPHarness` 在
  bigmodel.cn 页面上下文执行 `fetch('/api/monitor/usage/sub-account-rank', {credentials:'include'})`，
  结果走 `_parse_team`。若 `cdp_enabled=False` 回退到 ② Cookie + usage_url 模式
  （受反爬限制，已基本失效）③ API Key 模式（个人版）。智谱 Authorization **不加 `Bearer`** 前缀。
- `OpenCodeProvider`：无官方 API，CDP 抓 workspace 页面 fetch + DOM 兜底。
- `MimoProvider`：CDP 调 `/api/v1/tokenPlan/usage`。

**配置 / 日志**：
- 配置 `%APPDATA%/token_view/config.json`（pathlib + 原子写）
- 日志 `%APPDATA%/token_view/debug.log`（`logger.py`，调试 GUI 异步流程必看）
- 状态文件 `%APPDATA%/token_view/state.json`（Headroom 风格，schema=1，给 companion 工具消费）

## 关键坑（非显而易见，踩过）

- **pywebview 跨平台**：Windows 上 `transparent=True` 在高 DPI 下会被 WebView2 裁切，
  改成 `transparent=not is_windows`，CSS 用 `body.no-transparent` 兜底非透明背景。
- **DPI 缩放**：JS 传来的尺寸是 CSS 逻辑像素，Win32 `SetWindowPos` 要物理像素，
  用 `api.screen.windows_dpi_scale()` 换算。`api.window._resize_windows` 是参考实现。
- **macOS 主线程**：Cocoa `NSWindow.setFrame_` 必须在主线程，用
  `api.screen.run_on_macos_main_thread` 同步等。`quit_app` 走 `os._exit(0)`
  否则 Cocoa 事件循环不会退。
- **智谱团队版无公开用量 API**：API Key 调个人 monitor 接口返回 `"当前用户不存在coding plan"`。
  团队用量只能靠登录态 Cookie 抓取（控制台背后的 XHR）。
- **反爬根因（真相）**：不是 WAF/`acw_sc__v2`。后端 `sub-account-rank` 要求请求带
  `Bigmodel-Organization` 和 `Bigmodel-Project` 两个 header（值在 localStorage 的
  `Bigmodel-Organization`/`Bigmodel-Project` key 里，对应组织 id 和项目 id），
  缺这两个就返回空 `data:{}`。程序化请求漏了这俩，所以拿空。
  **CDP 方案核心**：`Runtime.evaluate` 在 bigmodel.cn 页面上下文执行 IIFE，从
  `localStorage` 读这两个 header、从 `document.cookie` 读 `bigmodel_token_production`
  JWT 加 `Authorization` 头（裸 JWT，不加 Bearer），再 `fetch(..., {credentials:'include'})`。
  `api.chrome.launch_cdp_chrome()` 用独立 `--user-data-dir=%APPDATA%/token_view/chrome_profile`
  + `--remote-allow-origins=*` 启动调试 Chrome，不污染用户主 Chrome。
- **CDP 优先级**：`ZhipuProvider.fetch` 中 `cdp_enabled=True`（默认）即走 CDP；目标
  取值要先从 `cdp_url/json` 找 `type=='page'` 且 url 含 `bigmodel.cn` 的 target 的
  `webSocketDebuggerUrl`，再 `Runtime.evaluate` 执行 IIFE fetch。WebSocket 连接
  必须带 `origin: http://127.0.0.1:<port>` 头，否则 Chrome 返回 403 Forbidden
  （`api.cdp.CDPHarness` 自动处理）。
- **WebView 必须在 GUI 线程**；cookie 抓取依赖用户登录态，无法离线/无头测试，
  必须真人登录。
- **pywebview 文档陷阱**：部分 `Screen` 对象在不同版本是 dict/对象两种形态，
  `api.screen.screen_value()` 兼容两种。
- **pywebview WinForms 序列化**：Windows 上 pywebview 的 `get_functions` 会递归遍历
  `api.window.native`（.NET WinForms Form），导致 COM 无限递归崩溃。`main.py`
  标记 `window._serializable = False` 避开。

## 状态文件协议（Headroom 风格）

任何想消费用量数据的工具（菜单栏/状态栏 hook/IDE 插件/手机端推送）**读
`%APPDATA%/token_view/state.json` 即可**，不要自己重抓 CDP。Schema：
- `schema: 1`
- `ts: <unix timestamp>`
- `ts_iso: <ISO 8601>`
- `providers: [{id, name, type, status, error, level, fetched_at, items: [{label, percent, reset_at, note}]}]`

前端通过 `Api.collect_and_persist()` 触发一次 fetch + 落盘；其他 consumer
直接 `import json; json.load(open(state_file_path()))` 即可。

## 新增 Provider

1. 在 `providers/` 下加 `<name>.py`，继承 `BaseProvider`，实现 `fetch() -> UsageData`
2. 走 CDP 的话用 `CDPHarness(port, page_keyword=...)` 复用 `/json` + `Runtime.evaluate`
3. `providers/__init__.py` 的 `build()` 加 `if ptype == "..."` 分支
4. `config.new_provider("...")` 加默认值
