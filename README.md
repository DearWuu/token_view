# Token 用量监控

桌面悬浮窗，实时显示智谱 GLM Coding Plan / OpenCode Go / 小米 MiMo 的 token 用量。
**pywebview**（WebView2 / WKWebView）跑前端，CDP 协议连接调试 Chrome 抓数据，
免去手动复制 Cookie。

## 快速开始

### 方式一：下载 exe（推荐）

从 [Releases](https://github.com/DearWuu/token_view/releases) 下载最新的 `TokenView.exe`，
**双击运行**。

> Windows 用户需安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win11 自带，Win10 一般也预装）。

### 方式二：从源码运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（Windows 控制台必须加 -X utf8，否则中文乱码）
python -X utf8 main.py
```

## 自己打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包（输出 dist/TokenView.exe）
pyinstaller TokenView.spec --clean --noconfirm
```

打包配置见 `TokenView.spec`，关键点：
- `console=False` 窗口模式，不弹 console
- `web/` 资源通过 `datas` 打包进 exe
- `hiddenimports` 显式列 `webview.platforms.*` 等动态 import 的模块
- `excludes` 排除 numpy/PySide6 等大依赖

## 项目结构

```
.
├── main.py                    # 入口：创建 pywebview 窗口 + 启动
├── config.py                  # 配置 JSON 读写（pathlib + 原子写）
├── logger.py                  # 统一日志
├── requirements.txt           # 运行时依赖
├── TokenView.spec             # PyInstaller 打包配置
│
├── providers/                 # 用量数据源
│   ├── base.py                # BaseProvider / UsageData / UsageItem
│   ├── cdp.py                 # CDPHarness + 异常类
│   ├── zhipu.py               # 智谱 GLM（CDP/cookie/API key）
│   ├── opencode.py            # OpenCode Go
│   ├── mimo.py                # 小米 MiMo
│   └── __init__.py            # build() 工厂
│
├── api/                       # pywebview js_api 桥层
│   ├── core.py                # Api 类（编排）
│   ├── chrome.py              # Chrome 查找 + CDP 启动
│   ├── screen.py              # 跨平台屏幕工作区 / Win32 HWND / DPI
│   ├── window.py              # 窗口几何（move/resize/置顶/auto-hide）
│   ├── providers.py           # Provider JSON 配置 CRUD
│   ├── state.py               # state.json 协议（Headroom 风格）
│   ├── settings.py            # 设置窗口 + 模式/刷新间隔
│   └── __init__.py            # 暴露 Api 类
│
├── web/                       # 前端
│   ├── index.html             # 主面板
│   ├── app.js                 # 主逻辑：定时刷新 + 模式切换 + resize + auto-hide
│   ├── style.css              # 主题 + 顶部模式 + 假透明度 CSS 变量
│   └── settings.html          # 设置页
│
├── tests/                     # pytest 单元测试（27 个用例）
│   ├── test_base.py           # 数据模型 + 工厂
│   ├── test_state.py          # state.json 协议
│   └── test_config.py         # 配置读写
│
└── docs/                      # 文档
    ├── architecture.md        # 架构图 + 数据流
    └── archive/               # 历史任务交接
```

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  桌面进程（pywebview）                                    │
│                                                            │
│  ┌────────────────────┐      ┌────────────────────────┐  │
│  │ web/index.html     │      │ web/settings.html      │  │
│  │ + app.js + css     │ ←──→ │ (独立 webview 窗口)     │  │
│  │ 8 方向 resize       │  js   └────────────────────────┘  │
│  │ auto-hide dock     │  api                            │
│  └─────────┬──────────┘                                  │
│            │                                              │
│  ┌─────────▼────────────────────────────────────────────┐ │
│  │  api/core.py:Api                                    │ │
│  │   ├─ chrome.py                                     │ │
│  │   ├─ screen.py (Win32 HWND / DPI)                 │ │
│  │   ├─ window.py (SetWindowPos / move / resize)     │ │
│  │   ├─ providers.py (CRUD)                          │ │
│  │   ├─ state.py (state.json 协议)                   │ │
│  │   └─ settings.py                                  │ │
│  └─────────┬────────────────────────────────────────────┘ │
│            │                                              │
│  ┌─────────▼────────────────────────────────────────────┐ │
│  │  providers/                                        │ │
│  │   ├─ base.py + cdp.py (CDPHarness 共享)           │ │
│  │   ├─ zhipu.py                                     │ │
│  │   ├─ opencode.py                                  │ │
│  │   └─ mimo.py                                       │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
           │                          │
           │ 写 state.json            │  WebSocket CDP
           ▼                          ▼
   %APPDATA%/token_view/      调试 Chrome
   ├─ config.json             (--remote-debugging-port)
   ├─ state.json                │
   ├─ debug.log                ▼
   └─ chrome_profile/      bigmodel.cn / opencode.ai / xiaomimimo.com
```

详细架构 + 数据流：[docs/architecture.md](docs/architecture.md)

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
| **顶部模式** | 点击 ⇧，卡片移到屏幕顶部 + 启用 **auto-hide**（QQ 风格） |

**auto-hide**：
- 启用顶部模式后，鼠标离开窗口 200ms 自动滑出（露 4px 缝）
- 鼠标回到屏幕顶部 4px 区域，窗口滑下完整显示
- 再点 ⇧ 关闭，回到原位

**8 方向 resize**：
- 4 条边 + 4 个角都能拖（4 边 + 3 角 nw/ne/sw + 1 grip 当 se）
- 拖左/上边时窗口位置跟着变

**通用**：
- **刷新间隔**：默认 60 秒，可在设置里改（15~3600 秒）。
- **窗口置顶**：默认开启，右键菜单可切换。
- **透明度**（假透明度）：背景半透，文字/进度条保持清晰。
- **窗口位置和大小**：可拖拽/拉伸，退出时自动保存，下次启动恢复。
- 进度条刷新时从上次值平滑过渡。

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

## 测试

```bash
pytest tests/
```

27 个单元测试覆盖数据模型、Provider 工厂、state.json 协议、config 读写、原子写。
无 GUI 依赖，0.2s 跑完。

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

**Q: 打包后 exe 太大？**
22MB 包含 pywebview + PIL + websocket-client + 全部 Python 运行时。
可用 UPX 压缩（编辑 TokenView.spec 设 `upx=True`）。
