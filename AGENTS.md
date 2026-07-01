# AGENTS.md

OpenCode/agent 操作本仓库的精简备忘。深度架构与历史坑位见 `CLAUDE.md`（权威），
这里只补其未覆盖或易踩漏的 executable 事实。

## 运行

- 启动：`python -X utf8 main.py`（Windows 控制台必须加 `-X utf8`，否则中文乱码）
- 依赖：`pywebview` / `requests` / `websocket-client` / `pillow` / `pystray`（requirements.txt）。
  本机已装；缺则跑 `pip install -r requirements.txt`
- 测试：`pytest tests/`（27 个用例，仅核心数据/状态协议，无 GUI）
- 打包：`pyinstaller TokenView.spec --clean --noconfirm` → `dist/TokenView.exe`（22MB）
- 验证：跑 `main.py` 看效果 + 看 `%APPDATA%/token_view/debug.log`

## 目录结构

```
.
├── main.py                    # pywebview 入口：创建窗口 + 启动
├── config.py                  # 配置 JSON 读写（pathlib + 原子写）
├── logger.py                  # 统一日志到 %APPDATA%/token_view/debug.log
├── requirements.txt
├── TokenView.spec             # PyInstaller 打包配置
│
├── providers/                 # 用量数据源（每个 provider 独立文件）
│   ├── base.py                #   BaseProvider / UsageData / UsageItem / fmt_tokens
│   ├── cdp.py                 #   CDPHarness + CDP 异常类（三个 provider 共用）
│   ├── zhipu.py               #   智谱 GLM（CDP/cookie/API key 三模式）
│   ├── opencode.py            #   OpenCode Go（CDP 模式）
│   ├── mimo.py                #   小米 MiMo（CDP 模式）
│   └── __init__.py            #   build() 工厂 + 重导出
│
├── api/                       # pywebview js_api 桥层
│   ├── core.py                #   Api 类（编排下面所有模块）
│   ├── chrome.py              #   Chrome 查找 + CDP Chrome 启动
│   ├── screen.py              #   跨平台屏幕工作区 / Win32 HWND / DPI
│   ├── window.py              #   窗口几何（move/resize/置顶/auto-hide）
│   ├── providers.py           #   Provider JSON 配置 CRUD
│   ├── state.py               #   state.json 协议（Headroom 风格）
│   ├── settings.py            #   设置窗口 + 模式/刷新间隔
│   └── __init__.py            #   暴露 Api 类
│
├── web/                       # 前端（HTML/CSS/JS）
│   ├── index.html             #   主面板（8 方向 resize handles）
│   ├── app.js                 #   主逻辑：刷新 / 模式 / resize / auto-hide dock / 假透明度
│   ├── style.css              #   主题 + 顶部模式 + 假透明度 CSS 变量
│   └── settings.html          #   设置页
│
├── tests/                     # pytest 单元测试（27 个用例）
│   ├── conftest.py
│   ├── test_base.py           # 数据模型 + 工厂
│   ├── test_state.py          # state.json 协议
│   └── test_config.py         # 配置读写
│
├── docs/                      # 文档
│   ├── architecture.md        # 架构图 + 数据流
│   └── archive/               # 历史任务交接文档（不再反映当前实现）
│       ├── PROMPT-cdp-攻坚.md
│       └── README.md
│
├── README.md
├── AGENTS.md                  # 本文件
└── CLAUDE.md                  # 深度架构与坑位
```

## 配置 & 日志位置（不在仓库内，易找错）

- 配置：`%APPDATA%/token_view/config.json`
- 日志：`%APPDATA%/token_view/debug.log`（CDP 各步骤都写这里，排错第一手）
- 状态文件：`%APPDATA%/token_view/state.json`（Headroom/claude-statusbar 风格，
  供其他工具读，**不依赖** pywebview；schema=1）
- CDP Chrome 独立 profile：`%APPDATA%/token_view/chrome_profile`
- OpenCode 调试 dump：`%APPDATA%/token_view/opencode_debug.html`

## 智谱团队用量唯一可用路径：CDP

其余三条（cookie+auth / curl_cffi / QWebEngine 注入）都已验证返回空 `data:{}`，
**不要再尝试**。

完整接入流程必须四件套（缺一即失败）：
1. `api.chrome.launch_cdp_chrome()` 启动 Chrome 时**必须**带 `--remote-allow-origins=*`，
   否则 CDP WebSocket 403 Forbidden
2. `Runtime.evaluate` 执行的 IIFE 必须从 `localStorage` 读 `Bigmodel-Organization` /
   `Bigmodel-Project` 加进 fetch headers（这是反爬根因，不是 WAF/`acw_sc__v2`）
3. 从 `document.cookie` 读 `bigmodel_token_production` 加裸 `Authorization` 头
   （**不加 `Bearer`**），`credentials:'include'` 还要带上其余 cookie
4. `websocket.create_connection` 必须传 `origin="http://127.0.0.1:<port>"` 头，
   否则 403（CDPHarness 自动处理）

解析逻辑（`providers.zhipu.ZhipuProvider._parse_team`）已对，别改：
`data.rankList[]` 按 `customerId==cfg.customer_id` 匹配，取不到取第一名；
读 `rateLimitStatus` 的 `fiveHourPercentage` / `weekPercentage` / `mcpPercentage`。

