# AGENTS.md

桌面悬浮窗，实时显示 coding plan 的 token 用量。**pywebview** 实现（HTML/CSS/JS +
Python 桥），支持**智谱 GLM Coding Plan（含团队版）**、**OpenCode Go**、**Kimi**、
**小米 MiMo**、**火山 Ark**。行业类似项目（Headroom / claude-statusbar /
claude-monitor）均不用 Qt，证明这条路线是对的。

**数据抓取不依赖浏览器常开**：各 provider 首次从调试 Chrome **一次性提取凭证**
（设置页「提取凭证」按钮），之后日常刷新走纯 HTTP 直连（已实测无反爬，见下文）。
CDP 仅用于凭证提取和直连失败时的兜底。

## 运行

- 启动：`python -X utf8 main.py`（Windows 控制台必须加 `-X utf8`，否则中文乱码）
- 依赖：`pywebview` / `requests` / `websocket-client` / `pillow` / `pystray`（requirements.txt）。
  本机已装；缺则跑 `pip install -r requirements.txt`
- 测试：`pytest tests/`（14 个用例，凭证判定/fetch 路由/解析，无 GUI）
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
└── AGENTS.md                  # 本文件
```

## 架构（big picture）

**渲染层**：pywebview 把 `web/index.html` 跑在 WebView2（Windows）/WKWebView（macOS）/
WebKitGTK（Linux）里。CSS 处理圆角/阴影/暗色/DPI/8 方向 resize，JS 调
`window.pywebview.api.xxx` 拿数据。

**桥层**：`api/core.py:Api` 注入到 `webview.create_window(js_api=...)`。Api 是无下划线
公开方法的薄门面，内部把职责分到 `api/{chrome, screen, window, providers, state, settings}`。

**数据层**：`providers/{base,zhipu,opencode,mimo,kimi,volcengine,cdp}` 各自 fetch →
`UsageData`。`BaseProvider.fetch() -> UsageData`（含 `UsageItem[]`：label / used_percent /
reset_at / note）。

**统一取数优先级**（5 个 provider 一致）：
1. **凭证直连**（`has_direct_credentials(cfg)` 为 True 时）：纯 HTTP 直连，不开浏览器
2. **CDP 兜底**（直连失败且 `cdp_enabled` 时）：连调试 Chrome 页面内 fetch
3. 两者都不可用 → 报错引导用户「提取凭证」

各 provider 都实现 `extract_credentials(port, cdp_url)` 类方法：从调试 Chrome
一次性提取凭证存入 config（cookie / JWT / org_id 等），设置页「提取凭证」按钮
触发 `Api.extract_provider_credentials`。

- `ZhipuProvider`：直连用 `auth_token`(JWT，无 exp 长期有效) + `org_id` +
  `project_id` 三个凭证调 `/api/monitor/usage/sub-account-rank`。Authorization
  **不加 `Bearer`** 前缀。API Key 模式（个人版）保留。
- `OpenCodeProvider`：直连用 `auth` cookie GET workspace 页面，用量由 SSR 以
  SolidJS `$R[n]={...usagePercent:0}` 序列化形态直接嵌在 HTML（key 无引号，
  `_parse_usage_text` 三种形态正则都覆盖），无需执行 JS。
- `KimiProvider`：直连用 `kimi-auth` cookie（JWT 约 28 天有效）POST 会员网关，
  headers 由 `_build_headers`（JWT payload 的 sub/device_id/ssid 注入）。
- `MimoProvider`：直连用 cookie 调 `/api/v1/tokenPlan/usage`。
- `VolcEngineProvider`：直连用 cookie + `csrfToken`（从 cookie 里取）POST
  GetAgentPlanAFPUsage（未实测，CDP 兜底）。

**配置 / 日志 / 状态**（均在 `%APPDATA%/token_view/`）：
- `config.json`：pathlib + 原子写
- `debug.log`：`logger.py`，调试 GUI 异步流程必看
- `state.json`：Headroom 风格，schema=1，给 companion 工具（菜单栏/状态栏 hook 等）消费
- `chrome_profile/`：CDP Chrome 独立 user-data-dir

## 配置 & 日志位置（不在仓库内，易找错）

- 配置：`%APPDATA%/token_view/config.json`
- 日志：`%APPDATA%/token_view/debug.log`（CDP 各步骤都写这里，排错第一手）
- 状态文件：`%APPDATA%/token_view/state.json`（Headroom/claude-statusbar 风格，
  供其他工具读，**不依赖** pywebview；schema=1）
- CDP Chrome 独立 profile：`%APPDATA%/token_view/chrome_profile`
- OpenCode 调试 dump：`%APPDATA%/token_view/opencode_debug.html`

## 智谱团队用量：凭证直连（已实测无反爬）

**2026-07 实测结论（36 次压测）**：纯 `requests` 带三个凭证直连 `sub-account-rank`
全部成功（含 10 次无间隔爆发、裸 `python-requests` UA 也通过）。阿里云 WAF 的
JS 挑战 cookie `acw_sc__v2` 从未出现（每次响应种的 `acw_tc` 只是会话追踪，无害）。
当年"cookie+auth 路径返回空 `data:{}`"的真相 100% 确认是**缺 `Bigmodel-Organization`
/`Bigmodel-Project` 两个 header**（去掉即复现空 data），与 TLS 指纹/WAF 无关。

最小凭证集（连 Cookie 头都不需要）：
1. `Authorization: <bigmodel_token_production JWT>`（**不加 Bearer**，无 exp 长期有效）
2. `Bigmodel-Organization: <org_id>`（localStorage `Bigmodel-Organization`）
3. `Bigmodel-Project: <project_id>`（localStorage `Bigmodel-Project`）

**日常流程**：设置页「提取凭证」→ CDP 一次性拿这三个值存 config → 之后纯 HTTP。
直连失败（401/空 data）自动回退 CDP（若 `cdp_enabled`）。

**重置倒计时（nextResetTime 的出处，踩坑记录）**：
- `sub-account-rank` 的 `rateLimitStatus` **只有百分比**（fiveHour/week/mcp），
  实测完整 dump 确认无任何重置时间字段
- 重置时间在**另一个接口**：`GET https://bigmodel.cn/api/monitor/usage/quota/limit?type=2`
  （团队用量页前端自己也调它），同一套 JWT + org/project 凭证即可，
  返回 `limits[]` 带 `nextResetTime`（毫秒）：`TOKENS_LIMIT unit=3` → 5h 窗口、
  `unit=6` → 每周窗口、`TIME_LIMIT` → MCP 月度
