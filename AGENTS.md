# AGENTS.md

OpenCode/agent 操作本仓库的精简备忘。深度架构与坑位见 `CLAUDE.md`（权威），这里只补其未覆盖或易踩漏的 executable 事实。

## 运行

- 启动：`python -X utf8 main.py`（Windows 控制台必须加 `-X utf8`，否则中文乱码）
- 依赖：`PySide6`（含 QtWebEngine）、`requests`、`websocket-client`。本机已装；若缺跑 `pip install websocket-client`
- 无 tests / lint / typecheck / build 配置 —— 不要伪造命令。验证手段只有「跑起来看 debug.log」

## 配置 & 日志位置（不在仓库内，易找错）

- 配置：`%APPDATA%/token_view/config.json`
- 日志：`%APPDATA%/token_view/debug.log`（CDP 各步骤都写这里，排错第一手）
- CDP Chrome 独立 profile：`%APPDATA%/token_view/chrome_profile`
- OpenCode 调试 dump：`%APPDATA%/token_view/opencode_debug.html`

## 智谱团队用量唯一可用路径：CDP

其余三条（cookie+auth / curl_cffi / QWebEngine 注入）都已验证返回空 `data:{}`，**不要再尝试**。

完整接入流程必须三件套（缺一即失败）：
1. `settings.launch_cdp_chrome()` 启动 Chrome 时**必须**带 `--remote-allow-origins=*`，否则 CDP WebSocket 403 Forbidden
2. `Runtime.evaluate` 执行的 IIFE 必须从 `localStorage` 读 `Bigmodel-Organization` / `Bigmodel-Project` 加进 fetch headers（这是反爬根因，不是 WAF/`acw_sc__v2`）
3. 从 `document.cookie` 读 `bigmodel_token_production` 加裸 `Authorization` 头（**不加 `Bearer`**），`credentials:'include'` 还要带上其余 cookie
4. `websocket.create_connection` 必须传 `origin="http://127.0.0.1:<port>"`头，否则 403

解析逻辑（`_parse_team`）已对，别改：`data.rankList[]` 按 `customerId==cfg.customer_id` 匹配，取不到取第一名；读 `rateLimitStatus` 的 `fiveHourPercentage` / `weekPercentage` / `mcpPercentage`。

## UI 关键约束（widget.py）

- `RefreshWorker` **必须是 `QObject`，不能是 `QWidget`** —— Widget 不允许 `moveToThread`，否则崩溃
- `UsageCard._clear_body()` 清 body 时，dock 模式下 body 里是 `QHBoxLayout` 而非 widget，必须 `takeAt` 同时处理子 widget 和子 layout，否则上次内容残留 → 同一数据重复渲染十几次
- dock（顶部）模式：`cfg["dock"]=True`，每张卡片整行扁平显示「服务商名 + 各用量项 + 进度条 + 百分比」；此时 `set_items` 后**不要 `adjustSize()`**，否则会把顶部条压扁
- 普通模式 resize 时 `_apply_scale()` 按宽度线性缩放字号/进度条/徽标；dock 模式 scale 用 `base=900`
- 配置已支持 `compact`（紧凑）与 `dock`（顶部）两个布尔，`geometry` 持久化为 `[x,y,w,h]`
- `SettingsDialog` 操作 `cfg` 的**深拷贝副本** `self.work`，只有 `_save()` 才写回并 `accept()`

## 配置字段（zhipu，`config.new_provider`）

`cdp_enabled`（默认 True）/ `cdp_port`（9222）/ `cdp_url` / `api_key`（个人版）/ `cookie` / `usage_url` / `auth_token` / `customer_id`。
CDP 启用时 `cookie`/`usage_url`/`auth_token` 可留空，团队用量不再依赖它们。

## 改代码注意

- 全程中文：代码注释、日志、用户提示、commit message 均用中文
- 优先编辑现有文件，不过度抽象，不加无谓新文件
- `QWebEngineCookieStore` 信号是 `cookieAdded`（**不是** `cookieReceived`），slot 签名 `_on_cookie(self, cookie, origin=None)`
- WebEngine 必须在 GUI 线程；登录抓取依赖真人登录态，无法离线/无头测试
- 窗口 flags：`FramelessWindowHint | Tool | WindowStaysOnTopHint` + `WA_TranslucentBackground`；`app.setQuitOnLastWindowClosed(False)` 配合托盘实现关窗不退