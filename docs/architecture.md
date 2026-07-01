# 架构说明

## 一、整体形态

```
┌────────────────────────────────────────────────────────────────┐
│  桌面进程（pywebview）                                          │
│                                                                │
│  ┌───────────────────────┐  ┌────────────────────────────┐    │
│  │ web/index.html        │  │ web/settings.html          │    │
│  │ + app.js + style.css  │  │ (独立 webview 窗口)        │    │
│  │ 主悬浮窗 UI           │  │                            │    │
│  └──────────┬────────────┘  └────────────┬───────────────┘    │
│             │                            │                    │
│             │ window.pywebview.api.xxx   │                    │
│             ▼                            ▼                    │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  api/core.py:Api   (js_api 桥)                          │ │
│  │                                                          │ │
│  │  get_usage / get_config / save_config                    │ │
│  │  add_provider / remove_provider / update_provider       │ │
│  │  launch_cdp_chrome                                       │ │
│  │  toggle_on_top / set_opacity / set_geometry             │ │
│  │  set_top_mode / set_compact / set_dock                   │ │
│  │  set_refresh_interval                                    │ │
│  │  resize_window_to_content / move_window_to_top          │ │
│  │  open_settings_window / close_settings / quit_app        │ │
│  │  collect_and_persist (新)                                │ │
│  │  └─ 内部分发到下面子模块                                 │ │
│  └──────┬──────────────┬──────────────┬─────────────┬──────┘ │
│         │              │              │             │        │
│   ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐  ┌────▼─────┐ │
│   │ chrome.py │  │ screen.py │  │ window.py │  │providers │ │
│   │ find/     │  │ 跨平台    │  │ move/     │  │  .py     │ │
│   │ launch    │  │ 屏幕/DPI  │  │ resize/   │  │ (CRUD)   │ │
│   │ CDP Chrome│  │ 几何      │  │ top/alpha │  │          │ │
│   └─────┬─────┘  └───────────┘  └───────────┘  └──────────┘ │
│         │                                                      │
│   ┌─────▼─────┐  ┌────────────┐  ┌─────────────┐             │
│   │ state.py  │  │ settings.py│  │ logger.py   │             │
│   │ state.json│  │ 设置窗口   │  │ debug.log   │             │
│   │ 原子写    │  │ + 模式     │  │             │             │
│   └───────────┘  └────────────┘  └─────────────┘             │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                  │ 写 state.json                │
                  ▼                               │
       %APPDATA%/token_view/state.json            │
                  │                               │
                  │ 读（不需要 pywebview）         │
                  ▼                               │
       ┌─────────────────────────────────────┐    │
       │  其他工具（companion）               │    │
       │   ├ 菜单栏 / 状态栏 hook            │    │
       │   ├ IDE 插件                        │    │
       │   └ 手机端推送                      │    │
       └─────────────────────────────────────┘    │
                                                   │
                  │ WebSocket CDP                  │
                  ▼                                │
       ┌─────────────────────────────────────┐     │
       │  调试 Chrome（用户登录的）           │     │
       │  9222 + --remote-allow-origins=*    │     │
       │  user-data-dir=独立 profile         │     │
       └─────────────────────────────────────┘     │
                                                   │
                  │ fetch (bigmodel.cn / opencode.ai)
                  ▼                                │
       ┌─────────────────────────────────────┐     │
       │  服务端                              │     │
       │  智谱 / OpenCode / 小米 MiMo         │     │
       └─────────────────────────────────────┘     │
```

## 二、数据流

### A. 前端主动 refresh

```
web/app.js setInterval(refresh, refreshInterval)
   ↓
window.pywebview.api.get_usage()       # api/core.py:Api.get_usage
   ↓
for p in cfg["providers"]:
    if p["enabled"]:
        providers.build(p).fetch()     # 构造对应 Provider
                                       # zhipu/opencode/mimo
            ↓
        Provider.fetch() → UsageData
            ↓
   包装成 dict（id, name, type, level, status, error, items[]）
   ↓
return list[dict]
   ↓
app.js renderCards(providers)
```

### B. 状态文件供给（Headroom 风格）

```
web/app.js / 其他 companion
   ↓
window.pywebview.api.collect_and_persist()
   ↓
Api.get_usage() → list[dict]  (同 A)
   ↓
api/state.py:write_state(providers_data)
   ↓
原子写 %APPDATA%/token_view/state.json (schema=1)
   ↓
其他工具（菜单栏/sw 状态栏 hook/IDE 插件）直接读 state.json
**完全不需要 pywebview，不需要重抓 CDP**
```

### C. CDP 抓取流程（智谱团队版）