- **别用** `open.bigmodel.cn` + API Key 调这个接口——团队版账号返回
  `{"code":500,"msg":"当前用户不存在coding plan"}`（那是个人版通道）
- 实现：`_fetch_team_http` 先调 `_fetch_quota_resets_http` 拿 {label: reset_at}
  再解析 rank（失败仅无倒计时，不阻塞主流程）；CDP 兜底在同一个 JS 里
  多发一次 fetch 拿回 quota 文本一并解析

## CDP 四件套（凭证提取 / 兜底时仍需）
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

5 个 provider 共享 `CDPHarness`（`find_page` + `evaluate` + `get_cookies`），
**不要**在 provider 内部再写一遍 `requests.get(/json)` + `ws_connect`。

- **凭证提取**（推荐主路径）：`extract_domain_cookies(port, cdp_url, 域名关键字, 域名)`
  一次性拿 page + 过滤后的 cookie 列表；`cookie_header()` 拼成 HTTP Cookie 头。
  提取后存 config，日常刷新走纯 HTTP，不碰 CDP。
- **CDP 兜底**（直连失败时）：实例化 `CDPHarness(port, page_keyword=域名)`，
  `find_page()` 拿 target，拼 JS 字符串 `evaluate(ws_url, js, await_promise=True)`，
  解析 `result.get("value")`。