## CDP 抽到 `providers/cdp.py` 后

三个 provider 共享 `CDPHarness`（`find_page` + `evaluate`），**不要**在 provider
内部再写一遍 `requests.get(/json)` + `ws_connect`。新增 provider 步骤：
1. 继承 `BaseProvider`
2. 实例化 `CDPHarness(port, page_keyword=你的域名)`
3. `harness.find_page()` 拿 target
4. 拼 JS 字符串，`harness.evaluate(ws_url, js, await_promise=True)` 拿 result
5. 解析 `result.get("value")` 得到 string 响应

## UI 关键约束

- 窗口 flags：`frameless=True` + `on_top=True`（pywebview 参数）
- Windows 下**禁用透明**（`transparent=not is_windows`）—— WebView2 高 DPI 透明窗口
  容易裁切，靠非透明背景色 (`body.no-transparent` CSS 类) 兜底
- macOS 上 quit 后 Cocoa 事件循环不会自动退，必须 `os._exit(0)`（在 `api/core.py`）
- **不要回头用 PySide6**——这是当初迁到 pywebview 的核心理由
- pywebview 启动时 `console=False` 才是窗口模式（PyInstaller spec 已设）

## pywebview vs Qt 选型背景

调研过 4 个行业标杆（claude-monitor / Headroom / claude-statusbar / claude-monitor-cyd），
**没有**用 Qt 的。pywebview 把"悬浮卡片 + 高 DPI + 圆角透明 + 跨平台 + 实时刷新"
这一组硬骨头外包给 WebView2 / WKWebView，是这个细分赛道的行业标准。

## 配置字段（zhipu，`config.new_provider`）

`cdp_enabled`（默认 True）/ `cdp_port`（9222）/ `cdp_url` / `api_key`（个人版）/
`cookie` / `usage_url` / `auth_token` / `customer_id`。
CDP 启用时 `cookie`/`usage_url`/`auth_token` 可留空，团队用量不再依赖它们。

## 状态文件协议（Headroom 风格）

`api.state.write_state(providers_data)` 原子写到 `%APPDATA%/token_view/state.json`。
结构：
```json
{
  "schema": 1,
  "ts": 1719850000.12,
  "ts_iso": "2024-07-01T12:00:00+00:00",
  "providers": [
    {"id":"...","name":"...","type":"zhipu","status":"ok","error":"",
     "level":"团队·张三","fetched_at":...,
     "items":[{"label":"5h 窗口","percent":12.5,"reset_at":...,"note":""}]}
  ]
}
```

任何想消费用量数据的工具（菜单栏/状态栏 hook/IDE 插件/手机端推送）**读这个 JSON 即可**，
不要自己重抓 CDP。前端用 `Api.collect_and_persist()` 触发一次并把结果回传。

## auto-hide dock（顶部模式）

QQ 风格：启用顶部模式后，鼠标离开窗口 200ms 自动滑出（露 4px 缝），
鼠标回顶部 4px 区域窗口滑下显示。实现：
- 前端 `setupDockAutoHide`（web/app.js）：监听 mouseleave/mouseenter/mousemove
- mouseleave 走 200ms 延迟（避免 WebView2 改 y 后重派 mouseenter/mouseleave 死循环）
- mousemove 用 `e.screenY < 4`（**不是** `e.clientY`）判断"接近屏幕顶部"
- 后端 `api.set_dock_hidden(true/false)` 调 Win32 SetWindowPos 物理移动 y
- `fitWindowOnce` 完重设 dock hidden（fit 后 h 变了 y 算错）

## 8 方向 resize handle

`web/index.html` 4 边 + 3 角 nw/ne/sw + 1 grip（id="resize-grip"）= 8 个。
实现 `web/app.js:startResize` + `onResizeMove`，调两个 API：
- `api.resize_window_to_content(w, h)`：改大小
- `api.move_window(x, y)`：拖左/上边时改位置

`web/style.css` 中 `.resize-edge` / `.resize-grip` 都设 `-webkit-app-region: no-drag`
跳出 body 的 drag region。`body.dock-mode` 时全部 `display: none`（窗口被屏幕顶部挡着时不让 resize）。

## 假透明度

`api.set_opacity` 不调 Win32 `Form.Opacity`（整窗 alpha 把文字/进度条一起淡化），
改用 CSS 假透明度：背景 `rgba(20, 20, 24, var(--opacity-primary))`，前端
`applyWindowOpacity(alpha)` 改 `--opacity-primary` 变量。文字/进度条颜色不变，
**只有**背景透。设置页拖透明度滑块时 `api.set_opacity` 内部 evaluate_js
调主窗口 `applyWindowOpacity`，实时反映。

## 改代码注意

- 全程中文：代码注释、日志、用户提示、commit message 均用中文
- 优先编辑现有文件，不过度抽象，不加无谓新文件
- WebView 必须在 GUI 线程；登录抓取依赖真人登录态，无法离线/无头测试
- Win32 SetWindowPos 物理坐标用 `screen_helper.windows_dpi_scale` 换算（CSS 逻辑 → 物理）
- **不要**用 `e.clientY` 判断"鼠标接近屏幕顶部"——WebView 内部坐标会乱；
  **用** `e.screenY`（屏幕绝对坐标）
