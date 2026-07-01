"""Python ⇄ JS 桥层（pywebview js_api）。

公开：
  - Api            注入到 webview.create_window(js_api=...) 的对象

子模块按职责拆分：
  - chrome         Chrome 查找 + CDP 启动
  - screen         跨平台屏幕工作区 / Win32 HWND / DPI
  - window         窗口几何（move/resize/置顶/透明度/持久化）
  - providers      Provider JSON 配置 CRUD
  - state          状态文件协议（Headroom 风格，给 companion 消费）
  - settings       设置窗口 + 模式/刷新间隔
  - core           Api 主类（编排上面所有模块）
"""
from .core import Api

__all__ = ["Api"]