## 关键坑（非显而易见，踩过）

- **各 provider 重置时间（reset_at）来源不一**：智谱个人版 `quota/limit` 有
  `nextResetTime`；团队版要额外调 `quota/limit?type=2`（见上节，sub-account-rank
  只有百分比）；OpenCode 的 `resetInSec` 是**距重置的秒数**要 `+ time.time()`；
  Kimi 是 ISO 字符串 `resetTime`；MiMo 接口不返回，按自然月（次月 1 号）估算。
  前端倒计时圆环按 label 推断窗口时长（5h/7d/30d）算剩余比例，无 reset_at 不显示。
- **pywebview 跨平台**：Windows 上 `transparent=True` 在高 DPI 下会被 WebView2 裁切，
  改成 `transparent=not is_windows`，CSS 用 `body.no-transparent` 兜底非透明背景。
- **DPI 缩放**：JS 传来的尺寸是 CSS 逻辑像素，Win32 `SetWindowPos` 要物理像素，
  用 `api.screen.windows_dpi_scale()` 换算。`api.window._resize_windows` 是参考实现。
- **macOS 主线程**：Cocoa `NSWindow.setFrame_` 必须在主线程，用
  `api.screen.run_on_macos_main_thread` 同步等。`quit_app` 走 `os._exit(0)`
  否则 Cocoa 事件循环不会退。
- **智谱团队版无公开用量 API**：API Key 调个人 monitor 接口返回 `"当前用户不存在coding plan"`。
  团队用量只能靠登录态凭证（JWT + org/project）抓取（控制台背后的 XHR）。
- **反爬根因（已实测确认）**：不是 WAF/`acw_sc__v2`/TLS 指纹。后端 `sub-account-rank`
  要求请求带 `Bigmodel-Organization` 和 `Bigmodel-Project` 两个 header（值在 localStorage 的
  `Bigmodel-Organization`/`Bigmodel-Project` key 里，对应组织 id 和项目 id），
  缺这两个就返回空 `data:{}`（2026-07 压测 36 次精确复现）。程序化请求漏了这俩，
  所以拿空。**纯 `requests` 带齐三个凭证（裸 JWT Authorization + 两个 header）即过**，
  无需 Cookie/UA/Referer，`acw_sc__v2` JS 挑战从未触发。
- **取数优先级**：`ZhipuProvider.fetch` 中 `has_direct_credentials(cfg)`（auth_token + org_id
  + project_id 三件套）为 True 时优先纯 HTTP；失败（401/空 data）且 `cdp_enabled` 时
  回退 CDP 兜底。CDP 仅用于凭证提取和兜底，不依赖浏览器常开。WebSocket 连接必须带
  `origin: http://127.0.0.1:<port>` 头，否则 Chrome 返回 403 Forbidden
  （`CDPHarness` 自动处理）。
- **WebView 必须在 GUI 线程**；cookie 抓取依赖用户登录态，无法离线/无头测试，
  必须真人登录。
- **pywebview 文档陷阱**：部分 `Screen` 对象在不同版本是 dict/对象两种形态，
  `api.screen.screen_value()` 兼容两种。
- **pywebview WinForms 序列化**：Windows 上 pywebview 的 `get_functions` 会递归遍历
  `api.window.native`（.NET WinForms Form），导致 COM 无限递归崩溃。`main.py`
  标记 `window._serializable = False` 避开。
- **WebView2 透明窗口裁切**（高 DPI 125%/150%）：WinForms WebView2 + Frameless + Transparent
  + 高 DPI 已知会让窗口边缘被裁切。规避：Windows 不开 `transparent=True`，CSS 用
  `body.no-transparent` 给不透明背景。
