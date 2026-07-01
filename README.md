# Token 用量监控

桌面悬浮窗，实时显示智谱 GLM Coding Plan 和 OpenCode Go 的 token 用量。基于 pywebview，通过 CDP 协议连接调试 Chrome 抓取数据，免去手动复制 Cookie。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动
python -X utf8 main.py
```

> Windows 必须加 `-X utf8`，否则控制台中文乱码。

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

## 配置智谱 GLM Coding Plan（团队版）

智谱团队版**必须用 CDP 模式**——API Key 无法访问团队数据，Cookie 直连会被反爬拦截（缺 `Bigmodel-Organization` / `Bigmodel-Project` 头）。

### 步骤

1. **点右上 ⚙ 打开设置**，添加智谱账号。
2. **保持 CDP 启用**（默认），端口默认 `9222`。
3. 点 **🚀 启动调试 Chrome 登录智谱**，在弹出的独立 Chrome 里完成登录，保持「团队用量」页打开。
4. 返回设置点 **保存**，悬浮窗自动刷新。

> CDP Chrome 启动后保持运行即可，程序每次刷新都会连它读取数据。

## 配置 OpenCode Go / 小米 MiMo

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
| 配置文件 | `%APPDATA%\token_view\config.json` |
| 调试日志 | `%APPDATA%\token_view\debug.log` |
| CDP Chrome 用户数据 | `%APPDATA%\token_view\chrome_profile` |

## 打包

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name TokenView \
  --add-data "web;web" \
  --hidden-import=clr --hidden-import=webview.platforms.edgechromium \
  main.py
```

打包后 `dist/TokenView.exe` 即可分发。详见下方打包说明。

## 常见问题

**Q: 显示「CDP 连接失败」？**
确认调试 Chrome 已启动且 `9222` 端口没被占用。

**Q: 智谱显示「暂无数据」？**
团队版必须用 CDP 模式。确认 Chrome 里打开了智谱团队用量页。

**Q: Windows 下窗口右侧/底部被裁？**
确保系统 DPI 缩放设置正确，程序已内置 DPI 自适应。
