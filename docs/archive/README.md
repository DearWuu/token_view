# Archive

历史任务交接文档 / 设计稿，**不再反映当前实现**。

## 目录

- [PROMPT-cdp-攻坚.md](PROMPT-cdp-攻坚.md) — 智谱反爬 CDP 攻坚阶段的任务交接。
  时代背景：项目还跑在 PySide6 (QWidget) 上，团队用量 API 三条路径（cookie+auth /
  curl_cffi / QWebEngine 注入）都返回空 `data:{}`，最后走 CDP 连真实 Chrome。
  当前实现已迁到 pywebview，但 CDP 抓取协议本身仍保留在
  [`providers/zhipu.py`](../../providers/zhipu.py) 和
  [`providers/cdp.py`](../../providers/cdp.py)。
