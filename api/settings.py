"""设置窗口 + 模式/刷新间隔管理。"""
from __future__ import annotations

import ctypes
import os
import platform
from ctypes import wintypes
from typing import Optional

import config
from logger import log


def set_top_mode(enabled: bool) -> bool:
    """进入 / 退出顶部条模式（运行态由 api/core 的实例属性管理）。"""
    return True


def set_compact(cfg: dict, compact: bool) -> bool:
    cfg["compact"] = compact
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


# --------------------- Win32 窗口图标 ---------------------

def _set_window_icon(window, icon_path: str) -> None:
    """通过 Win32 WM_SETICON 设置窗口图标（pywebview 不支持 icon 参数时使用）。"""
    if platform.system() != "Windows" or not icon_path:
        return
    hwnd = _window_hwnd(window)
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        # 分别加载大小图标
        hicon_small = user32.LoadImageW(
            None, icon_path, IMAGE_ICON,
            16, 16, LR_LOADFROMFILE,
        )
        hicon_big = user32.LoadImageW(
            None, icon_path, IMAGE_ICON,
            32, 32, LR_LOADFROMFILE,
        )
        if hicon_small:
            user32.SendMessageW(wintypes.HWND(hwnd), WM_SETICON, ICON_SMALL, hicon_small)
        if hicon_big:
            user32.SendMessageW(wintypes.HWND(hwnd), WM_SETICON, ICON_BIG, hicon_big)
        log("设置窗口图标已应用")
    except OSError as e:
        log(f"设置窗口图标失败: {e}")


def _window_hwnd(window) -> int:
    """读取 Windows 原生窗口句柄。"""
    native = getattr(window, "native", None)
    for name in ("Handle", "handle", "hwnd"):
        value = getattr(native, name, None) if native is not None else None
        if value is None:
            value = getattr(window, name, None)
        if value is None:
            continue
        try:
            if hasattr(value, "ToInt64"):
                return int(value.ToInt64())
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


# --------------------- 设置窗口（独立 webview window） ---------------------

_settings_window = None  # 持有 pywebview 窗口引用，防止 GC


def open_settings_window(js_api) -> bool:
    """打开 / 复用设置窗口。

    复用已有窗口避免每次重建 WebView2（冷启动白屏约 2 秒）。"""
    global _settings_window
    try:
        import webview
    except ImportError as e:
        log(f"打开设置窗口失败（pywebview 不可用）: {e}")
        return False

    # 复用：窗口仍在则拉到前台显示，并刷新配置数据
    if _settings_window is not None:
        try:
            _settings_window.show()
            _settings_window.evaluate_js(
                "typeof loadConfig === 'function' && loadConfig()")
            log("设置窗口已复用")
            return True
        except Exception:  # noqa: BLE001  窗口已被销毁则重建
            _settings_window = None

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    settings_html = os.path.join(current_dir, "web", "settings.html")
    icon_path = os.path.join(current_dir, "assets", "icon.ico")
    if not os.path.exists(icon_path):
        icon_path = None

    _settings_window = webview.create_window(
        "设置 - Token 用量监控",
        settings_html,
        js_api=js_api,
        width=1200,
        height=700,
        resizable=True,
        on_top=True,
    )
    _set_window_icon(_settings_window, icon_path)

    # 用户点 X 关掉后清引用，下次重新创建
    def _on_closed():
        global _settings_window
        _settings_window = None

    try:
        _settings_window.events.closed += _on_closed
    except (AttributeError, TypeError):
        pass
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