- **WS_EX_LAYERED 整窗 alpha 副作用**：用 `Form.Opacity` 整窗 alpha 混合时，文字/进度条
  也一起淡化，但视觉上大块背景"穿透感"强、细线条不显透明，用户反馈"只淡背景"。
  改为**假透明度**（CSS `rgba(--opacity-primary)`），仅背景半透，文字清晰。
- **WebView2 改 y 后重派 mouseenter/mouseleave**：`set_dock_hidden` 调 Win32
  SetWindowPos 改窗口 y，WebView2 会重派 mouseenter/mouseleave，导致 dock auto-hide
  死循环（`auto-hide dock: y=0 ↔ -NNN` 反复）。修法：mouseleave 走 200ms 延迟，
  `set_dock_hidden` 去重 + 配 `e.screenY < 4`（不用 `e.clientY`，WebView 内部坐标会乱）。
- **fit 完 dock hidden y 算错**：`moveToTop` 立即触发 set_dock_hidden 用的是 `move_to_top`
  时的临时高度 h，之后 `resize_window_to_content` 把窗口 fit 到真实高度，y 仍按
  旧 h 算 → 窗口完全在屏幕外。修法：`fitWindowOnce` 完重设 `set_dock_hidden(true)`，
  后端 `GetWindowRect` 拿新 h 重算 `new_y = 4 - h`。
- **mouseenter/mouseleave 监听 `document` 不在 `container`**：因为 WebView 重派时基于
  整个 WebView 视口边界而非单个 DOM 元素。但 mouseleave 走 200ms 延迟后，
  后续 WebView 重派立即反转被定时器压制。

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

凭证直连三件套：`auth_token`(JWT，无 exp 长期有效) / `org_id` / `project_id`
——设置页「提取凭证」自动填充，齐备时 `has_direct_credentials` 返回 True，
日常刷新走纯 HTTP。其余：`cdp_enabled`（默认 True）/ `cdp_port`（9222）/ `cdp_url`
/ `api_key`（个人版）/ `cookie` / `usage_url`（旧路径）/ `customer_id`。
直连失败且 `cdp_enabled` 时自动回退 CDP 兜底。

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

## 新增 Provider

1. 在 `providers/` 下加 `<name>.py`，继承 `BaseProvider`
2. 实现 `fetch() -> UsageData`：优先纯 HTTP 直连，失败回退 CDP
3. 实现 `has_direct_credentials(cfg)`（凭证齐备返回 True）和
   `extract_credentials(port, cdp_url)`（一次性从 CDP 提取凭证存 config）
4. 纯 HTTP 用 `requests`；CDP 用 `extract_domain_cookies` 提取凭证 / `CDPHarness` 兜底
5. `providers/__init__.py` 的 `_CLASSES` 字典加 `"type": YourProvider`
6. `config.new_provider("...")` 加默认值

## 打包（PyInstaller）

`TokenView.spec` 配置：
- `console=False` 窗口模式
- `datas=[('web', 'web')]` 打包 web 资源
- `hiddenimports` 显式列 `webview.platforms.winforms` 等动态 import
- `excludes` 排除 numpy/PySide6 等大依赖

产物：`dist/TokenView.exe`（~22MB，onedir 可拆分）。WebView2 Runtime 用户机器
需预装（Win11 自带，Win10 一般预装）。

## 改代码注意

- 全程中文：代码注释、日志、用户提示、commit message 均用中文
- 优先编辑现有文件，不过度抽象，不加无谓新文件
- WebView 必须在 GUI 线程；登录抓取依赖真人登录态，无法离线/无头测试
- Win32 SetWindowPos 物理坐标用 `screen_helper.windows_dpi_scale` 换算（CSS 逻辑 → 物理）
- **不要**用 `e.clientY` 判断"鼠标接近屏幕顶部"——WebView 内部坐标会乱；
  **用** `e.screenY`（屏幕绝对坐标）
