# Token 用量监控

桌面悬浮窗，实时显示智谱 GLM Coding Plan、OpenCode Go、小米 MiMo 的 token 用量。基于 pywebview，通过 CDP 协议连接调试 Chrome 抓取数据，免去手动复制 Cookie。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（Windows 必须加 -X utf8 避免中文乱码）
python -X utf8 main.py

# macOS / Linux
python main.py
```

## 项目结构

```
main.py          # 入口：创建 pywebview 窗口、DPI 感知、macOS 置顶
api.py           # Python ↔ JS 桥接层：窗口管理、Provider 管理、CDP 启动
config.py        # 配置读写（%APPDATA%/token_view/config.json）
providers.py     # 用量查询：ZhipuProvider / OpenCodeProvider / MimoProvider
logger.py        # 日志（%APPDATA%/token_view/debug.log）
web/
  index.html     # 主窗口页面
  app.js         # 前端逻辑：卡片渲染、窗口缩放、模式切换
  style.css      # 样式
  settings.html  # 设置窗口页面
```

## 配置供应商

### 智谱 GLM Coding Plan（团队版）

团队版**必须用 CDP 模式**——API Key 无法访问团队数据，Cookie 直连会被反爬拦截。

1. 点右上 ⚙ 打开设置，添加智谱账号。
2. 保持 CDP 启用（默认），端口默认 `9222`。
3. 点 **🚀 启动调试 Chrome 登录智谱**，在弹出的独立 Chrome 里完成登录，保持「团队用量」页打开。
4. 返回设置点保存，悬浮窗自动刷新。

> CDP Chrome 启动后保持运行即可，程序每次刷新都会连它读取数据。

### 智谱 GLM Coding Plan（个人版）

1. 设置里添加智谱账号，取消勾选 CDP。
2. 填入 API Key（在 open.bigmodel.cn 创建）。
3. 保存即可。

### OpenCode Go / 小米 MiMo

1. 设置里添加对应账号。
2. CDP 模式下 Cookie 留空，点 **🚀 启动调试 Chrome** 登录对应平台。
3. 保存即可。

## 功能说明

| 按钮 | 功能 |
|------|------|
| ↻ | 手动刷新 |
| ⇧ | 放大并移到屏幕顶部（顶部条模式） |
| ⤡ | 切换紧凑/普通模式 |
| 📌 | 屏幕置顶开关 |
| ⚙ | 打开设置 |
| ✕ | 退出 |

- **窗口拖拽**：拖动标题栏移动窗口位置。
- **窗口缩放**：拖动窗口四条边或四个角调整大小。
- **刷新间隔**：默认 60 秒，可在设置里改。
- 绿 = 低于 70%，黄 = 70%~90%，红 = 90%+。

## 文件位置

| 内容 | 路径 |
|------|------|
| 配置文件 | `%APPDATA%\token_view\config.json`（macOS: `~/Library/Application Support/token_view/config.json`）|
| 调试日志 | 同目录下 `debug.log` |
| CDP Chrome 用户数据 | 同目录下 `chrome_profile` |

---

## 打包

### Windows 打包为 exe

#### 前置条件

- Windows 10/11
- Python 3.10+（推荐 3.12-3.14）
- 已安装项目依赖：`pip install -r requirements.txt`

#### 步骤

```bash
# 1. 安装 PyInstaller
pip install pyinstaller

# 2. 打包（单文件模式）
python -m PyInstaller --noconfirm --onefile --windowed --name TokenView ^
  --add-data "web;web" ^
  --hidden-import=clr ^
  --hidden-import=webview.platforms.edgechromium ^
  --hidden-import=webview.platforms.winforms ^
  --collect-all webview ^
  main.py
```

> PowerShell 用 `^` 换行不生效，直接写一行或用反引号 `` ` ``。

打包完成后，`dist/TokenView.exe` 即可分发（约 16 MB），目标机器无需安装 Python。

#### 验证

```bash
dist\TokenView.exe
```

#### 注意事项

- WebView2 Runtime 需预装（Windows 11 已内置，Windows 10 可能需手动安装：[下载地址](https://developer.microsoft.com/microsoft-edge/webview2/)）
- 首次启动较慢（单文件需解压到临时目录），后续正常
- `--onefile` 单文件 vs `--onedir` 文件夹：单文件方便分发，文件夹启动更快

---

### macOS 打包为 .app

#### 前置条件

- macOS 12+
- Python 3.10+（推荐 pyenv 安装，避免系统 Python 权限问题）
- 已安装项目依赖：`pip install -r requirements.txt`
- pywebview macOS 后端依赖 PyObjC（`pip install pyobjc`）

#### 步骤

```bash
# 1. 安装 PyInstaller
pip install pyinstaller

# 2. 打包（.app 应用包）
python -m PyInstaller --noconfirm --onefile --windowed --name TokenView \
  --add-data "web:web" \
  --hidden-import=webview.platforms.cocoa \
  --collect-all webview \
  main.py
```

> macOS 的 `--add-data` 分隔符是 `:`（冒号），Windows 是 `;`（分号）。

打包完成后，`dist/TokenView.app` 即可分发。

#### 验证

```bash
open dist/TokenView.app
```

#### 注意事项

- **Apple Silicon (M1/M2/M3)** 和 **Intel** 需要分别打包，不能通用。用 `arch -x86_64 python ...` 可在 Apple Silicon 上打 Intel 包（需装 x86 Python）
- 首次打开如果提示「无法验证开发者」，右键 → 打开，或 `xattr -cr dist/TokenView.app`
- 如需分发给其他用户，需用 Apple Developer 证书签名 + 公证（`codesign` + `xcrun notarytool`）
- WebView 后端是系统自带的 WKWebView，无需额外运行时

---

### 跨平台打包参数对照

| 参数 | Windows | macOS |
|------|---------|-------|
| `--add-data` 分隔符 | `;` | `:` |
| `--hidden-import` 后端 | `webview.platforms.edgechromium` `webview.platforms.winforms` `clr` | `webview.platforms.cocoa` |
| 产物 | `dist/TokenView.exe` | `dist/TokenView.app` |
| WebView 依赖 | WebView2 Runtime（Win11 内置） | WKWebView（系统内置） |
| 体积 | ~16 MB | ~12 MB |

---

## 常见问题

**Q: 显示「CDP 连接失败」？**
确认调试 Chrome 已启动且 `9222` 端口没被占用。确保 Chrome 启动时带了 `--remote-debugging-port=9222 --remote-allow-origins=*`。

**Q: 智谱显示「暂无数据」？**
团队版必须用 CDP 模式。确认 Chrome 里打开了智谱团队用量页且处于登录状态。

**Q: Windows 下窗口右侧/底部被裁？**
程序已内置 DPI 自适应。如果仍有问题，检查 Windows 显示设置 → 缩放比例是否正常。

**Q: 打包后 exe 打不开？**
确认目标机器已安装 WebView2 Runtime。Windows 11 自带，Windows 10 需手动安装。

**Q: macOS 打开 .app 提示「无法验证开发者」？**
右键点击 .app → 打开 → 确认。或终端执行 `xattr -cr /path/to/TokenView.app`。

**Q: 想改 CDP Chrome 的数据目录？**
手动启动 Chrome 时加 `--user-data-dir=你的路径` 即可。默认在 `%APPDATA%\token_view\chrome_profile`。
