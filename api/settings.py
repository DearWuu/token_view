"""设置窗口 + 模式/刷新间隔管理。"""
from __future__ import annotations

import os
from typing import Optional

import config
from logger import log


# 顶层模式：仅运行时记忆，不入 config（避免和 JS 端重复状态）
_top_mode_width: Optional[int] = None


def set_top_mode(enabled: bool) -> bool:
    """进入 / 退出顶部条模式。清掉 _top_mode_width 让窗口从内容重新算。"""
    global _top_mode_width
    if not enabled:
        _top_mode_width = None
    return True


def top_mode_width() -> Optional[int]:
    return _top_mode_width


def set_compact(cfg: dict, compact: bool) -> bool:
    global _top_mode_width
    _top_mode_width = None
    cfg["compact"] = compact
    config.save(cfg)
    return True


def set_dock(cfg: dict, dock: bool) -> bool:
    cfg["dock"] = dock
    config.save(cfg)
    return True


def set_refresh_interval(cfg: dict, seconds: int) -> bool:
    cfg["refresh_interval"] = max(15, min(3600, seconds))
    config.save(cfg)
    return True


def set_opacity(cfg: dict, opacity: float) -> bool:
    """把透明度持久化到配置，调用方需另外把它应用到窗口。"""
    cfg["opacity"] = max(0.3, min(1.0, opacity))
    config.save(cfg)
    return True


# --------------------- 设置窗口（独立 webview window） ---------------------

_settings_window = None  # 持有 pywebview 窗口引用，防止 GC


def open_settings_window(js_api) -> bool:
    """打开 / 复用设置窗口。"""
    global _settings_window
    try:
        import webview
    except ImportError as e:
        log(f"打开设置窗口失败（pywebview 不可用）: {e}")
        return False

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    settings_html = os.path.join(current_dir, "web", "settings.html")

    _settings_window = webview.create_window(
        "设置 - Token 用量监控",
        settings_html,
        js_api=js_api,
        width=1200,
        height=400,
        resizable=True,
        on_top=True,
    )
    log("设置窗口已打开")
    return True


def close_settings_window() -> bool:
    global _settings_window
    if _settings_window is not None:
        try:
            _settings_window.destroy()
        except (OSError, RuntimeError) as e:
            log(f"关闭设置窗口失败: {e}")
        _settings_window = None
    return True