```
Api.get_usage → ZhipuProvider.fetch
   ↓
if cfg["cdp_enabled"]:
    CDPHarness(port, page_keyword="bigmodel.cn")
        ├─ requests.get(cdp_url/json)            # 拿 page target 列表
        ├─ 找 type=="page" && url 含 "bigmodel.cn" 的 target
        └─ 拿 webSocketDebuggerUrl
   ↓
生成 IIFE：
   1. 从 document.cookie 提 bigmodel_token_production (裸 JWT，不加 Bearer)
   2. 从 localStorage 提 Bigmodel-Organization / Bigmodel-Project
   3. fetch('/api/monitor/usage/sub-account-rank?startTime=...',
             {credentials:'include',
              headers: {Authorization, Bigmodel-Organization, Bigmodel-Project,
                        Accept, Cache-Control}})
   4. return r.text()
   ↓
websocket.create_connection(ws_url, origin="http://127.0.0.1:9222")
   ↓
send {id:1, method:"Runtime.evaluate",
      params:{expression: js, awaitPromise: true, returnByValue: true}}
   ↓
recv → result.value 是接口返回的 JSON 字符串
   ↓
ZhipuProvider._parse_team(j["data"], data)
   ├─ 找 customerId==cfg.customer_id 的成员
   ├─ 读 rateLimitStatus.{fiveHourPercentage, weekPercentage, mcpPercentage}
   └─ 写 UsageItem 到 data.items
   ↓
return UsageData
```

## 三、为什么选 pywebview

调研过 4 个行业标杆项目（claude-monitor / Headroom / claude-statusbar / claude-monitor-cyd），
**没有用 PySide6/Qt 的**。原因：

| 痛点 | pywebview | Qt (PySide6) |
|------|-----------|--------------|
| Windows 高 DPI 透明窗口 | 直接用 CSS `border-radius` 干净 | 需 `QGraphicsDropShadowEffect` + `WA_TranslucentBackground` 拼 |
| 圆角 + 阴影 | CSS 一行 | 手写 `setStyleSheet` + 反复跨平台调 |
| 实时刷新 | `setInterval` + `await api.xxx` | `QThread` + `QObject` + `Signal`（Widget 不能 moveToThread） |
| 跨平台 DPI | 浏览器自己处理 | Win32 GetDpiForWindow + 物理/逻辑像素换算 |
| 调试 | 浏览器 DevTools 实时改 | 改一行 QSS 重启进程 |
| 动画 | CSS transition/keyframes | QPropertyAnimation 配曲线参数 |
| 包大小 | pywebview 5MB+ | PySide6 100MB+ |

**结论**：悬浮卡片 + 高 DPI + 圆角透明 + 跨平台 + 实时刷新 = Qt 的痛点密集区。
pywebview 把这些全外包给 WebView2/WKWebView，质量好得多。

## 四、状态文件协议

`api/state.py` 写 `%APPDATA%/token_view/state.json`，原子写。

**为什么**：Headroom（macOS 菜单栏，~780 行 Swift）的设计哲学——
数据采集和展示彻底解耦，数据落 JSON，UI 只读 JSON。
这样：
- 状态栏 / 菜单栏 / IDE 插件 / 手机端推送可以**独立仓库**实现
- 主项目保持单一职责
- 数据流不会因为某个 UI 坏掉而中断

**schema**：

```json
{
  "schema": 1,
  "ts": 1719850000.12,
  "ts_iso": "2024-07-01T12:00:00+00:00",
  "providers": [
    {
      "id": "abcd1234",
      "name": "智谱 GLM",
      "type": "zhipu",
      "level": "团队·张三",
      "status": "ok",
      "error": "",
      "fetched_at": 1719850000.0,
      "items": [
        {"label": "5h 窗口", "percent": 12.5, "reset_at": 1719853600, "note": ""},
        {"label": "每周窗口", "percent": 45.0, "reset_at": 1720281600, "note": ""}
      ]
    }
  ]
}
```

**消费方**（一行 Python）：

```python
import json
from api.state import state_file_path
data = json.loads(state_file_path().read_text(encoding="utf-8"))
for p in data["providers"]:
    print(p["name"], p["status"], [(i["label"], i["percent"]) for i in p["items"]])
```

## 五、新增 Provider 流程

1. `providers/<name>.py` 写一个继承 `BaseProvider` 的类，实现 `fetch() -> UsageData`。
2. 如果走 CDP，`providers.cdp.CDPHarness(port, page_keyword=...)` 复用 /json + Runtime.evaluate。
3. `providers/__init__.py` 的 `build()` 加 `if ptype == "..."` 分支。
4. `config.new_provider("...")` 加默认值。
5. `web/settings.html` 加 provider-specific 表单（可选）。
6. 测试：手动启动 CDP Chrome 登录 + `pytest tests/` 跑可测部分。
