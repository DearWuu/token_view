# Token 用量监控

桌面悬浮窗，实时显示智谱 GLM Coding Plan / OpenCode Go / 小米 MiMo 的 token 用量。
**pywebview**（WebView2 / WKWebView）跑前端，CDP 协议连接调试 Chrome 抓数据，
免去手动复制 Cookie。

## 快速开始

```bash
# 1. 安装依赖
pip install pywebview requests websocket-client pystray pillow

# 2. 启动
python -X utf8 main.py
```

> Windows 必须加 `-X utf8`，否则控制台中文乱码。托盘图标在右下角，关窗不退出。

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  main.py                                                 │
│  webview.create_window(js_api=api.Api())                 │
│                                                          │
│  ┌──────────────────┐       ┌──────────────────────────┐ │
│  │  web/index.html  │       │  web/settings.html       │ │
│  │  app.js          │ ←──→  │  (独立窗口)              │ │
│  │  style.css       │  js   └──────────────────────────┘ │
│  │  CSS 渲染卡片    │  api                                │
│  └──────────────────┘       │                            │
│           │                 │  api/core.py:Api          │
│           │                 │   ├─ chrome.py            │
│           │                 │   ├─ screen.py            │
│           │                 │   ├─ window.py            │
│           │                 │   ├─ providers.py         │
│           │                 │   ├─ state.py             │
│           │                 │   └─ settings.py          │
│           │                 └────────────────────────────│
│           │                              │               │
│           │                              ▼               │
│           │                 ┌──────────────────────────┐│
│           │                 │  providers/              ││
│           │                 │   ├─ base.py             ││
│           │                 │   ├─ cdp.py (CDPHarness) ││
│           │                 │   ├─ zhipu.py            ││
│           │                 │   ├─ opencode.py         ││
│           │                 │   └─ mimo.py             ││
│           │                 └──────────────────────────┘│
└──────────────────────────────────────────────────────────┘
                          │
                          │  写 state.json (Headroom 风格)
                          ▼
        %APPDATA%/token_view/state.json
        供其他工具（菜单栏/状态栏 hook/IDE 插件）消费
```

详细架构图与数据流见 [`docs/architecture.md`](docs/architecture.md)。

## 配置智谱 GLM Coding Plan（团队版）

智谱团队版**必须用 CDP 模式**——API Key 无法访问团队数据，Cookie 直连会被反爬拦截
（缺 `Bigmodel-Organization` / `Bigmodel-Project` 头）。

### 步骤

1. **右键托盘图标 → 设置**，点 `＋ 智谱` 添加账号。
2. **保持 CDP 启用**（默认勾选），端口默认 `9222` 无需改。
3. **填 `我的账号ID`**（可选）：在智谱团队页面的用量列表里找你名字对应的 ID
   （如 `9951…`），留空则显示排名第一的成员。
4. 点 **🚀 启动调试 Chrome 登录智谱**：
   - 会打开一个独立的 Chrome 窗口（不污染你的日常 Chrome）
   - 在弹出的页面里完成登录，保持「团队用量」页打开
   - 返回设置点 **保存**
5. 保存后悬浮窗自动刷新，开始显示用量。

> CDP Chrome 启动后保持运行即可，程序每次刷新都会连它读取数据。窗口关掉会自动退出
> 程序，下次需要重新启动 CDP Chrome。

### 数据说明

| 指标 | 含义 |
|------|------|
| 5h 窗口 | 近 5 小时的用量百分比 |
| 每周窗口 | 本周用量百分比 |
| MCP 月度 | 本月 MCP 工具调用百分比 |

- 绿 = 低于 70%，**黄** = 70%~90%，**红** = 90%+。
- 普通模式下可看到进度的 token 数和重置倒计时。

## 配置智谱 GLM Coding Plan（个人版）

如果你用的是**个人版 API Key**（非团队）：

1. 设置里添加智谱账号，**取消勾选 CDP**。
2. 在 `API Key` 里填入你在 [open.bigmodel.cn](https://open.bigmodel.cn) 创建的 API Key。
3. 保存即可。接口会自动调 `/api/monitor/usage/quota/limit` 获取个人版用量。

## 配置 OpenCode Go

1. 设置里 `＋ OpenCode` 添加账号。
2. **填 workspace_id**（如 `wrk_xxxxxxxx`，可在 opencode.ai 网址里找到）。
3. 点 **🚀 启动调试 Chrome 登录 OpenCode**。
4. 在新 Chrome 里登录 opencode.ai，返回设置点保存。
5. 保存后自动开始抓取。

> CDP 模式下 Cookie 留空即可。旧版手动填 Cookie 的方式仍可用（取消 CDP 勾选 +
> 填 auth cookie），但推荐 CDP。

## 功能说明

| 模式 | 说明 |
|------|------|
| **普通模式** | 悬浮卡片，垂直排列用量项，含进度条 + token 数 + 重置倒计时 |
| **紧凑模式** | 点击 ⤢，隐藏副文本，只保留标签 + 百分比 |
| **顶部模式** | 点击 ⤓，卡片移到屏幕顶部，横向一行显示「服务商 + 各项用量 + 进度条 + 百分比」 |

- **刷新间隔**：默认 60 秒，可在设置里改（15~3600 秒）。
- **窗口置顶**：默认开启，右键菜单可切换。
- **窗口位置和大小**：可拖拽，退出时自动保存，下次启动恢复。
- 进度条刷新时从上次值平滑过渡，不会从零跳动。

## 文件位置

| 内容 | 路径 |
|------|------|
| 配置文件 | `%APPDATA%\token_view\config.json` |
| 状态文件 | `%APPDATA%\token_view\state.json` |
| 调试日志 | `%APPDATA%\token_view\debug.log` |
| CDP Chrome 用户数据 | `%APPDATA%\token_view\chrome_profile` |
| OpenCode 调试 HTML | `%APPDATA%\token_view\opencode_debug.html` |

## 状态文件协议（Headroom 风格）

`%APPDATA%\token_view\state.json` 持续写入最新用量数据（schema=1），
供其他工具（菜单栏、状态栏 hook、IDE 插件、手机端推送）**直接消费**，无需自己重抓 CDP。

```json
{
  "schema": 1,
  "ts": 1719850000.12,
  "ts_iso": "2024-07-01T12:00:00+00:00",
  "providers": [
    {"id":"...","name":"智谱","type":"zhipu","status":"ok","error":"",
     "level":"团队·张三","fetched_at":...,
     "items":[{"label":"5h 窗口","percent":12.5,"reset_at":...,"note":""}]}
  ]
}
```

## 常见问题

**Q: 点了启动 Chrome 但没反应？**
确认 Chrome 已安装。如果安装位置不在常见路径，请手动用以下命令启动：
```
chrome.exe --remote-debugging-port=9222 --remote-allow-origins=*
```

**Q: 显示「CDP 连接失败」？**
确认调试 Chrome 已启动且 `9222` 端口没有被占用。确保 Chrome 启动时带了
`--remote-allow-origins=*`。

**Q: 显示「登录可能已过期」？**
在 CDP Chrome 里刷新 bigmodel.cn 页，确认处于登录状态，然后点悬浮窗的 ↻ 手动刷新。

**Q: 智谱显示「暂无数据」？**
团队版必须用 CDP 模式。API Key 只能看到个人版数据。如果已经是 CDP 模式，
确认 Chrome 里打开了智谱团队用量页。

**Q: 想改 CDP Chrome 的数据目录？**
手动启动 Chrome 时加 `--user-data-dir=你的路径` 即可。默认在
`%APPDATA%\token_view\chrome_profile`。
